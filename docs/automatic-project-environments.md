# Automatic project environments: implementation plan

Status: partially implemented in the current changeset, 2026-10-17. The full
automatic launch/validation path below remains the target, not a shipped claim.

Implemented now:

- A shared SDK adapter contract and opt-in Node.js/npm agent-assisted adoption.
  See [Agent-assisted environments](agent-assisted-environments.md) for the
  permission boundary, lifecycle, provenance claims and current limitations.

- Immutable, evidence-bearing plans per component, with persisted identities.
  Different components can select different versions of one SDK.
- JS/TS source-only components beside non-Node manifest-backed projects are
  treated as assets rather than mandatory Node runtimes. The report and plan
  expose this decision as `source_components`; both dashboards use that decision.
  A Node manifest anywhere in the repository or an explicit Node override retains
  the Node gates. Inferred Python, Go, Rust and other languages keep their SDK
  requirements, as do manifest-free JavaScript repositories.
  This is a heuristic that can also exempt a source-only Node server or tool;
  the dashboard notice explicitly tells operators to declare or override that
  runtime when needed.
- Committed discovery applies the same dependency tree, build output and vendor
  exclusions as the working-tree walk, so a vendored third-party tree cannot
  become this project's runtime requirement or move a repository off Herdr's own
  self-build route. Ignored paths have a separate bounded enumeration budget
  and do not spend the authored-file budget.
- Opt-in organization **Prepare supported project SDKs before agent launch**
  admits launches before any runtime pane exists. Planning and downloads run on
  the preparation lane, not organization launch workers. The starting commit,
  permission revision and intended execution are recorded before work is queued.
- **Approve supported project build/test recipes automatically** is a separate
  opt-in. It selects only the adapter's bounded native build/test proposals.
  Node's declared build/test scripts are also eligible under this separate
  permission. Neither declarations alone nor the old `auto_sdk` setting imply it. Without check approval, SDK preparation can succeed while candidate
  validation still explicitly requires check selection.
- Explicit administrator actions in both project explorer dashboards:
  **Plan environment**, then **Prepare SDKs**, with progress, cancellation and
  retry. Discovery still starts without entering a tool name.
- Trusted publisher metadata and checksummed acquisition for exact, three-part
  .NET SDK and Rust toolchain versions on Linux glibc x64/arm64. Installations
  coexist by artifact identity; acquisition runs without root and changes no
  global SDK selection. Rust prepares the compiler, Cargo and host standard library.
  Minimal Rust profiles can also request `rustfmt` and `clippy`: their exact-version
  publisher archives are independently checksummed and their commands are probed.
- Cargo projects can propose headless build/test pairs from an exact
  `cargo test --no-default-features` or `cargo build --no-default-features` in a
  fenced README example. Both proposals carry that README as evidence, including
  the companion operation derived from it. Shell expressions,
  extra arguments and graphical smoke commands are not imported. These are still
  proposals requiring administrator approval (or the separate automatic-check
  permission). The README is frozen as a protected policy input when selected.
- Durable jobs, leases, restart recovery, cancellation fencing, atomic staging
  and reuse probes. Manual preparation requires the clean checkout's exact HEAD.
  Automatic preparation exports only the recorded commit's SDK-selection blobs
  and component directories into a disposable verification context. It runs no
  checkout hooks, smudge filters or project scripts and never moves shared HEAD.
  For manual preparation, unrelated untracked notes are allowed; new or
  ignored SDK-selection files that could shadow committed evidence are blocked.
  A failed or cancelled generation is not ready.

The preparation receipt certifies **SDK preparation only**. Separate validation
runs can now execute administrator-approved native .NET/Rust check recipes with
the prepared component bindings and record real check evidence. Their commands
can restore dependencies as part of native build/test execution; a standalone
restore job is not implemented. A prepared compatible SDK policy now configures a
fresh agent shell through Herdr's native tab `--env` contract, with per-execution
Cargo/.NET CLI caches and the requested binding recorded on the launch job.
Trusted exports are reapplied after shell initialization; a completion marker
prevents agent start from racing them. Before task delivery, the foreground
agent's actual Linux process exports must match, with PID/start-time fencing.
Different .NET/Rust component SDKs use one dispatcher per command. It selects the
nearest approved component from the invocation cwd, then execs that component's
absolute SDK with its own environment and caches. Run from the component
directory: a `--project` or `--manifest-path` argument does not select a component.
Unknown scopes, conflicting selections within a component and modified dispatcher
manifests are refused. Shared SDK archives are not merged or overwritten.

