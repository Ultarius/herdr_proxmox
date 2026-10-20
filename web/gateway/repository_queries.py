"""Bounded read-only repository questions for HTTP clients and local workers.

Only immutable Git answers are cached. Task evidence and release progress are
read afresh and report recorded facts, never replace validation/admission gates.
The CLI uses the worker's existing filesystem access, not a dashboard credential.
"""
import argparse
from collections import OrderedDict
from contextlib import closing
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from types import SimpleNamespace

import project_git
from project_files import project_directory
from operation_metrics import METRICS, query_measurement

SHA = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
ID = re.compile(r'[a-f0-9]{32}')
KINDS = ('diff', 'ancestry', 'history', 'requirements', 'evidence', 'status', 'incorporation')


class RepositoryQueries:
    def __init__(self, projects, tasks=None, analysis=None):
        self.projects = Path(projects).resolve()
        self.tasks = tasks
        self.analysis = analysis
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self.cache_bytes = 0

    @staticmethod
    def git(repository, *arguments):
        return project_git.git(repository, '--no-replace-objects', *arguments, output_limit=1_000_000)

    def revision(self, repository, value):
        if not isinstance(value, str) or not SHA.fullmatch(value):
            raise ValueError('Use a full immutable commit SHA, not a branch or abbreviated revision.')
        self.git(repository, 'cat-file', '-e', value + '^{commit}')
        return value

    def query(self, body):
        with query_measurement() as timing:
            repository, result, cached = self._query(body)
        return dict(repository=repository.relative_to(self.projects).as_posix(), kind=body['kind'],
                    observed_at=datetime.now(timezone.utc).isoformat(), cached=cached,
                    measurement=timing, result=result)

    def _query(self, body):
        if not isinstance(body, dict) or body.get('kind') not in KINDS:
            raise ValueError('Select a supported repository question: ' + ', '.join(KINDS))
        name = body.get('repository')
        if (not isinstance(name, str) or not name or '\\' in name or '\0' in name
                or Path(name).is_absolute() or '..' in Path(name).parts):
            raise ValueError('Select a repository relative to the projects directory.')
        # Revalidate the boundary and repository identity before every cache hit.
        repository = project_directory(self.projects, self.projects / name)
        if Path(project_git.git(repository, 'rev-parse', '--show-toplevel').strip()).resolve() != repository:
            raise ValueError('Select the repository root, not a subdirectory.')
        common = project_git.common_directory(repository)
        result, cached = self.answer(repository, common, body)
        return repository, result, cached

    def answer(self, repository, common, body):
        kind = body['kind']
        if kind in ('status', 'evidence', 'incorporation'):
            return self.recorded(repository, body), False
        revision = self.revision(repository, body.get('revision'))
        base = self.revision(repository, body.get('base')) if kind in ('diff', 'ancestry') else None
        file = body.get('file', '')
        if not isinstance(file, str) or '\0' in file or '\\' in file or Path(file).is_absolute() or '..' in Path(file).parts:
            raise ValueError('Choose a relative file path.')
        limit = body.get('limit', 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('History limit must be a whole number from 1 to 50.')
        # Analyzer semantics are part of the identity; host readiness/overrides
        # are deliberately excluded from this committed-requirements answer.
        from project_analysis import ANALYZER_VERSION
        key = (str(repository), str(common), kind, revision, base, file, limit, ANALYZER_VERSION)
        with self.lock:
            stored = self.cache.get(key)
            if stored is not None:
                self.cache.move_to_end(key)
                METRICS.increment('repository.cache_hit')
                return copy.deepcopy(stored[0]), True
        METRICS.increment('repository.cache_miss')
        if kind == 'diff':
            arguments = ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', base, revision, '--']
            if file:
                arguments.append(':(literal)' + file)
            content = self.git(repository, *arguments)
            result = dict(base=base, revision=revision, patch=content[:65536], truncated=len(content) > 65536)
        elif kind == 'ancestry':
            left, right = map(int, self.git(repository, 'rev-list', '--left-right', '--count', base + '...' + revision).split())
            result = dict(base=base, revision=revision, base_is_ancestor=left == 0,
                          revision_is_ancestor=right == 0, base_only_commits=left, revision_only_commits=right)
        elif kind == 'history':
            arguments = ['log', '--no-show-signature', '--max-count=' + str(limit),
                         '--format=%H%x00%P%x00%s%x00', revision, '--']
            if file:
                arguments.append(':(literal)' + file)
            fields = self.git(repository, *arguments).split('\0')
            commits = [dict(sha=fields[index].strip(), parents=fields[index + 1].split(), subject=fields[index + 2][:1000])
                       for index in range(0, len(fields) - 2, 3)]
            result = dict(revision=revision, commits=commits, limit=limit, coverage='bounded history, not a full log')
        else:
            if self.analysis is None:
                raise ValueError('Committed project discovery is unavailable.')
            result = copy.deepcopy(self.analysis.report(repository.relative_to(self.projects).as_posix(), revision=revision))
        size = len(json.dumps(result).encode('utf-8'))
        if size > 1_000_000:
            raise ValueError('The repository answer exceeds 1 MB; narrow the question or inspect the discovery report directly.')
        with self.lock:
            prior = self.cache.pop(key, None)
            if prior:
                self.cache_bytes -= prior[1]
            self.cache[key] = (copy.deepcopy(result), size)
            self.cache_bytes += size
            while len(self.cache) > 128 or self.cache_bytes > 8_000_000:
                _, (_, removed) = self.cache.popitem(last=False)
                self.cache_bytes -= removed
        return result, False

    def recorded(self, repository, body):
        if self.tasks is None or not ID.fullmatch(str(body.get('task_id', ''))):
            raise ValueError('Select an existing task for this repository.')
        task = self.tasks.get(body['task_id'])
        name = repository.relative_to(self.projects).as_posix()
        if task.get('repository') != name:
            raise ValueError('Task belongs to another repository.')
        head = task.get('head_sha')
        if body['kind'] == 'status':
            assignment = task.get('assignment') or {}
            return dict(task_id=task['id'], revision=head, state=task['state'],
                        assignment={key: assignment[key] for key in ('state', 'reason') if key in assignment},
                        issues=[str(task[key])[:2000] for key in ('error', 'automation_error') if task.get(key)],
                        basis='Recorded task state; live agent availability is not assessed.')
        if body['kind'] == 'evidence':
            requested = body.get('revision') or head
            if requested is not None and not SHA.fullmatch(str(requested)):
                raise ValueError('Use a full candidate commit SHA.')
            evidence = task.get('builds', {}).get(requested, {})
            exact = evidence.get('target') == requested and requested is not None
            return dict(task_id=task['id'], revision=requested, current_candidate=head, exact_target=exact,
                current_candidate_matches=requested is not None and requested == head,
                state=evidence.get('state', 'not_run'), policy_digest=evidence.get('policy_digest'),
                recorded_checks_passed=bool(exact and evidence.get('state') == 'complete'
                                           and evidence.get('required_checks_verified') is True),
                checks=[{key: check[key] for key in ('id', 'name', 'status', 'required') if key in check}
                        for check in evidence.get('checks', [])[:100] if isinstance(check, dict)],
                checks_truncated=len(evidence.get('checks', [])) > 100,
                current_policy='not_evaluated', basis='Recorded build evidence, not fresh approval or permission to publish.')
        release = self.tasks.releases.get(body.get('release_id'))
        if release['repository'] != name:
            raise ValueError('Release belongs to another repository.')
        revision = release.get('revision') or {}
        member = next((item for item in revision.get('members', release.get('members', []))
                       if item['task_id'] == task['id']), None)
        outcome = release.get('results', {}).get(task['id'], {}) if member else {}
        selected = (member or {}).get('candidate_sha')
        return dict(task_id=task['id'], release_id=release['id'], release_state=release['state'],
                    manifest_digest=revision.get('digest'), selected=member is not None, revision=selected,
                    current_candidate=head, current_candidate_matches=bool(selected and selected == head),
                    incorporation=outcome.get('state', 'pending' if member else 'not_selected'),
                    integration_sha=outcome.get('integration_sha'), combined_sha=release.get('resulting_sha'),
                    basis='Recorded release membership and incorporation; this does not mean merged into main.')


class ReadOnlyTasks:
    """CLI projection using existing filesystem permissions; no database setup."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.releases = SimpleNamespace(get=lambda identifier: self.read('release_candidates', identifier))

    def read(self, table, identifier):
        if table not in ('tasks', 'release_candidates') or not ID.fullmatch(str(identifier)):
            raise ValueError('Select a recorded task or release identity.')
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT data FROM ' + table + ' WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Recorded task or release not found.')
        return json.loads(row[0])

    def get(self, identifier):
        return self.read('tasks', identifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--kind', required=True, choices=KINDS)
    parser.add_argument('--revision')
    parser.add_argument('--base')
    parser.add_argument('--file', default='')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--tasks-db')
    parser.add_argument('--task-id')
    parser.add_argument('--release-id')
    args = vars(parser.parse_args())
    from project_analysis import ProjectAnalysis
    service = RepositoryQueries(args.pop('projects'), analysis=None)
    service.analysis = ProjectAnalysis(service.projects)
    path = args.pop('tasks_db')
    service.tasks = ReadOnlyTasks(path) if path else None
    try:
        print(json.dumps(service.query(args), ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, str(error)[:500] + '\n')


if __name__ == '__main__':
    main()
