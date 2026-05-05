import hashlib
import json
import time
from pathlib import Path
from typing import Any, TypeAlias


HOST_CONFIG_FILE = "/host-metrics/config.json"
METRICS_FILE = "/host-metrics/metrics.json"
ACTION_REQUEST_FILE = Path("/host-requests/host_action.json")
ACTION_RESULT_FILE = Path("/host-requests/host_action_result.json")

DEFAULT_MAX_MESSAGE_LEN = 133
MAX_METRICS_AGE_SECONDS = 3600
JsonDict: TypeAlias = dict[str, Any]


def load_host_config() -> JsonDict:
    with open(HOST_CONFIG_FILE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def load_metrics() -> JsonDict:
    with open(METRICS_FILE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def get_max_message_len(config: JsonDict) -> int:
    configured = int(config.get("display", {}).get("max_message_len", DEFAULT_MAX_MESSAGE_LEN))
    return max(32, configured)


def split_text(text: str, limit: int) -> list[str]:
    text = " ".join(str(text).split())
    if not text:
        return [""]

    if len(text) <= limit:
        return [text]

    words = text.split(" ")
    chunks = []
    current = ""
    content_limit = max(1, limit - 6)

    for word in words:
        if not current:
            if len(word) <= content_limit:
                current = word
                continue

            while len(word) > content_limit:
                chunks.append(word[:content_limit])
                word = word[content_limit:]

            current = word
            continue

        candidate = f"{current} {word}"
        if len(candidate) <= content_limit:
            current = candidate
            continue

        chunks.append(current)

        if len(word) <= content_limit:
            current = word
            continue

        while len(word) > content_limit:
            chunks.append(word[:content_limit])
            word = word[content_limit:]

        current = word

    if current:
        chunks.append(current)

    if len(chunks) == 1:
        return chunks

    numbered = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"{index}/{total} "
        numbered.append(f"{prefix}{chunk[: limit - len(prefix)]}")

    return numbered


def normalize_response(response: str | list[str] | None, config: JsonDict) -> str | list[str] | None:
    if response is None:
        return None

    limit = get_max_message_len(config)

    if isinstance(response, str):
        parts = split_text(response, limit)
        return parts if len(parts) > 1 else parts[0]

    messages = []
    for item in response:
        messages.extend(split_text(item, limit))

    return messages


def is_allowed_context(kwargs: dict, config: JsonDict) -> bool:
    if not kwargs.get("is_dm", False):
        return False

    allowed_sender_keys = set(config.get("allowed_sender_keys", []))
    return kwargs.get("sender_key") in allowed_sender_keys


def command_enabled(config: JsonDict, command_name: str) -> bool:
    enabled_commands = set(config.get("commands", {}).get("enabled", []))
    return command_name in enabled_commands


def get_command_triggers(config: JsonDict) -> JsonDict:
    return dict(config.get("commands", {}).get("triggers", {}))


def safe_trigger_label(trigger: str) -> str:
    if not trigger:
        return ""

    prefix = trigger[0]
    if prefix in {"!", "/", "#", "."} and len(trigger) > 1:
        return f"{prefix}\u2060{trigger[1:]}"

    return trigger


def primary_trigger(config: JsonDict, command_name: str, fallback: str) -> str:
    triggers = get_command_triggers(config)
    values = triggers.get(command_name, [])
    if isinstance(values, list) and values:
        return str(values[0])
    return fallback


def resolve_command(command_token: str, config: JsonDict) -> str | None:
    token = command_token.lower()
    for command_name, triggers in get_command_triggers(config).items():
        if not isinstance(triggers, list):
            continue
        normalized = {str(trigger).lower() for trigger in triggers}
        if token in normalized:
            return str(command_name)
    return None


def get_metrics_or_error() -> tuple[JsonDict | None, str | None]:
    try:
        metrics = load_metrics()
    except Exception as error:
        return None, f"Host metrics not available: {error}"

    current_timestamp = int(time.time())
    metrics_timestamp = int(metrics.get("timestamp", 0))
    age_seconds = current_timestamp - metrics_timestamp

    if age_seconds > MAX_METRICS_AGE_SECONDS:
        return None, f"Host metrics stale: {age_seconds}s old"

    metrics["_age_seconds"] = age_seconds
    return metrics, None


def require_metrics(config: JsonDict) -> JsonDict | str | list[str]:
    metrics, error = get_metrics_or_error()
    if error:
        return normalize_response(error, config) or "Host metrics unavailable."
    if metrics is None:
        return normalize_response("Host metrics unavailable.", config) or "Host metrics unavailable."
    return metrics


def write_action_request_and_wait(request: dict) -> str:
    request["created_at"] = int(time.time())
    temporary_file = ACTION_REQUEST_FILE.with_suffix(".tmp")

    try:
        ACTION_RESULT_FILE.unlink()
    except Exception:
        pass

    try:
        temporary_file.write_text(json.dumps(request), encoding="utf-8")
        temporary_file.replace(ACTION_REQUEST_FILE)
    except Exception as error:
        temporary_file.unlink(missing_ok=True)
        return f"Could not create host action request: {error}"

    for _ in range(10):
        time.sleep(0.5)

        try:
            result = json.loads(ACTION_RESULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            continue

        status = "OK" if result.get("success") else "ERROR"
        return f"{status}: {result.get('message', 'no message')}"

    return "ERROR: No response from host"


def handle_reboot(kwargs: dict, parts: list[str], config: JsonDict) -> str:
    if len(parts) != 2:
        return "usage: bang reboot <PIN>"

    if not command_enabled(config, "reboot"):
        return "Reboot command is disabled."

    if not config.get("allow_reboot", False):
        return "Host reboot is disabled."

    return write_action_request_and_wait(
        {
            "type": "reboot",
            "action": "reboot",
            "pin_sha256": hashlib.sha256(parts[1].strip().encode("utf-8")).hexdigest(),
            "sender_name": kwargs.get("sender_name"),
            "sender_key": kwargs.get("sender_key"),
        }
    )


def handle_docker_control(kwargs: dict, parts: list[str], config: JsonDict) -> str:
    if len(parts) != 3:
        return "usage: bang dockerctl <start|stop|restart> <container>"

    if not command_enabled(config, "dockerctl"):
        return "Docker control is disabled."

    action = parts[1].lower()
    allowed_actions = set(config.get("commands", {}).get("docker_actions", ["start", "stop", "restart"]))
    if action not in allowed_actions:
        return "Invalid Docker action."

    return write_action_request_and_wait(
        {
            "type": "docker",
            "action": action,
            "name": parts[2],
            "sender_name": kwargs.get("sender_name"),
            "sender_key": kwargs.get("sender_key"),
        }
    )


def handle_vm_control(kwargs: dict, parts: list[str], config: JsonDict) -> str:
    if len(parts) != 3:
        return "usage: bang vmctl <start|stop|restart> <vm>"

    if not command_enabled(config, "vmctl"):
        return "VM control is disabled."

    action = parts[1].lower()
    allowed_actions = set(config.get("commands", {}).get("vm_actions", ["start", "stop", "restart"]))
    if action not in allowed_actions:
        return "Invalid VM action."

    return write_action_request_and_wait(
        {
            "type": "vm",
            "action": action,
            "name": parts[2],
            "sender_name": kwargs.get("sender_name"),
            "sender_key": kwargs.get("sender_key"),
        }
    )


def read_last_action_result() -> str:
    try:
        result = json.loads(ACTION_RESULT_FILE.read_text(encoding="utf-8"))
    except Exception as error:
        return f"No action result available: {error}"

    status = "OK" if result.get("success") else "ERROR"
    return f"{status}: {result.get('message', 'no message')}"


def format_help(config: JsonDict) -> list[str]:
    enabled = set(config.get("commands", {}).get("enabled", []))
    base = []
    control = []

    if "host" in enabled:
        base.append("host")
    if "alerts" in enabled:
        base.append("alerts")
    if "disk" in enabled:
        base.append("disk")
    if "temp" in enabled:
        base.append("temp")
    if "docker" in enabled:
        base.append("docker")
    if "vms" in enabled:
        base.append("vms")
    if "result" in enabled:
        base.append("result")

    if "dockerctl" in enabled:
        control.append("dockerctl")
    if "vmctl" in enabled:
        control.append("vmctl")
    if "reboot" in enabled:
        control.append("reboot")

    lines = []
    if base:
        labels = [safe_trigger_label(primary_trigger(config, name, name)) for name in base]
        lines.append(f"cmds: {', '.join(labels)}")
    if "dockerctl" in control:
        actions = "|".join(config.get("commands", {}).get("docker_actions", ["start", "stop", "restart"]))
        trigger = safe_trigger_label(primary_trigger(config, "dockerctl", "!dockerctl"))
        lines.append(f"docker ctl: {trigger} {actions} <name>")
    if "vmctl" in control:
        actions = "|".join(config.get("commands", {}).get("vm_actions", ["start", "stop", "restart"]))
        trigger = safe_trigger_label(primary_trigger(config, "vmctl", "!vmctl"))
        lines.append(f"vm ctl: {trigger} {actions} <name>")
    if "reboot" in control:
        trigger = safe_trigger_label(primary_trigger(config, "reboot", "!reboot"))
        lines.append(f"reboot: {trigger} <PIN>")
    return lines or ["No commands enabled."]


def format_host(metrics: JsonDict) -> str:
    return str(metrics.get("summary") or "Host summary unavailable.")


def format_disk(metrics: JsonDict) -> str:
    return str(metrics.get("disk_summary") or "Disk summary unavailable.")


def format_temperatures(metrics: JsonDict) -> list[str] | str:
    display = metrics.get("temperature_display", [])
    return display or "No temperature sensors found."


def format_docker(metrics: JsonDict) -> list[str] | str:
    display = metrics.get("docker_display", [])
    return display or "No Docker containers found."


def format_vms(metrics: JsonDict) -> list[str] | str:
    display = metrics.get("vm_display", [])
    return display or "No KVM VMs found."


def format_alerts(metrics: JsonDict) -> list[str] | str:
    alerts = metrics.get("alerts", [])
    if not alerts:
        return "no alerts"
    return [f"alert: {alert}" for alert in alerts]


def bot(**kwargs):
    message_text = str(kwargs.get("message_text", "")).strip()
    parts = message_text.split()

    if not parts:
        return None

    if not kwargs.get("is_dm", False):
        return None

    try:
        config = load_host_config()
    except Exception as error:
        return normalize_response(f"Host config not available: {error}", {})

    if not is_allowed_context(kwargs, config):
        return None

    command_name = resolve_command(parts[0], config)
    if command_name is None:
        return None

    if not command_enabled(config, command_name):
        return normalize_response(f"Command is disabled: {command_name}", config)

    if command_name == "help":
        return normalize_response(format_help(config), config)

    if command_name == "reboot":
        return normalize_response(handle_reboot(kwargs, parts, config), config)

    if command_name == "dockerctl":
        return normalize_response(handle_docker_control(kwargs, parts, config), config)

    if command_name == "vmctl":
        return normalize_response(handle_vm_control(kwargs, parts, config), config)

    if command_name == "result":
        return normalize_response(read_last_action_result(), config)

    if command_name == "alerts":
        metrics = require_metrics(config)
        if not isinstance(metrics, dict):
            return metrics
        return normalize_response(format_alerts(metrics), config)

    metrics = require_metrics(config)
    if not isinstance(metrics, dict):
        return metrics

    if command_name == "host":
        return normalize_response(format_host(metrics), config)

    if command_name == "disk":
        return normalize_response(format_disk(metrics), config)

    if command_name == "temp":
        return normalize_response(format_temperatures(metrics), config)

    if command_name == "docker":
        return normalize_response(format_docker(metrics), config)

    if command_name == "vms":
        return normalize_response(format_vms(metrics), config)

    return None
