#!/usr/bin/env bash
# Disposable lab-host smoke test. Cleanup requires an exact per-run CT marker.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

owns_config() {
  local config=$1 marker=$2
  # pct percent-encodes descriptions; the config parser appends a line ending.
  # Accept only the exact marker, optionally followed by that single encoded LF.
  grep -qxF "description: $marker" <<<"$config" || grep -qxF "description: $marker%0A" <<<"$config"
}

cleanup() {
  local state=$1 config ctid marker status
  [[ -f $state && ! -L $state ]] || return 0
  { read -r ctid; read -r marker; } <"$state"
  [[ $ctid =~ ^[1-9][0-9]{2,8}$ && $marker =~ ^herdr-ci-[a-f0-9]{32}$ ]] || { echo 'Invalid CI state; refusing cleanup.' >&2; return 1; }
  config=$(pct config "$ctid") || { echo 'Cannot verify container ownership; refusing cleanup.' >&2; return 1; }
  owns_config "$config" "$marker" || { echo 'Container ownership does not match; refusing cleanup.' >&2; return 1; }
  status=$(pct status "$ctid") || return 1
  if [[ $status != 'status: stopped' ]]; then
    timeout 60 pct stop "$ctid" || return 1
  fi
  pct destroy "$ctid" --purge 1 || return 1
  rm -f -- "$state" "$state.key" "$state.key.pub" "$state.known_hosts"
  printf 'Removed owned CI container %s.\n' "$ctid"
}

diagnostics() {
  local state=$1 results=$2 ctid marker config
  [[ -f $state ]] || return 0
  { read -r ctid; read -r marker; } <"$state"
  [[ $ctid =~ ^[1-9][0-9]{2,8}$ && $marker =~ ^herdr-ci-[a-f0-9]{32}$ ]] || return 1
  config=$(pct config "$ctid") || return 1
  owns_config "$config" "$marker" || return 1
  printf '%s\n' "$config" >"$results/container-config.txt"
  pct exec "$ctid" -- systemctl --no-pager --full status ssh herdr-web herdr-ci-session >"$results/services.log" 2>&1 || true
  pct exec "$ctid" -- journalctl --no-pager -u herdr-web -u herdr-ci-session -n 100 >"$results/journal.log" 2>&1 || true
  chmod 0644 "$results/"*
}

run_test() (
  local state=$1 results=$2 ctid marker ip public_key config
  : "${PVE_TEMPLATE:?Set PVE_TEMPLATE to a downloaded Debian 13 template volume}"
  : "${PVE_STORAGE:?Set PVE_STORAGE to container root-disk storage}"
  [[ ! -e $state && ! -L $state ]] || { echo 'CI state already exists; inspect it before reuse.' >&2; return 1; }
  for tool in pct pvesh pvesm flock ssh ssh-keygen python3 timeout; do command -v "$tool" >/dev/null; done
  [[ -f $repo/web/public/dashboard/main.dart.js ]] || { echo 'Download/build the web assets first.' >&2; return 1; }
  mkdir -p -- "$results"
  chmod 0755 "$results"
  # Serialize runs on this host, including runs from other repositories.
  exec 9>/run/lock/herdr-proxmox-ci.lock
  flock -x 9
  ctid=$(pvesh get /cluster/nextid)
  [[ $ctid =~ ^[1-9][0-9]{2,8}$ ]]
  marker="herdr-ci-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  umask 077
  printf '%s\n%s\n' "$ctid" "$marker" >"$state"
  finish() {
    local rc=$?
    trap - EXIT
    diagnostics "$state" "$results" || true
    cleanup "$state" >>"$results/cleanup.log" 2>&1 || rc=1
    chmod 0644 "$results/"* 2>/dev/null || true
    exit "$rc"
  }
  trap finish EXIT
  ssh-keygen -q -t ed25519 -N '' -f "$state.key"
  local network=(--bridge "${PVE_BRIDGE:-vmbr0}" --ip "${PVE_IP:-dhcp}")
  [[ -z ${PVE_GATEWAY:-} ]] || network+=(--gateway "$PVE_GATEWAY")
  bash "$repo/ct/herdr.sh" --template "$PVE_TEMPLATE" --storage "$PVE_STORAGE" \
    --ctid "$ctid" --description "$marker" --ssh-key "$state.key.pub" --web \
    "${network[@]}" >"$results/install.log" 2>&1 || {
      local install_rc=$?
      cat "$results/install.log" >&2
      return "$install_rc"
    }
  config=$(pct config "$ctid")
  grep -qx 'unprivileged: 1' <<<"$config"
  owns_config "$config" "$marker"
  # Verify the actual SSH login, with a host key read through the trusted PVE channel.
  ip=$(pct exec "$ctid" -- hostname -I | python3 -c 'import ipaddress,sys; print(next(str(a) for v in sys.stdin.read().split() if (a:=ipaddress.ip_address(v)).version == 4))')
  public_key=$(pct exec "$ctid" -- cat /etc/ssh/ssh_host_ed25519_key.pub)
  printf '%s %s\n' "$ip" "$public_key" >"$state.known_hosts"
  ssh -i "$state.key" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$state.known_hosts" "herdr@$ip" 'bash -lc "herdr --version"' >"$results/ssh.log" 2>&1
  # runtime.py runs as herdr; do not inherit the host's private-key umask here.
  pct exec "$ctid" -- install -d -m 0755 /opt/herdr-ci
  pct push "$ctid" "$repo/tests/ci/smoke.sh" /opt/herdr-ci/smoke.sh --perms 0755
  pct push "$ctid" "$repo/tests/ci/runtime.py" /opt/herdr-ci/runtime.py --perms 0644
  pct exec "$ctid" -- bash /opt/herdr-ci/smoke.sh >"$results/smoke.log" 2>&1 || {
    local smoke_rc=$?
    cat "$results/smoke.log" >&2
    return "$smoke_rc"
  }
  printf 'Real unprivileged LXC installation and smoke checks passed in CT %s.\n' "$ctid"
)

main() {
  [[ $EUID == 0 ]] || { echo 'Run on a dedicated Proxmox lab host as root.' >&2; exit 1; }
  [[ $# == 3 ]] || { echo 'Usage: proxmox-ci.sh run|cleanup STATE_FILE RESULTS_DIRECTORY' >&2; exit 1; }
  case "$1" in
    run) run_test "$2" "$3" ;;
    cleanup) mkdir -p -- "$3"; diagnostics "$2" "$3" || true; cleanup "$2" >>"$3/cleanup.log" 2>&1; chmod 0644 "$3/"* ;;
    *) exit 1 ;;
  esac
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
