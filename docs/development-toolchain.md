# Development toolchain and missing tools

This page describes the current toolchain/provisioning implementation. The
[automatic project environment implementation plan](automatic-project-environments.md)
defines the work required to prepare managed repositories end to end, including
.NET, Rust, multiple SDK versions, dependency restoration and automatic resume.
The current changeset implements SDK preparation for exact .NET/Rust versions,
frozen session guidance and native validation under those bindings, with a shared
publication gate. Opt-in automatic launch preparation parks work before runtime
pane creation and resumes only its recorded execution after SDK verification.
Interactive shells inherit the environment; conflicting component SDKs use one
cwd-aware dispatcher. Discovery reads alone do not prepare an environment.

## Who provides which tool

There are separate provisioning scopes:

1. **The project's own pinned build toolchain** — Flutter 3.47.9 and its bundled Dart SDK, installed
   by the root-run `install/dev-tools-install.sh`. The Flutter files are owned
   by `herdr` and symlinked into
   `/home/herdr/.local/bin`. The dashboard's SDK service requests it because
   `scripts/build-web.sh` cannot produce the dashboard without it. The dashboard
   detects the configured executable file; that establishes presence, not its
   actual version or usability. Installation and builds verify the required pin.
2. **Managed project SDKs** — exact .NET/Rust SDKs can be planned and queued by
   an administrator from either project explorer. Trusted publisher archives are
   hashed, unpacked and probed without root in a versioned store. This does not
   enforce the interactive agent's process environment. Frozen launch guidance
   names the prepared tools; independent validation probes and runs explicitly
   approved native checks under those component bindings.
3. **A tool one task happens to need** — a linter, a code generator, a
   language-specific checker. The worker obtains it within task permissions,
   in a task-specific cache or environment. Pin the repository-required version
   and verify release checksums or package-manager integrity, then report it.

Adding a task-specific tool to the root SDK service is not a small change. If it
also becomes a required check in `scripts/build-web.sh`, a provisioned toolchain
turns into a precondition for every integration event and every dashboard build,
because `validation.py` runs the repository's own scripts to verify each worker
merge. Project SDKs use their own preparation adapters; other task tools stay
out of the root catalog and are obtained within task permissions.

## The catalog

`install/toolchains.json` is the release's list of provisionable toolchains. It
ships with the release, is root-owned, and is the only place a toolchain's
installer is named:

```json
{ "flutter": { "label": "Flutter",
               "version_source": { "file": ".fvmrc", "key": "flutter" },
               "install_root": "/home/herdr/.local/share/flutter-{version}",
               "executable": "/home/herdr/.local/share/flutter-{version}/bin/flutter",
               "companion": "/home/herdr/.local/share/flutter-{version}/bin/dart",
               "script": "install/dev-tools-install.sh" } }
```

- **The catalog carries no version.** `version_source` names the file the
  repository already maintains — `.fvmrc` here — and the catalog substitutes
  whatever it declares into `install_root`, `executable` and `companion`. A
  version copied into this file would be a second pin to keep in step, and the
  project would silently build against whichever copy was forgotten.
- The install root is versioned, so an SDK of another version on disk reads as
  absent rather than present.
- `script` is relative to the release root. The root worker refuses an absolute
  path, a `..` traversal, or a symlink, so a name can never become arbitrary
  root execution. A request still accepts no installer options, and it cannot
  supply a version: the worker reads the declaration itself.
- After installing, the worker probes the installed executable and fails if it
  reports anything other than the declared version. An installation that
  succeeds while producing the wrong SDK is a failure, not a completion.
- Adding a toolchain requires a catalog entry, an installer and a version probe
  appropriate to that SDK. The current installation probe supports Flutter.
  The gateway rejects malformed catalog entries together and reports a catalog
  error; it does not hide bad entries and then guess among the remaining ones.

A request names the toolchain it wants. An older gateway sends a bare
`{'action': 'install'}`, which is accepted only while the release provisions
exactly one toolchain; with several, a nameless request is refused rather than
silently provisioning whichever sorts first. Automatic installation is refused
for the same reason, even if only one of several declared toolchains is missing.
The blocked build records the refusal once instead of stalling without explanation.

Provisioning is separate from selection, and selection is no longer a hardcoded
default: `web/gateway/toolchain_policy.py` resolves the approved policy from the
repository's own committed declarations, and that is what names the toolchain in
the build identity.

