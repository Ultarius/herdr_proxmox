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

Provider-native conversation resume is not available. Herdr session identity is not
proof of a provider conversation ID. Continuing an archived session supplies saved
context to a fresh session; it never claims to restore provider state.

## Automatic follow-up from group reviews

Group discussions produce draft proposals by default. An organization can opt into
bounded automatic follow-up, per organization and off by default:

- `auto_queue_proposals` — qualifying proposals are created and queued without a
  separate operator click;
- `max_per_meeting` — how many proposals from one discussion may start;
- `max_open_per_agent` — queued plus active work allowed per assignee;
- `max_follow_up_depth` — how many follow-up generations are allowed;
- `daily_cap` — automatically created tasks per organization per 24 hours;
- `paused` — kill switch; queued work stays queued and nothing new starts.

A proposal qualifies only when all of the following hold:

- its discussion is finalized and belongs to a task in review, completion or
  merged state (evidence-backed review, not mid-implementation);
- its assignee is an individual, non-archived worktree agent for the same
  repository;
- the follow-up depth, per-meeting, per-agent and daily limits are not exceeded;
- the proposal did not set `needs_review=true`, which always keeps it a draft.

Qualifying proposals go through the same task creation, assignment queue and
verified handoff path as manual work. Failures and uncertainty (repository
unavailable, invalid assignee, capacity) leave the proposal as a draft and record
the reason on the task. Enabling the policy starts a fresh evaluation window:
discussions created before it are recorded as evaluated and are never queued
retroactively. Each evaluation is recorded once per meeting.

Delivery remains operator-controlled: no automatic publication, merge, base
update or deployment. Every automatically created task records its source
meeting, proposal, depth and the policy snapshot in force, appears in activity
and can have its queued assignment cancelled.

When the policy is enabled, the discussion instruction states it explicitly and
asks agents to propose sparingly, with acceptance criteria and checks.

A worker can also report an optional `follow_up` object in its completion
receipt. It is recorded once per commit as a draft task with recorded depth for
operator review; it never starts automatically.



A retry reconciles an already-reserved organization launch by task identity, without
sending a second prompt or regenerating its request fingerprint. Pending launches count
against the task execution cap. Closing an old task session does not prevent automatic
validation of its unchanged, receipt-verified candidate; validation cannot recapture new
commits from a finished session.

The cap covers task executions launched by the contribution controller. General
organization and group sessions are outside this task cap. Provider-native state is not
restored, and no task assignment grants publication or deployment permission.

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
