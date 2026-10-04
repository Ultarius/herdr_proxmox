# Herdr on Proxmox VE

A standalone two-stage installer: `ct/herdr.sh` creates an unprivileged Debian 13
LXC, then `install/herdr-install.sh` installs official Herdr in that container.
Default resources are 2 cores, 4 GiB RAM, and a 16 GiB disk; agent workloads may
need more. The container remains unprivileged; nesting is enabled for Debian 13's
systemd compatibility. Automatic template selection matches the host architecture
(amd64 on x86-64, arm64 on ARM64), and mismatched explicit templates are rejected.

## Relationship to Paperclip

The [Paperclip Community Script](https://community-scripts.org/scripts/paperclip)
provides a useful host/container split, but its application is a Node.js web
service with PostgreSQL. Official [Herdr](https://herdr.dev/docs/install/) is a
terminal application distributed as a binary. Its installer verifies the release
checksum. This implementation is written independently and uses Proxmox `pct`
directly: Community Scripts' existing helper code fetches application installers
from its own upstream repository, where a renamed Herdr installer does not exist.

This targets **herdrdev/herdr**, not the separate **herdr-webui** project.
Access terminals through the Proxmox console or SSH. An optional custom dashboard
uses Flutter **3.44.8**, Jaspr **0.23.5**, Juice **1.10.0** and ZenRouter **3.0.0**.
Herdr starts its session server when you launch it and
keeps panes running after you detach. Dashboard installation adds an on-demand
`herdr-session` user service so the dashboard can start its server.

## Install

Run on the **Proxmox VE host as root**, not on Windows or inside an existing CT.
The installer supports both a local checkout and the following repository-hosted
commands **after these changes have been pushed to GitHub**. These edits do not
publish or deploy anything by themselves. Remote execution downloads the needed
installer files. Use this single command for both CLI-only and dashboard installs:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/herdr.sh)"
```

The script asks **Include the web dashboard? [Y/n]**. Press Enter for the complete
system, or answer `n` for Herdr and the four agent CLIs only. If you include the
dashboard, it downloads the **latest installable stable dashboard release**
automatically, falling back to the newest installable prerelease if no stable
build exists. Releases without the archive and checksum are skipped.
Open `http://<lxc-ip>:8787` from your LAN; no SSH key or tunnel is required.
The installer prints a command to read the dashboard login token from the
Proxmox console. You can add your public SSH key later under **CLI accounts →
SSH access (optional)**. You do not need to select a build number or tag.

By default it downloads the newest Debian 13 standard template to `local`, uses
`local-lvm` for the root disk, and allocates the next free CT ID with DHCP on
`vmbr0`. If those storages or that bridge do not exist on your host, specify them
explicitly as shown below. CLI-only installation provides console access unless
you supply a public SSH key for SSH access.

### Custom CPU, RAM and disk

The Community Scripts style environment properties are supported:

```bash
var_cpu="5" var_ram="10240" var_disk="24" \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/herdr.sh)"
```

This creates **5 CPU cores, 10 GiB RAM (10240 MiB), and a 24 GiB root disk**.
Defaults are `var_cpu=2`, `var_ram=4096`, and `var_disk=16`. Values must be positive
integers. Explicit `--cores`, `--memory` and `--disk` options take precedence over
environment properties.

### Dashboard downloads and unattended installation

Answering yes uses the latest published release's `herdr-proxmox.tar.gz`, verifies
its SHA-256 checksum, and installs the matching gateway and built UI. A source tag
alone does not contain the compiled dashboard. Publishing that build is handled
by the repository's release workflow; installers do not need to perform release
steps. If no installable release exists yet, the script stops before creating a
container and explains how to install CLI-only instead.

GitHub's `/releases/latest/download` URL excludes prereleases. The installer
queries release metadata to select a build containing both required assets, so a
prerelease such as `v0.0.1` can be installed automatically when no stable build
exists. To select it explicitly, prefix the install command with
`HERDR_RELEASE=v0.0.1`.

For automation without questions, supply these environment properties:

```bash
HERDR_WEB=1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/herdr.sh)"
```

Use `HERDR_WEB=0` or `--no-web` to explicitly skip the dashboard. When standard
input is not a terminal, an unspecified choice defaults to CLI-only. Explicit
`--web` and `--ssh-key` options are also supported. The dashboard does not install
Flutter on your Proxmox host. It listens on the LXC's IPv4 interfaces and requires
the dashboard token for controls. Allow TCP 8787 from your LAN if a Proxmox firewall
is enabled; the installer does not change host firewall rules.

For repeatable installations, set `HERDR_RELEASE=v0.1.0` (replace with an actual
published tag). CLI-only remote downloads use `HERDR_REF=main` by default; it can
be a published branch, tag or commit SHA without slashes. Other environment options
are `HERDR_TEMPLATE`, `HERDR_STORAGE`, `HERDR_SSH_KEY` and `HERDR_WEB=1`.

### Explicit template, storage and network

To use a predownloaded template rather than automatic template selection:

Find a Debian 13 standard template and download the exact filename listed:

```bash
pveam update
pveam available --section system | grep debian-13-standard
pveam download local <exact-debian-13-template-filename>
pvesm status
```

Choose storage that supports container root disks (commonly `local-lvm`), and
substitute the exact downloaded template filename below:

```bash
bash ct/herdr.sh \
  --template local:vztmpl/<exact-debian-13-template-filename> \
  --storage local-lvm \
  --ssh-key /root/herdr.pub
```

The same options work remotely:

```bash
var_cpu="5" var_ram="10240" var_disk="24" \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/herdr.sh)" \
  -- --template local:vztmpl/<exact-debian-13-template-filename> \
  --storage local-lvm --bridge vmbr0 --ssh-key /root/herdr.pub
```

The `--` after the downloaded script supplies Bash's command name, allowing the
following options to reach the installer.

The key file must contain your **public** SSH key; keep the corresponding private
key on your client. Omit `--ssh-key` for console-only setup. The installer creates
a normal `herdr` user and permits key-based SSH; password SSH and root SSH are
disabled. It installs Codex, Claude Code, OpenCode, and Google Antigravity CLI
for the `herdr` user. Authenticate accounts afterward through the dashboard's
**CLI configuration** page or over SSH.

The installer generates `en_US.UTF-8` and sets it as the container's default
locale before configuring the applications, preventing minimal-template locale
warnings. Locale setup is safe to rerun.

Codex uses npm with a user-owned `~/.local` prefix; Claude Code uses its native
stable installer. OpenCode keeps its native directory and has a launcher in
`~/.local/bin`, alongside Antigravity. Vendor installers are downloaded over
HTTPS during provisioning. To rerun this step in an existing container provisioned
by this checkout, run `bash /root/agents-install.sh` as root.

Use `bash ct/herdr.sh --help` for resource and network options. Default networking
uses DHCP on `vmbr0`. For static networking add, for example:

```bash
--ip 192.168.1.80/24 --gateway 192.168.1.1
```

Container startup at host boot is enabled. Existing CT/VM IDs are rejected. A
failed installation leaves the newly created CT intact for diagnosis; it never
automatically destroys a container. Inspect network/DNS and apt errors with
`pct enter <CTID>`, then rerun `bash /root/herdr-install.sh` inside it (include
`--ssh-key /root/herdr-authorized-key` if originally supplied).

## Use and update

```bash
# From the Proxmox host:
pct enter <CTID>
su - herdr
cd ~/projects
herdr

# Or from your SSH client:
ssh herdr@<container-ip>
cd ~/projects
herdr
```

Detach with `Ctrl+B`, then `Q`; run `herdr` again to reattach. See the official
[remote workflow](https://herdr.dev/docs/how-to-work/) for connecting clients.
The installed commands are `codex`, `claude`, `opencode`, and `agy` (Antigravity).
Launch your chosen CLI as `herdr` over SSH and complete its login flow before
using it in Herdr. These resource defaults are a
starting point, not a workload capacity guarantee.

Update as the same user:

```bash
herdr update
```

Back up the container before upgrades. Projects, credentials, configuration and
session state live under `/home/herdr`; include that home in your backups.
Container boot does not automatically launch Herdr. Use **Start Herdr** on the
dashboard after reboot, or launch it manually as the `herdr` user;
restoration depends on Herdr and the individual agent's resume support.

## Custom web dashboard

Jaspr renders the DOM shell, containing the Flutter dashboard in an iframe.
Juice owns the shared dashboard connection, refresh timer, loading/error state,
and workspace action use cases. A feature-owned Juice bloc manages organization
requests through that same connection. ZenRouter handles workspace, agent and organization views,
including browser back navigation in the embedded dashboard. Tokens stay in
memory and are cleared on disconnect; no credentials are saved in the browser.

Build on your development machine with Flutter 3.44.8 on PATH:

```powershell
# Windows
./scripts/build-web.ps1
```

```bash
# Linux/macOS
bash scripts/build-web.sh
```

Copy the repository **including generated `web/public`** to the Proxmox host,
then add `--web` to the container creation command. The host script transfers
only the built web assets and gateway into `/opt/herdr-web`. Flutter/Dart are
build-time tools and do not need to run inside the CT.

### Publish an installable web release

The GitHub CI workflow builds and tests both applications. A pushed `v*` tag also
publishes `herdr-proxmox.tar.gz` and `herdr-proxmox.tar.gz.sha256` as GitHub release
assets, containing the installers, complete gateway and compiled dashboard from
that same tag. Commit and push the finished changes first, then create a version
tag from that commit, for example:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Choose a new unused version tag. Wait for **CI / Publish installable dashboard
release** to succeed before using remote `--web`. No release has been published
by the local edits. An actual Proxmox deployment still needs verification on your
host; the hosted Debian smoke job tests provisioning, not Proxmox storage/network.

### VS Code preview

Open this repository in VS Code, select **Web build: Edge** (or **Chrome**) in
Run and Debug, and press **F5**. The prelaunch tasks build both apps, start a
static Dart server on `127.0.0.1:8788`, and launch the browser. Flutter 3.44.8
and its Dart executable must be on PATH. The preview server remains in the task
terminal; terminate **web: serve build** when finished.

This checks the compiled web UI. Live agent data and workspace controls require
the Proxmox gateway. Open its LAN URL directly, or establish the optional SSH
tunnel described below and choose **Proxmox dashboard: SSH tunnel**. Release builds are
optimized; use these browser launches for inspection, rather than Dart hot reload.

For an interactive local chat/group preview without Proxmox, build
`web/dashboard` and run `python3 scripts/preview-mock.py`. Open
`http://127.0.0.1:8789/dashboard/` and connect with `local-preview-token`.
The preview seeds Max and Iris, uses the real authenticated gateway, SQLite
storage and background jobs, and simulates agent responses and artifact files.
Enter `mock:block` to exercise a permission prompt and terminal controls.
Container configuration and updates are disabled. All preview data is temporary
and removed when the process exits normally; no real agent is launched.

For an existing CT, copy `web/public`, the complete `web/gateway` directory,
`install/web-install.sh`, `install/dashboard-update.py`, and the release's
`VERSION` file into the same layout under `/opt/herdr-web`, then run:

```bash
bash /opt/herdr-web/install/web-install.sh
```

After installing this version once, **CLI configuration → Dashboard updates**
checks GitHub for stable `vMAJOR.MINOR.PATCH` releases with both packaged assets.
Checks are cached for one hour. Review the release notes and choose **Install
update**. The root-owned `herdr-update.path` service watches a fixed request
location; the gateway itself remains unprivileged. The updater downloads only
from this repository, verifies the published SHA-256 checksum, validates archive
paths and sizes, and replaces the gateway and compiled dashboard together.
It does not execute release installer scripts or update the Herdr CLI.

The gateway is stopped while its installed files and configuration/database are
backed up under `/var/lib/herdr-updater/backup-*`. Projects and agent terminals
remain running. Existing database initialization applies compatible schema
changes on gateway startup; failed startup restores both files and configuration.
Destructive or incompatible schema migrations need a separate release-specific
migration implementation before publication. Reload the browser after completion.
Status is retained in `/var/lib/herdr-updater/status.json`; diagnose the worker
with `journalctl -u herdr-update`. Backups are retained for manual recovery and
should be managed according to available disk space. Checksums detect corruption;
release integrity relies on the repository and GitHub HTTPS.

The gateway runs as `herdr`, listening on **0.0.0.0:8787 inside the CT** by default.
Open **http://&lt;lxc-ip&gt;:8787** from your LAN. Read the login token on the
Proxmox host (replace `<CTID>` with your container ID):

```bash
pct exec <CTID> -- cat /home/herdr/.config/herdr-web/token
```

Enter that token on the dashboard. SSH setup is optional: on **CLI accounts**,
paste your computer's `.pub` key under **SSH access (optional)**. It adds a key
for the `herdr` user without replacing existing keys. Private keys stay on your
computer. Agent work terminals can then be accessed with `ssh herdr@<lxc-ip>`.
On that same page, **Dashboard access** lets you switch between **Allow LAN access**
and **Require SSH tunnel**. SSH-only mode requires at least one public key and
confirmation that you have tested your tunnel. The dialog provides the tunnel
command. The listener changes after five seconds; direct LAN connections then
close, and you continue at `http://127.0.0.1:8787` through the tunnel. Access-token
login remains required in both modes. You can enable LAN access again from the
tunneled dashboard. The setting survives gateway restart and normal reinstall.

If you lose access, use the Proxmox CT console as root to restore LAN mode:

```bash
HERDR_WEB_BIND=0.0.0.0 bash /opt/herdr-web/install/web-install.sh
```

You can also start Herdr through `pct enter <CTID>` and `su - herdr` before SSH
has been configured.

The connected dashboard shows **Herdr: stopped** and a **Start Herdr** button
when no session server is running. The button launches `herdr server` through
the `herdr` user's systemd service and refreshes the workspace list. It requires
the dashboard token and reuses an existing server rather than launching a
duplicate. It starts a background server; run `herdr` as that user to attach its
terminal UI. The server continues running if you close the browser or restart
the dashboard gateway. The user service is started on demand, not automatically
enabled at boot. Diagnose startup with `journalctl --user -u herdr-session`
as `herdr`.

To keep the gateway loopback-only instead, reinstall the web service inside the CT
with `HERDR_WEB_BIND=127.0.0.1 bash /opt/herdr-web/install/web-install.sh`.
After adding an SSH key, you can forward it from your client:

```bash
ssh -L 8787:127.0.0.1:8787 herdr@<container-ip>
# In that remote shell:
cat ~/.config/herdr-web/token
herdr
```

For the optional tunnel, open `http://127.0.0.1:8787` on your client. Start Herdr
using **Start Herdr**, the Proxmox console or SSH before using workspace controls. The dashboard lists workspaces and agents,
creates workspaces within existing directories under `/home/herdr/projects`,
and focuses or renames workspaces. It polls every five seconds and reports
backend errors. The **CLI configuration** page detects installed runtimes and
local authentication state, and opens an interactive browser terminal for login.
See [browser CLI configuration](docs/cli-configuration.md) for provider flows and
status limitations. Agent work terminals remain accessible over SSH.
The **Saved logs** page archives retained terminal text on demand, with an 8 KB
preview, a separate paged text viewer, downloads up to 10 MB and actual file
sizes. These are snapshots rather than complete run transcripts. See
[saved terminal logs](docs/run-logs.md).
The **Organization** page adds durable organizations, persona-based hires,
reporting relationships, explicit launches, delegation and operator completion
reports. Organizations can be edited while Herdr is stopped; launches and
delegation require a running session and authenticated agent CLIs. Personas are
delivered as initial conversation prompts, using each CLI's configured default
model. Run and task delivery states do not prove successful work; inspect replies
over SSH before recording a report.

Organization data lives in `~/.config/herdr-web/organizations.sqlite3`; include it
in backups. Launch/delegation run as background jobs with request deduplication
and no automatic terminal retries. After a failure or gateway restart, inspect
the terminal before releasing a run binding or resubmitting a task. The same
dashboard token controls all organizations under one OS user; organization names
do not create independent access or process isolation.

In **Organization → Chat**, select a launched hire to send a prompt directly
from the dashboard. Messages are durable jobs with deduplicated submission;
responses are labeled terminal snapshots, not extracted conversation transcripts.
Live terminal output refreshes every five seconds. Fixed terminal controls
(Up, Down, Tab, Enter, Escape and Interrupt) let the operator respond to menus
and confirmation prompts. Inspect the terminal before approving its selected
option. Use SSH for richer terminal interaction. Timeouts and interrupted jobs
are not replayed automatically.

The same page supports named discussion groups with a description and 2–6
members from the organization. Creating a group creates and launches a dedicated
Herdr agent, using the first member's runtime and project. Messages on the group
page go to that persistent agent conversation. It uses Herdr agent-to-agent
commands to coordinate the selected members, choose rounds and follow-ups, and
produce a Markdown action artifact plus a structured discussion transcript.
Launch member agents before sending a group message. Existing groups acquire
an agent on their next edit or message. Later messages reuse the same conversation;
release and relaunch its run explicitly if that session needs recovery.
Groups appear under **GROUPS** in the sidebar for the selected organization.
Each opens its own `/groups/<id>` page with **Posts**, **Artifacts**, **Members**
and **About** tabs. Each round becomes a feed post showing agent contributions;
**View resulting artifact** opens the action brief from that specific discussion.
Artifacts remain available across later discussions and group edits.
Contributions and the artifact remain in the job history and can be copied.
Files also live under `~/.config/herdr-web/discussion-artifacts/<job-id>`;
include that directory in backups. Groups are saved separately and can be edited;
each discussion retains its original group and run snapshots.

Discussion uses the coding agents' file-writing tools. An agent without those
tools, a blocked agent, a missing output file, or a replaced binding produces
`needs_attention`, never a successful artifact. Group messages have a fifteen-minute wait limit; member prompts request a three-minute limit.
Agents are reserved against overlapping dashboard messages/delegations while a
discussion runs. External SSH activity can still interfere. Instructions ask
agents to discuss and propose actions without changing projects; this is an
instruction, not an operating-system sandbox. Review artifacts before acting.

The token grants workspace and organization controls; rotate it by replacing the token file
and restarting `herdr-web`. Diagnose the gateway with
`journalctl -u herdr-web -n 100`.

## Validation

[Installation CI](docs/ci.md) documents the GitHub-hosted Debian provisioning
smoke suite, APT and image-layer caches, and optional manual Proxmox LXC test.
Default CI does not require a Proxmox host.

The design options, implemented organization workflow and remaining live
compatibility checks are documented in
[Organization and persona agents](docs/organization-agents.md). No marketplace
plugins are required for this first implementation.

Local checks: Bash syntax, mocked host command tests (`tests/host.sh`), gateway
tests (`python3 -m unittest discover -s tests`), Flutter analyzer/use-case tests,
and release compilation of the Flutter/Jaspr UI.
An actual Proxmox creation, Debian provisioning, SSH login and interactive Herdr
session still require testing on a real Proxmox host. No deployment was performed
from the Windows development workspace.
