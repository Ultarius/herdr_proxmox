#!/usr/bin/python3
"""Root-only fixed deployment worker: releases, local promotion and rollback.

It never executes scripts from an archive and accepts no path outside the
allowed roots. Local promotion installs a retained deployment package produced
by the gateway build runner; the gateway enforces administrator approval and
this worker re-verifies the approved build ID and SHA-256 before installing.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

ROOT = Path('/opt/herdr-web')
STATE = Path('/var/lib/herdr-updater')
REQUEST = Path('/var/lib/herdr-update-requests/request.json')
CONFIG = Path('/home/herdr/.config/herdr-web')
LOCAL_ARTIFACTS = Path('/home/herdr/herdr-validation')
REPOSITORY = 'Ultarius/herdr_proxmox'
REQUIRED = ('web/gateway/server.py', 'web/public/index.html',
            'web/public/main.dart.js', 'web/public/dashboard/index.html', 'VERSION',
            # The release must carry the project's version declaration and the
            # catalog that reads it. Without them the deployed gateway cannot
            # resolve the required SDK version, so refuse the package rather
            # than install one that has quietly lost that ability.
            '.fvmrc', 'install/toolchains.json')
RELEASE = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+')
ALPHA = re.compile(r'alpha-[a-z0-9-]+-[a-f0-9]{12}-b[1-9][0-9]*')
# Operator state that outlives any single deployment: task records and the
# GitHub publishing credential.
PRESERVED_CONFIG = ('tasks.sqlite3', 'github.json')
BUILD_ID = re.compile(r'[a-f0-9]{32}')
SHA256 = re.compile(r'[a-f0-9]{64}')
SOURCE = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
STORAGE_RESERVE = 256 * 1024 * 1024
INODE_RESERVE = 1024


def footprint(path):
    """Bound copy allocation; refuse links rather than undercount their targets."""
    def measure(item, optional=False):
        try:
            info = item.lstat()
        except FileNotFoundError as error:
            if optional:
                return 0, 0
            raise ValueError(f'Deployment tree changed while measuring {item}; retry the update.') from error
        try:
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f'Deployment tree contains a symbolic link at {item}. '
                                 'Use regular files/directories before updating.')
            size, count = max(4096, ((info.st_size + 4095) // 4096) * 4096), 1
            if stat.S_ISDIR(info.st_mode):
                for child in item.iterdir():
                    child_size, child_count = measure(child)
                    size, count = size + child_size, count + child_count
            elif not stat.S_ISREG(info.st_mode):
                raise ValueError(f'Deployment tree contains a special file at {item}.')
            return size, count
        except FileNotFoundError as error:
            raise ValueError(f'Deployment tree changed while measuring {item}; retry the update.') from error
    return measure(Path(path), optional=True)


def require_storage(allocations):
    """Sum simultaneous allocations on each filesystem, not each pathname."""
    volumes = {}
    for destination, size, entries in allocations:
        anchor = Path(destination)
        while not anchor.exists():
            anchor = anchor.parent
        device = anchor.stat().st_dev
        volume = volumes.setdefault(device, [{}, 0, 0])
        # Sample every destination even on one device: bind mounts and quotas
        # may report different availability. Keep the shared allocation total.
        volume[0][anchor] = None
        volume[1] += size
        volume[2] += entries
    for anchors, size, entries in volumes.values():
        for anchor in anchors:
            check_storage(anchor, size, entries)


def check_storage(anchor, size, entries):
    free = shutil.disk_usage(anchor).free
    needed = size + STORAGE_RESERVE
    reasons = []
    if free < needed:
        reasons.append(f'{free // (1024 * 1024)} MiB available; {((needed + 1048575) // 1048576)} MiB required')
    if hasattr(os, 'statvfs'):
        info = os.statvfs(anchor)
        if info.f_files and info.f_favail < entries + INODE_RESERVE:
            reasons.append(f'{info.f_favail} free inodes; {entries + INODE_RESERVE} required')
    if reasons:
        raise ValueError(f'Not enough disk space for dashboard update on {anchor}: {"; ".join(reasons)}. '
                         'Free disk space or expand the container disk, then retry. '
                         'RAM and swap do not provide disk space for installation.')


def deployment_storage(source, config_source=None):
    """Budget backup plus replacement without assuming old files are freed."""
    backup_paths = [ROOT / name for name in ('web', 'install', 'VERSION', '.fvmrc', 'BUILD.json')]
    backup_paths.append(CONFIG)
    backup = [footprint(path) for path in backup_paths]
    allocations = [(STATE, sum(size for size, _ in backup), sum(count for _, count in backup) + 1)]
    for name in ('web', 'install', 'VERSION', '.fvmrc', 'BUILD.json'):
        size, count = footprint(source / name)
        allocations.append((ROOT, size, count))
    # Legacy release packages omit BUILD.json, but replace_install creates it.
    if not (source / 'BUILD.json').exists():
        allocations.append((ROOT, 4096, 1))
    if config_source is not None:
        size, count = footprint(config_source)
        # Rollback also preserves current task records/credentials. Budget their
        # writes even if they have grown beyond the older saved configuration.
        preserved = [footprint(CONFIG / name) for name in PRESERVED_CONFIG]
        allocations.append((CONFIG, size + sum(n for n, _ in preserved), count + sum(n for _, n in preserved)))
    require_storage(allocations)


def failure_message(error):
    # copytree wraps ENOSPC in a list of strings rather than an OSError.
    if (isinstance(error, OSError) and error.errno == errno.EDQUOT
            or 'quota exceeded' in str(error).lower()):
        return ('Dashboard update exceeded a storage quota. Increase the container/filesystem allowance or free space '
                'inside that allowance, then retry. On Proxmox, inspect pct config <ctid> and the storage backend; '
                'df may show filesystem space beyond the container allowance.')
    if (isinstance(error, OSError) and error.errno == errno.ENOSPC
            or 'no space left on device' in str(error).lower()):
        return ('Dashboard update could not allocate filesystem space. Free disk space or expand the container disk, '
                'then retry. Check both available bytes and inodes with df -h and df -i. '
                'If space remains, inspect filesystem metadata, quotas and backing storage; free space alone does not identify the cause.')
    return str(error)[:500]


def warn(message):
    """The journal remains usable when the status volume cannot be written."""
    try:
        print(message, file=sys.stderr, flush=True)
    except OSError:
        pass


def status(state, **fields):
    temporary = STATE / 'status.tmp'
    temporary.write_text(json.dumps(dict(state=state, **fields)))
    temporary.chmod(0o644)
    temporary.replace(STATE / 'status.json')
    # Requests are consumed and status is replaced. Keep operator approval and
    # deployment transitions in a durable root-owned record as well.
    audit = STATE / 'audit.jsonl'
    try:
        with audit.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(time_ns=time.time_ns(), state=state, **fields)) + '\n')
        audit.chmod(0o600)
    except OSError as error:
        warn(f'Could not append updater audit: {error}')


def download(url, target, limit):
    with urlopen(Request(url, headers={'User-Agent': 'herdr-proxmox-updater'}), timeout=60) as response:
        length = response.headers.get('Content-Length', '')
        expected = int(length) if isinstance(length, str) and length.isdecimal() else limit
        if expected > limit:
            raise ValueError('Release download exceeds size limit.')
        require_storage([(target.parent, expected, 1)])
        with target.open('wb') as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError('Release download exceeds size limit.')
                if total > expected:
                    # A server can omit or understate Content-Length. Never
                    # trust it instead of the streamed size cap/free-space check.
                    require_storage([(target.parent, len(chunk), 0)])
                output.write(chunk)


def service(action):
    subprocess.run(['systemctl', action, 'herdr-web'], check=True, timeout=60)


def sha256(path, sync=False):
    digest = hashlib.sha256()
    with Path(path).open('r+b' if sync else 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        if sync:
            os.fsync(stream.fileno())
    return digest.hexdigest()


def sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def backup_inventory(backup, sync=False):
    """Record the entire copy, including empty directories and file contents."""
    footprint(backup)  # Refuse links/special files before walking or hashing.
    files, directories = [], []
    for path in sorted(backup.rglob('*')):
        relative = path.relative_to(backup).as_posix()
        if relative == 'manifest.json':
            continue
        if path.is_dir():
            directories.append(relative)
        else:
            files.append(dict(path=relative, size=path.stat().st_size, sha256=sha256(path, sync=sync)))
    if sync:
        for name in sorted(directories, key=lambda name: name.count('/'), reverse=True):
            sync_directory(backup / name)
    return dict(version=1, files=files, directories=directories, file_count=len(files))


def verify_config(config):
    """Validate frozen DB/WAL copies; SQLite may create sidecars only in scratch."""
    for database in config.rglob('*.sqlite3'):
        with tempfile.TemporaryDirectory(prefix='herdr-backup-check-') as folder:
            scratch = Path(folder)
            paths = [database]
            wal = database.with_name(database.name + '-wal')
            if wal.exists():
                paths.append(wal)
            sizes = [footprint(path) for path in paths]
            require_storage([(scratch, sum(size for size, _ in sizes), len(paths) + 2)])
            for path in paths:
                shutil.copy2(path, scratch / path.name)
            target = scratch / database.name
            connection = None
            try:
                with target.open('rb') as stream:
                    if stream.read(16) != b'SQLite format 3\x00':
                        raise ValueError('missing SQLite header')
                connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=2)
                connection.execute('PRAGMA query_only = ON')
                if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('SQLite quick_check failed')
            except (sqlite3.Error, ValueError) as error:
                raise ValueError(f'Backup configuration database is invalid: {database.name}: {error}') from error
            finally:
                if connection is not None:
                    connection.close()


def verify_backup(backup):
    """Always revalidate contents; a sidecar certificate is never a cache hit."""
    if backup.is_symlink() or not re.fullmatch(r'backup-[0-9]+', backup.name):
        raise ValueError('Only a completed backup directory can be restored.')
    if not (backup / 'web').is_dir() or not (backup / 'config').is_dir():
        raise ValueError('Backup is missing web or configuration content.')
    actual = backup_inventory(backup)
    manifest = backup / 'manifest.json'
    if manifest.exists():
        try:
            expected = json.loads(manifest.read_text())
        except (OSError, ValueError) as error:
            raise ValueError('Backup manifest is unreadable.') from error
        if expected != actual:
            raise ValueError('Backup manifest does not match its files; refusing restore.')
        method = 'manifest'
    else:
        # BUILD.json was the final optional copy in legacy workers. Its presence
        # is a completion witness, not a substitute for a historical manifest.
        try:
            identity = json.loads((backup / 'BUILD.json').read_text())
        except (OSError, ValueError) as error:
            raise ValueError('Legacy backup is unverified: missing or invalid BUILD.json witness.') from error
        if (not isinstance(identity, dict) or not str(identity.get('build_id', '')).strip()
                or identity.get('deployment_mode') not in ('release', 'local')
                or any(not (backup / name).is_file() for name in
                       ('web/gateway/server.py', 'web/public/index.html', 'VERSION'))):
            raise ValueError('Legacy backup is unverified: incomplete identity or required files.')
        for name in ('install', '.fvmrc'):
            if (ROOT / name).exists() and not (backup / name).exists():
                raise ValueError(f'Legacy backup is unverified: missing {name}; refusing a mixed deployment.')
        method = 'legacy-witness'
    verify_config(backup / 'config')
    evidence = dict(method=method, file_count=actual['file_count'],
                    digest=hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest())
    previous = backup_certificates(backup.parent).get(backup.name)
    if method == 'legacy-witness' and previous is not None:
        if previous.get('path') != str(backup) or previous.get('digest') != evidence['digest']:
            raise ValueError('Legacy backup changed since certification; refusing restore.')
    return evidence


def backup_certificates(state):
    try:
        entries = json.loads((state / 'backups.json').read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError('Backup certification registry is unreadable; refusing to reset its evidence.') from error
    if not isinstance(entries, dict) or any(not isinstance(value, dict) for value in entries.values()):
        raise ValueError('Backup certification registry is invalid.')
    return entries


def record_backup_verification(backup, evidence):
    """Keep certification outside immutable legacy backups; recheck on restore."""
    registry = STATE / 'backups.json'
    entries = backup_certificates(STATE)
    entries[backup.name] = dict(evidence, path=str(backup), mtime_ns=backup.stat().st_mtime_ns,
                                verified_at_ns=time.time_ns())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=STATE, prefix='backups-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            temporary.chmod(0o600)
            json.dump(entries, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(registry)
        sync_directory(STATE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_request():
    """Reject symlinks and bound the only caller-controlled input."""
    if REQUEST.is_symlink() or REQUEST.stat().st_size > 4096:
        raise ValueError('Invalid update request.')
    payload = json.loads(REQUEST.read_text())
    if not isinstance(payload, dict):
        raise ValueError('Invalid update request.')
    mode = payload.get('mode', 'release')
    if mode not in ('release', 'alpha', 'local', 'rollback', 'provenance'):
        raise ValueError('Invalid deployment mode.')
    if mode in ('rollback', 'provenance'):
        return dict(mode=mode, actor=str(payload.get('actor', ''))[:80])
    if mode == 'local':
        build_id, digest, path = payload.get('build_id'), payload.get('sha256'), payload.get('path')
        if not isinstance(build_id, str) or not BUILD_ID.fullmatch(build_id):
            raise ValueError('Invalid local deployment build ID.')
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError('Invalid local deployment checksum.')
        if not isinstance(path, str):
            raise ValueError('Invalid local deployment path.')
        archive = Path(path)
        allowed = LOCAL_ARTIFACTS.resolve()
        if (archive.is_symlink() or not archive.is_absolute() or archive.name != 'deployment.tar.gz'
                or archive.parent.name != build_id or archive.parent.resolve().parent != allowed):
            raise ValueError('Local deployment package is outside the retained build directory.')
        return dict(mode='local', build_id=build_id, sha256=digest, path=archive,
                    actor=str(payload.get('actor', ''))[:80])
    version = payload.get('version', '')
    if not isinstance(version, str) or not (ALPHA if mode == 'alpha' else RELEASE).fullmatch(version):
        raise ValueError('Invalid release version.')
    return dict(mode=mode, version=version, actor=str(payload.get('actor', ''))[:80])


def extract(archive, staging):
    with tarfile.open(archive) as package:
        members = package.getmembers()
        if len(members) > 20000 or sum(m.size for m in members) > 750_000_000:
            raise ValueError('Deployment extraction exceeds size limit.')
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe deployment archive entry.')
        allocation = sum(max(4096, ((member.size + 4095) // 4096) * 4096) for member in members)
        require_storage([(staging, allocation, len(members))])
        package.extractall(staging, members=members, filter='data')
    source = staging / 'herdr-proxmox'
    for required in REQUIRED:
        if not (source / required).is_file():
            raise ValueError('Incomplete deployment package.')
    return source


def resolve_tag_sha(version):
    """Best-effort GitHub tag-to-commit lookup; never blocks an installation."""
    if not isinstance(version, str) or not (RELEASE.fullmatch(version) or ALPHA.fullmatch(version)):
        return None
    headers = {'User-Agent': 'herdr-proxmox-updater', 'Accept': 'application/vnd.github+json'}
    try:
        url = f'https://api.github.com/repos/{REPOSITORY}/git/ref/tags/{version}'
        seen = set()
        for _ in range(5):
            with urlopen(Request(url, headers=headers), timeout=15) as response:
                target = json.loads(response.read(100_000)).get('object') or {}
            sha = str(target.get('sha', ''))
            if not SOURCE.fullmatch(sha):
                return None
            if target.get('type') == 'commit':
                return sha
            if target.get('type') != 'tag' or sha in seen:
                return None
            seen.add(sha)
            # Never follow response-supplied URLs outside the fixed API origin.
            url = f'https://api.github.com/repos/{REPOSITORY}/git/tags/{sha}'
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def release_identity(source, version):
    identity = dict(build_id=version, source_sha=None,
                    package_version=version, deployment_mode='release')
    identity_file = source / 'BUILD.json'
    if identity_file.is_file():
        supplied = json.loads(identity_file.read_text())
        if (not isinstance(supplied, dict) or supplied.get('build_id') != version
                or supplied.get('deployment_mode') != 'release'
                or not SOURCE.fullmatch(str(supplied.get('source_sha', '')))):
            raise ValueError('Release build identity does not match the package.')
        identity['source_sha'] = supplied['source_sha']
        if ALPHA.fullmatch(version):
            if supplied.get('channel') != 'alpha' or not str(supplied.get('source_branch', '')).startswith('alpha/'):
                raise ValueError('Alpha package must identify its feature branch and channel.')
            branch = supplied['source_branch']
            slug = re.sub(r'[^a-z0-9]+', '-', branch[6:].lower()).strip('-')[:48] or 'feature'
            prefix = f'alpha-{slug}-{hashlib.sha256(branch.encode()).hexdigest()[:12]}-b'
            if not version.startswith(prefix):
                raise ValueError('Alpha tag does not match the declared feature branch.')
            identity.update(channel='alpha', source_branch=branch)
    else:
        if ALPHA.fullmatch(version):
            raise ValueError('Alpha packages require source identity.')
        # Legacy or repackaged releases carry no BUILD.json. Resolve the tag's
        # commit as a best-effort association, not proof of the archive's contents.
        identity['source_sha'] = resolve_tag_sha(version)
    return identity


def wait_for_ready(expected=None):
    """Authenticated readiness: the running gateway proves auth and build identity."""
    try:
        token = (CONFIG / 'token').read_text().strip()
    except OSError as error:
        raise ValueError('Authenticated startup check requires the gateway token.') from error
    if not token:
        raise ValueError('Authenticated startup check requires the gateway token.')
    for attempt in range(30):
        try:
            request = Request('http://127.0.0.1:8787/api/build', headers={'Authorization': 'Bearer ' + token})
            with urlopen(request, timeout=2) as response:
                if response.status != 200:
                    raise OSError('Unhealthy response')
                if expected:
                    identity = json.loads(response.read(100_000))
                    if identity.get('build_id') != expected:
                        raise OSError('Running build identity does not match the deployment')
            # Build metadata is a file read; probe storage separately so a
            # healthy root page cannot hide a broken organization database.
            request = Request('http://127.0.0.1:8787/api/organizations/state',
                              headers={'Authorization': 'Bearer ' + token})
            with urlopen(request, timeout=2) as response:
                if response.status != 200:
                    raise OSError('Storage readiness failed')
                if not isinstance(json.loads(response.read(2_000_001)), dict):
                    raise OSError('Invalid storage readiness response')
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise ValueError('Updated gateway failed its startup check.')


def replace_install(source, build_identity):
    """Replace gateway and UI together, preserving installers and provenance."""
    for part in ('gateway', 'public'):
        shutil.rmtree(ROOT / 'web' / part)
        shutil.copytree(source / 'web' / part, ROOT / 'web' / part)
        for installed in (ROOT / 'web' / part).rglob('*'):
            installed.chmod(0o755 if installed.is_dir() else 0o644)
        (ROOT / 'web' / part).chmod(0o755)
    # Keep service installers matched to the deployed gateway. Copy only;
    # privileged setup remains an explicit root/operator action.
    if (source / 'install').is_dir():
        shutil.copytree(source / 'install', ROOT / 'install', dirs_exist_ok=True)
        (ROOT / 'install').chmod(0o755)
        for installed in (ROOT / 'install').rglob('*'):
            installed.chmod(0o755 if installed.is_dir() else 0o644)
    shutil.copy2(source / 'VERSION', ROOT / 'VERSION')
    if (source / '.fvmrc').is_file():
        shutil.copy2(source / '.fvmrc', ROOT / '.fvmrc')
        (ROOT / '.fvmrc').chmod(0o644)
    (ROOT / 'BUILD.json').write_text(json.dumps(build_identity))
    (ROOT / 'BUILD.json').chmod(0o644)


def backup_current():
    backup = STATE / ('backup-' + str(time.time_ns()))
    partial = backup.with_name(backup.name + '.partial')
    partial.mkdir(mode=0o700)
    try:
        shutil.copytree(ROOT / 'web', partial / 'web')
        if (ROOT / 'install').exists():
            shutil.copytree(ROOT / 'install', partial / 'install')
        shutil.copytree(CONFIG, partial / 'config')
        for name in ('VERSION', '.fvmrc', 'BUILD.json'):
            if (ROOT / name).exists():
                shutil.copy2(ROOT / name, partial / name)
        verify_config(partial / 'config')
        manifest = partial / 'manifest.json'
        with manifest.open('x') as stream:
            json.dump(backup_inventory(partial, sync=True), stream)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(partial)
        partial.rename(backup)
        sync_directory(STATE)
    except Exception:
        try:
            # Only remove the private, newly created staging directory. Failed
            # cleanup leaves a .partial directory that rollback never selects.
            if partial.resolve().parent != STATE.resolve() or partial.is_symlink():
                raise ValueError('Backup staging directory changed during cleanup.')
            shutil.rmtree(partial)
        except (OSError, ValueError) as error:
            warn(f'Could not remove incomplete backup {partial}: {error}')
        raise
    return backup


def restore(backup):
    # Validate again immediately before any stop/removal, including automated
    # recovery paths which do not pass through rollback's candidate selection.
    evidence = verify_backup(backup)
    record_backup_verification(backup, evidence)
    service('stop')
    # Tasks and GitHub credentials were created after this deployment backup.
    # Restoring the older configuration directory must not silently discard
    # them, and an older gateway cannot read them either way.
    preserved = {}
    for name in PRESERVED_CONFIG:
        source = CONFIG / name
        if source.is_file() and not source.is_symlink():
            preserved[name] = source.read_bytes()
    try:
        shutil.rmtree(ROOT / 'web')
        shutil.copytree(backup / 'web', ROOT / 'web')
        if (ROOT / 'install').exists():
            shutil.rmtree(ROOT / 'install')
        if (backup / 'install').exists():
            shutil.copytree(backup / 'install', ROOT / 'install')
        shutil.rmtree(CONFIG)
        shutil.copytree(backup / 'config', CONFIG)
        for name, content in preserved.items():
            target = CONFIG / name
            target.write_bytes(content)
            target.chmod(0o600)
        subprocess.run(['chown', '-R', 'herdr:herdr', str(CONFIG)], check=True)
        (ROOT / 'VERSION').unlink(missing_ok=True)
        if (backup / 'VERSION').exists():
            shutil.copy2(backup / 'VERSION', ROOT / 'VERSION')
        (ROOT / '.fvmrc').unlink(missing_ok=True)
        if (backup / '.fvmrc').exists():
            shutil.copy2(backup / '.fvmrc', ROOT / '.fvmrc')
        (ROOT / 'BUILD.json').unlink(missing_ok=True)
        if (backup / 'BUILD.json').exists():
            shutil.copy2(backup / 'BUILD.json', ROOT / 'BUILD.json')
    finally:
        service('start')
    return evidence


def rollback(actor=''):
    backups = sorted((path for path in STATE.glob('backup-*')
                      if re.fullmatch(r'backup-[0-9]+', path.name) and not path.is_symlink()
                      and path.is_dir() and (path / 'web').is_dir() and (path / 'config').is_dir()),
                     key=lambda path: int(path.name.removeprefix('backup-')))
    if not backups:
        raise ValueError('No deployment backup is available to restore.')
    backup = None
    skipped = []
    for candidate in reversed(backups):
        try:
            evidence = verify_backup(candidate)
        except (OSError, ValueError) as error:
            skipped.append(dict(backup=candidate.name, reason=str(error)[:300]))
            warn(f'Ignoring unverified backup {candidate.name}: {error}')
            continue
        backup = candidate
        break
    if backup is None:
        reasons = '; '.join(f'{item["backup"]}: {item["reason"]}' for item in skipped[:3])
        raise ValueError(f'No verified deployment backup is available. {reasons}')
    record_backup_verification(backup, evidence)
    deployment_storage(backup, backup / 'config')
    try:
        expected = json.loads((backup / 'BUILD.json').read_text()).get('build_id')
    except (OSError, ValueError, AttributeError):
        expected = None
    # Stop writers before copying SQLite files. Preserve the current state too:
    # an unsuccessful rollback must restore it rather than strand the operator.
    service('stop')
    try:
        safety = backup_current()
    except Exception:
        service('start')
        raise
    try:
        evidence = restore(backup)
        wait_for_ready(expected)
    except Exception as error:
        error.recovery_backup = str(safety)
        try:
            restore(safety)
        except Exception as recovery:
            error.recovery_error = failure_message(recovery)
            warn(f'Rollback safety restore failed: {error.recovery_error}; backup: {safety}')
        raise
    status('complete', mode='rollback', actor=actor, restored=str(backup), safety=str(safety), backup=str(safety),
           backup_verification=evidence, skipped_backups=skipped)


def provenance(actor='', backup=None):
    """Resolve a release tag commit for an installed deployment that lacks it."""
    identity_path = ROOT / 'BUILD.json'
    try:
        identity = json.loads(identity_path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError('Deployment identity is missing or unreadable.') from error
    if not isinstance(identity, dict) or identity.get('deployment_mode') == 'local':
        raise ValueError('Local deployments carry their own source provenance.')
    version = identity.get('package_version') or identity.get('build_id')
    if not isinstance(version, str) or not RELEASE.fullmatch(version):
        raise ValueError('Only release deployments can resolve source provenance.')
    if SOURCE.fullmatch(str(identity.get('source_sha', ''))):
        status('complete', mode='provenance', actor=actor, build_id=version,
               backup=backup, source_sha=identity['source_sha'], note='Source identity was already recorded.')
        return
    resolved = resolve_tag_sha(version)
    if not resolved:
        raise ValueError('The GitHub release tag commit could not be resolved.')
    identity['source_sha'] = resolved
    # Readers must see either the old identity or the complete replacement.
    temporary = ROOT / 'BUILD.json.tmp'
    with temporary.open('x') as output:
        json.dump(identity, output)
    temporary.chmod(0o644)
    temporary.replace(identity_path)
    status('complete', mode='provenance', actor=actor, build_id=version, source_sha=resolved, backup=backup)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root through herdr-update.service.')
    STATE.mkdir(exist_ok=True)
    stopped = False
    replaced = False
    backup = None
    previous_backup = None
    try:
        previous_status = json.loads((STATE / 'status.json').read_text())
        if isinstance(previous_status, dict):
            previous_backup = previous_status.get('backup')
    except (OSError, ValueError):
        pass
    try:
        request = read_request()
        if request['mode'] in ('release', 'alpha'):
            identity_path = ROOT / 'BUILD.json'
            if identity_path.exists():
                try:
                    identity = json.loads(identity_path.read_text())
                except ValueError as error:
                    raise ValueError('Deployment identity is unreadable; refusing a release update.') from error
                if not isinstance(identity, dict) or identity.get('deployment_mode') == 'local':
                    raise ValueError('Release installation is disabled for a local deployment.')
        REQUEST.unlink()
        status('running', mode=request['mode'],
               backup=previous_backup,
               **{key: request[key] for key in ('version', 'build_id', 'actor') if key in request})
        if request['mode'] == 'rollback':
            rollback(request.get('actor', ''))
            return
        if request['mode'] == 'provenance':
            provenance(request.get('actor', ''), previous_backup)
            return
        expected = None
        with tempfile.TemporaryDirectory(prefix='herdr-update-') as staging:
            staging = Path(staging)
            # Check temporary storage before downloading/copying the archive.
            if request['mode'] == 'local':
                require_storage([(staging, request['path'].stat().st_size, 1)])
            if request['mode'] in ('release', 'alpha'):
                archive, checksum = staging / 'release.tar.gz', staging / 'checksum'
                base = f'https://github.com/Ultarius/herdr_proxmox/releases/download/{request["version"]}'
                download(base + '/herdr-proxmox.tar.gz', archive, 250_000_000)
                download(base + '/herdr-proxmox.tar.gz.sha256', checksum, 4096)
                expected_file = checksum.read_text().strip().split()
                if (len(expected_file) != 2 or expected_file[1] != 'herdr-proxmox.tar.gz'
                        or not re.fullmatch('[a-fA-F0-9]{64}', expected_file[0])):
                    raise ValueError('Invalid release checksum file.')
                if sha256(archive) != expected_file[0].lower():
                    raise ValueError('Release checksum mismatch.')
                source = extract(archive, staging)
                if (source / 'VERSION').read_text().strip() != request['version']:
                    raise ValueError('Release version does not match package.')
                build_identity = release_identity(source, request['version'])
                if request['mode'] == 'alpha':
                    resolved = resolve_tag_sha(request['version'])
                    if not resolved or resolved != build_identity['source_sha']:
                        raise ValueError('Alpha package source does not match its immutable tag.')
            else:
                # Verify and extract the same root-owned copy. The retained
                # build file belongs to herdr and may change after approval.
                archive = staging / 'local.tar.gz'
                with request['path'].open('rb') as stream, archive.open('wb') as output:
                    total = 0
                    while chunk := stream.read(1024 * 1024):
                        total += len(chunk)
                        if total > 270_000_000:
                            raise ValueError('Local deployment package exceeds size limit.')
                        output.write(chunk)
                if sha256(archive) != request['sha256']:
                    raise ValueError('Local deployment package failed its checksum check.')
                source = extract(archive, staging)
                try:
                    build_identity = json.loads((source / 'BUILD.json').read_text())
                except (OSError, ValueError) as error:
                    raise ValueError('Local deployment package has no readable build identity.') from error
                if (not isinstance(build_identity, dict) or build_identity.get('deployment_mode') != 'local'
                        or build_identity.get('build_id') != request['build_id']
                        or not SOURCE.fullmatch(str(build_identity.get('source_sha', '')))):
                    raise ValueError('Local deployment identity does not match the approved build.')
                expected = build_identity['build_id']
            if request['mode'] in ('release', 'alpha'):
                expected = request['version']
            subprocess.run(['/usr/bin/python3', '-m', 'compileall', '-q', str(source / 'web/gateway')], check=True)
            # Staging, installation and backup may share a volume. Budget the
            # remaining simultaneous copies before stopping the live service.
            deployment_storage(source)
            service('stop')
            stopped = True
            backup = backup_current()
            replaced = True
            replace_install(source, build_identity)
            service('start')
            wait_for_ready(expected)
            status('complete', mode=request['mode'], backup=str(backup),
                   actor=request.get('actor', ''),
                   **{key: request[key] for key in ('version', 'build_id') if key in request})
    except Exception as exc:
        warn(failure_message(exc))
        recovery_error = getattr(exc, 'recovery_error', None)
        recovery_verification = None
        rolled_back = False
        try:
            if replaced:
                recovery_verification = restore(backup)
                rolled_back = True
            elif stopped:
                service('start')
        except Exception as recovery:
            recovery_error = failure_message(recovery)
        try:
            REQUEST.unlink(missing_ok=True)
        except OSError as cleanup:
            warn(f'Could not remove update request: {cleanup}')
        failed_request = locals().get('request', {})
        fields = dict(error=failure_message(exc), rolled_back=rolled_back,
                      **({'recovery_error': recovery_error} if recovery_error else {}),
                      **({'backup_verification': recovery_verification} if recovery_verification else {}),
                      **{key: failed_request[key] for key in ('mode', 'version', 'build_id') if key in failed_request},
                      actor=failed_request.get('actor', ''),
                      backup=str(backup) if backup else getattr(exc, 'recovery_backup', previous_backup))
        try:
            status('failed', **fields)
        except Exception as reporting:
            warn(f'Could not persist updater failure ({reporting}); recovery details: {json.dumps(fields)}')
        raise


if __name__ == '__main__':
    main()
