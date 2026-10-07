#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
check_runner="$PWD/scripts/check-evidence.py"
check() { python3 "$check_runner" "$@"; }
planned_checks=(flutter-config flutter-version flutter-pin flutter-dependencies flutter-analyze flutter-tests flutter-release shell-dependencies shell-analyze shell-compile)
trap 'check_status=$?; python3 "$check_runner" --finish "${planned_checks[@]}" || check_status=1; exit "$check_status"' EXIT
# On a fresh SDK, first-run notices can precede --machine JSON. Initialize
# separately so only the version response is captured by the parser.
check flutter-config flutter config --no-analytics
version=$(check flutter-version flutter --version --machine)
printf '%s' "$version" | check flutter-pin python3 -c 'import json,sys; v=json.load(sys.stdin)["frameworkVersion"]; assert v=="3.44.8", "Flutter 3.44.8 required, found "+v'
mkdir -p web/public/dashboard
cd web/dashboard
check flutter-dependencies flutter pub get
check flutter-analyze flutter analyze
check flutter-tests flutter test
check flutter-release flutter build web --release --base-href /dashboard/ --no-web-resources-cdn
cp -R build/web/. ../public/dashboard/
cd ../shell
check shell-dependencies dart pub get
check shell-analyze dart analyze
# Jaspr mounts a client-rendered DOM shell; no server rendering or CLI generator needed.
check shell-compile dart compile js lib/main.dart -O2 -o ../public/main.dart.js
cp web/index.html ../public/index.html
printf '\nBuilt web/public. Copy the repository to your Proxmox host to install.\n'
