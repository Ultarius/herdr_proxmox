# Release candidates on the workflow board

A release candidate records a repository, release name, optional planned cut
date and selected task commits. Create it from **New release candidate**, then
select tasks and use **Add to …** in their inspectors. A task may belong to more
than one release. Follow-up provenance and execution-wait links are not code
dependencies and never determine which tasks are selected.

Release nodes open a wide **Release details** dialog with space for member
evidence, combined checks, automation and review controls. The layout switches
to one column on narrow screens, and long names and commit hashes wrap.
**Back to workflow** or Escape closes the dialog and returns focus to its node;
the board remains mounted with its zoom, filters, selection and pan position.
Reopening the same release preserves unfinished form edits.

The workflow toolbar's **Full screen** control expands the board to the browser
viewport. **Exit full screen** or Escape restores the embedded board without
resetting its view. When release details are open, Escape closes those first.
Switching to List also leaves full-screen mode.

Release details use a native modal dialog: the rest of the page is unavailable
until it closes, including navigation outside the workflow. Clicking its backdrop
keeps Escape available. Escape during IME composition does not dismiss details
or leave full screen. Closing details keeps the node selected; if a refresh removes
that release, the dialog closes and focus returns to an available workflow control.

New release names are unique within a repository, ignoring ASCII case. Existing
duplicate labels remain readable during upgrades; use each record's ID to
distinguish them. Eligibility snapshots reuse loaded tasks and batch missing
records on one connection. Notifications omit unchanged and audit-only saves,
while retaining real incorporation progress.

If freezing reports that recorded checks used a different policy, open that
member's task. **Validate latest commit** appears in its top **Next step** area
and in the bottom actions, even if a historical run passed. **Checks & builds**
distinguishes that recorded pass from current-policy readiness. The detail page
assesses the current approved policy without rewriting the historical evidence;
an unavailable policy assessment does not count as a current pass.

Validation may queue the existing branch-update process when upstream policy
files moved. If that captures a new candidate SHA, remove and add the member
again to review its new selection before freezing. The durable build queue
already distinguishes policies for the same commit, so a new approved policy
can create a fresh run without discarding the older result.

## Plans from group discussions

Enable **Create release plans from group discussions automatically** in the
organization automation settings. It defaults off, requires an administrator,
and applies to discussions created after enabling it. Existing finalized
discussions are not reprocessed. Pausing automatic follow-up also prevents new
plans. Task queueing, candidate capture and validation retain their own grants
and limits; a release plan does not start a draft task.

The final discussion artifact may include an explicit plan in the same JSON
object as its proposals:

```json
{
  "task_proposals": [
    {"ref": "api", "title": "Implement API", "description": "Acceptance criteria and checks", "profile_id": "worker", "needs_review": false},
    {"ref": "tests", "title": "Test API", "description": "Acceptance criteria and checks", "profile_id": "worker", "needs_review": true}
  ],
  "release_plan": {"name": "alpha.15", "task_refs": ["api", "tests"]}
}
```

References must identify 1–10 successfully materialized proposals from that
discussion, each with one unique `ref`. All selected tasks must belong to the
discussion organization and the same repository, derived from their assigned
profiles. Herdr never infers membership from prose, titles, follow-up links or
other tasks in the repository. Invalid, ambiguous, skipped or cross-repository
selections create no release; the discussion records an explanatory outcome.
A conflicting existing release name is refused rather than modified.

Both standalone and task-linked discussions use this path. Release creation and
initial membership commit together under a deterministic organization/discussion
identity. A restart before the separate discussion marker is saved reuses that
release without overwriting later operator edits. The plan retains its group,
discussion, artifact digest, proposal and task provenance. Recovery can report
an already-created plan after the grant is disabled, but refuses a changed
finalized artifact. The discussion's Artifacts panel shows
creation or refusal and links to the board.

Planned tasks appear immediately, including drafts without a commit. While the
release is collecting, automatic members follow the latest captured task head
only after required checks pass for that exact SHA. A changed or no-longer-verified
head clears the selection until its checks pass. This tracking belongs to the
created plan; disabling creation affects new plans. Removing a planned member
and adding it manually switches it to explicit commit selection.

**Freeze and integrate** remains an explicit operator action, disabled while a
member has no selected candidate. Freeze rechecks every SHA against the current
approved policy and stops all automatic selection updates. Integration and
publication receive no permission from the discussion plan.

## Freeze and integrate

The default manual mode below is unchanged. Continuous private assembly is a
separate per-release authorization; discussion-created plans do not enable it.

