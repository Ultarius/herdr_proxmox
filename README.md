# Herdr on Proxmox VE

A standalone two-stage installer: `ct/herdr.sh` creates an unprivileged Debian 13
LXC, then `install/herdr-install.sh` installs official Herdr in that container.
Default resources are 4 cores, 8 GiB RAM, a 32 GiB disk and 2 GiB swap; agent
workloads may need more. The container remains unprivileged; nesting is enabled
for Debian 13's systemd compatibility. Automatic template selection matches the
host architecture (amd64 on x86-64, arm64 on ARM64), and mismatched explicit
templates are rejected.

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

### Custom CPU, RAM, disk and swap

The Community Scripts style environment properties are supported:

```bash
var_cpu="6" var_ram="12288" var_disk="48" var_swap="4096" \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Ultarius/herdr_proxmox/main/ct/herdr.sh)"
```

This creates **6 CPU cores, 12 GiB RAM (12288 MiB), a 48 GiB root disk and 4 GiB swap**.
Defaults are `var_cpu=4`, `var_ram=8192`, `var_disk=32` and `var_swap=2048`. CPU,
memory and disk must be positive integers; swap may be `0` to disable it. Explicit
`--cores`, `--memory`, `--disk` and `--swap` options take precedence over environment
properties.

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
var_cpu="6" var_ram="12288" var_disk="48" var_swap="4096" \
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
`install/web-install.sh`, `install/dashboard-update.py`, `install/sdk-install.py`,
`install/operator-admin.py`, `install/dev-tools-install.sh`,
`install/flutter-release.py`, and the release's `VERSION` file into the same
layout under `/opt/herdr-web`, then run:

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

Named operator tokens are optional. As root, `herdr-operator add <name> --role
admin|operator` prints a token once and stores only its SHA-256; `list`, `rotate`
and `remove` manage the file at `/etc/herdr/operators.json`. The shared
dashboard token keeps working as the bootstrap administrator and is shown as
`dashboard`. Coordinator approvals, retries, repairs, SDK installation requests
and validation runs record the authenticated operator name; validation waivers
require the admin role.

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
Group creation initializes the facilitator only. Its startup prompt withholds
the group description and asks it to wait for a separate discussion message
with the purpose, task, roster and output paths. Discussions may read
or prompt only their selected members, not unrelated group conversations. If a
proposal or document is missing, they should report the missing input instead
of searching home or the filesystem for a substitute. Permission policies still
apply independently; dashboard output access does not grant whole-home access.
The facilitator is instructed to start with one round and stop when the answer is
sufficient. At most two follow-up rounds are requested by default, each to resolve
a material question. These defaults yield to the group's description or the user
message: those define the workflow, depth, format and desired artifact (for
example a decision memo, research brief or implementation plan). No assigned
work is a valid result, and a repository is required
only for tasks that need project files. Machine-wide searches, shell history and
tooling/credential directories are outside ordinary discussion scope.
Each discussion includes the participants' saved project, launch directory
(including individual worktrees) and live Herdr-reported cwd when available.
Members are instructed to confirm their directory before project-specific work
and report mismatches. These are agent instructions, not filesystem confinement
or a programmatically enforced round limit. Artifacts distinguish member reports
from independent observations and label assumptions and proposals.
Groups appear under **GROUPS** in the sidebar for the selected organization.
Each opens its own `/groups/<id>` page with **Posts**, **Artifacts**, **Members**
and **About** tabs. Each round becomes a feed post showing agent contributions;
**View resulting artifact** opens the action brief from that specific discussion.
Artifacts remain available across later discussions and group edits.

Use **Remove agent** on the Team roster, or **Remove group** on a group page
or the discussion-group list. A confirmation describes the removal. Queued or
running tasks block removal; a live agent that is working or has unknown state
must be interrupted first. Agents assigned to groups must be removed from those
groups before deleting their profile. Former direct reports become roots.
Removal closes only a verified bound Herdr pane and archives the dashboard
entry. Group removal also archives its facilitator, leaving member agents intact.
History and saved artifact files are retained; Git worktree checkouts and branches
are never deleted by these actions. Released or replaced terminal sessions are
not closed. Removed profiles/groups cannot be edited, relaunched or reused.

While a discussion runs, the group page polls live output once per second.
The collapsed **Live discussion** panel shows facilitator/member status and
incremental member replies from the validated transcript. Expand it to inspect
replies, the action-brief draft, or bounded terminal views. Terminal views can
include earlier conversation and CLI controls; they are not clean reply text.
The facilitator is instructed to update the transcript after each member reply.
This is live polling, not a token stream: clean replies appear when the agent
saves valid documents. Completed discussions stop live inspection and display
their final posts and artifact. Blocked runs keep their status/output visible.

An interrupted discussion can finish in its Herdr terminal after the dashboard
has stopped waiting. Its files then appear as saved drafts while the job remains
`needs_attention`. Use **Recover saved artifact** on that discussion to validate
and publish its existing Markdown and transcript without re-prompting agents.
Recovery requires the original live conversation bindings, ready/idle agents,
no overlapping queued/running tasks, and valid bounded output files. It preserves
the earlier error in job history. Missing/invalid files and blocked or replaced
conversations remain `needs_attention`.
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

