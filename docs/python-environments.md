# Python preparation and execution environments

Python uses the shared environment plan, admission, preparation, launch,
validation and publication pipeline. It does not replace the gateway's Python,
change global PATH, write shell startup files or use pyenv shims.

## Selection and acquisition

`install/python-tools.json` freezes uv 0.13.8, its Linux x64/arm64 archive
checksums, and stable CPython 3.11-and-newer entries from that release's download catalog.
The supported host is Linux glibc x64/arm64. All component declarations must be
decidably satisfied. A range selects the highest compatible stable version in
this pinned catalog; a pin selects that exact version. Unsupported syntax,
conflicts and unavailable versions refuse preparation. Updating the catalog is
a reviewed release change; it never rolls an active plan forward.

`catalog_sha256` identifies the original upstream catalog and `selection_policy`
names the selection rule. Both are provenance metadata, not runtime verification
of the filtered bundled catalog. Git and the discovery runner fingerprint cover
that bundled file; archive checksums and installation fingerprints are verified
separately during preparation and reuse.

Herdr downloads and checksums uv and the selected Python Build Standalone
archive, then lets uv install from a local mirror into the final managed path.
uv cannot choose another download. Its installation directory and command
registration are explicitly scoped; no user Python alias is installed.
The receipt records distribution checksums, the uv binary identity, a local
interpreter-tree fingerprint and a bounded isolated probe importing ssl, sqlite3
and venv. Distribution checksums and a local fingerprint are not signatures or
publisher attestation. Relative internal SDK links are fingerprinted; external
links and special files are refused. Virtual environments are never adopted or
copied into the SDK store.

## Recipes, permissions and execution paths

The immutable Python recipe includes the source revision, component, interpreter
artifact, manager/version, platform, selected groups/extras and hashes of its
project/selection/lock inputs. It contains no execution environment path.

SDK preparation, dependency restoration and checks are separate grants.
`auto_prepare_restore` authorizes locked Python restoration before automatic
launch; changing it invalidates waiting admission intents. Both dashboards expose
the switch. For a manually approved plan, the project explorer separately offers
dependency approval through the administrator-only
`POST /api/projects/environment/authorize-restore` endpoint. Choosing validation
checks does not grant this permission.

Each launch and isolated validation gets its own environment under its execution
cache, created directly at the final path. `uv.lock` projects use the pinned uv
manager (`uv venv`, then frozen sync without development groups or editables).
Hash-locked `requirements.lock` projects use the selected interpreter's venv and
bundled pip with exact versions and SHA-256 hashes. Source-only components get a
bare venv without pip. Unlocked dependencies, recursive pip includes/options,
Poetry and uv workspaces currently refuse explicitly. They need their own
manager/ownership contracts; Herdr never substitutes a different manager.

Restore can execute project build hooks. It is not a discovery operation and is
not a sandbox. Discovery and planning never invoke a task runner or manager,
including commands that merely list tasks.

A durable running receipt is created exclusively before manager effects.
Completed receipts record commands, outcomes and the real environment probe.
A failed or interrupted restore requires inspection or a new execution; it is
not implicitly replayed on recovery. The worker does not yet expose an in-place
restore retry action. An execution receipt can be reused only in its original
checkout/path, after another probe.

Launch uses the environment interpreter and bin directory; multiple component
environments use cwd dispatch. Validation materializes the same recipe in its
own worktree. Runtime copies carry these paths; the original frozen policy
digest stays unchanged. Publication requires the matching recipe/base identity,
successful restore receipt, probed execution path and approved checks. A passing
bare-interpreter probe cannot substitute for restored-environment evidence.

## Verification

`tests/test_environment_contracts.py` proves provider dispatch, declaration
preservation, command aliases, generic materialization, permission preflight and
evidence hooks with an unrelated fixture SDK.
`tests/test_environment_python.py` exercises selection, permission refusal,
input drift, internal-link fingerprints, real venv creation, interrupted restore
handling and distinct launch/validation paths. Run the strict Linux test runner
with `--all`; Windows skips do not qualify the filesystem path.

`tests/ci/python_environment.py` downloads the real pinned uv/Python artifacts,
creates a locked fixture, prepares it through the product service, restores
dependencies independently for launch and validation, and checks the publication
gate. It runs on both release qualification architectures before publication.

## Adapter and provider contracts

The SDK adapter declares its acquisition provider, requirement planner, optional
execution materializer, evidence-path matcher, execution variables, aliases,
extra operations and automatic-check eligibility. The provider registry owns
acquisition and receipt-integrity hooks. Registry hook references come from the
deployed gateway, never from repository manifests or plan data. Archive providers
retain their existing .NET/Rust strategy; installer providers receive the original
per-source declarations, not a reconstructed exact-version constraint.

Plans and policies use `restore_recipes`, `restore_recipe`, `artifact_receipt`,
`restore_permission`, `restore_authorized` and `protected_inputs`. Launch and
validation both call `materialize_execution_policy`; it checks restoration
permission across the whole policy before any provider can have effects.
The Python adapter contract is version 2. Previously staged Python plans must be
rebuilt; their frozen identities are not silently rewritten.
