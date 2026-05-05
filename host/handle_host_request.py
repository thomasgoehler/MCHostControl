#!/usr/bin/env python3

import hashlib
import json
import subprocess
import time
from pathlib import Path


BASE_DIR = Path("/opt/meshcore-hostbot")
CONFIG_FILE = BASE_DIR / "config.json"
REQUEST_FILE = BASE_DIR / "requests" / "host_action.json"
RESULT_FILE = BASE_DIR / "requests" / "host_action_result.json"
MAX_REQUEST_AGE_SECONDS = 300


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def command_enabled(config: dict, command_name: str) -> bool:
    enabled_commands = set(config.get("commands", {}).get("enabled", []))
    return command_name in enabled_commands


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


def validate_request_age(request: dict) -> bool:
    created_at = int(request.get("created_at", 0))
    age_seconds = int(time.time()) - created_at

    if age_seconds < 0 or age_seconds > MAX_REQUEST_AGE_SECONDS:
        write_result(False, f"Request expired: {age_seconds}s old")
        return False

    return True


def validate_sender(config: dict, request: dict) -> bool:
    allowed_sender_keys = set(config.get("allowed_sender_keys", []))
    sender_key = str(request.get("sender_key", "")).strip()

    if not sender_key:
        write_result(False, "Missing sender key.")
        return False

    if sender_key not in allowed_sender_keys:
        write_result(False, f"Sender key is not allowed: {sender_key}")
        return False

    return True


def validate_reboot_pin(config: dict, request: dict) -> bool:
    if not command_enabled(config, "reboot"):
        write_result(False, "Reboot command is disabled.")
        return False

    if not config.get("allow_reboot", False):
        write_result(False, "Host reboot is disabled.")
        return False

    expected_hash = str(config.get("reboot_pin_sha256", "")).strip().lower()
    provided_hash = str(request.get("pin_sha256", "")).strip().lower()

    if not expected_hash:
        write_result(False, "Reboot PIN hash is not configured.")
        return False

    if len(provided_hash) != 64 or any(character not in "0123456789abcdef" for character in provided_hash):
        write_result(False, "Invalid reboot PIN hash.")
        return False

    if provided_hash != expected_hash:
        write_result(False, "Invalid reboot PIN.")
        return False

    return True


def handle_reboot() -> None:
    write_result(True, "Host reboot accepted.")
    subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)


def handle_updates(config: dict) -> None:
    if not command_enabled(config, "updates"):
        write_result(False, "Updates command is disabled.")
        return

    success, output = run_command(
        ["apt-get", "update", "-o", "Acquire::Retries=1"],
        timeout=300,
    )
    if not success:
        write_result(False, f"apt update failed: {output}")
        return

    success, output = run_command(["apt", "list", "--upgradable"], timeout=60)
    if not success:
        write_result(False, f"Could not read upgradable packages: {output}")
        return

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    packages = [line for line in lines if not line.startswith("Listing...")]
    count = len(packages)

    if count == 0:
        write_result(True, "System is up to date.")
        return

    write_result(True, f"{count} package(s) can be upgraded.")


def handle_docker(config: dict, action: str, name: str) -> None:
    if not command_enabled(config, "dockerctl"):
        write_result(False, "Docker control is disabled.")
        return

    blocked_containers = set(config.get("blocked_docker_containers", []))
    allowed_actions = set(config.get("commands", {}).get("docker_actions", ["start", "stop", "restart"]))

    if name in blocked_containers:
        write_result(False, f"Container is blocked: {name}")
        return

    if not docker_container_exists(name):
        write_result(False, f"Container not found: {name}")
        return

    if action not in allowed_actions:
        write_result(False, f"Invalid Docker action: {action}")
        return

    success, output = run_command(["docker", action, name])

    if success:
        write_result(True, f"Docker {action} OK: {name}")
    else:
        write_result(False, f"Docker {action} failed for {name}: {output}")


def handle_vm(config: dict, action: str, name: str) -> None:
    if not command_enabled(config, "vmctl"):
        write_result(False, "VM control is disabled.")
        return

    blocked_vms = set(config.get("blocked_vms", []))
    allowed_actions = set(config.get("commands", {}).get("vm_actions", ["start", "stop", "restart"]))

    if name in blocked_vms:
        write_result(False, f"VM is blocked: {name}")
        return

    if not vm_exists(name):
        write_result(False, f"VM not found: {name}")
        return

    if action not in allowed_actions:
        write_result(False, f"Invalid VM action: {action}")
        return

    if action == "start":
        command = ["virsh", "start", name]
    elif action == "stop":
        command = ["virsh", "shutdown", name]
    elif action == "restart":
        command = ["virsh", "reboot", name]

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


def main() -> None:
    request = load_request()

    if request is None:
        return

    try:
        config = load_config()
    except Exception as error:
        write_result(False, f"Could not load host config: {error}")
        return

    if not validate_request_age(request):
        return

    if not validate_sender(config, request):
        return

    request_type = request.get("type")
    action = request.get("action")
    name = request.get("name", "")

    if request_type == "reboot" and action == "reboot":
        if validate_reboot_pin(config, request):
            handle_reboot()
        return

    if request_type == "updates" and action == "check":
        handle_updates(config)
        return

    if request_type == "docker":
        handle_docker(config, action, name)
        return

    if request_type == "vm":
        handle_vm(config, action, name)
        return

    write_result(False, f"Unknown request: type={request_type}, action={action}")


if __name__ == "__main__":
    main()
