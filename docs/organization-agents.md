# Organization and persona agents: feasibility

Reviewed on 2026-10-04 against the live Herdr marketplace index, official
documentation, and the plugin repositories below. The first implementation is
now part of the dashboard; live Proxmox/agent compatibility remains to be tested.

## Options and selected implementation

| Option | Strength | Tradeoff | Decision |
| --- | --- | --- | --- |
| Application-owned roster + official Herdr CLI | Durable hires, explicit personas, all four installed runtimes, one authoritative history | We own validation, storage, and job lifecycle | Implemented first |
| Agent Team as a server-side adapter | Project-local Markdown roles and leader workflow | Its workspace leader is not a complete organization or durable roster | Optional future import/export convention |
| A2A plugin as a server-side adapter | Standard agent cards and tracked interoperability | Additional Node service, contract/version checks and named-persona routing to validate | Add when external A2A clients are needed |
| Herdr Projects orchestration | Existing coordinator, worktrees, task threads and memory | A second task authority would conflict with our records without a deliberate integration | Alternative orchestration choice |

There is no Flutter/Jaspr incompatibility to migrate. Agent Team's `package.json`
and `src/start-team.mjs` show a Node executable that calls Herdr through
`spawnSync`, starts a named runtime, then sends a leader instruction prompt.
Its UI/workflow cannot be imported as Flutter widgets, but its server-side
commands can be called through a gateway. A2A likewise belongs behind an HTTP
or CLI adapter rather than inside the browser. The Flutter page is implemented
in Dart; this gateway implements the roster and job behavior independently in
Python/SQLite. No plugin source or Node UI has been vendored or installed.
[Agent Team launch source](https://github.com/gdli6177/herdr-agent-team/blob/12e43cdd1cba7f78897a46564a88ddf8b90948df/src/start-team.mjs),
[A2A contract](https://github.com/IsaiasZc/herdr-a2a/blob/main/docs/herdr-contract.md).

## Implemented first version

### Team role presets

When creating an organization, select any combination of Lead Developer,
Frontend Developer, Backend Developer, Full-stack Developer, Reviewer,
Integration Engineer, QA Engineer and DevOps Engineer. Select an existing
project folder from the **Team project directory** dropdown and a model policy,
then use **Preview team** to inspect
personas, CLI/model/effort choices and reporting relationships before saving.
The dropdown uses the same root folder listing as the project explorer, excluding
files, symbolic links and hidden folders. If no folders are available, add or
clone a project in the explorer and reopen the organization form. Changing the
selected folder requires a fresh team preview.
The **Team preset** selector can fill that draft with Solo developer (one member),
Small product team (three), Frontend/backend team (five), or Full engineering team
(all eight roles). All presets start with the balanced model policy. Applying a
preset replaces the draft roster; the resulting members can still be edited,
duplicated or removed before preview and creation. API clients may supply just
`team_preset` instead of an explicit roster, or override its recommended members.
Organization creation saves all selected profiles in the same SQLite transaction;
a failed save creates no partial team, and retrying the same request cannot
duplicate hires. Existing organizations and manually hired profiles retain their
normal editing workflow. Preset profiles can be renamed and customized afterward.

Before creation, expand **Configure member** to edit the name, persona
responsibilities, CLI, model, reasoning and reporting relationship. Use **Add
another role** to create multiple specialists of the same type (up to 20 members).
Automatic model choices remain automatic when the global policy changes; explicit
overrides remain attached to that member. Any edit requires another preview.
Manager cycles, missing managers and unsupported model/effort overrides are
rejected before any records are saved. The resolved persona includes the reporting
instruction and shared team boundaries. The API accepts a `team_members` draft
roster with distinct `key` values and `manager_key` references.

The lead reports to the organization owner. Developers, integration and DevOps
report to the selected lead, or to the owner if no lead was selected. Reviewer
and QA report directly to the owner for independent assessment. Every role has
distinct responsibilities, evidence/reporting expectations and publication
boundaries. Profiles use separate Git worktrees and CLI default permissions.
They are immediately available for task assignment; their sessions start through
the existing task/Launch workflow. Creating an organization sends no billable
agent prompt and creates no idle sessions.

For a Git repository root, the Integration Engineer is also saved as the default
repository coordinator; an existing manual coordinator setting takes precedence.
These defaults recover after restart or configuration import. Newly created tasks
automatically use the independent Reviewer for acceptance, with QA as the fallback
when the reviewer is the assignee or ineligible at task creation. That reviewer
selection is retained for the task; a busy or subsequently removed reviewer is
not silently replaced. Launch the reviewer before acceptance dispatch, or change
the per-task review settings explicitly. Template creation does not enable publishing, automatic builds or SDK
installation. Non-Git or nested directories show a workflow setup note in preview.

Model selection uses installed CLIs with current Herdr integrations. Codex and
Claude require a signed-in account and use the maintained model catalog;
individual subscription entitlements cannot be enumerated, so these are
recommendations that can be changed in the member editor. OpenCode uses its
project/account catalog, including providers configured through the environment,
and preserves reported costs and reasoning variants. Models explicitly lacking
tool calling are excluded. The CLI's
[model enumeration](https://opencode.ai/docs/cli/#models) reports available models
from configured providers and supports verbose cost metadata.

- **Balanced:** choose models meeting each role's capability floor. Coding,
  coordination, review and integration require the general/coding tier; bounded
  QA permits lighter models. Suitable free OpenCode models are preferred within
  the target tier.
- **Lowest cost:** prefer eligible models reported free by cost metadata or a
  free model ID, then preferred CLIs and known costs. Explicit positive pricing
  overrides a free ID. Cheap models below the role minimum are excluded.
- **Higher capability:** prefer the stronger tier for lead/review/integration,
  with a general-tier fallback when necessary.

**Preferred CLI** breaks ties between equally suitable model choices, allowing
an included-usage account to be preferred without guessing its subscription.
The preview explains capability floors and reported context capacity. Models
with explicitly insufficient context or no tool calling are excluded; unfamiliar
models need positive tool/context metadata to establish compatibility. Capability
tiers are maintained policy heuristics, not measured model benchmarks.

Creation also exposes maximum concurrent sessions (0–50), concurrent meetings
(0–10), and session starts per rolling 24 hours (0–500). Zero means unlimited.
Custom teams default to 4 sessions, 1 meeting and 20 starts; team presets supply
size-appropriate recommendations. These initial limits are saved atomically with
the roster and are enforced by the existing shared session budget, including
idle sessions and review helpers. Later administrator changes to automation
policy take precedence. Changing limits requires another preview.

The preview's CLI account check lists available and excluded CLIs, with reasons
such as missing installation, missing integration, unknown sign-in or failed
model discovery. It never copies credentials into a team or template. Reported
sign-in and catalog availability do not claim that subscription quotas or every
model entitlement have been verified.

### Saved template library

Administrators can expand **Save reusable template** in the creation form,
provide a name/description, and save the current draft as a new template.
**Saved template version** loads an exact saved version into the editable draft;
**Save new version** appends edits to that template. Old versions remain readable.
A stale editor cannot overwrite newer changes: load the latest version first,
or save the draft under a new template name. Lost-response retries use the same
request ID and do not create duplicate versions. Loading replaces the draft;
saving preserves edits made while a save was in flight.

Versions freeze member names, persona responsibilities and reporting references,
plus model policy, explicit CLI/model/effort overrides, fallback CLI preferences,
resource limits and preset provenance when supplied. They exclude project paths,
account data, organization branding and live sessions. Automatic model choices are evaluated against the
new project's CLI catalog whenever a saved template is used. Each created
organization records the source template ID/version/digest; its member profiles
and initial limits remain independent of later template updates.

The library supports 50 templates and 25 immutable versions per template, stored
alongside the organization database. Configuration exports include its complete
history; imports validate definitions, version continuity and content digests,
and reject conflicting records. The same 2 MB limit applies to export and import.
Oversized exports fail explicitly without omitting records or historical versions;
back up the organization SQLite database directly for libraries beyond that limit.
The authenticated team save, preview and library endpoints separately allow up to
4,395,136 request bytes (about 4.4 MB) to accommodate the maximum roster, paths and
escaped Unicode text. This deliberately exceeds the 2 MB configuration transfer
limit; other endpoints retain their existing caps, normally 64 KiB.
Shared-library writes and imports require an administrator; authenticated
operators can load templates and create organizations using them.

`GET /api/organizations/templates` includes saved-template/version metadata.
`POST /api/organizations/templates/library` supports `action=read` with
`template_id`/`version`, and `action=save` with `name`, `description`, `definition`
and `request_id`. Appending also requires `template_id` and `expected_version`.
Preview and organization creation accept `template_id` and `template_version`,
with optional member overrides. Omitting the version selects the current latest
version, while supplying it pins the exact version.

These policies are deterministic recommendations, not billing guarantees or
benchmarks. They compare reported input/output costs when available; account
subscriptions, quotas and unknown prices cannot be compared reliably. Free
models and limits can change. Antigravity falls back to CLI-managed model and
reasoning settings and shows an account-verification reminder. Unsupported
reasoning variants fall back to the model default. Account/model changes after
preview require another preview before the organization can be created.

Authenticated `GET /api/organizations/templates` returns the role/policy catalog.
`POST` to the same endpoint previews `template_roles`, `project` and
`model_policy` without creating records. `POST /api/organizations/save` accepts
those fields on new organizations and the preview's `template_plan` digest.
Model discovery runs outside the database write transaction; saved request
replays do not need the CLI accounts to remain available.

Open **Organization** in the connected dashboard (`/organization` in ZenRouter).
The page supports multiple organizations, purpose/shared instructions, hiring
and editing durable agent profiles, a manager hierarchy, a project directory,
and runtime selection (Codex, Claude Code, OpenCode, Antigravity). A profile has
a versioned persona; the runtime uses its CLI's configured default model.

The dark application shell has an organization icon rail on the far left. Select
an icon to switch teams; the selection is retained while navigating between pages
and cleared on sign-out. The adjacent sidebar provides dashboard, chart, agent
activity, logs and CLI configuration navigation. On smaller screens it becomes
a drawer while the organization rail stays visible.

The **Chart** view uses saved manager relationships to draw the hierarchy. Drag
to pan, zoom with the controls or wheel, and use **Fit** to frame the team. Select
an agent card with the left mouse button to open its console; right click to
configure its persona. **Team** contains the roster and launch controls;
**Runs** contains launch/delegation history. Status dots reflect matching live
Herdr run bindings, with gray indicating no matching status.

The agent menu also opens the console (`/agents/<profile-id>`). Blocked live
agents show **requires attention** beside their menu name and on their chart
card. The console refreshes retained terminal output on an adaptive interval:
every second while the agent works, every 5 seconds while it is idle or
done, and every 2 seconds while it awaits attention or has no status. The
organization snapshot it needs for the agent's name, role and response delivery
is read when the console opens, once a minute, and while a submitted response
is still being delivered. Terminal refreshes use a narrow live inspection
without copying the organization database. If delivery is uncertain, use
**Recheck pending response** to retry the same recorded response; other key
actions stay disabled until its delivery is known. The console
shows the runtime's permission or question prompt. Use arrows/Tab to select
Allow or Reject in that prompt, then confirm the selected response; Escape
cancels where supported. These are runtime terminal controls, so the selected
option must be reviewed before confirming. The gateway records response
delivery and the operator, and rejects a console response if its inspected
prompt changed, the agent stopped being blocked, or its conversation was
replaced. Retained output is bounded and may omit earlier work.

Both dashboards show **Requested folder access** above the agent console and
outside collapsed live discussions, naming the requesting group participant and
listing the explicit path patterns from a blocked OpenCode external-directory
prompt. These are observations of terminal text, not verified filesystem events
or an access history. The list clears on the next successful inspection when the
agent resumes or the folder prompt disappears. Unknown, incomplete or wrapped
prompt layouts remain available in the raw console; oversized pattern lists are
explicitly marked as incomplete. No permission is approved or saved by the panel.
Inspection errors hide the summary until a fresh request can be observed.
Review the selected runtime option in the console before confirming it.

**Allow dashboard outputs** grants reply/artifact folder access, not all of
`~/.cache` or `/tmp`. The panel points to **Additional accessible path globs** in
**Edit persona** or **Edit group** for future launches. Saved settings require
relaunch; the panel does not claim to know the runtime's remembered **Allow
always** decisions or grant broader access automatically.

Launch creates a separate workspace/root pane, waits for the shell, then starts
a unique Herdr alias and sends the saved organization instructions and persona
as the first conversation prompt. This follows Agent Team's prompt convention;
it is not a privileged system prompt or proof that a model obeyed the persona.
Profile edits affect future launches. Each run retains the profile and
organization snapshot used to start it.

The operator can delegate on behalf of one launched hire to another. Both
bindings are checked before delivery; the recipient receives a task ID and the
sender's alias for a possible reply through Herdr. This is direct terminal
coordination, not an implemented standardized A2A protocol. Completion reports
are explicitly recorded by the operator after checking the reply/evidence over
SSH. Saved terminal snapshots are now available through the Saved logs page;
these do not represent complete conversation transcripts. Automatic agent-authored reports are future
work. Current activity remains on the Live agents page; organization run states
record launch/delivery history rather than claiming to be live process status.

The backend stores `organizations`, `profiles`, `jobs`, and request deduplication
records in `~/.config/herdr-web/organizations.sqlite3`. SQLite is built into
Python; no database service or extra Python package is required. Include this
file in container backups. Manager cycles and cross-organization references are
rejected. Assigned directories must already exist beneath `~/projects`.
Launch searches for repository roots only within that managed projects directory;
a `.git` above it (such as a home-directory repository) cannot own a launch.

Launch and delegation return immediately with a durable job ID and run in a
single background worker. The UI disables concurrent form actions and retains
the same request ID for an explicit request retry. Repeated requests return the
original response. Terminal jobs are never automatically retried. Restarting
the gateway marks interrupted queued/running jobs `uncertain`, preserving them
for inspection. Failed commands become `needs_attention`; even a timeout can
mean input was sent. Release a run binding only after inspecting/stopping its
old terminal over SSH; release itself does not kill a process.

Launch checks the installed `herdr api schema --json` AgentInfo contract.
Identity checks use native `agent_status`, readiness flags where available,
the alias, pane, runtime, and a reported native conversation reference. If a
runtime reports no native reference, identity is limited to its live alias and
pane; inspect it over SSH after restores or manual conversation switches.
Pane moves intentionally require a new binding rather than guessing an ID.

Local verification covers authenticated HTTP create/hire/read, persistence,
deduplication, cycles/scope/path validation, two distinct launch personas,
delegation/reporting, incompatible schemas, blocked agents, conversation
replacement, and restart recovery with a simulated Herdr command adapter.
Flutter widget tests exercise real forms, validation, persona edits, navigation,
connection reuse, disconnects, retry IDs, and narrow screen layout. Flutter
and Jaspr analyzer/release compilation are also checked.

Browser verification of the compiled release app also exercised organization
creation, saving a hire, editing its persona to version 2, and disconnect/
reconnect against the real local HTTP gateway and SQLite database. It caught
and resolved a JavaScript-specific integer-shift bug in request-ID generation.
Agent execution was disabled in that disposable local preview.

This does not establish
live coding-agent or Proxmox compatibility: the disposable CT checks at the end
of this document remain required before a production deployment.

## Conclusion

An organization with a persistent roster, a Hire agent form, personas, and
delegation is feasible. Keep organizational records in this application's backend;
use Herdr to run and observe the corresponding coding-agent processes. Flutter,
Jaspr, Juice and ZenRouter can remain at their currently pinned versions.

Herdr already offers the building blocks for agent-to-agent work. An agent can
use its CLI to launch another agent, prompt it, inspect its output, and wait for
status. This terminal coordination is distinct from the standardized A2A wire
protocol. The `herdr-a2a` plugin adds that protocol and tracked delegation.
[Official automation reference](https://herdr.dev/docs/agent-automation/),
[A2A plugin](https://github.com/IsaiasZc/herdr-a2a).

## Relevant plugins

These were present in the [marketplace index](https://assets.herdr.dev/plugins/index.json)
generated at 2026-10-04T08:30:33Z. Versions below are manifest versions from that
snapshot, not claims of compatibility testing in our container.

| Plugin | Snapshot | Useful capability | Integration boundary |
| --- | --- | --- | --- |
| [Herdr Agent Team](https://github.com/gdli6177/herdr-agent-team) | 0.1.0; Herdr >=0.8.0; Node >=20 | Project-local Markdown roles and playbook; starts a leader | Good persona/team convention. It does not provide the application's durable organization database. |
| [herdr-a2a](https://github.com/IsaiasZc/herdr-a2a) | 0.0.2; Herdr >=0.8.2; Node >=20 | Agent discovery, A2A cards, worker launch/reuse, tasks and follow-up | Candidate for structured delegation. Verify that worker selection can preserve a specific hired persona, rather than choosing only an agent runtime. |
| [Herdr Projects](https://github.com/eliasstravik/herdr-projects) | 0.2.34; Herdr >=0.9.1 | Coordinator, task threads, shared instructions/memory and worktrees | Larger orchestration alternative; choose it deliberately to avoid two competing task records. |
| [murmur](https://github.com/AFetisa/herdr-murmur) | 0.1.0; Herdr >=0.7.0; Node >=18 | Runtime agent/subagent tree and observability | Useful UI reference. Its deeper usage data comes from Claude transcripts; its runtime tree is different from an editable reporting hierarchy. |

Agent Team initializes `.agents/team/playbook.md` and role files for a leader,
researcher, implementer and reviewer, preserving existing files. Leader profiles
can select runtime, model and launch arguments. A custom role editor could write
the same convention. Its README's skill-install example still names the older
Herdr repository; use the current
[official skill documentation](https://herdr.dev/docs/agent-skill/) when setting up
agents. Installing a plugin does not install or authenticate the coding agent.

A2A's own checked contract describes an older Herdr binary. Its manifest version
and documentation are evidence of intent, not proof that every current Herdr
combination works. Pin a reviewed revision and test against the installed
binary's `herdr api schema --json` before enabling the adapter.
[Plugin contract](https://github.com/IsaiasZc/herdr-a2a/blob/main/docs/herdr-contract.md).

## Proposed user flow

1. Create an organization: name, purpose, standing instructions, project roots.
2. Hire an agent: name, role, persona, coding-agent runtime, model, manager and
   assigned project. Hiring creates a durable profile; launching is a separate
   action so the roster survives shutdowns and startup failures.
3. Start the hire in its project: create a dedicated tab/shell pane, apply its
   persona through a runtime-specific adapter, launch the agent, and store the
   returned runtime identity alongside the profile.
4. Assign work or delegate to another hire. Show queued, delivered, working,
   needs input, reported complete and failed as separate task states.
5. Keep terminals in the existing SSH workflow. The browser owns organization
   setup, role editing, assignment, status and delegation history.

Example: an Engineering organization has a lead named `maya`, an implementer
named `noah`, and a reviewer named `iris`. Maya can delegate implementation to
Noah and review to Iris. Their names and roles remain in the roster when no
process is running. A persona is an instruction document describing how each
should work; it does not give a model additional capabilities by itself.

## Backend and UI changes

Suggested backend entities:

| Entity | Durable fields |
| --- | --- |
| Organization | ID, name, purpose, shared instructions |
| Agent profile | ID, organization ID, name, role, persona/version, runtime, model, manager ID |
| Project membership | Organization ID, workspace/project reference, allowed profile IDs |
| Agent run | Profile ID, session, pane ID, native agent-session reference, start/end state |
| Delegation | ID, sender/recipient profile IDs, run bindings, task, delivery state, result, timestamps |

SQLite would fit the current Python gateway and a single Proxmox container.
Validate manager relationships for cycles and scope every profile/task lookup by
organization. Organization labels do not provide process isolation: the current
gateway has one token and runs every process under one OS user. Independently
trusted organizations would need a separate access and runtime isolation design.

Add ZenRouter pages for organization setup, roster, agent detail/persona and
delegations. Use feature-owned Juice blocs for forms and organization state;
retain the existing connection owner. Keep credentials on the server. Add fixed,
validated backend operations instead of exposing an arbitrary CLI endpoint.

The current gateway's ten-second command timeout is insufficient for agent
startup. Launch and delegation should return a job ID and execute off the HTTP
request path. Use idempotency keys to prevent double hiring/launching after a
retry. Retain failed runs so users can repair missing binaries or authentication
over SSH. No automatic retries should submit the same prompt twice.

## Herdr adapter responsibilities

- Create layout first, then call `agent start` on a returned shell pane ID.
- Allocate a unique live alias and bind it to the persistent profile ID.
- Apply persona instructions through each runtime's supported mechanism. A pane
  name or `SOUL.md` file alone does not prove the runtime has loaded a persona.
- Address prompts to the current run; verify identity after restarts or moves.
- Use Herdr status for activity and explicit reports for task completion. An
  idle agent or a delivered prompt does not prove the requested work succeeded.
- Surface authentication/permission dialogs as needs input for the operator.

Names are live aliases, and are cleared when their processes exit. Startup also
requires an existing pane whose shell is available. These constraints make a
separate roster/run model necessary.
[CLI reference](https://herdr.dev/docs/cli-reference/).

## Recommended delivery order

First implement organization storage, the Hire agent form, persona editing and
explicit launch through official Herdr. Use Agent Team's Markdown convention if
its project-local team model fits. Next add direct named-agent delegation and
durable task reports. Add the A2A adapter when standardized interoperability is
needed, after verifying named-persona routing and delivery semantics. Keep a
single authoritative delegation record whichever adapter is chosen.

Before declaring this functional, verify in a disposable Proxmox CT: two distinct
personas on the same runtime, leader-to-worker delegation, a reply, blocked
authentication, double-click/retry behavior, disconnect/reconnect, process
replacement and container restart. No plugins were installed or executed during
this review, and no live multi-agent compatibility test has been performed.

### Chat reply delivery evidence

Chat jobs persist bounded delivery evidence before submission, after the CLI
returns, and after the original session is checked idle and unchanged. These
stages do not prove the model processed the request. Only a nonempty, bounded
UTF-8 reply in that attempt's own reply file makes it answered: each execution
attempt writes a unique `reply-<id>.md` recorded in the delivery evidence before
submission, so a late or repeated invocation can never overwrite or be mistaken
for another attempt's reply. Legacy jobs keep their original `reply.md`. CLI
evidence stores only short status/type fields, never echoed prompts or terminal
output.

The chat record shows the delivery stage, its timestamps, the reply file name
and the job ID beside the error. The labels keep an acknowledged CLI return
distinct from a verified reply so the dashboard never implies acknowledgment or
completion that was not proven.

The reply contract explicitly requires saving the file even when the requested
answer is JSON. Missing, empty, oversized, symlinked and unreadable files have
separate errors including the expected path; integration events surface the
job error directly. Uncertain delivery never triggers an automatic resend.
Inspect the conversation, recover a saved result if it exists, or explicitly
choose validation-only continuation when Git incorporation is verified. Recovery
preserves the previous error and marks the reply verified without terminal input.
Older jobs retain their existing evidence; an update cannot prove past delivery.

## Organization limits and group proposals

Select an organization under **Org chart**, then open **Automation & limits**.
The same validated policy editor is available in **Group -> Settings**. Limits
are scoped to the organization, including standalone group discussions; no task
needs to exist to read or change them. Only administrators can save policies.
Task automation retains its task-specific controls and a shortcut to organization
settings.

**Max per meeting** limits automatic queueing, not draft creation. Every valid
proposal becomes a task; excess proposals stay unstarted drafts with the queue
limit reason recorded. Follow-up depth, assignee queue and daily limits likewise
defer automatic execution rather than discard proposals. Group opt-in and
organization-wide queueing are separate; `needs_review` proposals remain drafts.
A rejected proposal shows its recorded reason in **Group -> Proposals**, alongside
its assignee and a link if a task was created. Task-linked reviews use the parent
task's recorded materialization outcomes. Only finalized artifacts are parsed;
viewing proposals does not create work or re-evaluate a discussion.

Changing a limit affects future evaluation, not discussions already evaluated.
Older history can be loaded from the proposals tab. Existing drafts remain in
Tasks, where an operator may review, start or queue them.


## Repository-wide review groups

Enable **Review the whole repository by default** in the group editor for a
refactoring council. All members must have the same configured project. Set the
group purpose to require a subsystem inventory, divided coverage, cross-cutting
code evidence, challenge of candidate refactors and a coverage table with gaps.
The complete Refactoring Council description is in the repository `plan.txt`.

**Start discussion** captures the configured project's committed HEAD at submission
and supplies that SHA to every member through the facilitator. Retained worktree
HEADs and historical knowledge do not choose the target. Dirty/uncommitted work
is outside the baseline. No fetch, checkout or branch update is performed; update
the configured project explicitly if you intend to review newer upstream code.
The saved baseline survives monitoring/recovery. Custom messages and task-linked
reviews use their explicit scope. Existing groups default to topic discussions
until edited; an upgrade does not broaden their authorization.

This is a coordinated advisory review, not an enforced scanner: the artifact must
state inspected areas and omissions before concluding that no refactor is needed.


## Automatic verification and draft PR delivery

Delivery inspection belongs to an exact candidate SHA. When a new candidate
supersedes an older inspection, the previous report is retained in bounded
delivery history and its frozen prompt and notification retries are not reused.
The current candidate requires fresh verification and inspection; prior failures
do not reset the task's automatic repair budget. An attention report for the
current SHA continues to require operator inspection.

History retains at most five compact reports (candidate, state, error, agent
summary, failure stage and superseding candidate/time), with an optional chat ID
for audit. Frozen prompts, packets and retry state are excluded. A superseded
inspection chat remains in organization job history and may finish normally;
it is not cancelled while delivery is uncertain. Its late reply cannot update
the new candidate's flow, which references its own chat ID. The supersession
activity also records the old chat ID so operators can locate that job.
The activity subtitle displays the superseded candidate and chat ID without
opening the raw audit records. Existing history is compacted on its next
supersession; read-only polls do not rewrite saved task data.

Task API actions and the delivery scheduler share a per-task reentrant lock in
the gateway process. It covers publication and PR creation through result
recording, so a supported capture action cannot change the recorded candidate
mid-request. A later candidate starts fresh preflight and delivery after that
critical section. External Git edits are outside this task lock; exact-SHA
publication and checkout checks remain required. `accepting_repair` is different:
it is a persisted recovery checkpoint spanning candidate capture, and must resume
that transition after a crash rather than being treated as a superseded report.

Clicking **Validate latest commit** also checks whether protected validation
files differ because the task predates the approved upstream. If the task did
not edit protected files, that click authorizes one saved branch-update job for
its assigned worktree agent, even before a PR exists. The job pins both commits,
keeps a recovery snapshot, merges without rewriting history, captures a verified
completion receipt and validates the resulting commit. It uses the configured
validation executor; a durable build service is not required for this manual flow.
Both dashboards show branch-update progress in task activity. Publication remains
a separate action.

The approved checkout must match the fetched task base branch. This operation
does not advance the shared checkout or approve new policy rules. Task-authored
policy edits, policy edits made while resolving conflicts, ambiguous decisions,
changed upstream approval and uncertain sessions stop for inspection. Restarts
adopt the saved session instead of sending another prompt. A failed validation
can retry checks, but cannot recursively launch another branch-update agent.
Background validation alone does not authorize this repair.

After the verified merge, an exact-candidate policy transition records the
operator, pinned upstream, repair identity and approved policy digest for
independent validation. The repair session's original frozen policy is retained;
the transition cannot be reused for another candidate or completion receipt.

In a task's **Automation settings**, administrators can independently enable
**Automatically capture completed candidates** and **Automatically validate
captured candidates**. Capture checks the completion receipt, task session,
branch and clean committed candidate; it needs no build service and does not
claim that validation passed. Validation uses the same executor as manual builds and
retains the task session until exact-commit checks settle and the worker reviews
the results in a separate, verified reply. With capture off, automatic validation
waits for an operator to capture the candidate. Existing combined policies keep
both behaviors until edited. Organization defaults apply to new tasks. The worker
waits idle for dashboard feedback rather than polling or occupying a long-running
gateway worker thread. Gateway-executed builds stop when the gateway restarts and
are marked interrupted; they are not automatically retried. Install the durable
build service to run builds independently of gateway restarts.

When an organization policy save is rejected, its editor remains open with the
draft intact. The issue appears at the top, and affected rules or limits are
highlighted with an inline explanation. Correct the draft and save again, or
cancel to leave the saved policy unchanged. A retry after an uncertain response
reuses its request identity until the draft changes.

Under **Builds & deployments**, administrators can use **Install build service**
to queue the release's fixed installer, inspect status/output, and retry a failed
installation. Requests that do not start within 11 minutes expire with an error;
inspect `herdr-build-install.path` before retrying. The root worker rejects expired
requests if the watcher resumes later, and preserves any newer retry during cleanup.
The root request worker is provisioned by `install/web-install.sh`
on new installations. Existing containers need a one-time root bootstrap:

```sh
bash /opt/herdr-web/install/build-service-setup.sh
```

This bootstrap enables dashboard installation; it does not install or enable
delivery automation itself. Installation requires CPU/memory cgroup controllers,
starts `herdr-build.service`, and restarts the gateway. It waits until current
builds finish before accepting an installation request and refuses new build
submissions while installation is queued/running. The dashboard reconnects and
reports the service executor afterward. Automatic validation and publication
remain explicit policy choices. The direct root command
`bash /opt/herdr-web/install/build-install.sh` remains available.

Automatic publication additionally authorizes the reviewed task branch to be
pushed to its recorded destination. The protected private-fork workflow uses only the fork configured by the
operator for that upstream.
It never pushes any upstream branch. The operator opens the draft PR through
the comparison link, and Herdr adopts it after checking the exact approved SHA,
head repository/ref, and upstream base repository/ref. The adoption record does
not claim Herdr created the PR, and it preserves the operator's PR text.
Unprotected repositories retain their configured publication workflow. Merge
and deployment remain manual; publication does not establish acceptance.

Delivery waits for matching required checks, verifies a clean unchanged task tree
and completion receipt, uses the normal push/PR recovery paths, and checks the
remote branch and PR identities. Running GitHub checks or undecided mergeability
keep the session reserved. Failures are reported to the worker when its original
session is available. A read-only inspection reply must name the exact commit
and report reviewed or needs_attention; the gateway rechecks evidence before
allowing a handoff. Permissions are never approved automatically.

Failed durable checks are returned to the original worker with bounded check
summaries and the build identity. When the durable result is `failed`, the
worker may repair task code, test it and commit on its existing branch. A verified
reply must identify both the failed SHA and its replacement; the replacement
must descend from the failed commit and have a clean checkout and matching
completion receipt. It receives fresh durable verification before publication.
There are at most two automatic validation repairs per task. Infrastructure
errors, exhausted repairs, and publishing/authentication failures require an
operator decision; the worker must not weaken checks, change credentials,
switch destinations or publish independently.

Enabling publication refreshes identity, repository access, reported publishing
account write access, and fork-network identity. Setup is read-only and does
not prove a token may write. It never creates disposable branches or PRs.

Protected publishing requires two distinct `github_pat_` fine-grained PATs:
the fork owner's PAT selects only the fork with Contents read/write and Metadata
read (plus Workflows read/write for workflow changes); the upstream owner's PAT
selects only the upstream with Pull requests read/write, Contents read, Metadata
read, and available check/status read permissions. Do not grant upstream
Contents write. Herdr cannot inspect the absence of that permission, but its
backend denies upstream pushes, branch/ref mutations, merges, and unrelated
mutation endpoints regardless of credential strength. A token prefix proves
format only. Classic PATs are still supported by ordinary clone connections,
but not by this protected publishing workflow.

Private repositories under different personal owners cannot both be selected
by one fine-grained PAT. Herdr therefore reports manual PR adoption for this
protected arrangement instead of attempting creation with the upstream PAT.
An unreadable private fork in a read-only capability probe does not prevent a
permitted fork push. After publishing, use the comparison link to open the draft
PR on GitHub, then check for adoption. If the upstream PAT cannot read the PR's
full head/base repository metadata, adoption remains pending; Herdr does not
substitute another credential or guess identity. Denied check/status reads are
recorded as `checks_unavailable`, not as successful checks, and cannot satisfy
verification or delivery completion.

The operator supplies protected mappings in `/etc/herdr/publishing-policy.json`,
root-owned with mode `0444` or `0644` under root-owned, non-writable directories.
The product ships an empty example; an absent/empty policy protects no projects
only on a never-enabled deployment. Tasks explicitly displays that state.
The installer preserves existing content, and dashboard updates do not replace
this external file. Root may change it without a release; publishing setup
cannot edit it. A present invalid policy refuses gateway startup. A live policy
failure disables GitHub writes and reports `protected_publication_error`, while
credential reads, clone/fetch, and task/release listing remain available.
Each `protected` entry contains exactly `upstream`, `fork`, `mode: "fork_only"`,
and `arrangement: "manual_adoption"`; the [publishing setup guide](task-review-and-publishing.md#protected-private-fork-publication)
provides a complete fictional JSON example. After writing the mapping, root runs
`python3 /opt/herdr-web/web/gateway/publication_policy.py --arm /etc/herdr/publishing-policy.json`.
This creates root-owned `/etc/herdr/publishing-policy.required`, mode `0444`.
Production requires this marker before enabling a non-empty policy and refuses
a missing/empty enabled policy across restarts. Back up both external files.
Every parent directory of both files must be root-owned and not group/other-writable.
Production pins the `/etc/herdr` path; environment overrides apply only to development/tests.
The updated root worker blocks upgrades/rollback until any legacy release-tree
mappings are migrated and enabled externally. An older installed worker must be
updated after root performs the migration and before initiating an upgrade.
This schema supports manual adoption; publishing still uses one global credential
pair rather than independent per-project slots. Git askpass receives
a temporary `0600` push-only credential file and accepts prompts only for the
recorded fork. Both tokens remain server-side and are omitted from snapshots,
logs, task records, and prompts. Shared Unix-user access is not OS isolation.

A publishing failure report includes the failed stage, whether the branch was
already uploaded, and the required operator action. Late failures after a
successful inspection produce new feedback. If the original session cannot
receive feedback, the dashboard retains the reservation, displays the delivery
error and retries notification every 30 seconds; it does not replay publication.

The task status and Automation settings show the delivery stage and agent report.
A failure keeps the reservation and offers **Retry verification and delivery
inspection**; explicit retry can rerun failed durable checks and reconcile an
already-published branch/PR. Disable capture/verification to disable publication
and stop requiring this barrier. An active inspection chat must still settle
before another task can use its session. No-change receipts remain terminal
without creating a pointless commit or PR.

## Fixing stale projects from a discussion

Each new agent launch allocates `.ci-cache/herdr-scratch/<execution-id>` inside
its assigned checkout. Git repositories receive a local `info/exclude` entry,
without changing tracked ignore rules; a tracked reserved scratch path is refused.
The gateway exports `TMPDIR`, `TMP`, `TEMP` and `HERDR_SCRATCH` in the newly owned
shell and waits for its activation marker before starting the agent. Failure
stops launch. Scratch is per execution, retained across an inspected restart of
the same execution, and never automatically deleted with potentially useful
diagnostics. Non-Git sessions also receive scratch space. These directories do
not isolate agents sharing the same Unix account.

Sessions receive neutral `HERDR_BUILD_JOBS` and `HERDR_TEST_THREADS` budget
signals. Adapter-derived command guidance follows their frozen SDK bindings.
Only sessions bound to `rust-toolchain` receive a Cargo wrapper and Rust budget
aliases; sessions without a bound policy receive generic guidance. Interactive
wrappers currently support Cargo only. Gateway validation shares its capacity
lane across every toolchain and recipe; the console
reports queued/running state and **System health** shows waiting work and the
current budget. See [Build capacity](build-capacity.md) for resource thresholds,
scope, recovery and the distinction from hard container limits.

Launch instructions, the agent console, each live discussion terminal and the
facilitator's member workspace context name each session's scratch path in both
dashboards. Temporary pinned review copies must use that path and
preserve repository-relative file names, or use direct `git show` reads instead.
Read-only discussions allow those scratch writes without authorizing source
changes or installation. No general `/tmp` or home-cache access is granted.

Any missing project SDK is an environment blocker, not a reason for
the project agent to bootstrap a replacement. Use **Plan environment** and
**Prepare SDKs** in the project's environment card, or enable the organization's
**Prepare supported project SDKs before agent launch** policy. Verified preparation
is adopted by a new launch. Project agents retain the Herdr coordination CLI;
they receive no dashboard administrator credentials or new provisioning authority.
For an already-blocked shared SDK-cache bootstrap, reject/cancel that operation
and prepare the environment before relaunching. The gateway never auto-answers
these permission prompts, and old sessions do not gain the new scratch exports.

Worktree launches do not automatically grant external-directory access to the
main checkout, including discussion sessions. Read pinned committed evidence with
`git show <SHA>:<path>` from each participant's own `launch_directory`: worktrees
share Git objects, so this does not require changing the source checkout. The
configured source path in workspace context identifies the repository, not a
permission grant or the directory to use for file access.

Only protocol output folders and explicitly configured accessible paths are
added to OpenCode's external-directory rules. These rules inherit other tool
permissions; they are not read-only grants. Discussion scope is an instruction,
not filesystem confinement. Operators should consider edit and shell permissions
when explicitly allowing external paths. Existing sessions retain their original
runtime permissions until relaunched; updating Herdr cannot revoke their saved
Allow always decisions.

Session scratch and any installed wrappers require a writable assigned checkout and
Git exclusion metadata. Read-only mounts and tracked reserved scratch paths are
refused explicitly; use a writable project clone/worktree for managed sessions.
There is no implicit fallback to a shared home or temporary directory. This keeps
scratch inside the session's existing directory grant. General conversation does
not require Git project content, but its launch still needs writable scratch.

To answer a blocked member's permission prompt, expand **Live discussion**, then
the member's terminal, and choose **Respond to permission or question**. The live
view retains the tail (up to 20,000 characters) so current permission prompts
remain visible instead of older history. Review
the selected OpenCode option before confirming. For a dashboard output directory,
select **Allow once** with the terminal controls if needed. Confirmation sends
Enter only if the original conversation is still blocked on the exact inspected
prompt. A changed prompt requires a fresh inspection. This can unblock a member
while the facilitator is still running; it does not restart the discussion or
change the saved permission policy. Saved permission changes apply on the next
launch.

An unsubmitted repository review shows each session project mismatch directly
in the group post: agent name, saved session project, current configured project
and group target. Administrators can choose **Archive and relaunch in current
project** to preserve and close an idle stale session and launch a fresh one from
its current profile. The gateway immediately binds the replacement to the group
and durably schedules continuation when all mismatches are resolved. It waits for
member startup, idle sessions and admission capacity; no manual retry or open
browser is needed. Saved launch records also recover continuation after a gateway
restart. Group notifications refresh progress. A failed startup or preparation
remains visible for inspection instead of repeatedly replaying prompts. Cancelled
or already-submitted discussions never resume through this recovery path. It does
not restart in the retained, outdated checkout.

**Force realign session** confirms an explicit stop of that stale session even
when it is working or blocked. It preserves visible terminal output and checkout
files before closure and records the operator and stop request.
The archive and its viewer explicitly label this as a partial visible-screen
capture (up to 160 lines), omitting earlier history and possibly ending mid-reply.
The character limit and any additional truncation are recorded too. It still
refuses task-owned sessions, other job reservations, ambiguous identities and failed
archives. Submitted discussions cannot use either action. Partial recovery stays
visible and retries reuse the recorded launch request instead of duplicating a
session. If current profile projects themselves disagree, update group/agent
settings first; these controls do not silently redirect agents to another project.
