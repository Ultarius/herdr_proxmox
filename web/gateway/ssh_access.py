"""Add validated public keys to the gateway user's SSH account."""
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import threading


class SshAccess:
    def __init__(self, home=None):
        self.directory = Path(home or Path.home()) / '.ssh'
        self.lock = threading.Lock()

    def existing(self):
        if self.directory.is_symlink():
            raise ValueError('SSH directory must not be a symbolic link.')
        target = self.directory / 'authorized_keys'
        if target.is_symlink():
            raise ValueError('SSH keys file must not be a symbolic link.')
        if not target.exists():
            return ''
        if target.stat().st_size > 65536:
            raise ValueError('SSH keys file is too large to manage in the dashboard.')
        return target.read_text()

    def snapshot(self):
        with self.lock:
            lines = self.existing().splitlines()
            return {'key_count': sum(bool(line.strip()) and not line.lstrip().startswith('#') for line in lines)}

    def add(self, body):
        if not isinstance(body, dict) or not isinstance(body.get('public_key'), str):
            raise ValueError('Paste a public SSH key.')
        key = body['public_key'].strip()
        fields = key.split()
        if len(key) > 12000 or '\n' in key or '\r' in key or len(fields) < 2 or fields[0] not in ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'):
            raise ValueError('Paste one public key, without private-key content or SSH options.')
        try:
            raw = base64.b64decode(fields[1], validate=True)
            length = int.from_bytes(raw[:4], 'big')
            if raw[4:4 + length].decode() != fields[0]:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError('Invalid public SSH key.') from None
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'key.pub'
            candidate.write_text(key + '\n')
            result = subprocess.run(['ssh-keygen', '-l', '-f', str(candidate)], capture_output=True, text=True, timeout=5)
            if result.returncode:
                raise ValueError('Invalid public SSH key.')
        with self.lock:
            current = self.existing()
            added = not any(line.split()[:2] == fields[:2] for line in current.splitlines())
            if added:
                suffix = ('' if not current or current.endswith('\n') else '\n') + key + '\n'
                if len((current + suffix).encode()) > 65536:
                    raise ValueError('SSH keys file is full.')
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.directory.chmod(0o700)
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
                fd = os.open(self.directory / 'authorized_keys', flags, 0o600)
                try:
                    os.fchmod(fd, 0o600) if hasattr(os, 'fchmod') else (self.directory / 'authorized_keys').chmod(0o600)
                    with os.fdopen(fd, 'a') as output:
                        fd = None
                        output.write(suffix)
                finally:
                    if fd is not None:
                        os.close(fd)
        return {**self.snapshot(), 'added': added}
