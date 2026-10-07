# Development toolchain and missing tools

## Who provides which tool

There are two separate toolchains, and the product only owns one of them.

1. **The project's own pinned build toolchain** — Flutter/Dart 3.44.8, installed
   by the root-run `install/dev-tools-install.sh`. The Flutter files are owned
   by `herdr` and symlinked into
   `/home/herdr/.local/bin`. The dashboard's fixed-purpose SDK service requests
   it because `scripts/build-web.sh` cannot produce the dashboard without it.
   Readiness is the `installed` flag, which means "Flutter is present".
2. **A tool one task happens to need** — a linter, a code generator, a
   language-specific checker. The worker obtains it within task permissions,
   in a task-specific cache or environment. Pin the repository-required version
   and verify release checksums or package-manager integrity, then report it.

Adding a task-specific tool to the root SDK service is not a small change. It
makes the gateway aware of one tool, adds a version the gateway must keep
matching, and — if it also becomes a required check in `scripts/build-web.sh` —
turns a provisioned toolchain into a precondition for every integration event
and every dashboard build, because `validation.py` runs the repository's own
scripts to verify each worker merge. That is why the SDK service stays
Flutter-only.

## The request chain

Three files, easy to confuse by name. Only one writes each file:

| Step | File | Writes or reads |
| --- | --- | --- |
| Dashboard queues a request | `web/gateway/sdk_install.py` | writes `/var/lib/herdr-sdk-requests/request.json` |
| Root worker consumes it | `install/sdk-install.py` | reads and unlinks that request, runs `install/dev-tools-install.sh`, and is the **only writer of `/var/lib/herdr-sdk/status.json`** |
| Gateway reports readiness | `web/gateway/sdk_install.py` | reads `status.json` and whether a request is pending |

An agent never runs these installers. An administrator can request provisioning
or opt into the existing automatic SDK policy; the root worker executes it.

## What an agent does with a missing tool

The worker bundle (`web/gateway/skills/herdr-worktree-integration/`) has a small
`SKILL.md` routing entry and full references for merge, tools, validation and
reporting. `.agents/skills/herdr-worktree-integration/SKILL.md` is the project
discovery entry linking to that canonical bundle, without duplicated procedures.
Integration prompts include the routing entry with absolute deployed reference
paths, so older worktrees need not contain the new project skill. Task launches
receive only the tool-reference location. Detail is loaded when needed, not
flattened into the 8,000-character chat request. CLI discovery and file access
depend on the runtime and profile permissions; report unreadable resources
rather than bypassing permissions. In short: install it yourself, pinned and
integrity-verified, inside a task-specific cache or environment; run the check for real;
report the commands, their real exit status, and the version and location you
installed. A check that could not run is `not_run`, never `passed`.

`missing_toolchain` means a required tool could not be obtained locally, including
network, architecture or version failures. Report the actual cause. Permission
denials use `missing_permissions`. The SDK service provisions Flutter/Dart only;
it cannot fix arbitrary tool blockers. Agents share the `herdr` account and home,
so use task-specific caches/environments and never overwrite shared tools.

The report path already exists: an agent's summary reaches the dashboard
through the existing integration event, so an agent-installed tool is visible
without new plumbing.

## Historical events

Provisioning a tool never rewrites history. An event that was recorded as
merged with validation pending stays pending until somebody retries validation
for it; enabling automatic provisioning does not convert an old event into a
pass and never waives a check.
