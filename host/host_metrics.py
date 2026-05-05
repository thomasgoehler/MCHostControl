#!/usr/bin/env python3

import json
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path


BASE_DIR = Path("/opt/meshcore-hostbot")
CONFIG_FILE = BASE_DIR / "config.json"
OUTPUT_FILE = BASE_DIR / "metrics.json"


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except OSError:
        return {}


def run_command(command: list[str], timeout: int = 5) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except Exception as error:
        return False, str(error)


def read_first_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            return file_handle.readline().strip()
    except OSError:
        return ""


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


def get_uptime_seconds() -> int:
    uptime_text = read_first_line("/proc/uptime")
    if not uptime_text:
        return 0
    return int(float(uptime_text.split()[0]))


def get_memory_info() -> dict:
    values = {}

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as file_handle:
            for line in file_handle:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0]) * 1024
    except OSError:
        return {}

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = total - available

    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": round((used / total) * 100, 1) if total else 0,
    }


def read_cpu_times() -> tuple[int, int]:
    line = read_first_line("/proc/stat")
    parts = [int(value) for value in line.split()[1:]]
    idle = parts[3] + parts[4]
    total = sum(parts)
    return idle, total


def get_cpu_percent() -> float:
    idle_start, total_start = read_cpu_times()
    time.sleep(0.2)
    idle_end, total_end = read_cpu_times()

    idle_delta = idle_end - idle_start
    total_delta = total_end - total_start

    if total_delta <= 0:
        return 0.0

    return round((1 - idle_delta / total_delta) * 100, 1)


def get_disk_info(path: str = "/") -> dict:
    usage = shutil.disk_usage(path)
    used = usage.total - usage.free

    return {
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": used,
        "free_bytes": usage.free,
        "used_percent": round((used / usage.total) * 100, 1),
    }


def get_primary_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as socket_handle:
            socket_handle.connect(("8.8.8.8", 80))
            return socket_handle.getsockname()[0]
    except OSError:
        return "unknown"


def get_temperatures() -> list[dict]:
    temperatures = []

    for thermal_zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        type_file = thermal_zone / "type"
        temp_file = thermal_zone / "temp"

        if not temp_file.exists():
            continue

        try:
            name = type_file.read_text(encoding="utf-8").strip()
            raw_value = int(temp_file.read_text(encoding="utf-8").strip())
            temperatures.append({"name": name, "celsius": round(raw_value / 1000, 1)})
        except Exception:
            continue

    return temperatures[:10]


def get_docker_containers() -> list[dict]:
    success, output = run_command(
        ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"]
    )

    if not success:
        return [{"name": "docker unavailable", "status": output, "image": ""}]

    containers = []
    for line in output.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            containers.append({"name": parts[0], "status": parts[1], "image": parts[2]})

    return containers


def get_kvm_vms() -> list[dict]:
    success, output = run_command(["virsh", "list", "--all", "--name"])

    if not success:
        return [{"name": "virsh unavailable", "state": output}]

    vms = []
    for vm_name in output.splitlines():
        vm_name = vm_name.strip()
        if not vm_name:
            continue

        state_success, state_output = run_command(["virsh", "domstate", vm_name])
        vms.append({"name": vm_name, "state": state_output if state_success else "unknown"})

    return vms


def extract_robot_target(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    for index, part in enumerate(parts):
        if part in {"-t", "--test"} and index + 1 < len(parts):
            target = parts[index + 1].strip()
            if "." in target:
                target = target.split(".")[-1]
            return target

    for index, part in enumerate(parts):
        if part in {"-s", "--suite"} and index + 1 < len(parts):
            target = parts[index + 1].strip()
            if "." in target:
                target = target.split(".")[-1]
            return target

    for part in reversed(parts):
        if part.endswith(".robot"):
            return Path(part).stem

    return ""


def is_robotframework_run(command: str, config: dict) -> bool:
    lowered = command.lower()
    monitoring = config.get("monitoring", {})
    keywords = [
        str(item).lower()
        for item in monitoring.get("robot_process_keywords", ["pabot", "robot", "pybot"])
    ]
    required_paths = [str(item).lower() for item in monitoring.get("robot_project_paths", [])]

    if not any(keyword and keyword in lowered for keyword in keywords):
        return False

    if required_paths and not any(path and path in lowered for path in required_paths):
        return False

    robot_markers = [" robotcode ", " pabot", " robot ", ".robot", " --test ", " -t ", " --suite ", " -s "]
    return any(marker in f" {lowered} " for marker in robot_markers)


def get_robotframework_processes(config: dict) -> list[dict]:
    success, output = run_command(["ps", "-eo", "pid=,args="], timeout=10)
    if not success:
        return [{"pid": 0, "command": f"process check unavailable: {output}"}]

    processes = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            continue

        pid_text, command = parts
        if not is_robotframework_run(command, config):
            continue

        processes.append(
            {
                "pid": int(pid_text),
                "command": command,
                "target": extract_robot_target(command),
            }
        )

    return processes


def get_android_devices() -> list[dict]:
    success, output = run_command(["adb", "devices", "-l"], timeout=10)
    if not success:
        return [{"serial": "adb unavailable", "state": output}]

    devices = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1]
        extras = {}
        for item in parts[2:]:
            if ":" not in item:
                continue
            key, value = item.split(":", 1)
            extras[key] = value

        devices.append(
            {
                "serial": serial,
                "state": state,
                "model": extras.get("model", ""),
                "device": extras.get("device", ""),
                "transport_id": extras.get("transport_id", ""),
            }
        )

    return devices


