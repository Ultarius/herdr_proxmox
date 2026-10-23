# Automatic task capture, validation and delivery

## What

Tasks have an administrator-controlled, opt-in automatic capture and validation policy. It is disabled by default and uses the same build executor as manual validation. Gateway builds stop on gateway restart; the optional durable build service runs builds independently. The Tasks page shows a collapsible Git timeline with captured commit-parent edges, the recorded base, current candidate and SHA-specific build colors. Previous candidate builds remain available separately and never count as evidence for a newer candidate.

Organization → Automation also offers defaults for automatic validation and
publication of **new** tasks, including tasks created from discussion proposals.
Per-task settings can override those defaults. Existing tasks are not changed
retroactively. Validation defaults apply with either executor. If the durable service is unavailable,
a new task inherits validation but leaves automatic publication off; the organization's
saved defaults are preserved.

Enabling publication requires configured GitHub publishing. Each inheriting task
preflights its upstream and destination and records authorization for that exact
destination and task branch, with the originating policy's administrator and
timestamp. Invalid destinations or unavailable credentials must be resolved, or
the default disabled, before creating an automatically publishing task.

## Why

Agent idle status is not completion evidence. A worker can pause for permission, fail, or leave uncommitted work. Likewise, a successful build for an older SHA cannot validate a later merge commit. Automatic publication, merging and deployment are separate decisions.

## How

1. Create and launch a task. Its prompt asks the agent to commit changes and write a completion receipt last, inside its ignored local guidance directory.
2. An administrator enables automatic capture and/or validation. Validation uses the configured executor, including the gateway runner when the durable build service is not installed. Interrupted or failed builds require an explicit retry.
3. Every ten seconds the task reconciler checks the original live session identity and idle status, outstanding agent jobs, the assigned branch, and the receipt. The receipt must match the current HEAD, launch ID and fresh session token. Candidate capture also requires a clean checkout with no Git operation in progress.
4. Capture records the exact candidate SHA and up to twelve commits with real parent edges. The reconciler queues one build per task and SHA using the existing durable build identity. An old completion file is invalid after a session restart because the token changes. Restart provides the new receipt protocol without replaying the prior task.
5. Build results are attached to their SHA. Failed builds are not retried automatically; inspect and use Validate latest commit to retry explicitly. A new committed result needs a matching new receipt. Waiting reasons are persisted and visible.
6. With validation enabled, the agent remains reserved through exact-commit verification and its inspection of the results. With publication also enabled, delivery publishes the exact validated branch and opens a draft PR under its recorded authorization before that inspection. GitHub merge, base update and deployment remain separate actions. Existing manually captured candidates can be queued under the enabled policy without requiring an agent to recreate their receipt.

Pausing the organization, disabling delivery defaults and editing unrelated
limits remain available if the durable executor goes offline. Newly enabling
publication still requires the durable service; validation can use the gateway. The organization pause controls admission
and queueing; it does not cancel already-running task delivery.

For tasks created by an older build with incomplete inherited delivery settings,
inspect their destination and resave the task's Automation settings to establish
the validation reservation and publication authorization. If delivery already
needs attention, use the explicit delivery retry after fixing its cause.

Delivery inspections bind to a validation run as well as the candidate SHA.
When a new run passes for the same candidate under the current approved policy,
a verified, settled inspection of the old failed run is retained in history and
replaced with an inspection of the passing evidence. The agent must acknowledge
the new evidence before handoff; the build alone never releases its session.
This does not retry a build, reset publication errors or grant publication.

If the previous chat is still running or its delivery is uncertain, Herdr keeps
the handoff blocked and states which failed and passing runs are involved.
An old permission error is historical evidence, not a current status probe.
Queued/running chats and chats waiting for input remain `waiting_agent`; resolve
their console prompt if necessary and wait. Retry is disabled while that chat is
active, and the gateway checks the durable job again before accepting a retry.
An uncertain or invalid reply becomes `attention`: inspect or recover the saved
reply in Org chart → Chat, then choose **Retry verification and delivery
inspection** in the task Overview. If the job is unavailable, inspect the original
session and request a fresh inspection with that action. An already passing build
is reused. A legacy inspection without a recorded validation-run identity also
needs explicit recovery.

A crash after prompt preparation is recovered from the atomic organization
request receipt: an existing chat is read by its recorded ID; a request that never
committed can be replaced with the passing evidence before any prompt is sent.
The old error and wait identities remain in bounded history. Cancelled and legacy
unverified-complete failure inspections can also be superseded by a new verified
pass; a verified successful inspection is not treated as a failed one.

The timeline is a bounded view of actual Git history, not a pipeline diagram. Green requires a completed build with required checks verified. Red indicates build failure. Other nodes have no verified pass. Missing edges outside the captured history are not invented. Legacy candidates need recapture to load parent data. Tree equality with the last fetched configured base is informational: it does not prove publication, deployment or independent validation.

## Completion receipt

The launch prompt supplies the exact path, run ID and token. The worker writes JSON with outcome set to complete, commit set to the full committed HEAD, run_id, token and tests as an array of actual check results. Worker-reported tests are separate from runner evidence; they do not grant a verified pass.

## Verification

Regression coverage exercises matching completion, stale session tokens, wrong commits, uncommitted work, service prerequisites, build deduplication, no automatic failure retries, real merge graph rendering, historical evidence separation, mobile scrolling and light/dark themes. Deployment and live LXC validation must be performed after installing this batch.
