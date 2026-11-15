"""Operator-owned publication restrictions, independent of mutable credentials."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile

NAME = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')


def guard_path(path):
    return path.with_suffix('.required')


def _read(path, require_root):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
        raise ValueError()
    if require_root and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) not in (0o444, 0o644)):
        raise ValueError()
    if require_root:
        for parent in path.absolute().parents:
            ancestor = parent.lstat()
            if not stat.S_ISDIR(ancestor.st_mode) or ancestor.st_uid != 0 or ancestor.st_mode & 0o022:
                raise ValueError()
    return path.read_text(encoding='utf-8')


class PolicyRequiredError(ValueError):
    pass


def load(path, require_root=False, *, check_guard=True):
    try:
        try:
            required = _read(guard_path(path), require_root) if check_guard else None
        except (OSError, ValueError):
            raise PolicyRequiredError(f'Publishing protection marker {guard_path(path)} is invalid or unreadable. Restore its trusted ownership and contents; publication remains disabled.') from None
        if required is not None and required != 'required\n':
            raise PolicyRequiredError(f'Publishing protection marker {guard_path(path)} is invalid. Restore it from backup; publication remains disabled.')
        content = _read(path, require_root)
        if content is None:
            data = {'version': 1, 'protected': []}
        else:
            data = json.loads(content)
        if type(data.get('version')) is not int or data.get('version') != 1 or not isinstance(data.get('protected'), list):
            raise ValueError()
        records = {}
        for item in data['protected']:
            if (not isinstance(item, dict) or set(item) != {'upstream', 'fork', 'mode', 'arrangement'}
                    or any(not isinstance(item.get(key), str) or not NAME.fullmatch(item[key])
                           or any(part in ('.', '..') for part in item[key].split('/')) for key in ('upstream', 'fork'))
                    or item['upstream'].lower() == item['fork'].lower()
                    or item['mode'] != 'fork_only' or item['arrangement'] != 'manual_adoption'
                    or item['upstream'].lower() in records):
                raise ValueError()
            records[item['upstream'].lower()] = dict(item)
        # Forks must be distinct destinations, never another protected upstream.
        if (any(item['fork'].lower() in records for item in records.values())
                or len({item['fork'].lower() for item in records.values()}) != len(records)):
            raise ValueError()
        if check_guard and required is not None and not records:
            raise PolicyRequiredError(f'Publishing protection was enabled, but {path} is missing or empty. Restore the operator policy; GitHub publication remains disabled.')
        if check_guard and require_root and records and required is None:
            raise PolicyRequiredError(f'Publishing policy {path} must be enabled by root first: run publication_policy.py --arm {path}.')
        return records
    except PolicyRequiredError:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError(f'Publishing policy {path} is present but invalid or unreadable. Restore valid JSON and root ownership with mode 0444 or 0644 under trusted directories. GitHub publication is disabled until the operator repairs it.') from None


def arm(path, *, require_root=True):
    """Root provisioning records protection outside the replaceable policy file."""
    if require_root and getattr(os, 'geteuid', lambda: -1)() != 0:
        raise ValueError('Run policy provisioning as root.')
    records = load(path, require_root, check_guard=False)
    guard = guard_path(path)
    try:
        existing = _read(guard, require_root)
    except (OSError, ValueError):
        raise PolicyRequiredError(f'Publishing protection marker {guard} is invalid or unreadable. Repair it before provisioning protection.') from None
    if records and existing is None:
        descriptor, temporary = tempfile.mkstemp(prefix='.publishing-policy-', dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write('required\n')
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o444)
            temporary.replace(guard)
        finally:
            temporary.unlink(missing_ok=True)
    # An existing guard may never silently be cleared by an empty policy.
    load(path, require_root)
    return records


def fine_pair(push, upstream):
    if (not isinstance(push, str) or not re.fullmatch(r'github_pat_[A-Za-z0-9_]{20,244}', push)
            or not isinstance(upstream, str) or not re.fullmatch(r'github_pat_[A-Za-z0-9_]{20,244}', upstream)):
        raise ValueError('Protected fork publication requires separate fine-grained github_pat_ push and upstream credentials. Token format does not prove permissions.')
    if push == upstream:
        raise ValueError('Protected fork publication requires two distinct fine-grained credentials.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validate and enable root-owned publishing protection.')
    parser.add_argument('--arm', type=Path, required=True, help='Operator policy to validate and enable')
    arguments = parser.parse_args()
    try:
        enabled = arm(arguments.arm)
        print('Publishing protection enabled.' if enabled else 'No protected projects configured; fork-only protection is not enabled.')
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
