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
5. For a clean checkout, first use `git merge --ff-only <target>` when HEAD is
   an ancestor of the target. This updates the branch without a merge commit.
   If histories have diverged, use `git merge --no-edit <target>` instead.
   A fast-forward refusal is not evidence of conflicts. Do not blindly retry a
   failed command: first exclude dirty files, permissions and active operations.
   Do not substitute `git pull`, a moving main ref
   or a rebase. If already incorporated, proceed to validation without another merge.
6. Resolve conflicts only within the assigned scope, preserving both the intended
   upstream changes and your work. Stage only resolved files. If resolution requires
   a product decision or affects unrelated work, stop and report the conflicts.
   Leave recoverable state intact; do not auto-abort, force-push or deploy.
7. Verify `git merge-base --is-ancestor <target> HEAD`, no unresolved entries
   (`git diff --name-only --diff-filter=U`) and no remaining merge/rebase operation.
   Run the checks relevant to the merged changes and your current task. Distinguish
   actual test results from static inspection. Missing required suites/toolchains
   mean `not_run`, even if another suite passed; first obtain a tool you are able
   to install yourself (read `tools.md` beside this file), and retain all partial results.
