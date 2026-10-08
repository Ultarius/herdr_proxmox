"""Put release-matched guidance inside an ignored worker checkout directory."""
import hashlib
import os
from pathlib import Path
import tempfile
import uuid

import project_git
from repository_lock import repository_lock


def local_bundle(checkout, recover=False):
    with repository_lock(checkout):
        return _local_bundle(checkout, recover)


def _local_bundle(checkout, recover):
    checkout = Path(checkout).resolve()
    source = Path(__file__).parent / 'skills/herdr-worktree-integration'
    names = ('SKILL.md', 'references/merge.md', 'references/tools.md',
             'references/validation.md', 'references/report.md')
    files = {}
    for name in names:
        path = source / name
        if source.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('Worker skill reference is missing or unsafe: ' + name)
        data = path.read_bytes()
        if len(data) > 100000:
            raise ValueError('Worker skill resource exceeds its size limit.')
        files[name] = data
    digest = hashlib.sha256(b''.join(name.encode() + b'\0' + data for name, data in files.items())).hexdigest()
    relative = '.ci-cache/herdr-guidance/' + digest
    # Local metadata keeps arbitrary repositories clean without changing tracked rules.
    exclude = Path(project_git.git(checkout, 'rev-parse', '--path-format=absolute',
                                   '--git-path', 'info/exclude').strip())
    if exclude.parent.is_symlink() or exclude.is_symlink():
        raise ValueError('Git exclusion metadata must not be a symlink.')
    exclude.parent.mkdir(exist_ok=True)
    if exclude.exists() and (not exclude.is_file() or exclude.stat().st_size > 1000000):
        raise ValueError('Git exclusion metadata must be a bounded regular file.')
    existing = exclude.read_bytes() if exclude.exists() else b''
    pattern = b'/.ci-cache/herdr-guidance/'
    if pattern not in existing.splitlines():
        descriptor, temporary = tempfile.mkstemp(prefix='.herdr-exclude-', dir=exclude.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(existing + (b'\n' if existing and not existing.endswith(b'\n') else b'') + pattern + b'\n')
                stream.flush()
                os.fsync(stream.fileno())
            if exclude.exists():
                os.chmod(temporary, exclude.stat().st_mode & 0o777)
            os.replace(temporary, exclude)
        finally:
            Path(temporary).unlink(missing_ok=True)
    project_git.git(checkout, 'check-ignore', '--no-index', relative + '/SKILL.md')
    destination = checkout / relative
    for parent in (checkout / '.ci-cache', checkout / '.ci-cache/herdr-guidance', destination, destination / 'references'):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError('Worker guidance directories must be regular directories.')
        parent.mkdir(exist_ok=True)
    if recover and any((destination / name).exists() and
                       ((destination / name).is_symlink() or not (destination / name).is_file()
                        or (destination / name).stat().st_size != len(data)
                        or (destination / name).read_bytes() != data)
                       for name, data in files.items()):
        destination.rename(destination.with_name(digest + '.quarantine-' + uuid.uuid4().hex))
        destination.mkdir()
        (destination / 'references').mkdir()
    for name, data in files.items():
        path = destination / name
        if path.is_symlink():
            raise ValueError('Worker guidance files must not be symlinks.')
        if not path.exists():
            try:
                with path.open('xb') as stream:
                    stream.write(data)
                continue
            except FileExistsError:
                pass  # Another preparation created it; verify its content below.
        if not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data:
            raise ValueError('Worker guidance cache was modified; inspect it before continuing.')
    return destination
