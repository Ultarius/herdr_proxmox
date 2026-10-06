#!/usr/bin/python3
"""Root-only fixed-purpose SDK installer. Accepts no arguments and no payload.

Like the dashboard updater, this worker never executes anything from a request.
The only accepted request is a bare install action; every command it runs comes
from the release itself. Installer output is retained as a bounded tail so a
failed run is diagnosable from the dashboard.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import stat

STATE = Path('/var/lib/herdr-sdk')
REQUEST = Path('/var/lib/herdr-sdk-requests/request.json')
INSTALLER = Path('/opt/herdr-web/install/dev-tools-install.sh')
OUTPUT_LIMIT = 1800
TIMEOUT_SECONDS = 1800


def parse_request(text):
    """Reject anything but the single supported action."""
    payload = json.loads(text)
    if payload != {'action': 'install'}:
        raise ValueError('Unsupported SDK install request.')
    return payload


def status(state, **fields):
    temporary = STATE / 'status.tmp'
    temporary.write_text(json.dumps(dict(state=state, **fields)))
    temporary.chmod(0o644)
    temporary.replace(STATE / 'status.json')


def install():
    result = subprocess.run(['bash', str(INSTALLER)], capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS,
                            env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
    # Keep stderr even when apt/curl produced stdout; it usually contains
    # the actual installer failure, and the status message retains the final tail.
    tail = ((result.stdout or '') + '\n' + (result.stderr or '')).strip()[-OUTPUT_LIMIT:]
    if result.returncode:
        raise ValueError('SDK installation failed. Inspect journalctl -u herdr-sdk. ' + tail)
    return tail


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root through herdr-sdk.service.')
    STATE.mkdir(exist_ok=True)
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        if REQUEST.is_symlink():
            raise ValueError('Invalid SDK install request.')
        with os.fdopen(os.open(REQUEST, flags), 'r') as request:
            metadata = os.fstat(request.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024:
                raise ValueError('Invalid SDK install request.')
            parse_request(request.read(1025))
        REQUEST.unlink()
        if not INSTALLER.is_file():
            raise ValueError('This dashboard release does not include the SDK installer.')
        status('running', started_at=datetime.now(timezone.utc).isoformat())
        tail = install()
        status('complete', output=tail)
    except Exception as exc:
        REQUEST.unlink(missing_ok=True)
        status('failed', error=str(exc)[:2000])
        raise


if __name__ == '__main__':
    main()
