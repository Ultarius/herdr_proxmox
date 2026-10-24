# Task review and GitHub publishing

A validated candidate has passed its configured checks. Group discussion
finalization means the discussion output is ready, not that the candidate was
approved. Read the findings before completing the task. A follow-up may fix an
unmet acceptance criterion or propose an independent improvement; creating a
draft does not record which of those applies.

The task page shows the latest group review and its findings. New reviews record
the candidate SHA. An older candidate, or a legacy review without a recorded
candidate, needs comparison against the latest changes. Independent acceptance
review results remain visible separately. The full discussion and its proposal
actions are under Activity & agents.

## Back up all tasks

As an administrator, choose **Tasks → Database backup** in either dashboard.
The download is a timestamped `herdr-tasks-….sqlite3` file: a consistent SQLite
backup of the complete task database, including tasks outside the latest-100
dashboard listing and any active view filters. It preserves all stored task
fields, full audit history, retry records, assignments, automation policies,
release/revision records, knowledge and discovery records, and notification
metadata stored in that database. Exporting does not modify tasks or pause work.

The file contains task content and should be kept privately. It does not include
Git repositories/worktrees, external build logs or artifact files, organization
configuration, or saved credentials. Back those up separately. The authenticated
administrator-only endpoint is `GET /api/tasks/export`; responses are not cached.
Query parameters are ignored, so cache-busting parameters never filter the
export. `GET /api/tasks/export/info` checks the estimated database size and the
backup volume's free space without copying the database.

Temporary copies use `task-backups/` beside the live database, rather than the
system temp directory. Set the gateway's `HERDR_TASK_BACKUP_DIR` to a plain
absolute directory on another writable volume when needed. The directory is
created privately; each export has its own private scratch directory. The
preflight requires the estimated database size plus a reserve of at least
64 MiB (or 10% for larger databases). Space checks cannot reserve space against
other applications, so a later storage failure still preserves the live store.
Only one export can copy or stream at a time; overlapping requests return
HTTP 409 with `Retry-After`. Cancelled downloads release the copy and lease.
Copying has a five-minute limit, with separate, shorter lock-wait handling.

Downloads up to 64 MiB use the dashboard's buffered path, with size checks
against the preflight estimate, response header, and incoming chunks. It has
a ten-minute header/inactivity timeout instead of the ordinary 30-second read
timeout, and uses the filename supplied by the gateway. Larger backups in normal
cookie-authenticated browser sessions use the browser's native download manager,
which streams directly to disk without accumulating database bytes in JavaScript.
Its filename comes directly from `Content-Disposition`. The dashboard reports
that the download has started; check the browser's Downloads view for completion
or errors. Task operations may resume once the download has been handed off.
Bearer-only clients cannot hand authentication headers to a browser download;
their buffered fallback is capped at 64 MiB. Sign in normally with a browser
session or use the authenticated export API with an HTTP client that streams
directly to disk for larger files. The API has no browser-size cap; use a
sufficiently long client timeout and honor `Content-Disposition`.

This is a database backup, not a dashboard configuration import. For recovery,
pause automation and stop the gateway and agents first. Preserve the existing
`tasks.sqlite3` and its `-wal`/`-shm` sidecars before replacing the database with
the downloaded file; do not leave old sidecars next to the restored file. Use
the same Herdr release initially, restore matching organization/project data,
and inspect active tasks and pending operations before resuming automation.
The restored database must be owned by `herdr:herdr`, have mode `0600`, and live
in a directory writable by `herdr` (normally mode `0700`). Run
`PRAGMA integrity_check;` on the restored database as `herdr` before starting
writers, and require the single result `ok`. Check that the gateway user can
write the database and create its journal/WAL sidecars. If integrity or access
checks fail, leave writers stopped and preserve the original backup for recovery.
Repository commits and external artifacts cannot be recovered from this file.

## Configure GitHub

As a dashboard administrator, open Tasks → GitHub publishing, or choose
Set up GitHub publishing on a task.

You can choose a saved personal-access-token connection from **Publishing token
source** instead of pasting its token again. First save the token under
**Repository connections**, then select it here. The browser sends only its
connection ID; the gateway reads the private token file and runs the same
identity and account-access validation as manual setup. Selecting a connection
suggests its repository when the allowlist is empty; deliberate allowlist edits
are preserved. Validation and saving remain explicit, and setup never publishes
a branch or opens a pull request.