Waiters reference exact preparation jobs/generations. Safe waits and authorized
but unsubmitted launches recover after restart; graceful shutdown requeues SDK
work. Cancellation, permission revocation and changed task/profile identities stop
continuations. Runtime acceptance followed by an unknown response remains an
inspected recovery case, never an automatic repeated prompt.

Neither a green discovery assessment nor an SDK receipt proves project checks ran.
Managed files currently share the gateway
Unix identity: hashes authenticate downloaded archives, while reuse probes detect
usability/version failures; this is not isolation against another process running
as that same user, or an integrity measurement of the entire installed tree.

Installed SDK availability and managed activation are separate plan fields. For
example, installed Node can be `ready` while `activation=unavailable` blocks a
mixed-project preparation job. Existing host measurements are reused, including
alternative measured executable versions; absence of an adapter never erases them.

Current refusals are explicit: unresolved/moving SDK versions, Rust extra targets,
optional components other than rustfmt/clippy and non-minimal profiles, custom .NET SDK search paths,
unsupported executor platforms, and capabilities without a managed adapter. A
mixed .NET/Node project remains blocked until both capabilities can be prepared.

Deterministic preparation tests and strict native Linux tests have run. Real
publisher metadata was resolved and Rust archive layouts inspected, but the new
real SDK installation/build qualification has **not run** on a glibc executor.
The pinned Herdr 0.9.5 daemon has run locally in isolated HOME/socket state with a
credential-free test agent and fixture SDKs, exercising admission, inheritance,
native recipe execution and the actual publication evidence gate. This qualifies
the runtime protocol, not the publisher SDKs.
Run `python3 tests/ci/project_environments.py` as an unprivileged Linux glibc
user, or dispatch CI with **run_project_environments** enabled. The same job now
runs on release-producing pushes on x64 and arm64 and is a prerequisite of publication. It
downloads pinned fixture SDKs and performs a real .NET restore/build and offline
Cargo test plus real rustfmt/Clippy acquisition, probes and commands in disposable
repositories. Its results qualify adapters; they do not
turn SDK receipts into project validation evidence. Qualification also runs the
actual product validation engine and publication evidence gate. Missing SDK receipts
cause the existing unverified directory to be quarantined for inspection and
the publisher artifact to be acquired again; no existing bytes are certified
without a matching receipt and successful probes.

`python3 tests/ci/live_environment.py` additionally combines automatic admission,
real publisher SDKs, a checksum-pinned real Herdr daemon and a test agent that
requires no model credentials. The daemon executable/version and SHA-256 values
are pinned in `tests/ci/herdr-runtime.json`. The process-level daemon double in
`tests/ci/environment_daemon.py` supplies real PTYs/children for deterministic
per-push recovery tests; it is explicitly not evidence of vendor semantics.

## 1. Product outcome and meaning of “any project”

Point Herdr at a repository and assign work. Herdr discovers its components,
derives requirements, prepares suitable execution environments, verifies them,
and starts or resumes the work. Operators do not type tool names to begin this
process. Installing Flutter for Herdr itself is independent of this workflow.

The automatic path is:

```text
repository + task
  → bounded inventory and evidence
  → component requirements and SDK selection rules
  → host/executor compatibility
  → immutable environment plan
  → reuse or install SDKs and prepare dependencies
  → verify from each component's actual execution context
  → start/resume agent work
  → validate the exact candidate using the same environment contract
```

“Any project” means an extensible system that can discover arbitrary repositories,
automatically prepare supported requirements, and explain unsupported or
unresolved ones. It cannot promise every proprietary SDK, operating system,
hardware device, service or computed build configuration on every host. Support
must be advertised per ecosystem, version, platform and operation, not as one
universal “supported” flag. A newly recognized language must not silently imply
an installer or an executable validation recipe exists.

For supported projects with resolvable requirements and an allowed executor,
missing tools should be installed automatically before build-dependent work
starts. A failed build is a recovery trigger, not the normal discovery mechanism.
Browsing and editing may remain available while preparation is blocked.

## 2. What exists, and what is missing

