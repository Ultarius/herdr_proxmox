# OpenCode models and reasoning

The agent editor automatically loads the connected account catalog. Explicit
**Refresh models** bypasses the cache. Provider/model IDs and enabled variants
come from OpenCode, never invented dropdown suggestions.

Successful discovery is cached on disk under the gateway user's
`.config/herdr-web/model-cache/`. Cache keys include the project, the CLI binary
identity, the account credentials file identity and the global/project config
file identities; credentials themselves are never copied into the cache.

Fresh catalogs are reused for 24 hours. A requested model or variant missing
from a catalog older than 60 seconds causes a refresh; recently missing
selections are rejected rather than repeatedly invoking the CLI, and an aged
catalog refreshes on its next use. A failed automatic refresh preserves the last
successful catalog for up to seven days, shows a stale warning and backs off for
60 seconds; unknown models or variants still fail validation. Explicit refresh
failures are reported and never overwrite the last successful catalog.

Reasoning is stored as model + variant in the profile-specific OpenCode agent
frontmatter, selected with `--agent`; the `--model` argument remains
`provider/model`. This route is verified in OpenCode 1.18.35 and later; older
versions must use Model default or upgrade. Permission defaults are preserved
when an agent file is created only for variant selection. Clearing reasoning
removes the variant on the next launch.

Task launch validates models before rotating a startup session, and startup
checks record their stage. A failed preflight that created no pane, worktree or
session can be explicitly retried from the task using the current saved agent
settings; retry refuses another active execution or a removed/archived assignee.
The older incomplete-model-metadata failure is eligible only when those same
allocation fields are absent. Failures after allocation remain
inspection-required and are never automatically replayed. Automation waits for
launch readiness before opening the task worktree.

Malformed metadata reports the model ID, line/offset, output length and digest;
raw provider metadata or credentials are not included in diagnostics. One
malformed read-only enumeration is retried once; terminal prompts are never
retried by this mechanism. Discovery validates the complete response before
replacing the cached catalog atomically.
