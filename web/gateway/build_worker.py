"""Fixed-purpose unprivileged executor. No caller-supplied commands or roots."""
import json
from pathlib import Path
import shutil
import logging
import time

from build_queue import BuildQueue
from validation import ValidationRuns, RUN_ID, TARGET, stamp

CONFIG = Path('/etc/herdr/build-service.json')


def execute_next(queue, runner):
    run = queue.claim()
    if not run:
        return False
    if not RUN_ID.fullmatch(str(run.get('id', ''))) or not TARGET.fullmatch(str(run.get('target', ''))):
        queue.finish(dict(run, state='error', note='Invalid build request identity.'))
        return True
    folder = runner.root / run['id']
    try:
        if folder.is_symlink():
            raise ValueError('Build directory must not use symlinks.')
        folder.mkdir(parents=True, exist_ok=True)
        runner._write(folder, run)
        runner._execute(run, folder)
    except Exception as error:
        # A poison request must reach a terminal state instead of crashing the
        # service and being implicitly retried on every restart.
        logging.exception('Build request failed')
        run.update(state='error', note=str(error)[:500], finished_at=stamp())
    queue.finish(run)
    if folder.is_dir() and not folder.is_symlink():
        runner._write(folder, run)
    return True


def recover_interrupted(queue, runner):
    """Clean only this service's validated temporary checkouts after restart."""
    for run in queue.interrupt_running():
        if not RUN_ID.fullmatch(str(run.get('id', ''))):
            continue
        folder = runner.root / run['id']
        if folder.is_symlink() or (folder / 'tree').is_symlink():
            continue
        try:
            checkout = runner._checkout(run.get('path'))
            runner._cleanup(checkout, folder / 'tree')
            folder.mkdir(parents=True, exist_ok=True)
            runner._write(folder, run)
        except (ValueError, OSError):
            logging.exception('Interrupted build cleanup failed; retained for inspection')


def main():
    config = json.loads(CONFIG.read_text())
    projects = Path(config['projects'])
    runner = ValidationRuns(projects, lambda _: None, external=True)
    queue = BuildQueue(config['queue'])
    recover_interrupted(queue, runner)
    while True:
        # Do not claim a build under disk pressure. Keep it queued for recovery.
        try:
            if shutil.disk_usage(projects).free >= config.get('minimum_free_bytes', 2 * 1024**3):
                if execute_next(queue, runner):
                    continue
        except Exception:
            logging.exception('Build service iteration failed')
        time.sleep(2)


if __name__ == '__main__':
    main()
