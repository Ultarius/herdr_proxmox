## Missing tools and validation failures

Use the project's frozen, prepared SDK bindings for every build and check.
If the required compiler/runtime/package manager is absent or reports another
version, stop those checks and report `missing_toolchain`. Include the component,
required version, attempted command and actual error. A check that never ran is
`not_run`; a check that ran and failed is `failed`. Never weaken checks or claim
that static inspection substitutes for an unavailable required check.

Project SDK provisioning belongs to the dashboard's environment preparation
service. The administrator can open the project's environment card, choose
**Plan environment**, then **Prepare SDKs**, or enable **Prepare supported project
SDKs before agent launch** in organization automation. The gateway verifies the
installation before a new launch adopts it. The environment plan identifies the
acquisition method and required authorization for each supported adapter;
separately authorized preparation agents handle supported assisted capabilities.
Recognition of a tool does not imply that an installer is supported.
Report unresolved versions or unsupported/native prerequisites to the operator.

Do not bootstrap a language toolchain or version manager, download a replacement
project SDK into a home cache, change the selected SDK or bypass an environment
blocker with a local install.
Do not run `bash /opt/herdr-web/install/dev-tools-install.sh`. The Herdr CLI
provides session/coordination commands; it does not grant dashboard administrator
authority. Do not search for tokens, invent a provisioning CLI command, or call
administrator APIs using credentials found on the machine.

For an auxiliary tool outside the pinned project SDK, installation requires
explicit task authorization and an allocated session-local destination. Without
that authority, report the missing capability. When authorized, pin the exact
version, use HTTPS and verify publisher checksums or package-manager integrity.
Prefer a verified prebuilt binary over bootstrapping another toolchain. Never
execute a downloaded shell script as a shortcut. Reject absolute paths, `..`
entries and symlinks before extracting an archive. Scope PATH to the check command
or invoke the tool by absolute path. Report the version, source, verification,
location and actual installation/check result. Keep attempts bounded.

Use the session's supplied `HERDR_SCRATCH` / `TMPDIR` for temporary review files,
downloads and pinned snapshots. Prefer direct `git show <SHA>:<path>` reads when
no temporary copy is needed; preserve repository-relative paths in snapshots.
Do not hardcode `/tmp/pin`, `/tmp/pinned`, or a shared home cache. Scratch writes
for authorized analysis are allowed during read-only discussions; source changes,
installation and execution still require their own authority. Never stage or
commit scratch contents.

Agents share the `herdr` Unix user, home and installed tools; session directories
are not OS isolation. Do not overwrite shared executables, use `pip install
--user`, edit shell startup files/global CLI settings, install into `/opt` or
`/usr/local`, use sudo/system package managers, or enter another task's caches.
Permission denials mean `missing_permissions`: report the exact path/operation,
stop bounded attempts and do not request broader access as a workaround.

## Managed build storage

Use the SDK adapter's supplied build mode, output variables and download caches.
New environment plans default to compact builds; full debugger information is an
operator choice in Managed build storage followed by a newly approved plan.
Do not override those variables or build into additional output trees. Heavy
commands queue for the shared compiler budget and stop on storage pressure;
waiting is not a reason to launch a duplicate command.

Managed output and execution caches are rebuildable and can be reclaimed after
the session closes. Keep screenshots, save data, test fixtures and deliverables
in the project's documented evidence paths outside managed storage. Installed
dependencies stay private to the execution. Only adapter-declared download data
may be reused, scoped to the same repository and execution lane. Do not link
mutable dependency trees across tasks or delete another execution's data.

If a storage limit stops a check, report the actual blocker and preserve source
edits and evidence. Do not repeatedly retry, change the budget, clean unrelated
directories or claim success. The operator can inspect managed storage, preview
cleanup of inactive outputs or increase the filesystem capacity.
