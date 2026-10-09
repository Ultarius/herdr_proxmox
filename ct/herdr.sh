#!/usr/bin/env bash
# Standalone Proxmox VE LXC installer inspired by the Paperclip two-stage layout.
set -Eeuo pipefail
# pct extracts unprivileged templates as mapped UID 100000. Proxmox-created
# parent directories must be traversable even when CI protects its keys with 077.
umask 022

usage() {
  cat <<'EOF'
Usage: bash ct/herdr.sh [options]
  --template VOLID  Debian 13 template (default: download newest standard template to local)
  --storage NAME    Root disk storage (default: HERDR_STORAGE or local-lvm)
  --ctid ID          Container ID (default: next free ID)
  --bridge NAME      Network bridge (default: vmbr0)
  --ip CIDR|dhcp     IPv4 configuration (default: dhcp)
  --gateway IP      Gateway for static IPv4
  --cores N         CPU cores (default: var_cpu or 4)
  --memory MB       Memory in MiB (default: var_ram or 8192)
  --disk GB         Root disk in GiB (default: var_disk or 32)
  --swap MB         Swap in MiB; 0 disables swap (default: var_swap or 2048)
  --ssh-key FILE    Public key for the herdr user (optional)
  --description TEXT  Container description (optional)
  --web             Install the dashboard with direct LAN access
  --no-web          Install CLI tools only; skip the dashboard question
  --help            Show this help
Run as root on a Proxmox VE host. Remote execution downloads this repository.
Without --template, download the newest available Debian 13 standard template to local.
Default root disk storage: local-lvm. Override with --storage or HERDR_STORAGE.
Remote --web requires a published release containing herdr-proxmox.tar.gz and its checksum.
Interactive installation asks whether to include the dashboard (default: yes).
Unattended installation defaults to CLI-only unless --web or HERDR_WEB=1 is supplied.
Set HERDR_REF to a source branch/tag for CLI installation; HERDR_RELEASE to a web release tag.
EOF
}
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
template=${HERDR_TEMPLATE:-} storage=${HERDR_STORAGE:-local-lvm} ctid='' bridge=vmbr0 ip=dhcp gateway=''
cores=${var_cpu:-4} memory=${var_ram:-8192} disk=${var_disk:-32} swap=${var_swap:-2048}
ssh_key=${HERDR_SSH_KEY:-} description='' web=${HERDR_WEB:-} archive='' checkout=''
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --web) web=1; shift ;;
    --no-web) web=0; shift ;;
    --template|--storage|--ctid|--bridge|--ip|--gateway|--cores|--memory|--disk|--swap|--ssh-key|--description)
      (($# >= 2)) || die "Missing value for $1"
      case "$1" in
        --template) template=$2 ;; --storage) storage=$2 ;; --ctid) ctid=$2 ;;
        --bridge) bridge=$2 ;; --ip) ip=$2 ;; --gateway) gateway=$2 ;;
        --cores) cores=$2 ;; --memory) memory=$2 ;; --disk) disk=$2 ;; --swap) swap=$2 ;; --ssh-key) ssh_key=$2 ;;
        --description) description=$2 ;;
      esac
      shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ $EUID == 0 ]] || die 'Run as root on the Proxmox host.'
case "$(uname -m)" in
  x86_64) architecture=amd64 ;;
  aarch64|arm64) architecture=arm64 ;;
  *) die 'Supported Proxmox host architectures: x86_64 and arm64.' ;;
esac
if [[ -z $web ]]; then
  web=0
  if [[ -t 0 ]]; then
    while true; do
      read -r -p 'Include the web dashboard? [Y/n] ' answer || die 'Installation cancelled.'
      case "$answer" in
        ''|y|Y|yes|YES) web=1; break ;;
        n|N|no|NO) break ;;
        *) printf 'Please answer yes or no.\n' ;;
      esac
    done
  fi
fi
for command in pct pvesh pvesm; do
  command -v "$command" >/dev/null || die "Missing Proxmox command: $command"
