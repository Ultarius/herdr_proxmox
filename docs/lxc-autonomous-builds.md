# Autonomous builds and deployment from the LXC

Research and live verification: 7 October 2026.

## Evidence and queue implementation

The runner executes discovered scripts independently under one total timeout.
Each script has runner-observed duration/exit evidence. HERDR_CHECK_REPORT adds
bounded JSONL records attributed to the script, not to an independent verifier.
build-web.sh now reports its individual checks and planned checks it did not run.
Passed, failed, unavailable, not_run, skipped and waived remain distinct; only
required passing checks with exit zero satisfy required_checks_verified.
Both artifact manifests and coordinator feedback retain checks[]. The dashboard
shows check results and warns about non-passing evidence before manual promotion.

The optional build_queue stores requests in SQLite, deduplicates repository/SHA/
policy/toolchain, bounds pending plus active jobs to one, records attempts and
keeps event subscribers. Restarted running jobs become interrupted. An explicit
retry creates a new attempt; repeated requests for queued/running attempts return
the same job. The worker runs without a browser and gateway polls publish durable
result feedback to each linked integration event without rerunning the build.

install/build-install.sh is explicit root bootstrap, not repository code executed
by the build worker. It configures an unprivileged systemd service with CPUQuota,
MemoryHigh/Max, TasksMax, IOWeight, nice and a disk-space precondition. Bootstrap
refuses unavailable CPU/memory controllers instead of claiming unenforced limits.
Limits are initial budgets and have not been measured on the LXC. Gateway fallback
remains available when the service is not configured. No service was installed
or enabled during local development.

The admin-only auto_build repository setting is off by default. Enabled
coordination schedules only terminal incorporated events with gateway evidence
(zero conflicts and no operation active), through the service queue. Multiple
worker events share repository/SHA builds. Full queues and absent services remain
visible on the integration board and are reassessed; failed attempts do not
automatically retry. Pausing stops new submissions, not in-flight builds.
The watcher polls active build results every five seconds without a browser.
Runner-owned post-build checks verify unchanged HEAD and tracked source before
retention. Script evidence remains distinctly attributed; this is not a sandbox.
Task/job linkage and event waivers are retained in build/package manifests.
Service restart cleans validated temporary worktrees and records interrupted;
unexpected request errors are terminal rather than restart retry loops.

Still pending: approved SDK retry orchestration,
load-based deferral, RSS fallback, retention pruning, isolated preview and the
independent maintenance service. Unattended builds require the service to be
installed and auto_build to be enabled; they are one stage of product delivery. No
automatic promotion policy is enabled.

## Verified live behavior

The dashboard's base update completed a fast-forward from a135f61 to a8cf7c2,
then from a8cf7c2 to freshly fetched 332f793. Main is clean and has zero commits
ahead or behind origin/main. The audit records the operator, old and new full
commit IDs, time, and a pinned refs/herdr/base-updates recovery reference.
Agent branches were not changed by this action. Fetch independently queued
Max's next integration; this latest worker job was still running during review.

## Existing foundation

- The gateway runs as a systemd service as herdr and restarts on failure.
- The integration watcher runs without an open browser, but fetching is explicit.
- The pinned Flutter/Dart SDK is installed and available through the service PATH.
- ValidationRuns accepts an integration event, creates a detached exact-commit
  worktree, runs scripts/validate.sh and/or scripts/build-web.sh, records an exit
  status, retains bounded static artifacts and removes the worktree. It rejects
  a second running gateway validation globally. The optional service uses a durable
  queue with one pending or active job.
- scripts/build-web.sh analyzes/tests Flutter and builds Flutter plus the Jaspr
  shell into web/public. Flutter release assets are static files; the browser
  does not compile the application.
- The updater installs fixed GitHub release packages with a checksum, backup,
  restart, and authenticated startup checks. It also accepts manifest-backed local
  packages through explicit administrator promotion.

## Recommended workflow

Step zero is one configured base ref per repository, shared by the watcher,
base updates and scheduled fetches. Validation must consume its pinned event
SHA, never re-resolve a moving branch. This consistency is not implemented yet.

