# Managed task revisions

Herdr owns commit creation for newly launched ordinary task sessions. Agents
submit explicit files, an expected parent SHA and a message. The gateway creates
an immutable revision record and Git commit, then advances only the assigned task
branch. Git remains the store for file contents and commit history.

Existing sessions and commits remain capturable. Existing integration and
conflict-repair procedures retain their separately authorized merge protocol;
this endpoint creates single-parent task commits, not merge commits. New task
prompts, including handovers, require managed submission without a direct-commit
fallback. Submission does not approve policy changes or publish anything.

## Worker use

At launch, Herdr writes a private `commit-context.json` beside the completion
receipt. It contains the task ID, launch ID, assigned checkout root and existing
task-session token.
The prompt supplies actual installed paths. No dashboard credential is needed.
Generated commands use the gateway's running Python executable; `python3` in this
example is illustrative. That interpreter runs the Herdr helper only. Project
builds and checks must still follow the task's frozen toolchain policy.
The helper and its small shared contract/anchored reader are copied into the
release-matched, content-verified guidance bundle inside the checkout, so workers
need no external-directory permission to read deployed gateway modules.
From the assigned checkout, using those paths:

```sh
python3 /path/from/task-prompt/task_commit_client.py \
  --context /path/from/task-prompt/commit-context.json \
  --request-file /ignored/path/from/task-prompt/revision-1.json \
  --parent FULL_EXPECTED_HEAD_SHA \
  --message 'Guard stale-generation error writes' \
  --file web/dashboard/lib/logs_page.dart \
  --file web/dashboard/lib/ssh_access_card.dart
```

Repeat `--file` for every intended addition, modification, mode change or deletion.
Fresh capture requires the assigned checkout root as the current directory;
subdirectory invocation is refused before reading or sending files. Paths are
repository-relative. Replaying an existing captured request can run elsewhere.
There is no implicit staging of the whole checkout. A deleted file is submitted
by its original path, including when its parent directory has been removed.
Binary bytes and executable modes are preserved. Capture uses the shared Linux
descriptor-anchored filesystem boundary and refuses symlinks and special files.
Unsupported hosts fail closed.

Use the launch-specific ignored request path supplied in the prompt, or another
path outside the repository or in an ignored directory. The request is
written once with private permissions and contains captured bytes and hashes,
but no token. Retry the same file after an uncertain response; contents are never
silently recaptured or overwritten. Use a new file for a new revision. Its task
and run must match the current context. The helper uses loopback HTTP on port
8787; `--gateway` can select another loopback port. It does not discover credentials
or submit to remote hosts.

The result supplies a full commit SHA and revision ID. Inspect remaining edits,
run the required checks, then write the normal completion receipt last with that
SHA. Normal capture still requires a clean checkout. Submitting a revision does
not assert validation passed or start publication.

## API and bounds

`POST /api/tasks/submit` accepts only these fields:

```json
{
  "task_id": "32 lowercase hex characters",
  "run_id": "current launch ID",
  "token": "current task-session token",
  "request_id": "stable-request-key",
  "parent": "full expected commit SHA",
  "message": "Describe the change",
  "files": [
    {"path": "src/file", "mode": "100644", "content": "BASE64", "sha256": "CONTENT_SHA256"},
    {"path": "scripts/check", "mode": "100755", "content": "BASE64", "sha256": "CONTENT_SHA256"},
    {"path": "removed/file", "mode": "deleted"}
  ]
}
```

The request selects 1–512 distinct files, at most 2 MiB each and 8 MiB of contents
altogether. The HTTP body is capped at 12 MiB. Messages are at most 8000 bytes.
Full commit IDs, request keys, paths, modes and captured-content hashes are
validated. Paths cannot escape the repository, address Git metadata, overlap,
implicitly replace directories, or traverse a parent-commit file or symlink.
Symlink/gitlink changes are unsupported through this regular-file endpoint.

The token grants submission only for the current assigned launch. Terminal
tasks, old launches, replaced conversations and mismatched assignees are refused.
It grants no dashboard, publication or policy-review authority. Agents share a
Unix account and can already access Git: this is managed orchestration and
provenance, not exclusive filesystem enforcement. Verification remains required.

Authenticated submissions have process-local backpressure before base64 decoding
and before task/repository lock acquisition. Defaults allow a burst of 8 requests
per task-session token, replenishing one request every 5 seconds, at most 2 active
requests per session and 4 across the gateway. Invalid credentials allocate no
bucket. At most 2048 session buckets are retained; only inactive, replenished
buckets are evicted. Token digests stay in memory and never enter diagnostics.

Limits are load shedding, not a durable quota or a cross-process semaphore. A
restart replenishes the allowance; separate gateway processes have separate
budgets. Existing cross-process repository claims still serialize Git mutations.
JSON request reading/parsing and lightweight session authentication precede
admission, so this is not an ingress bandwidth or HTTP-thread limit.

HTTP `429` includes `Retry-After`, `retry_after` and `retryable: true`. No journal
or Git mutation happens for a limited request. Retry the same captured request
file after the delay, including after an uncertain completed submission: the
usual idempotency contract still applies. Completed replays consume allowance
too, since recovery/summary inspection can use Git. The helper reports the
refusal; it never recaptures, changes the request ID or loops automatically.

