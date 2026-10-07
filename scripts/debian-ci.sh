#!/usr/bin/env bash
# Automatic provisioning smoke test on an ephemeral GitHub-hosted Docker runner.
# Uses Debian/systemd to test installer execution; Proxmox is tested separately.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
results="$PWD/ci-results"
mkdir -p "$results"
apt_cache="$PWD/.ci-cache/apt"
mkdir -p "$apt_cache"
owner=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
name="herdr-ci-$owner"
finish() {
  local rc=$?
  trap - EXIT
  if ((rc != 0)); then
    local diagnostic
    for diagnostic in install web-install smoke image-build; do
      if [[ -f $results/$diagnostic.log ]]; then
        printf '\n--- %s failure diagnostics (last 80 lines) ---\n' "$diagnostic" >&2
        tail -n 80 "$results/$diagnostic.log" >&2 || true
      fi
    done
  fi
  if [[ $(docker inspect --format '{{ index .Config.Labels "herdr.ci.owner" }}' "$name" 2>/dev/null || true) == "$owner" ]]; then
    docker logs "$name" >"$results/container.log" 2>&1 || true
    docker exec "$name" journalctl --no-pager -u herdr-web -u herdr-ci-session -n 100 >"$results/journal.log" 2>&1 || true
    docker rm -f "$name" >"$results/cleanup.log" 2>&1 || rc=1
  fi
  exit "$rc"
}
trap finish EXIT
# CI preloads this image with BuildKit's GitHub cache; local runs can build it.
if ! docker image inspect herdr-ci-systemd >/dev/null 2>&1; then
  docker build --pull -t herdr-ci-systemd -f tests/ci/Dockerfile tests/ci >"$results/image-build.log" 2>&1
fi
# The privileged systemd fixture is confined to a disposable hosted runner.
docker run --detach --name "$name" --label "herdr.ci.owner=$owner" \
  --privileged --cgroupns=host --volume /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock --volume "$PWD:/workspace:ro" \
  --volume "$apt_cache:/var/cache/apt/archives" \
  herdr-ci-systemd >/dev/null
ready=0
for attempt in {1..60}; do
  if docker exec "$name" systemctl is-active --quiet basic.target; then ready=1; break; fi
  sleep 2
done
[[ $ready == 1 ]] || { echo 'Debian systemd fixture did not become ready.' >&2; exit 1; }
printf 'Baseline ready after %ss; installing Herdr and agent CLIs fresh.\n' "$SECONDS"
# Official Debian images normally delete downloaded .debs. Cache archives only,
# never the package database or an already-provisioned application filesystem.
docker exec "$name" bash -c 'rm -f /etc/apt/apt.conf.d/docker-clean; printf "%s\n" "APT::Keep-Downloaded-Packages \"true\";" "Binary::apt::APT::Keep-Downloaded-Packages \"true\";" > /etc/apt/apt.conf.d/99ci-keep-debs; ssh-keygen -q -t ed25519 -N "" -f /root/ci-key'
docker exec "$name" bash /workspace/install/herdr-install.sh >"$results/install.log" 2>&1
printf 'Application installation finished after %ss; installing dashboard.\n' "$SECONDS"
docker exec "$name" bash -c 'mkdir -p /opt/herdr-web/web /opt/herdr-web/install; cp -a /workspace/web/public /opt/herdr-web/web/; cp -a /workspace/web/gateway /opt/herdr-web/web/; cp /workspace/install/web-install.sh /workspace/install/dashboard-update.py /workspace/install/sdk-install.py /workspace/install/operator-admin.py /workspace/install/dev-tools-install.sh /workspace/install/flutter-release.py /workspace/install/build-install.sh /opt/herdr-web/install/'
docker exec "$name" bash /opt/herdr-web/install/web-install.sh >"$results/web-install.log" 2>&1
docker exec "$name" install -o herdr -g herdr -m 0644 /root/ci-key.pub /home/herdr/.config/herdr-web/ci-key.pub
docker exec "$name" bash /workspace/tests/ci/smoke.sh >"$results/smoke.log" 2>&1
printf 'Debian provisioning smoke checks passed in %ss (Docker fixture, not LXC).\n' "$SECONDS"
