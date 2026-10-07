---
name: herdr-worktree-integration
description: Integrate an exact commit into the assigned worker worktree after a Herdr dashboard merge request, preserving local work and reporting validation and blockers.
---

# Herdr worktree integration

The worker owning the checkout performs the merge. The gateway queues decisions,
pins recovery snapshots and verifies incorporation. The coordinator summarizes
supplied evidence; it does not merge or inspect other workers' directories.

Task branches are published only through administrator dashboard actions. Do not push, open pull requests, update the shared base checkout, or deploy during worker integration. Report your commit and evidence for review.

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
   to install yourself (see Missing tools), and retain all partial results.

## Missing tools and validation failures

A tool needed for your task is yours to obtain within the task's existing
permissions and owner restrictions. Pin the version required by the repository's
CI or lockfile. Prefer a task-specific, versioned cache under `$HOME` rather
than changing shared executables on PATH.

For a released binary, download over HTTPS and verify the publisher's SHA-256
checksum before extracting only the needed files. Reject absolute paths, `..`
paths and symlinks in the archive. Package-manager installs must use their
integrity verification: Go's checksum database (do not disable it), or
locked/hash-verified packages in a task virtual environment. `go install
<module>@<exact version>` requires compatible Go; if Go is absent, prefer a
verified prebuilt binary over bootstrapping an unrelated toolchain. Never
execute a downloaded shell script as an installation shortcut.

Keep installations inside the task's directories. Agents currently share the
`herdr` Unix user and its home: user-space installation is not agent isolation.
Avoid overwriting shared tools, `pip install --user`, modifying shell startup
files, global CLI configuration or shared package environments. Do not install
into `/opt`, `/usr/local`, or another task's directories, and do not use sudo or
system package managers. Use a virtual environment or task-specific prefix.
Scope PATH to the check command or invoke the tool by absolute path.
Keep downloads out of candidate commits; prefer a task cache outside the checkout.

Run the check for real. Report the exact commands, actual exit status, version,
integrity/checksum source and install location in your report. Integration
requests use `tests.summary` for that evidence, or `reason` when blocked. A
passing web build never substitutes for a missing required check. Do not edit
checks, weaken assertions or substitute a different tool.

If installation fails, stop bounded attempts and report the actual blocker,
including the tool/version, source, failing command and error (redact credentials).
Offline downloads, unsupported architectures, unavailable releases, missing disk
space and permission denials can block even user-space installation. Missing
required tools mean `missing_toolchain`; permission denials mean
`missing_permissions`. A check that never ran remains `not_run`. A check that ran
and failed remains `failed`, even if a later tool installation also fails.
Do not retry indefinitely or broaden permissions to bypass a failure.

Ask the platform for provisioning when a tool needs root, changes shared system
state, or is the project's pinned build toolchain. The fixed-purpose SDK service
installs Flutter/Dart only; it cannot provision arbitrary task tools. Agents do
not run `bash /opt/herdr-web/install/dev-tools-install.sh`. Report unsupported
platform requirements to the operator, with the reason local installation failed.

## Validation failures and permission requests

A test failure is not permission to investigate or change container infrastructure.
Inspect the failing test and implementation inside the assigned checkout first.
If a test mocks root identity but performs a real privileged operation (for example
`os.chown`), report a test isolation defect with the command, traceback and file
evidence. Do not work around it with sudo, Docker, user namespaces, ownership
changes, or reads of `/etc/subuid` and `/etc/subgid`. Fix tests only when the task
authorizes edits; a validation-only request requires reporting the failure.

Do not request broad access such as `/etc/*` to make validation pass. Report the
exact required operation and path if an assigned check truly needs permission.
Preserve a successful merge and its recovery ref; return tests `failed` for a
check that ran and failed, or `not_run` for a check that could not run. Never
repeat the merge to retry validation. Stop and report instead of expanding scope.

## Report

Return only the JSON schema requested by the gateway:

```json
{"outcome":"integrated|blocked","blocker":"missing_toolchain|missing_permissions|owner_restriction|read_only_role|task_conflict|state_conflict|unspecified","commit":"full HEAD SHA","tests":{"status":"passed|failed|not_run","summary":"commands, results and any skipped required checks"},"reason":"integration result or precise blocker"}
```

Use `integrated` only when the target is incorporated and conflicts/operations are
resolved. `passed` means every required check was actually run and passed. The
gateway independently checks Git state; the test evidence remains worker-reported.
Write the reply through the dashboard's provided delivery instructions. Include
recovery references in a blocker where useful; restore into a separate recovery
checkout only on an explicit recovery request, never by resetting the working branch.

Attempt a permitted task-local installation before reporting a missing tool.
If it cannot complete, report `missing_toolchain` with the tool, version and
actual failure. Administrator-approved SDK provisioning can resolve the pinned
Flutter/Dart toolchain; it does not guarantee resolution of arbitrary tools.
