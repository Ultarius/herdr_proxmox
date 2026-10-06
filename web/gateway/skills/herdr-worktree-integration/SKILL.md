---
name: herdr-worktree-integration
description: Integrate an exact commit into the assigned worker worktree after a Herdr dashboard merge request, preserving local work and reporting validation and blockers.
---

# Herdr worktree integration

The worker owning the checkout performs the merge. The gateway queues decisions,
pins recovery snapshots and verifies incorporation. The coordinator summarizes
supplied evidence; it does not merge or inspect other workers' directories.

## Scope and readiness

An integration assessment asks only for `integrate_now`, `defer`, or `blocked`.
Do not merge during assessment. Only a subsequent merge request authorizes the
exact supplied commit in your assigned checkout. A skill provides instructions,
not additional filesystem, command or role permissions. Respect explicit owner
restrictions and report a blocker if permissions or required tools are missing.

Before using Herdr control commands, check `test "${HERDR_ENV:-}" = 1`.
If it fails, stop using Herdr control commands. When it passes, use `herdr --skill`
for the installed binary's command reference. This check does not establish Git
permissions or authorize interaction with other agents.

## Merge procedure

1. Work only in the absolute checkout named in the request. Check
   `git rev-parse --show-toplevel`, the current branch, HEAD and
   `git status --porcelain=v1`. Confirm this is your assigned checkout and branch;
   do not switch branches, update the shared main checkout or touch other worktrees.
2. Use `git rev-parse --git-path MERGE_HEAD`, `--git-path rebase-merge` and
   `--git-path rebase-apply` to inspect operation state. A linked worktree's `.git`
   is often a file. If an operation is active, determine its purpose before
   continuing. Do not automatically finish or abort an unrelated operation;
   report a blocker when its ownership or target is unclear.
3. Verify the supplied full commit exists (`git cat-file -e <target>^{commit}`)
   and the supplied recovery ref resolves (`git rev-parse --verify <recovery-ref>`).
   If recovery evidence is missing, report a blocker before mutation. The gateway
   snapshot preserves HEAD, index, tracked files and nonignored untracked files;
   ignored files and submodule contents are not covered.
4. If local changes could be overwritten, defer or report a blocker with the
   affected paths. The recovery snapshot does not make destructive commands safe.
   Do not automatically stash, reset, clean, discard files or commit unrelated work.
5. Merge the exact supplied commit into your existing branch with
   `git merge --no-edit <target>`. Do not substitute `git pull`, a moving main ref
   or a rebase. If already incorporated, proceed to validation without another merge.
6. Resolve conflicts only within the assigned scope, preserving both the intended
   upstream changes and your work. Stage only resolved files. If resolution requires
   a product decision or affects unrelated work, stop and report the conflicts.
   Leave recoverable state intact; do not auto-abort, force-push or deploy.
7. Verify `git merge-base --is-ancestor <target> HEAD`, no unresolved entries
   (`git diff --name-only --diff-filter=U`) and no remaining merge/rebase operation.
   Run the checks relevant to the merged changes and your current task. Distinguish
   actual test results from static inspection. Missing required suites/toolchains
   mean `not_run`, even if another suite passed; retain all partial results.

## Report

Return only the JSON schema requested by the gateway:

```json
{"outcome":"integrated|blocked","commit":"full HEAD SHA","tests":{"status":"passed|failed|not_run","summary":"commands, results and any skipped required checks"},"reason":"integration result or precise blocker"}
```

Use `integrated` only when the target is incorporated and conflicts/operations are
resolved. `passed` means every required check was actually run and passed. The
gateway independently checks Git state; the test evidence remains worker-reported.
Write the reply through the dashboard's provided delivery instructions. Include
recovery references in a blocker where useful; restore into a separate recovery
checkout only on an explicit recovery request, never by resetting the working branch.