The connection must name the branch destination: its own repository for direct
publication, or the configured fork for fork publication. SSH deploy keys and
connections referring to the already-configured GitHub account are not PAT
sources. Successful cloning proves read access only; the reused PAT still needs
the publishing permissions below. In direct mode a saved connection permits only
one allowlisted repository: the repository named by that connection. In fork
mode the allowlist names the upstream, while its branch destination must match
the saved connection's fork. Every allowlist entry must use that same branch
destination. To publish to different destinations, enter a token manually; that
path supports up to 20 allowlisted repositories. A scope error identifies the
offending allowlist entry, its destination, and the selected connection's scope.

Publishing stores an independent copy of the selected token. Rotating or removing
the repository connection does not update that copy; reopen publishing setup and
validate/save again to copy a replacement. Revoking the PAT on GitHub invalidates
every copy of that same PAT. Existing separate PR credentials remain configured
unless explicitly replaced or removed.

API callers use administrator-only `POST /api/github/configure` with
`connection_id` in place of `token`, alongside the usual `repositories` and
optional `publication` fields. Supplying both credential sources is rejected.
`pr_token`, `pr_login`, and `clear_pr_credential` retain their existing behavior
for unprotected repositories. Protected publishing requires a new or preserved
upstream credential; clearing it or falling back to the push token is refused.
A preserved credential is revalidated against the current protected mapping.

### Protected private-fork publication

Protected mappings are deployment data in `/etc/herdr/publishing-policy.json`,
outside the replaceable release tree. The product ships only an empty example;
no project is protected by default. As root, create or replace the policy with
your own upstream and fork names before enabling protected publication:

```json
{"version": 1, "protected": [
  {"upstream": "SourceOwner/project", "fork": "ForkOwner/project",
   "mode": "fork_only", "arrangement": "manual_adoption"}
]}
```

These are fictional example names. Keep `/etc/herdr` root-owned, mode `0755`,
and the file root-owned with mode `0444` or `0644`; no ancestor may be writable
by other users. The root installer creates an empty policy only if none exists
and validates existing files without replacing their contents. After writing a
non-empty policy, run as root:

```sh
python3 /opt/herdr-web/web/gateway/publication_policy.py --arm /etc/herdr/publishing-policy.json
```

This standalone standard-library validator creates the root-owned companion
`/etc/herdr/publishing-policy.required` with mode `0444`. The gateway requires
this marker before enabling a non-empty production policy. Once enabled, a
missing or empty policy is an error even after a gateway restart; it cannot
silently turn a protected project into an ordinary publishing target. Re-running
the root installer also validates and enables a non-empty policy. Dashboard updates
and rollbacks leave this external file alone. Root can change the protected set
without a Herdr release; ordinary publishing setup cannot edit or clear it.
Back up both the policy and its `.required` companion separately: task database
backups and dashboard rollback do not restore them.
`HERDR_PUBLISHING_POLICY` selects another path only in development/tests. The
production gateway pins `/etc/herdr/publishing-policy.json` and ignores that
environment override. All ancestors of the policy and marker must be root-owned
and not group/other-writable (`/`, `/etc`, and `/etc/herdr` for the default path).
If migrating from the release-tree prototype, copy every existing protected
upstream/fork mapping to this external path and enable it before switching
versions. The updated root worker refuses replacement or rollback while legacy
mappings are missing from the external policy or protection is not enabled;
validation happens before stopping the gateway. An already-installed older
worker cannot gain this check retroactively: migrate and update the root worker
before initiating an upgrade. Releases never activate a bundled example as a
project-specific mapping.

On a fresh deployment without the `.required` marker, an absent or empty policy
means no protected projects and ordinary publishing proceeds. The administrator
sees distinct absent/empty-policy notices in Tasks. A running gateway also
remembers that it observed protection and rejects an unexpected empty policy.
To deliberately disable protection, root must remove the `.required` companion,
replace the policy with the empty example, restart the gateway, and revalidate
publishing settings. Removing only the policy never disables enabled protection.
A present invalid, unreadable, insecure or unsupported policy refuses gateway
startup with an operator-facing error. If it becomes invalid while the gateway
is running, credential reads, clone/fetch, and task/release listing remain
available; the snapshot exposes `protected_publication_error`. GitHub writes
fail closed until root repairs the file, because an unreadable protected set
cannot safely identify which repositories are unprotected.

