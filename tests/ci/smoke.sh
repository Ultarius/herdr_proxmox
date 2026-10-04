#!/usr/bin/env bash
# Run as root in the disposable, fully provisioned Debian test container.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Smoke orchestration requires root.' >&2; exit 1; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]]
locale -a | grep -qi '^en_US\.utf8$'
[[ $(env LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 locale charmap) == UTF-8 ]]
[[ $(env LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 perl -e 'print "locale-ok"' 2>&1) == locale-ok ]]
systemctl is-active --quiet ssh herdr-web
[[ $(/usr/sbin/sshd -T | awk '$1 == "passwordauthentication" {print $2}') == no ]]
[[ $(/usr/sbin/sshd -T | awk '$1 == "permitrootlogin" {print $2}') == no ]]
# Runtime starts Herdr through the same authenticated endpoint used by the UI.
herdr_uid=$(id -u herdr)
trap 'runuser -u herdr -- env XDG_RUNTIME_DIR="/run/user/$herdr_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$herdr_uid/bus" systemctl --user stop herdr-session.service >/dev/null 2>&1 || true' EXIT
runuser -u herdr -- env HOME=/home/herdr PATH=/home/herdr/.local/bin:/usr/local/bin:/usr/bin:/bin \
  python3 "$PWD/runtime.py" before
if [[ -f /root/ci-key ]]; then
  ssh -i /root/ci-key -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/root/ci-known-hosts herdr@127.0.0.1 'test "$(id -un)" = herdr'
fi
systemctl restart herdr-web
runuser -u herdr -- env HOME=/home/herdr PATH=/home/herdr/.local/bin:/usr/local/bin:/usr/bin:/bin \
  python3 "$PWD/runtime.py" after
printf 'Provisioning, CLI, Herdr server, gateway and persistence smoke checks passed.\n'
