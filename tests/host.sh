#!/usr/bin/env bash
# Mock Proxmox commands: no containers or host settings are touched.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
unset var_cpu var_ram var_disk var_swap HERDR_TEMPLATE HERDR_STORAGE HERDR_SSH_KEY HERDR_WEB HERDR_REF HERDR_RELEASE
export HERDR_WEB=0
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/ct" "$tmp/install"
cp "$repo/install/herdr-install.sh" "$tmp/install/"
cp "$repo/install/agents-install.sh" "$tmp/install/"
# Only bypass the root guard in this isolated copy, so tests work on Windows/Git Bash.
sed '/\[\[ \$EUID == 0 \]\]/d' "$repo/ct/herdr.sh" >"$tmp/ct/herdr.sh"
export HERDR_TEST_LOG="$tmp/commands"
export HERDR_TEST_DOWNLOADS="$tmp/downloads"
export HERDR_TEST_RELEASE_METADATA="$tmp/releases.json"
printf '%s\n' '[{"tag_name":"v0.0.1","draft":false,"prerelease":true,"published_at":"2026-10-04T00:00:00Z","assets":[{"name":"herdr-proxmox.tar.gz"},{"name":"herdr-proxmox.tar.gz.sha256"}]}]' >"$HERDR_TEST_RELEASE_METADATA"
cat >"$tmp/bin/pct" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$HERDR_TEST_LOG"
if [[ $1 == create && $(umask) != 0022 ]]; then
  printf 'Container creation inherited a restrictive umask.\n' >&2
  exit 1