The protected upstream is never a Git push destination, even if a legacy task,
a release record, or another repository's configuration names it. Backend
checks authorize the configured fork at the final push boundary. Direct mode
and upstream destination options are unavailable for protected repositories.
Herdr does not merge PRs or deploy releases.

This policy schema implements `fork_only` with `manual_adoption`. It does not
represent protected automatic PR creation. Unprotected repositories retain their
existing automatic direct/fork workflow.

Publishing currently stores **one global credential pair**, shared by the
allowlist. Multiple policy entries do not create independent per-project
credentials; every configured project must be accessible with that same pair.
Independent credential slots and their setup UI require a separate migration.

Supply two **distinct fine-grained PATs**:

| Credential | Resource owner and selected repository | Permissions |
| --- | --- | --- |
| Fork push PAT | `ForkOwner`, selecting `ForkOwner/project` | Contents read/write; Metadata read; Workflows read/write only when publishing workflow-file changes |
| Upstream PAT | `SourceOwner`, selecting `SourceOwner/project` | Pull requests read/write; Contents read; Metadata read; Checks read and Commit statuses read for available monitoring; no Contents write |

Protected setup accepts only `github_pat_` tokens. A prefix proves format, not
validity, permissions, or access. Classic PATs and OAuth/App tokens are refused
for this workflow; ordinary repository cloning and unprotected publishing keep
their existing credential support. A saved repository connection supplies the
**fork push PAT**; enter the separate upstream PAT in publishing setup. An
upstream PAT is never substituted for a Git push credential, and the push PAT
is never substituted for protected upstream PR operations.

The upstream PAT's lack of Contents write is an operator requirement that Herdr
cannot inspect. Herdr enforces the behavior independently: it refuses upstream
branch writes, merges, and unrelated mutation endpoints even if an overpowered
credential were supplied. Keep branch protection enabled.

Both repositories are private and have different personal owners. A single
fine-grained PAT cannot select the other owner's private repository, and two
PATs do not combine into one authorization for PR creation. The supported
protected arrangement is therefore: **Herdr pushes the reviewed branch to the
fork; the operator opens the draft PR on GitHub; Herdr verifies and adopts it**.
It never attempts automatic PR creation for this policy and never changes
credential type or destination to work around missing access.

Setup reads upstream/fork metadata with their respective credentials, checks
fork-network identity and the publishing account's reported write access, and
records read-only branch capabilities. Repository metadata access does not
prove Contents read. Probes of an existing default/base branch use the upstream
credential; an unavailable private fork is a manual-adoption diagnostic, not a
reason to prevent an authorized fork push. A not-yet-published release branch
is not required to exist at setup. After publication, the exact head branch may
be probed with the upstream credential; if readable its SHA must match the
approved commit. An unavailable fork-branch probe is diagnostic: adoption can
still succeed when the PR itself exposes complete matching head/base repository,
ref and SHA metadata. Missing PR identity evidence leaves adoption pending.

Git push uses an isolated publisher and a temporary `0600` push-only credential
file. Repository-scoped askpass prompts include the destination path and are
refused for any other repository. The PR token is not loaded by that helper.
This is credential-handling separation, not OS isolation from other processes
running as the same Unix user.

Unprotected repositories retain their configured publication modes: Direct
publishes a task branch and PR in the same repository; Through a fork publishes
the branch to an existing configured fork and targets the upstream. Protected
policy overrides those ordinary choices and all upstream fallbacks.

GitHub failures identify the repository, operation, and credential role in the
UI and gateway journal (`journalctl -u herdr-web.service`). API error responses
retain `source` and `github_status` and add `github_repository`,
`github_operation`, `github_credential_role`, and `github_request_id` when known.
Complete GitHub response bodies and raw field values are not included in these
diagnostics.
Credential roles follow the selected credential: a separate PR credential is
labelled `pull request token`, while the single-token fallback for unprotected
repositories is labelled `push token`. Protected publication has no fallback.
Explicit credentials carry that role from the same configuration snapshot as
the selected secret; the request URL does not determine its source.
An explicit credential without source metadata remains `provided token`.