Agent profiles store optional provider, model and reasoning settings. Blank values
preserve CLI defaults. OpenCode requires provider and model IDs together; an explicit
reasoning variant uses OpenCode v2 `provider/model#variant` syntax. Codex receives
session config overrides; Claude receives `--model` and `--effort`. Alternate Claude
providers and Antigravity settings remain configured in those CLIs. Settings apply
on the next launch, and group agents inherit the first member's launch settings.
Model and variant IDs must be available to the selected account.

The hire form offers provider, model and reasoning dropdowns. OpenCode's
**Load models from OpenCode** action runs `opencode models --verbose` in the
selected project, as the same LXC user as the agent. Its live list is authoritative:
models from unavailable providers are not added from a guessed catalog. Select a
provider first, then a model; the reasoning dropdown uses that model's enabled
variants. Changing provider or model clears the old reasoning choice.

OpenCode interactive reasoning selection requires v2 (`provider/model#variant`).
On v1, choose Model default; the gateway rejects explicit variants before launching.
Launches recheck the selected OpenCode model and variant against the CLI catalog.
Catalog presence does not prove paid account entitlement. Codex and Claude retain
curated suggestions and custom model entry; their lists are not live account catalogs.

Dashboard chat replies are captured as per-message Markdown files under
`~/.config/herdr-web/chat-replies/<job-id>/reply.md`, using the same live Herdr
agent conversation. Include this folder in backups. Missing, empty or oversized
reply files fail visibly rather than substituting terminal screenshots. Agents
need file-writing tools and permission to write the reply. Raw terminal output
remains in the separate live diagnostics view; historical terminal-snapshot
messages are collapsed by default. This is file-backed delivery, not a native
JSON/ACP runtime adapter or token streaming.

Chat polls live activity and partial reply files every second. While an agent is
working or blocked, terminal reads use the visible screen to avoid idle-only
history operations. Reply drafts, terminal activity and completed replies are
collapsed by default with bounded, scrollable contents. File previews become
available when the agent starts writing its reply; they are not token-level
streaming and are not marked complete until the final file is validated.

Agent settings expose OpenCode permission modes: CLI defaults, Allow dashboard
outputs, and Full autonomy. The gateway writes a named OpenCode primary agent
policy under `~/.config/opencode/agents/herdr-dashboard-<profile-id>.md` and
passes its name with `--agent` on launch. It does not overwrite the user's global
OpenCode config. Output mode includes external directory access to gateway
chat-reply and discussion-artifact folders; other tool restrictions still apply.
Agent and group forms also accept up to 20 **Additional accessible path globs**,
one per line (absolute container paths, `~/...`, or `$HOME/...`). These augment
output mode, or provide explicit external-directory overrides alongside CLI
defaults. Examples: `~/shared/reference/**` and `/home/herdr/worktrees/**`.
Patterns apply on the next launch and do not override separate read/edit/command
rules. Group paths apply to its facilitator; members retain their own policies.
Full autonomy sets OpenCode tool permissions to allow and is not a sandbox.

Herdr supports `worktree create`, `worktree open`, `worktree list`, and
`worktree remove`. Worktrees are separate Git checkouts opened as Herdr
workspaces and grouped with their parent repository. **Use a Git worktree** is
enabled by default for agents and group facilitators, including saved profiles
without this setting. Each new Git launch uses `worktree create`, a unique
`codex/herdr-<job-id>` branch from committed HEAD, and a checkout under
`<projects>/.herdr-worktrees/<job-id>`. Local uncommitted changes are not copied.
Non-Git directories use ordinary workspaces; worktree creation errors stop the
launch rather than falling back to a shared checkout. Existing sessions remain
in their original workspace until released and relaunched. The Runs tab records
the branch and checkout path. Release does not delete worktrees or branches;
preserve or merge work before removing them through Herdr. The toggle can be
disabled to use a shared project directory. Check `herdr worktree --help` for
support in the installed version.

Groups default to read-only discussion, including legacy groups without that
field. The facilitator instructs all members not to modify projects, while
allowing artifact delivery. This is a conversational policy, not OS enforcement.
Group permission modes apply to the facilitator's next launch; member settings
are independent. Use CLI defaults for non-OpenCode groups.

### Clone a project from the dashboard

On Dashboard > Workspaces, use **Clone a project** with an HTTPS repository URL (without embedded credentials) or `git@host:path` SSH URL and a new folder name. The gateway clones into a new subdirectory of `HERDR_PROJECTS`; existing folders are never overwritten. Private repositories use the container Git credentials. SSH needs a configured key and trusted host. Authentication is non-interactive, and cloning has a two-minute timeout. Clones run as persisted background jobs: navigate away and return to see queued, running, completed or failed status. A gateway restart marks unfinished jobs interrupted without replaying them. Git diagnostics are bounded and redacted in gateway logs. Failed clone folders remain for inspection and must be handled over SSH before reusing the same name.

After cloning, the workspace directory field is filled in. Create a workspace if needed, and set each relevant agent project directory to the returned repository path. Cloning does not change existing agent sessions; relaunch them to apply their project settings and create worktrees from the repository.

