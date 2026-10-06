"""Named operator identities layered over the shared dashboard token.

The master token keeps working as the bootstrap administrator. Root can add
named operator tokens; every authenticated request then resolves to one
identity, which the coordinator records on approvals, retries and repairs.
"""
import hashlib
import hmac
import json
from pathlib import Path
import re
import threading

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}')
ROLES = ('admin', 'operator')


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Operators:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        entries = data.get('operators') if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def identify(self, token):
        """Resolve a bearer token to a named operator, or None."""
        if not isinstance(token, str) or not token:
            return None
        digest = token_digest(token)
        with self.lock:
            entries = self._load()
        for entry in entries:
            stored = entry.get('token_sha256') if isinstance(entry, dict) else None
            if isinstance(stored, str) and hmac.compare_digest(stored, digest):
                name, role = entry.get('name'), entry.get('role')
                if isinstance(name, str) and role in ROLES:
                    return {'name': name, 'role': role}
        return None

    def snapshot(self):
        """Names and roles only; token digests never leave the file."""
        with self.lock:
            entries = self._load()
        return [{'name': entry.get('name'), 'role': entry.get('role')}
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get('name'), str)
                and entry.get('role') in ROLES]