For PR creation failures (HTTP 422), Herdr reads at most 16 KiB of the error
response and reports recognized validation categories: rejected head/base,
missing or invalid title/body/draft fields, no commits, an existing PR, or
unsupported draft PRs. Unfamiliar message strings are retained as bounded
`GitHub detail` text after removing the exact request credential and echoed PR
title/body, followed by credential, secret-assignment, URL, and control-character
redaction. Arbitrary response objects and `value` fields are never dumped.
API errors expose these summaries as `github_validation`.
Summaries are limited to three short strings. Conflicting structured fields and
message hints produce a neutral diagnostic. The flattened dashboard error keeps
all complete summaries, with a 2000-character bound above the maximum formatted
GitHub error size; the API array remains available for structured presentation.
Malformed, empty, oversized, and detail-free responses get distinct diagnostics.
When GitHub supplies a request ID with the expected hexadecimal/colon format,
it is shown in the error and exposed as `github_request_id` for support tracing.
A valid browser comparison proves a diff exists for that browser session, but
does not prove the PAT can read enough metadata for adoption. Protected flows
check for the operator-created PR; only unprotected automatic flows may create
a new PR after checking for an existing exact-head match.

Validate and save is read-only: it never creates a branch or PR to prove write
permission. Account write access and token write permission are distinct; the
latter is unproven until a real fork publication succeeds. Read probes do not
prove automatic PR creation, adoption, or check-monitoring capability.

### Open and adopt the protected draft PR

After publishing a task or release, use **Open GitHub comparison and create
draft PR**. The link identifies the upstream base and published fork head. On
GitHub, review the diff and copy or complete the suggested title and description,
then create the draft PR as the operator. Return to Herdr and choose **Check for
operator-created PR** or **Check for operator-created release PR**. Delivery
may also reconcile the matching PR during a later poll.

Adoption verifies an open draft PR's head repository/ref, upstream base
repository/ref, and exact approved head SHA. It records the origin as `adopted`
and preserves the operator's title and description; it does not claim that
Herdr created the PR or applied preview edits. A retry reuses the same verified
PR. A changed candidate needs its own current publication and review.

If no matching PR exists, the branch remains published and Herdr shows
`awaiting_manual_pr` with the comparison link. If GitHub hides or omits the head
repository metadata, Herdr cannot verify the identity and does not adopt the PR.
A visible browser comparison or an operator-created PR is not proof that the
upstream PAT can read enough API metadata for adoption. The record stays pending
with an explanation; Herdr does not guess an identity from a branch name or
weaken the match to make adoption succeed.

A task or release retains its recorded destination. Protected records cannot
be redirected to the upstream, including records created before this policy.
A published/frozen revision is not silently retargeted. Ordinary unprotected
flows retain the reviewed, exact-SHA PR-creation and reconciliation behavior.

The suggested body includes task context, current-candidate validation and
check results, acceptance-review state, discussion references, follow-up task
IDs, and a section to fill in risks and limitations. Worker-reported checks are
labeled separately from durable verification. The finalized discussion excerpt
is offered as a separate opt-in and is left out of the default body, because it
publishes the group's deliberation verbatim. Review and complete these sections
before submission; the generator does not infer acceptance or approval from
successful tests.

Approving the text records its SHA-256 and the exact candidate, not the text
itself. The description already exists on GitHub, and the task record is served
in full by the task list, so storing it there would add up to 30 KB per task for
no reader.

Publication and PR creation remain separate. Recovery distinguishes the cases
explicitly:

- **Push failed:** nothing is published; no branch exists at the destination.
- **Push succeeded, PR denied:** the branch is published at the recorded
  destination and the failure says so. Fix PR authentication and retry; no
  second push is attempted.
- **PR response lost:** retrying searches upstream and reconciles the existing
  pull request rather than creating a duplicate.
- **Existing PR found:** it is reused, and its title and description are not
  silently replaced.
- **Branch moved at the destination:** Herdr stops for inspection and never
  force-pushes.

Pull request discovery and validation both check identities: the head must be
the recorded publishing repository and branch, and the base must be the upstream
repository and the task's recorded base branch. Matching a branch name alone
would not be enough — it could attach another account's pull request to this
task. Checks, reviews and merge status are read from the upstream pull
request when the credential can read them. The upstream base branch must exist
before a pull request is created. Both account repository access and token permissions limit authority;
setup's read-only account-access check cannot guarantee token write permission.
The fork push token needs Contents write, plus Workflows write when workflows
change. The upstream token remains Contents read-only. Protected publishing
accepts fine-grained PATs only.

