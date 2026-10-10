# Agent execution lifecycle, handoff and queued assignments

A task launch binds one agent identity to one execution. An execution occupies its agent
while its launch job is in any state other than `finished` or `released`, including
pending and unresolved-error reservations. A `finished` execution passed the handoff
checks and handed its slot back. `released` is the advanced manual recovery path and
also frees the slot.

## Handoff on assignment

Starting a task whose agent is busy verifies the previous execution before any new work
begins. Availability shown by the page is provisional; launching rechecks the evidence
and live session. The verifier requires all of the following:

- the previous task has a captured candidate (review, publication or PR stages), or a recorded completion;
- its recorded checkout is on the task branch, clean, and free of unfinished Git
  operations;
- a completion receipt matches this task, execution run, session token and the
  checked-out HEAD and recorded candidate (a `completed` task may rely on its exact recorded completion instead);
- the live session is idle and still the recorded identity.

Only then does the gateway archive the previous session, close its pane and mark the
execution `finished`, and start the new task. Failures before closure leave the previous
session intact. If closure succeeded but the next launch failed, the previous archive
and checkout remain available and the task records the handoff stage for retry. The
handoff is idempotent by request ID, so a restart between stages reconciles by retrying
the same request.

A task whose agent is busy can also be queued instead of started.

## Queued assignments

`Queue for this agent` records a durable assignment in `tasks.sqlite3` with a position.
A background scheduler starts queued tasks in order whenever their assigned agent is
free, performing the same verified handoff when the previous task is finished. A
separate global cap (`MAX_ACTIVE_EXECUTIONS`) bounds active task executions and
pending/uncertain task launch reservations, across manual and scheduled starts; it is
independent of the single-run build queue.

Queue entries and task state are committed together in SQLite. They survive restart, can
be reordered and cancelled from the task page, and keep their position while the agent
is still working. A queued task that cannot start records the blocking reason on the
task without losing its place. Later tasks for the same agent do not overtake it; other
agents can still run. Cancelling a queued assignment remains possible even when its
repository is unavailable. The starting SHA remains the commit captured at task
creation.

## No changes needed

A worker may report `outcome=no_changes` in its completion receipt only when the
checkout is clean and unchanged at the recorded base commit, with no unfinished Git
operation, and a non-empty reason is supplied. The task then completes as `Done: no
changes needed` with `validation: not_applicable`; nothing is captured, validated or
published. The session is finished automatically when possible.

## Follow-up context

Tasks created from group discussion proposals record their source. Their launch prompt
includes the predecessor task title and recorded completion so the worker can judge
relevance without replaying history. This is bounded context, not an instruction to
repeat the predecessor's work.

## Native resume

Before closing a session the gateway probes the live agent for a resume reference
and records `unavailable`, `reported`, `verified` or a failed probe on the archive.
Only a `verified` capability — a reference with structured adapter arguments
validated before storage — enables the portal's **Resume original conversation**
action (Organization → Archived sessions), which relaunches the profile in the
retained checkout with those arguments. A `reported` reference is recorded for
operators but never executed; those archives offer **Continue with saved context**
instead. Restoration is not completed work: the resumed session waits for
instructions. Herdr session identity alone is never treated as proof of a provider
conversation ID.

## Handover, consultations and instances

A finished session can hand its task to another agent without losing context.
The outgoing agent may record a bounded `handover` note in its completion
receipt (state, decisions, open questions, next step); it is captured as
reported knowledge and included in the handover packet. Handover requires an
archived source session, a same-repository target with no active execution, and
produces a packet of task evidence, the handover note and relevant knowledge.
The target continues in the same checkout with a fresh session and receipt
token, and the lineage is recorded on both sides.

Consultations ask a live individual agent a bounded question from a task. The
answer is stored as reported knowledge on the task and never counts as
validation evidence; the consultant must be idle and has no task authority.

