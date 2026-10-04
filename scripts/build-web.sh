#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# On a fresh SDK, first-run notices can precede --machine JSON. Initialize
# separately so only the version response is captured by the parser.
flutter config --no-analytics
version=$(flutter --version --machine)
printf '%s' "$version" | python3 -c 'import json,sys; v=json.load(sys.stdin)["frameworkVersion"]; assert v=="3.44.8", "Flutter 3.44.8 required, found "+v'
mkdir -p web/public/dashboard
cd web/dashboard
flutter pub get
flutter analyze
flutter test
flutter build web --release --base-href /dashboard/ --no-web-resources-cdn
cp -R build/web/. ../public/dashboard/
cd ../shell
dart pub get
dart analyze
# Jaspr mounts a client-rendered DOM shell; no server rendering or CLI generator needed.
dart compile js lib/main.dart -O2 -o ../public/main.dart.js
cp web/index.html ../public/index.html
printf '\nBuilt web/public. Copy the repository to your Proxmox host to install.\n'
