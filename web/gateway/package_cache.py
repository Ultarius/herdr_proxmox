"""Adapter-selected download reuse; installed dependencies remain execution-local.

These caches are an optimisation, never an SDK, restore receipt, or permission to
execute package code. A repository and an execution lane own each shared pool.
Cargo shares only checksum-verified compressed archives, copied into a private
CARGO_HOME; its configuration, credentials, unpacked sources and build output are
never shared. npm/uv/pip caches stay private to each execution. These directory scopes
do not provide a security boundary between processes running as the same user.
"""
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import tomllib


VERSION = 1
LANES = frozenset(('agent', 'validation'))
LOCK_LIMIT = 4 * 1024 * 1024
ARCHIVE_LIMIT = 256 * 1024 * 1024
TRANSFER_LIMIT = 1024 * 1024 * 1024
_HEX = re.compile(r'[a-f0-9]{64}\Z')
_PACKAGE = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_VERSION = re.compile(r'[A-Za-z0-9.+_-]{1,200}\Z')
_BUCKET = re.compile(r'[A-Za-z0-9._-]{1,200}\Z')


def _absolute(path):
    return Path(os.path.abspath(path))


def _link(info):
    # Windows junctions/reparse points must be treated like POSIX symlinks.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _directory(path, *, create=False):
    """Walk existing ancestors without resolving links, then create private dirs."""
    path = _absolute(path)
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                continue
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = current.lstat()
        if _link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Package cache directories must be regular directories, without links: ' + str(current))
    return path


def _contained(path, root):
    path, root = _absolute(path), _absolute(root)
    if not path.is_relative_to(root) or path == root:
        raise ValueError('A package cache path is outside its managed directory.')
    return path


def repository_info(checkout):
    """One sanitized lookup, scoped to this on-disk repository incarnation."""
    import project_git
    checkout = _absolute(checkout)
    common = Path(project_git.git(checkout, 'rev-parse', '--git-common-dir').strip())
    if not common.is_absolute():
        common = checkout / common
    common = common.resolve(strict=True)
    configuration = common / 'config'
    content = _read(configuration, LOCK_LIMIT)
    info = configuration.lstat()
    # Config identity changes on replacement/re-clone. Normal worktree creation
    # changes the common directory itself, so its mtime/ctime is not a key.
    material = '\0'.join((os.path.normcase(str(common)), str(info.st_dev), str(info.st_ino),
                          str(info.st_ctime_ns), sha256(content).hexdigest()))
    return dict(common=common, project=common.parent, key=sha256(material.encode()).hexdigest())


def repository_key(checkout):
    return repository_info(checkout)['key']


def repository_identity(checkout):
    """Stable allocator key; worktree paths do not split the download pool."""
    return repository_key(checkout)


def _component(entry):
    component = entry.get('component') or '.'
    path = PurePosixPath(component)
    if path.is_absolute() or '..' in path.parts or '\\' in component or ':' in component:
        raise ValueError('A package cache component must stay within its checkout.')
    return path.as_posix()


def cargo(entry, context):
    """Share compressed registry archives, never an entire Cargo home."""
    private = Path(context['execution_root']) / 'cargo'
    return dict(kind='cargo-archives', private=str(private),
                shared=str(Path(context['scope']) / 'cargo-archives-v1'),
                components=[_component(entry)], environment={'CARGO_HOME': str(private)})


def npm(entry, context):
    """Keep request metadata and private-package downloads execution-local."""
    private = Path(context['execution_root']) / 'npm'
    return dict(kind='npm', private=str(private), components=[_component(entry)],
                environment={'npm_config_cache': str(private)})


def python(entry, context):
    """Private download caches. Apply pip arguments explicitly with --isolated."""
    private = Path(context['execution_root']) / 'python'
    return dict(kind='python', private=str(private), components=[_component(entry)],
                environment={'UV_CACHE_DIR': str(private / 'uv'), 'UV_LINK_MODE': 'copy',
                             'PIP_CACHE_DIR': str(private / 'pip')},
                directories=[str(private / 'uv'), str(private / 'pip')],
                pip_cache_arguments=['--cache-dir', str(private / 'pip')])


