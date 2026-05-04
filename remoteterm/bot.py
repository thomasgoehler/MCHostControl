import hashlib
import json
import time
from pathlib import Path


HOST_CONFIG_FILE = "/host-metrics/config.json"
METRICS_FILE = "/host-metrics/metrics.json"
ACTION_REQUEST_FILE = Path("/host-requests/host_action.json")
ACTION_RESULT_FILE = Path("/host-requests/host_action_result.json")

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


def format_host(metrics: dict) -> list[str]:
    memory = metrics.get("memory", {})
    disk = metrics.get("disk", {})
    load_average = metrics.get("load_average", [0, 0, 0])

    return [
        f"Host {metrics.get('hostname', 'unknown')} ({metrics.get('_age_seconds', 0)}s old)",
        f"Load: {load_average[0]} {load_average[1]} {load_average[2]} | CPU: {metrics.get('cpu_percent', 0)}%",
        (
            f"RAM: {format_bytes(memory.get('used_bytes', 0))}/"
            f"{format_bytes(memory.get('total_bytes', 0))} "
            f"({memory.get('used_percent', 0)}%)"
        ),
        (
            f"Disk {disk.get('path', '/')}: {format_bytes(disk.get('used_bytes', 0))}/"
            f"{format_bytes(disk.get('total_bytes', 0))} "
            f"({disk.get('used_percent', 0)}%)"
        ),
        f"Uptime: {format_uptime(metrics.get('uptime_seconds', 0))} | IP: {metrics.get('ip_address', 'unknown')}",
    ]


def format_disk(metrics: dict) -> str:
    disk = metrics.get("disk", {})

    return (
        f"Disk {disk.get('path', '/')}: "
        f"{format_bytes(disk.get('used_bytes', 0))}/"
        f"{format_bytes(disk.get('total_bytes', 0))} used "
        f"({disk.get('used_percent', 0)}%), "
        f"free {format_bytes(disk.get('free_bytes', 0))}"
    )


def format_temperatures(metrics: dict) -> list[str] | str:
    temperatures = metrics.get("temperatures", [])

    if not temperatures:
        return "No temperature sensors found."

    return [
        f"{item.get('name', 'sensor')}: {item.get('celsius', '?')} C"
        for item in temperatures[:8]
    ]


def format_docker(metrics: dict) -> list[str] | str:
    containers = metrics.get("docker_containers", [])

    if not containers:
        return "No Docker containers found."

    lines = [f"Docker containers: {len(containers)}"]
    for container in containers[:10]:
        lines.append(f"{container.get('name')}: {container.get('status')}")

    return lines


def format_vms(metrics: dict) -> list[str] | str:
    vms = metrics.get("kvm_vms", [])

    if not vms:
        return "No KVM VMs found."

    lines = [f"KVM VMs: {len(vms)}"]
    for vm in vms[:10]:
        lines.append(f"{vm.get('name')}: {vm.get('state')}")

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
        return f"Host config not available: {error}"

    if not is_allowed_context(kwargs, config):
        return None

    command = parts[0].lower()

    if command == "!help":
        return (
            "Commands:\n"
            "- host: send !host\n"
            "- disk: send !disk\n"
            "- temp: send !temp\n"
            "- docker list: send !docker\n"
            "- vms list: send !vms\n"
            "- docker control: !dockerctl start|stop|restart <name>\n"
            "- vm control: !vmctl start|stop|restart <name>\n"
            "- reboot: !reboot <PIN>\n"
            "- last action result: !result"
        )

    if command == "!reboot":
        return handle_reboot(kwargs, parts, config)

    if command == "!dockerctl":
        return handle_docker_control(kwargs, parts)

    if command == "!vmctl":
        return handle_vm_control(kwargs, parts)

    if command == "!result":
        return read_last_action_result()

    if command not in {"!host", "!status", "!server", "!disk", "!temp", "!docker", "!vms"}:
        return None

    metrics, error = get_metrics_or_error()
    if error:
        return error

    if command in {"!host", "!status", "!server"}:
        return format_host(metrics)

    if command == "!disk":
        return format_disk(metrics)

    if command == "!temp":
        return format_temperatures(metrics)

    if command == "!docker":
        return format_docker(metrics)

    if command == "!vms":
        return format_vms(metrics)

    return None
