#!/usr/bin/env bash
# Cleanup safety tests: no actual host commands or containers are used.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
source "$repo/scripts/proxmox-ci.sh"
log="$tmp/commands"
marker=herdr-ci-0123456789abcdef0123456789abcdef
config_marker=$marker
stop_failure=0 destroy_failure=0
pct() {
  printf '%s\n' "$*" >>"$log"
  case "$1" in
    config) printf 'description: %s\nunprivileged: 1\n' "$config_marker" ;;
    status) printf 'status: running\n' ;;
    stop) [[ $stop_failure == 0 ]] ;;
    destroy) [[ $destroy_failure == 0 ]] ;;
    *) return 1 ;;
  esac
}
timeout() { shift; "$@"; }
state="$tmp/state"
printf '987654321\n%s\n' "$marker" >"$state"
touch "$state.key"
config_marker=herdr-ci-ffffffffffffffffffffffffffffffff
if cleanup "$state" >"$tmp/output" 2>&1; then exit 1; fi
[[ -f $state && -f $state.key ]]
if grep -Eq '^(stop|destroy) ' "$log"; then exit 1; fi
config_marker=$marker
stop_failure=1
if cleanup "$state" >"$tmp/output" 2>&1; then exit 1; fi
[[ -f $state ]]
if grep -q '^destroy ' "$log"; then exit 1; fi
stop_failure=0 destroy_failure=1
if cleanup "$state" >"$tmp/output" 2>&1; then exit 1; fi
[[ -f $state && -f $state.key ]]
destroy_failure=0
# Real pct output encodes the newline added when reading config comments.
config_marker="$marker%0A"
cleanup "$state" >"$tmp/output"
[[ ! -e $state && ! -e $state.key ]]
grep -q '^destroy 987654321 --purge 1$' "$log"
before=$(wc -l <"$log")
cleanup "$state"
[[ $(wc -l <"$log") == "$before" ]]
printf 'not-a-container\n%s\n' "$marker" >"$state"
if cleanup "$state" >"$tmp/output" 2>&1; then exit 1; fi
[[ $(wc -l <"$log") == "$before" ]]
# Additional description text must never grant cleanup ownership.
for suffix in '%0Aextra' '%0A%0A' 'extra'; do
  if owns_config "description: $marker$suffix" "$marker"; then exit 1; fi
done
printf 'Proxmox CI ownership and cleanup failure tests passed.\n'
