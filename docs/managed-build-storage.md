# Managed build storage

Herdr separates source, retained evidence, installed SDKs, generated build output
and dependency downloads. Tasks keep their own mutable output and dependency
installations. Supported adapters can reuse download data within the same Git
repository and execution lane. Agent and independent-validation downloads use
separate pools.

The shared orchestration handles ownership, budgets, activity and reclamation.
The gateway passes its configured projects directory to storage operations;
storage helpers have no separate default or environment-derived project root.
Execution bindings retain that directory for finalization after a restart.
SDK adapters declare build profiles, output variables, download strategies,
command wrappers, legacy output locations and agent guidance. Adding an adapter
does not require adding ecosystem branches to session or storage orchestration.

## Compact builds

New environment plans default to `compact`. **System health → Managed build
storage** lets an administrator choose `full` for future plans. The selected
mode participates in the plan and policy digests. Existing frozen policies and
running sessions retain their approved settings; changing the default does not
rewrite them. Plan and approve the project environment again, then start a new
execution to adopt a new mode. For an eligible captured task candidate, an
operator can use the explicit validation-environment review to approve the new
prepared plan for that candidate. This does not update its agent session or
translate an unsupported storage-contract version.

The Rust adapter implements compact mode with:

```text
CARGO_PROFILE_DEV_DEBUG=line-tables-only
CARGO_PROFILE_TEST_DEBUG=line-tables-only
CARGO_INCREMENTAL=0
```

This keeps source-line backtraces and removes incremental compilation data. It
does not override assertions, overflow checks or the project's optimization
settings. Full mode requests debug level `2` and incremental compilation. It
uses more disk. Adapters without a compact profile retain their normal behavior.
Cargo commands follow the adapter-provided `CARGO_TARGET_DIR`; scripts that
hardcode `target/debug/...` must use that variable for managed compiler outputs.
Keep save data, screenshots and retained deliverables outside disposable output.

## Reuse without sharing mutable installations

The initial download strategies are:

| Adapter | Shared data | Private data |
| --- | --- | --- |
| Rust | Compressed registry `.crate` archives verified against `Cargo.lock` SHA256 checksums | Cargo configuration, credentials, extracted sources, Git dependencies and compiler output |
| Node.js | None | npm request metadata/download cache, `node_modules`, install-script output and project files |

Linked Git worktrees share the repository's canonical Git common-directory
identity plus the on-disk repository incarnation. Linked worktrees reuse the
archive pool; replacing/re-cloning a checkout at the same path gets a new scope.
Cargo archive transfers make independent copies; they never alias a writable
private cache through a hard link. A cold pool downloads normally. The next
validation check or a closed agent session publishes eligible verified archives.
Reuse is an optimization, not a restore receipt or permission to execute install
scripts. It does not change SDK verification or dependency-restore approval.

Agents are not granted shared archive-pool or ownership-control directories.
The broker verifies and independently copies archive bytes under a pool lease.
npm cache integrity verification does not make registry request metadata suitable
for sharing between executions. Its cache remains private. Python cache strategy
metadata is also private, including explicit pip arguments and copy-mode uv. It is not
enabled for Python restoration by this change. Other package managers and vendor
trees remain private until an adapter defines and qualifies a safe reuse model.
Sharing an installed tree or symlinking `node_modules`, a virtual environment or
a vendor directory between tasks is not supported.

## Ownership and retention

The store defaults to `~/.local/share/herdr/workspace-storage`; set
`HERDR_WORKSPACE_STORAGE_ROOT` on the gateway to use another filesystem. Objects
have exact registered identities, an ownership receipt and a generation. Sources,
SDK installations, validation logs/receipts, screenshots and unrelated ignored
directories are not inferred to be disposable. Per-execution permission grants
cover only registered `data/` children. Ownership receipts and shared archive
pools remain outside those grants. Grants are checked against the registry and
the configured store before an agent starts. All agents still share a Unix
account; these cooperative permissions are not an OS security boundary.

Defaults are:

| Limit | Default |
| --- | --- |
| Generated-output budget while a managed build is running | 8 GiB per execution |
| Retained output and private caches, across closed executions | 8 GiB |
| Retained output and private caches, per closed execution | 1 GiB |
| Retained shared download pools | 4 GiB |
| Idle retention age | 24 hours |
| Background retention interval | 5 minutes |
| Free-space headroom required by the build broker | 2 GiB |

These are cooperative build and retention limits, not filesystem quotas. Active
sessions retain their output; review-ready or idle status alone does not close a
session. A closed session can resume after its generated data has been reclaimed;
the tools rebuild it. Archive pools have their own short-lived broker leases:
an unrelated open session does not pin them. Pool retention can run while other
builds are busy; output cleanup also waits for the global build lane. Retention
budgets count eligible inactive bytes, rather than deleting closed caches to
compensate for protected active output. Usage can exceed these soft targets.

The check runner and adapter build broker inspect free bytes and inodes while a
process runs, including the managed store's filesystem. Managed output size is
checked periodically, with internal hard links counted once. A storage stop fails
the current check, prevents further validation commands, and retains source and
receipts. Sampling can overshoot a limit between checks; noncooperating commands
are not contained. Package restoration outside the check runner has its existing
permission/time limits and is not given a filesystem quota.
Free-space probes use the stable owned parent of generated output. A tool can
remove and recreate its `data/` directory while cleaning; absent data counts as
zero output, while the parent, checkout and managed-store filesystem remain
monitored. Ownership receipts stay outside that disposable directory.

