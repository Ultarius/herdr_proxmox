#!/usr/bin/env bash
# Run as root inside the CT with a complete /opt/herdr-web checkout and built assets.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
cd /opt/herdr-web
[[ -f web/public/index.html && -f web/public/dashboard/index.html && -f web/public/main.dart.js ]] || {
  echo 'Build the pinned Flutter/Jaspr UI first with scripts/build-web.sh.' >&2; exit 1;
}
id herdr >/dev/null
apt-get -o Acquire::Retries=5 install -y python3
install -d -o herdr -g herdr -m 0700 /home/herdr/.config/herdr-web
if [[ ! -f /home/herdr/.config/herdr-web/token ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))' >/home/herdr/.config/herdr-web/token
fi
chown herdr:herdr /home/herdr/.config/herdr-web/token
chmod 0600 /home/herdr/.config/herdr-web/token
bind=${HERDR_WEB_BIND:-0.0.0.0}
[[ $bind == 0.0.0.0 || $bind == 127.0.0.1 ]] || { echo 'HERDR_WEB_BIND must be 0.0.0.0 or 127.0.0.1.' >&2; exit 1; }
policy=/home/herdr/.config/herdr-web/dashboard-access.json
if [[ -n ${HERDR_WEB_BIND:-} || ! -f $policy ]]; then
  mode=lan
  [[ $bind != 127.0.0.1 ]] || mode=ssh
  printf '{"mode":"%s"}\n' "$mode" >"$policy"
fi
chown herdr:herdr "$policy"
chmod 0600 "$policy"
bind=$(python3 -c 'import json,sys; print({"lan":"0.0.0.0", "ssh":"127.0.0.1"}[json.load(open(sys.argv[1]))["mode"]])' "$policy")
cat >/etc/systemd/system/herdr-web.service <<'EOF'
[Unit]
Description=Herdr dashboard gateway
After=network.target
[Service]
User=herdr
Group=herdr
Environment=HOME=/home/herdr
Environment=PATH=/home/herdr/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HERDR_WEB_ROOT=/opt/herdr-web/web/public
WorkingDirectory=/home/herdr/projects
ExecStart=/usr/bin/python3 /opt/herdr-web/web/gateway/server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
install -d /etc/systemd/system/herdr-web.service.d
printf '[Service]\nEnvironment=HERDR_WEB_BIND=%s\n' "$bind" > /etc/systemd/system/herdr-web.service.d/listen.conf
systemctl daemon-reload
systemctl enable --now herdr-web
systemctl restart herdr-web
systemctl is-active --quiet herdr-web
printf 'Dashboard ready on %s:8787. Token: /home/herdr/.config/herdr-web/token.\n' "$bind"
