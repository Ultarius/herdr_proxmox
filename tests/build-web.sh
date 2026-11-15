#!/usr/bin/env bash
# Reproduce first-run stdout without downloading an SDK or building an app.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/project/scripts" "$tmp/project/web/dashboard" "$tmp/project/web/dashboard_state" "$tmp/project/web/jaspr_dashboard/web/css" "$tmp/project/web/jaspr_dashboard/web/vendor/xterm"
# Exercise the actual build entrypoint with its evidence helper present, just
# as it is in a checkout. Copying only the shell entrypoint hides dependencies.
cp "$repo/scripts/build-web.sh" "$repo/scripts/check-evidence.py" "$tmp/project/scripts/"
# The build entrypoint reads the version from the project's own declaration, so
# the fixture must declare one exactly as a real checkout does.
cp "$repo/.fvmrc" "$tmp/project/"
printf '<html>DOM dashboard</html>\n' >"$tmp/project/web/jaspr_dashboard/web/index.html"
printf '/* theme */\n' >"$tmp/project/web/jaspr_dashboard/web/css/tokens.css"
printf '// terminal\n' >"$tmp/project/web/jaspr_dashboard/web/vendor/xterm/xterm.js"
printf '<svg/>\n' >"$tmp/project/web/jaspr_dashboard/web/icons.svg"
printf '/* application */\n' >"$tmp/project/web/jaspr_dashboard/web/styles.css"
printf '// source must not ship\n' >"$tmp/project/web/jaspr_dashboard/web/main.dart"
mkdir -p "$tmp/project/web/jaspr_dashboard/build/release"
printf '// stale copied source must not ship\n' >"$tmp/project/web/jaspr_dashboard/build/release/main.dart"
mkdir -p "$tmp/project/web/public/dashboard/canvaskit"
printf '// obsolete renderer\n' >"$tmp/project/web/public/dashboard/flutter_service_worker.js"
printf 'obsolete\n' >"$tmp/project/web/public/dashboard/canvaskit/obsolete.wasm"
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
    printf '{"frameworkVersion":"%s"}\n' "${HERDR_BUILD_TEST_VERSION:-3.47.9}"
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
if [[ $1 == compile ]]; then printf '// compiled DOM\n' >"${@: -1}"; fi
EOF
# Git Bash on Windows may need an explicitly supplied Python runtime.
if [[ -n ${HERDR_TEST_PYTHON:-} ]]; then
  cat >"$tmp/bin/python3" <<'EOF'
#!/usr/bin/env bash
exec "$HERDR_TEST_PYTHON" "$@"
EOF
fi
# Native Windows Python cannot execute extensionless Bash fixtures directly.
if [[ ${OS:-} == Windows_NT && -n ${HERDR_TEST_PYTHON:-} ]]; then
  for tool in flutter dart; do
    printf '@"C:\\Program Files\\Git\\bin\\bash.exe" "%%~dp0%s" %%*\r\n' "$tool" >"$tmp/bin/$tool.cmd"
  done
  printf '@"%s" %%*\r\n' "$HERDR_TEST_PYTHON" >"$tmp/bin/python3.cmd"
  # The real Unix entrypoint launches extensionless executables. Its Windows
  # fixture equivalents need an explicit extension for Python CreateProcess.
  "$HERDR_TEST_PYTHON" - "$tmp/project/scripts/check-evidence.py" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text()
source = source.replace('subprocess.run(arguments, check=False)',
    "subprocess.run([arguments[0] + '.cmd', *arguments[1:]], check=False)")
path.write_text(source)
PY
fi
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH"
export HERDR_CHECK_REPORT="$tmp/passing-checks.jsonl"
bash "$tmp/project/scripts/build-web.sh" >"$tmp/output"
[[ $(head -n 1 "$HERDR_BUILD_TEST_LOG") == 'flutter config --no-analytics' ]]
[[ -f $tmp/project/web/public/dashboard/index.html && -f $tmp/project/web/public/main.dart.js && -f $tmp/project/web/public/index.html ]]
[[ ! -e $tmp/project/web/public/dashboard/canvaskit && ! -e $tmp/project/web/public/dashboard/flutter_service_worker.js ]]
[[ ! -e $tmp/project/web/public/main.dart && ! -e $tmp/project/web/public/dashboard/main.dart ]]
python3 - "$HERDR_CHECK_REPORT" <<'PY'
import json, sys
checks = [json.loads(line) for line in open(sys.argv[1])]
assert len(checks) == 11, checks
assert len({check['id'] for check in checks}) == 11, checks
assert all(check['status'] == 'passed' and check['exit_code'] == 0 for check in checks), checks
PY
for entry in '' /dashboard; do
  [[ -f $tmp/project/web/public$entry/css/tokens.css ]]
  [[ -f $tmp/project/web/public$entry/vendor/xterm/xterm.js ]]
  [[ -f $tmp/project/web/public$entry/icons.svg ]]
done
if grep -q '^flutter build ' "$HERDR_BUILD_TEST_LOG"; then exit 1; fi
# The tool-ownership guard for this script lives in one place:
# tests/test_flutter_release.py::test_the_product_build_requires_only_its_own_pinned_toolchain
# The fix must still enforce the pinned version and stop before building.
: >"$HERDR_BUILD_TEST_LOG"
export HERDR_CHECK_REPORT="$tmp/version-mismatch-checks.jsonl"
if HERDR_BUILD_TEST_VERSION=3.47.8 bash "$tmp/project/scripts/build-web.sh" >"$tmp/output" 2>&1; then exit 1; fi
grep -q 'Flutter 3.47.9 required' "$tmp/output"
if grep -q '^flutter build ' "$HERDR_BUILD_TEST_LOG"; then exit 1; fi
python3 - "$HERDR_CHECK_REPORT" <<'PY'
import json, sys
checks = {check['id']: check for check in map(json.loads, open(sys.argv[1]))}
assert len(checks) == 11, checks
assert checks['flutter-pin']['status'] == 'failed', checks
assert checks['flutter-pin']['exit_code'] != 0, checks
assert checks['jaspr-tests']['status'] == 'not_run', checks
assert checks['jaspr-compile']['status'] == 'not_run', checks
PY
printf 'Web build cold-start and version guard tests passed.\n'
