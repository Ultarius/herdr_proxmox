"""Session-local scratch space, independent of project SDK provisioning."""
from contextlib import nullcontext
import errno
from pathlib import Path
import re

import project_git
from repository_lock import repository_lock


def prepare(checkout, identity, repository=True):
    try:
        return _prepare(checkout, identity, repository)
    except OSError as error:
        if isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise ValueError('Cannot prepare session scratch: the assigned checkout and Git exclusion metadata '
                             'must be writable. Use a writable clone/worktree or correct its ownership and mount permissions.') from error
        raise


def _prepare(checkout, identity, repository):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Scratch space requires an execution identity.')
    checkout = Path(checkout).resolve(strict=True)
    relative = '.ci-cache/herdr-scratch'
    with repository_lock(checkout) if repository else nullcontext():
        directory = checkout
        directories = []
        for part in ('.ci-cache', 'herdr-scratch', identity):
            directory = directory / part
            if directory.is_symlink() or directory.exists() and not directory.is_dir():
                raise ValueError('Session scratch directories must be regular directories.')
            directories.append(directory)
        if repository:
            # Never mix disposable files with a repository-owned tracked tree.
            if project_git.git(checkout, 'ls-files', '-z', '--', relative):
                raise ValueError('The reserved session scratch path contains tracked files.')
            from worker_guidance import ignore_directory
            ignore_directory(checkout, relative)
            project_git.git(checkout, 'check-ignore', '--no-index', relative + '/' + identity + '/probe')
        for directory in directories:
            directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def toolchains(run):
    frozen = (run or {}).get('execution_toolchain_policy') or (run or {}).get('toolchain_policy') or {}
    return (frozen.get('policy') or {}).get('toolchains') or []


def environment(directory, run=None):
    import build_capacity
    import environment_adapters
    value = str(directory)
    limits = build_capacity.budget()
    budget = build_capacity.variables(dict(fd=None, budget=limits))
    return dict(TMPDIR=value, TMP=value, TEMP=value, HERDR_SCRATCH=value, **budget,
                **environment_adapters.capacity_environment(toolchains(run), budget))


def install_wrappers(directory, run):
    import build_capacity
    import environment_adapters
    wrappers = {entry['capacity_wrapper'] for entry in environment_adapters.session_tools(toolchains(run))}
    return build_capacity.install_cargo(directory) if 'cargo' in wrappers else None


def toolchain_guidance(run):
    import environment_adapters
    lines = []
    for entry in environment_adapters.session_tools(toolchains(run)):
        if not entry['commands']:
            continue
        scope = ' in component ' + str(entry['component']) if entry['component'] is not None else ''
        commands = ', '.join(entry['commands'])
        lines.append(f"Bound {entry['name']}{scope}: use the prepared commands {commands} by name from the selected environment.")
        if entry['capacity_wrapper'] == 'cargo':
            lines.append('Cargo commands use the session PATH wrapper, which queues heavy work and caps compiler/test parallelism.')
    return '\n'.join(lines) + ('\n' if lines else '')


def guidance(run):
    directory = run.get('scratch_directory')
    if not directory:
        return ''
    return (
        '\nSession scratch directory: ' + str(directory) + '. '
        'TMPDIR, TMP, TEMP and HERDR_SCRATCH point here. Use mktemp under "$TMPDIR" '
        'or a unique child of this directory for temporary files and pinned review snapshots; '
        'preserve repository-relative file paths. Do not hardcode /tmp, /tmp/pin or /tmp/pinned '
        'or use another session\'s scratch directory. Prefer direct git show/git ls-tree reads '
        'from your assigned checkout when no temporary copy is needed. '
        'Scratch writes for the assigned analysis are allowed in a read-only discussion; '
        'this does not authorize project changes, SDK installation or broader filesystem access. '
        'Never stage or commit scratch files.\n'
        'Resource budget: run the project\'s declared build/test commands through session-provided wrappers on PATH when present. '
        'Do not bypass a wrapper with an absolute executable path, replace PATH, or start background builds. '
        'HERDR_BUILD_JOBS and HERDR_TEST_THREADS describe the session budget; tool support for parallelism limits varies. '
        'A "Herdr build queued" message means waiting for capacity; do not start a duplicate build.\n'
        'If a check reports No space left on device, stop heavy work and report the storage blocker. '
        'Do not create additional build-output/cache copies or retry the same failed build repeatedly. '
        'Preserve source edits and receipts; ask the operator to inspect disposable build caches or expand storage. '
        'Once space is restored, finish the original task checks and submit the required completion receipt; '
        'an idle session by itself does not finish a task.\n'
    ) + toolchain_guidance(run)


def tool_guidance():
    return (
        '\nEnvironment setup: use the recorded, prepared SDKs for checks. '
        'Do not bootstrap a language toolchain or version manager, or install/switch a project SDK in /tmp, a home cache, '
        'the scratch directory or shared locations. If an SDK is missing or mismatches, '
        'stop those checks and report missing_toolchain with the tool/version, component, '
        'command and actual error; do not claim the checks passed. The administrator can use '
        'the project environment card: Plan environment, then Prepare SDKs, or enable '
        'Prepare supported project SDKs before agent launch in organization automation. '
        'A new launch must adopt the verified environment; do not mutate a live session\'s SDK. '
        'The Herdr CLI (herdr --skill) provides session and coordination commands, not '
        'dashboard administrator authorization. Do not search for dashboard tokens or invoke '
        'administrator APIs to bypass an environment blocker.\n'
    )
