"""Bounded durable exports of Herdr terminal history, not complete transcripts."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import uuid

MAX_FILE = 10_000_000
MAX_TOTAL = 100_000_000
PAGE = 65536


def capture(binary, pane, source):
    process = subprocess.Popen([binary, 'pane', 'read', pane, '--source', source, '--lines', '2000', '--format', 'text'],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks = []
    size = 0
    overflow = False
    def read():
        nonlocal size, overflow
        while True:
            part = process.stdout.read(65536)
            if not part:
                break
            size += len(part)
            if size > MAX_FILE:
                overflow = True
                process.kill()
                break
            chunks.append(part)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        process.wait(timeout=10)
        reader.join(timeout=2)
        if overflow:
            raise ValueError('History exceeds the 10 MB export limit. Save the visible screen instead.')
        if reader.is_alive() or process.returncode:
            raise ValueError('Herdr history could not be read. For recent history, wait until the agent is idle; otherwise save the visible screen.')
        return b''.join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=2)
        process.stdout.close()


class RunLogs:
    def __init__(self, root, binary, reader=capture):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.binary = binary
        self.reader = reader
        self.lock = threading.RLock()
        self.tickets = {}

    def path(self, key, suffix):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key):
            raise ValueError('Invalid log ID.')
        path = self.root / (key + suffix)
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError('Invalid log path.')
        return path

    def metadata(self, key):
        item = json.loads(self.path(key, '.json').read_text())
        size = self.path(key, '.txt').stat().st_size
        return {**item, 'id': key, 'size_bytes': size, 'size_mb': size / 1_000_000,
                'download_allowed': size <= MAX_FILE}

    def snapshot(self):
        with self.lock:
            logs = []
            for path in self.root.glob('*.json'):
                try:
                    logs.append(self.metadata(path.stem))
                except (OSError, ValueError):
                    continue
            logs.sort(key=lambda x: x['created_at'], reverse=True)
            return {'logs': logs[:200], 'total_bytes': sum(x['size_bytes'] for x in logs),
                    'limit_bytes': MAX_TOTAL, 'download_limit_bytes': MAX_FILE}

    def action(self, action, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        if action == 'save':
            pane, source = body.get('pane'), body.get('source', 'visible')
            label = body.get('label', pane)
            if not isinstance(pane, str) or not re.fullmatch(r'w[0-9]+:p[0-9]+', pane):
                raise ValueError('Use a pane ID such as w1:p1.')
            if source not in ('visible', 'recent-unwrapped'):
                raise ValueError('Unsupported history source.')
            if not isinstance(label, str) or not label.strip() or len(label) > 120 or any(ord(c) < 32 for c in label):
                raise ValueError('Use a log label of 1–120 characters.')
            data = self.reader(self.binary, pane, source)
            if len(data) > MAX_FILE:
                raise ValueError('Export exceeds 10 MB.')
            with self.lock:
                snapshot = self.snapshot()
                if len(snapshot['logs']) >= 200 or snapshot['total_bytes'] + len(data) > MAX_TOTAL:
                    raise ValueError('Saved-log storage is full (200 snapshots or 100 MB). Delete an old snapshot first.')
                key = uuid.uuid4().hex
                metadata = {'label': label, 'pane': pane, 'source': source, 'kind': 'terminal_snapshot',
                            'created_at': datetime.now(timezone.utc).isoformat()}
                try:
                    for suffix, content in (('.txt', data), ('.json', json.dumps(metadata).encode())):
                        with self.path(key, suffix).open('xb') as file:
                            os.chmod(file.name, 0o600)
                            file.write(content)
                except BaseException:
                    self.path(key, '.txt').unlink(missing_ok=True)
                    self.path(key, '.json').unlink(missing_ok=True)
                    raise
                return self.metadata(key)
        key = body.get('id')
        with self.lock:
            item = self.metadata(key)
            if action == 'preview':
                with self.path(key, '.txt').open('rb') as file:
                    file.seek(max(0, item['size_bytes'] - 8192))
                    data = file.read(8192)
                return {**item, 'text': data.decode('utf-8', errors='replace'), 'truncated': item['size_bytes'] > 8192}
            if action == 'delete':
                self.path(key, '.json').unlink()
                self.path(key, '.txt').unlink()
                self.tickets = {k: v for k, v in self.tickets.items() if v[0] != key}
                return {'deleted': True}
            if action == 'ticket':
                mode = body.get('mode', 'view')
                if mode not in ('view', 'download'):
                    raise ValueError('Invalid log action.')
                if mode == 'download' and not item['download_allowed']:
                    raise ValueError('Download exceeds the 10 MB limit; use the paged viewer.')
                self.tickets = {k: v for k, v in self.tickets.items() if v[2] > time.monotonic()}
                if len(self.tickets) >= 100:
                    raise ValueError('Too many open log links. Retry after five minutes.')
                ticket = secrets.token_urlsafe(32)
                self.tickets[ticket] = (key, mode, time.monotonic() + 300)
                return {'ticket': ticket, 'mode': mode}
        raise ValueError('Unsupported log action.')

    def page(self, ticket, offset=0):
        with self.lock:
            value = self.tickets.get(ticket)
            if not value or value[2] <= time.monotonic():
                raise ValueError('Log link expired. Open it again from the dashboard.')
            key, mode, _ = value
            item = self.metadata(key)
            if mode == 'download':
                if item['size_bytes'] > MAX_FILE:
                    raise ValueError('Download exceeds 10 MB.')
                del self.tickets[ticket]
                with self.path(key, '.txt').open('rb') as file:
                    data = file.read(MAX_FILE + 1)
                if len(data) > MAX_FILE:
                    raise ValueError('Download exceeds 10 MB.')
                return {**item, 'delivery_mode': mode}, data, None
            if type(offset) is not int or not 0 <= offset <= item['size_bytes']:
                raise ValueError('Invalid log offset.')
            with self.path(key, '.txt').open('rb') as file:
                file.seek(offset)
                data = file.read(PAGE)
                # Avoid splitting valid UTF-8 characters across pages.
                end = offset + len(data)
                if end < item['size_bytes']:
                    while data and (file.read(1)[0] & 0xC0) == 0x80:
                        file.seek(end)
                        data += file.read(1)
                        end += 1
            return {**item, 'delivery_mode': mode}, data, end if end < item['size_bytes'] else None
