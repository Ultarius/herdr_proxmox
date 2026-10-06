"""Exact-commit validation runs with downloadable logs.

A run materializes an integration event's target commit in a detached Git
worktree beside the managed projects directory, executes one of the fixed
validation scripts found in that worktree, and keeps bounded output for
download. It never touches the checkout the coordinator may deliver: no
branch is switched, no merge is performed, and the worktree is removed when
the run finishes.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import subprocess
import threading

import project_git
from project_files import project_directory

COMMANDS = ('scripts/validate.sh', 'scripts/build-web.sh')
BASH = 'bash'
EVENT_ID = re.compile(r'[a-f0-9]{64}')
RUN_ID = re.compile(r'[a-f0-9]{32}')
TARGET = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
TIMEOUT_SECONDS = 1800
OUTPUT_LIMIT = 200_000
HISTORY = 20


def stamp():
    return datetime.now(timezone.utc).isoformat()


class ValidationRuns:
    def __init__(self, projects, lookup, record=None, root=None, timeout=TIMEOUT_SECONDS):
        self.projects = Path(projects)
        self.lookup = lookup
        self.record = record
        self.root = Path(root) if root else self.projects.parent / 'herdr-validation'
        self.timeout = timeout
        self.lock = threading.Lock()
        # Durable runs cannot remain "running" after the gateway process exits.
        for run in self._history():
            if run.get('state') == 'running':
                folder = self.root / run['id']
                run.update(state='interrupted', finished_at=stamp(), note='Gateway restarted; inspect retained logs before retrying.')
                self._write(folder, run)

    def _write(self, folder, run):
        temporary = folder / 'run.tmp'
        temporary.write_text(json.dumps(run, indent=2))
        temporary.replace(folder / 'run.json')

    def _read(self, folder):
        try:
            return json.loads((folder / 'run.json').read_text())
        except (OSError, ValueError):
            return None

    def _history(self):
        try:
            folders = [f for f in self.root.iterdir() if f.is_dir() and not f.is_symlink() and RUN_ID.fullmatch(f.name)]
        except OSError:
            return []
        runs = [self._read(folder) for folder in folders]
        runs = [run for run in runs if isinstance(run, dict) and RUN_ID.fullmatch(str(run.get('id', '')))]
        return sorted(runs, key=lambda run: str(run.get('started_at', '')), reverse=True)

    def running(self):
        return [run for run in self._history() if run.get('state') == 'running']

    def snapshot(self):
        runs = self._history()
        return {'runs': runs[:HISTORY], 'running': sum(run['state'] == 'running' for run in runs),
                'commands': list(COMMANDS), 'timeout_seconds': self.timeout}

    def log(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid validation run ID.')
        folder = self.root / run_id
        if folder.is_symlink() or (folder / 'log.txt').is_symlink():
            raise ValueError('Invalid validation log path.')
        run = self._read(folder)
        if run is None:
            raise ValueError('Validation run not found.')
        try:
            content = (folder / 'log.txt').read_text(errors='replace')
        except OSError:
            content = 'No log was recorded for this run.\n'
        return run, content

    def submit(self, body, actor='dashboard_operator'):
        if not isinstance(body, dict) or not EVENT_ID.fullmatch(str(body.get('id', ''))):
            raise ValueError('Select an integration event to validate.')
        event = self.lookup(body['id'])
        if not isinstance(event, dict):
            raise ValueError('Integration event not found.')
        target = event.get('target')
        if not isinstance(target, str) or not TARGET.fullmatch(target):
            raise ValueError('This event has no exact commit to validate.')
        with self.lock:
            if self.running():
                raise ValueError('Validation is already running. Wait for it to finish.')
            run = dict(id=secrets.token_hex(16), event_id=event['id'], repository=event.get('repository'),
                       target=target, path=event.get('path'), actor=actor, state='running',
                       started_at=stamp(), finished_at=None, exit_code=None, command=None, note='')
            folder = self.root / run['id']
            folder.mkdir(parents=True, exist_ok=True)
            self._write(folder, run)
        threading.Thread(target=self._execute, args=(run, folder), daemon=True).start()
        return dict(run)

    def _execute(self, run, folder):
        worktree = folder / 'tree'
        log_path = folder / 'log.txt'
        lines, state, exit_code = [], 'error', None
        try:
            checkout = self._checkout(run['path'])
            self._worktree(checkout, worktree, run['target'])
            head = project_git.git(worktree, 'rev-parse', 'HEAD').strip()
            if head != run['target']:
                raise ValueError('The worktree does not match the exact target commit.')
            commands = self._commands(worktree)
            command = ' + '.join(commands)
            if not commands:
                raise ValueError('No validation script found (looked for ' + ', '.join(COMMANDS) + ').')
            run['command'] = command
            lines.append(f"Validating {run['target']} in an isolated worktree with {command}.\n\n")
            arguments = [BASH, commands[0]] if len(commands) == 1 else [BASH, '-c', ' && '.join('bash ' + item for item in commands)]
            process = subprocess.Popen(arguments, cwd=worktree, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=os.name != 'nt',
                                       env=dict(os.environ, CI='1', GIT_TERMINAL_PROMPT='0'))
            tail = bytearray()
            def collect():
                while True:
                    chunk = process.stdout.read(4096)
                    if not chunk:
                        break
                    tail.extend(chunk)
                    if len(tail) > OUTPUT_LIMIT:
                        del tail[:-OUTPUT_LIMIT]
            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            try:
                exit_code = process.wait(timeout=self.timeout)
            finally:
                # Kill the entire Linux validation group, including descendants
                # still holding stdout or writing in the temporary worktree.
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
            state = 'complete' if exit_code == 0 else 'failed'
            lines.extend((bytes(tail).decode('utf-8', errors='replace'),
                          f"\n\nValidation exited with status {exit_code}.\n"))
        except subprocess.TimeoutExpired:
            lines.append(bytes(tail).decode('utf-8', errors='replace'))
            state = 'error'
            lines.append(f'Validation exceeded the {self.timeout} second limit.\n')
        except (ValueError, OSError) as error:
            lines.append(f'Validation could not run: {error}\n')
        finally:
            self._cleanup(checkout=locals().get('checkout'), worktree=worktree)
            run.update(state=state, exit_code=exit_code, finished_at=stamp())
            content = ''.join(lines)[-OUTPUT_LIMIT:]
            try:
                log_path.write_text(content)
                self._write(folder, run)
            except OSError:
                pass
            if self.record is not None:
                try:
                    self.record(run['event_id'], dict(run_id=run['id'], state=run['state'], target=run['target'],
                                                      command=run['command'], exit_code=run['exit_code'],
                                                      finished_at=run['finished_at'], actor=run['actor']))
                except (ValueError, OSError):
                    pass

    def _checkout(self, value):
        path = Path(str(value))
        if not path.is_absolute():
            path = Path(self.projects) / path
        return project_directory(self.projects, path)

    def _worktree(self, checkout, worktree, target):
        project_git.git(checkout, 'worktree', 'add', '--detach', str(worktree), target, timeout=max(self.timeout, 60))

    def _commands(self, worktree):
        commands = []
        for candidate in COMMANDS:
            if (worktree / candidate).is_file() and not (worktree / candidate).is_symlink() and not (worktree / 'scripts').is_symlink():
                commands.append(candidate)
        return commands

    def _cleanup(self, checkout, worktree):
        if worktree.exists() and checkout is not None and checkout.exists():
            try:
                project_git.git(checkout, 'worktree', 'remove', '--force', str(worktree), timeout=60)
            except (ValueError, OSError):
                shutil.rmtree(worktree, ignore_errors=True)
        elif worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        if checkout is not None and checkout.exists():
            try:
                project_git.git(checkout, 'worktree', 'prune', timeout=30)
            except (ValueError, OSError):
                pass
