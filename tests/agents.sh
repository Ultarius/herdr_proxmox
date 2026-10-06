#!/usr/bin/env bash
# Check user context and failure propagation without downloading/installing CLIs.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo/install/agents-install.sh"
log=$(mktemp)
trap 'rm -f -- "$log" "$log.config"' EXIT
curl() {
  local output
  [[ " $* " == *' --compressed '* && " $* " == *' --http1.1 '* && " $* " == *' --retry-all-errors '* ]] || return 1
  while (($#)); do
    if [[ $1 == -o ]]; then output=$2; break; fi
    shift
  done
  printf '# mock installer\n' >"$output"
}
runuser() {
  [[ $1 == -u && $2 == herdr && $3 == -- ]]
  printf '%s\n' "$*" >>"$log"
  if [[ " $* " == *' bash -s '* ]]; then
    local argument config=''
    for argument in "$@"; do
      [[ $argument != CURL_HOME=* ]] || config=${argument#CURL_HOME=}
    done
    [[ -n $config && -r $config/.curlrc ]]
    grep -qx 'http1.1' "$config/.curlrc"
    grep -qx 'retry-all-errors' "$config/.curlrc"
    grep -qx 'retry = 3' "$config/.curlrc"
    printf '%s\n' "$config" >>"$log.config"
  fi
  [[ ${FAIL_AGENTS:-0} == 0 || "$*" != *'npm install'* ]]
}
install_agents
while IFS= read -r config; do
  [[ ! -e $config ]] || { echo 'Temporary curl configuration leaked.' >&2; exit 1; }
done <"$log.config"
for command in codex claude opencode agy; do
  grep -q "/home/herdr/.local/bin/$command --version" "$log"
done
grep -q 'npm install --global @openai/codex@latest' "$log"
grep -q 'bash -s -- stable' "$log"
grep -q 'bash -s -- --no-modify-path' "$log"
grep -q 'bash -s -- --dir /home/herdr/.local/bin' "$log"
grep -q 'ln -sfn /home/herdr/.opencode/bin/opencode' "$log"
# Invoke in a fresh shell so errexit is active, not suppressed by an if test.
export -f curl runuser
export log
if FAIL_AGENTS=1 bash -c 'source "$1"; install_agents' _ "$repo/install/agents-install.sh"; then
  echo 'Installation failure was swallowed.' >&2
  exit 1
fi
while IFS= read -r config; do
  [[ ! -e $config ]] || { echo 'Temporary curl configuration leaked after failure.' >&2; exit 1; }
done <"$log.config"
printf 'Agent installer mock tests passed.\n'