Template instances run a task as an ephemeral parallel copy of a profile's
persona, model and permissions: their own profile record, session and worktree,
bounded per template, and closed automatically when the task reaches a terminal
state. They appear as executions, not as new team members, and never join
rosters, queues or discussions.

## Decisions, delivery certainty and wake

A blocked agent is a first-class condition, not a generic failure. Availability
reports **needs your decision** when the recorded delivery stage is `blocked` or
the live agent reports `blocked`, including a bounded preview of the visible
screen. Operator actions (Send Enter, Send Escape, Cycle permission mode) are
ordinary audited input jobs: ownership is revalidated immediately before keys are
sent and the operator name is recorded. Keys are labeled by their effect because
Enter does not universally mean approval.

Delivery certainty is preserved from the CLI's structured errors:

- `none` - the operation was refused before any input (for example
  `agent_blocked`); the prompt stays pending and may be retried safely;
- `unknown` - a timeout or stalled submission may have delivered input; inspect
  before retrying and never replay automatically;
- `sent` - the prompt was submitted; the recorded completion baseline (sequence
  plus server/session scope) lets later reconciliation decide whether a completed
  turn is newer than this dispatch.

Read-only Herdr commands retry once on timeout; prompt-like commands never retry.
After a restart, bound sessions are annotated present or missing without state
changes, and interrupted queued or running jobs remain `uncertain`.

Job state changes wake reconciliation immediately instead of waiting for the next
ten-second cycle; the periodic snapshot remains authoritative and the timeout is
only a backstop. Events never decide work - locks, receipts and idempotent job
transitions still do.



## Startup sessions and availability

Hiring or turning on an agent creates a startup session: the persona is
delivered and the agent waits for work. An open conversation is not the same
fact as executing work, so every task start classifies the agent first:

- **ready** — an idle startup session, or a session whose chats and meetings
  have all completed; starting the task archives that conversation and opens a
  dedicated task execution;
- **occupied** — a chat, discussion or delegation is queued or running; the
  task waits and starts when the reservation ends;
- **working** — another task is executing; queued work waits behind it;
- **handoff** — the previous task has recorded completion evidence; its checkout
  and live session are verified before archival and the next task starts;
- **attention** — permission prompts, uncertain identity, a dirty checkout or
  unfinished delegation work; inspect the session before new work.

Rotation of a startup session verifies the live identity and idle state,
refuses when the checkout has uncommitted changes or an unfinished Git
operation, preserves bounded terminal history and saved messages before
closing the pane, and records durable `closing`/`closed` stages on the task so a retry or restart
reuses the same closure request. Delegations and pending interactions always
require inspection; completed chats and meetings keep their artifacts and
archived transcripts, so they do not block rotation. The manual close/restart
path remains available for every other case.

## Automatic follow-up from group reviews

Group discussions produce draft proposals by default. Follow-up can be enabled
per organization and per group:

- `auto_queue_proposals` (organization, off by default) — qualifying proposals
  from every group are created and queued without a separate operator click;
- `create_tasks` (group, off by default) — this group may create and queue
  qualifying proposals from its own discussions on its own;
- `max_per_meeting` — how many proposals from one discussion may start;
- `max_open_per_agent` — queued plus active work allowed per assignee;
- `max_follow_up_depth` — how many follow-up generations are allowed;
- `daily_cap` — automatically created tasks per organization per 24 hours;
- `paused` (organization) — kill switch; queued work stays queued and nothing
  new starts automatically.

A meeting is evaluated when its organization enables automatic follow-up or its
own group opted into creating tasks. A proposal qualifies only when all of the
following hold:

- its discussion is finalized and belongs to a task in review, completion or
  merged or closed state (terminal outcomes may need corrective follow-ups);
- its assignee is an individual, non-archived worktree agent for the same
  repository;
- the follow-up depth, per-meeting, per-agent and daily limits are not exceeded;
- the proposal did not set `needs_review=true`, which always keeps it a draft.

