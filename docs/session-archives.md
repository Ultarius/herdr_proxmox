# Session archives and continuation

Organization → Runs → Archived sessions lists preserved session evidence. Archive and close saves the evidence before closing a verified idle pane. Archive storage failure or unavailable terminal capture leaves an existing pane open.

Archives contain a bounded terminal snapshot (not a complete provider transcript), the latest saved dashboard messages/reports, the launch identity, task and checkout references, and available Git HEAD/branch/status evidence. They are stored atomically in SQLite, include a content SHA-256, and can be downloaded as JSON. Archive access and continuation use the admin session endpoint.

When the original agent is absent, cleanup also checks every workspace pane inventory. Only absence of both the agent and recorded pane allows reconciliation as already closed. Missing pane identity or an existing pane remains an inspection blocker. Already-lost terminal history is labeled unavailable; remaining saved records can still be archived.

Continue with saved context creates a fresh session using the current profile in the retained checkout. Historical evidence is supplied as context, explicitly not instructions to replay; the agent waits for a new task. Idempotency prevents repeated continuation requests from creating duplicate launches. Another active binding blocks continuation.

Before closure the gateway probes the live agent for a resume reference, because Herdr clears its registration once the pane returns to an idle shell. The archive records `unavailable`, `reported`, `verified` or a failed probe. Only a `verified` capability — a reference with structured adapter arguments that were validated before storage — enables **Resume original conversation**, which relaunches the profile in the retained checkout with those arguments. A `reported` reference (free-form command text, or a reference without structured arguments) is recorded for operators but never executed; those archives offer Start with saved context instead. Resume requests are idempotent by request ID, refuse while another binding is active, and fail closed when the archive lacks a verified reference.

After a gateway restart the store annotates each bound session's liveness (`present` or `missing`) without changing its state and without replaying any prompt; queued or running jobs still become `uncertain`. Restored sessions recover their binding by alias and identity, while interrupted deliveries remain inspection-required.

Archives contain operational conversation evidence and should be treated like other administrator-accessible dashboard records. Existing sessions must be archived before closure to retain terminal evidence.
