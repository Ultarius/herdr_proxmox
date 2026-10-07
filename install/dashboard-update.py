#!/usr/bin/python3
"""Root-only fixed deployment worker: releases, local promotion and rollback.

It never executes scripts from an archive and accepts no path outside the
allowed roots. Local promotion installs a retained deployment package produced
by the gateway build runner; the gateway enforces administrator approval and
this worker re-verifies the approved build ID and SHA-256 before installing.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
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
            'web/public/main.dart.js', 'web/public/dashboard/index.html', 'VERSION')
RELEASE = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+')
# Operator state that outlives any single deployment: task records and the
# GitHub publishing credential.
PRESERVED_CONFIG = ('tasks.sqlite3', 'github.json')
BUILD_ID = re.compile(r'[a-f0-9]{32}')
SHA256 = re.compile(r'[a-f0-9]{64}')
SOURCE = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')


def status(state, **fields):
    temporary = STATE / 'status.tmp'
    temporary.write_text(json.dumps(dict(state=state, **fields)))
    temporary.chmod(0o644)
    temporary.replace(STATE / 'status.json')
    # Requests are consumed and status is replaced. Keep operator approval and
    # deployment transitions in a durable root-owned record as well.
    audit = STATE / 'audit.jsonl'
    with audit.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(time_ns=time.time_ns(), state=state, **fields)) + '\n')
    audit.chmod(0o600)


def download(url, target, limit):
    with urlopen(Request(url, headers={'User-Agent': 'herdr-proxmox-updater'}), timeout=60) as response:
        with target.open('wb') as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError('Release download exceeds size limit.')
                output.write(chunk)


def service(action):
    subprocess.run(['systemctl', action, 'herdr-web'], check=True, timeout=60)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_request():
    """Reject symlinks and bound the only caller-controlled input."""
    if REQUEST.is_symlink() or REQUEST.stat().st_size > 4096:
        raise ValueError('Invalid update request.')
    payload = json.loads(REQUEST.read_text())
    if not isinstance(payload, dict):
        raise ValueError('Invalid update request.')
    mode = payload.get('mode', 'release')
    if mode not in ('release', 'local', 'rollback', 'provenance'):
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
    if not isinstance(version, str) or not RELEASE.fullmatch(version):
        raise ValueError('Invalid release version.')
    return dict(mode='release', version=version, actor=str(payload.get('actor', ''))[:80])


def extract(archive, staging):
    with tarfile.open(archive) as package:
        members = package.getmembers()
        if len(members) > 20000 or sum(m.size for m in members) > 750_000_000:
            raise ValueError('Deployment extraction exceeds size limit.')
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe deployment archive entry.')
        package.extractall(staging, members=members, filter='data')
    source = staging / 'herdr-proxmox'
    for required in REQUIRED:
        if not (source / required).is_file():
            raise ValueError('Incomplete deployment package.')
    return source


def resolve_tag_sha(version):
    """Best-effort GitHub tag-to-commit lookup; never blocks an installation."""
    if not isinstance(version, str) or not RELEASE.fullmatch(version):
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
    else:
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
    (ROOT / 'BUILD.json').write_text(json.dumps(build_identity))
    (ROOT / 'BUILD.json').chmod(0o644)


def backup_current():
    backup = STATE / ('backup-' + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    shutil.copytree(ROOT / 'web', backup / 'web')
    if (ROOT / 'install').exists():
        shutil.copytree(ROOT / 'install', backup / 'install')
    shutil.copytree(CONFIG, backup / 'config')
    if (ROOT / 'VERSION').exists():
        shutil.copy2(ROOT / 'VERSION', backup / 'VERSION')
    if (ROOT / 'BUILD.json').exists():
        shutil.copy2(ROOT / 'BUILD.json', backup / 'BUILD.json')
    return backup


def restore(backup):
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
        (ROOT / 'BUILD.json').unlink(missing_ok=True)
        if (backup / 'BUILD.json').exists():
            shutil.copy2(backup / 'BUILD.json', ROOT / 'BUILD.json')
    finally:
        service('start')


def rollback(actor=''):
    backups = sorted((path for path in STATE.glob('backup-*')
                      if path.is_dir() and (path / 'web').is_dir() and (path / 'config').is_dir()),
                     key=lambda path: path.name)
    if not backups:
        raise ValueError('No deployment backup is available to restore.')
    backup = backups[-1]
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
        restore(backup)
        wait_for_ready(expected)
    except Exception as error:
        restore(safety)
        error.recovery_backup = str(safety)
        raise
    status('complete', mode='rollback', actor=actor, restored=str(backup), safety=str(safety), backup=str(safety))


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
        if request['mode'] == 'release':
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
            if request['mode'] == 'release':
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
            if request['mode'] == 'release':
                expected = request['version']
            subprocess.run(['/usr/bin/python3', '-m', 'compileall', '-q', str(source / 'web/gateway')], check=True)
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
        if replaced:
            restore(backup)
        elif stopped:
            service('start')
        REQUEST.unlink(missing_ok=True)
        status('failed', error=str(exc)[:500], rolled_back=replaced,
               actor=locals().get('request', {}).get('actor', ''),
               backup=str(backup) if backup else getattr(exc, 'recovery_backup', previous_backup))
        raise


if __name__ == '__main__':
    main()
