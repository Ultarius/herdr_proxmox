#!/usr/bin/env bash
# Installed root-owned at /usr/local/sbin/herdr-proxmox-ci by ct/github-runner.sh.
# Repository installers still execute as root; only trusted lab workflows may use it.
set -Eeuo pipefail
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $EUID == 0 ]] || die 'Invoke the installed helper through sudo.'
[[ $# == 9 ]] || die 'Usage: herdr-proxmox-ci run|cleanup WORKSPACE STATE RESULTS TEMPLATE STORAGE BRIDGE IP GATEWAY'
action=$1 workspace=$2 state=$3 results=$4
template=$5 storage=$6 bridge=$7 ip=$8 gateway=$9
[[ $action == run || $action == cleanup ]] || die 'Unknown operation.'
# Match the fixed --work directory and repository used by the host installer.
expected_workspace=/opt/actions-runner/_work/herdr_proxmox/herdr_proxmox
[[ $(realpath -e -- "$workspace") == "$expected_workspace" ]] || die 'Unexpected runner workspace.'
workspace=$expected_workspace
[[ $(realpath -m -- "$results") == "$workspace/ci-results" && ! -L $results ]] || die 'Unexpected results directory.'
[[ $(realpath -m -- "$(dirname -- "$state")") == /opt/actions-runner/_work/_temp ]] || die 'Unexpected state directory.'
[[ $(basename -- "$state") =~ ^herdr-ci-[0-9]+-[0-9]+\.state$ && ! -L $state ]] || die 'Unexpected state file.'
state="/opt/actions-runner/_work/_temp/$(basename -- "$state")"
results="$workspace/ci-results"
[[ -f $workspace/scripts/proxmox-ci.sh && ! -L $workspace/scripts && ! -L $workspace/scripts/proxmox-ci.sh ]] || die 'Missing CI entry point.'
if [[ $action == run ]]; then
  defaults=/etc/herdr-proxmox-ci.conf
  if [[ -e $defaults ]]; then
    [[ -f $defaults && ! -L $defaults && $(stat -c '%u:%a' "$defaults") == 0:600 ]] || die 'Host defaults must be a root-owned file with mode 0600.'
    source "$defaults"
  fi
  template=${template:-${DEFAULT_PVE_TEMPLATE:-}}
  storage=${storage:-${DEFAULT_PVE_STORAGE:-}}
  bridge=${bridge:-${DEFAULT_PVE_BRIDGE:-vmbr0}}
  ip=${ip:-${DEFAULT_PVE_IP:-dhcp}}
  gateway=${gateway:-${DEFAULT_PVE_GATEWAY:-}}
  [[ $ip != dhcp ]] || gateway=''
  [[ $template =~ ^[A-Za-z0-9_-]+:vztmpl/debian-13[^/]*\.tar\.(zst|gz|xz)$ ]] || die 'Set PVE_TEMPLATE to a Debian 13 template volume.'
  [[ $storage =~ ^[A-Za-z0-9_-]+$ ]] || die 'Set PVE_STORAGE to root-disk storage.'
  [[ $bridge =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Invalid PVE_BRIDGE.'
  [[ $ip == dhcp || $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die 'Invalid PVE_IP.'
  [[ -z $gateway || $gateway =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Invalid PVE_GATEWAY.'
fi
cd -- "$workspace"
# Return diagnostics to the runner so checkout can clean them on the next run.
finish() {
  local rc=$?
  trap - EXIT
  if [[ -d $results && ! -L $results ]]; then
    chown -R --no-dereference github-runner:github-runner "$results" || rc=1
  fi
  exit "$rc"
}
trap finish EXIT
/usr/bin/env PVE_TEMPLATE="$template" PVE_STORAGE="$storage" \
  PVE_BRIDGE="$bridge" PVE_IP="$ip" PVE_GATEWAY="$gateway" \
  /usr/bin/bash "$workspace/scripts/proxmox-ci.sh" "$action" "$state" "$results"