Qualifying proposals go through the same task creation, assignment queue and
verified handoff path as manual work. Failures and uncertainty (repository
unavailable, invalid assignee, capacity) leave the proposal as a draft and record
the reason on the task. Enabling the policy starts a fresh evaluation window:
discussions created before it are recorded as evaluated and are never queued
retroactively. Each evaluation is recorded once per meeting. The group enablement timestamp
(`create_tasks_since`) changes only when task creation is enabled; ordinary
group edits preserve it. Disabling and re-enabling starts a new window.

Delivery remains operator-controlled: no automatic publication, merge, base
update or deployment. Every automatically created task records its source
meeting, proposal, depth and the policy snapshot in force, appears in activity
and can have its queued assignment cancelled.

When the policy is enabled, the discussion instruction states it explicitly and
asks agents to propose sparingly, with acceptance criteria and checks.

A worker can also report an optional `follow_up` object in its completion
receipt. It is recorded once per commit as a draft task with recorded depth for
operator review; it never starts automatically.

## Outcome notices to the proposing group

A task created from a group proposal or a discovery meeting records its origin in
`source.group_id`. When that task reaches a terminal state (`completed`, `merged`
or `closed`), the gateway schedules one outcome review with the originating group
and records `outcome_notice` on the task:

- the notice is one discussion per task, created with a stable request ID, so a
  restart or a lost response cannot create a second meeting. The full request is
  saved before dispatch and reused unchanged, including its evidence packet;
- the organization pause switch also defers new outcome meetings; paused
  organizations are excluded before selecting the bounded batch. Failed
  dispatches rotate behind tasks that have not been attempted;
- the group receives a bounded packet: task, recorded outcome and reason,
  candidate or merged commit, whether required checks were verified for that
  exact commit, worker-reported checks, change summary and relevant project
  knowledge;
- the discussion instruction asks the group to confirm the outcome satisfies the
  original proposal and to propose only necessary follow-ups under the same
  proposal contract; an empty proposal array is valid;
- follow-ups from an outcome review use the ordinary follow-up policy: drafts by
  default, queued when the organization or the group enables task creation;
- each group has `notify_outcomes` (default on), set in the group form. When it
  is off, the task is recorded as evaluated, and enabling it later notifies only
  work that finishes afterwards;
- a missing or removed group is recorded once as unavailable instead of being
  retried forever; transient failures retry on the next poll and are recorded as
  `outcome_error`, shown in the task overview. A scheduled notice means the
  meeting is queued, not that the group has already received or completed it.

Tasks without a group origin (manual creation, receipt `follow_up`) are never
notified.

## Acceptance verification

Checks prove the commit builds; they do not prove it satisfies the task's
acceptance criteria. A task can therefore name an acceptance reviewer in
**Automation settings → Acceptance review**:

- the reviewer must be an individual, non-ephemeral worktree agent in the same
  repository and never the agent doing the work;
- with automatic review on, the poller dispatches one bounded review per verified
  candidate once required checks are verified for that exact commit (or the
  worker proved no changes were required). A busy reviewer leaves the review
  waiting with the reason recorded; it is never dropped;
- the review prompt asks for strict JSON saved to the reply file:
  `{"verdict":"satisfied|not_satisfied|uncertain","reason":"...","evidence":["..."]}`;
- the verdict is recorded on the task, captured as reported knowledge and shown
  in the overview. It is a reported review, not validation evidence;
- `not_satisfied`, `uncertain`, `inconclusive` and `attention` (stalled past six
  hours) join the review evidence signature, so a configured automatic review
  group convenes with the verdict in its context, and the task's next step says
  the acceptance review needs attention.

A retry reconciles an already-reserved organization launch by task identity, without
sending a second prompt or regenerating its request fingerprint. Pending launches count
against the task execution cap. Closing an old task session does not prevent automatic
validation of its unchanged, receipt-verified candidate; validation cannot recapture new
commits from a finished session.