Read-only repository questions and retained validation phase timings are
documented in [Repository questions and operation measurements](repository-queries.md).

**Freeze and integrate** pins the configured base commit, each selected candidate
SHA in selection order, approved environment policy and manifest digest. The
inspector's recorded check success is historical: freeze also requires that
evidence to carry the current approved policy digest. A published task or open
PR alone is not release admission. If freeze refuses an old-policy result, use
**Open task and validation**, choose **Validate latest commit**, and return after
its required checks pass. If that changes the candidate SHA, remove and add the
task to select the new SHA; no old result is transferred to it.

The approved project checkout must match the configured base. Every selected commit
must have passed required checks under that policy; changed candidates and
protected policy differences are refused. No implicit fetch or policy waiver is
performed. Local candidate commits are supported; prior publication is not
required.

## Continuous private assembly

In a collecting release's inspector, enable **Continuous private assembly**.
This authorizes the gateway to follow the latest checked heads of the selected
tasks and build private assembly revisions. It grants no publication, deployment,
task launch or policy approval. Membership may be edited while assembly runs;
the in-flight manifest stays immutable and new inputs wait for the next revision.

Each snapshot pins its base, selected eligible SHAs, approved policy, source
validation identities and publication destination. Source checks must pass for
their exact SHA and record a policy identity, but that source policy may be older
than the current one. Authored protected-input changes still stop assembly:
source comparison uses the merge base with the approved base so upstream-only
drift is not mistaken for an authored policy edit. The final combined commit is
checked against the approved base and validated under its current policy.

The existing journal, retained refs, detached worktree, parent verification and
exact-commit executor perform integration. Passing combined checks produces
**Assembly checked · awaiting freeze**, not a publishable release. Partial scopes
may assemble while other selected tasks await checks; freeze requires a current,
checked checkpoint covering the complete selected scope. Explicit exclusions
remain recorded with their reasons and original denominator.

**Freeze verified assembly as release candidate** rechecks the selected task
heads/evidence, base, policy, combined receipt, protected inputs and branch SHA.
It admits that exact immutable checkpoint and stops automatic tracking.
The freeze request names both the reviewed manifest digest and combined SHA.
Historical task checks alone cannot grant release readiness. Publication remains explicit.
An already-frozen release cannot silently change into continuous mode; revise its
selection first. The manual freeze path still requires per-task current-policy
checks before integration.

Completed or blocked snapshots roll to a new generation when eligible task SHAs,
membership, base or policy change. Old worktrees, conflicts, refs and receipts
remain inspectable through retained revisions. Active integration, validation and
agent work finish their current snapshot first. Identical failed inputs are not
retried automatically; use **Re-run combined checks** or inspect and resume.
Idle continuous releases are assessed on a 10-second safety cadence. Freeze
always performs fresh admission checks rather than trusting that cached view.

The default grant permits 20 assembly generations (maximum 100). Used counts
survive restarts and stop/re-enable; changing the limit is an explicit admin
action. Exhaustion is visible and never starts an unbounded rebuild loop.

Automatic conflict repair is a separate option within the grant. Select an
already-launched writable repository agent and a repair budget (default 2,
maximum 10). Its session must match the assembly's frozen policy. Only the
recorded unfinished merge with its exact parents can be dispatched; policy,
unexpected-worktree and verification failures do not authorize arbitrary repair.
The attempt reservation is committed before dispatch. Uncertain dispatch/save
results reconcile the same request identity without consuming another attempt.
An agent reply never substitutes for independent merge/result verification or
combined validation. Budget exhaustion stops for operator inspection.

**Stop assembly automation** prevents new generations and new automatic repair
dispatches, including a reserved repair not yet sent. Work already dispatched
may finish and be verified, but cannot publish. A stopped checked checkpoint
still needs an explicit freeze. The API authorization is admin-only through
`POST /api/releases/assembly`, with `enabled`, `auto_repair`, `profile_id`,
`max_runs`, `max_repairs`, and the usual request/edit-version identity.

Freeze performs Git inspection and member-ref retention outside the SQLite write
transaction, under the repository lock. Before committing the manifest it
rechecks the release version, selected task SHAs and their validation facts.
Retention refs include the manifest digest, so a failed final comparison cannot
block another selection's refs. A refused freeze can leave harmless retained
refs for inspection.

The gateway integrates into a dedicated detached worktree under
`herdr-releases`, beside the managed projects folder. It creates a unique branch
`herdr/release-<id>-r<generation>`, never switches the project or task checkout,
and never marks a task merged into main. Pending commits have retained Git refs.

