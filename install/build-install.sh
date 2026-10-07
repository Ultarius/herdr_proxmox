#!/usr/bin/env bash
# Explicit bootstrap for the unprivileged, concurrency-one build executor.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ -d /home/herdr/projects && -f /opt/herdr-web/web/gateway/build_worker.py ]] || {
  echo 'Install the matching gateway and projects directory first.' >&2; exit 1;
}
controllers=$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || true)
[[ " $controllers " == *' cpu '* && " $controllers " == *' memory '* ]] || {
  echo 'CPU/memory cgroup controllers are unavailable. Build service was not enabled; the gateway runner remains available.' >&2; exit 1;
}
install -d -o root -g root -m 0755 /etc/herdr
install -d -o herdr -g herdr -m 0700 /var/lib/herdr-build /home/herdr/herdr-validation
cat >/etc/systemd/system/herdr-build.service <<'EOF'
[Unit]
Description=Execute exact-commit Herdr build requests
After=network.target
[Service]
Type=simple
User=herdr
Group=herdr
WorkingDirectory=/home/herdr/projects
Environment=HOME=/home/herdr
Environment=PATH=/home/herdr/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 /opt/herdr-web/web/gateway/build_worker.py
Restart=on-failure
RestartSec=5
CPUQuota=150%
MemoryHigh=2G
MemoryMax=3G
TasksMax=256
IOWeight=50
Nice=10
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
# The worker needs this configuration to start. Remove the gateway marker if
# startup fails, so a failed bootstrap cannot strand requests in a dead queue.
printf '%s\n' '{"projects":"/home/herdr/projects","queue":"/var/lib/herdr-build","minimum_free_bytes":2147483648}' >/etc/herdr/build-service.json
chmod 0644 /etc/herdr/build-service.json
if ! systemctl enable --now herdr-build.service; then
  rm -f /etc/herdr/build-service.json
  exit 1
fi
systemctl restart herdr-web.service
echo 'Build service enabled. Automatic build scheduling remains off.'
