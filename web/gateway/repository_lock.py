"""Serialize gateway Git mutations across linked worktrees and processes.

This does not lock out external Git clients. Callers still recheck state and
respect Git's native locks. Never acquire this while waiting for an agent reply.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import threading
import time

_guard = threading.Lock()
_locks = {}
_local = threading.local()


def common_directory(path):
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, timeout=15, env=environment)
    if result.returncode:
        raise ValueError('Cannot resolve repository metadata for locking.')
    return Path(os.fsdecode(result.stdout).strip()).resolve()


@contextmanager
def repository_lock(path, timeout=15):
    common = common_directory(path)
    key = os.path.normcase(str(common))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise ValueError('Repository is busy. Retry after the current Git operation.')
    depths = getattr(_local, 'depths', None)
    if depths is None:
        depths = _local.depths = {}
    handle = None
    try:
        if not depths.get(key):
            import stat
            descriptor = os.open(common / 'herdr-operation.lock',
                                 os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            handle = os.fdopen(descriptor, 'r+b')
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError('Repository operation lock must be a regular file.')
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        handle.seek(0)
                        if os.fstat(handle.fileno()).st_size == 0:
                            handle.write(b'0')
                        handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError('Repository is busy in another gateway process.')
                    time.sleep(0.05)
        depths[key] = depths.get(key, 0) + 1
        try:
            yield common
        finally:
            depths[key] -= 1
    finally:
        if handle is not None:
            handle.close()
        lock.release()
