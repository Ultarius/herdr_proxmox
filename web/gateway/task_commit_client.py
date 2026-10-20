"""Capture explicit task files once, then submit/retry that immutable request.

The only credential is the existing task-session token written at launch.
No dashboard token, Git configuration or direct commit fallback is used.
"""
import argparse
from contextlib import ExitStack
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import uuid

from project_access import ANCHORED_ACCESS, open_project, open_directory
from task_revision_contract import file_path, normalize, MAX_REQUEST, MAX_BYTES


def write_private(path, value, *, exclusive=False):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Submission context or request must not be a symlink.')
    content = json.dumps(value, sort_keys=True).encode()
    if len(content) > MAX_REQUEST:
        raise ValueError('Submission request exceeds its size limit.')
    descriptor, temporary = tempfile.mkstemp(prefix='.herdr-commit-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # A request is a captured revision. Never overwrite one with newer
            # workspace bytes, including when two clients race to create it.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        if os.name != 'nt':
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path):
    # The context is a local launch artifact, not a repository declaration.
    # Reject links and bound reads; shared-account permissions remain in force.
    with open_project(Path(path).absolute().parent, Path(path).name) as descriptor:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Submission context and request must be regular files.')
        chunks = bytearray()
        while len(chunks) <= MAX_REQUEST:
            chunk = os.read(descriptor, min(65536, MAX_REQUEST + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        data = bytes(chunks)
    if len(data) > MAX_REQUEST:
        raise ValueError('Submission context or request exceeds its limit.')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Submission context or request must be an object.')
    return value


def capture(root, names):
    if not ANCHORED_ACCESS or not hasattr(os, 'O_NONBLOCK'):
        raise ValueError('Secure task-file capture requires a Linux gateway with descriptor-anchored access.')
    if not 1 <= len(names) <= 512 or len(set(names)) != len(names):
        raise ValueError('Select between 1 and 512 distinct files.')
    changes, total = [], 0
    for name in names:
        name = file_path(name)
        relative = PurePosixPath(name)
        # Pin the parent before opening the final name. Both census and
        # manifest access use the same Linux descriptor boundary.
        with open_project(root, '.') as root_descriptor, ExitStack() as ancestors:
            try:
                parent = root_descriptor
                for part in relative.parts[:-1]:
                    parent = ancestors.enter_context(open_directory(parent, part))
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                changes.append(dict(path=name, mode='deleted'))
                continue
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Only regular submitted files are supported.')
                data = bytearray()
                while len(data) <= 2 * 1024 * 1024:
                    chunk = os.read(descriptor, min(65536, 2 * 1024 * 1024 + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError('Submitted file exceeds 2 MiB.')
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError('Submitted revision exceeds 8 MiB.')
                after = os.fstat(descriptor)
                if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('Submitted file changed during capture; retry with stable files.')
                changes.append(dict(path=name, mode='100755' if info.st_mode & 0o111 else '100644',
                    content=base64.b64encode(data).decode('ascii'), sha256=hashlib.sha256(data).hexdigest()))
            finally:
                os.close(descriptor)
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--request-file', required=True)
    parser.add_argument('--parent')
    parser.add_argument('--message')
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--gateway', default='http://127.0.0.1:8787')
    args = parser.parse_args(argv)
    try:
        endpoint = urlsplit(args.gateway)
        if (endpoint.scheme != 'http' or endpoint.hostname not in ('127.0.0.1', 'localhost', '::1')
                or endpoint.username or endpoint.password or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment):
            raise ValueError('Task submission uses a loopback gateway URL only.')
        context = read_json(args.context)
        location = Path(args.request_file)
        if location.exists() or location.is_symlink():
            body = read_json(location)
            if body.get('task_id') != context.get('task_id') or body.get('run_id') != context.get('run_id'):
                raise ValueError('Captured request belongs to another task session.')
            if ((args.parent is not None and args.parent != body.get('parent'))
                    or (args.message is not None and args.message.strip() != body.get('message'))
                    or (args.file and sorted(args.file) != sorted(item['path'] for item in body.get('files', [])))):
                raise ValueError('Captured request already exists with different inputs; use a new request file.')
        else:
            root = context.get('checkout_root')
            if not isinstance(root, str) or not Path(root).is_absolute():
                raise ValueError('Task context lacks its checkout root; obtain the current launch context.')
            root = Path(root).resolve()
            if Path.cwd().resolve() != root:
                raise ValueError('Run task-file capture from the assigned checkout root; --file paths are repository-relative.')
            body = normalize(dict(task_id=context.get('task_id'), run_id=context.get('run_id'),
                request_id=uuid.uuid4().hex, parent=args.parent, message=args.message,
                files=capture(root, args.file)))
            write_private(location, body, exclusive=True)
        body['token'] = context['token']
        request = Request(args.gateway.rstrip('/') + '/api/tasks/submit',
                          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=120) as response:
            answer = json.loads(response.read(MAX_REQUEST + 1))
        print(json.dumps(answer))
        return 0
    except HTTPError as error:
        try:
            message = json.loads(error.read(4096)).get('error', 'Gateway refused the submission.')
        except (ValueError, AttributeError):
            message = 'Gateway refused the submission.'
        print(message, file=sys.stderr)
    except (ValueError, OSError, KeyError, URLError) as error:
        print(str(error), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
