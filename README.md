# MeshCore HostBot

Control and monitor a Linux host via MeshCore RemoteTerm.

## Features

- Host monitoring: CPU, RAM, disk, uptime, IP, temperatures
- Docker container listing and control
- KVM/libvirt VM listing and control
- Secure host reboot
- DM-only command handling
- Sender key whitelist
- File-based communication between RemoteTerm container and host
- No Docker socket or libvirt socket mounted into RemoteTerm

## Architecture

```text
RemoteTerm Python Bot
  -> writes /host-requests/host_action.json
  -> host systemd path detects /opt/meshcore-hostbot/requests/host_action.json
  -> /opt/meshcore-hostbot/handle_host_request.py executes the action
  -> writes /opt/meshcore-hostbot/requests/host_action_result.json
  -> bot reads result and replies
```

Metrics are written regularly by systemd timer:

```text
meshcore-hostbot.timer
  -> meshcore-hostbot.service
  -> /opt/meshcore-hostbot/host_metrics.py
  -> /opt/meshcore-hostbot/metrics.json
```

## Repository Layout

```text
meshcore-hostbot/
├── README.md
├── host/
│   ├── host_metrics.py
│   ├── handle_host_request.py
│   └── systemd/
│       ├── meshcore-hostbot.service
│       ├── meshcore-hostbot.timer
│       ├── meshcore-host-action.service
│       └── meshcore-host-action.path
├── remoteterm/
│   └── bot.py
├── scripts/
│   └── update-portainer.sh
└── docker/
    └── remoteterm-compose.example.yml
```

## Installation

### 1. Create host directories

```bash
sudo mkdir -p /opt/meshcore-hostbot/requests
sudo chmod 755 /opt/meshcore-hostbot
sudo chmod 777 /opt/meshcore-hostbot/requests
```

The requests directory must be writable by the RemoteTerm container.

### 2. Copy host scripts

From the repository root:

```bash
sudo cp host/host_metrics.py /opt/meshcore-hostbot/
sudo cp host/handle_host_request.py /opt/meshcore-hostbot/
sudo chmod +x /opt/meshcore-hostbot/*.py
```

### 3. Install systemd units

```bash
sudo cp host/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-hostbot.timer
sudo systemctl enable --now meshcore-host-action.path
```

### 4. Test metrics

```bash
sudo /opt/meshcore-hostbot/host_metrics.py
cat /opt/meshcore-hostbot/metrics.json
```

### 5. Configure RemoteTerm Docker volumes

Add these mounts to the RemoteTerm container:

```yaml
volumes:
  - /opt/remoteterm/data:/app/data
  - /opt/meshcore-hostbot:/host-metrics:ro
  - /opt/meshcore-hostbot/requests:/host-requests:rw
```

Important: mount the whole `/opt/meshcore-hostbot` directory, not just `metrics.json`.
Mounting a single JSON file can lead to stale file handles when the metrics file is atomically replaced.

### 6. Add the RemoteTerm Python Bot

Copy `remoteterm/bot.py` into the RemoteTerm Python Bot UI.

Change these values before using it:

```python
REBOOT_PIN = "CHANGE_ME"

ALLOWED_CONTROL_SENDER_KEYS = {
    "CHANGE_ME_SENDER_KEY",
}
```

You can discover your sender key with a debug bot:

```python
def bot(**kwargs):
    return f"sender_key={kwargs.get('sender_key')} dm={kwargs.get('is_dm')}"
```

## Commands

All commands are DM-only and only accepted from whitelisted sender keys.

### Monitoring

```text
!help
!host
!disk
!temp
!docker
!vms
!result
```

### Docker Control

```text
!dockerctl start <container>
!dockerctl stop <container>
!dockerctl restart <container>
```

### VM Control

```text
!vmctl start <vm>
!vmctl stop <vm>
!vmctl restart <vm>
```

### Host Reboot

```text
!reboot <PIN>
```

## Safety Notes

The bot intentionally does not expose arbitrary shell command execution.

The host action handler dynamically checks whether Docker containers and VMs exist, but blocks critical containers by default:

```python
BLOCKED_DOCKER_CONTAINERS = {
    "remoteterm-meshcore",
    "portainer",
}
```

This prevents accidentally stopping the RemoteTerm bot itself or Portainer.

## Troubleshooting

Check metrics:

```bash
cat /opt/meshcore-hostbot/metrics.json
```

Check action result:

```bash
cat /opt/meshcore-hostbot/requests/host_action_result.json
```

Check systemd units:

```bash
systemctl status meshcore-hostbot.timer
systemctl status meshcore-host-action.path
systemctl status meshcore-host-action.service
journalctl -u meshcore-host-action.service -n 50 --no-pager
```

Run action handler manually:

```bash
sudo python3 /opt/meshcore-hostbot/handle_host_request.py
```

## Portainer Update Script

Install:

```bash
sudo mkdir -p /opt/scripts
sudo cp scripts/update-portainer.sh /opt/scripts/update-portainer.sh
sudo chmod +x /opt/scripts/update-portainer.sh
```

Run manually:

```bash
sudo /opt/scripts/update-portainer.sh
```

## License

MIT