## The approved policy

`toolchain_policy.resolve_committed(checkout)` reads the committed declaration, the check scripts and
the lockfiles, and returns one canonical structure. Hashing it gives a digest that
a session, a verification run and a publication gate can all compare:

- The policy is resolved from the **approved checkout**, never from a candidate
  commit. A candidate that edits `.fvmrc`, CI, a check script or a lockfile is
  changing the rules it will be judged by, so `changes()` reports those edits for
  explicit review instead of adopting them.
- Sessions freeze the policy when work starts, recording it on the job with its
  digest and the executable paths. A later change to the declaration is then
  visible as a change to verify against rather than one that silently applies to
  work already in flight.
- The launch prompt states the rule plainly: use these tools, report a mismatch
  instead of switching SDKs, and propose an upgrade as a separate change. This is
  instruction, not enforcement — see the limit below.

## Verification evidence

Verification selects the approved SDK itself, probes the real executable, and runs
the repository's scripts with that SDK first on `PATH`. A declared
`flutter-3.47.9` cannot prove which executable ran, so the run records what it
actually measured:

- the exact candidate commit,
- the approved policy digest and any policy changes the candidate made,
- each toolchain's required version, actual version, executable path and whether
  they match,
- the required check results.

Those fields travel into the artifact manifest, the recorded result and the task,
so a delivery is authorised by evidence rather than by a label. If a probe does
not match, verification does not run at all: it records a `missing_toolchain`
blocker and refuses with what was required and what was detected.

Automatic publication then requires passing evidence for the current commit **and**
for the currently approved policy. A mismatch keeps automatic delivery pending and
records why; the automatic delivery workflow reports the blocker to its waiting
agent instead of leaving an unexplained pending delivery. A missing SDK or a provisioning failure is an
operator action; a code failure follows the existing bounded repair workflow.

## A limit worth stating

Agents share the `herdr` Unix user, and launch settings cannot guarantee which
executable every command a session runs will pick. Session prompts name the
approved executable paths. Verification puts the SDK
first on `PATH`; session launch does not yet bind the agent environment. These
settings do not confine the agent. Automatic delivery requires passing
verification under the approved toolchain. Confining execution itself needs
protected SDK installations and isolated environments per session, which is a
larger change than this one.

## The request chain

Three files, easy to confuse by name. Only one writes each file:

| Step | File | Writes or reads |
| --- | --- | --- |
| Dashboard queues a request | `web/gateway/sdk_install.py` | writes `/var/lib/herdr-sdk-requests/request.json` |
| Root worker consumes it | `install/sdk-install.py` | reads and unlinks that request, resolves the named toolchain through `install/toolchains.json`, runs its installer, and is the **only writer of `/var/lib/herdr-sdk/status.json`** |
| Gateway reports readiness | `web/gateway/sdk_install.py` | reads `status.json`, the release catalog, and each toolchain's probe |

An agent never runs these installers. An administrator can request provisioning
or opt into the existing automatic SDK policy; the root worker executes it.

In the dashboard, provisioning lives under **Herdr build tools**, next to the
other host-environment cards on the CLI accounts page (`/configuration`). It is
scoped and labelled as Herdr's own build requirements because it concerns
building Herdr itself from source, not any one repository. That label matters:
a project may be written in any language, and a card titled after "development
toolchains" reads as a statement about the project the operator has open.
The card refreshes every five seconds while installation is queued or running,
and periodically while idle. **Refresh toolchain status** requests an immediate
update. Failed requests remain visible after refreshing; completed installations
offer their bounded output for inspection.

## Environment plans

An environment plan describes what preparing one project would involve: one
exact version per requirement, where each came from, and what Herdr can actually
do about it. The pure plan builder transforms the resolved report without
executing commands. Analysis can probe installed executables. The explicit
preparation-plan POST also resolves trusted publisher metadata; it does not
download SDK archives or run project scripts. Polling never enqueues provisioning.

Four requirement states, used by both dashboards and the preparation worker:

| State | Meaning |
| --- | --- |
| `ready` | An installed version satisfies the declared constraint |
| `acquire` | A version exists to install and would satisfy it |
| `unsupported` | Herdr cannot obtain a satisfying version on this host |
| `unresolved` | No exact version could be chosen from the evidence |

