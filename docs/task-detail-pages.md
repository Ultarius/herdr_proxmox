# Task pages and evidence

The Tasks list is a compact index: title, repository, assigned identity, state, next step and last activity. Open task navigates to /tasks/<task-id>, which can be bookmarked or opened directly. The Tasks sidebar section stays selected.

## Live work on the workflow board

An agent's Working indicator describes its live session, not every task assigned
to that agent. The gateway exposes the task and launch-run identity only after
verifying that session's alias, pane and conversation. The workflow highlights a
task only when its task id, current run id and assigned profile all match that
binding. Older gateways, unavailable snapshots and unmatched or idle sessions do
not claim active task work.

An active card keeps its lifecycle badge (such as Pull request open), adds a
visible work phase, and has a distinct working outline. Branch updates can show
Resolving conflicts · running checks without changing the PR's lifecycle. The
agent chip names the active task; a filtered-out task is identified as hidden.
Selecting the agent chip brings its visible active task into view. The Jaspr
workflow List rows use the same binding so unrelated assignments do not all
receive a live ring. The Flutter task list and detail page show the same bound
work phase with an activity icon. Needs attention covers blocked sessions,
including permission prompts and errors; it does not imply only a question.
Gateway-owned profile/group labels are discarded unless the live session is
verified. Ambiguous task claims are suppressed for that snapshot and can recover
on the next poll; neither dashboard guesses or stops the sessions.

## Task sections

- Overview shows the original task description, including acceptance criteria and required checks, the assigned identity, completion/blocker information and automation settings. A primary action offers implementation start, exact-commit validation or review when appropriate.
- Activity & agents shows recorded participants, their launch-time roles/runtime/model when available, associated sessions and prior restarted sessions. Worker completion reports remain separate from independent build results. Activity entries have readable labels; repeated adjacent identical entries are grouped. Up to 500 retained audit records remain expandable.
- Changes shows the branch and immutable base/candidate identities, candidate diff, PR/merge information and a vertical Git graph. Selecting a commit opens its bounded first-parent diff and SHA-specific build evidence; root commits are compared with the empty tree. Viewing history is read-only and does not recapture a candidate.
- Checks & builds shows current-target evidence, previous candidates separately, checks/reviews and authenticated log download. Artifacts and deployment actions remain on Builds & deployments.

## Git graph

The detail API queries at most 30 commits in the recorded base-to-candidate range and includes the base. Commit messages, authors and parent edges come from Git. The graph shows newest commits first, compact branch lanes at the left, and separate build badges. Missing history is an explicit empty state with a read-only refresh action; disconnected placeholder dots are not fabricated. Merge diff inspection uses the first parent and labels that comparison. All commit links are limited to the displayed task history.

## Attribution

New tasks preserve the assigned profile snapshot and each task launch preserves a participant identity. Legacy task details can recover identity from the saved launch profile, never from the current renamed profile or a Git author. Missing historical identity is stated explicitly. Sessions are linked by task ID or its recorded launch ID, not by every conversation involving the same agent. Session tokens and private launch parameters are excluded from detail responses. The view does not infer that every assigned session authored a commit.

## Tests

Tests exercise direct route parsing, list/detail/back navigation, four sections, participant/session history, grouped activity, read-only commit selection, stale build separation, missing task/history states, mobile and desktop layouts, both themes, exact merge parents, historical identity preservation, immutable storage during detail reads, authenticated commit inspection, invalid commit rejection and storage failure handling. Existing explicit publication and operator-role tests remain in place.

Task detail inspection and GitHub publication retain the existing access model: authenticated operators can inspect task evidence; mutation and publishing remain administrator-controlled. Deployment is still separate.

## Recover validation after an environment change

When a captured candidate was implemented under an older session policy, SDK
preparation alone does not authorize validation under a different policy. The
**Checks & builds** section offers **Review validation environment** for a
session-policy mismatch on an open `review_ready`, `published` or `pr_open`
task. The machine-readable recovery hint distinguishes this from protected-input
changes and a policy that changed while a build was queued. An administrator reviews the candidate SHA, prepared
SDKs, selected recipes and approved project commit before approving validation
of that exact candidate. **Validate latest commit** then runs the checks.

This records a candidate-scoped validation transition. It retains the original
session policy, candidate commit, task ID and prior build evidence. Changes to
the candidate, execution or approved environment invalidate the transition;
protected candidate inputs are still checked. Agent permission prompts are a
separate issue and are not answered by this action.

The dialog includes the execution ID, candidate revision and previous approval.
Reviews are transient: reload requires another review, while saved approvals
persist and appear as **Validation environment approved for this candidate**.
The approval cannot authorize a merge result, even when it shares the candidate
SHA. Missing legacy binding identities are reported as missing, not as digests.

The approved checkout remains the authority for execution inputs. When upstream
protected inputs changed since the task base, update the task branch against
upstream before reviewing and validating. Comparing only against the old base
would permit checks under inputs different from those in the candidate.

Build-event identities now include the frozen policy digest. A submission after
upgrade may create one additional event rather than reuse an older event. Old
events and their independently identified validation runs remain available.

## Recover a saved delivery inspection

Delivery jobs retain up to 20 diagnostic transitions (stage, code and time), so
repeated failures and reply recovery do not erase every preceding stage. This
bounded history excludes prompts, reply bodies and credentials. Existing jobs
start their history from the last stage already recorded on disk.

When reconciliation can recover a matching candidate's legacy validation identity
from its old inspection prompt, it saves the structured identity once. Later
reconciliation uses that record; migration does not approve validation, execute
checks, or send another inspection prompt. Malformed and mismatched legacy
packets remain unmigrated.
Prompt-based migration recognizes the same 32 lowercase hexadecimal run IDs as
the validation runner. Previously stored structured identities are retained.

A passing build and a saved worker review do not by themselves release the
worker reservation. The gateway must accept the inspection reply. If a status
probe fails after the worker writes its reply, the chat remains interrupted and
retains its exact reply filename. Inspect the original conversation, resolve any
pending decision, and use **Recover saved reply** in Org chart → Chat. Recovery
checks that the original session is unchanged and idle, reads the saved file,
and records the accepting operator without sending another prompt. Task delivery
then reconciles the accepted report; validation does not need rerunning. Handoff
is offered only after that delivery barrier clears.

Older gateways could overwrite the filename when recording a blocked status.
Installing the fix prevents further loss but does not reconstruct old records.
After inspecting such a job's saved file, an authenticated operator can provide
its exact filename to `POST /api/organizations/recover`:

```json
{
  "chat": true,
  "inspected": true,
  "organization_id": "<organization ID>",
  "job_id": "<interrupted chat job ID>",
  "reply_name": "reply-abcdefabcdef.md"
}
```

The filename must be `reply-<12 lowercase hex digits>.md` or legacy `reply.md`,
inside that job's reply directory. Recovery never guesses among files, follows
reply symlinks, or replaces an existing attempt filename. The original session
checks still apply. Do not mark the job answered or the task complete by editing
SQLite: reply recovery and task reconciliation perform the remaining checks.
