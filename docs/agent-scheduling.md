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

## Restart recovery and validation

A retry reconciles an already-reserved organization launch by task identity, without
sending a second prompt or regenerating its request fingerprint. Pending launches count
against the task execution cap. Closing an old task session does not prevent automatic
validation of its unchanged, receipt-verified candidate; validation cannot recapture new
commits from a finished session.

The cap covers task executions launched by the contribution controller. General
organization and group sessions are outside this task cap. Provider-native state is not
restored, and no task assignment grants publication or deployment permission.