The repository's cross-process operation lock owns each worker step. A persisted
merge intent pins both parents before Git runs. Recovery accepts a completed
merge only when its parents, clean worktree, protected inputs and release ref
match that intent. Ref updates use an expected old SHA. Unexpected external
movement is a visible blocker, not an instruction to reset or overwrite it.

All selected members are listed, even when task filters hide their canvas edges.
Dashed edges mean awaiting incorporation; solid green means incorporated into
this release, separately from main. Counts come from the recorded member results.

## Conflicts and checks

Conflicts retain the worktree and merge intent. Operators can resolve and commit
the existing merge, then **Resume inspected release**, or explicitly ask a
writable, launched repository agent to resolve it. The agent must have the same
frozen environment policy. Its prompt names the dedicated worktree and exact
parents, forbids publication and policy changes, and is deduplicated across
dispatch recovery. Herdr waits for its recorded reply and independently verifies
the resulting commit before continuing. An agent reply alone is not evidence of
a successful integration.

**Exclude member and abort its unfinished merge** is an explicit operator action
requiring a reason. It verifies the pending merge identity, aborts that member's
unfinished merge and retains its exclusion in the original selected denominator.
If the worktree has advanced unexpectedly, it stops for inspection.

Once integration finishes, the existing exact-commit validation executor checks
the combined result in a separate validation worktree. Individual task passes
cannot certify the assembled release. Capacity waits are retried; failed checks
remain visible. Restart recovery reconciles existing validation runs before
submitting another. Late results cannot reopen a published or revised run.

## Revisions and publication

Use **Revise selection** after a blocked, failed, ready or published run to start
another collecting revision. The previous manifest, results, worktree and branch
remain in `release_revisions`; its validation cannot certify the next revision.
Use **Inspect retained revisions** to browse older manifests and their recorded
member results, checks, publication destination and combined commit. These views
are read-only and cannot replay actions against the current release. History is
loaded in pages of 20; **Load older revisions** continues from the last generation.
Task candidates that advanced must be removed and added again to review the new
SHA. No automatic cleanup deletes retained worktrees or refs.

Publication is a separate explicit action for the verified combined SHA. Its
GitHub destination and branch are frozen before integration and shown in the
inspector. The existing isolated publisher pushes without force. A lost push
response is retried against that same destination and SHA. This publishes the
release branch; it does not create a PR, merge into main, deploy or promote it.
Authenticated publication runs on the Linux gateway, as task publication does.
The publisher additionally requires a persisted `publishing` run whose release
ID, branch, generation, resulting SHA and destination match the push. A branch
name matching the release pattern alone grants no publication authority.

The next deployment layer needs a receipt binding the frozen revision and
combined SHA to an immutable artifact digest, target environment and observed
deployment identity. Acceptance checks must name that exact deployment and
artifact. Promotion should reuse the accepted artifact rather than rebuild it;
rollback should identify a retained earlier deployment. Herdr's existing updater
and deployment-package format are specific to Herdr itself, so arbitrary project
deployments need an explicit provider contract. Publication, deployment and
acceptance remain separate facts; the node should display deployed, accepted or
rejected only after their corresponding receipts exist.

Administrator mutations use `/api/releases/{create,membership,assembly,freeze,revise,
resume,retry,repair,exclude,publish,review,review_policy,pull}` with stable request IDs. Manifest edits
require the observed `edit_version`; frozen-run actions additionally require the
manifest digest, and publication requires the reviewed `head_sha`. SQLite record
versions prevent a worker from overwriting newer callbacks or operator actions.
Authenticated operators can read `/api/releases/revisions?id=<release-id>`;
`before=<generation>` pages older entries and `revision=<full-manifest-digest>`
reads one release-scoped snapshot. A missing or incomplete active manifest is a
visible blocker, rather than an exception that terminates the release worker.

## Agents and group evidence review

The release is a coordination point for selected tasks. Each member shows its
assigned agent, recorded task state and (when available) live session activity.
The canvas node also counts implementing tasks. These facts are separate from
incorporation: an agent working on a task does not mean its code has entered the
release. New task launches include their current collecting/assembly membership
in the task instructions. This is a launch-time snapshot, not a new permission.

The working sequence is:

1. Assign tasks to the release and enable continuous private assembly. Agents
   continue working in their own task branches and submit managed commits.
