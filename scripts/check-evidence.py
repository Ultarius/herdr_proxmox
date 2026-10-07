#!/usr/bin/env python3
"""Run one explicit check, forwarding output and optionally appending evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def execute(identifier, arguments, report=None):
    if not arguments:
        raise ValueError('A check command is required.')
    started = time.monotonic()
    if shutil.which(arguments[0]) is None:
        code, status = 127, 'unavailable'
        summary = 'Required tool is unavailable: ' + arguments[0]
        print(summary, file=sys.stderr)
    else:
        code = subprocess.run(arguments, check=False).returncode
        status, summary = ('passed' if code == 0 else 'failed'), ''
    record = dict(id=identifier, name=identifier, command=' '.join(arguments), status=status,
                  exit_code=code, duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                  required=True, summary=summary)
    if report:
        path = Path(report)
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size > 500_000)):
            raise ValueError('Check report is not a bounded regular file.')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record) + '\n')
    return code


def finish(report, identifiers):
    """An early script exit leaves planned but unexecuted checks explicit."""
    if not report:
        return
    path = Path(report)
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size > 500_000)):
        raise ValueError('Check report is not a bounded regular file.')
    seen = {json.loads(line)['id'] for line in path.read_text().splitlines() if line.strip()} if path.exists() else set()
    with path.open('a', encoding='utf-8') as stream:
        for identifier in identifiers:
            if identifier not in seen:
                stream.write(json.dumps(dict(id=identifier, name=identifier, command='', status='not_run',
                    exit_code=None, duration_ms=0, required=True, summary='Script ended before this check ran.')) + '\n')


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--finish':
        finish(os.environ.get('HERDR_CHECK_REPORT'), sys.argv[2:])
        raise SystemExit(0)
    if len(sys.argv) < 3:
        raise SystemExit('Usage: check-evidence.py CHECK_ID COMMAND [ARGS...]')
    raise SystemExit(execute(sys.argv[1], sys.argv[2:], os.environ.get('HERDR_CHECK_REPORT')))
