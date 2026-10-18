"""Bounded task revision wire contract shared by the gateway and local helper."""
import base64
import hashlib
from pathlib import PurePosixPath
import re
from request_identity import TASK_REQUEST

SHA = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
ID = re.compile(r'[a-f0-9]{32}')
MAX_BYTES = 8 * 1024 * 1024
MAX_REQUEST = 12 * 1024 * 1024


def file_path(value):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 1000:
        raise ValueError('Select an explicit repository-relative file.')
    parts = value.split('/')
    if ('\\' in value or ':' in value or any(ord(c) < 32 for c in value)
            or any(part in ('', '.', '..') or part.lower() == '.git' for part in parts)):
        raise ValueError('Select an explicit repository-relative file outside Git metadata.')
    return value


def normalize(body):
    if not isinstance(body, dict) or set(body) - {'task_id', 'run_id', 'token', 'request_id', 'parent', 'message', 'files'}:
        raise ValueError('Invalid task revision fields.')
    if not ID.fullmatch(str(body.get('task_id', ''))) or not TASK_REQUEST.fullmatch(str(body.get('request_id', ''))):
        raise ValueError('Select a task and a stable revision request ID.')
    if not SHA.fullmatch(str(body.get('parent', ''))):
        raise ValueError('Supply the full expected parent SHA.')
    message = body.get('message')
    if not isinstance(message, str) or not message.strip() or len(message.encode('utf-8')) > 8000 or '\x00' in message:
        raise ValueError('Supply a commit message of at most 8000 bytes.')
    files = body.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 512:
        raise ValueError('Submit between 1 and 512 explicit file changes.')
    names, changes, total = set(), [], 0
    for item in files:
        if not isinstance(item, dict) or set(item) - {'path', 'mode', 'content', 'sha256'}:
            raise ValueError('Invalid submitted file change.')
        name = file_path(item.get('path'))
        if name in names:
            raise ValueError('A submitted path occurs more than once.')
        names.add(name)
        mode = item.get('mode')
        if mode == 'deleted':
            if set(item) != {'path', 'mode'}:
                raise ValueError('A deletion must contain only path and mode.')
            changes.append(dict(path=name, mode=mode))
            continue
        if mode not in ('100644', '100755'):
            raise ValueError('Only regular files, executable files and deletions are supported.')
        try:
            data = base64.b64decode(item['content'], validate=True)
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError('Submitted file content must be base64.') from error
        total += len(data)
        if len(data) > 2 * 1024 * 1024 or total > MAX_BYTES:
            raise ValueError('Submitted contents exceed the 2 MiB file or 8 MiB revision limit.')
        digest = hashlib.sha256(data).hexdigest()
        if item.get('sha256') != digest:
            raise ValueError('Submitted file hash does not match its captured bytes.')
        changes.append(dict(path=name, mode=mode, content=base64.b64encode(data).decode('ascii'), sha256=digest))
    # Prefix collisions would replace a directory implicitly. Every deletion
    # and replacement must be expressed at its actual file path.
    if any('/'.join(PurePosixPath(name).parts[:i]) in names
           for name in names for i in range(1, len(PurePosixPath(name).parts))):
        raise ValueError('Submitted file paths overlap.')
    return dict(task_id=body['task_id'], run_id=body.get('run_id'), request_id=body['request_id'],
                parent=body['parent'], message=message.strip(), files=sorted(changes, key=lambda item: item['path']))
