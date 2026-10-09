# Dashboard development tasks and draft pull requests

Tasks is the outbound contribution workflow. Worktree integration remains the
separate upstream-to-worker flow. Publishing never updates the shared checkout,
merges a pull request or deploys a build. For a step-by-step first run, see
[first-task-walkthrough.md](first-task-walkthrough.md).

## Administrator workflow

1. Fetch the repository through Project explorer. Hire an individual worker with
   its project set to that repository and Use a Git worktree enabled. Starting a
   task for a busy worker verifies the previous execution's evidence, archives and
   finishes that session, then starts the new task; a task whose agent is busy can
   instead be queued and starts when the agent is free. A task never silently takes
   over an existing conversation. See [agent-scheduling.md](agent-scheduling.md).
2. Open Tasks, create a task with acceptance criteria and required checks and select
   the worker. The base is optional: leaving it empty uses the repository's configured
   base, then origin's recorded default branch, then main or master only when exactly
   one exists; otherwise the gateway asks for an explicit branch. An explicit base
   such as `refs/remotes/origin/main` is also accepted. The task
   records the current remote-tracking commit, not a moving branch name. The first
   task sets the repository base in its common Git directory's `herdr-base.json`.
   Subsequent tasks, inspection, watcher and base updates use that same base;
   conflicting base selections are rejected. Future normal worktree launches also
   start at that configured base. Existing worker branches are not rewritten.
3. Launch task agent. A dedicated `herdr/task-<12hex>` branch is created from the
   repository. The gateway then pins only this newly created, clean checkout to the
   recorded start SHA, under the repository lock, and verifies HEAD reached it before
   any agent starts. Existing branches and checkouts are never reset or reused
   automatically, so a live or released task branch is left untouched. When the
   assigned agent is busy, launch first runs the verified handoff described in
   [agent-scheduling.md](agent-scheduling.md), or the task is queued instead.
4. The agent implements, tests and commits locally. Inspect its conversation via
   Organization/agent activity. Capture candidate requires a clean task checkout,
   the expected branch and an actual commit descended from the recorded base.
5. Review its diff and exact SHA. Validate candidate uses the existing exact-commit
   build runner and attaches independent check evidence to that SHA. Missing checks
   are not passes. Retained artifacts/logs remain on Builds & deployments.
6. Configure GitHub publishing, then Review and publish. This is explicit upload
   approval. The gateway pushes only the immutable reviewed SHA to its task branch,
   never a protected base branch and never with force. A changed or dirty checkout
   requires another candidate review.
7. Open draft PR. Creation finds and reuses a matching PR after a lost response or
   duplicate-create response. The body identifies the task, candidate and available
   independent check evidence. Publication itself does not certify tests.
8. Review and merge on GitHub. The dashboard polls open PRs every 60 seconds while
   running, and offers manual refresh. It displays check runs, legacy commit
   statuses and individual review records; these are evidence, not a synthesized
   branch-protection approval. A changed PR head is flagged for review.
9. After merge, refresh/fetch and use the guarded Update base branch action. Build
   merged result targets GitHub's **resulting merge SHA**, including squash/merge
   commits, rather than the old PR head. Deployment is still an administrator
   approval on Builds & deployments. Workers incorporate upstream independently.

## GitHub credentials and boundaries

Use a fine-grained personal access token limited to selected repositories. Grant
Contents write, Pull requests write, Checks read and Commit statuses read. Changes
to GitHub workflow files may also need Workflows write. Repository settings and
organization token policies can further restrict access. See GitHub's
[permission reference](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
and [pull request API](https://docs.github.com/en/rest/pulls/pulls).

The administrator enters the token in Tasks → GitHub publishing. Validation checks
the account and repository push access; subsequent PR/check operations report
missing permissions explicitly. The gateway writes `github.json` beside its
organization database with mode 0600. Tokens are absent from snapshots, task audit,
remote URLs, process arguments and surfaced GitHub errors. Git uses an askpass
helper reading the file. Publishing runs from a temporary bare Git configuration
with the task repository's objects, so repository hooks, credential helpers and
URL rewrites cannot redirect authentication. API and push redirects are refused.

**0600 is not isolation from agents running as the same `herdr` user.** Current
agents can read same-user files, including this credential. Use trusted agents and
repositories; do not promise secret isolation. Enforced isolation requires a
separate publishing identity/broker and authenticated fixed operations. Do not
enable unattended publishing based solely on a worker's instruction to push.

API requests are bounded and use ETags. Oversized/paginated check/review responses
report incomplete evidence instead of treating a partial list as complete.

## GitHub repository settings

Keep branch protection and required reviews enabled on the base branch. The gateway
refuses to publish outside `herdr/task-<12hex>` and never uses force, but repository
protection is the authoritative control for merges into the base. Review the required
checks yourself: the dashboard reports check runs, statuses and reviews as evidence,
and a run that finished without verified required checks is labelled as such rather
than as a pass.

## Durability and current limits

`tasks.sqlite3` contains task records, queued assignments, organization
follow-up automation policies, immutable build-event selections and durable
request fingerprints. Mutating task operations require administrator identity and
a stable request ID; reusing an ID for different content is rejected. Pending push
requests reconcile remote head before redispatch; PR creation finds before creating.
Background polling never pushes, creates a PR or merges. Gateway restarts do not
automatically retry an uncertain publication; the operator repeats the request.
Unchanged background polls do not rewrite the task or grow the audit trail, and the
per-task durable audit is pruned; a real change, a recorded failure and its recovery
are all kept. Build evidence that does not match the recorded task commit is recorded
as a task error instead of being retried silently. Upstream GitHub errors return
HTTP 502 with `source: github` and `github_status` (such as 401/403). Dashboard
authentication alone uses HTTP 401, so expired publishing credentials do not sign
out a valid dashboard session. Publishing resolves the repository object store under the
repository lock and then pushes without holding it, so a publication cannot block
launches, base updates or candidate capture.

Authenticated operators intentionally have read access to task titles,
descriptions, commits, diffs, repository/PR identities and task evidence. This
matches their existing organization and project read access; it does not grant
publication or credential-management rights. Task lists expose only whether
publishing is configured to operators. The publishing account login and repository
allowlist, including `GET /api/github`, require an administrator. These roles do
not provide per-project confidentiality: that requires a separate project ACL
model applied consistently to task, organization, file and build endpoints.

V1 merges on GitHub. There is no dashboard merge API, automatic publication,
automatic build on PR merge, or automatic deployment. Builds of a merged result
are explicit; remote commits must have been fetched locally. Existing configured
base changes and task-session replacement need a dedicated reviewed workflow;
launch does not reset a released or live task branch. Configuration export does
not include tasks, repository objects or GitHub credentials; preserve the SQLite
database and repositories separately when migrating.

Acceptance still requires a sandbox GitHub drill after deployment: token expiry,
fine-grained permission failures, lost responses, conflicting/rejected pushes,
failed CI, external PR head changes, restart reconciliation, merge-result build
and separate promotion/rollback. Local tests use mocked GitHub and real temporary
Git repositories; no production branch or pull request is created by those tests.
