# Storage review decisions

Audit note for the 2026-10-22 managed-storage review. This records design decisions,
not live qualification results. Operator instructions and the current contract
are in [Managed build storage](managed-build-storage.md); runtime policy is in
[CI](ci.md). Implementation: [ownership/reclamation](../web/gateway/workspace_storage.py),
[execution setup](../web/gateway/execution_storage.py),
[retention service](../web/gateway/storage_service.py).

Verified against the implementation and addressed without changing the compact
default or adding ecosystem branches to shared orchestration.

| Finding | Assessment and change |
| --- | --- |
| Tracked files outside Projects / below nested repositories | Confirmed. Inspect all enclosing Git boundaries (a nested index can hide an outer tracked file), reject repositories within output, check original and quarantined paths. Explicit private caches may contain dependency Git repositories. |
| Python 3.11 `Path.is_junction` failure | Confirmed. Use no-follow stat/reparse checks; the full Linux suite runs on the declared minimum Python and the pinned primary Python. |
| Terminal job state before harvest | Confirmed. Harvest and close owners first; pending finalization stays active and is retried. Leases cover broker transfers. |
| Failed preparation leaks active owners | Confirmed. Validate layouts before allocation; close on failure and record a durable pre-agent failure for reconciliation. Uncertain sessions remain protected intentionally. |
| Count-only cleanup confirmation | Confirmed. Preview includes selected paths and per-root bytes; both UIs render them and have assertions. |
| Repository key follows only path | Confirmed. Include Git config file identity/change time/content digest in the repository incarnation; use one sanitized Git lookup. A re-clone changes the key. |
| Agent grants include ownership roots / persisted arbitrary paths | Confirmed. Resolve registered owners, validate configured-store containment, grant only exact `objects/<id>/data` children, exclude shared pools and control directories. |
| Malformed row or row cap blocks the store | Confirmed. Isolate bad rows, query allocations by identity, fetch registry rows in batches, add reviewed metadata-only repair with preserved audit data. Complete inventory still materializes all rows; batching bounds a fetch, not total inventory memory. |
| Any session pins every pool | Confirmed. Pool eligibility uses its own lease. Unrelated sessions no longer pin it, and archive-only retention can run during another build. |
| Leases unused in real commands | Confirmed. Broker transfers use owner leases; command children inherit private/output lease descriptors. A Linux regression covers a descendant surviving its supervisor. This remains cooperative, not containment of arbitrary unwrapped processes. |
| npm cache has no integrity verification | Overstated. npm verifies integrity, but metadata/private-package retention and mutable cross-execution sharing are still unsuitable. Keep npm/uv/pip caches private; share only broker-verified archives. |
| npm stores username/password verbatim in the request key | Incorrect for current make-fetch-happen: its key formatter sets `auth: false`. Query strings remain included. Do not depend on caches to sanitize secrets; no npm cache is shared here. |
| Pathname containment has rename races | Confirmed for destructive operations. Pin Linux parent descriptors for registration/quarantine/removal and use fd-based symlink-resistant recursive removal. Refuse destructive cleanup where these APIs are unavailable. |
| Full-environment Git identity lookup | Confirmed. Use `project_git.git`, which strips Git overrides and disables fsmonitor. |
| Active bytes cause excessive closed-cache eviction | Confirmed relative to the documented closed-cache budget. Retention targets now count eligible inactive bytes. Active build limits remain separate. |
| HTTP request waits indefinitely on maintenance lock | Confirmed. Administrative actions return a typed HTTP 409 retry response when busy; preview scans only selected owners. Background scans may wait off the HTTP thread. |
| Storage version coupled to build mode | Corrected. Current plans have an independently checked `storage_version`; legacy plans lacking it preserve old behavior. Actual recipe allocations determine validation closure. |
| Duplicate wrapper vocabulary | Confirmed. Remove unused `capacity_wrapper`; adapter installer/guidance hooks are authoritative. Retired unknown SDK bindings still fail explicitly rather than silently using host tools. |
| Dead pin field / unused prepare cache parameter / empty legacy cache allocation | Removed. Current storage entries allocate only managed caches; activation/receipt folders remain separate. |
| Whole archive buffered in memory | Confirmed. Stream bounded chunks, verify before atomic publication. Crash partials stay inside leased, budgeted pools and are reclaimed with their pool. |
| Linked ancestors refused | Intentional conservative path contract. Do not weaken deletion boundaries to accept linked storage ancestors. |
| Close errors swallowed | Corrected. Persist pending storage finalization/error and reconcile it; cache-transfer warnings are recorded separately from task verification. |
| One finalization aborts all scans | Corrected. Isolate/persist per-owner failures, continue inventory and retry later. |
| Tool clean makes the monitor report a missing store | Corrected. Probe the stable owned parent and count absent data as zero output. Ownership metadata remains outside tool-owned data. |
| Semantically invalid registry metadata cannot be archived | Corrected. Validate metadata separately from filesystem/activity checks and bind owner-receipt repairs to reviewed bytes; archival deletes no files. |
| Active roots never expire by age | Intentional. Recover/close an uncertain session explicitly. Idle age alone is not evidence that processes have stopped; known failed pre-agent preparations are closed by reconciliation. |
| Exclusive inventory lock probe | Intentional: an exclusive nonblocking probe detects any active shared lease. A concurrent probe can conservatively label a root busy briefly, never authorize unsafe deletion. |
| Persistent per-identity lock files | Intentional stable-inode coordination. Unlinking a lock while a cooperating process may still hold it can split ownership across two lock inodes. |
| Platform byte accounting / admin host paths | Intentional. POSIX reports allocated blocks, other clients logical bytes. Absolute inventory paths remain administrator-only. |

Primary references: [Python 3.12 pathlib](https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.is_junction),
[npm cache integrity](https://docs.npmjs.com/cli/v11/commands/npm-cache/#details),
[make-fetch-happen cache key](https://github.com/npm/make-fetch-happen/blob/main/lib/cache/key.js).