def short_status(text: str, replacements: dict[str, str]) -> str:
    status = " ".join(str(text).split()).lower()
    for source, target in replacements.items():
        status = status.replace(source, target)
    return status


def get_display_name(kind: str, name: str, config: dict) -> str:
    names = config.get("names", {})
    kind_names = names.get(kind, {})
    return str(kind_names.get(name, name))


def format_temperature(item: dict, unit: str) -> str:
    celsius = float(item.get("celsius", 0))
    if unit.upper() == "F":
        fahrenheit = round((celsius * 9 / 5) + 32, 1)
        return f"{item.get('name', 'sensor')}: {fahrenheit} F"
    return f"{item.get('name', 'sensor')}: {celsius} C"


def build_docker_display(containers: list[dict], config: dict) -> list[str]:
    limit = int(config.get("display", {}).get("max_list_items", 8))
    lines = [f"docker total: {len(containers)}"]
    for container in containers[:limit]:
        name = str(container.get("name", "unknown"))
        display_name = get_display_name("docker", name, config)
        status = short_status(
            container.get("status", ""),
            {"up ": "up ", "exited": "down", "created": "new"},
        )
        lines.append(f"{display_name}: {status}")
    return lines


def build_vm_display(vms: list[dict], config: dict) -> list[str]:
    limit = int(config.get("display", {}).get("max_list_items", 8))
    lines = [f"vms total: {len(vms)}"]
    for vm in vms[:limit]:
        name = str(vm.get("name", "unknown"))
        display_name = get_display_name("vms", name, config)
        lines.append(f"{display_name}: {short_status(vm.get('state', ''), {})}")
    return lines


def build_temperature_display(temperatures: list[dict], config: dict) -> list[str]:
    unit = str(config.get("display", {}).get("temperature_unit", "C"))
    limit = int(config.get("display", {}).get("max_list_items", 8))
    if not temperatures:
        return ["No temperature sensors found."]
    return [f"temp {format_temperature(item, unit)}" for item in temperatures[:limit]]


def build_robot_display(processes: list[dict], config: dict) -> list[str]:
    limit = int(config.get("display", {}).get("max_list_items", 8))
    if not processes:
        return ["robot: idle"]

    if processes[0].get("pid") == 0:
        return [f"robot: {processes[0].get('command', 'unavailable')}"]

    targets = [str(process.get("target", "")).strip() for process in processes if str(process.get("target", "")).strip()]
    if len(processes) == 1 and targets:
        return [f"robot: {targets[0]}"]

    lines = [f"robot: running {len(processes)}"]
    for process in processes[:limit]:
        target = str(process.get("target", "")).strip()
        if target:
            lines.append(f"pid {process.get('pid')}: {target}")
            continue

        command = " ".join(str(process.get("command", "")).split())
        lines.append(f"pid {process.get('pid')}: {command[:90]}")
    return lines


def build_android_display(devices: list[dict], config: dict) -> list[str]:
    limit = int(config.get("display", {}).get("max_list_items", 8))
    if not devices:
        return ["android: no devices"]

    if devices[0].get("serial") == "adb unavailable":
        return [f"android: {devices[0].get('state', 'adb unavailable')}"]

    lines = [f"android: {len(devices)} device(s)"]
    for device in devices[:limit]:
        lines.append(f"{device.get('serial', 'unknown')}: {device.get('state', 'unknown')}")
    return lines


