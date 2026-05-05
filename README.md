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
- Shared host-side `config.json` for bot and action handler
- `config.example.json` template committed, real config kept local
- Host config also controls UI limits, enabled commands, alerts and display names
- No Docker socket or libvirt socket mounted into RemoteTerm

## Architecture

```text
RemoteTerm Python Bot
  -> reads /host-metrics/config.json
  -> reads prepared summaries from /host-metrics/metrics.json
  -> writes /host-requests/host_action.json
  -> host systemd path detects /opt/meshcore-hostbot/requests/host_action.json
  -> /opt/meshcore-hostbot/handle_host_request.py validates config and executes the action
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
|- README.md
|- host/
|  |- config.example.json
|  |- host_metrics.py
|  |- handle_host_request.py
|  `- systemd/
|     |- meshcore-hostbot.service
|     |- meshcore-hostbot.timer
|     |- meshcore-host-action.service
|     `- meshcore-host-action.path
|- remoteterm/
|  `- bot.py
|- scripts/
|  `- update-portainer.sh
`- docker/
   `- remoteterm-compose.example.yml
```

## Installation

### 1. Create host directories

```bash
sudo mkdir -p /opt/meshcore-hostbot/requests
sudo chmod 755 /opt/meshcore-hostbot
sudo chmod 777 /opt/meshcore-hostbot/requests
```

The requests directory must be writable by the RemoteTerm container.

### 2. Copy host files

From the repository root:

```bash
sudo cp host/host_metrics.py /opt/meshcore-hostbot/
sudo cp host/handle_host_request.py /opt/meshcore-hostbot/
sudo cp host/config.example.json /opt/meshcore-hostbot/config.json
sudo chmod 600 /opt/meshcore-hostbot/config.json
sudo chmod +x /opt/meshcore-hostbot/*.py
```

### 3. Configure host policy

Start from `host/config.example.json`, copy it to `/opt/meshcore-hostbot/config.json`, then edit `/opt/meshcore-hostbot/config.json`:

```json
{
  "allowed_sender_keys": [
    "CHANGE_ME_SENDER_KEY"
  ],
  "allow_reboot": false,
  "reboot_pin_sha256": "REPLACE_WITH_SHA256_OF_YOUR_PIN",
  "blocked_docker_containers": [
    "remoteterm-meshcore",
    "portainer"
  ],
  "blocked_vms": [],
  "commands": {
    "enabled": [
      "help",
      "alerts",
      "host",
      "disk",
      "temp",
      "docker",
      "vms",
      "result",
      "dockerctl",
      "vmctl",
      "reboot"
    ],
    "docker_actions": ["start", "stop", "restart"],
    "vm_actions": ["start", "stop", "restart"]
  },
  "display": {
    "max_message_len": 133,
    "max_list_items": 8,
    "temperature_unit": "C",
    "compact_mode": true
  },
  "thresholds": {
    "cpu_warn_percent": 85,
    "ram_warn_percent": 90,
    "disk_warn_percent": 90
  },
  "names": {
    "docker": {},
    "vms": {}
  }
}
```

Generate a SHA-256 hash for your reboot PIN:

```bash
printf '%s' 'YOUR_PIN' | sha256sum
```

Set `allow_reboot` to `true` only if you actually want to enable `!reboot`.

Useful host-side knobs:

- `commands.enabled`: turn commands on or off without editing the bot
- `commands.docker_actions` and `commands.vm_actions`: restrict allowed control actions
- `display.max_message_len`: keeps the bot aligned with MeshCore message limits
- `display.max_list_items`: controls how many containers, VMs or temperatures are shown
- `names.docker` and `names.vms`: optional display aliases for long service names
- `thresholds.*`: warn levels used to build the `alerts` list in `metrics.json`

### 4. Install systemd units

```bash
sudo cp host/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-hostbot.timer
sudo systemctl enable --now meshcore-host-action.path
```

### 5. Test metrics

```bash
sudo /opt/meshcore-hostbot/host_metrics.py
cat /opt/meshcore-hostbot/metrics.json
```

### 6. Configure RemoteTerm Docker volumes

Add these mounts to the RemoteTerm container:

```yaml
volumes:
  - /opt/remoteterm/data:/app/data
  - /opt/meshcore-hostbot:/host-metrics:ro
  - /opt/meshcore-hostbot/requests:/host-requests:rw
```

Important: mount the whole `/opt/meshcore-hostbot` directory, not just `metrics.json`.
Mounting a single JSON file can lead to stale file handles when the metrics file is atomically replaced.

### 7. Add the RemoteTerm Python Bot

Copy `remoteterm/bot.py` into the RemoteTerm Python Bot UI.

You can discover your sender key with a debug bot:

```python
def bot(**kwargs):
    return f"sender_key={kwargs.get('sender_key')} dm={kwargs.get('is_dm')}"
```

## Commands

All commands are DM-only and only accepted from sender keys allowed in `config.json`.

### Monitoring

```text
!help
!alerts
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

Security-relevant policy is enforced on the host:

- allowed sender keys
- blocked Docker containers
- blocked VMs
- reboot enable flag
- reboot PIN hash
- enabled commands and actions
- message length and list display limits
- alert thresholds and display aliases

This means a modified bot alone is not enough to bypass host-side rules.

## Troubleshooting

Check metrics:

```bash
cat /opt/meshcore-hostbot/metrics.json
```

Check config:

```bash
cat /opt/meshcore-hostbot/config.json
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
