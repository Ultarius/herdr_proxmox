---
name: herdr-worktree-integration
description: Assess or integrate an exact upstream commit in an assigned Herdr worktree, validate it, and acquire missing task tools. Use for gateway integration requests or development-task tool setup; it grants no merge or publication permission.
---

# Herdr worktree integration

The owning worker integrates its checkout. The gateway queues requests, pins
recovery and verifies Git; the coordinator summarizes supplied evidence.
Respect the exact request, owner restrictions, assigned branch and CLI permissions.
An assessment requests a decision only. Validation-only requests never authorize
another merge, reset, stash, branch switch or deployment. Dashboard administrators
handle publication, pull requests and updates to shared base checkouts.

Before Herdr control commands, require `test "${HERDR_ENV:-}" = 1`, then consult
`herdr --skill`. That environment flag grants no Git or other-agent permissions.

## Choose the applicable reference

Read only the resources needed for the current request, before taking its action:

- **Assessment:** inspect the assigned checkout and report integrate_now, defer
  or blocked. No mutation. Read [merge procedure](references/merge.md) when
  determining merge readiness; do not execute its mutation steps yet.
- **Authorized merge:** read [merge procedure](references/merge.md) before Git
  mutation. Verify the target and recovery ref; preserve unrelated work.
- **Validation or a failed check:** read [validation and permissions](references/validation.md).
  Verify incorporation, conflicts and operation state. Run required checks;
  static inspection and a successful web build cannot replace a missing check.
- **Missing task tool:** read [tool acquisition](references/tools.md) before
  installing. Use pinned, integrity-verified task-local tools within existing
  permissions. Agents share the herdr account/home; do not overwrite shared tools.
- **Integration reply:** read [report contract](references/report.md). Use the
  exact JSON schema supplied in this request and its reply delivery instructions.

A check that never ran remains not_run; a check that ran and failed remains
failed. Report actual commands, statuses and missing checks. Instructions are
not extra permission: if a linked resource is unreadable, report the precise
path and limitation rather than guessing its contents or requesting broad access.