Tokens are stored server-side with restrictive file permissions and omitted from
snapshots. Gateway and agents currently share a Linux user, so this is not
credential isolation from same-user agents. The setup dialog explains this.

GitHub reference:
[managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

Check-runs, commit-status, and review reads that GitHub denies with 403/404 are
recorded as unavailable while readable PR and evidence fields can still refresh.
`checks_unavailable` is not a passing result: delivery cannot claim verification
or complete its check barrier from missing evidence. A real successful API read
is required before reporting that a monitoring capability works.

## Repair an existing PR’s merge conflicts

Confirmed GitHub merge conflicts take priority over the candidate’s previous
successful checks in the task header. Open **Activity & agents**: the conflict
card appears above Agent participation. **Resolve conflicts with the assigned
agent** queues an explicit repair of this task and its existing PR branch.
It does not create another task branch or pull request.

The repair records the published candidate and exact upstream commit. The queue
waits for the assigned persistent worktree agent and the organization’s session
budget; other work and discussion reservations are not interrupted. Before
dispatch, the gateway rechecks GitHub, verifies the clean original checkout,
fetches remote-tracking refs and pins recovery evidence. The original session
is archived before a fresh session starts in that preserved checkout.

The agent merges the pinned upstream commit, resolves conflicts and commits
locally. It must provide a fresh-session completion receipt. The gateway verifies
both original and upstream commits are ancestors of the repaired candidate,
and requires a clean checkout without an unfinished Git operation. Old checks
cannot validate the new candidate: durable validation runs for its exact SHA.

The card shows queued, working, permission-decision, validation and review-ready
states, and each transition is recorded in Task activity. Permission decisions
remain operator actions; allowing a prompt resumes monitoring automatically.
Temporary session-read failures show a retry message without claiming the agent
needs inspection. After three consecutive failures, the card shows **monitoring
unavailable**, including the failure count and diagnostic. Read-only retries
continue and normal monitoring resumes after a verified identity read. A changed
binding or conversation still requires inspection immediately; it is never
automatically treated as a transient connection failure.
A queued repair can be cancelled. Ambiguous session identity or delivery needs
inspection; an inspected recheck adopts an already submitted session rather
than sending another prompt. A changed PR head or base before dispatch requires
a fresh inspected request.

**Review and publish repaired candidate** uses the existing publication approval
flow and updates the same branch in the recorded publishing repository. The
existing PR receives that commit; the gateway never force-pushes or merges it.
Refresh PR status afterwards: upstream can move again while repair is running.

## Agent ownership and follow-ups

Task completion and execution completion are separate. A follow-up draft does
not reserve its assigned agent. A finished implementation may hand its agent
to the next queued task while the original remains Review ready.

The scheduler verifies the previous execution's receipt against the exact
candidate, a clean assigned checkout without an unfinished Git operation,
the live session identity and settled state, and absence of discussion
reservations. It archives the context and closes the verified session before
starting the next task. Failed checks retain ownership and require inspection.

A group proposal alone cannot replace these checks. It can discuss work that
is still running, recommend optional improvements, or refer to an earlier
candidate. Likewise, an idle pane by itself is not proof of completed work.

### Readable task export

The Tasks toolbar offers **Export JSON** beside **Database backup**. Both export
all stored tasks, complete history, and related metadata, including records beyond
the dashboard list limits. JSON is pretty-printed UTF-8 and can be opened in a
text editor. Repository files, credentials, and external logs/artifacts require
separate backups.

The JSON document has format `herdr-task-export`, version `1`, an export timestamp,
database version metadata, the SQLite schema, and every table with column
information and all rows. Stored JSON objects and arrays are expanded into nested
values; `json_text_columns` identifies them. Each row includes its SQLite storage
types and rowid when available. BLOBs are base64 strings, non-finite real values
are tagged with `sqlite_real`, and invalid or duplicate-key JSON remains text.
Use the SQLite backup for exact database restoration; JSON is a readable data
export, and there is currently no JSON import action.

Administrator-only endpoints are `GET /api/tasks/export/json` and
`GET /api/tasks/export/json/info`. They share the database export's single-flight
lock, snapshot consistency, private scratch storage, cleanup, and download limits.
JSON generation measures additional disk requirements before writing and has a
five-minute deadline. Its transfer preflight conservatively estimates expansion;
large exports use native browser streaming with a browser session.
