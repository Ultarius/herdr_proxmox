#!/usr/bin/env bash
# Proxmox host adaptation of the Community Scripts GitHub Runner installer:
# https://github.com/community-scripts/ProxmoxVE/blob/main/ct/github-runner.sh
# This standalone installer uses the official actions/runner distribution.
set -Eeuo pipefail

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  cat <<'EOF'
Usage: bash ct/github-runner.sh [--configure|--uninstall]
Run as root on a dedicated x64 Proxmox VE lab host.
Installs a repository runner for Ultarius/herdr_proxmox with label proxmox-ci.
Prompts for a short-lived registration token from repository Settings >
Actions > Runners > New self-hosted runner. Do not supply a personal access token.
HERDR_REF selects the repository ref for the root helper (default: main).
Installation detects the Debian 13 template and prompts for host storage/network.
--configure saves host defaults and updates the CI helper for an existing runner.
--uninstall stops/unregisters the runner and removes its local files and account.
Removal prompts for a removal token from Settings > Actions > Runners >
select this runner > Remove. Finish active CI and resolve retained CT state first.
EOF
  exit 0
fi
[[ $# == 0 || ( $# == 1 && ( $1 == --uninstall || $1 == --configure ) ) ]] || die 'Use --help for usage.'
[[ $EUID == 0 ]] || die 'Run as root on the dedicated Proxmox host.'
runner_user=github-runner
runner_dir=/opt/actions-runner

uninstall_runner() {
  local runner_home=/home/github-runner account_home removal_token
  [[ -d $runner_dir && ! -L $runner_dir && $(realpath -e -- "$runner_dir") == /opt/actions-runner ]] || die 'Expected runner directory not found or is redirected.'
  [[ -f $runner_dir/config.sh && ! -L $runner_dir/config.sh && -f $runner_dir/svc.sh && ! -L $runner_dir/svc.sh ]] || die 'Runner scripts are missing or redirected.'
  account_home=$(getent passwd "$runner_user" | cut -d: -f6)
  [[ $account_home == "$runner_home" && ! -L $runner_home && $(realpath -e -- "$runner_home") == /home/github-runner ]] || die 'Unexpected runner account home.'
  [[ $(id -u "$runner_user") != 0 ]] || die 'Refusing to remove a root account.'
  if [[ -f $runner_dir/.runner ]]; then
    jq -e '.gitHubUrl == "https://github.com/Ultarius/herdr_proxmox"' "$runner_dir/.runner" >/dev/null || die 'This runner belongs to a different repository.'
  fi
  [[ -t 0 ]] || die 'Run interactively to enter the removal token.'
  if pgrep -u "$runner_user" -f '(^|/)Runner.Worker([[:space:]]|$)' >/dev/null; then
    die 'A runner job is active. Finish the job before uninstalling.'
  fi
  # Retained state may identify a CT whose cleanup failed. Preserve that evidence.
  shopt -s nullglob
  local states=("$runner_dir"/_work/_temp/herdr-ci-*.state)
  ((${#states[@]} == 0)) || die 'Retained CI state exists under _work/_temp. Resolve the owned test CT before uninstalling.'
  if [[ -f $runner_dir/.runner ]]; then
    printf 'Get the removal token from Settings > Actions > Runners > this runner > Remove.\n'
    read -r -s -p 'Runner removal token: ' removal_token
    printf '\n'
    [[ -n $removal_token ]] || die 'A removal token is required.'
  fi
  cd -- "$runner_dir"
  if [[ -f .service ]]; then
    ./svc.sh stop
    ./svc.sh uninstall
  fi
  exec 8>/run/lock/herdr-proxmox-ci.lock
  flock -n 8 || die 'A Proxmox CI test is still active; local files are preserved.'
  states=("$runner_dir"/_work/_temp/herdr-ci-*.state)
  ((${#states[@]} == 0)) || die 'CI state appeared during shutdown; resolve the test CT before uninstalling.'
  if [[ -f .runner ]]; then
    # If GitHub rejects the token, leave local credentials and files for a retry.
    runuser -u "$runner_user" -- ./config.sh remove --token "$removal_token"
    unset removal_token
  fi
  cd /
  # All recursive targets above were resolved and checked against fixed paths.
  mountpoint -q "$runner_dir" && die 'Runner directory is a mount point; local files are preserved.'
  mountpoint -q "$runner_home" && die 'Runner home is a mount point; local files are preserved.'
  rm -f -- /etc/sudoers.d/herdr-proxmox-ci /usr/local/sbin/herdr-proxmox-ci /etc/herdr-proxmox-ci.conf
  userdel "$runner_user"
  rm -rf --one-file-system -- "$runner_dir" "$runner_home"
  printf 'GitHub runner, service, account, files and CI sudo helper removed.\n'
}
if [[ ${1:-} == --uninstall ]]; then
  uninstall_runner
  exit 0
fi

configure_host() {
  local template=${PVE_TEMPLATE:-} storage=${PVE_STORAGE:-} bridge=${PVE_BRIDGE:-vmbr0}
  local ip=${PVE_IP:-dhcp} gateway=${PVE_GATEWAY:-} store answer
  local template_stores disk_stores
  template_stores=$(pvesm status --content vztmpl | awk 'NR > 1 && $3 == "active" {print $1}')
  disk_stores=$(pvesm status --content rootdir | awk 'NR > 1 && $3 == "active" {print $1}')
  if [[ -z $template ]]; then
    template=$(
      for store in $template_stores; do
        pveam list "$store" | awk '$1 ~ /:vztmpl\/debian-13-standard_.*\.tar\.(zst|gz|xz)$/ {print $1}'
      done | sort -V | tail -n 1
    )
  fi
  [[ -n $template ]] || die 'Download a Debian 13 template first using pveam, or set PVE_TEMPLATE.'
  printf 'Detected Debian template: %s\n' "$template"
  read -r -p "Template volume [$template]: " answer
  template=${answer:-$template}
  [[ $template =~ ^[A-Za-z0-9_-]+:vztmpl/debian-13[^/]*\.tar\.(zst|gz|xz)$ ]] || die 'Invalid Debian 13 template volume.'
  pvesm path "$template" >/dev/null
  [[ -n $disk_stores ]] || die 'No active storage supports container root disks.'
  if [[ -z $storage ]]; then
    if grep -qxF local-lvm <<<"$disk_stores"; then storage=local-lvm
    else storage=$(head -n 1 <<<"$disk_stores"); fi
  fi
  printf 'Available container disk storage:\n%s\n' "$disk_stores"
  read -r -p "Root disk storage [$storage]: " answer
  storage=${answer:-$storage}
  grep -qxF "$storage" <<<"$disk_stores" || die 'Choose active storage supporting rootdir.'
  read -r -p "Network bridge [$bridge]: " answer; bridge=${answer:-$bridge}
  [[ $bridge =~ ^[A-Za-z0-9_.-]+$ && -d /sys/class/net/$bridge/bridge ]] || die 'Choose an existing network bridge.'
  read -r -p "IPv4 address (dhcp or CIDR) [$ip]: " answer; ip=${answer:-$ip}
  if [[ $ip == dhcp ]]; then gateway=''
  else
    [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die 'Invalid IPv4 CIDR.'
    read -r -p "IPv4 gateway [$gateway]: " answer; gateway=${answer:-$gateway}
    [[ $gateway =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Static IPv4 requires a gateway.'
  fi
  printf 'DEFAULT_PVE_TEMPLATE=%q\nDEFAULT_PVE_STORAGE=%q\nDEFAULT_PVE_BRIDGE=%q\nDEFAULT_PVE_IP=%q\nDEFAULT_PVE_GATEWAY=%q\n' \
    "$template" "$storage" "$bridge" "$ip" "$gateway" > "$scratch/host.conf"
  install -o root -g root -m 0600 "$scratch/host.conf" /etc/herdr-proxmox-ci.conf
}

install_ci_helper() {
  local source_file=${BASH_SOURCE[0]:-} helper=''
  if [[ -n $source_file && -f $source_file ]]; then
    helper="$(cd -- "$(dirname -- "$source_file")/.." && pwd)/scripts/proxmox-ci-root.sh"
  fi
  if [[ -n $helper && -f $helper ]]; then
    cp -- "$helper" "$scratch/proxmox-ci-root.sh"
  else
    curl -fsSL --retry 3 "https://raw.githubusercontent.com/Ultarius/herdr_proxmox/$ref/scripts/proxmox-ci-root.sh" \
      -o "$scratch/proxmox-ci-root.sh"
  fi
  bash -n "$scratch/proxmox-ci-root.sh"
  install -o root -g root -m 0755 "$scratch/proxmox-ci-root.sh" /usr/local/sbin/herdr-proxmox-ci
}
[[ $(uname -m) == x86_64 ]] || die 'The workflow requires a Linux x64 runner.'
for tool in pct pvesh pvesm pveam apt-get systemctl curl; do
  command -v "$tool" >/dev/null || die "Missing host command: $tool"
done
[[ -t 0 ]] || die 'Run interactively so the registration token can be entered securely.'
ref=${HERDR_REF:-main}
[[ $ref =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Invalid HERDR_REF; use a branch/tag without slashes or a commit SHA.'
scratch=$(mktemp -d)
trap 'unset registration_token; rm -rf -- "$scratch"' EXIT
if [[ ${1:-} == --configure ]]; then
  [[ -f $runner_dir/.runner ]] || die 'Install and register the runner first.'
  pgrep -u "$runner_user" -f '(^|/)Runner.Worker([[:space:]]|$)' >/dev/null && die 'Finish the active runner job before configuring.'
  configure_host
  install_ci_helper
  printf 'Host defaults saved. Run CI with run_lxc selected; GitHub PVE variables are optional overrides.\n'
  exit 0
fi
[[ ! -e $runner_dir && ! -L $runner_dir ]] || die "$runner_dir already exists; preserve or remove the old runner separately."
! getent passwd "$runner_user" >/dev/null || die "Account $runner_user already exists; inspect the existing installation first."
[[ ! -e /usr/local/sbin/herdr-proxmox-ci && ! -e /etc/sudoers.d/herdr-proxmox-ci ]] || die 'A Herdr CI root helper already exists.'
configure_host

printf 'This runner will execute trusted repository installers as root on this lab host.\n'
printf 'Get a registration token at:\n  https://github.com/Ultarius/herdr_proxmox/settings/actions/runners/new\n'
read -r -s -p 'Runner registration token: ' registration_token
printf '\n'
[[ -n $registration_token ]] || die 'A registration token is required.'

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl jq git sudo \
  tar gzip python3 openssh-client util-linux
release=$(curl -fsSL --retry 3 -H 'Accept: application/vnd.github+json' \
  https://api.github.com/repos/actions/runner/releases/latest)
version=$(jq -er '.tag_name | ltrimstr("v")' <<<"$release")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Unexpected runner release version.'
asset="actions-runner-linux-x64-$version.tar.gz"
digest=$(jq -er --arg asset "$asset" '.assets[] | select(.name == $asset) | .digest' <<<"$release")
[[ $digest =~ ^sha256:[a-f0-9]{64}$ ]] || die 'Runner release has no usable SHA-256 digest.'
curl -fSL --retry 3 "https://github.com/actions/runner/releases/download/v$version/$asset" -o "$scratch/runner.tar.gz"
printf '%s  %s\n' "${digest#sha256:}" "$scratch/runner.tar.gz" | sha256sum -c -

install_ci_helper

useradd --system --user-group --create-home --home-dir /home/github-runner --shell /bin/bash "$runner_user"
install -d -o "$runner_user" -g "$runner_user" -m 0755 "$runner_dir"
tar -xzf "$scratch/runner.tar.gz" -C "$runner_dir" --no-same-owner
cd "$runner_dir"
bash bin/installdependencies.sh
chown -R "$runner_user:$runner_user" "$runner_dir"
runner_name="herdr-proxmox-ci-$(hostname -s)"
runuser -u "$runner_user" -- ./config.sh --unattended \
  --url https://github.com/Ultarius/herdr_proxmox --token "$registration_token" \
  --name "$runner_name" --labels proxmox-ci --work _work
unset registration_token

printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/herdr-proxmox-ci\n' "$runner_user" > "$scratch/sudoers"
visudo -cf "$scratch/sudoers"
install -o root -g root -m 0440 "$scratch/sudoers" /etc/sudoers.d/herdr-proxmox-ci
./svc.sh install "$runner_user"
./svc.sh start
printf '\nRunner %s installed with labels self-hosted, linux, x64, proxmox-ci.\n' "$runner_name"
printf 'Host defaults saved. Create the proxmox-ci GitHub environment, then dispatch CI on main with run_lxc selected.\n'
printf 'Service status: cd /opt/actions-runner && ./svc.sh status\n'