Retention runs only on registered, inactive roots. Unknown activity, active
executions and live storage leases prevent cleanup. Real command processes inherit
their private/output leases, so a cooperating child that outlives the pane keeps
its data protected. Session closure harvests before publishing terminal state;
failed finalization stays visible per execution and is retried. One failed owner
does not hide the inventory or prevent finalization of other executions. Validation
storage diagnostics remain separate from the recorded build result. Failed preparation closes its
allocated owners, and a durable pre-agent failure marker allows reconciliation
after a busy registry prevents immediate closure. Uncertain abandoned sessions
remain protected until explicitly recovered/closed; they are not expired blindly.
Generations and ownership are rechecked before quarantine and removal. Internal
hardlink groups must be wholly contained in the root; external links, junctions,
unexpected ownership and unsafe paths are retained with a reason. A partial
cleanup keeps its quarantine receipt so a later cleanup can finish safely.
Linux registration, quarantine and recursive removal use pinned parent directory
descriptors and no-follow opens. Windows clients can inspect and allocate, but
destructive cleanup and legacy adoption are refused without these native APIs.
Windows storage locks are nonblocking and exclusive, including leases requested
as shared. Concurrent use can return a retryable busy error; it does not wait
for another holder. Linux provides shared reader leases for managed builds.
Database contention remains nonblocking. Background scans allow SQLite to recover
an existing rollback journal after a writer crashes, under the same registry lock
and sidecar validation as normal writes. Dashboard polling reads cached observations;
read-only inventory never performs recovery or creates a missing store.
If job or validation history lookup fails, the scan reports that authority as
unavailable and still measures registered owners. Activity is unknown, cleanup
is disabled, and healthy inventory remains visible while the lookup is retried.
Enclosing Git repositories are checked even outside the Projects root. Repositories
inside build outputs or legacy adoption candidates remain protected. Explicitly
owned private caches and installed dependency trees can contain dependency Git
checkouts/databases; these are disposable with their cache. Files tracked by an
enclosing source repository remain protected even in those caches.

## Operator workflow and existing worktrees

Open **System health → Managed build storage** as an administrator. Ordinary
dashboard refreshes read a cached inventory; **Scan storage** requests a fresh
scan. Select eligible roots, preview their exact paths and sizes, then confirm
cleanup. Activity and preview generations are checked again when cleanup starts.
Scans and cleanup failures appear separately from successful build evidence.

Pre-existing worktree output is never adopted automatically. Under **Existing
worktree outputs**, enter the source project folder (initially empty) and the earlier execution ID
(the 32-character worktree ID, not the task ID). Preview the exact adapter-declared
directory, then explicitly register it as disposable. Registration permits
automatic retention cleanup; manual cleanup uses the same preview/confirm flow.
The initial Rust migration contract accepts only the matching worktree's
`target/debug`. Sibling `target/test-saves`, captures outside that directory and
source files remain outside this cleanup. Tracked files, changed previews,
unknown/active activity and a mismatched Git repository prevent adoption.

Review each earlier execution independently after its session is closed.
Upgrading the platform does not adopt or delete existing output directories.

Administrative API: `GET /api/storage` returns cached observations.
`POST /api/storage` accepts `scan`, `preview`, `reclaim`, `configure`,
`legacy-preview`, `legacy-adopt` and `forget-invalid`. `reclaim` requires selected IDs and their
exact preview generations. Legacy adoption requires the complete unchanged
preview plus `reviewed: true`. Preview responses include every selected path and
per-root byte count, which both dashboards show in the confirmation. A busy
maintenance/build conflict returns HTTP 409 with `code: storage_busy`,
`retryable: true`, `retry_after: 2` and a `Retry-After: 2` header instead of waiting
on the HTTP thread. Retry the reviewed action after the conflict clears; it is
not retried automatically. Invalid registry rows are isolated from valid owners and new
allocations; they can be reviewed and archived with **Review registry repair**.
`forget-invalid` requires the row ID, its exact `row_digest` and `reviewed: true`;
it archives the metadata and never deletes files. Invalid path/project/adapter
metadata and mismatching regular ownership receipts can also be reviewed. For
receipt errors the preview digest binds the receipt bytes as well as the row;
changed or repaired metadata requires a fresh review. Missing/linked files and
activity refusals alone do not make a valid owner repairable. Archiving invalid
metadata leaves its contents unmanaged; it does not authorize later deletion.
There is no global 10,000-row
outage threshold. Configuration accepts the named settings in
`execution_storage.DEFAULTS`; byte limits and idle seconds are nonnegative
integers. Zero active-output limit disables that size limit; free-space checks
still apply. Operators without the administrator role cannot use this API.
The dashboards expose the build-mode selector. Budgets and idle seconds are
currently API-only settings through `configure`; they are not hidden dashboard
controls. Idle retention applies to closed/eligible roots. An open or uncertain
session has no age-based expiry: recover or explicitly close it before retention.

Agents receive adapter-provided mode/output instructions, scoped cache paths,
storage rules and failure remedies at launch and in their release-matched worker
guidance. A storage blocker must be reported; agents must not bypass wrappers,
enlarge budgets, delete shared data or repeatedly run the same failed build.
