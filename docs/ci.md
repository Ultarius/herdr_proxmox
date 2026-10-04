# Installation CI

The [workflow](../.github/workflows/ci.yml) uses GitHub-hosted runners by default.
It does not require your Proxmox infrastructure or publish a GHCR image.

Every push and pull request runs Bash syntax checks, mocked installer and cleanup
tests, gateway unit tests, workflow linting, and the pinned Flutter/Jaspr release
build with analysis and tests. Built web assets are passed between jobs.

Pushing a `v*` tag additionally publishes a GitHub release after the installer
checks and web build pass. It contains `herdr-proxmox.tar.gz` (matching installer,
complete gateway and compiled UI) and its SHA-256 checksum. The remote `--web`
installer consumes these assets. The release job has repository-content write
permission; ordinary checks retain read-only permissions. This packaging job
does not certify a real Proxmox installation, and tag runs do not run the
default-branch Debian provisioning job.

The runtime smoke test starts Herdr through the authenticated dashboard endpoint
using the installed `herdr-session` user service. It verifies that a repeat start
reuses the server and that workspace/organization controls still work after the
dashboard gateway restarts.

Pushes to the default branch and manual runs on that branch additionally provision
a fresh Debian 13 systemd Docker container with the actual installers. The host
runner is Ubuntu 24.04; the installation target is Debian 13, matching the LXC.
Vendor installers run only in this trusted branch job.

The hosted Debian fixture provisions without an initial SSH key, adds its test
public key through the authenticated dashboard API, checks duplicate prevention,
and performs a real SSH login. This covers configuring SSH after reaching the
dashboard. The installed gateway defaults to LAN binding; dashboard API controls
still require the access token and same-origin checks.

## Caches and clean installation

- Dart package downloads are cached using the SDK version and lockfiles.
- BuildKit stores Debian baseline image layers in GitHub Actions' cache. The
  baseline contains systemd, Python and an SSH client, but no Herdr, agent CLIs,
  dashboard, application user or organization database.
- APT `.deb` archives are cached by Debian version, architecture, installer hash
  and day, with fallback keys. The wrapper disables Debian's archive deletion
  and mounts only the archive directory into each new container.

APT still refreshes package indexes. Herdr, Codex, Claude, OpenCode and Antigravity
are installed fresh; their vendor downloads are not covered by the APT cache.
Cache restoration, image loading and package extraction still take time. The
wrapper prints elapsed phase times so the first successful run provides a real
baseline. Cold caches must be populated before later runs benefit.

## Smoke coverage

The real provisioning test checks Debian 13, active SSH and gateway services,
SSH authentication settings, user ownership and version execution of all five
CLIs, and the user-local npm prefix. The authenticated CLI configuration endpoint
must detect all four installed agent runtimes; unauthorized and foreign-origin
setup requests are rejected. It starts a real headless Herdr session, checks its
API schema, creates a workspace and verifies the gateway snapshot.
It also loads compiled web assets, checks rejected unauthenticated and foreign
origin requests, creates an organization and two persona hires with a reporting
relationship, verifies request deduplication and project restrictions, and checks
that those records survive a gateway restart.

This does not authenticate vendor accounts, run paid model prompts, or verify
agent responses or terminal delegation. Those need separate authenticated tests.
The hosted Docker fixture exercises Linux provisioning; it cannot prove Proxmox
template, networking or LXC compatibility. Logs are uploaded for seven days;
tokens and generated SSH private keys are not uploaded.

On an isolated Linux Docker host, after building the web assets, the same test is:

```bash
bash scripts/build-web.sh
bash scripts/debian-ci.sh
```

The systemd Docker fixture needs privileged mode and a writable host cgroup
mount. CI uses a disposable GitHub-hosted runner for this job.

## Optional real Proxmox test

