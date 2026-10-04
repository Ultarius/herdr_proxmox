#!/usr/bin/env bash
# Run as root in the disposable, fully provisioned Debian test container.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Smoke orchestration requires root.' >&2; exit 1; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]]
systemctl is-active --quiet ssh herdr-web
[[ $(/usr/sbin/sshd -T | awk '$1 == "passwordauthentication" {print $2}') == no ]]
[[ $(/usr/sbin/sshd -T | awk '$1 == "permitrootlogin" {print $2}') == no ]]
# Only the test session is supervised here; production still starts Herdr over SSH.
trap 'systemctl stop herdr-ci-session.service >/dev/null 2>&1 || true' EXIT
systemd-run --unit=herdr-ci-session --uid=herdr \
  --property=WorkingDirectory=/home/herdr/projects \
  --setenv=HOME=/home/herdr \
  --setenv=PATH=/home/herdr/.local/bin:/usr/local/bin:/usr/bin:/bin \
  /home/herdr/.local/bin/herdr server
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
