import hashlib
import json
import time
from pathlib import Path


HOST_CONFIG_FILE = "/host-metrics/config.json"
METRICS_FILE = "/host-metrics/metrics.json"
ACTION_REQUEST_FILE = Path("/host-requests/host_action.json")
ACTION_RESULT_FILE = Path("/host-requests/host_action_result.json")

MAX_MESSAGE_LEN = 133
MAX_METRICS_AGE_SECONDS = 3600


def load_host_config() -> dict:
    with open(HOST_CONFIG_FILE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} TB"


def format_uptime(seconds: int) -> str:
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours:02d}h"
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def split_text(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
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


def normalize_response(response: str | list[str] | None) -> str | list[str] | None:
    if response is None:
        return None

    if isinstance(response, str):
        parts = split_text(response)
        return parts if len(parts) > 1 else parts[0]

    messages = []
    for item in response:
        messages.extend(split_text(item))

    return messages


def load_metrics() -> dict:
    with open(METRICS_FILE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def is_allowed_context(kwargs: dict, config: dict) -> bool:
    if not kwargs.get("is_dm", False):
        return False

    allowed_sender_keys = set(config.get("allowed_sender_keys", []))
    return kwargs.get("sender_key") in allowed_sender_keys


def get_metrics_or_error() -> tuple[dict | None, str | None]:
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


def short_status(text: str, replacements: dict[str, str]) -> str:
    status = " ".join(str(text).split()).lower()

    for source, target in replacements.items():
        status = status.replace(source, target)

    return status


def format_host(metrics: dict) -> str:
    memory = metrics.get("memory", {})
    disk = metrics.get("disk", {})
    return (
        f"host {metrics.get('hostname', 'unknown')} {metrics.get('_age_seconds', 0)}s | "
        f"cpu {metrics.get('cpu_percent', 0)}% | "
        f"ram {memory.get('used_percent', 0)}% | "
        f"disk {disk.get('used_percent', 0)}% | "
        f"up {format_uptime(metrics.get('uptime_seconds', 0))}"
    )


def format_disk(metrics: dict) -> str:
    disk = metrics.get("disk", {})

    return (
        f"disk {disk.get('path', '/')}: "
        f"{format_bytes(disk.get('used_bytes', 0))}/"
        f"{format_bytes(disk.get('total_bytes', 0))} "
        f"({disk.get('used_percent', 0)}%) free {format_bytes(disk.get('free_bytes', 0))}"
    )


def format_temperatures(metrics: dict) -> list[str] | str:
    temperatures = metrics.get("temperatures", [])

    if not temperatures:
        return "No temperature sensors found."

    return [f"temp {item.get('name', 'sensor')}: {item.get('celsius', '?')} C" for item in temperatures[:8]]


def format_docker(metrics: dict) -> list[str] | str:
    containers = metrics.get("docker_containers", [])

    if not containers:
        return "No Docker containers found."

    lines = [f"docker total: {len(containers)}"]
    for container in containers[:10]:
        lines.append(
            f"{container.get('name')}: "
            f"{short_status(container.get('status', ''), {'up ': 'up ', 'exited': 'down', 'created': 'new'})}"
        )

    return lines


def format_vms(metrics: dict) -> list[str] | str:
    vms = metrics.get("kvm_vms", [])

    if not vms:
        return "No KVM VMs found."

    lines = [f"vms total: {len(vms)}"]
    for vm in vms[:10]:
        lines.append(f"{vm.get('name')}: {short_status(vm.get('state', ''), {})}")

    return lines


def handle_reboot(kwargs: dict, parts: list[str], config: dict) -> str:
    if len(parts) != 2:
        return "Usage: !reboot <PIN>"

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


def handle_docker_control(kwargs: dict, parts: list[str]) -> str:
    if len(parts) != 3:
        return "Usage: !dockerctl <start|stop|restart> <container>"

    action = parts[1].lower()
    name = parts[2]

    if action not in {"start", "stop", "restart"}:
        return "Invalid Docker action."

    return write_action_request_and_wait(
        {
            "type": "docker",
            "action": action,
            "name": name,
            "sender_name": kwargs.get("sender_name"),
            "sender_key": kwargs.get("sender_key"),
        }
    )


def handle_vm_control(kwargs: dict, parts: list[str]) -> str:
    if len(parts) != 3:
        return "Usage: !vmctl <start|stop|restart> <vm>"

    action = parts[1].lower()
    name = parts[2]

    if action not in {"start", "stop", "restart"}:
        return "Invalid VM action."

    return write_action_request_and_wait(
        {
            "type": "vm",
            "action": action,
            "name": name,
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


def format_help() -> list[str]:
    return [
        "!host !disk !temp !docker !vms",
        "!dockerctl start|stop|restart <name>",
        "!vmctl start|stop|restart <name>",
        "!reboot <PIN> !result",
    ]


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
        return normalize_response(f"Host config not available: {error}")

    if not is_allowed_context(kwargs, config):
        return None

    command = parts[0].lower()

    if command == "!help":
        return normalize_response(format_help())

    if command == "!reboot":
        return normalize_response(handle_reboot(kwargs, parts, config))

    if command == "!dockerctl":
        return normalize_response(handle_docker_control(kwargs, parts))

    if command == "!vmctl":
        return normalize_response(handle_vm_control(kwargs, parts))

    if command == "!result":
        return normalize_response(read_last_action_result())

    if command not in {"!host", "!status", "!server", "!disk", "!temp", "!docker", "!vms"}:
        return None

    metrics, error = get_metrics_or_error()
    if error:
        return normalize_response(error)

    if command in {"!host", "!status", "!server"}:
        return normalize_response(format_host(metrics))

    if command == "!disk":
        return normalize_response(format_disk(metrics))

    if command == "!temp":
        return normalize_response(format_temperatures(metrics))

    if command == "!docker":
        return normalize_response(format_docker(metrics))

    if command == "!vms":
        return normalize_response(format_vms(metrics))

    return None
