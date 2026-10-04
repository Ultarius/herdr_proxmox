#!/usr/bin/env bash
# Run as root inside the LXC; vendor installers run as the herdr user.
set -Eeuo pipefail

install_agents() (
  local agent_home=/home/herdr agent_path=/home/herdr/.local/bin:/usr/local/bin:/usr/bin:/bin
  local staging
  staging=$(mktemp -d)
  trap 'rm -rf -- "$staging"' EXIT
  curl -fsSL --compressed --retry 3 --connect-timeout 10 --max-time 120 https://claude.ai/install.sh -o "$staging/claude.sh"
  curl -fsSL --compressed --retry 3 --connect-timeout 10 --max-time 120 https://opencode.ai/install -o "$staging/opencode.sh"
  curl -fsSL --compressed --retry 3 --connect-timeout 10 --max-time 120 https://antigravity.google/cli/install.sh -o "$staging/agy.sh"
  local installer
  for installer in claude opencode agy; do
    bash -n "$staging/$installer.sh" || { printf 'Invalid %s installer download.\n' "$installer" >&2; exit 1; }
  done

  # Keep npm installs and future updates owned by the service user.
  runuser -u herdr -- env HOME="$agent_home" PATH="$agent_path" npm config set prefix "$agent_home/.local"
  runuser -u herdr -- env HOME="$agent_home" PATH="$agent_path" npm install --global @openai/codex@latest
  runuser -u herdr -- env HOME="$agent_home" PATH="$agent_path" bash -s -- stable <"$staging/claude.sh"
  runuser -u herdr -- env HOME="$agent_home" PATH="$agent_path" bash -s -- --no-modify-path <"$staging/opencode.sh"
  runuser -u herdr -- ln -sfn "$agent_home/.opencode/bin/opencode" "$agent_home/.local/bin/opencode"
  runuser -u herdr -- env HOME="$agent_home" PATH="$agent_path" bash -s -- --dir "$agent_home/.local/bin" <"$staging/agy.sh"
  # Antigravity's bootstrap can mask a native setup failure; verify every CLI.
  local command
  for command in codex claude opencode agy; do
    runuser -u herdr -- env HOME="$agent_home" PATH="$agent_path" "$agent_home/.local/bin/$command" --version
  done
)

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  [[ $EUID == 0 ]] || { echo 'Run as root inside the container.' >&2; exit 1; }
  source /etc/os-release
  [[ $ID == debian && $VERSION_ID == 13 ]] || { echo 'Debian 13 is required.' >&2; exit 1; }
  [[ $(getent passwd herdr | cut -d: -f6) == /home/herdr ]] || { echo 'Create the herdr user with herdr-install.sh first.' >&2; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=5 update
  apt-get -o Acquire::Retries=5 install -y ca-certificates curl nodejs npm jq unzip
  install_agents
fi