| Area | Existing implementation | Work needed |
| --- | --- | --- |
| Inventory | `project_inventory.py`, anchored access, coverage and unknowns | Keep bounded scans; expand ecosystem evidence without running project code |
| Requirements | `project_requirements.py`, `dependency_constraints.py`, evidence and conflicts | Native selection policies, inheritance/workspaces, targets, components and host dependencies |
| Reports | `project_analysis.py`, committed/working-tree reports and caching | Preserve per-component decisions and expose an executable preparation plan |
| Host probing | `project_toolchains.py`, version measurements and installer offers | Structured ecosystem probes, contextual selection and all usable installations |
| Installation | `sdk_install.py`, `install/sdk-install.py`, `install/toolchains.json` | Project-scoped exact versions, multiple tools, durable jobs and trusted adapters |
| Automation | `integration_coordinator.py`, `auto_sdk`, toolchain blocker recovery | Preparation before launch, dependency scheduling, verified completion and bounded resume |
| Launch | Task/session creation through `contributions.py` and runtime launch paths | Bind a resolved environment to every agent execution and subprocess |
| Validation | `toolchain_policy.py`, `validation.py`, exact-commit/policy evidence | Ecosystem recipes, component working directories and the same environment contract as launch |
| UI | Shared analysis loader and both project cards | Preparation policy, progress, blockers and environment receipts |

The current installer request carries a tool name. Its privileged worker reads
the version from the deployed Herdr release, and its probe is Flutter-specific.
Adding `dotnet` or `rust` to that catalog would not make it install the versions
required by a managed project.

The coordinator currently tries to identify one installable tool after a
toolchain blocker. A .NET API plus a Node frontend needs several tools, and a
monorepo may need multiple versions of the same tool. Neither case should be
reduced to “pick one SDK.”

Validation retains legacy repository script paths for compatibility and now
consumes approved per-component .NET/Rust recipes through the shared environment
policy. Installing Cargo alone still does not approve a check: the administrator
selects the frozen proposal, and validation probes the SDK before running it.

## 3. Keep five kinds of requirement separate

| Kind | Examples | Responsible layer |
| --- | --- | --- |
| SDK/runtime | .NET SDK, Rust compiler, Node, Python, Flutter/Dart | Trusted tool adapters |
| Package manager and development tool | Cargo, npm/pnpm, Composer, project-local linters | Adapter plus project dependency preparation |
| Project dependency | NuGet packages, crates, Python packages, npm packages | Ecosystem restore inside the task environment |
| Native/host capability | Linker, C compiler, ICU/OpenSSL libraries, Windows SDK, GPU | Host capability registry or suitable execution image |
| External service | Database, registry, emulator, private package feed | Explicit service/credential binding or an actionable blocker |

An SDK being present proves none of the other rows. Restoring packages can also
execute repository or dependency code. Discovery must remain static; restore,
build and tests run only in an authorized unprivileged execution environment.

## 4. Shared contracts

### Requirement

Retain a list per component and invocation context, containing:

- canonical capability ID, such as `dotnet-sdk`, `rust-toolchain` or `node`;
- purpose, evidence paths/fields, file hashes and source revision;
- compatibility constraints with their notation;
- selection rules separately: exact pin, channel, ancestor lookup and policies;
- targets, optional/required components and relevant host capabilities;
- basis: repository declaration, source inference or organization policy;
- unresolved/conflicting evidence without filling in a version by guesswork.

Do not put SDK-selection semantics into a generic SemVer string. Keep the shared
constraint evaluator for compatible range languages and add ecosystem resolvers
for policies such as .NET SDK selection or Rust channels. All callers use the
same resolver result; dashboards, launch and provisioning must not invent
separate interpretations.

Normalize Rust's currently separate compiler/toolchain observations into one
toolchain capability with `rustc` and Cargo as provided executables. Distinguish
SDK identity from command spelling: `python3` can provide Python; `rustc` is not
an executable named `rust-toolchain`.

### Environment plan

A plan is persisted before execution and includes:

- project/repository identity, task, approved source revision, component and cwd;
- requirement digest, approval-policy revision and resolver/adapter versions;
- executor OS, architecture, libc/ABI and required native/service capabilities;
- exact SDK/package-manager versions and immutable artifact identities;
- separately versioned environment bindings for different components;
- operations with dependencies: acquire, verify, activate, restore and check;
- permitted network sources, privileges, disk/time budgets and cache policy;
- unresolved prerequisites, blocked operations and their specific remedies;
- recipe digest, expected probes and the exact resume target.

The plan digest covers all fields that can affect execution. Artifact URLs are
resolved by a trusted provider and validated again by the installer; a repository
does not get to choose a privileged script, command, URL or destination path.

### Receipt

Record the plan digest, exact versions, artifact hashes, adapter identity,
executable locations, execution cwd, host identity, measured selection and probe
results. Dependency restoration and test results are separate evidence. An
installer exit code of zero is insufficient to mark an environment ready, and
environment readiness is insufficient to mark project tests passed.

## 5. Resolve versions and components before choosing installers

