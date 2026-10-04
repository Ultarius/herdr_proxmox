#!/usr/bin/env bash
# Run inside a dedicated Debian 13 LXC, as root. Safe to rerun after a failed install.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root inside the container.' >&2; exit 1; }
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]] || { echo 'Debian 13 is required.' >&2; exit 1; }
ssh_key=''
case "${1:-}" in
  '') [[ $# == 0 ]] || exit 1 ;;
  --ssh-key) [[ $# == 2 && -f $2 ]] || exit 1; ssh_key=$2 ;;
  *) echo 'Usage: herdr-install.sh [--ssh-key PUBLIC_KEY_FILE]' >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|aarch64) ;;
  *) echo 'Herdr requires x86_64 or aarch64.' >&2; exit 1 ;;
esac
export DEBIAN_FRONTEND=noninteractive
# apt handles the short network startup delay after pct start.
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y ca-certificates curl git ripgrep openssh-server ncurses-term
if ! id herdr >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash herdr
fi
[[ $(getent passwd herdr | cut -d: -f6) == /home/herdr ]] || { echo 'Existing herdr user has an unexpected home directory.' >&2; exit 1; }
install -d -o herdr -g herdr -m 0750 /home/herdr/projects
install -d -o herdr -g herdr -m 0755 /home/herdr/.local /home/herdr/.local/bin
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 https://herdr.dev/install.sh -o "$tmp"
# The official installer verifies the binary against the stable manifest SHA-256.
# Execute as herdr so its install/update directory remains user-owned.
install -o herdr -g herdr -m 0700 "$tmp" /home/herdr/.local/herdr-installer.sh
runuser -u herdr -- env HOME=/home/herdr HERDR_INSTALL_DIR=/home/herdr/.local/bin \
  sh /home/herdr/.local/herdr-installer.sh
rm -f /home/herdr/.local/herdr-installer.sh
runuser -u herdr -- /home/herdr/.local/bin/herdr --version
bash "$(dirname -- "${BASH_SOURCE[0]}")/agents-install.sh"
# Explicit PATH for SSH commands as well as interactive login shells.
cat >/etc/profile.d/herdr.sh <<'EOF'
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
EOF
if [[ -n $ssh_key ]]; then
  ssh-keygen -l -f "$ssh_key" >/dev/null
  [[ $(head -c 4 "$ssh_key") == ssh- || $(head -c 6 "$ssh_key") == ecdsa- ]] || { echo 'Expected a public key.' >&2; exit 1; }
  install -d -o herdr -g herdr -m 0700 /home/herdr/.ssh
  touch /home/herdr/.ssh/authorized_keys
  while IFS= read -r key || [[ -n $key ]]; do
    [[ -n $key ]] || continue
    grep -qxF -- "$key" /home/herdr/.ssh/authorized_keys || printf '%s\n' "$key" >>/home/herdr/.ssh/authorized_keys
  done <"$ssh_key"
  chown herdr:herdr /home/herdr/.ssh/authorized_keys
  chmod 0600 /home/herdr/.ssh/authorized_keys
fi
# Unlock even without an initial key so dashboard-added keys work later.
# A random, discarded password unlocks the account for key authentication.
# Password SSH authentication is disabled below.
if [[ $(passwd -S herdr | awk '{print $2}') == L ]]; then
  printf 'herdr:%s\n' "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')" | chpasswd
fi
cat >/etc/ssh/sshd_config.d/00-herdr.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
/usr/sbin/sshd -t
systemctl enable ssh
systemctl restart ssh
# Keep the user's systemd context alive after SSH logout.
loginctl enable-linger herdr
printf '\nInstallation verified. Run as herdr: cd ~/projects && herdr\n'
