"""Bounded script execution and explicit, independently sourced check evidence."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time

STATUSES = {'passed', 'failed', 'skipped', 'unavailable', 'not_run', 'waived'}
REPORT_LIMIT = 512_000
CHECK_LIMIT = 200


def required_passed(checks):
    required = [check for check in checks if check.get('required', True)]
    return bool(required) and all(check.get('status') == 'passed' and check.get('exit_code') == 0
                                  for check in required)


def read_report(path, script):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > REPORT_LIMIT:
        raise ValueError('Check report must be a bounded regular file.')
    result, seen = [], set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        check = json.loads(line)
        if not isinstance(check, dict):
            raise ValueError('Invalid check report record.')
        identifier, status = check.get('id'), check.get('status')
        code, duration = check.get('exit_code'), check.get('duration_ms', 0)
        required = check.get('required', True)
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 120
                or identifier in seen or not isinstance(status, str) or status not in STATUSES or type(required) is not bool
                or (code is not None and type(code) is not int)
                or type(duration) is not int or duration < 0
                or (status == 'passed' and code != 0)):
            raise ValueError('Invalid or duplicate check report record.')
        seen.add(identifier)
        result.append(dict(id=script + ':' + identifier, name=str(check.get('name', identifier))[:200],
                           status=status, command=str(check.get('command', ''))[:2000],
                           exit_code=code, duration_ms=duration, summary=str(check.get('summary', ''))[:2000],
                           required=required, source='script', script=script))
        if len(result) > CHECK_LIMIT:
            raise ValueError('Too many reported checks.')
    return result


def run_scripts(tree, commands, folder, timeout, bash, output_limit, changed=None, environment=None):
    import build_capacity
    queued = bytearray()
    def capacity(data):
        if data['state'] == 'waiting':
            message = 'Herdr build queued: ' + data['reason']
            queued.extend((message + '\n').encode())
            del queued[:-output_limit]
            publish_log(folder, queued)
            if changed:
                changed([dict(id='herdr:build-capacity', name='Build capacity', required=False,
                              source='runner', status='not_run', exit_code=None, duration_ms=0,
                              summary=message, phase='waiting_capacity')])
    # Production execution is Linux. Windows cannot inherit the cooperative
    # lane descriptor into its check children; holding it would self-deadlock a
    # nested Cargo wrapper. Native Windows checks use only the compiler budget.
    if sys.platform == 'win32':
        limits = build_capacity.budget(storage=tree)
        if (limits.get('storage') or {}).get('blocked'):
            raise ValueError(limits['storage']['reason'] + ' No checks ran.')
        return _run_scripts(tree, commands, folder, timeout, bash, output_limit, changed,
                            environment, dict(fd=None, budget=limits))
    with build_capacity.claim(changed=capacity, sample=lambda: build_capacity.budget(storage=tree)) as lease:
        return _run_scripts(tree, commands, folder, timeout, bash, output_limit, changed,
                            environment, lease, bytes(queued))


def publish_log(folder, contents):
    try:
        temporary = folder / 'log.tmp'
        temporary.write_bytes(contents)
        temporary.replace(folder / 'log.txt')
    except OSError:
        pass  # Capacity/console observation must still work on a full disk.


def _run_scripts(tree, commands, folder, timeout, bash, output_limit, changed, environment, lease, queued=b''):
    import build_capacity
    initial = f"Herdr build running: budget {lease['budget']['compiler_jobs']} compiler job(s), 1 test thread (tool support varies).\n"
    checks, tail = [], bytearray(queued + initial.encode())
    deadline = time.monotonic() + timeout
    timed_out = False
    def log(chunk, force=False):
        tail.extend(chunk)
        if len(tail) > output_limit:
            del tail[:-output_limit]
        now = time.monotonic()
        if not force and now - log.published < 1:
            return
        log.published = now
        publish_log(folder, tail)
    log.published = 0
    for index, script in enumerate(commands):
        recipe = script if isinstance(script, dict) else None
        identifier = recipe['id'] if recipe else script
        arguments = recipe['arguments'] if recipe else [bash, script]
        cwd = tree / recipe.get('cwd', '.') if recipe else tree
        if not cwd.resolve().is_relative_to(tree.resolve()):
            raise ValueError('Check working directory escapes the candidate checkout.')
        if recipe:
            import toolchain_policy
            import environment_adapters
            bound = recipe.get('bindings') or []
            environment_for_check = toolchain_policy.environment(dict(toolchains=bound))
            environment_for_check.update(environment_adapters.execution_caches(bound, folder, create=True))
        else:
            environment_for_check = environment
            bound = []
        capacity_environment = build_capacity.variables(lease)
        import environment_adapters
        capacity_environment.update(environment_adapters.capacity_environment(bound, capacity_environment))
        check = dict(id=identifier, name=identifier, command=' '.join(arguments), required=True,
                     source='runner', status='not_run', exit_code=None, duration_ms=0, summary='')
        checks.append(check)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            check['summary'] = 'Total validation timeout exhausted before this script.'
        else:
            report = folder / f'checks-{index}.jsonl'
            started = time.monotonic()
            try:
                process = subprocess.Popen(arguments, cwd=cwd, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=os.name != 'nt',
                    env=dict(os.environ if environment_for_check is None else environment_for_check,
                             **capacity_environment,
                             CI='1', GIT_TERMINAL_PROMPT='0', HERDR_CHECK_REPORT=str(report.resolve())),
                    **({'pass_fds': (lease['fd'],)} if os.name != 'nt' else {}))
                def collect(proc=process):
                    while chunk := proc.stdout.read(4096):
                        log(chunk)
                reader = threading.Thread(target=collect, daemon=True)
                reader.start()
                try:
                    check['exit_code'] = process.wait(timeout=remaining)
                    check['status'] = 'passed' if check['exit_code'] == 0 else 'failed'
                except subprocess.TimeoutExpired:
                    timed_out = True
                    check.update(status='failed', summary='Validation timeout exceeded.')
                finally:
                    if os.name != 'nt':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif process.poll() is None:
                        process.kill()
                    process.wait()
                    reader.join(5)
                    if not reader.is_alive():
                        process.stdout.close()
            except OSError as error:
                check.update(status='unavailable', summary=str(error)[:500])
            finally:
                check['duration_ms'] = max(0, int((time.monotonic() - started) * 1000))
            try:
                checks.extend(read_report(report, identifier))
            except (ValueError, OSError, UnicodeError) as error:
                checks.append(dict(id=identifier + ':report', name='Check evidence', status='failed',
                    command='', exit_code=None, duration_ms=0, summary=str(error)[:500], required=True, source='runner'))
        if changed:
            changed(list(checks))
    if required_passed(checks):
        exit_code = 0
    else:
        # A script can exit zero while its own report marks a required check
        # failed; the aggregate must stay nonzero so callers cannot record a
        # complete build from failed evidence.
        exit_code = next((check['exit_code'] for check in checks if check.get('exit_code')), None) or 1
    log(f'\n\nValidation exited with status {exit_code}.\n'.encode(), force=True)
    if timed_out:
        log(f'Validation exceeded the {timeout} second limit.\n'.encode(), force=True)
    return checks, exit_code, timed_out
