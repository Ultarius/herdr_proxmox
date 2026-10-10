# Event-driven execution

The gateway uses events to wake existing durable workflows. Stored tasks, jobs,
exact-commit build artifacts, receipts and fresh runtime identity checks remain
its authority. A runtime `done` event does not complete or validate a task.

## Committed changes and independent consumers

Task, job and policy writes insert a small notification into an SQLite outbox
inside the same transaction. Rollbacks produce no notification. A background
pump publishes committed rows and acknowledges them afterwards. A crash between
publish and acknowledgement can deliver a duplicate, so notifications are hints
to reread state, never commands to repeat an effect. Delivered history is pruned;
undelivered rows remain available for recovery.

Every worker has an independent bounded mailbox. Reading or clearing one cannot
consume another worker's wakeup. Repeated record keys coalesce. Overflow requests
full reconciliation rather than silently losing an update. Task-only batches
scope candidate validation, completion, acceptance and outcome reconciliation to
the affected tasks. Runtime, policy and job changes can require broader scans.

A 10-second safety scan still detects file receipts and changes made outside the
notification path. Housekeeping runs on elapsed time every 60 seconds rather
than every sixth wakeup. Acceptance deadlines can wake the scheduler earlier.

Scoping is bounded, never lossy. A batch naming more than 256 tasks widens to a
full scan instead of dropping the tail, and a scoped pass reconciles every task
it was woken for. Full scans are page-limited for predictable cost, so each pass
resumes where the last stopped rather than re-reading the same first page; a
task that keeps matching is therefore never starved behind it.

## Runtime lifecycle subscription

On Unix hosts the gateway opens the Herdr newline-JSON socket and subscribes to
pane, workspace and worktree lifecycle events. It waits for
`subscription_started` before reading `api snapshot`. Agent-status subscriptions
require a `pane_id`: the first snapshot discovers those IDs, the gateway reopens
the stream with the corresponding subscriptions, waits for acknowledgement, and
reads another snapshot. Pane inventory changes repeat this sequence.

Event payloads are not applied to cached state. Events arriving during a snapshot
force another read. Disconnects and `events_lost` require reconnect and snapshot
recovery. Once a subscription has worked, terminal mutations are gated while
runtime state is being reconciled. Existing per-operation identity and readiness
checks still run before effects.

Configuration:

- `HERDR_RUNTIME_EVENTS=0` explicitly selects periodic checks.
- `HERDR_SOCKET_PATH` overrides the socket location.
- Otherwise the path uses `XDG_CONFIG_HOME` / `~/.config`, and `HERDR_SESSION`.
- Windows currently uses periodic checks; named-pipe streaming is not implemented.
- A runtime that cannot establish a subscription uses periodic checks. A runtime
  that disconnects after establishing one waits for verified recovery.
- The gate is bounded. Recovery normally completes in seconds; if it has not
  recovered within five minutes the gateway reports `degraded` and stops
  blocking, because refusing every launch and prompt indefinitely is a worse
  failure than acting under per-command identity checks. Reads are never gated.

The notification response includes runtime state, reason and last successful
snapshot time. Protocol-shaped regression tests cover acknowledgement order,
pane-scoped subscriptions, inventory changes and lost-event recovery. An actual
LXC reconnect/restart check is still required before claiming live compatibility.
The wire contract was checked against the upstream Herdr 0.9.3 schema.

## Dashboard notification feed

Authenticated `GET /api/notifications?after=<revision>&epoch=<epoch>` long-polls
for up to 20 seconds. Only 32 concurrent readers are admitted. Its bounded history
contains record identifiers, not prompts or terminal output. A changed process
epoch, invalid future cursor or missing history causes a reset and fresh fetch.

The dashboard follows the cursor and refreshes task and group views when changes
arrive. Session changes and disposal stop scheduling reads and ignore old replies.
Periodic page refresh remains a compatibility and recovery backstop. This is an
HTTP long-poll feed, not a WebSocket or durable browser event log.

## Acceptance and admission fixes

Acceptance requests freeze their packet and deterministic identity before input
is submitted. An ambiguous response retries that identity instead of creating a
new request from changed task text. Answered, invalid, failed and missing review
jobs are consumed once. A changed candidate archives the prior verdict and
requires a review for the new SHA. Stalled requests move to attention and are not
automatically resubmitted. The acceptance endpoint is exposed by the HTTP router.

Organization launch reservations enforce shared active-session and daily-session
limits. Task, group and general launch admission share a capacity guard. Meeting
admission is organization-scoped and serialized: queued meetings wait in order,
but do not occupy meeting capacity until admitted. Completion releases the
meeting slot for the next waiting meeting. Paused organizations defer new work.

## Verification and next steps

Tests exercise independent wakeups, overflow, rollback and duplicate outbox
replay, cursor resets, runtime recovery, exact-candidate review idempotency,
meeting capacity and authenticated dashboard refresh.

Next, probe this implementation on the deployed LXC without launching work:
verify the socket path and snapshot schema, observe an idle test session changing
state, and verify recovery with an isolated runtime restart. Add latency and
queue-age metrics, then extend structured permission handling and verified native
resume. Publishing, merging, deployment and permission decisions retain their
separate controls.
