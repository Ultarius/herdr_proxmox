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
an agent card to edit its persona. **Team** contains the roster and launch controls;
**Runs** contains launch/delegation history. Status dots reflect matching live
Herdr run bindings, with gray indicating no matching status.

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
