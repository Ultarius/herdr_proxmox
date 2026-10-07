"""Construct a bounded matched gateway/UI package; never authorize promotion."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile

LIMIT = 256 * 1024 * 1024
FILE_LIMIT = 20000
REQUIRED = ('web/gateway/server.py', 'web/public/index.html',
            'web/public/main.dart.js', 'web/public/dashboard/index.html')


def retain(tree, folder, run):
    tree, folder = Path(tree), Path(folder)
    for required in REQUIRED:
        if not (tree / required).is_file():
            raise ValueError('Full deployment output is incomplete: ' + required)
    files, total = [], 0
    for part in ('web/gateway', 'web/public'):
        source = tree / part
        if source.is_symlink() or source.parent.is_symlink():
            raise ValueError('Deployment package must not use symlinks.')
        for current, directories, names in os.walk(source, followlinks=False):
            directories.sort()
            for name in directories:
                if (Path(current) / name).is_symlink():
                    raise ValueError('Deployment package must not use symlinks.')
            for name in sorted(names):
                path = Path(current) / name
                relative = path.relative_to(tree).as_posix()
                if '__pycache__' in path.parts or path.suffix == '.pyc':
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Deployment package requires regular files.')
                total += info.st_size
                files.append((path, relative))
                if total > LIMIT or len(files) > FILE_LIMIT:
                    raise ValueError('Deployment package exceeds retention limit.')
    try:
        version = (tree / 'VERSION').read_text().strip()
    except OSError:
        version = 'local'
    identity = dict(build_id=run['id'], source_sha=run['target'], gateway_version=version,
                    package_version=version, deployment_mode='local')
    temporary = folder / 'deployment.tmp'
    with tarfile.open(temporary, 'w:gz') as archive:
        for path, relative in files:
            archive.add(path, arcname='herdr-proxmox/' + relative, recursive=False)
        for name, content in [('BUILD.json', json.dumps(identity)), ('VERSION', version + '\n')]:
            payload = content.encode()
            entry = tarfile.TarInfo('herdr-proxmox/' + name)
            entry.size, entry.mode = len(payload), 0o644
            archive.addfile(entry, io.BytesIO(payload))
    digest = hashlib.sha256()
    with temporary.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    manifest = dict(identity, sha256=digest.hexdigest(), bytes=temporary.stat().st_size,
                    file_count=len(files) + 2, validation_exit_code=run['exit_code'] if 'exit_code' in run else 0,
                    deployment_authorized=False, checks=run.get('checks', []),
                    toolchain_pin=run.get('toolchain_pin'),
                    task_id=run.get('task_id'), validation_waiver=run.get('validation_waiver'),
                    required_checks_verified=run.get('required_checks_verified', False))
    temporary.replace(folder / 'deployment.tar.gz')
    (folder / 'deployment-manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest
