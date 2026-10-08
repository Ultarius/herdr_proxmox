# Session archives and continuation

Organization → Runs → Archived sessions lists preserved session evidence. Archive and close saves the evidence before closing a verified idle pane. Archive storage failure or unavailable terminal capture leaves an existing pane open.

Archives contain a bounded terminal snapshot (not a complete provider transcript), the latest saved dashboard messages/reports, the launch identity, task and checkout references, and available Git HEAD/branch/status evidence. They are stored atomically in SQLite, include a content SHA-256, and can be downloaded as JSON. Archive access and continuation use the admin session endpoint.

When the original agent is absent, cleanup also checks every workspace pane inventory. Only absence of both the agent and recorded pane allows reconciliation as already closed. Missing pane identity or an existing pane remains an inspection blocker. Already-lost terminal history is labeled unavailable; remaining saved records can still be archived.

Continue with saved context creates a fresh session using the current profile in the retained checkout. Historical evidence is supplied as context, explicitly not instructions to replay; the agent waits for a new task. Idempotency prevents repeated continuation requests from creating duplicate launches. Another active binding blocks continuation.

This does not restore a provider-native conversation. Herdr session identity is not proof of a provider conversation ID. Native resume requires a verified provider conversation identifier and a runtime-specific resume adapter. Closing does not remove repository files or branches.

Archives contain operational conversation evidence and should be treated like other administrator-accessible dashboard records. Existing sessions must be archived before closure to retain terminal evidence.