def configure(entries, execution_root, checkout, *, lane, shared_root, create=False, repository=None, create_shared=True):
    """Return process variables plus durable transfer/guidance metadata.

    Only release-owned Adapter.download_cache hooks select a strategy; repository
    entries cannot name a strategy or import path. create=False has no filesystem
    side effects. Call seed before commands and harvest afterwards (also after a
    failed command); callers should report cache errors without changing checks.
    """
    import environment_adapters
    from environment_providers import invoke

    if lane not in LANES:
        raise ValueError('Package caches require an agent or validation execution lane.')
    root, execution = _directory(shared_root), _directory(execution_root)
    checkout = Path(checkout).resolve(strict=True)
    if root.is_relative_to(checkout) or checkout.is_relative_to(root):
        raise ValueError('Shared package caches must be outside project checkouts.')
    if execution.is_relative_to(root) or root.is_relative_to(execution):
        raise ValueError('Shared download pools and private execution caches must be separate.')
    identity = (repository or repository_info(checkout))['key']
    scope = root / ('v' + str(VERSION)) / identity / lane
    context = dict(execution_root=str(execution), scope=str(scope))
    selected = {}
    for entry in entries:
        adapter = environment_adapters.ADAPTERS.get(entry.get('name'))
        hook = getattr(adapter, 'download_cache', None)
        if not hook:
            continue
        item = invoke(hook, entry, context)
        if not isinstance(item, dict):
            raise ValueError('A package cache adapter returned an invalid contract.')
        shared = _contained(item['shared'], scope) if item.get('shared') else None
        private = _contained(item['private'], execution) if item.get('private') else None
        key = (item['kind'], str(shared), str(private))
        if key in selected:
            selected[key]['components'] = sorted(set(selected[key]['components'] + item.get('components', [])))
            continue
        if shared:
            _directory(shared, create=create and create_shared)
        if private:
            _directory(private, create=create)
        for directory in item.get('directories', []):
            _directory(_contained(directory, shared or private), create=create)
        selected[key] = item
    items = list(selected.values())
    environment = {key: value for item in items for key, value in item.get('environment', {}).items()}
    manifest = dict(version=VERSION, repository=identity, lane=lane, shared_root=str(root),
                    execution_root=str(execution), caches=items)
    guidance = []
    if items:
        guidance = ['Package-manager caches are private to this execution. Only broker-verified archive bytes may be reused. '
                    'Keep build outputs and installed dependency trees local to this execution. '
                    'Use the supplied cache settings; do not copy dependency trees between worktrees or delete shared caches. '
                    'Download reuse grants no dependency restore or installation permission.']
    return dict(environment=environment, manifest=manifest, guidance=guidance)


def pip_arguments(manifest):
    """pip --isolated ignores PIP_CACHE_DIR; generated invocations need argv."""
    return next((list(item['pip_cache_arguments']) for item in manifest.get('caches', [])
                 if item.get('kind') == 'python'), [])


def _read(path, limit):
    _directory(path.parent)
    if _link(path.lstat()):
        raise ValueError('Package cache files must not be links: ' + str(path))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('A package cache input is not a bounded regular file: ' + str(path))
        value = source.read(limit + 1)
        if len(value) > limit:
            raise ValueError('A package cache input exceeds its size limit: ' + str(path))
        return value


def _locks(checkout, components):
    """Cargo workspaces may keep their lockfile above a selected component."""
    found = set()
    for component in components:
        directory = _absolute(checkout / PurePosixPath(component))
        if not directory.is_relative_to(checkout):
            raise ValueError('A Cargo cache component is outside its checkout.')
        while True:
            _directory(directory)
            candidate = directory / 'Cargo.lock'
            if candidate.exists() or candidate.is_symlink():
                found.add(candidate)
                break
            if directory == checkout:
                break
            directory = directory.parent
    return sorted(found)


def _packages(checkout, components):
    result = {}
    for lock in _locks(checkout, components):
        document = tomllib.loads(_read(lock, LOCK_LIMIT).decode('utf-8'))
        packages = document.get('package', [])
        if not isinstance(packages, list) or len(packages) > 10000:
            raise ValueError('A Cargo lockfile contains an unsupported package list.')
        for package in packages:
            if not isinstance(package, dict):
                raise ValueError('A Cargo lockfile contains an unsupported package entry.')
            name, version = package.get('name', ''), package.get('version', '')
            source, checksum = package.get('source', ''), package.get('checksum', '')
            if not isinstance(source, str) or not source.startswith('registry+'):
                continue
            if not all(isinstance(value, str) for value in (name, version, checksum)):
                continue
            if not (_PACKAGE.fullmatch(name) and _VERSION.fullmatch(version) and _HEX.fullmatch(checksum)):
                continue
            if len(source) > 2048:
                continue
            filename = name + '-' + version + '.crate'
            identity = sha256(source.encode('utf-8')).hexdigest()
            result[(identity, filename, checksum)] = dict(source=source, filename=filename, checksum=checksum)
    return result


