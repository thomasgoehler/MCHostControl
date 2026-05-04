#!/usr/bin/env python3

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path


OUTPUT_FILE = Path("/opt/meshcore-hostbot/metrics.json")


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


def main() -> None:
    metrics = {
        "timestamp": int(time.time()),
        "hostname": socket.gethostname(),
        "ip_address": get_primary_ip(),
        "load_average": [round(value, 2) for value in os.getloadavg()],
        "cpu_percent": get_cpu_percent(),
        "memory": get_memory_info(),
        "disk": get_disk_info("/"),
        "temperatures": get_temperatures(),
        "docker_containers": get_docker_containers(),
        "kvm_vms": get_kvm_vms(),
        "uptime_seconds": get_uptime_seconds(),
    }

    temporary_file = OUTPUT_FILE.with_suffix(".tmp")
    temporary_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    temporary_file.replace(OUTPUT_FILE)


if __name__ == "__main__":
    main()