1. Inventory the approved commit for execution decisions. Working-tree reports
   remain useful previews and must not authorize privileged installation.
2. Build component/workspace relationships and recorded invocation directories.
   Scope each ancestor rule to its actual ecosystem and stop at a disclosed
   repository boundary.
3. Determine required capabilities for the requested operation. An unrelated
   unsupported mobile component need not block an independent backend unit test;
   a whole-repository validation must account for every required component.
4. Enumerate installed SDKs and available trusted artifacts for the target host.
5. Apply exact pins and ecosystem policies first. For ranges, apply a documented
   deterministic selection policy, record the selected exact version and freeze
   it for that task. Do not silently roll an active task to a newer release.
6. Partition incompatible component requirements into separate environments when
   the build permits it. If one invocation genuinely requires incompatible
   versions, report a conflict rather than hiding it through separate probes.
7. If source inference establishes a language but no version, either stop with
   an unresolved version or use an explicitly enabled organization baseline.
   Label the latter `basis: policy`; never call it repository evidence.

Installed versions and available downloads are different sets. Record which
rules produced a chosen version. Cache remote version metadata with a revision
and timestamp; a read-only dashboard poll must not perform installs, start jobs,
or repeatedly refresh remote metadata.

## 6. .NET adapter

### Discover and resolve

