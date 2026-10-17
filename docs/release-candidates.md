# Release candidates on the workflow board

A release candidate records a repository, release name, optional planned cut
date and selected task commits. Create it from **New release candidate**, then
select tasks and use **Add to …** in their inspectors. A task may belong to more
than one release. Follow-up provenance and execution-wait links are not code
dependencies and never determine which tasks are selected.

New release names are unique within a repository, ignoring ASCII case. Existing
duplicate labels remain readable during upgrades; use each record's ID to
distinguish them. Eligibility snapshots reuse loaded tasks and batch missing
records on one connection. Notifications omit unchanged and audit-only saves,
while retaining real incorporation progress.

## Freeze and integrate

**Freeze and integrate** pins the configured base commit, each selected candidate
SHA in selection order, approved environment policy and manifest digest. The
approved project checkout must match the configured base. Every selected commit
must have passed required checks under that policy; changed candidates and
protected policy differences are refused. No implicit fetch or policy waiver is
performed. Local candidate commits are supported; prior publication is not
required.

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

Administrator mutations use `/api/releases/{create,membership,freeze,revise,
resume,retry,repair,exclude,publish}` with stable request IDs. Manifest edits
require the observed `edit_version`; frozen-run actions additionally require the
manifest digest, and publication requires the reviewed `head_sha`. SQLite record
versions prevent a worker from overwriting newer callbacks or operator actions.
Authenticated operators can read `/api/releases/revisions?id=<release-id>`;
`before=<generation>` pages older entries and `revision=<full-manifest-digest>`
reads one release-scoped snapshot. A missing or incomplete active manifest is a
visible blocker, rather than an exception that terminates the release worker.

## Verification

Run `python3 -m unittest tests.test_release_candidates` for real-Git recovery,
conflict, evidence and concurrency boundaries. Run the Jaspr
`test/workflow_dom_test.dart` browser suite for release nodes, membership,
keyboard navigation, filtering and revision-bound actions. The existing
contribution suite exercises compatibility with task build routing. No live
publication or deployment is needed to run these tests.
