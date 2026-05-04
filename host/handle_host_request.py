#!/usr/bin/env python3

import json
import subprocess
import time
from pathlib import Path


REQUEST_FILE = Path("/opt/meshcore-hostbot/requests/host_action.json")
RESULT_FILE = Path("/opt/meshcore-hostbot/requests/host_action_result.json")
MAX_REQUEST_AGE_SECONDS = 300


BLOCKED_DOCKER_CONTAINERS = {
    "remoteterm-meshcore",
    "portainer",
}


BLOCKED_VMS = set()


def write_result(success: bool, message: str) -> None:
    result = {
        "timestamp": int(time.time()),
        "success": success,
        "message": message,
    }

    RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")


def run_command(command: list[str], timeout: int = 60) -> tuple[bool, str]:
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


def docker_container_exists(name: str) -> bool:
    success, output = run_command(
        ["docker", "inspect", "--format", "{{.Name}}", name],
        timeout=10,
    )

    return success and output.strip().lstrip("/") == name


def vm_exists(name: str) -> bool:
    success, _ = run_command(["virsh", "dominfo", name], timeout=10)
    return success


def handle_reboot() -> None:
    write_result(True, "Host reboot accepted.")
    subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)


def handle_docker(action: str, name: str) -> None:
    if name in BLOCKED_DOCKER_CONTAINERS:
        write_result(False, f"Container is blocked: {name}")
        return

    if not docker_container_exists(name):
        write_result(False, f"Container not found: {name}")
        return

    if action not in {"start", "stop", "restart"}:
        write_result(False, f"Invalid Docker action: {action}")
        return

    success, output = run_command(["docker", action, name])

    if success:
        write_result(True, f"Docker {action} OK: {name}")
    else:
        write_result(False, f"Docker {action} failed for {name}: {output}")


def handle_vm(action: str, name: str) -> None:
    if name in BLOCKED_VMS:
        write_result(False, f"VM is blocked: {name}")
        return

    if not vm_exists(name):
        write_result(False, f"VM not found: {name}")
        return

    if action == "start":
        command = ["virsh", "start", name]
    elif action == "stop":
        command = ["virsh", "shutdown", name]
    elif action == "restart":
        command = ["virsh", "reboot", name]
    else:
        write_result(False, f"Invalid VM action: {action}")
        return

    success, output = run_command(command)

    if success:
        write_result(True, f"VM {action} OK: {name}")
    else:
        write_result(False, f"VM {action} failed for {name}: {output}")


def load_request() -> dict | None:
    if not REQUEST_FILE.exists():
        return None

    try:
        request = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    except Exception as error:
        REQUEST_FILE.unlink(missing_ok=True)
        write_result(False, f"Invalid request JSON: {error}")
        return None

    REQUEST_FILE.unlink(missing_ok=True)
    return request


def validate_request_age(request: dict) -> bool:
    created_at = int(request.get("created_at", 0))
    age_seconds = int(time.time()) - created_at

    if age_seconds < 0 or age_seconds > MAX_REQUEST_AGE_SECONDS:
        write_result(False, f"Request expired: {age_seconds}s old")
        return False

    return True


def main() -> None:
    request = load_request()

    if request is None:
        return

    if not validate_request_age(request):
        return

    request_type = request.get("type")
    action = request.get("action")
    name = request.get("name", "")

    if request_type == "reboot" and action == "reboot":
        handle_reboot()
        return

    if request_type == "docker":
        handle_docker(action, name)
        return

    if request_type == "vm":
        handle_vm(action, name)
        return

    write_result(False, f"Unknown request: type={request_type}, action={action}")


if __name__ == "__main__":
    main()