The distinction between `unsupported` and `unresolved` is deliberate. A project
that declares `.NET 8.0.100` and a host with no .NET installer is `unsupported`:
the version is known and nothing is queued, so the next step is a manual install
or a new adapter. A project that declares nothing usable is `unresolved`, and the
next step is to declare something. Collapsing them would either hide a real gap or
queue an installation on no evidence.

`environment_capabilities.py` advertises support per capability and per operation
(`detect`, `resolve`, `acquire`, `activate`, `verify`, `restore`, `check`) rather
than with one universal flag. A recognised language is not a prepared capability:
discovery can read a requirement without having its installer. Exact .NET SDK
and Rust compiler/Cargo/host-standard-library acquisition and activation are
implemented for Linux glibc x64/arm64. Real installation qualification is a
separate CI release prerequisite, also available through manual dispatch, and
is pending its first run. Every registry entry states
which operations exist; native checks require explicit selection in an approved
plan. A successful SDK preparation job does not claim those checks ran.

`environment_plan.py` selects a version by fixed precedence: an operator override,
then a repository constraint, then a single unambiguous selection. Installed
versions and installable artifacts are separate sets and are never merged, so an
already-installed version that misses the project's range is refused rather than
reused. The plan's digest covers every execution-affecting field and excludes only
the build timestamp, so the same inputs always yield the same environment and a
different digest means a different one. `environment_store.py` persists plans
addressed by that digest: re-planning after a commit is a new row, not an
overwrite of a running environment.

A plan built from a working-tree report is marked `preview` and must not authorise
installation. Administrator preparation instead freezes committed declarations
and trusted artifact metadata, persists a job and verifies SDK selection from
each component directory of the unchanged checkout. Tracked edits and new or
ignored SDK-selection files block verification; unrelated untracked files do
not. Host readiness and managed activation capability remain separate fields,
so an installed tool is not called absent merely because its adapter is missing.
The service stores
plans/jobs in `environments.sqlite3` beside the gateway database and SDKs in
`environments/`, overridable through `HERDR_ENVIRONMENT_ROOT`. It refuses to
acquire as root. Cancelled/failed work can be retried; repeated active submissions
share a job. Explicit re-preparation after completion re-probes reusable SDKs.

**SDKs prepared** means only that the SDK probes succeeded. The SDK job retains
`dependencies=not_run` and `checks=not_run`. Project checks are recorded separately
by validation, using the approved plan/policy identities, component cwd and SDK
environment. Automatic launch continuation is durable, while uncertain external
runtime acceptance is never automatically replayed. Enable **Prepare supported
project SDKs before agent launch** in organization automation. Native build/test
approval is a separate switch because those commands can execute project code.
The SDK store currently shares the gateway user, so it is not a protection
boundary against same-user agents. See
[the implementation plan](automatic-project-environments.md) for the remaining
work and the real installation qualification command.

## Project requirements are a separate scope

Project discovery reads repository evidence without requiring an operator to name
an SDK. The release catalog describes Herdr's own installer capability, not what
every hosted project needs. Overrides use the same constraint shape as discovery.
The project explorer reports requirements, unresolved evidence, host observations
and explicit preparation/activation blockers separately.

The shared `environment_adapters.py` contract supplies provider resolution,
archive layout, probes, command names and activation variables. Managed .NET and
Rust acquisition remains publisher-backed. Node.js/npm can instead be prepared by
a dedicated agent under separate administrator permission, then adopted into a
new immutable plan. Reported provenance remains an unverified claim; gateway
inspection, tree fingerprints and contextual probes establish the recorded bytes
and version evidence. See [Agent-assisted environments](agent-assisted-environments.md).

Automatic project launch waits for its exact prepared bindings. Unsupported
capabilities remain blockers; discovery does not grant installation or project
code execution. The old release-scoped SDK request chain above remains limited
to installers shipped by the release and never infers a tool from a bare request.

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
denials use `missing_permissions`. The SDK service provisions only the
toolchains in the release catalog; it cannot fix arbitrary tool blockers.
Agents share the `herdr` account and home, so use task-specific
caches/environments and never overwrite shared tools.

The report path already exists: an agent's summary reaches the dashboard
through the existing integration event, so an agent-installed tool is visible
without new plumbing.

## Historical events

Provisioning a tool never rewrites history. An event that was recorded as
merged with validation pending stays pending until somebody retries validation
for it; enabling automatic provisioning does not convert an old event into a
pass and never waives a check.

