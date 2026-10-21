# Rust projects with browser assets and helper scripts

A JavaScript file is not, by itself, a Node project declaration. In a Cargo
repository, source-only inferred components such as browser shims under assets/
and asset-generation helpers under tools/ remain visible in discovery without
blocking the Rust environment. An actual package.json still creates a required
Node component, and an administrator override can explicitly require the helper
runtime. JS/TS is the specific asset exemption: inferred Python, Go and Rust
components still require their SDKs. A Node manifest anywhere in the repository
keeps the inferred Node requirements too. A manifest-free JavaScript repository
also remains unresolved until its runtime selection is provided.

This is an ecosystem heuristic, not proof that the JS/TS files are browser assets.
It also exempts a source-only Node server or tool without a Node manifest. Both
dashboards warn about this; add `package.json` or a Node.js override when that
component needs a runtime. No filenames or directory names prove runtime usage.

For a repository declaring a pinned Rust version and a minimal toolchain profile,
Herdr can acquire rustc, Cargo and the host standard library, plus explicitly
requested rustfmt and clippy. All archives come from that version's Rust publisher
manifest and are checked against its SHA-256 hashes. Missing add-on commands or
failed version probes prevent readiness. Extra targets, other optional components
and non-minimal profiles remain unsupported rather than silently omitted.

## Prepare and validate from the dashboard

1. Open Project explorer → Git changes → **Plan environment**. The committed
   source used by the approved project checkout must contain the declarations
   you want to use. The host-readiness list alone is not a prepared environment.
2. Review and approve the intended check proposals. Either a documented
   `cargo test --no-default-features` or `cargo build --no-default-features`
   produces both headless proposals with the same README evidence and feature
   selection. The dashboard shows the evidence and requested SDK components.
   This avoids implicitly compiling the default
   graphical client on a headless container. It does not verify GPU rendering,
   browser builds or smoke tests.
3. Choose **Prepare SDKs** and wait for completion. SDK preparation does not mean
   dependencies were restored or tests passed. Do not bootstrap another global
   Rust installation from the agent terminal.
4. Return to the task and choose **Validate latest commit**. The validation runner
   uses the approved recipes and prepared SDK bindings. Cargo may restore
   dependencies as part of the approved build/test commands.

For automatic preparation, enable **Prepare supported project SDKs before agent
launch** and separately **Approve supported project build/test recipes
automatically** in the organization policy. No Node preparation permission is
needed solely because browser assets or a source-only helper were detected.

Existing sessions pin their launch environment. After changing the plan or an
adapter contract, create/prepare a fresh plan; if validation reports that the
session policy differs, inspect and relaunch the assigned task under that plan.
Preparing a different environment does not silently rewrite an old session's
permissions or evidence. Resolve pending permission prompts in the agent console
before resuming delivery.

## Discovery and approval boundaries

The headless README parser recognizes only exact fenced `cargo build
--no-default-features` and `cargo test --no-default-features` lines. It never runs
documentation, repository setup scripts or shell expressions during discovery.
The documented command and README blob become part of the selected policy,
even when only the derived companion check is selected. Deleting or renaming
that README fails policy resolution rather than dropping its protection. Recipe
evidence follows the same committed regular-file and 256 KiB size limits as SDK
declarations; oversized or symlinked evidence needs review rather than being
silently ignored. Candidate changes to those inputs require policy review. A real Node manifest
without a version remains a blocker; this change does not select an arbitrary
Node version or waive unresolved declared dependencies.

Linux glibc x64/arm64 are the supported managed-SDK executors. Publisher metadata
checks and portable parser tests do not replace the Linux installation and
real-project validation checks.

The optional-component archive fixture matches the checksummed Rust 1.97.1
Linux x64 publisher tarballs: `components` names `rustfmt-preview` or
`clippy-preview`, whose payload contains both command binaries. Fixture probes
are doubles, not proof that those Linux binaries execute on another host.
The real Linux release qualification (`python3 tests/ci/project_environments.py`)
now downloads the Rust 1.97.1 optional archives, executes their version probes,
and runs `cargo fmt -- --check` and offline Clippy on x64 and arm64. This is a
release CI gate; adding it does not mean it has run on the Windows development host.

New SDKs are probed once in staging before their installation and receipt are
published together. Failed staging probes leave no published SDK. Later reuse
probes refuse a failing SDK without renaming, deleting or changing its published
path, because existing plans may already bind it. Diagnose such an installation
before operator-directed repair; no automatic replacement of bound bytes occurs.
Only an existing tree without a receipt is quarantined as `.unverified-*` for
inspection. Those preserved trees consume disk until an operator reviews and
removes them; no automatic quarantine retention or reclamation is implemented.

Requested unsupported add-ons (for example `rust-src`) block preparation of the
entire environment plan, including otherwise supported SDKs. Herdr does not
silently drop components to certify a partial toolchain. Empty `components: []`
is accepted on other SDK lookups; a non-empty component selection requires an
adapter that supports it.
