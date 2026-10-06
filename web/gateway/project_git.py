"""Git checkout status, bounded diffs and remote-tracking fetches.

Status and diffs only read the repository. An explicit fetch updates
remote-tracking branches; it never switches branches or changes working files.
"""
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit
import os
from pathlib import Path
import subprocess
import tempfile
from project_files import project_directory
from repository_lock import repository_lock


def git(path, *arguments, timeout=15, index_file=None):
    # Compound callers (snapshots and base updates) hold this reentrant lock
    # throughout their checks and mutations; individual Git mutations also join.
    # Read-only queries such as `worktree list` never take the lock.
    mutates = any(arg in ('fetch', 'update-ref', 'commit-tree', 'write-tree', 'read-tree') for arg in arguments)
    if not mutates and 'worktree' in arguments:
        mutates = any(arg in ('add', 'remove', 'prune', 'move', 'repair') for arg in arguments)
    if mutates:
        with repository_lock(path):
            return _git(path, *arguments, timeout=timeout, index_file=index_file)
    return _git(path, *arguments, timeout=timeout, index_file=index_file)


def _git(path, *arguments, timeout=15, index_file=None):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    for key in list(environment):
        if key.startswith('GIT_') and key not in ('GIT_TERMINAL_PROMPT', 'GIT_OPTIONAL_LOCKS'):
            del environment[key]
    if index_file:
        environment['GIT_INDEX_FILE'] = str(index_file)
    environment.update(GIT_AUTHOR_NAME='Herdr recovery', GIT_AUTHOR_EMAIL='recovery@herdr.local',
                       GIT_COMMITTER_NAME='Herdr recovery', GIT_COMMITTER_EMAIL='recovery@herdr.local')
    try:
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
                             '-C', str(path), *arguments], capture_output=True, timeout=timeout, env=environment)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('Git is unavailable or the checkout inspection timed out.') from error
    if result.returncode:
        raise ValueError('Git information is unavailable for this checkout.')
    if len(result.stdout) > 1_000_000:
        raise ValueError('Git output is too large. Narrow the selected changes.')
    return result.stdout.decode('utf-8', errors='replace')


