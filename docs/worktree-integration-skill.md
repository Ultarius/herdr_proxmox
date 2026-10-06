# Worker integration skill

## Dashboard recovery and shared base updates

An interrupted worker event exposes **Recover saved result** and **Continue
validation only** in its details. Inspect the original conversation first. Both
actions require the original session to be idle and unchanged. Recovery reads
bounded, non-symlink output and lets the existing state machine parse the reply
and verify Git. Validation continuation retires uncertain delivery only after
verifying incorporation, no conflicts and no active operation; it preserves the
recovery snapshot and queues checks without another merge. Actions are audited
with the operator and are idempotent for the selected original job.

**Update base branch** is an administrator-only action on the shared checkout.
Review the expected local branch, remote-tracking comparison ref and exact target
commit. The gateway refuses local changes, divergent history, changed selections,
active Git operations and managed work using that checkout. Ignored local files
are never silently overwritten and repository hooks are disabled for this
administrative operation. It pins the old HEAD
under `refs/herdr/base-updates/`, audits the attempt, fast-forwards to the exact SHA
and verifies HEAD. Base update audit and recovery refs are visible in Git changes.
This does not deploy software or advance any worker branch. Recover the old HEAD
into a separate checkout; do not reset a branch that may now contain new work.

Gateway mutations share an exclusive lock keyed by the canonical Git common
directory, including linked worktrees. Fetch, snapshots, validation worktree
creation/removal and launch-time worktree creation join this lock. It is reentrant
within a thread and excludes other gateway processes. External Git clients and
agents do not automatically obey it: state rechecks and Git's own locks still
apply. Locks are released before waiting for worker replies. New worker notices
remain the watcher's existing idempotent flow, rather than fabricated notifications.

The assigned worker merges an exact supplied commit in its existing worktree.
The gateway pins the recovery snapshot before delivery, serializes work per agent
and verifies incorporation and conflict state. The coordinator summarizes supplied
evidence. It does not perform merges or require access to workers' checkouts.

Read-only preflight selects a fast-forward (`git merge --ff-only <exact-commit>`)
when HEAD is an ancestor of the target. Diverged histories use the regular merge
path. Workers recheck their checkout before mutation; they never blindly retry
after a dirty-tree, permission or operation-state failure. Both paths still pin
recovery first and preserve the assigned branch.

Merged commits with missing or failing tests use `validation_pending` or
`validation_failed`, shown as “Merged · validation pending/failed.” **Retry
validation** requests only checks: it does not merge again or replace the existing
recovery snapshot. Legacy blocked events with verified incorporation can also
use this path. Git verification and worker-reported test results remain separate
in the event details and audit history.

Worker jobs advance independently of the coordinator summary session. Job
completion and explicit fetches wake the watcher through a one-way event;
the 60-second scan remains a fallback. Store notifications never call back into
integration code while holding a database or agent lock.
Pausing coordination stops new dispatches but still collects and verifies the
results of requests already delivered.

For an interrupted coordinator report, **Recover saved report** reads bounded,
non-symlink reply output only after checking the original session is idle and
unchanged. If there is no recoverable output, inspect the conversation and choose
**Create fresh summary**. The gateway retires the interrupted job, increments the
report generation, and queues current evidence once. It rejects busy sessions.
If the old binding was released, the same profile is relaunched; the gateway does
not kill a terminal or widen permissions automatically. Both repair choices are
audited and safe to retry after a lost HTTP response.

The canonical procedure is
`web/gateway/skills/herdr-worktree-integration/SKILL.md`. The gateway includes it in
merge requests so agents on older commits receive the current procedure without
depending on skill discovery or reading files outside their checkout. Dashboard
updates copy the entire gateway directory, including the skill. No relaunch is
needed to receive the procedure in a newly delivered merge request. Already
delivered prompts retain their original instructions.

For native project discovery, copy the canonical `SKILL.md` (not this guide) to
`.agents/skills/herdr-worktree-integration/SKILL.md` in the target project and
commit it. OpenCode supports this location, including inside Git worktrees.
Existing worktrees receive it when they incorporate that commit. Avoid multiple
different copies under the same skill name. Runtime discovery support varies;
the dashboard's inline delivery is the fallback, not a permission bypass.

Keep Herdr's terminal-control skill separate. Export the release-matched official
skill with `herdr --skill` and install it in a location supported by the chosen
agent (for OpenCode, `.agents/skills/herdr/SKILL.md` locally or
`~/.config/opencode/skills/herdr/SKILL.md` globally). Review existing files before
replacing them. The `HERDR_ENV=1` guard applies before Herdr control commands;
it is a workflow guard, not a sandbox or proof of Git authorization.