The project explorer uses Linux directory descriptors to prevent symlink swaps from escaping the configured projects boundary. Unsupported platforms fail closed. Previews remain limited to 64 KB and listings to 1,000 entries.

Group history is paginated, with faster polling while a discussion is active on Posts and slower polling when idle or viewing another tab. Each discussion preserves a collapsed snapshot of its original purpose and roster. Archived groups remain available in the sidebar for reading saved discussions and downloading artifacts. Transcript downloads normalize member names from the recorded roster; the original agent-written JSON stays unchanged on disk.

Browser sign-in uses an opaque HttpOnly, SameSite=Strict cookie valid for seven days. Refreshing or reopening the dashboard restores the session without storing the dashboard access token in browser storage. Sign out revokes that browser session. Gateway restart or token rotation invalidates existing sessions; Secure cookies are enabled explicitly for HTTPS deployments as described below. Bearer-token API access remains supported.

### Integration notices

A background watcher measures every checkout where a launched agent is working, once a minute, whether or not a dashboard is open. It is read-only: it runs `git status` and commit counts, and never prompts an agent, merges, resets or stashes anything. A notice is recorded when a checkout is behind its base branch, has unresolved conflicts, or is in the middle of a merge or rebase.

Notices appear as a count in the dashboard header, with a short message the first time one shows up. The list offers **Ask to integrate** for an idle agent, **Copy instructions**, and **Open Git changes** for that checkout. A busy agent's action stays disabled and the server refuses the request, so long-running work is never interrupted; ask once the agent has finished. Comparison uses the remote-tracking refs, so fetch first when the base branch looks stale. Both fetching and merging stay operator-initiated, and a notice clears itself once the checkout is current again.

### HTTPS browser sessions

For a trusted HTTPS reverse proxy, set `Environment=HERDR_WEB_COOKIE_SECURE=1` in a systemd drop-in for `herdr-web.service` (using `systemctl edit herdr-web`), then restart the service and preserve the original Host header. The gateway then marks session cookies Secure regardless of request headers. The default `0` supports direct HTTP LAN access and loopback SSH tunnels; direct HTTP LAN traffic is unencrypted. Use HTTPS or an SSH tunnel on untrusted networks. Invalid setting values stop startup.

### Fetching and worktree integration

In Project explorer → Git changes, **Fetch remote updates** updates origin's remote-tracking branches without switching branches or changing working files. Fetch supports HTTPS origins without embedded credentials and `git@host:path` SSH origins, using existing container credentials. Local refresh does not fetch; the last-fetch timestamp distinguishes cached tracking information from a recent remote check. A failed fetch is reported beside the local status rather than replacing it, and a checkout that cannot be inspected is listed with its error instead of disappearing.

Each checkout is also compared with `origin/main` (or `origin/master`), including agent branches without an upstream. Behind-main warnings and conflict counts remain visible while agents work. Use **Copy merge instructions** or, for an idle mapped agent, **Ask … to integrate main**. That explicit action sends a tracked organization chat request with a longer wait than an ordinary message, because merging and testing can outlast the default. Busy agents are not interrupted. The agent must preserve its work, merge in its own worktree, resolve conflicts and run tests within its assigned permissions. Fetching itself never merges, resets, stashes or discards files. A failed fetch, a conflict, or a merge request to an agent authorizes nothing on its own; the operator still reviews and releases the resulting run.

### Integration coordinator and recovery

After deploying, enable coordination under Project explorer → Git changes → Integration coordinator. A dedicated OpenCode coordinator collects decisions. The durable inbox waits for idle workers, supports integrate/defer/blocked decisions, merges exact commits, and verifies incorporation and conflicts with reported test evidence. Pausing prevents new delivery.

Before a merge request, the gateway pins a stash-shaped snapshot under `refs/herdr/recovery/<id>`, preserving tracked and non-ignored untracked files and the original staging tree without changing the checkout. Snapshot failure blocks delivery. The coordinator dashboard shows recovery references, separate-worktree recovery commands, and durable configuration/state audit history. Ignored files and submodule working directories require separate backups. Recovery references remain until an operator explicitly removes them. Audit history is not a tamper-proof security ledger.

### Development agent toolchain

For an LXC used for Flutter development, explicitly run `bash install/dev-tools-install.sh` as root from this repository. It installs Flutter 3.44.8 for the `herdr` user, selects the exact stable architecture from the official manifest, verifies SHA-256 before extraction, and adds `flutter` and `dart` under `~/.local/bin`. Existing unrelated launchers are preserved. The SDK, package cache and build outputs add several GiB, so size `var_disk` with headroom for them. Run `scripts/build-web.sh` as `herdr` from the assigned repository/worktree to validate the dashboard and shell. Dashboard-only installations do not need this SDK. The web installer also provides a root-owned `herdr-sdk.path` service: when the Integration card reports a missing toolchain, an administrator can queue the same pinned installation from the dashboard, which only writes a fixed request file. Existing OpenCode processes must be relaunched to pick up saved dashboard-output permission policies; changing a profile does not change a running process.
