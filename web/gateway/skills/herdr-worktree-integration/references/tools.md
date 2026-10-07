## Missing tools and validation failures

A tool needed for your task is yours to obtain within the task's existing
permissions and owner restrictions. Pin the version required by the repository's
CI or lockfile. Prefer a task-specific, versioned cache under `$HOME` rather
than changing shared executables on PATH.

For a released binary, download over HTTPS and verify the publisher's SHA-256
checksum before extracting only the needed files. Reject absolute paths, `..`
paths and symlinks in the archive. Package-manager installs must use their
integrity verification: Go's checksum database (do not disable it), or
locked/hash-verified packages in a task virtual environment. `go install
<module>@<exact version>` requires compatible Go; if Go is absent, prefer a
verified prebuilt binary over bootstrapping an unrelated toolchain. Never
execute a downloaded shell script as an installation shortcut.

Keep installations inside the task's directories. Agents currently share the
`herdr` Unix user and its home: user-space installation is not agent isolation.
Avoid overwriting shared tools, `pip install --user`, modifying shell startup
files, global CLI configuration or shared package environments. Do not install
into `/opt`, `/usr/local`, or another task's directories, and do not use sudo or
system package managers. Use a virtual environment or task-specific prefix.
Scope PATH to the check command or invoke the tool by absolute path.
Keep downloads out of candidate commits; prefer a task cache outside the checkout.

Run the check for real. Report the exact commands, actual exit status, version,
integrity/checksum source and install location in your report. Integration
requests use `tests.summary` for that evidence, or `reason` when blocked. A
passing web build never substitutes for a missing required check. Do not edit
checks, weaken assertions or substitute a different tool.

If installation fails, stop bounded attempts and report the actual blocker,
including the tool/version, source, failing command and error (redact credentials).
Offline downloads, unsupported architectures, unavailable releases, missing disk
space and permission denials can block even user-space installation. Missing
required tools mean `missing_toolchain`; permission denials mean
`missing_permissions`. A check that never ran remains `not_run`. A check that ran
and failed remains `failed`, even if a later tool installation also fails.
Do not retry indefinitely or broaden permissions to bypass a failure.

Ask the platform for provisioning when a tool needs root, changes shared system
state, or is the project's pinned build toolchain. The fixed-purpose SDK service
installs Flutter/Dart only; it cannot provision arbitrary task tools. Agents do
not run `bash /opt/herdr-web/install/dev-tools-install.sh`. Report unsupported
platform requirements to the operator, with the reason local installation failed.
