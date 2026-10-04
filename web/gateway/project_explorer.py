"""Bounded, read-only browsing of project files."""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat


def browse(root, body):
    value = body.get('path', '') if isinstance(body, dict) else None
    if not isinstance(value, str) or len(value) > 2000 or '\\' in value or '\x00' in value:
        raise ValueError('Invalid project path.')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts or relative.drive:
        raise ValueError('Select a path inside the projects directory.')
    root = root.resolve()
    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('Symbolic links cannot be opened in the explorer.')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Select a path inside the projects directory.')
    info = target.stat()
    base = dict(path=target.relative_to(root).as_posix(), root=str(root))
    if stat.S_ISDIR(info.st_mode):
        entries = []
        with os.scandir(target) as children:
            for child in children:
                if len(entries) >= 1000:
                    break
                details = child.stat(follow_symlinks=False)
                kind = 'link' if child.is_symlink() else 'folder' if stat.S_ISDIR(details.st_mode) else 'file' if stat.S_ISREG(details.st_mode) else 'special'
                entries.append(dict(name=child.name, path=(target / child.name).relative_to(root).as_posix(),
                                    kind=kind, size=details.st_size if kind == 'file' else None,
                                    modified=datetime.fromtimestamp(details.st_mtime, timezone.utc).isoformat()))
        entries.sort(key=lambda entry: (entry['kind'] != 'folder', entry['name'].casefold()))
        return dict(**base, kind='folder', entries=entries, limited=len(entries) == 1000)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Only regular files and folders can be opened.')
    # O_NOFOLLOW also protects the final file against a symlink replacement.
    descriptor = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
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