Recognize projects/solutions, `global.json`, relevant shared MSBuild files,
NuGet/tool configuration and workloads. Resolve the SDK from the recorded CLI
directory and nearest applicable `global.json`. Preserve `rollForward`,
`allowPrerelease` and supported `paths` semantics. A target framework is not an
exact SDK pin; computed MSBuild configuration needs an isolated evaluation step.
These distinctions follow the [.NET SDK selection contract](https://learn.microsoft.com/en-us/dotnet/core/tools/global-json).

### Prepare and verify

- Enumerate SDKs with the selected host's `dotnet --list-sdks`, not just its
  single default `--version` response.
- Resolve an exact permitted SDK artifact, install side by side, and record its
  trusted metadata and digest. Use a release-owned adapter, not a command found
  in the repository. Microsoft's [install-script interface](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-install-script)
  supports exact versions and a private installation directory, but does not
  supply native OS dependencies or persistent environment activation.
- Bind the intended host executable, SDK roots and relevant variables for each
  component. Then run selection probes from the exact cwd with the real
  `global.json`; check the SDK actually chosen rather than assuming `PATH`
  determines selection.
- Treat workloads, native libraries and project-local tools as explicit plan
  operations. Restore packages using the repository's lock/restore policy and
  scoped credentials; keep repository targets away from privileged execution.
- Select `restore`, `build` and `test` recipes for the identified project or
  solution. Do not assume every solution contains tests or supports the same
  test command/options.

A Linux-compatible API should prepare and run on a supported Linux executor.
A Windows-specific application may require a Windows executor for execution or
tests; assess cross-compilation separately instead of claiming a Linux SDK alone
makes every Windows workload usable. An absent suitable executor is a concrete
platform blocker, not a request to install more Linux SDKs.

### First acceptance scenario

A clean supported Linux executor receives a .NET-only repository with an exact
SDK selection and a test project. Herdr installs the SDK, verifies contextual
selection, restores dependencies, runs the selected tests and resumes the task
without a tool-name form or an operator shell command. A second task reuses the
verified installation. A different SDK selection can coexist without changing
the first task's environment.

## 7. Rust adapter

### Discover and resolve

Read Cargo workspace relationships, `Cargo.toml`, `rust-toolchain.toml`, legacy
`rust-toolchain` and relevant `.cargo` configuration. Cargo's `rust-version`
declares a supported compiler floor, not an exact selected toolchain; preserve
that distinction using the [Cargo contract](https://doc.rust-lang.org/cargo/reference/rust-version.html).

Honor toolchain-file format/precedence, dated channels, components, profiles and
targets. Rustup also has command, environment and directory overrides; bind and
report their effect instead of inheriting a developer's global default. Absolute
custom toolchain paths require explicit support and availability, not a guessed
download. See the [rustup selection rules](https://rust-lang.github.io/rustup/overrides.html).

### Prepare and verify

- Provide a trusted rustup/toolchain acquisition adapter with isolated state and
  verified distribution metadata. An unapproved shell bootstrap from a project
  or the network is not an installation plan.
- Resolve moving channels to an exact distribution identity before execution;
  freeze that identity, including the compiler commit and host target.
- Prepare required components and target standard libraries. A target does not
  by itself provide a compatible linker, native dependency or execution host.
- Bind Cargo/rustup state per environment. Verify the active toolchain plus
  `rustc -Vv`, Cargo version and required components/targets in the actual cwd.
  Account for rustup proxy activation and `RUSTUP_TOOLCHAIN` explicitly.
- Restore using lockfile policy and execute Cargo build/test steps unprivileged;
  build scripts and procedural macros are executable project/dependency code.

### First acceptance scenario

A clean supported executor receives a pinned Rust workspace. Herdr prepares its
toolchain and components, verifies the compiler, restores dependencies and runs
tests. A second workspace selecting another version runs concurrently without
rewriting global defaults. Missing cross-compilation linkers produce a named
native-capability blocker; they do not produce an endless rustup retry.

## 8. Ecosystem expansion matrix

These are adapter requirements, not claims that each adapter currently exists.
Every row must advertise its supported platforms and operations before becoming
eligible for automatic preparation.

| Ecosystem | Evidence to resolve | Environment to prepare | Important boundaries |
| --- | --- | --- | --- |
| .NET | Projects, solutions, SDK selection, workloads and tool manifests | SDK plus required workloads/tools and NuGet restore | SDK selection is not target-framework matching; OS dependencies remain separate |
| Rust | Cargo workspace, compiler floor, toolchain file, components and targets | Compiler/Cargo, components, target libraries and dependencies | Channels are not SemVer ranges; native linkers and build scripts need explicit handling |
| Node | `package.json`, runtime pins, workspace graph, package-manager declaration and lockfile | Node plus selected npm/pnpm/Yarn and dependency install | Keep package-manager versions separate; lifecycle scripts require an execution policy |
| Python | `pyproject.toml`, runtime pins, lockfiles and environment-manager configuration | Interpreter plus a project virtual environment and dependency tools | `requires-python` is a range; a requirements file alone need not declare an interpreter version |
| Go | `go.mod`, `go.work`, `go` and `toolchain` directives | Go toolchain, module cache and any cgo prerequisites | Make automatic toolchain switching/downloads explicit in the environment plan |
| Dart/Flutter | Pub workspace/packages, SDK constraints, FVM selection and targets | Appropriate SDK pair, Pub restore, optional target tooling | Flutter includes Dart; Android/iOS/desktop builds can need additional hosts and SDKs |
| Java/JVM | Maven/Gradle projects, wrappers and Java toolchain configuration | Selected JDK, build tool/wrapper and dependencies | Wrappers and build configuration can execute code; project evaluation is a separate step |
| PHP | Composer runtime/extension constraints, lockfile and project scripts | PHP, required extensions, Composer and dependencies | Extensions/native packages are host-specific capabilities |
| Ruby | Ruby pins, Gemfile/gemspec and Bundler selection | Ruby, Bundler and an isolated gem environment | Native gems can need a compiler and system headers |
| C/C++ | CMake/Meson/build-system evidence, compiler/target declarations and dependency-manager configuration | Compiler, linker, build system, sysroot and packages | File extensions rarely establish an exact compiler or ABI; use explicit configuration or policy |
| Other/custom | Recognized manifests, devcontainer/Nix/image declarations or an explicit adapter | A supported declared environment or remote executor | Do not execute arbitrary bootstrap files as a trusted installer; report unsupported resolution precisely |

For Node, the [Corepack project](https://github.com/nodejs/corepack) documents
package-manager selection and caching; do not assume it is bundled with every
Node installation. Go's [toolchain selection rules](https://go.dev/doc/toolchain)
include automatic switching, so a verified initial binary alone is insufficient.
Both are reasons for structured activation and post-activation probes.

## 9. Trusted installer and execution architecture

An adapter advertises `detect`, `resolve`, `enumerate`, `acquire`, `activate`,
`verify`, dependency preparation and check-recipe capabilities separately. Its
release-owned definition identifies supported platforms, trusted providers,
artifact verification, probe formats and native prerequisites. Project version
requirements do not live in this catalog.

Use ordinary unprivileged acquisition where possible. Put reusable verified SDKs
in a managed versioned store; use staging plus atomic publication and a receipt.
Agents must not be able to change a shared installation later used by another
task's verification. Keep writable caches, virtual environments and dependency
trees separate from protected SDK content.

OS package changes are a distinct privileged capability. A narrow host broker
may install approved packages for the exact supported OS, or the scheduler may
choose a suitable prebuilt executor. Repository manifests, Dockerfiles, package
scripts, arbitrary paths and environment variables are not authority to run
commands as root. Publisher metadata, versions, package identities, destinations
and adapter arguments must all be revalidated by the relevant worker.

Use separate service/worker identities or a real isolation boundary where agents
must not control plan storage, request authorization or shared SDKs. The current
shared `herdr` account cannot provide that separation merely through a plan
digest or a launch prompt. A constrained launch environment improves repeatability;
it is not, by itself, confinement of arbitrary agent commands.

Do not automatically trust and run a repository's container build. Image, Nix,
devcontainer and remote execution providers need their own policy and isolation
contracts, including secrets, mounts, network access and host capabilities.

## 10. Durable preparation, scheduling and resume

Replace the single `request.json` install slot with persisted preparation jobs
and step records. Keep Herdr's existing self-build installer available during
migration; do not reinterpret an old request as a new project plan.

Persist:

- environment plans keyed by immutable digest;
- jobs keyed by plan, executor and preparation generation;
- step dependencies, attempt count, next retry, lease owner and lease expiry;
- installation receipts and active environment references;
- task/build waiters with exact execution identity and resume state.

Use transactional claims and bounded leases. Serialize publication of the same
SDK/artifact while allowing unrelated SDK downloads and projects to progress.
An installer must not hold the organization store lock or a coordinator writer
lock during network work or subprocess execution.

```text
discovered → resolving → planned → queued → acquiring → verifying
                                                     → restoring → ready
                ↘ blocked / failed / cancelled / superseded
```

Resume only the recorded task/build after a verified ready receipt. Recheck its
revision, policy, environment digest and cancellation state; obsolete work must
not restart because an old download finished. Install completion must not retry
a successful build, duplicate an agent session or rewrite a historical failure
as a pass. Reconnects and process restarts must recover the same job/waiter.

Classify failures: ambiguous version, unsupported host, unavailable artifact,
native prerequisite, credentials, policy restriction, network failure, quota,
corrupt download, probe mismatch and project restore/build failure. Retry only
transient categories within configured budgets. Code failures use the task's
repair workflow; they do not reinstall SDKs indefinitely.

## 11. Bind launch, restore and validation to the same environment

Every execution receives an environment ID and component binding, not just
instructions to install tools. Generate a controlled launcher with the correct
cwd, executable roots, environment variables and writable caches. Use it for
agent terminals, agent tool subprocesses, dependency restore and validation.

Record and compare effective selection after activation. Rustup proxies, FVM,
Go toolchain switching and .NET contextual resolution can make the first binary
on `PATH` insufficient evidence. Re-probe before validation when shared external
installations may have changed; prefer protected managed installs for reuse.

Check recipes must be structured operations with arguments, cwd and policy:

- repository-declared recipes retain their evidence and run unprivileged;
- trusted conventional recipes remain labelled proposals until selected;
- dependencies between component checks are explicit;
- skipped/unavailable checks remain skipped/unavailable, never success;
- no recognized recipe is an explicit unresolved build plan, not an empty pass.

SDK acquisition permission, dependency-script execution permission and required
validation policy are different settings. Keep declaration and lockfile changes
visible for review rather than letting a candidate weaken its own checks. An
environment migration never silently rewrites project manifests or lockfiles.

## 12. API and dashboard behavior

The implementation currently exposes authenticated
`GET /api/projects/environment?plan=<digest>` or `?job=<id>`, plus administrator
POSTs to `/api/projects/environment/plan` (`path`, optional `revision`),
`/select` (`digest`, `checks`: a list of proposal IDs),
`/prepare` (`digest`, optional boolean `retry`) and `/cancel` (`id`). Reads never
enqueue work. Active submissions coalesce; `retry: true` creates a new generation
after a terminal job, including re-verification of a formerly ready installation.
The dashboard requests that recheck when explicitly preparing again.

The remaining full-environment API responsibilities are:

| Operation | Purpose |
| --- | --- |
| Existing project analysis GET | Evidence, requirements, host assessment and supported preparation capabilities; no install side effects |
| Plan POST | Resolve/persist a preview against an exact source revision and policy |
| Prepare POST with plan identity | Authorize/enqueue the verified plan under the existing organization policy |
| Environment/job GET | Progress, receipts, blockers, logs and freshness |
| Cancel/retry POST | Explicit job action with revision/generation checks and idempotent request identity |

Task launch calls the same preparation service automatically when the policy
permits it. Once an operator enables trusted automatic preparation within agreed
limits, ordinary supported installs should not require repeated per-tool dialogs.
Policy exceptions and unsupported environments remain visible decisions. Keep
the existing `auto_sdk` blocker-recovery setting until its replacement is
implemented and migrated explicitly; a schema upgrade must not expand prior
permission into arbitrary OS changes or project script execution.

Both dashboards should show:

- project requirement, evidence and any policy-selected version;
- selected execution environment and host compatibility;
- missing/installed/resolving/installing/verifying/ready states separately;
- “can install” separately from “installation needed”;
- dependency-restore and build/test results separately from SDK availability;
- precise blockers and actions: retry, provide credentials, choose an available
  executor, enable an allowed policy, or resolve conflicting declarations;
- plan/receipt identity and the task that will resume.

There should be a useful **Prepare environment** action for manual recovery, but
it must not be the entry point for discovery or an extra compulsory step on the
automatic path. Keep Herdr's self-build tool card in its own maintenance scope.

## 13. Implementation work packages

Current completion boundaries:

| Package | Current state |
| --- | --- |
| 1 | Planning/persistence foundation implemented and hardened; full launch-bound receipts remain part of the vertical slices |
| 2 | Exact .NET/Rust publisher adapters implemented; real install/build qualification pending |
| 3 | Durable SDK queue and task-launch waiters implemented, including leases, cancellation, restart recovery and inspected uncertain delivery |
| 4–5 | Exact .NET/Rust acquisition, automatic launch admission, contextual probes and separately approved native checks implemented; full publisher qualification runs in release CI |
| 6 | Interactive process inheritance, cwd-aware SDK dispatch, per-component validation and publication gating implemented for the managed adapters; independent validation waiters remain future work |
| 7–8 | Not implemented: additional SDK adapters, native prerequisite provisioning and alternate executors |

The runtime now consumes prepared receipts through `EnvironmentPolicies` for
frozen session guidance, contextual validation and publication checks. This
completes the policy transport portion of packages 4–6, including dynamic
fingerprints and per-component native recipe execution. Automatic task-launch
admission and safe continuation now consume the same plans.

The remaining product seam is the agent launch contract: carry a recorded component
cwd, executable bindings and environment through task/session creation into the
actual child process. A prompt naming SDK paths is not enforcement. This
initial shell inheritance is now wired through `environment_launch.configure`
and checked by `verify_process` before task delivery. It uses the documented
[tab creation environment contract](https://herdr.dev/docs/cli-reference/#tabs),
not an assumed `agent start --env` flag. Different SDK versions use
`environment_dispatch.py` to select by component cwd. This has run against the
pinned live daemon with fixture SDKs; the real-SDK matrix is a release gate.
Candidate checks consume component bindings under independently approved check
selections.

### Implemented task-launch admission and durable continuation

`environment_admission.py` is shared by explicit launch and the task scheduler.
Its contracts are:

1. Resolve the recorded starting commit, derive its component plan and apply an
   administrator-approved preparation/check policy. Preserve the exact selected
   recipes and policy identity. Discovery reads never enqueue work, and the old
   `auto_sdk` setting does not grant new project preparation authority.
2. Return `ready`, `waiting`, or `blocked`. Ready means verified bindings for
   every required component on the selected executor. Unknown selections,
   unsupported capabilities and conflicting approvals remain named blockers.
3. Persist a waiter before queueing preparation. Its identity includes task,
   execution, starting commit, plan/check-policy digest, executor, preparation
   generation and the intended next action. A waiter contains an enum action,
   never shell command text. Concurrent requests reuse the same active waiter.
4. Keep waiting outside organization workers and repository locks. A preparation
   completion wakes admission; it does not directly start an agent. On restart,
   reconstruct pending waiters from storage and reconcile preparation state.
5. Claim a ready continuation transactionally. Recheck task cancellation,
   assignment, source commit, policy, preparation generation and session ownership.
   Superseded or cancelled waiters cannot start work when an old download ends.
6. Use the existing launch request identity and persist the transition before the
   external CLI call. After an ambiguous acceptance or crash, reconcile the
   recorded pane/session before another call. SQLite cannot make an external
   process launch exactly-once on its own. Uncertain delivery remains inspected
   recovery, not an automatic repeated prompt.

Acceptance requires a clean-host .NET task and then Rust through the live product
path, including a gateway restart during acquisition, two waiters sharing an SDK,
cancel/re-plan races, expired leases, and an accepted launch whose response is
lost. Each case must either yield one correctly bound execution or an actionable
blocker without duplicate task delivery. SDK readiness and project check success
remain separate evidence.

| Order | Deliverable | Main code seams | Exit criterion |
| --- | --- | --- | --- |
| 1 | Requirement, plan, receipt and capability contracts; persistence migration | `project_requirements.py`, `project_analysis.py`, new environment store | A two-component project produces a stable plan without executing code or installing anything |
| 2 | Trusted adapters and artifact/version providers | `project_toolchains.py`, new adapter registry and worker | Exact per-project artifacts can be acquired/verified without a release-wide project version pin |
| 3 | Durable scheduler, leases, request authorization and broker boundary | `sdk_install.py`, `install/sdk-install.py`, new preparation service | Concurrent waiters share one install; crashes recover without duplicate launch or unsafe partial reuse |
| 4 | .NET vertical slice | .NET resolver/probes/installer/recipes plus launch integration | The clean-host .NET acceptance scenario works through the actual product path |
| 5 | Rust vertical slice | Cargo/rustup resolver, acquisition, activation and recipes | The clean-host Rust and simultaneous-version scenarios work through the same generic contracts |
| 6 | Generic launch/validation binding and multi-component scheduling | `contributions.py`, runtime/session launch paths, `validation.py`, `toolchain_policy.py`, coordinator | Agent commands and candidate validation use the recorded environment; .NET plus Node does not require picking one tool |
| 7 | Node, Python, Go and migration of Flutter/Dart | Ecosystem adapters and Herdr self-build compatibility path | Supported clean-host repositories prepare automatically; existing Herdr build behavior remains verified |
| 8 | Native prerequisites, additional ecosystems and execution providers | Host capability registry, image/remote providers, UI | Each newly advertised combination has end-to-end evidence and precise unsupported cases |

Launch binding and generic check execution must be included in each vertical
slice; work package 6 completes shared coverage rather than postponing the
ability to run .NET/Rust. A standalone installer test is not completion.

Existing documentation and the deployed worker guidance will also need an
explicit migration. Current guidance tells workers to obtain task tools locally;
the new guidance should first use the recorded environment and report a structured
missing capability. Keep a bounded, policy-controlled local fallback without
allowing it to alter shared SDKs or make validation evidence inconsistent.

## 14. Test strategy and acceptance gates

Use pure fixtures for selection algorithms and failure precedence, real native
filesystem tests for containment, mocked providers for deterministic download
failure tests, and separate network-enabled real-installer integration tests.
Do not treat mocks as proof that a real SDK can be installed or activated.

Required coverage:

1. .NET exact selection, supported roll-forward policies, prereleases, nearest
   configuration, SDK search paths, malformed files, unknown SDK requirements
   and supported/unsupported platform-specific operations.
2. Rust exact/detailed channel identity, legacy/toolchain file precedence,
   compiler floor versus selection, workspace inheritance, components, targets,
   local overrides and absent native prerequisites.
3. Clean hosts and hosts with a wrong SDK, several SDKs, an unreadable candidate,
   an already correct managed receipt and a tampered installation.
4. Two projects requiring different versions, two jobs sharing one version,
   .NET plus Node, and a genuinely conflicting same-invocation requirement.
5. A project without Herdr-specific validation scripts: prepare, run selected
   checks and record real evidence without modifying the project to fit Herdr.
6. Timeout, offline/cache behavior, authentication, cancellation, disk quota,
   archive traversal/link attacks, checksum failures and unsupported artifacts.
7. Gateway/worker restart during every install step, stale lease recovery,
   retries, idempotency, a newer revision superseding a plan and exactly one
   eligible task/build resuming after readiness.
8. Same environment binding in interactive agent execution and verification;
   no host-global version switch, privileged project execution or publication
   based solely on successful installation.
9. Both dashboards show matching evidence, progress, refusal reasons and retry
   behavior. Organization polling and analysis GETs perform no provisioning.
10. Existing manual operations, Flutter self-builds, captures, validation,
    approval checks and publication continue to work during migration.

Run the strict Python gate on supported Linux hosts without skipped boundary
tests. Add disposable executor coverage for every platform/architecture claimed
by an adapter. Real installation tests use pinned test identities and clean
environments; routine pull requests need not redownload every SDK, but release
qualification must exercise the advertised clean-host paths. Provider metadata
changes should have explicit compatibility tests rather than silently changing
task selections.

## 15. Definition of done

For each advertised ecosystem/platform combination, a representative repository
with missing tools must reach real verified task execution and candidate checks
without a tool-name form, a manual SDK installation or a Herdr-specific project
rewrite. Repeat execution must reuse a valid environment, concurrent versions
must coexist, and restart/cancellation behavior must preserve task ownership.

When this cannot be achieved, Herdr must state the exact unresolved requirement
or unsupported capability and the next available action. A discovery card,
an installer catalog entry, a green version probe or a conventional command list
alone does not satisfy this definition.