2. Existing task capture/validation permissions govern delivery. Enabling
   assembly does not start draft tasks, grant candidate capture, or install tools.
   The release inspector's task links lead to the existing capture/validation
   actions when that evidence is missing. With task delivery automation already
   authorized, passing commits are consumed without another membership edit.
3. The gateway assembles checked commits, shows pending members and integration
   progress, and runs the combined required checks. A separately authorized,
   bounded conflict agent can resolve an unfinished merge. Herdr verifies it.
4. **Ask group to review release evidence** queues a discussion for a group whose
   members share the tasks' organization and configured repository. The store
   rechecks member repositories when accepting the discussion, so a roster edit
   between selection and dispatch cannot redirect a release review.

The retained review packet records full task IDs, selected and current SHAs,
their check facts, the manifest/policy digests and combined validation facts.
The prompt carries every selected SHA, a compact task prefix and an explicit
missing/changed-evidence indicator. Oversized discussion inputs are refused
before dispatch. A digest identifies the full packet; prompts and packets stay
in the journal instead of being copied into every tasks poll.

The group discusses missing capture/check evidence, risks and PR readiness. Its
bounded, redacted report, discussion job link and packet digest appear in the
release inspector. Changes to membership, task SHAs, check facts, manifest or
combined result mark the advice outdated; ordinary task activity does not.
Group output supplies neither a check receipt nor authorization. Release-review
artifacts are excluded from automatic task proposals and release planning, even
if the organization has separately granted those features.

Discussion intent and the exact prompt are journaled before dispatch. A stable
organization request identity reconciles an uncertain response across restart.
An interrupted review can be rechecked against that same job/request after
inspection; it does not silently start another group conversation. Existing
discussion scheduling waits for busy members rather than interrupting task work.
This is an instruction and orchestration boundary, not OS isolation from agents
that share the gateway's Unix identity.

### Automatic review after collection

Select a repository group, set **Automatic review limit (1–20)**, then enable
**Automatic release review**. This release-scoped authorization is independent
of continuous assembly, task capture/validation and publication. It can be
enabled while collecting; the worker waits for a checked full selection.

The trigger requires every selected member to be incorporated or explicitly
excluded, a combined receipt for the exact SHA under the frozen policy, and an
unchanged approved base/policy. Included task candidates must still match their
recorded SHAs, be past implementation, and have no pending required delivery
inspection. A partial assembly cannot trigger the broad review. Exclusion
states and reasons enter the review evidence; excluded code is not claimed as
incorporated.

The worker durably reserves one review per evidence snapshot and spends its
review budget before discussion dispatch. A current manually requested review
already covers that snapshot. Restarts, repeated polling, disabling/re-enabling,
and revising the release do not reset the counter or replay a reserved snapshot.
New checked assemblies can trigger another review within the remaining limit.
Failures and uncertain discussions require inspection, rather than an automatic
retry loop. The inspector displays the waiting reason, reserved count, report
and recommended follow-up actions.

The idle safety cadence does not delay queued discussion/PR effects. Committed
operator edits and validation callbacks invalidate the idle throttle by record
version; validation completion and automatic review reservation wake the worker
after saving their durable state. Inactive throttle entries are pruned.

Disabling the grant, or changing its group, cancels an automatic reservation
that has not started dispatch. Once dispatch may have happened, the same request
is reconciled instead of pretending the discussion was unsent. Group advice
still cannot certify checks, create/launch follow-up tasks, freeze, publish, or
open a PR. Follow-up recommendations remain visible for a separate authorized
action through the existing task, conflict-repair and release controls.

### Task completion and freeing agents

Task lifecycle state, execution completion and release incorporation are
different facts. Captured implementation normally becomes `review_ready`.
Incorporating it into a release does not set `completed` or `merged`. Automatic
`completed` applies to a verified no-changes result or a checked candidate whose
file tree matches the fetched configured base. An operator can also record an
inspected completion/supersession; a confirmed GitHub merge is recorded as
`merged`.

The assignment scheduler can reuse an agent before that task becomes terminal.
It verifies the task's completion receipt, exact branch/HEAD, clean checkout,
idle original session, and absence of other session owners. If durable task
delivery is enabled, passing verification and the agent's delivery inspection
must finish first (including task PR delivery if task auto-publication was
separately enabled). It then archives/closes the original execution and starts
the next queued assignment in order. A merely idle agent is insufficient.
Release incorporation, freeze and publication are not prerequisites for handoff.
A running group discussion does reserve its own participants through the normal
session scheduler; a member cannot review and implement the next task at once.
An independent reviewer group avoids competing for implementation agents.
Without queued work, the session may remain retained for inspection; optional
reaping policies govern cleanup. No-changes completion explicitly finishes its
session, and terminal template instances have their own verified cleanup path.

