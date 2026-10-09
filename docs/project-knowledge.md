# Project knowledge

Open **Project knowledge** in the dashboard sidebar, or use the knowledge link in a group. Select an organization and optionally a repository. Search by title, body or knowledge ID; filter by kind and review state. Expand a record to inspect its source commit, task/group links, agent participation, check evidence and review history.

Administrators can add findings, decisions, questions, guidance and outcomes. New content is reported, not verified. Review requires a reason and preserves the original content. Supersession requires an active replacement in the same organization and repository scope. Superseded records remain searchable but are excluded from discussion context.

## Automatic capture

Task candidate, completion and publication state changes capture an immutable outcome in the task database transaction. A separate exact-commit check flag is true only when the durable build record is complete, targets that commit, and verifies required checks. An operator review never turns that flag on.

Identity-validated completion receipts may include an optional `knowledge` array of up to ten objects, each with `kind`, `title` and `body`. Allowed kinds are `outcome`, `finding`, `decision`, `question`, and `guidance`. Questions start as hypotheses; other agent claims start as reported. Receipt tokens and raw receipts are not copied into knowledge.

Completed discussion artifacts and delegation reports are reconciled by the existing background poller. A knowledge-capture failure does not change a completed meeting into a failed meeting; future polling retries capture. Discussion transcripts can include the same optional knowledge array. Repeated reconciliation deduplicates records.

## Use in meetings

A discussion receives a bounded, cited selection from its organization-wide records and participating repository scopes. Retrieval ranks topic overlap and review state from the latest 200 eligible records; at most twelve records are included, with bounded excerpts. Discovery also receives a smaller knowledge selection within its existing evidence budget. Agents are instructed to treat this as historical data, challenge stale claims, and check current code and tests.

This does not enable task creation or launching policies. Meeting follow-up proposals still use the existing draft review workflow.

## Persistence and limits

Knowledge and review audit entries are stored in `tasks.sqlite3`; include this database in backups. Startup backfills the latest 200 task records and reconciliation considers the latest 200 eligible completed discussion/delegation jobs. Existing session archives are preserved separately. This is structured project memory, not a guarantee of complete historical transcript recovery or native provider conversation resume.

Text is bounded and common credential patterns are redacted. Redaction cannot identify every possible secret: do not put credentials in knowledge records. Existing dashboard authentication and administrator permissions protect mutations; organization/repository scope is validated server-side.
