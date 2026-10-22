"""Account-wide cooperative build lane and conservative compiler budgets.

No shell commands, credentials or project code are accepted by the broker.
The standalone copy in a session wraps Cargo; gateway check runners use the
same kernel lock. This coordinates cooperating processes, not hostile code.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

GIB = 1024 ** 3
CARGO_HEAVY = {'build', 'test', 'check', 'clippy', 'bench', 'run', 'doc', 'rustc', 'rustdoc', 'install', 'fix'}


@contextmanager
def storage_leases(environment):
    """Process-held leases outlive job-state changes and inherited pane exits."""
    encoded = (environment or {}).get('HERDR_STORAGE_LEASES') or '[]'
    if len(encoded) > 16384:
        raise ValueError('Storage lease metadata exceeds its limit.')
    paths = json.loads(encoded)
    if not isinstance(paths, list) or len(paths) > 16 or any(not isinstance(path, str) for path in paths):
        raise ValueError('Invalid storage lease metadata.')
    descriptors = []
    try:
        for value in dict.fromkeys(paths):
            path = Path(value)
            if not path.is_absolute() or '..' in path.parts:
                raise ValueError('A storage lease requires an exact absolute path.')
            for parent in (path, *path.parents):
                info = parent.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Storage lease paths must not be linked.')
            fd = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Storage leases require regular control files.')
            if os.name == 'nt':
                if not _lock(fd):
                    raise ValueError('Managed storage is busy.')
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Managed storage is busy.') from None
        yield tuple(descriptors)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def start_process(arguments, *, env, **options):
    """Attach storage leases to the real child and all cooperating descendants."""
    context = storage_leases(env)
    descriptors = context.__enter__()
    try:
        if os.name != 'nt':
            options['pass_fds'] = tuple(dict.fromkeys([*options.get('pass_fds', ()), *descriptors]))
        process = subprocess.Popen(arguments, env=env, **options)
    except BaseException:
        context.__exit__(*sys.exc_info())
        raise
    process.herdr_storage_context = context
    return process


def release_process_storage(process):
    context = getattr(process, 'herdr_storage_context', None)
    if context is not None:
        process.herdr_storage_context = None
        context.__exit__(None, None, None)


def root():
    return Path(os.environ.get('HERDR_BUILD_CAPACITY_ROOT') or Path.home() / '.local/state/herdr/build-capacity')


def storage_status(path=None, minimum_free=2 * GIB):
    """Inspect the build filesystem without walking or deleting its contents."""
    path = Path.cwd() if path is None else Path(path)
    try:
        disk = shutil.disk_usage(path)
        inodes = None
        if hasattr(os, 'statvfs'):
            info = os.statvfs(path)
            if info.f_files > 0:
                inodes = info.f_favail
        blocked = disk.free < minimum_free or inodes is not None and inodes < 1024
        reason = ''
        if inodes is not None and inodes < 1024:
            reason = 'Storage has too few free inodes; free space or expand the filesystem before building.'
        elif disk.free < minimum_free:
            reason = (f'Low disk space: {disk.free // (1024 ** 2)} MiB available; '
                      f'{minimum_free // (1024 ** 2)} MiB required before building. '
                      'Free build-cache space or expand the container disk.')
        return dict(available=True, total_bytes=disk.total, used_bytes=disk.total - disk.free,
                    free_bytes=disk.free, free_inodes=inodes, minimum_free_bytes=minimum_free,
                    blocked=blocked, reason=reason)
    except OSError:
        return dict(available=False, blocked=False, reason='Storage measurements unavailable.')


class StorageLimitExceeded(ValueError):
    """A running check stopped without certifying success."""


def output_size(directory, limit):
    """Bounded, non-following scan of a registered generated-output root."""
    total, count, seen = 0, 0, set()
    for parent, directories, files in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if not Path(parent, name).is_symlink()]
        for name in files:
            path = Path(parent, name)
            info = path.lstat()
            import stat
            identity = (info.st_dev, info.st_ino)
            if stat.S_ISREG(info.st_mode) and identity not in seen:
                seen.add(identity)
                total += info.st_blocks * 512 if hasattr(info, 'st_blocks') else info.st_size
            count += 1
            if total > limit or count > 500000:
                return max(total, limit + 1)
    return total


def wait_for_process(process, *, timeout=None, storage=None, environment=None, interval=1):
    """Watch cheap free-space signals; bound managed output every ten seconds.

    The caller owns termination of its process group. An unavailable measurement
    stops this build rather than repeatedly writing into an unknown filesystem.
    This is a cooperative limit, not a filesystem quota.
    """
    environment = os.environ if environment is None else environment
    directory = environment.get('HERDR_BUILD_OUTPUT_ROOT')
    limit = int(environment.get('HERDR_BUILD_OUTPUT_LIMIT_BYTES') or 0)
    deadline = None if timeout is None else time.monotonic() + timeout
    next_size = 0
    while process.poll() is None:
        managed = environment.get('HERDR_BUILD_STORAGE_ROOT')
        # Tools may clean/recreate their data directory. Its owned parent is
        # stable and measures the same filesystem without treating clean as a
        # lost store. The data directory itself still supplies output bytes.
        for path in dict.fromkeys([storage, *([Path(directory).parent] if directory else []), *([managed] if managed else [])]):
            status = storage_status(path)
            if status.get('blocked') or not status.get('available'):
                raise StorageLimitExceeded(status['reason'] + ' Build stopped; source and receipts were preserved.')
        now = time.monotonic()
        if directory and limit and now >= next_size:
            try:
                size = output_size(directory, limit)
            except FileNotFoundError:
                size = 0
            if size > limit:
                raise StorageLimitExceeded('Managed build output exceeded its ' + str(limit // (1024 ** 2)) +
                    ' MiB budget. Build stopped; inspect storage before retrying.')
            next_size = now + 10
        remaining = None if deadline is None else deadline - now
        if remaining is not None and remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.wait(timeout=min(interval, remaining) if remaining is not None else interval)
        except subprocess.TimeoutExpired:
            pass
    return process.returncode


def budget(proc='/proc', cgroup='/sys/fs/cgroup', *, storage=None):
    """Read cheap capacity/pressure inputs; swap occupancy is not a veto."""
    proc, cgroup = Path(proc), Path(cgroup)
    try:
        cpus = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count() or 1
    except OSError:
        cpus = 1
    total, available, pressure = None, None, 0.0
    try:
        info = {line.split(':')[0]: int(line.split()[1]) * 1024
                for line in (proc / 'meminfo').read_text().splitlines() if ':' in line}
        total, available = info.get('MemTotal'), info.get('MemAvailable')
    except (OSError, ValueError, IndexError):
        pass
    # Services may live below the container cgroup. Every finite ancestor
    # constrains them; reading only the leaf's `max` would use host capacity.
    directories = [cgroup]
    try:
        membership = next(line[3:] for line in (proc / 'self/cgroup').read_text().splitlines()
                          if line.startswith('0::'))
        child = (cgroup / membership.lstrip('/')).resolve()
        if child.is_relative_to(cgroup.resolve()) and child.is_dir():
            directories = [child, *[p for p in child.parents if p.is_relative_to(cgroup.resolve())]]
    except (OSError, StopIteration):
        pass
    for directory in directories:
        try:
            quota, period = (directory / 'cpu.max').read_text().split()
            if quota != 'max':
                cpus = min(cpus, int(quota) / int(period))
        except (OSError, ValueError, ZeroDivisionError):
            pass
        try:
            limit = (directory / 'memory.max').read_text().strip()
            if limit != 'max':
                limit = int(limit)
                used = int((directory / 'memory.current').read_text())
                # Inactive file cache is reclaimable, unlike anonymous RSS.
                try:
                    stats = dict(line.split() for line in (directory / 'memory.stat').read_text().splitlines())
                    used -= min(used, int(stats.get('inactive_file', 0)))
                except (OSError, ValueError):
                    pass
                total = min(total, limit) if total is not None else limit
                headroom = max(0, limit - used)
                available = min(available, headroom) if available is not None else headroom
        except (OSError, ValueError):
            pass
        try:
            for line in (directory / 'memory.pressure').read_text().splitlines():
                fields = line.split()
                avg = float(dict(item.split('=') for item in fields[1:])['avg10'])
                pressure = max(pressure, avg if fields[0] == 'some' else avg * 4)
        except (OSError, ValueError, KeyError, IndexError):
            pass
    reserve = max(GIB // 2, min(2 * GIB, (total or 4 * GIB) // 5))
    reason = ('Waiting for memory pressure to settle.' if pressure >= 20 else
              'Waiting for available memory.' if available is not None and available < reserve + GIB // 2 else '')
    jobs = max(1, min(2, max(1, math.floor(cpus) - 1),
                      max(1, ((available if available is not None else 3 * GIB) - reserve) // (2 * GIB))))
    disk = storage_status(storage)
    if disk['blocked']:
        reason = disk['reason']
    return dict(compiler_jobs=int(jobs), test_threads=1, cpu_cores=cpus, storage=disk,
                memory_total=total, memory_available=available, memory_reserve=reserve,
                memory_pressure=pressure, waiting_reason=reason)


def _write(path, data):
    temporary = path.with_name(path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError('Build capacity record is not a bounded regular file.')
    return json.loads(path.read_text(encoding='utf-8'))


def _start(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _live(record):
    try:
        if os.name == 'nt':
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, record['pid'])
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                    return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(record['pid'], 0)
        return not record.get('process_start') or record['process_start'] == _start(record['pid'])
    except (OSError, KeyError, TypeError):
        return False


def _open(path):
    if path.is_symlink():
        raise ValueError('Build capacity locks must not be symlinks.')
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b'0')
    return descriptor


def _lock(descriptor, blocking=False):
    if os.name == 'nt':
        import msvcrt
        os.lseek(descriptor, 0, 0)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except BlockingIOError:
        return False


@contextmanager
def try_idle(directory=None):
    """Maintenance never waits behind or runs alongside a cooperating build."""
    directory = Path(directory) if directory is not None else root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('Build capacity directory must not be a symlink.')
    descriptor = _open(directory / 'lane.lock')
    try:
        yield _lock(descriptor)
    finally:
        os.close(descriptor)


def _tickets(directory, *, prune=True):
    result = []
    for path in directory.glob('wait-*.json'):
        try:
            data = _read(path)
            if _live(data):
                result.append((data['created_ns'], path.name, data))
            elif prune:
                path.unlink(missing_ok=True)
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return sorted(result)


@contextmanager
def claim(kind='validation', *, directory=None, timeout=1800, sample=None, changed=None):
    sample = sample or budget
    directory = Path(directory) if directory is not None else root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('Build capacity directory must not be a symlink.')
    # A shell launched by the check runner may invoke a session Cargo shim.
    # Reuse its inherited open-file description rather than self-deadlocking.
    inherited = os.environ.get('HERDR_BUILD_LANE_FD')
    inherited_descriptor = None
    if os.name != 'nt' and inherited and inherited.isdigit():
        try:
            descriptor = int(inherited)
            held, lane = os.fstat(descriptor), (directory / 'lane.lock').stat()
            if descriptor > 2 and (held.st_dev, held.st_ino) == (lane.st_dev, lane.st_ino) and _lock(descriptor):
                inherited_descriptor = descriptor
        except (OSError, ValueError):
            pass
    if inherited_descriptor is not None:
        current = sample()
        if (current.get('storage') or {}).get('blocked'):
            raise ValueError(current['storage']['reason'] + ' No new command ran.')
        try:
            inherited_jobs = os.environ.get('HERDR_BUILD_JOBS') or os.environ.get('CARGO_BUILD_JOBS')
            current['compiler_jobs'] = max(1, min(current['compiler_jobs'], int(inherited_jobs)))
        except (KeyError, TypeError, ValueError):
            pass
        yield dict(fd=inherited_descriptor, budget=current, inherited=True)
        return
    identity = uuid.uuid4().hex
    ticket = directory / ('wait-' + identity + '.json')
    data = dict(pid=os.getpid(), process_start=_start(os.getpid()), created_ns=time.time_ns(),
                kind='cargo' if kind == 'cargo' else 'validation', state='waiting')
    descriptor, coordination, acquired = None, None, False
    started, previous = time.monotonic(), None
    try:
        coordination = _open(directory / 'queue.lock')
        if not _lock(coordination, blocking=True):
            raise ValueError('Build capacity coordination is unavailable.')
        if len(_tickets(directory)) >= 128:
            raise ValueError('Build capacity queue is full; retry after current work finishes.')
        _write(ticket, data)
        os.close(coordination)
        coordination = None
        descriptor = _open(directory / 'lane.lock')
        while True:
            current = sample()
            tickets = _tickets(directory)
            first = bool(tickets and tickets[0][1] == ticket.name)
            acquired = first and not current['waiting_reason'] and _lock(descriptor)
            if acquired:
                _write(directory / 'active.json', dict(data, state='running', started_at=time.time(), budget=current))
                ticket.unlink(missing_ok=True)
                if changed:
                    changed(dict(state='running', **current))
                break
            reason = current['waiting_reason'] or 'Waiting for another build or test to finish.'
            if reason != previous:
                _write(ticket, dict(data, reason=reason))
                if changed:
                    changed(dict(state='waiting', reason=reason, **current))
                previous = reason
            if time.monotonic() - started >= timeout:
                raise ValueError('Build capacity wait expired before execution; no checks ran.')
            time.sleep(.2)
        yield dict(fd=descriptor, budget=current, inherited=False)
    finally:
        ticket.unlink(missing_ok=True)
        if acquired:
            (directory / 'active.json').unlink(missing_ok=True)
        if descriptor is not None:
            os.close(descriptor)
        if coordination is not None:
            os.close(coordination)


def snapshot(directory=None, *, storage=None):
    directory = Path(directory) if directory is not None else root()
    current = budget(storage=storage)
    result = dict(current, active=None, waiting=0)
    if not directory.is_dir() or directory.is_symlink():
        return result
    try:
        result['waiting'] = len(_tickets(directory, prune=False))
        # Health polls never create locks, prune tickets or update metadata.
        # Windows byte-range locks require write access, but opening an existing
        # lock with O_RDWR does not create or modify any file bytes.
        flags = os.O_RDWR if os.name == 'nt' else os.O_RDONLY
        descriptor = os.open(directory / 'lane.lock', flags | getattr(os, 'O_NOFOLLOW', 0))
        try:
            if not _lock(descriptor):
                result['active'] = _read(directory / 'active.json')
        finally:
            os.close(descriptor)
    except (ValueError, OSError):
        pass
    return result


def variables(lease):
    values = dict(HERDR_BUILD_JOBS=str(lease['budget']['compiler_jobs']), HERDR_TEST_THREADS='1')
    if os.name != 'nt' and lease['fd'] is not None:
        values['HERDR_BUILD_LANE_FD'] = str(lease['fd'])
    return values


def cargo_variables(lease):
    """The standalone Cargo shim translates the shared lane budget for Rust."""
    value = variables(lease)
    return dict(value, CARGO_BUILD_JOBS=value['HERDR_BUILD_JOBS'], RUST_TEST_THREADS=value['HERDR_TEST_THREADS'])


def cargo_arguments(arguments, jobs):
    """Respect narrower caller choices; explicit flags cannot exceed the cap."""
    result, index, tests = [], 0, False
    while index < len(arguments):
        item = arguments[index]
        if item == '--':
            tests = True
        if not tests and (item in ('-j', '--jobs') or item.startswith(('--jobs=', '-j'))):
            if item in ('-j', '--jobs'):
                index += 1
                value = arguments[index] if index < len(arguments) else ''
            else:
                value = item.split('=', 1)[1] if item.startswith('--jobs=') else item[2:]
            try:
                count = int(value)
                count = min(jobs, count) if count > 0 else jobs
            except ValueError:
                count = jobs
            result.extend(('--jobs', str(count)))
        elif tests and (item == '--test-threads' or item.startswith('--test-threads=')):
            if item == '--test-threads':
                index += 1
            result.append('--test-threads=1')
        else:
            result.append(item)
        index += 1
    return result


def install_cargo(directory, *, lane=None):
    """Place the credential-free Cargo wrapper inside the session's cwd grant."""
    commands = Path(directory) / 'commands'
    commands.mkdir(mode=0o700, exist_ok=True)
    helper = commands / 'build_capacity.py'
    launcher = commands / 'cargo'
    if any(path.is_symlink() for path in (commands, helper, launcher)):
        raise ValueError('Build command wrappers must not be symlinks.')
    helper.write_bytes(Path(__file__).read_bytes())
    import shlex
    command = shlex.join([sys.executable, str(helper), str(lane or root())])
    launcher.write_text('#!/bin/sh\nexec ' + command + ' "$@"\n', encoding='utf-8')
    launcher.chmod(0o700)
    return commands