The current runner rejects a second running validation globally under its
submission lock and collects a bounded output tail. These are existing code
protections; the optional service adds a durable queue and cgroup budgets. Logs now
publish that capped tail during execution. Successful runs retain web/public in
artifact.tar.gz plus manifest.json before cleanup, capped at 256 MiB of input.
The manifest records build ID, exact SHA, command, archive hash and exit status.
It also records individual checks and the expected toolchain pin. The pin alone
does not certify the actual SDK version or deployability.
Browser preview remains to be built; artifact download is now available.

Implementation progress: Builds & deployments now lists validation history,
downloads logs/manifests and checksum-verified static or matched gateway/UI
packages, and hosts release-update controls. Downloadable manifests remain
deployment_authorized=false and the actual required_checks_verified result: an explicit
administrator action approves manual deployment, but never creates passing
per-check evidence. GET /api/build reports installed identity;
unknown legacy installations remain unknown instead of borrowing main's SHA.
Both gateway and root updater refuse release installation in local mode.
Coordinator profiles are annotated by binding ID as used or inactive.

Still pending: isolated preview, manifest-bound retention pruning, verified SDK
identity, configured base, measured service budgets
and the independent maintenance rollback service. None of these should be inferred
from the presence of a downloadable full package.

The first end-to-end milestone is manual build, artifact download, isolated
preview, administrator promotion through the existing updater, then maintenance
rollback. Prove that loop before adding durable scheduling or automatic builds.
Configure a consistent base before enabling scheduled fetches. Before unattended
builds, move execution to a resource-limited service with one active build.

Design structured check evidence before the durable queue: each required check
needs an ID, status, command, timing and exit code. Missing tools are unavailable,
not passed or silently installed. Route approved provisioning through fixed SDK
service operations. Persist required checks that did not execute as not_run;
record skipped and waived checks separately from passing checks.

The current retained archive is static web/public only. It is a preview artifact,
not a full dashboard deployment package: it contains no matching Python gateway.
The separate versioned package containing gateway and UI with validated member
paths, hashes and source SHA is now retained, and local promotion uses the
existing updater's fixed request protocol: the gateway records administrator
approval, the root worker re-verifies the approved build ID and SHA-256, backs
up all files and configuration, installs, restarts and verifies the running
build ID and organization storage through authenticated APIs. Missing tokens
fail readiness instead of falling back to an unauthenticated root page.
Rollback stops database writers before its safety copy and restores that copy
if the previous deployment fails readiness. Root-owned audit.jsonl retains
operator approvals and transitions after requests are consumed. Release lookup
failures do not hide local deployment/recovery status.
Installer scripts in a package are never executed. Downloading a package never
authorizes deployment by itself.

Preview must use a cookie-isolated host as well as a separate origin: changing
only the port does not isolate cookies. Serve no production credentials and
allow no production API access. Static preview cannot validate gateway behavior.
A candidate backend needs its own port, explicitly constructed HERDR environment
and isolated database copy. Preview completion is not deployment approval.

Before automatic promotion, provide an independently served maintenance service
with its own authentication and fixed restore-last-healthy operation. It must
remain available when the dashboard gateway or UI is broken. Backups must be
consistent SQLite backups or be captured with all writers stopped; copying live
sqlite files alone is not a transactionally consistent backup guarantee.

Expose GET /api/build with build_id, source_sha and gateway_version, and verify
those against the promoted manifest through authenticated readiness checks.
Move maintenance controls to Builds & deployments. Label coordinator profiles
from the actual configuration binding as used by coordination or inactive;
never infer that binding from a display name or delete a duplicate automatically.

Use a deterministic job controller for scheduling and promotion. Workers own
their task decisions and worktree merges; an agent should not execute the
deployment transaction.

1. Fetch configured repositories periodically with backoff, using the existing
   repository lock. Make remote and base branch explicit; the watcher currently
   resolves origin/main or origin/master, whereas base update accepts a selected
   remote-tracking reference. Resolve the target once and carry its exact SHA
   through every following stage.
2. Update a clean shared base only under an explicitly enabled fast-forward
   policy. Dirty, divergent, or active-operation checkouts stop with an actionable
   dashboard state. Keep agent worktrees independent.
