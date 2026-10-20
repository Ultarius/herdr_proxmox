#!/usr/bin/env python3
"""Measure managed submission on disposable repositories, never a hosted project.

Run with the gateway's Python, Git and temporary filesystem for useful local
measurements. This excludes HTTP transfer and contention; use the administrator
performance endpoint for real submission/lock observations under load.
"""
import argparse
import base64
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from operation_metrics import query_measurement
from task_commits import TaskCommits, command


def sample(files):
    with tempfile.TemporaryDirectory(prefix='herdr-submit-benchmark-') as directory:
        root = Path(directory).resolve()
        repository = root / 'repository'
        repository.mkdir()
        command(repository, 'init')
        task_id = 'a' * 32
        branch = 'herdr/task-' + task_id[:12]
        command(repository, 'symbolic-ref', 'HEAD', 'refs/heads/' + branch)
        tree = command(repository, 'mktree', data=b'').decode().strip()
        identity = dict(GIT_AUTHOR_NAME='Benchmark', GIT_AUTHOR_EMAIL='benchmark@herdr.local',
                        GIT_COMMITTER_NAME='Benchmark', GIT_COMMITTER_EMAIL='benchmark@herdr.local')
        base = command(repository, 'commit-tree', tree, data=b'benchmark base\n', identity=identity).decode().strip()
        command(repository, 'update-ref', 'refs/heads/' + branch, base)
        command(repository, 'read-tree', base)
        task = dict(id=task_id, profile_id='benchmark', run_id='benchmark', state='implementing',
                    branch=branch, base_sha=base)
        run = dict(id='benchmark', task_id=task_id, profile_id='benchmark', state='persona_sent',
                   completion_token='local-benchmark-only')
        service = SimpleNamespace(connect=lambda: sqlite3.connect(root / 'journal.sqlite3'),
            operation=lambda key: nullcontext(), get=lambda key: task,
            store=SimpleNamespace(snapshot=lambda **kwargs: dict(jobs=[run])),
            tree_for=lambda task: repository, path_for=lambda task: repository, save=lambda *args: None)
        commits = TaskCommits(service)
        changes = []
        for number in range(files):
            raw = b'captured benchmark bytes\x00\xff\r\n' + str(number).encode()
            changes.append(dict(path='changes/file-' + str(number), mode='100644',
                                content=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest()))
        body = dict(task_id=task_id, run_id=run['id'], token=run['completion_token'], request_id='benchmark',
                    parent=base, message='Benchmark managed submission', files=changes)
        with query_measurement() as measurement:
            result = commits.submit(body)
        if command(repository, 'rev-parse', 'HEAD').decode().strip() != result['sha']:
            raise RuntimeError('Benchmark did not advance its disposable task branch.')
        return measurement


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', type=int, default=64)
    parser.add_argument('--runs', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.files <= 512 or not 1 <= args.runs <= 10:
        parser.error('Select 1–512 files and 1–10 runs.')
    samples = [sample(args.files) for _ in range(args.runs)]
    print(json.dumps(dict(scope='disposable-local-submission', python=platform.python_version(),
        platform=platform.system(), payload='distinct small binary blobs',
        files=args.files, runs=args.runs, samples=samples,
        median_ms=statistics.median(row['duration_ms'] for row in samples),
        median_git_calls=statistics.median(row['git_calls'] for row in samples)), indent=2))


if __name__ == '__main__':
    main()