fi
if [[ $1 == push && $4 == /root/herdr-web.tar.gz ]]; then tar -tzf "$3" >"$HERDR_TEST_WEB_CONTENTS"; fi
if [[ ${HERDR_TEST_FAIL:-0} == 1 && $1 == exec ]]; then exit 1; fi
EOF
cat >"$tmp/bin/pvesh" <<'EOF'
#!/usr/bin/env bash
echo 987654321
EOF
cat >"$tmp/bin/pvesm" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$tmp/bin/pveam" <<'EOF'
#!/usr/bin/env bash
printf 'pveam %s\n' "$*" >>"$HERDR_TEST_LOG"
if [[ $1 == available ]]; then printf 'system debian-13-standard_13.0-1_amd64.tar.zst\nsystem debian-13-standard_13.1-1_amd64.tar.zst\nsystem debian-13-standard_13.6-1_arm64.tar.zst\n'; fi
EOF
cat >"$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
[[ ${HERDR_TEST_DOWNLOAD_FAIL:-0} == 0 ]] || exit 22
printf '%s\n' "$*" >>"$HERDR_TEST_DOWNLOADS"
checksum=0
metadata=0
while (($#)); do
  [[ $1 != *api.github.com* ]] || metadata=1
  [[ $1 != *.sha256 ]] || checksum=1
  if [[ $1 == -o ]]; then
    if ((metadata)); then cp "$HERDR_TEST_RELEASE_METADATA" "$2"; elif ((checksum)); then cp "$HERDR_TEST_SOURCE_ARCHIVE.sha256" "$2"; else cp "$HERDR_TEST_SOURCE_ARCHIVE" "$2"; fi
    exit
  fi
  shift
done
exit 1
EOF
cat >"$tmp/bin/ssh-keygen" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$tmp/bin/"*
if [[ -n ${HERDR_TEST_PYTHON:-} ]]; then
  printf '#!/usr/bin/env bash\nexec "$HERDR_TEST_PYTHON" "$@"\n' >"$tmp/bin/python3"
  chmod +x "$tmp/bin/python3"
fi
export PATH="$tmp/bin:$PATH"
# Reproduce the private-key umask inherited from the real CI wrapper.
(umask 077; bash "$tmp/ct/herdr.sh" --template local:vztmpl/debian-13-standard_test_amd64.tar.zst --storage local-lvm --description herdr-ci-test >"$tmp/output")
grep -q '^create 987654321 .*--unprivileged 1' "$HERDR_TEST_LOG"
grep -q '^create .*--arch amd64 --features nesting=1' "$HERDR_TEST_LOG"
grep -q '^create .*--cores 4 --memory 8192 --swap 2048 .*--rootfs local-lvm:32' "$HERDR_TEST_LOG"
grep -q '^create .*--description herdr-ci-test$' "$HERDR_TEST_LOG"
grep -q '^push .*herdr-install.sh' "$HERDR_TEST_LOG"
grep -q '^push .*agents-install.sh' "$HERDR_TEST_LOG"
grep -q '^exec .*bash /root/herdr-install.sh' "$HERDR_TEST_LOG"
var_cpu=5 var_ram=10240 var_disk=24 var_swap=4096 bash "$tmp/ct/herdr.sh" --template local:vztmpl/debian-13-standard_test_amd64.tar.zst --storage local-lvm >"$tmp/output"
grep -q '^create .*--cores 5 --memory 10240 --swap 4096 .*--rootfs local-lvm:24' "$HERDR_TEST_LOG"
var_cpu=5 bash "$tmp/ct/herdr.sh" --cores 3 >"$tmp/output"
grep -q '^create .*--cores 3 --memory 8192 --swap 2048' "$HERDR_TEST_LOG"
var_swap=1024 bash "$tmp/ct/herdr.sh" --swap 3072 >"$tmp/output"
grep -q '^create .*--swap 3072' "$HERDR_TEST_LOG"
var_swap=0 bash "$tmp/ct/herdr.sh" >"$tmp/output"
grep -q '^create .*--cores 4 --memory 8192 --swap 0' "$HERDR_TEST_LOG"
grep -q '^pveam download local debian-13-standard_13.1-1_amd64.tar.zst$' "$HERDR_TEST_LOG"
before=$(wc -l <"$HERDR_TEST_LOG")
if bash "$tmp/ct/herdr.sh" --template local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst >"$tmp/output" 2>&1; then exit 1; fi
grep -q 'Template must match the host architecture (amd64)' "$tmp/output"
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
if var_ram=oops bash "$tmp/ct/herdr.sh" >"$tmp/output" 2>&1; then exit 1; fi
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
if var_swap=oops bash "$tmp/ct/herdr.sh" >"$tmp/output" 2>&1; then exit 1; fi
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
if bash "$tmp/ct/herdr.sh" --template local:vztmpl/debian-13-standard_test_amd64.tar.zst --storage local-lvm --ip 'dhcp,bridge=bad' >"$tmp/output" 2>&1; then exit 1; fi
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
# Exercise exactly the raw bash -c invocation without network or a Proxmox host.
mkdir -p "$tmp/package/herdr-source"
cp -R "$tmp/ct" "$tmp/install" "$tmp/package/herdr-source/"
export HERDR_TEST_SOURCE_ARCHIVE="$tmp/source.tar.gz"
tar -czf "$HERDR_TEST_SOURCE_ARCHIVE" -C "$tmp/package" herdr-source
var_cpu=5 var_ram=10240 var_disk=24 bash -c "$(cat "$tmp/ct/herdr.sh")" -- --template local:vztmpl/debian-13-standard_test_amd64.tar.zst --storage fast-storage >"$tmp/output"
grep -q '^create .*--cores 5 --memory 10240 .*--rootfs fast-storage:24' "$HERDR_TEST_LOG"
before=$(wc -l <"$HERDR_TEST_LOG")
if HERDR_TEST_DOWNLOAD_FAIL=1 bash -c "$(cat "$tmp/ct/herdr.sh")" -- --web --ssh-key "$tmp/public.key" >"$tmp/output" 2>&1; then exit 1; fi
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
# Confirm the web bundle includes every gateway module and the standalone text viewer.
mkdir -p "$tmp/web/public/dashboard"
cp -R "$repo/web/gateway" "$tmp/web/gateway"
cp "$repo/install/web-install.sh" "$tmp/install/"
cp "$repo/install/dashboard-update.py" "$tmp/install/"
cp "$repo/install/sdk-install.py" "$tmp/install/"
cp "$repo/install/operator-admin.py" "$tmp/install/"
cp "$repo/install/dev-tools-install.sh" "$tmp/install/"
cp "$repo/install/flutter-release.py" "$tmp/install/"
touch "$tmp/web/public/index.html" "$tmp/web/public/main.dart.js" "$tmp/web/public/dashboard/index.html"
printf 'ssh-ed25519 AAAA test\n' >"$tmp/public.key"
export HERDR_TEST_WEB_CONTENTS="$tmp/web-contents"
bash "$tmp/ct/herdr.sh" --web --ssh-key "$tmp/public.key" --template local:vztmpl/debian-13-standard_test_amd64.tar.zst >"$tmp/output"
for file in cli_setup.py pty_exec.py run_logs.py log_view.html skills/herdr-worktree-integration/SKILL.md; do grep -q "web/gateway/$file" "$HERDR_TEST_WEB_CONTENTS"; done
grep -q 'install/sdk-install.py' "$HERDR_TEST_WEB_CONTENTS"
grep -q 'install/operator-admin.py' "$HERDR_TEST_WEB_CONTENTS"
mkdir "$tmp/package/herdr-proxmox"
cp -R "$tmp/ct" "$tmp/install" "$tmp/web" "$tmp/package/herdr-proxmox/"
export HERDR_TEST_SOURCE_ARCHIVE="$tmp/herdr-proxmox.tar.gz"
tar -czf "$HERDR_TEST_SOURCE_ARCHIVE" -C "$tmp/package" herdr-proxmox
(cd "$tmp" && sha256sum herdr-proxmox.tar.gz >herdr-proxmox.tar.gz.sha256)
bash -c "$(cat "$tmp/ct/herdr.sh")" -- --web --template local:vztmpl/debian-13-standard_test_amd64.tar.zst >"$tmp/output"
grep -q 'Browser access:' "$tmp/output"
# Force only the terminal-detection guard in an isolated copy to exercise prompts
# deterministically, without requiring a real terminal in hosted CI.
sed 's/\[\[ -t 0 \]\]/[[ 1 == 1 ]]/g; s/&& -t 0/\&\& 1 == 1/g' "$tmp/ct/herdr.sh" >"$tmp/ct/interactive.sh"
printf 'maybe\n\n' | env -u HERDR_WEB bash -c "$(cat "$tmp/ct/interactive.sh")" -- --template local:vztmpl/debian-13-standard_test_amd64.tar.zst >"$tmp/output"
grep -q 'Please answer yes or no' "$tmp/output"
grep -q 'Downloading dashboard release: v0.0.1' "$tmp/output"
grep -q 'Browser access:' "$tmp/output"
grep -q 'releases/download/v0.0.1/herdr-proxmox.tar.gz' "$HERDR_TEST_DOWNLOADS"
# Stable installable builds take priority over prereleases; empty releases do not.
python3 - "$HERDR_TEST_RELEASE_METADATA" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data += [dict(data[0], tag_name='v0.0.0', prerelease=False, published_at='2026-10-03T00:00:00Z'), dict(data[0], tag_name='v0.0.2', prerelease=False, assets=[])]
with open(path, 'w') as output: json.dump(data, output)
PY
bash -c "$(cat "$tmp/ct/herdr.sh")" -- --web --template local:vztmpl/debian-13-standard_test_amd64.tar.zst >"$tmp/output"
grep -q 'Downloading dashboard release: v0.0.0' "$tmp/output"
HERDR_RELEASE=v0.0.1 bash -c "$(cat "$tmp/ct/herdr.sh")" -- --web --template local:vztmpl/debian-13-standard_test_amd64.tar.zst >"$tmp/output"
grep -q 'Downloading dashboard release: v0.0.1' "$tmp/output"
printf 'n\n' | env -u HERDR_WEB bash "$tmp/ct/interactive.sh" --template local:vztmpl/debian-13-standard_test_amd64.tar.zst >"$tmp/output"
if grep -q 'Browser access:' "$tmp/output"; then exit 1; fi
env -u HERDR_WEB bash "$tmp/ct/interactive.sh" --no-web --template local:vztmpl/debian-13-standard_test_amd64.tar.zst </dev/null >"$tmp/output"
before=$(wc -l <"$HERDR_TEST_LOG")
if env -u HERDR_WEB bash "$tmp/ct/interactive.sh" </dev/null >"$tmp/output" 2>&1; then exit 1; fi
grep -q 'Installation cancelled' "$tmp/output"
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
before=$(wc -l <"$HERDR_TEST_LOG")
printf '%064d  herdr-proxmox.tar.gz\n' 0 >"$HERDR_TEST_SOURCE_ARCHIVE.sha256"
if bash -c "$(cat "$tmp/ct/herdr.sh")" -- --web --ssh-key "$tmp/public.key" >"$tmp/output" 2>&1; then exit 1; fi
grep -q 'Release checksum failed' "$tmp/output"
[[ $(wc -l <"$HERDR_TEST_LOG") == "$before" ]]
if HERDR_TEST_FAIL=1 bash "$tmp/ct/herdr.sh" --template local:vztmpl/debian-13-standard_test_amd64.tar.zst --storage local-lvm >"$tmp/output" 2>&1; then exit 1; fi
grep -q 'preserved for diagnosis' "$tmp/output"
if grep -q '^destroy ' "$HERDR_TEST_LOG"; then exit 1; fi
printf 'Host installer mock tests passed.\n'
