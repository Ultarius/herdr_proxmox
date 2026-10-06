"""Audited, explicit fast-forward of the shared checkout; workers stay untouched."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

import project_git
from project_files import project_directory
from repository_lock import common_directory, repository_lock

SHA = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
REQUEST = re.compile(r'[A-Za-z0-9_-]{1,64}')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def history(common):
    folder = Path(common) / 'herdr-base-updates'
    try:
        paths = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        return [json.loads(p.read_text()) for p in paths[:20]]
    except (OSError, ValueError):
        return []


def save(folder, entry):
    folder.mkdir(exist_ok=True)
    target = folder / (entry['request_id'] + '.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(entry, indent=2))
    temporary.replace(target)


def update(root, body, actor, role, busy=None):
    if role != 'admin':
        raise ValueError('Only an administrator can update the shared base checkout.')
    if not isinstance(body, dict) or not REQUEST.fullmatch(str(body.get('request_id', ''))):
        raise ValueError('A stable request ID is required for a base update.')
    if not all(isinstance(body.get(k), str) for k in ('path', 'branch', 'base', 'head', 'target')):
        raise ValueError('Select the checkout, destination branch, comparison ref and exact commits.')
    if not SHA.fullmatch(body['head']) or not SHA.fullmatch(body['target']):
        raise ValueError('Base updates require full commit IDs.')
    if not body['base'].startswith('refs/remotes/') or body['base'].startswith('-'):
        raise ValueError('Choose a remote-tracking comparison ref.')
    if body['branch'].startswith('-'):
        raise ValueError('Invalid destination branch.')
    root = Path(root).resolve()
    path = project_directory(root, str(root / body['path']))
    common = common_directory(path)
    if common.name != '.git' or path != common.parent or not common.is_relative_to(root):
        raise ValueError('Base updates apply only to the shared repository checkout.')
    with repository_lock(path):
        project_git.git(path, 'check-ref-format', body['base'])
        project_git.git(path, 'check-ref-format', '--branch', body['branch'])
        folder = common / 'herdr-base-updates'
        record_path = folder / (body['request_id'] + '.json')
        entry = json.loads(record_path.read_text()) if record_path.exists() else None
        fingerprint = {k: body[k] for k in ('path', 'branch', 'base', 'head', 'target')}
        if entry:
            if entry.get('actor') != actor or entry.get('selection') != fingerprint:
                raise ValueError('Request ID already belongs to another base update.')
            if entry['state'] != 'pending':
                return entry
        else:
            entry = dict(request_id=body['request_id'], selection=fingerprint, actor=actor,
                         state='pending', at=stamp(), old_head=body['head'], target=body['target'])
            save(folder, entry)  # Audit the attempt before any branch mutation.
        try:
            if busy and busy(path):
                raise ValueError('A managed agent or job is using the shared checkout.')
            if project_git.git(path, 'symbolic-ref', '--short', 'HEAD').strip() != body['branch']:
                raise ValueError('Destination branch changed. Refresh before updating.')
            if project_git.merge_state(path):
                raise ValueError('An operation is active. Inspect the checkout before updating.')
            if project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip():
                raise ValueError('Checkout has local changes. Preserve them before updating.')
            head = project_git.git(path, 'rev-parse', 'HEAD').strip()
            target = project_git.git(path, 'rev-parse', '--verify', body['base'] + '^{commit}').strip()
            if target != body['target']:
                raise ValueError('Remote-tracking target changed. Refresh before updating.')
            recovery = 'refs/herdr/base-updates/' + body['request_id']
            if head != body['target']:
                if head != body['head']:
                    raise ValueError('Checkout HEAD changed. Refresh before updating.')
                ahead = int(project_git.git(path, 'rev-list', '--count', body['target'] + '..HEAD').strip())
                if ahead:
                    raise ValueError('Destination has commits outside the target history. Review divergence; no automatic merge is allowed.')
                project_git.git(path, 'update-ref', recovery, head)
                entry['recovery_ref'] = recovery
                save(folder, entry)
                # Exact commit only: never a moving ref, stash, reset or rebase.
                # An administrative fast-forward must not execute repository
                # hooks that could alter other checkouts or deploy software.
                with tempfile.TemporaryDirectory(prefix='herdr-empty-hooks-') as hooks:
                    try:
                        project_git.git(path, '-c', 'gc.auto=0', '-c', 'submodule.recurse=false',
                                        '-c', 'core.hooksPath=' + hooks, 'merge', '--ff-only', '--no-overwrite-ignore', body['target'], timeout=60)
                    except ValueError as error:
                        raise ValueError('Fast-forward refused. Inspect ignored files, Git locks and filesystem permissions. '
                                         'The old HEAD recovery reference is retained; no reset or stash was attempted.') from error
            elif entry.get('recovery_ref') is None:
                # Idempotent no-op at the requested commit; no branch was moved.
                entry['note'] = 'Checkout already contains the exact target.'
            if project_git.git(path, 'rev-parse', 'HEAD').strip() != body['target']:
                raise ValueError('Update verification failed; inspect the checkout.')
            entry.update(state='complete', finished_at=stamp(), new_head=body['target'])
        except (ValueError, OSError) as error:
            entry.update(state='blocked', blocker='state_conflict', reason=str(error), finished_at=stamp())
        save(folder, entry)
        return entry