The organization can additionally set one shared session budget over task
executions, meetings, discovery and helpers: `max_active_sessions` (concurrent
launch bindings), `max_active_meetings` (concurrent discussions) and
`daily_session_cap` (launch starts per 24 hours). 0 keeps a limit off. Meetings
wait in `waiting_for_members` and tasks report a full-budget reason instead of
starting sessions past the budget; the legacy task execution cap still applies.
Provider-native state is not restored, and no task assignment grants publication
or deployment permission.

## Reclaiming finished sessions

A finished task closes its template instance, but a general organization session
that reaches `finished` keeps its agent process and pane resident until an
operator closes it. With enough finished work this holds the container's memory
even though no task is active.

Setting `reap_finished_sessions` (off by default) closes them automatically
during housekeeping. Only executions already recorded as `finished` are eligible,
so the task, its candidate and its checkout evidence were verified before; an
idle pane alone never authorizes closure. Busy agents, queued or running work,
absent ownership and failed inspections are all left for an operator.

Reclaimed sessions are archived, not discarded: terminal output is preserved,
native resume is probed, and pane ownership is rechecked immediately before
closing. A session with a verified reference can still be resumed from its
archive; one without it falls back to starting with saved context. Inspect any
session before enabling this if its transcript matters more than its memory.

`reap_orphaned_panes` (also off by default) covers a different case. When the
Herdr server restarts, pane processes do not survive and Herdr restores the
layout, so panes can come back as plain shells that no agent holds and that no
run could otherwise close. A pane is reclaimed only when all of the following
hold: the run's recorded alias has no live agent, no live agent claims that
pane, the pane reports no agent state, and the pane's directory resolves to the
checkout the run was assigned. Anything ambiguous is left for an operator,
because closing the wrong pane destroys real work. Reclaiming a pane archives
the run first and records why terminal history is unavailable.

## Agent integrations

Herdr integrations let an agent report working, blocked and idle state and hand
back a native resume reference. Without them an agent still runs, but it never
reports a resume reference, so archived sessions can only be continued with
saved context. The installer provisions an integration for every CLI it
installs, and the dashboard's CLI page reads `herdr integration status` to show
whether each agent is connected, outdated or absent. Integration names are not
always CLI names: Antigravity is `antigravity-cli`.

Herdr reports four states, and only `current` counts as connected. `outdated`
means the installed version is below the expected one, and `needs repair` means
a version at or above the expected one that Herdr still considers broken; both
are installed but neither reports reliably, so the dashboard offers an update
rather than reporting health.

Adding an agent and launching one both require the runtime's CLI to be
installed and its Herdr integration to be current. The precondition is checked
because an agent that cannot report its state or hand back a resume reference
still starts and works, so the misconfiguration only surfaces later as an
unrecoverable session. Runtimes outside the supported set are not judged,
because the gateway cannot verify their readiness.

## Legacy session closure

Some Herdr versions omit `agent_session`. Their task completion and conversation
identity are separate: marking a task done does not close its pane. For closure,
the gateway can verify the exact recorded alias, runtime, idle status, unique pane
ownership, workspace and managed checkout instead. It reads authoritative agent,
workspace and pane inventories and repeats these checks after terminal archival,
immediately before closing. Changed or ambiguous ownership remains a blocker.
The run records `session_close_identity: pane_checkout`; no conversation ID is
invented or adopted. Runtimes with a recorded session ID still require it to match.

## Product discovery

For meetings about product gaps that do not need a host task, see
[Product discovery](product-discovery.md). Its opt-in schedule produces reviewed
drafts; approved tasks reuse the queue and handoff described above.


## Autonomous session preparation and delegation resolution

Task-detail availability is a display snapshot, cached for at most five seconds
and invalidated when the saved job fingerprint changes. The response includes
`checked_at`, `cached` and `max_age_seconds`. Starting work never relies on that
cache: identity, readiness, reservations and checkout checks run again.