Use a dedicated x64 Proxmox VE lab host. The repository includes a host adaptation
of the [Community Scripts GitHub Runner installer](https://community-scripts.org/scripts/github-runner).
It installs the runner directly on the Proxmox host so the test can use local
`pct`, `pvesh` and `pvesm` commands.

Once these files are pushed to `main`, run this as root in the Proxmox host shell:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/github-runner.sh)"
```

Before installation, open repository **Settings → Actions → Runners → New
self-hosted runner**, select Linux/x64, and copy the registration token. Paste
that short-lived token at the installer's hidden prompt. It downloads the latest
official runner, verifies its release SHA-256 digest, installs dependencies,
creates the `github-runner` account, registers this repository with the
`proxmox-ci` label, and starts a systemd service. GitHub adds `self-hosted`,
`linux` and `x64` automatically. Existing installations are refused to avoid
overwriting runner credentials. Check the service with:

```bash
cd /opt/actions-runner
./svc.sh status
```

The installer also detects the newest downloaded Debian 13 standard template
across active template storage, prefers `local-lvm` for container disks when
available, and prompts for template, disk storage, bridge and IPv4 settings.
Press Enter to accept each detected default. Defaults are saved root-owned in
`/etc/herdr-proxmox-ci.conf`; CI uses them when GitHub variables are empty.
Download a Debian 13 template first if none is available.

For an already installed runner, save host defaults and update the CI helper
without reinstalling or providing another registration token:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/github-runner.sh)" -- --configure
```

The installer also installs a root-owned `/usr/local/sbin/herdr-proxmox-ci`
helper and a sudo rule for that command. The helper validates operations and
workspace, state and results paths before executing the checkout's LXC smoke
script. Repository installers execute as root, so restrict this runner and
default-branch changes to trusted maintainers. Reinstall the root helper from
`scripts/proxmox-ci-root.sh` if its interface changes. The runner automatically
updates its own software.

Create the GitHub environment `proxmox-ci`, restrict it to the default branch,
and configure required reviewers where available. These environment variables
are optional overrides of the host defaults:

| Variable | Value |
| --- | --- |
| `PVE_TEMPLATE` | Existing Debian 13 template volume, e.g. `local:vztmpl/<template>.tar.zst` |
| `PVE_STORAGE` | Storage for the disposable container root filesystem |
| `PVE_BRIDGE` | Optional bridge; defaults to `vmbr0` |
| `PVE_IP` | Optional network address; defaults to `dhcp` |
| `PVE_GATEWAY` | Gateway if needed for a static address |

Run the CI workflow manually on the default branch and select `run_lxc`. The
runner creates an unprivileged LXC using the production creator, tests a real
key-authenticated SSH login and runs the same smoke suite inside it.

Cleanup requires both the saved CT ID and the exact unique description marker.
It refuses to delete a container with a different marker and retains state if
stop or destruction fails. An EXIT trap and an always-run workflow step attempt
cleanup. A host outage or forced process termination can still leave a test CT;
inspect retained state and the marker before manually cleaning it up.

### Removing the host runner

Finish any active CI run first. In repository **Settings → Actions → Runners**,
select the runner, click **Remove**, and copy the removal token. After pushing
the uninstall option to `main`, run as root on the host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/github-runner.sh)" -- --uninstall
```

Paste the removal token at the hidden prompt. This stops and uninstalls the
service, unregisters the runner, removes `/opt/actions-runner`, the dedicated
`github-runner` account and home, and the CI root helper and sudo rule. If token
validation fails, local files are preserved so removal can be retried. The saved
host defaults are also removed. It refuses
removal while a worker is active or retained CI state needs attention. Installed
system packages and Proxmox containers are preserved.

To temporarily take the runner offline, run `./svc.sh stop` in
`/opt/actions-runner`; use `./svc.sh start` to bring it back online.

## Verification status

The Windows development environment can run syntax, mock, unit and workflow
lint checks. Full Debian provisioning requires a running Linux Docker daemon;
the optional LXC job requires the configured lab. A successful hosted workflow
run is needed before claiming the clean installation smoke suite passes.