Sources: [Herdr agent skill documentation](https://herdr.dev/docs/agent-skill/)
and [OpenCode skill discovery](https://opencode.ai/docs/skills/).

Coordinator summaries may describe work completed while the summary session was unavailable. They reconstruct supplied event evidence; they do not imply that the coordinator supervised those operations. Fresh summaries show a waiting status until dispatch is possible. Paused coordination stops new retry delivery while collecting in-flight results.

### Recovery rehearsal

A snapshot checkout alone shows saved files but does not restore the original staging state. In the affected repository, use a new empty folder:

```sh
git worktree add --detach <new-recovery-folder> <recovery-ref>^1
git -C <new-recovery-folder> restore --source=<recovery-ref> --worktree -- .
git -C <new-recovery-folder> read-tree <recovery-ref>^2
```

HEAD remains the original commit; the working files come from the snapshot and the index from its second parent. These commands apply only to the new recovery checkout. Inspect `git status` there before using the recovered work. Ignored files and submodule contents still need separate backups.

### Blocker categories

Worker blockers are stored as a fixed category instead of free text: `missing_toolchain`, `missing_permissions`, `owner_restriction`, `read_only_role`, `task_conflict`, `state_conflict`, `verification_failed`, `approval_expired`, `approval_stale` and `unspecified`. The board and audit records show the category label next to the worker's explanation, and approval actions are chosen per category. A category never grants permissions: `missing_toolchain` and `missing_permissions` cannot be waived, and no review changes runtime permissions. Widening an agent's runtime permissions remains a launch-time choice: agent CLIs expose different permission controls, so the dashboard does not guess flags for a running process. Grant a coordinator or worker the tools it needs when launching, and relaunch it to apply a profile change.

### Operator blocker reviews

Git changes → Integration coordinator → Review blockers shows worker reasons, the blocker category and test evidence. Select eligible actions individually or in bulk, choose validation retry, validation waiver or worker reassessment, and provide a reason. A waiver displays `Merged · validation waived`; original test results and recovery references remain unchanged. It is an operator exception, not a successful test result, and it requires the admin role. Paused coordination blocks new worker requests.

Approvals are scoped to the event revision, exact target and checkout. The gateway validates the entire batch before changing anything and persists the request ID for replay-safe delivery. An approval expires 24 hours after it is granted, and the coordinator re-verifies it immediately before delivery: the expiry, the approved checkout HEAD recorded at review time and, for validation actions, fresh Git incorporation. A failed re-check returns the event to `blocked` with `approval_expired`, `approval_stale` or `verification_failed` and audits `approval_invalid`; approved work is never delivered on stale evidence. Audit records identify the authenticated operator by name when named operator tokens are configured (see `cli-configuration.md`); the shared token is recorded as `dashboard`, and the older `dashboard_operator` label remains only for unknown actors. Uncertain jobs, ongoing conflicts and unverified incorporation cannot be waived through this panel.

### SDK installation service

The dashboard never runs privileged commands. A root oneshot service (`herdr-sdk.service`, triggered by `herdr-sdk.path`) watches `/var/lib/herdr-sdk-requests`: the gateway can only write a bare `{"action": "install"}` request, and the worker runs the pinned, release-matched `install/dev-tools-install.sh`, records status in `/var/lib/herdr-sdk/status.json` and refuses any other request shape. When `auto_sdk` is enabled in the coordinator configuration, a `missing_toolchain` blocker queues exactly one installation; otherwise an administrator presses **Install pinned SDK** on the Integration card. The gateway itself never gains root access.

### Exact-commit validation runners

**Validate exact commit** on an event materializes its target commit in a detached Git worktree beside the projects directory — never in the checkout the coordinator may deliver — and runs the first fixed script found there (`scripts/validate.sh`, then `scripts/build-web.sh`). No caller-supplied command is accepted; output is bounded and the worktree is removed when the run finishes. The board shows the runner state and offers **Download validation log**; the result is attached to the event as `validation_run` and audited as `validation_run`.

Operator identities are stored under `/etc/herdr/operators.json`, with a root-owned parent directory: a root-owned file inside the agent's writable home is insufficient protection against replacement. Recreate named tokens with `herdr-operator` if you used the earlier home-directory prototype. Automatic SDK provisioning requires administrator authorization. Validation worktrees isolate Git changes, not executable code: only validate repositories trusted to execute as the `herdr` user. Linux timeout handling stops the full process group; gateway restarts mark unfinished runs interrupted for operator inspection. Runs are serialized to limit resource pressure, and output is collected as a bounded tail. When both validation entrypoints exist, both suites run; an early failure stops the remaining suite.