An administrator can resolve a legacy delegation in Organization → Runs. First
record its completion report, then choose **Accept completion and free agent**,
or choose **Cancel after inspection** for delivered/interrupted work. A reason
and inspection acknowledgement are required. Resolution is recorded with actor
and time, and repeated requests are idempotent. `completed` and `cancelled`
delegations release their work reservations. Acceptance is an operator decision,
not independent validation; cancellation does not interrupt a live agent. Fresh
readiness checks still prevent a task or meeting from interrupting active work.

Group discussions prepare offline members on demand. A waiting meeting holds no
participant locks or worker thread between poll cycles. Active work takes
priority; ready waiting meetings are considered before new queued tasks, overlapping meetings preserve their order, and absent members are not
launched until existing active members are eligible. Each preparation launch has
a durable deterministic identity, retains the selected runtime/model/persona,
and uses the normal managed checkout setup. Task sessions can participate only
after their completion evidence, checkout and live readiness have been verified.

The gateway poll loop rechecks `waiting_for_members` meetings in rotating batches
of twenty every ten seconds, so busy early meetings do not hide later ones.
After 24 hours waiting becomes `needs_attention`. Group history shows preparation
progress; administrators can cancel a waiting meeting or retry inspected failed
preparation (at most three retries). Once a discussion prompt has been submitted,
it can only recover its saved artifact and transcript: it is never resent.
Legacy interrupted discussions without a verified preparation marker likewise
use artifact recovery. Gateway restart keeps waiting meetings. A known unsubmitted preparation can be
retried after inspection; uncertain launches or submitted discussions use
inspection and artifact recovery rather than automatic replay.

Product discovery also prepares an absent facilitator when it needs an evidence
checkout. This does not grant implementation, publication or deployment rights.

A startup conversation in a non-Git directory does not qualify for automatic
rotation. The task page offers **Archive inspected session and start**, requiring
explicit confirmation pinned to the exact run. Bounded terminal history is saved,
old files remain intact, and implementation starts in its separate Git worktree.
Invalid Git metadata is an inspection error, never a reason to skip Git checks.

## Next steps for reliable autonomous execution

The feedback loop is now planning -> draft/queued task -> implementation ->
exact-commit validation -> outcome discussion -> bounded follow-up. Publication
and deployment still require their separate explicit policies.

Prioritize the following before broadening automatic execution:

1. **Delivery recovery:** preserve structured runtime errors, distinguish input
   refused before submission from ambiguous submission, and reconcile receipts
   after restart. Never replay a possibly delivered prompt blindly.
2. **Permission interactions:** show approval/question states with a current
   visible-screen preview and audited operator controls. Never auto-approve.
3. **Native session resume:** record verified conversation references, runtime,
   endpoint and checkout; reconcile restored bindings without treating restored
   sessions as proof of task completion. Keep archived-context fallback.
4. **Events with snapshot recovery:** events should invalidate cached readiness
   and wake durable queues. Reconnect/resnapshot on lost events, coalesce bursts,
   and retain periodic reconciliation.
5. **Shared execution budgets:** include task, group, discovery and temporary
   helper jobs in organization/global capacity and cost controls. A task-only
   execution cap does not bound the full team.
6. **Progress and acceptance:** distinguish lifecycle activity from meaningful
   task progress; require bounded retries and explicit escalation. Evaluate
   proposal acceptance criteria against exact artifacts, not prose claims.
7. **Recovery tests:** inject crashes after reservation, prompt submission,
   receipt creation, build completion and meeting creation. Demonstrate no
   duplicate work, no lost source links and no false verified status.

Measure queue wait time, unresolved delivery age, permission wait time, validated
outcome rate and follow-up depth/cost. Use those measurements to expand autonomy
rather than enabling publication or unlimited self-generated tasks at once.

## Event-driven scheduling

The gateway now wakes durable workflows through independent notification
mailboxes, transactional task/job outboxes and runtime lifecycle subscriptions.
Task and group pages follow an authenticated notification feed. Periodic
reconciliation, verified evidence and explicit permission decisions remain
required. See [Event-driven execution](event-driven-execution.md) for recovery,
capacity, compatibility and deployment verification details.