def checkout(root, path, base=None):
    records = git(path, 'status', '--porcelain=v1', '-z', '--untracked-files=normal').split('\0')
    changes = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        code, name = record[:2], record[3:]
        if 'R' in code or 'C' in code:
            index += 1  # porcelain -z includes the original path after the destination
        kind = 'added' if code == '??' or 'A' in code else 'deleted' if 'D' in code else 'modified'
        changes.append(dict(path=name, status=code, kind=kind))
    branch = git(path, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    commit = git(path, 'rev-parse', '--short', 'HEAD').strip()
    try:
        upstream = git(path, 'rev-parse', '--abbrev-ref', '@{upstream}').strip()
        ahead, behind = map(int, git(path, 'rev-list', '--left-right', '--count', 'HEAD...@{upstream}').split())
    except ValueError:
        upstream, ahead, behind = None, None, None
    base_ahead = base_behind = None
    if base:
        base_ahead, base_behind = map(int, git(path, 'rev-list', '--left-right', '--count', f'HEAD...{base}').split())
    conflicts = sum(c['status'] in ('DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU') for c in changes)
    return dict(base=base.removeprefix('refs/remotes/') if base else None, base_ahead=base_ahead, base_behind=base_behind,
                conflicts=conflicts, path=path.relative_to(root).as_posix(), cwd=str(path), branch=branch, commit=commit,
                upstream=upstream, ahead=ahead, behind=behind, changes=changes,
                counts={kind: sum(c['kind'] == kind for c in changes) for kind in ('added', 'deleted', 'modified')})


def merge_state(path):
    """True when this checkout is in the middle of a merge, rebase or cherry-pick."""
    gitdir = Path(git(path, 'rev-parse', '--absolute-git-dir').strip())
    return any((gitdir / name).exists()
               for name in ('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD'))


def summary(root, path, base=None):
    """Integration state for one checkout, without the changed-file list."""
    tree = checkout(root, path, base)
    tree['dirty'] = bool(tree.pop('changes'))
    tree['merging'] = merge_state(path)
    return tree


def fetch(repository):
    """Update origin's remote-tracking branches without touching local branches."""
    remote = git(repository, 'remote', 'get-url', 'origin').strip()
    url = urlsplit(remote)
    https = url.scheme == 'https' and url.hostname and not url.username and not url.password
    ssh = bool(re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', remote))
    if not (https or ssh):
        raise ValueError('Fetch requires an origin using HTTPS without embedded credentials or git@host:path SSH.')
    # Explicit refspec updates remote-tracking branches only, even if the
    # repository config contains a fetch refspec targeting a local branch.
    try:
        git(repository, '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            'fetch', '--no-tags', '--no-recurse-submodules', 'origin',
            '+refs/heads/*:refs/remotes/origin/*', timeout=60)
    except ValueError as error:
        raise ValueError('Remote fetch failed. Check container Git credentials and connectivity.') from error


def inspect(root, body):
    root = Path(root).resolve()
    value = body.get('path', '')
    if not isinstance(value, str) or '\\' in value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('Select a checkout inside the projects directory.')
    path = project_directory(root, str(root / value))
    try:
        repository = Path(git(path, 'rev-parse', '--show-toplevel').strip()).resolve()
    except ValueError:
        return dict(repository=False, worktrees=[])
    if not repository.is_relative_to(root):
        raise ValueError('Repository is outside the projects directory.')
    common = Path(git(repository, 'rev-parse', '--git-common-dir').strip())
    if not common.is_absolute():
        common = repository / common
    if not common.resolve().is_relative_to(root):
        raise ValueError('Git metadata is outside the projects directory.')
    if body.get('file') is not None:
        name = body['file']
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:
            raise ValueError('Invalid changed file path.')
        # Literal pathspec prevents user file names being interpreted as Git patterns.
        diff = git(repository, 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--', ':(literal)' + name)
        return dict(diff=diff[:65536], truncated=len(diff) > 65536)
    action = body.get('action', 'status')
    if action not in ('status', 'fetch'):
        raise ValueError('Unknown Git action.')
    fetch_error = None
    if action == 'fetch':
        try:
            fetch(repository)
        except ValueError as error:
            # Report the failed fetch beside the local status instead of
            # discarding information the operator can still act on.
            fetch_error = str(error)
    base = body.get('base')
    if base is not None:
        if not isinstance(base, str) or not base.startswith('refs/remotes/'):
            raise ValueError('Choose a remote-tracking base ref.')
        git(repository, 'check-ref-format', base)
        git(repository, 'rev-parse', '--verify', base + '^{commit}')
    for candidate in (() if base else ('refs/remotes/origin/HEAD', 'refs/remotes/origin/main', 'refs/remotes/origin/master')):
        try:
            git(repository, 'rev-parse', '--verify', candidate)
            base = candidate
            break
        except ValueError:
            continue
    fetched = common.resolve() / 'FETCH_HEAD'
    try:
        last_fetch = datetime.fromtimestamp(fetched.stat().st_mtime, timezone.utc).isoformat() if fetched.is_file() else None
    except OSError:
        last_fetch = None
    paths = [repository]
    for record in git(repository, 'worktree', 'list', '--porcelain', '-z').split('\0'):
        if record.startswith('worktree '):
            candidate = Path(record[9:]).resolve()
            if candidate.is_relative_to(root) and candidate.is_dir() and candidate not in paths:
                paths.append(candidate)
    worktrees = []
    for candidate in paths[:20]:
        try:
            worktrees.append(checkout(root, candidate, base))
        except ValueError as error:
            # A checkout that cannot be inspected stays visible with its error
            # rather than disappearing from the list.
            worktrees.append(dict(path=candidate.relative_to(root).as_posix(), cwd=str(candidate), error=str(error)))
    shared = common.resolve().parent if common.name == '.git' else repository
    plan = None
    if shared.is_relative_to(root) and base:
        try:
            branch = git(shared, 'symbolic-ref', '--short', 'HEAD').strip()
            plan = dict(path=shared.relative_to(root).as_posix(), branch=branch, base=base,
                        head=git(shared, 'rev-parse', 'HEAD').strip(),
                        target=git(shared, 'rev-parse', '--verify', base + '^{commit}').strip())
        except ValueError:
            pass
    from base_updates import history
    return dict(repository_path=shared.relative_to(root).as_posix(), repository=True, last_fetch=last_fetch, fetch_error=fetch_error, worktrees=worktrees, limited=len(paths) > 20,
                base_update=plan, base_updates=history(common.resolve()))


def recovery_snapshot(root, value, key):
    path = project_directory(Path(root).resolve(), str(Path(root).resolve() / value))
    with repository_lock(path):
        return _recovery_snapshot(root, value, key)


def _recovery_snapshot(root, value, key):
    """Create a pinned stash-shaped commit without changing files or the real index.

    Includes non-ignored untracked files. Ignored files and submodule working
    directories are deliberately outside this Git snapshot.
    """
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        raise ValueError('Invalid recovery ID.')
    info = inspect(root, {'path': value})
    if not info.get('repository'):
        raise ValueError('Recovery requires a Git checkout.')
    path = project_directory(Path(root).resolve(), str(Path(root) / value))
    ref = 'refs/herdr/recovery/' + key
    try:
        commit = git(path, 'rev-parse', '--verify', ref).strip()
        return dict(ref=ref, commit=commit, path=value)
    except ValueError:
        pass
    if merge_state(path) or git(path, 'ls-files', '-u').strip():
        raise ValueError('Finish or inspect the existing integration before creating a recovery snapshot.')
    head = git(path, 'rev-parse', 'HEAD').strip()
    index_tree = git(path, 'write-tree').strip()
    index_commit = git(path, 'commit-tree', index_tree, '-p', head, '-m', 'Herdr recovery index').strip()
    with tempfile.TemporaryDirectory(prefix='herdr-recovery-') as temporary:
        index = Path(temporary) / 'index'
        git(path, 'read-tree', head, index_file=index)
        git(path, 'add', '-A', '--', '.', index_file=index, timeout=60)
        tree = git(path, 'write-tree', index_file=index).strip()
        commit = git(path, 'commit-tree', tree, '-p', head, '-p', index_commit,
                     '-m', 'Herdr pre-integration recovery').strip()
    git(path, 'update-ref', ref, commit)
    return dict(ref=ref, commit=commit, path=value, head=head,
                created_at=datetime.now(timezone.utc).isoformat())
