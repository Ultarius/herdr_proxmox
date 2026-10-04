#!/usr/bin/env bash
# Reproduce first-run stdout without downloading an SDK or building an app.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/project/scripts" "$tmp/project/web/dashboard" "$tmp/project/web/shell/lib" "$tmp/project/web/shell/web"
cp "$repo/scripts/build-web.sh" "$tmp/project/scripts/"
printf '<html>shell</html>\n' >"$tmp/project/web/shell/web/index.html"
export HERDR_BUILD_TEST_STATE="$tmp/initialized" HERDR_BUILD_TEST_LOG="$tmp/commands"
cat >"$tmp/bin/flutter" <<'EOF'
#!/usr/bin/env bash
set -eu
printf 'flutter %s\n' "$*" >>"$HERDR_BUILD_TEST_LOG"
case "$1" in
  config)
    [[ $2 == --no-analytics ]]
    echo 'Welcome to Flutter! First-run setup.'
    touch "$HERDR_BUILD_TEST_STATE"
    ;;
  --version)
    [[ -f $HERDR_BUILD_TEST_STATE ]] || echo 'Welcome to Flutter! First-run setup.'
    printf '{"frameworkVersion":"%s"}\n' "${HERDR_BUILD_TEST_VERSION:-3.44.8}"
    ;;
  build)
    mkdir -p build/web
    printf '<html>dashboard</html>\n' >build/web/index.html
    ;;
esac
EOF
cat >"$tmp/bin/dart" <<'EOF'
#!/usr/bin/env bash
set -eu
printf 'dart %s\n' "$*" >>"$HERDR_BUILD_TEST_LOG"
if [[ $1 == compile ]]; then printf '// compiled shell\n' >"${@: -1}"; fi
EOF
# Git Bash on Windows may need an explicitly supplied Python runtime.
if [[ -n ${HERDR_TEST_PYTHON:-} ]]; then
  cat >"$tmp/bin/python3" <<'EOF'
#!/usr/bin/env bash
exec "$HERDR_TEST_PYTHON" "$@"
EOF
fi
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH"
bash "$tmp/project/scripts/build-web.sh" >"$tmp/output"
[[ $(head -n 1 "$HERDR_BUILD_TEST_LOG") == 'flutter config --no-analytics' ]]
[[ -f $tmp/project/web/public/dashboard/index.html && -f $tmp/project/web/public/main.dart.js && -f $tmp/project/web/public/index.html ]]
# The fix must still enforce the pinned version and stop before building.
: >"$HERDR_BUILD_TEST_LOG"
if HERDR_BUILD_TEST_VERSION=3.44.7 bash "$tmp/project/scripts/build-web.sh" >"$tmp/output" 2>&1; then exit 1; fi
grep -q 'Flutter 3.44.8 required' "$tmp/output"
if grep -q '^flutter build ' "$HERDR_BUILD_TEST_LOG"; then exit 1; fi
printf 'Web build cold-start and version guard tests passed.\n'