Operators may tune `HERDR_TASK_SUBMIT_BURST` (1–128),
`HERDR_TASK_SUBMIT_REFILL_SECONDS` (1–3600), `HERDR_TASK_SUBMIT_MAX_ACTIVE` (1–32),
and `HERDR_TASK_SUBMIT_PER_SESSION` (1–8) before starting the gateway. Invalid
values fail startup rather than silently disabling limits.

## Construction and recovery

1. Normalize and fingerprint the captured request. A stable revision ID derives
   from task ID and request key; different content under the same key is refused.
   Credentials never enter the persisted record or response.
2. Serialize task actions and cross-process repository mutations. Verify branch,
   expected parent, task-base ancestry and absence of unfinished operations or
   staged changes. Herdr refuses staged changes; the worker must unstage them
   before submitting, without discarding working files. Herdr does not unstage them.
3. Persist intent with captured inputs, original index fingerprint, frozen policy
   digest and fixed author/committer/timestamp metadata.
4. Write raw Git blobs, construct a tree with a private index, then create the
   commit with Git plumbing. Agent staging, hooks, content filters and inherited
   Git overrides do not determine the submitted contents. The author is the
   assigned agent; Herdr is the committer. Fixed dates permit identical replay.
5. Pin `refs/herdr/revisions/<revision-id>` before checkpointing construction.
   Remove captured byte copies from SQLite, retaining hashes, modes, deletions,
   parent, tree, SHA, message and provenance.
6. Compare-and-swap the task branch from its expected parent to the commit.
   Atomically replace its index without writing working files. Later edits remain
   visible dirty changes and prevent clean candidate capture.
7. Complete the journal and save a compact task summary. Retry repairs a missing
   summary without moving a newer branch backwards.

Git and SQLite do not share an atomic transaction. Checkpoints and retained refs
bridge construction, ref advancement, index replacement and task-save gaps.
Construction is serialized per repository, including across gateway processes.
Each journal checkpoint uses a short SQLite writer transaction; the SQLite writer
is not held throughout Git construction or while agents and checks run. This
Git-backed workflow is not a lock-free WAL or a replacement storage engine.
Parent-entry inspection uses prefix-free batches at each path depth with bounded
argument sizes. Absent ancestors stop further inspection; regular-file, symlink,
directory-replacement and missing-deletion refusals remain in force. Captured
blobs are written in one `hash-object --no-filters --stdin-paths` invocation and
the private index is updated through one NUL-delimited `update-index --index-info`
feed. Blob paths are private temporary files, not reopened workspace files.
Deep existing trees and large argument sets still require multiple inspection
batches; the file-count bound does not imply constant construction time.
Committed Git objects/refs request fsync; private marker/index files are flushed.
These mechanisms do not strengthen the storage device's own durability guarantees.

Native Git index locks are respected. Herdr atomically installs its own identifiable
marker while replacing the index. After a killed process releases the repository
lock, the exact submission can reclaim only its matching marker. Unrelated Git
locks are never removed. Independently advanced branches or changed indexes cause
refusal and preserve the revision for inspection. No reset, force-push, overwrite
of working files or automatic rebase is a recovery fallback.

## Review and downstream evidence

Both dashboards show recent managed revisions in a task's Changes section,
including commit/parent IDs, pending recovery, author/committer, file count,
frozen policy digest and bounded errors. Authenticated read-only history retains
the full file identities, newest first, up to 50 per page:

```text
GET /api/tasks/revisions?id=<task-id>
GET /api/tasks/revisions?id=<task-id>&before=<next_before>
```

Task detail carries at most 10 compact summaries. Neither history nor detail
exposes captured bytes or tokens. Historical commits stay reachable through retained Git refs.
Candidate capture associates its exact SHA with the managed revision ID. Build
events and frozen release members retain that ID where available. SHA remains
the identity of the checked artifact, including for legacy commits.

A frozen policy digest is provenance, not approval. Existing protected-input
review and required-check gates still apply. Updating to a new base produces a
new resulting SHA requiring fresh checks. Release integration, combined validation
and explicit publication retain their existing contracts.

## Verification

Administrator performance diagnostics expose `task.submit` and `task.construct`
timings, Git/lock measurements and the fixed `task.submit_accepted` /
`task.submit_limited` counters. There are no per-token labels. Compare these under
real gateway load before increasing admission limits or changing repository locks.
`task.submit` starts after initial authentication and includes admitted work or a
limiter refusal; `task.construct` includes construction/recovery under the lock.

For repeatable submission-only measurements on a disposable repository:

```sh
python3 scripts/benchmark-task-commits.py --files 64 --runs 3
python3 scripts/benchmark-task-commits.py --files 512 --runs 3
```

Use the gateway's interpreter, Git and temporary filesystem. Output includes each
sample's Git-call count and elapsed time; it excludes HTTP transfer and contention.
Local measurements are not evidence of production throughput or Linux capture.

`python3 -m unittest discover -s tests -p test_task_commits.py` exercises real Git,
binary/deletion/mode handling, candidate association, idempotency, concurrent
submissions, stale parents, branch/index races, session authorization and history.
Tests exit real child processes abruptly after ref advancement and completion
persistence to prove recovery without `finally` cleanup or in-memory state.

Linux-only tests exercise actual descriptor-anchored capture. The existing strict
full-suite CI command, `python3 scripts/test-project-discovery.py --all`, includes
them. Windows skips do not verify that boundary. Browser tests check provenance
presentation and keep commit submission separate from validation/publication.
