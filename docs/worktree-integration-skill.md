# Worker integration skill

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
