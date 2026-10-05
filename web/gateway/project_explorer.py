"""Bounded, read-only browsing of project files."""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
from contextlib import contextmanager

ANCHORED_ACCESS = (os.open in os.supports_dir_fd and os.scandir in os.supports_fd
                   and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'))


@contextmanager
def open_project(root, relative):
    """Walk from a pinned root handle; never reopen a validated pathname."""
    if not ANCHORED_ACCESS:
        raise ValueError('Secure project browsing requires a Linux gateway.')
    descriptors = []
    try:
        descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for part in root.parts[1:]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            descriptors.append(descriptor)
        for index, part in enumerate(relative.parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(relative.parts) - 1:
                flags |= os.O_DIRECTORY
            descriptor = os.open(part, flags, dir_fd=descriptor)
            descriptors.append(descriptor)
        yield descriptor
    except OSError as error:
        raise ValueError('Project path is unavailable or contains a symbolic link.') from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def browse(root, body):
    value = body.get('path', '') if isinstance(body, dict) else None
    if not isinstance(value, str) or len(value) > 2000 or '\\' in value or '\x00' in value:
        raise ValueError('Invalid project path.')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts or relative.drive:
        raise ValueError('Select a path inside the projects directory.')
    root = Path(root).absolute()
    with open_project(root, relative) as descriptor:
        return browse_descriptor(root, relative, descriptor)


def browse_descriptor(root, relative, descriptor):
    info = os.fstat(descriptor)
    base = dict(path=relative.as_posix(), root=str(root))
    if stat.S_ISDIR(info.st_mode):
        entries = []
        with os.scandir(descriptor) as children:
            for child in children:
                if len(entries) >= 1000:
                    break
                try:
                    details = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue  # A concurrent deletion should not discard the listing.
                kind = 'link' if child.is_symlink() else 'folder' if stat.S_ISDIR(details.st_mode) else 'file' if stat.S_ISREG(details.st_mode) else 'special'
                entries.append(dict(name=child.name, path=(relative / child.name).as_posix(),
                                    kind=kind, size=details.st_size if kind == 'file' else None,
                                    modified=datetime.fromtimestamp(details.st_mtime, timezone.utc).isoformat()))
        entries.sort(key=lambda entry: (entry['kind'] != 'folder', entry['name'].casefold()))
        return dict(**base, kind='folder', entries=entries, limited=len(entries) == 1000)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Only regular files and folders can be opened.')
    with os.fdopen(os.dup(descriptor), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Only regular files can be previewed.')
        data = stream.read(65537)
    truncated = len(data) > 65536
    try:
        content = data[:65536].decode('utf-8', errors='strict' if not truncated else 'replace')
        if '\x00' in content:
            raise UnicodeError()
    except UnicodeError:
        return dict(**base, kind='file', size=info.st_size, binary=True, content='', truncated=False)
    return dict(**base, kind='file', size=info.st_size, binary=False, content=content, truncated=truncated)