def build_alerts(metrics: dict, config: dict) -> list[str]:
    thresholds = config.get("thresholds", {})
    alerts = []

    cpu_warn = float(thresholds.get("cpu_warn_percent", 85))
    ram_warn = float(thresholds.get("ram_warn_percent", 90))
    disk_warn = float(thresholds.get("disk_warn_percent", 90))

    if float(metrics.get("cpu_percent", 0)) >= cpu_warn:
        alerts.append(f"cpu {metrics.get('cpu_percent', 0)}%")

    memory = metrics.get("memory", {})
    if float(memory.get("used_percent", 0)) >= ram_warn:
        alerts.append(f"ram {memory.get('used_percent', 0)}%")

    disk = metrics.get("disk", {})
    if float(disk.get("used_percent", 0)) >= disk_warn:
        alerts.append(f"disk {disk.get('used_percent', 0)}%")

    for container in metrics.get("docker_containers", []):
        status = short_status(container.get("status", ""), {})
        if "unhealthy" in status or status.startswith("down"):
            name = get_display_name("docker", str(container.get("name", "unknown")), config)
            alerts.append(f"docker {name} {status}")

    for vm in metrics.get("kvm_vms", []):
        state = short_status(vm.get("state", ""), {})
        if state not in {"running", "idle"}:
            name = get_display_name("vms", str(vm.get("name", "unknown")), config)
            alerts.append(f"vm {name} {state}")

    return alerts


def build_summary(metrics: dict) -> str:
    memory = metrics.get("memory", {})
    disk = metrics.get("disk", {})
    robot_running = len([item for item in metrics.get("robot_processes", []) if int(item.get("pid", 0)) > 0])
    android_connected = len([item for item in metrics.get("android_devices", []) if str(item.get("serial", "")) != "adb unavailable"])
    return (
        f"host {metrics.get('hostname', 'unknown')} | "
        f"cpu {metrics.get('cpu_percent', 0)}% | "
        f"ram {memory.get('used_percent', 0)}% | "
        f"disk {disk.get('used_percent', 0)}% | "
        f"rf {robot_running} | adb {android_connected} | "
        f"up {format_uptime(metrics.get('uptime_seconds', 0))}"
    )


def build_disk_summary(metrics: dict) -> str:
    disk = metrics.get("disk", {})
    return (
        f"disk {disk.get('path', '/')}: "
        f"{format_bytes(disk.get('used_bytes', 0))}/"
        f"{format_bytes(disk.get('total_bytes', 0))} "
        f"({disk.get('used_percent', 0)}%) free {format_bytes(disk.get('free_bytes', 0))}"
    )


def main() -> None:
    config = load_config()
    temperatures = get_temperatures()
    docker_containers = get_docker_containers()
    kvm_vms = get_kvm_vms()
    robot_processes = get_robotframework_processes(config)
    android_devices = get_android_devices()

    metrics = {
        "timestamp": int(time.time()),
        "hostname": socket.gethostname(),
        "ip_address": get_primary_ip(),
        "load_average": [round(value, 2) for value in os.getloadavg()],
        "cpu_percent": get_cpu_percent(),
        "memory": get_memory_info(),
        "disk": get_disk_info("/"),
        "temperatures": temperatures,
        "docker_containers": docker_containers,
        "kvm_vms": kvm_vms,
        "robot_processes": robot_processes,
        "android_devices": android_devices,
        "uptime_seconds": get_uptime_seconds(),
        "summary": "",
        "disk_summary": "",
        "temperature_display": [],
        "docker_display": [],
        "vm_display": [],
        "robot_display": [],
        "android_display": [],
        "alerts": [],
    }

    metrics["summary"] = build_summary(metrics)
    metrics["disk_summary"] = build_disk_summary(metrics)
    metrics["temperature_display"] = build_temperature_display(temperatures, config)
    metrics["docker_display"] = build_docker_display(docker_containers, config)
    metrics["vm_display"] = build_vm_display(kvm_vms, config)
    metrics["robot_display"] = build_robot_display(robot_processes, config)
    metrics["android_display"] = build_android_display(android_devices, config)
    metrics["alerts"] = build_alerts(metrics, config)

    temporary_file = OUTPUT_FILE.with_suffix(".tmp")
    temporary_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    temporary_file.replace(OUTPUT_FILE)


if __name__ == "__main__":
    main()