def _subdirectories(path):
    _directory(path)
    if not path.exists():
        return []
    result = []
    for child in path.iterdir():
        info = child.lstat()
        if _link(info):
            raise ValueError('A package cache contains a linked directory: ' + str(child))
        if stat.S_ISDIR(info.st_mode) and _BUCKET.fullmatch(child.name) and child.name not in ('.', '..'):
            result.append(child)
    return sorted(result)


def _copy_verified(source, destination, expected):
    """Use independent bytes, not hardlinks to a private writable cache/source."""
    _directory(source.parent)
    info = source.lstat()
    if _link(info) or not stat.S_ISREG(info.st_mode) or info.st_size > ARCHIVE_LIMIT:
        raise ValueError('A cached archive is not a bounded regular file.')
    _directory(destination.parent, create=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.archive-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            input_fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            digest, total = sha256(), 0
            with os.fdopen(input_fd, 'rb') as stream:
                measured = os.fstat(stream.fileno())
                if not stat.S_ISREG(measured.st_mode) or measured.st_size > ARCHIVE_LIMIT:
                    raise ValueError('A cached archive changed before reading.')
                for chunk in iter(lambda: stream.read(131072), b''):
                    total += len(chunk)
                    if total > ARCHIVE_LIMIT:
                        raise ValueError('A cached archive exceeds its size limit.')
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError('A cached Cargo archive does not match Cargo.lock: ' + source.name)
            output.flush()
            os.fsync(output.fileno())
        if destination.exists() or destination.is_symlink():
            info = destination.lstat()
            if _link(info) or not stat.S_ISREG(info.st_mode):
                raise ValueError('An archive destination is not a regular file.')
            existing = sha256()
            fd = os.open(destination, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            with os.fdopen(fd, 'rb') as stream:
                if os.fstat(stream.fileno()).st_size > ARCHIVE_LIMIT:
                    raise ValueError('An archive destination exceeds its size limit.')
                size = 0
                for chunk in iter(lambda: stream.read(131072), b''):
                    size += len(chunk)
                    if size > ARCHIVE_LIMIT:
                        raise ValueError('An archive destination grew beyond its size limit.')
                    existing.update(chunk)
            if existing.hexdigest() == expected:
                return 0
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return total


def _validated(manifest, checkout):
    if manifest.get('version') != VERSION or manifest.get('lane') not in LANES:
        raise ValueError('Unsupported package cache manifest.')
    if repository_key(checkout) != manifest.get('repository'):
        raise ValueError('Package cache manifest belongs to another repository.')
    root = _directory(manifest['shared_root'])
    execution = _directory(manifest['execution_root'])
    scope = root / ('v' + str(VERSION)) / manifest['repository'] / manifest['lane']
    result = []
    for item in manifest.get('caches', []):
        if item.get('shared'):
            _directory(_contained(item['shared'], scope))
        if item.get('private'):
            _directory(_contained(item['private'], execution))
        result.append(item)
    return result


def _transfer(manifest, checkout, *, publish, max_bytes):
    result = dict(files=0, bytes=0, errors=[])
    try:
        items = _validated(manifest, checkout)
        for item in items:
            if item.get('kind') != 'cargo-archives':
                continue
            packages = _packages(_absolute(checkout), item.get('components', []))
            shared = Path(item['shared'])
            private = Path(item['private']) / 'registry' / 'cache'
            buckets = _subdirectories(private) if publish else []
            source_buckets = {}
            for source_key, filename, checksum in packages:
                if result['bytes'] >= max_bytes:
                    return result
                if not publish and source_key not in source_buckets:
                    source_buckets[source_key] = _subdirectories(shared / source_key)
                candidates = buckets if publish else source_buckets[source_key]
                for bucket in candidates:
                    source = bucket / filename if publish else bucket / (checksum + '.crate')
                    destination = (shared / source_key / bucket.name / (checksum + '.crate') if publish
                                   else private / bucket.name / filename)
                    if not source.exists() and not source.is_symlink():
                        continue
                    try:
                        # Limit a single pass even with an unusually large lockfile.
                        size = source.lstat().st_size
                        if size > max_bytes - result['bytes']:
                            return result
                        count = _copy_verified(source, destination, checksum)
                        result['files'] += bool(count)
                        result['bytes'] += count
                    except (OSError, ValueError) as error:
                        result['errors'].append(str(error))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        result['errors'].append(str(error))
    return result


def seed(manifest, checkout, *, max_bytes=TRANSFER_LIMIT):
    """Copy lock-verified archives from the pool before project commands run."""
    return _transfer(manifest, checkout, publish=False, max_bytes=max_bytes)


def harvest(manifest, checkout, *, max_bytes=TRANSFER_LIMIT):
    """Publish downloaded archives afterwards; failed checks may still download."""
    return _transfer(manifest, checkout, publish=True, max_bytes=max_bytes)
