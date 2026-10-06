#!/usr/bin/python3
"""Root-only fixed repository updater. Never executes scripts from an archive."""
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


def status(state, **fields):
    temporary = STATE / 'status.tmp'
    temporary.write_text(json.dumps(dict(state=state, **fields)))
    temporary.chmod(0o644)
    temporary.replace(STATE / 'status.json')


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


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root through herdr-update.service.')
    STATE.mkdir(exist_ok=True)
    stopped = False
    replaced = False
    backup = None
    try:
        # Reject symlinks and bound the only caller-controlled input.
        if REQUEST.is_symlink() or REQUEST.stat().st_size > 1024:
            raise ValueError('Invalid update request.')
        version = json.loads(REQUEST.read_text()).get('version', '')
        if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
            raise ValueError('Invalid release version.')
        REQUEST.unlink()
        status('running', version=version)
        with tempfile.TemporaryDirectory(prefix='herdr-update-') as staging:
            staging = Path(staging)
            archive, checksum = staging / 'release.tar.gz', staging / 'checksum'
            base = f'https://github.com/Ultarius/herdr_proxmox/releases/download/{version}'
            download(base + '/herdr-proxmox.tar.gz', archive, 250_000_000)
            download(base + '/herdr-proxmox.tar.gz.sha256', checksum, 4096)
            expected = checksum.read_text().strip().split()
            if len(expected) != 2 or expected[1] != 'herdr-proxmox.tar.gz' or not re.fullmatch('[a-fA-F0-9]{64}', expected[0]):
                raise ValueError('Invalid release checksum file.')
            if hashlib.sha256(archive.read_bytes()).hexdigest() != expected[0].lower():
                raise ValueError('Release checksum mismatch.')
            with tarfile.open(archive) as package:
                members = package.getmembers()
                if len(members) > 20000 or sum(m.size for m in members) > 750_000_000:
                    raise ValueError('Release extraction exceeds size limit.')
                for member in members:
                    path = Path(member.name)
                    if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                        raise ValueError('Unsafe release archive entry.')
                package.extractall(staging, members=members, filter='data')
            source = staging / 'herdr-proxmox'
            for required in ('web/gateway/server.py', 'web/public/index.html', 'web/public/main.dart.js', 'web/public/dashboard/index.html', 'VERSION'):
                if not (source / required).is_file():
                    raise ValueError('Incomplete release package.')
            if (source / 'VERSION').read_text().strip() != version:
                raise ValueError('Release version does not match package.')
            subprocess.run(['/usr/bin/python3', '-m', 'compileall', '-q', str(source / 'web/gateway')], check=True)
            service('stop')
            stopped = True
            backup = STATE / ('backup-' + str(time.time_ns()))
            backup.mkdir(mode=0o700)
            shutil.copytree(ROOT / 'web', backup / 'web')
            if (ROOT / 'install').exists():
                shutil.copytree(ROOT / 'install', backup / 'install')
            config = CONFIG
            shutil.copytree(config, backup / 'config')
            if (ROOT / 'VERSION').exists():
                shutil.copy2(ROOT / 'VERSION', backup / 'VERSION')
            replaced = True
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
            service('start')
            # Gateway loads storage on startup; HTTP verifies it actually serves requests.
            for attempt in range(30):
                try:
                    with urlopen('http://127.0.0.1:8787/', timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(1)
            else:
                raise ValueError('Updated gateway failed its startup check.')
            status('complete', version=version, backup=str(backup))
    except Exception as exc:
        if replaced:
            service('stop')
            shutil.rmtree(ROOT / 'web')
            shutil.copytree(backup / 'web', ROOT / 'web')
            if (ROOT / 'install').exists():
                shutil.rmtree(ROOT / 'install')
            if (backup / 'install').exists():
                shutil.copytree(backup / 'install', ROOT / 'install')
            config = CONFIG
            shutil.rmtree(config)
            shutil.copytree(backup / 'config', config)
            subprocess.run(['chown', '-R', 'herdr:herdr', str(config)], check=True)
            (ROOT / 'VERSION').unlink(missing_ok=True)
            if (backup / 'VERSION').exists():
                shutil.copy2(backup / 'VERSION', ROOT / 'VERSION')
            service('start')
        elif stopped:
            service('start')
        REQUEST.unlink(missing_ok=True)
        status('failed', error=str(exc)[:500], rolled_back=replaced)
        raise


if __name__ == '__main__':
    main()