def main(arguments):
    if not arguments:
        raise ValueError('Expected the build lane directory followed by Cargo arguments.')
    directory, *arguments = arguments
    own = str(Path(__file__).parent.resolve())
    search = os.pathsep.join(part for part in os.environ.get('PATH', '').split(os.pathsep)
                             if part and str(Path(part).resolve()) != own)
    executable = shutil.which('cargo', path=search)
    if not executable:
        raise ValueError('No Cargo executable is available behind the managed wrapper; prepare the project SDK.')
    options = arguments[:arguments.index('--') if '--' in arguments else len(arguments)]
    if any(item in ('--help', '-h', '--version', '-V') for item in options) or not any(item in CARGO_HEAVY for item in options):
        if os.environ.get('HERDR_STORAGE_LEASES'):
            process = start_process([executable, *arguments], env=dict(os.environ))
            try:
                return process.wait()
            finally:
                release_process_storage(process)
        os.execv(executable, [executable, *arguments])
    def status(data):
        if data['state'] == 'waiting':
            print('Herdr build queued: ' + data['reason'], file=sys.stderr, flush=True)
        else:
            print(f"Herdr build running: {data['compiler_jobs']} compiler job(s), 1 test thread.", file=sys.stderr, flush=True)
    with claim('cargo', directory=directory, changed=status) as lease:
        forwarded = cargo_arguments(arguments, lease['budget']['compiler_jobs'])
        environment = dict(os.environ, **cargo_variables(lease))
        process = start_process([executable, *forwarded], env=environment,
                                   start_new_session=os.name != 'nt',
                                   **({'pass_fds': (lease['fd'],)} if os.name != 'nt' else {}))
        try:
            return wait_for_process(process, storage=Path.cwd(), environment=environment)
        except BaseException:
            if os.name != 'nt':
                import signal
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            raise
        finally:
            release_process_storage(process)


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError) as error:
        print('Herdr build capacity: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