## Draft release pull requests

After explicit freeze and successful branch publication, the inspector offers a
title and description for **Create reviewed draft release PR**. This is another
administrator action, bound to the manifest digest and exact combined SHA. It
does not push again, merge, or deploy. The group may recommend this action; it
cannot invoke it through its report.

Before a GitHub write, the worker checks manifest integrity, the current approved
policy, the exact combined required-check receipt, the local release branch and
the published remote SHA. The upstream, publishing repository, branch and base
come from the frozen revision. The request uses the configured PR credential
and always creates a draft. Returned or existing PRs must match both repository
identities, both branches and the exact head SHA. Links use a constructed GitHub
URL rather than trusting a returned URL.

The approved text is journaled before the network operation. Lost responses and
duplicate-PR responses are reconciled by that exact identity before another POST.
An uncertain result leaves branch publication intact and exposes an explicit
retry action. Reusing an existing PR preserves its public title/description and
is labeled as reuse. A worker cannot overwrite a newer operator update or
callback with a stale save. Revised release generations do not inherit a PR
approval or a group's current-evidence claim.

## Verification

Run `python3 -m unittest tests.test_release_candidates tests.test_release_assembly
tests.test_release_coordination` for real-Git recovery,
conflict, evidence and concurrency boundaries. Run the Jaspr
`test/workflow_dom_test.dart` browser suite for release nodes, membership,
keyboard navigation, filtering and revision-bound actions. The existing
contribution suite exercises compatibility with task build routing. No live
publication or deployment is needed to run these tests. Publication-route,
migration and exact-candidate receipt regressions are in
`python3 -m unittest tests.test_publication_routes`.

## Release-first delivery

New organization configurations default to `publication_mode: release`. The
organization automation dialog in both dashboards selects `release`, `both`, or
`task`; repository overrides map managed repository-relative paths to a mode.
The route is copied onto each task at creation. Changing policy affects future
tasks, rather than silently redirecting work that is already running.

Upgrade migration preserves existing task records and existing organization
policies with the `task` route. Organizations represented by existing tasks also
retain that route. An organization without a stored policy or any existing tasks
uses the new default. Operators can opt into release-first delivery for future
work in the automation dialog.

* `release`: individual task push, publication-destination changes, draft PR
  creation and automatic task publication are refused by the gateway. Capture,
  validation and the existing agent verification/handoff barrier still run.
* `both`: either delivery path is available. It does not automatically publish
  both a task PR and a release PR.
* `task`: retains individual task PR delivery, including existing automation.

The automatic task-publication setting remains specific to the task/both routes;
it never grants automatic release publication. Group review, private assembly,
freeze, branch publication and release PR creation retain their separate grants
and explicit approvals. Agents can take their next assignment after the existing
implementation and delivery-inspection checks; they do not wait for the release
to be published or merged.

**Release this task only** is available on a captured task and in its workflow
inspector. It creates a one-member **collecting** release pinned to the reviewed
current SHA. It does not freeze, integrate, push, or create a PR. Repeating the
shortcut for the same task/SHA returns the same release, including after restart;
a changed head requires a new reviewed request. Normal admission, combined
checks and release publication rules apply. Existing batch memberships remain
explicit and are not silently removed.

Release branch publication writes receipts for incorporated members in the same
database transaction as the release state. Each receipt identifies the task's
exact source candidate, immutable manifest, policy digest and combined commit.
Branch publication, an open release PR, a closed PR and a verified GitHub merge
are separate stages. Task snapshots and details expose up to ten receipts per
task, labelled as covering either its current or an earlier candidate.

Open release PRs are refreshed by the worker once per minute; administrators can
also select **Refresh release PR status**. The observer checks the PR number,
source and target repositories, branch, base and exact reviewed head before
accepting merge evidence. Failed or contradictory refreshes preserve the prior
receipt and report an error. A recorded merge cannot be downgraded by a stale
observation. Network reads do not happen during ordinary task snapshot requests.

The release remains `published`, with its PR observation stored separately.
“Delivered through a merged release” does not set a member task to `merged` or
assert that its source SHA is an ancestor of main: GitHub squash/rebase merges
need not preserve that ancestry. A newer task candidate is never marked delivered
by an older receipt. Deployment, acceptance and rollback remain separate work.