Candidates that edit protected declarations, validation scripts or a protected
lockfile are blocked for explicit review. This change does not add an
in-dashboard policy-approval action: an operator must review and adopt the policy
in the approved repository before rerunning verification. Manual reviewed
publication retains its existing operator-controlled behavior.


### Project discovery access and SDK lookup

Both dashboards show **Discovered requirements** at a repository root in the
project explorer. They share discovery requests and override-failure handling.
Re-analyse bypasses the working-tree cache; source scope, revision, assessment
time and partial coverage remain visible. A rejected override save retains its
draft in the editor.

The cards distinguish an installed executable from a satisfied version rule:
without a declared range or selection they show **Installed — required version
unknown**. Flutter dependencies yield conventional `flutter test` and
`flutter analyze` suggestions; pure Dart packages keep Dart commands. Repeated
requirement explanations are shown once. **Unrecognised files** expands a bounded
sample of up to 50 paths, with the displayed and total counts shown explicitly.

Working-tree census and manifest reads share descriptor-anchored access with the
file explorer. Every directory is opened relative to a pinned parent, symlinks
are refused, and only regular manifests are read within the byte budget. Native
access requires a Linux gateway and fails closed on unsupported platforms.
Committed discovery continues to use the pinned Git tree.

Flutter components also consult the nearest `.fvmrc` in their ancestor chain,
starting at the component directory and stopping at the repository root. This
models [FVM's ancestor lookup](https://fvm.app/documentation/guides/running-flutter)
within an explicit boundary; it does not prove a later command invokes FVM.
A nearer unreadable pin stays unresolved instead of falling back to an ancestor.
Pure Dart packages do not acquire a Flutter requirement just because an ancestor
pins Flutter. Host assessment checks selected versions as well as compatibility
ranges, across all components. Python probes include both `python3` and `python`;
an installed runtime still does not establish an undeclared project version.

.NET SDK discovery starts its `global.json` search at the component directory
unless the analyzer receives an explicit CLI lookup directory. This is a stated
lookup assumption, not proof of the cwd a later build will use. The nearest
file shadows ancestor files even if malformed or missing `sdk.version`. Reports
retain the context, effective evidence, shadowed declarations and repository-root
boundary; host configuration above that boundary is not read. `rollForward`,
`allowPrerelease` and `paths` are retained as policy. SDK selection against
installed SDKs remains unresolved rather than flattening those policies into a
range or claiming a gateway-cwd probe proves compatibility.


### Verifying project discovery on the correct host

A Windows failure at `open_project` proves that the boundary fails closed; it
does not verify Linux traversal. Do not mock `ANCHORED_ACCESS`, `O_NOFOLLOW` or
`dir_fd` to make the security tests run. The existing **Installer and gateway
checks** job runs on Ubuntu 24.04 and now checks the real native prerequisites
and discovers the full Python suite through the strict runner. Any skip fails
the job, including tests added outside the focused module list.

From a disposable Linux checkout with Python 3.11+, Git and Bash installed, run:

```sh
python3 scripts/test-project-discovery.py -v
```

The focused runner includes access races, explorer, census, requirements,
committed/working-tree analysis, constraints, .NET lookup, toolchain assessment
and the entire coordinator suite. It rejects unsupported hosts and any skipped
test. Its preflight checks the actual temporary filesystem: renaming a pinned
directory must preserve the original read, and reopening its replacement symlink
must be refused. Use native Linux storage for temporary fixtures; a Windows bind
mount can have different rename semantics. The output identifies Python, kernel,
uid, temporary storage and a scoped fingerprint of the current runner, gateway
Python, Python tests and bundled skill Markdown bytes. This fingerprint does not
cover every full-suite fixture, installer input or execution environment.
Git HEAD alone would not identify an uncommitted or staged local run.

For the complete integration gate, run:

```sh
python3 scripts/test-project-discovery.py --all
```

These checks create disposable fixtures and require neither a running gateway
nor a real Proxmox host. A deployment smoke check is separate: on a test gateway,
run the focused runner from a disposable checkout as the gateway service user,
then use the authenticated project explorer to analyse a representative checkout.
Confirm readable manifests, explicit permission failures, working-tree freshness,
and committed reports that remain unchanged after an uncommitted manifest edit.
This exercises the installed Python, service permissions and actual project
storage. It does not require enabling automation or installing any project SDK.
Do not run a deployment smoke test against a live task's checkout.
