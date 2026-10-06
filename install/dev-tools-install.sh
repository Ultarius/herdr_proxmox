#!/usr/bin/env bash
# Explicit opt-in: install the project's pinned SDK for development agents.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root inside the development container.' >&2; exit 1; }
[[ $(getent passwd herdr | cut -d: -f6) == /home/herdr ]] || { echo 'Install the herdr user first.' >&2; exit 1; }
version=3.44.8
case "$(uname -m)" in
  x86_64) architecture=x64 ;;
  aarch64) architecture=arm64 ;;
  *) echo 'Unsupported Flutter architecture.' >&2; exit 1 ;;
esac
sdk="/home/herdr/.local/share/flutter-$version"
install -d -o herdr -g herdr -m 0755 /home/herdr/.local/share /home/herdr/.local/bin
if [[ ! -d $sdk ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=5 update
  apt-get -o Acquire::Retries=5 install -y ca-certificates curl git python3 xz-utils unzip libglu1-mesa
  staging=$(mktemp -d)
  trap 'rm -rf -- "$staging"' EXIT
  chmod 0755 "$staging"
  # Release archives live under /releases; /flutter contains engine artifacts.
  origin=https://storage.googleapis.com/flutter_infra_release/releases
  printf 'Downloading official Flutter release manifest.\n'
  curl -fsSL --http1.1 --retry 3 --retry-all-errors --connect-timeout 10 --max-time 120 "$origin/releases_linux.json" -o "$staging/releases.json"
  python3 "$(dirname -- "${BASH_SOURCE[0]}")/flutter-release.py" "$version" "$architecture" <"$staging/releases.json" >"$staging/selected"
  mapfile -t release <"$staging/selected"
  printf 'Downloading Flutter %s (%s): %s/%s\n' "$version" "$architecture" "$origin" "${release[0]}"
  curl -fsSL --http1.1 --retry 3 --retry-all-errors --connect-timeout 10 --max-time 1800 "$origin/${release[0]}" -o "$staging/sdk.tar.xz"
  printf '%s  %s\n' "${release[1]}" "$staging/sdk.tar.xz" | sha256sum --check --status
  chmod 0644 "$staging/sdk.tar.xz"
  install -d -o herdr -g herdr -m 0755 /home/herdr/.local/share /home/herdr/.local/bin
  install -d -o herdr -g herdr -m 0755 "$staging/unpack"
  runuser -u herdr -- tar --no-same-owner -xJf "$staging/sdk.tar.xz" -C "$staging/unpack"
  runuser -u herdr -- mv "$staging/unpack/flutter" "$sdk"
fi
runuser -u herdr -- env HOME=/home/herdr "$sdk/bin/flutter" config --no-analytics
runuser -u herdr -- env HOME=/home/herdr "$sdk/bin/flutter" --version --machine | python3 -c 'import json,sys; assert json.load(sys.stdin)["frameworkVersion"] == sys.argv[1], "Installed Flutter version does not match the project pin"' "$version"
for command in flutter dart; do
  link="/home/herdr/.local/bin/$command"
  if [[ -e $link || -L $link ]]; then
    [[ -L $link && $(readlink "$link") == "$sdk/bin/$command" ]] || { echo "Refusing to replace existing $link" >&2; exit 1; }
  else
    runuser -u herdr -- ln -s "$sdk/bin/$command" "$link"
  fi
done
printf 'Flutter %s ready for development agents. Use scripts/build-web.sh in the assigned checkout.\n' "$version"
