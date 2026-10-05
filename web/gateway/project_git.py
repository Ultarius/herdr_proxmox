"""Read-only Git checkout status and bounded diffs inside the project root."""
import os
from pathlib import Path
import subprocess
from project_files import project_directory


def git(path, *arguments):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    for key in list(environment):
        if key.startswith('GIT_') and key not in ('GIT_TERMINAL_PROMPT', 'GIT_OPTIONAL_LOCKS'):
            del environment[key]
    try:
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
                             '-C', str(path), *arguments], capture_output=True, timeout=15, env=environment)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('Git is unavailable or the checkout inspection timed out.') from error
    if result.returncode:
        raise ValueError('Git information is unavailable for this checkout.')
    if len(result.stdout) > 1_000_000:
        raise ValueError('Git output is too large. Narrow the selected changes.')
    return result.stdout.decode('utf-8', errors='replace')


def checkout(root, path):
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
    return dict(path=path.relative_to(root).as_posix(), cwd=str(path), branch=branch, commit=commit,
                upstream=upstream, ahead=ahead, behind=behind, changes=changes,
                counts={kind: sum(c['kind'] == kind for c in changes) for kind in ('added', 'deleted', 'modified')})


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
    paths = [repository]
    for record in git(repository, 'worktree', 'list', '--porcelain', '-z').split('\0'):
        if record.startswith('worktree '):
            candidate = Path(record[9:]).resolve()
            if candidate.is_relative_to(root) and candidate.is_dir() and candidate not in paths:
                paths.append(candidate)
    worktrees = []
    for candidate in paths[:20]:
        try:
            worktrees.append(checkout(root, candidate))
        except ValueError:
            continue
    return dict(repository=True, worktrees=worktrees, limited=len(paths) > 20)