3. Queue validation/build jobs durably, keyed by repository, SHA, toolchain, and
   validation policy. Make retries idempotent; do not depend on an HTTP request or
   an open browser. Keep worker-reported checks distinct from runner evidence.
4. Execute jobs in a dedicated unprivileged systemd build service with a clean
   environment, bounded CPU/memory/tasks, timeout, and a shared one-build queue.
   The present runner is a subprocess of the gateway with its inherited
   environment. Separate service ownership prevents a gateway restart from
   destroying supervision and limits the impact of repository build scripts.
5. Retain successful output before removing the temporary worktree. Record a
   manifest with SHA, SDK version, test results, artifact hashes, build ID, and
   timestamps. Static and matched gateway/UI package retention are implemented;
   toolchain/check evidence, retention pruning and dashboard preview still need
   implementation.
6. Preview the retained artifact and smoke-test the compiled browser interface,
   authenticated APIs, migration/recovery paths, and static assets. Include the
   compiled JavaScript request-ID check introduced after the browser-only bug;
   Node is an additional dependency if this check is added to the LXC build.
7. Promote only a manifest-backed build through the existing fixed updater
   protocol. Gateway and UI are staged together, the previous files and
   configuration are backed up, and readiness verifies authenticated API access
   and the expected running build ID instead of the root page alone. Rollback
   restores the newest backup and keeps a safety copy. Toolchain/check evidence
   and explicit UI/gateway version matching remain open.
8. Preserve the previous deployment and consistent database backup. On failure,
   roll back code and apply the documented database compatibility policy. Keep
   deployment records outside the gateway process and across rollback; an old
   Git HEAD reference alone cannot recover deployed assets or database migrations.

## Dashboard and policy

Release packages and local builds need one deployment identity: distinguish the
package VERSION from an immutable build ID and source SHA. In local promotion
mode disable release installation until an explicit administrator switches mode;
never silently overwrite a local deployment. Readiness must verify authenticated
API access, storage availability and the expected build ID, beyond HTTP `/`.

A build service reduces blast radius but is not a sandbox. Promotion executes
branch-built code as the dashboard. Keep promotion administrator-only and
repository/branch-allowlisted, with retained evidence and rollback compatibility.

Expose separate states for fetched, integrated, built, previewed, deploying,
healthy, and rolled back. Display repository SHA, deployed build ID, runner
evidence, logs, queue position, resource use, blockers, and retained artifacts.
Provide Retry, Preview, Promote, and Roll back controls.

Start with manual build and administrator-triggered promotion. After proving
promotion and rollback, add automatic fetch/build. Later
offer opt-in automatic promotion for the configured trusted repository/branch
only after every required check passes. Validation waivers must not count as
passing checks for automatic deployment. Stop rather than repeatedly retrying
deterministic failures; a summary agent may explain them.

The live LXC currently reports four cores and 8 GiB memory, matching installer
defaults. Treat this as a starting point, not a verified capacity limit. Measure
peak build plus agent usage, reserve gateway headroom, and pause new builds under
memory/disk pressure. Check which cgroup controllers are delegated by Proxmox
before relying on per-service limits.

## Acceptance tests before enabling automatic promotion

- Lost HTTP response, duplicate scheduling, process restart, and LXC reboot.
- Remote advances during a build: promote the pinned SHA or supersede explicitly.
- Concurrent fetch/worktree creation/recovery; dirty or divergent base checkout.
- Build failure, timeout, OOM, low disk, unavailable credentials, and SDK mismatch.
- Artifact tampering, wrong SHA, mismatched UI/gateway, and unsupported migration.
- Failed readiness check triggers rollback while preserving audit/configuration.
- Browser confirms deployed build ID and compiled UI controls actually work.

## Primary references

- Flutter release build and static assets:
  https://docs.flutter.dev/deployment/web
- systemd resource-control reference (CPUQuota, MemoryHigh/MemoryMax, TasksMax):
  https://github.com/systemd/systemd/blob/main/man/systemd.resource-control.xml

Role-aware drift, matched package retention, local promotion with authenticated
readiness, rollback, structured check evidence and an optional durable build
service and opt-in automatic build scheduling are implemented. Autonomous fetch,
preview and automatic promotion are not enabled yet. Live queue/service limits
still require LXC verification.
