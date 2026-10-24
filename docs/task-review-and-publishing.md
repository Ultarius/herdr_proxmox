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
The optional `pr_token`, `pr_login`, and `clear_pr_credential` fields keep their
existing behavior.

For either a saved connection or a manually entered token, supply:

- A fine-grained personal access token belonging to an account with push access
  to each branch destination. Select its owner as resource owner,
  choose the repositories, set an expiry, and obtain organization approval if
  the organization requires it.
- Repository permissions: Contents write, Pull requests write, Checks read and
  Commit statuses read. Publishing workflow changes may also require Workflows
  write. Keep branch protection enabled.
- An allowlist of 1–20 repositories, one `owner/repository` per line. For this
  repository that is `Ultarius/herdr_proxmox`. The task's GitHub origin must match
  an allowlisted repository.

Each allowlisted repository also chooses a publication mode:

- **Direct** pushes the task branch and opens the pull request in the same
  repository. The account needs push access there, as before.
- **Through a fork** pushes the task branch to your fork and opens the pull
  request upstream. The fork must be created on GitHub first; Herdr selects an
  existing fork rather than creating one. Setup verifies that the fork belongs
  to the upstream's fork network and that the account can write to it, and it
  no longer requires push access to the upstream — that requirement would
  contradict the purpose of contributing through a fork.

The upstream repository always remains the source of the base branch and the
pull request target. Only the branch destination moves to the fork.

For private repositories under different owners, use two fine-grained tokens:
the push token selects the fork and grants Contents write (plus Workflows write
when needed). The separate PR token selects the upstream and grants Pull requests
write, Contents read for base-branch checks, Checks read, and Commit statuses
read for monitoring. Metadata read is included by GitHub. The PR account must
have the fork access required by GitHub for cross-account PR creation; Herdr
does not require its token to select the fork during setup.

Setup and publication preflight inspect the upstream with the PR token and the
fork with the push token. Subsequent upstream branch/check/review reads use the
PR token as well. Omitting a separate PR token keeps the single-token behavior,
which requires that credential to access both repositories. A missing permission
is never fixed by automatically retrying with the other token.

GitHub failures identify the repository, operation, and credential role in the
UI and gateway journal (`journalctl -u herdr-web.service`). API error responses
retain `source` and `github_status` and add `github_repository`,
`github_operation`, `github_credential_role`, and `github_request_id` when known.
Complete GitHub response bodies and raw field values are not included in these
diagnostics.
Credential roles follow the selected credential: a separate PR credential is
labelled `pull request token`, while its single-token fallback is labelled
`push token`. Explicit credentials carry that role from the same configuration
snapshot as the selected secret; the request URL does not determine its source.
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
A valid comparison
in a browser proves a diff exists for that browser session, but does not prove
the PAT has the same access or that draft creation is supported. Retry the
release PR action after updating the gateway to capture its validation reason;
Herdr still reconciles an existing exact-head PR before creating another.

Validate and save reports capability rather than asserting it. Account access
and token permission are separate, and setup is read-only:

```
Fork access: verified
Account write access to the fork: verified
Token write capability: not yet exercised
Upstream PR creation: supported | not_exercised
```

Setup never creates a branch or pull request to prove write access. Fine-grained
PR write permission is reported as `not_exercised` until a real operation succeeds.
One fine-grained token is limited to a single resource owner; separate fork and
upstream tokens allow this private cross-owner setup without granting either
token access to both repositories. Account access and GitHub's fork/PR rules
still apply.

The gateway needs working Internet routing and DNS. Publishing uploads the exact
reviewed commit; it does not merge or deploy it. Pull request creation and
subsequent GitHub review are separate actions in the task workflow.

A task records the destination used for it. Before publication an administrator
can switch between the upstream repository and its configured fork; the change
is audited. Once the branch is published the destination is frozen, so a retry
can never be redirected to a different repository. Tasks created before fork
mode existed keep publishing directly.

After publication, open Changes → Open draft PR. The dashboard fetches a fresh
preview and states both destinations — the repository the branch is published to
and the repository the draft PR is opened into — together with the source branch
in `owner:branch` form, the target branch and the exact candidate SHA. It lets
you edit the public title and Markdown description. Cancel sends no PR request.
Create draft PR submits the edited text with explicit approval for that SHA; a
changed candidate requires a new preview. New PR creation without this approval
is rejected by the gateway, including requests from an older dashboard version.
Existing PR reconciliation remains available.

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
task. Checks, reviews and merge status are always read from the upstream pull
request, and the upstream base branch must exist before a pull request is
created. Both account repository access and token permissions limit authority;
setup's read-only account-access check cannot guarantee token write permission.
Fine-grained tokens need Contents write, plus Workflows write when workflows
change; classic tokens use the corresponding repo and workflow scopes.

Tokens are stored server-side with restrictive file permissions and omitted from
snapshots. Gateway and agents currently share a Linux user, so this is not
credential isolation from same-user agents. The setup dialog explains this.

GitHub reference:
[managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

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