done
[[ $web == 0 || $web == 1 ]] || die 'HERDR_WEB must be 0 or 1.'
[[ $storage =~ ^[A-Za-z0-9_-]+$ ]] || die 'Invalid storage name.'
[[ $bridge =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Invalid bridge name.'
[[ $ip != *','* && $gateway != *','* ]] || die 'Network values must not contain commas.'
if [[ $ip == dhcp ]]; then
  [[ -z $gateway ]] || die '--gateway requires a static --ip.'
else
  [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ && -n $gateway ]] || die 'Static IPv4 requires CIDR and --gateway.'
  [[ $gateway =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Invalid gateway.'
fi
for value in "$cores" "$memory" "$disk"; do
  [[ $value =~ ^[1-9][0-9]*$ ]] || die 'Resource values must be positive integers.'
done
[[ $swap =~ ^[0-9]+$ ]] || die 'Swap must be a non-negative integer; 0 disables swap.'
trap '[[ -z $checkout ]] || rm -rf -- "$checkout"' EXIT
source_file=${BASH_SOURCE[0]:-}
repo=''
if [[ -n $source_file && -f $source_file ]]; then
  repo=$(cd -- "$(dirname -- "$source_file")/.." && pwd)
fi
if [[ -z $repo || ! -f $repo/install/herdr-install.sh ]]; then
  for command in curl tar; do command -v "$command" >/dev/null || die "Missing command: $command"; done
  checkout=$(mktemp -d)
  if ((web)); then
    release=${HERDR_RELEASE:-latest}
    [[ $release =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Invalid HERDR_RELEASE tag.'
    if [[ $release == latest ]]; then
      command -v python3 >/dev/null || die 'Missing command: python3 (needed to select a dashboard release).'
      curl -fsSL --retry 3 'https://api.github.com/repos/Ultarius/herdr_proxmox/releases?per_page=100' -o "$checkout/releases.json" || die 'Cannot read GitHub releases. Retry, or select a known tag with HERDR_RELEASE.'
      release=$(python3 - "$checkout/releases.json" <<'PY'
import json, sys, re
releases = json.load(open(sys.argv[1]))
required = {'herdr-proxmox.tar.gz', 'herdr-proxmox.tar.gz.sha256'}
available = [r for r in releases if not r.get('draft') and required.issubset({a.get('name') for a in r.get('assets', [])})]
# Version tags only: feature-branch alpha releases use alpha-<slug>-<hash>-b<n>
# and must be installed explicitly from the dashboard, never by the installer.
versioned = [r for r in available if re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', r.get('tag_name', ''))]
stable = [r for r in versioned if not r.get('prerelease')]
candidates = stable or versioned
if not candidates:
    sys.exit('No published release contains the dashboard archive and checksum.')
print(max(candidates, key=lambda r: r.get('published_at') or '')['tag_name'])
PY
      ) || die 'No installable dashboard release was found.'
      [[ $release =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Unsupported release tag.'
    fi
    base="https://github.com/Ultarius/herdr_proxmox/releases/download/$release"
    printf 'Downloading dashboard release: %s\n' "$release"
    curl -fSL --retry 3 "$base/herdr-proxmox.tar.gz" -o "$checkout/herdr-proxmox.tar.gz" || die 'No installable dashboard release is available. Contact the repository maintainer, or rerun with --no-web for CLI-only installation.'
    curl -fSL --retry 3 "$base/herdr-proxmox.tar.gz.sha256" -o "$checkout/herdr-proxmox.tar.gz.sha256"
    (cd "$checkout" && sha256sum -c herdr-proxmox.tar.gz.sha256) || die 'Release checksum failed.'
    tar -xzf "$checkout/herdr-proxmox.tar.gz" -C "$checkout"
    repo="$checkout/herdr-proxmox"
  else
    ref=${HERDR_REF:-main}
    [[ $ref =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Invalid HERDR_REF; use a branch/tag without slashes or a commit SHA.'
    curl -fSL --retry 3 "https://codeload.github.com/Ultarius/herdr_proxmox/tar.gz/$ref" -o "$checkout/source.tar.gz"
    mkdir "$checkout/source"
    tar -xzf "$checkout/source.tar.gz" --strip-components=1 -C "$checkout/source"
    repo="$checkout/source"
  fi
fi
installer="$repo/install/herdr-install.sh"
[[ -f $installer ]] || die 'Missing install/herdr-install.sh; use a complete checkout.'
agent_installer="$(dirname -- "$installer")/agents-install.sh"
[[ -f $agent_installer ]] || die 'Missing install/agents-install.sh; use a complete checkout.'
if ((web)); then
  [[ -f $repo/web/public/index.html && -f $repo/web/public/main.dart.js && -f $repo/web/public/dashboard/index.html ]] || die 'Build web assets first using scripts/build-web.sh.'
fi
if [[ -z $template ]]; then
  command -v pveam >/dev/null || die 'Missing Proxmox command: pveam'
  pveam update
  filename=$(pveam available --section system | awk -v arch="$architecture" '$2 ~ ("^debian-13-standard_.*_" arch "\\.tar\\.(zst|gz|xz)$") {print $2}' | sort -V | tail -n 1)
  [[ -n $filename ]] || die 'No Debian 13 standard template available. Supply --template explicitly.'
  pveam download local "$filename"
  template="local:vztmpl/$filename"
fi
[[ $template =~ ^[A-Za-z0-9_-]+:vztmpl/debian-13[^/]*\.tar\.(zst|gz|xz)$ ]] || die 'Select a Debian 13 template volume.'
[[ $template == *_"$architecture".tar.* ]] || die "Template must match the host architecture ($architecture). Select a Debian 13 standard $architecture template."
if [[ -n $ssh_key ]]; then
  [[ -f $ssh_key ]] || die 'Public key file not found.'
  # Reject private keys and unsupported/malformed public-key files before creating a CT.
  ssh-keygen -l -f "$ssh_key" >/dev/null || die 'Invalid SSH public key.'
  [[ $(head -c 4 "$ssh_key") == ssh- || $(head -c 6 "$ssh_key") == ecdsa- ]] || die 'Supply a public key, not a private key.'
fi
[[ -n $ctid ]] || ctid=$(pvesh get /cluster/nextid)
[[ $ctid =~ ^[1-9][0-9]{2,8}$ ]] || die 'Container ID must be an integer of at least 100.'
[[ ! -e /etc/pve/lxc/$ctid.conf && ! -e /etc/pve/qemu-server/$ctid.conf ]] || die "ID $ctid already exists."
pvesm path "$template" >/dev/null
network="name=eth0,bridge=$bridge,ip=$ip,ip6=manual,type=veth,firewall=1"
[[ -z $gateway ]] || network+=",gw=$gateway"
metadata=()
[[ -z $description ]] || metadata=(--description "$description")
created=0
trap 'rc=$?; [[ -z $archive ]] || rm -f -- "$archive"; [[ -z $checkout ]] || rm -rf -- "$checkout"; if ((rc != 0 && created)); then printf "Installation failed. CT %s is preserved for diagnosis; inspect it with pct enter %s.\n" "$ctid" "$ctid" >&2; fi' EXIT
pct create "$ctid" "$template" --hostname herdr --ostype debian \
  --arch "$architecture" --features nesting=1 \
  --unprivileged 1 --cores "$cores" --memory "$memory" --swap "$swap" \
  --rootfs "$storage:$disk" --net0 "$network" --onboot 1 --tags 'ai;dev-tools' "${metadata[@]}"
created=1
pct start "$ctid"
pct push "$ctid" "$installer" /root/herdr-install.sh --perms 0700
pct push "$ctid" "$agent_installer" /root/agents-install.sh --perms 0700
if [[ -n $ssh_key ]]; then
  pct push "$ctid" "$ssh_key" /root/herdr-authorized-key --perms 0600
  pct exec "$ctid" -- bash /root/herdr-install.sh --ssh-key /root/herdr-authorized-key
else
  pct exec "$ctid" -- bash /root/herdr-install.sh
fi
if ((web)); then
  archive=$(mktemp)
  tar -C "$repo" -czf "$archive" web/public web/gateway install/web-install.sh install/dashboard-update.py install/sdk-install.py install/operator-admin.py install/dev-tools-install.sh install/flutter-release.py install/build-install.sh
  pct push "$ctid" "$archive" /root/herdr-web.tar.gz --perms 0600
  pct exec "$ctid" -- mkdir -p /opt/herdr-web
  pct exec "$ctid" -- tar -xzf /root/herdr-web.tar.gz -C /opt/herdr-web
  pct push "$ctid" "$repo/install/dashboard-update.py" /opt/herdr-web/install/dashboard-update.py --perms 0644
  if [[ -f $repo/VERSION ]]; then
    pct push "$ctid" "$repo/VERSION" /opt/herdr-web/VERSION --perms 0644
  fi
  pct exec "$ctid" -- bash /opt/herdr-web/install/web-install.sh
  pct exec "$ctid" -- rm -f /root/herdr-web.tar.gz
  printf 'Browser access: http://<container-ip>:8787 (allow TCP 8787 from your LAN in any enabled Proxmox firewall).\n'
  printf 'Dashboard login token: pct exec %s -- cat /home/herdr/.config/herdr-web/token\n' "$ctid"
  printf 'SSH is optional; add your public key later on the dashboard CLI accounts page.\n'
fi
printf '\nHerdr installed in CT %s. Start it as the herdr user:\n' "$ctid"
printf '  pct enter %s\n  su - herdr\n  herdr\n' "$ctid"
if [[ -n $ssh_key ]]; then
  printf 'Or connect with: ssh herdr@<container-ip>\n'
fi
printf 'Find its IP with: pct exec %s -- hostname -I\n' "$ctid"
