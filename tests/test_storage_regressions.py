"""Regression checks for storage ownership, repository identity and lifecycle."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import build_capacity
import environment_adapters
import execution_storage
import package_cache
from storage_service import StorageService
import workspace_storage as storage


class Jobs:
    def __init__(self, values=()):
        self.values = list(values)

    def job_records(self, **unused):
        return copy.deepcopy(self.values)

    def storage_records(self):
        return copy.deepcopy(self.values)

    def update_job(self, identity, **fields):
        value = next(item for item in self.values if item['id'] == identity)
        value.update(fields)

    def update_storage_finish(self, identity, value):
        self.update_job(identity, storage_finish=value)


class StorageRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.projects = self.base / 'projects'
        self.project = self.projects / 'game'
        self.project.mkdir(parents=True)
        self.root = self.base / 'storage'
        self.identity = 'd' * 32
        variables = patch.dict(os.environ, HERDR_PROJECTS=str(self.projects),
                               HERDR_WORKSPACE_STORAGE_ROOT=str(self.root),
                               HERDR_BUILD_CAPACITY_ROOT=str(self.base / 'lane'))
        variables.start()
        self.addCleanup(variables.stop)
        self.repository(self.project)
        self.manager = execution_storage.manager(projects=self.projects)

    def git(self, directory, *arguments):
        return subprocess.check_output(['git', '-C', str(directory), *arguments], stderr=subprocess.DEVNULL)

    def repository(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.git(directory, 'init', '-q')
        self.git(directory, 'config', 'user.name', 'Storage regression')
        self.git(directory, 'config', 'user.email', 'storage@example.invalid')
        (directory / 'README.md').write_text('source')
        self.git(directory, 'add', 'README.md')
        self.git(directory, 'commit', '-qm', 'source')

    def prepare(self):
        return execution_storage.prepare([dict(name='rust-toolchain', component='.', storage_contract=1)],
            self.project, lane='agent', execution_id=self.identity, projects=self.projects)

    def owned(self, manager=None, identity='owned'):
        return (manager or self.manager).allocate(identity, project=str(self.project), execution_id=self.identity)

    def test_database_writer_crash_rolls_back_without_changing_registered_ownership(self):
        self.owned()
        owner = self.manager.binding('owned')
        code = ("import os,sqlite3,sys\n"
                "db=sqlite3.connect(sys.argv[1])\n"
                "db.execute('PRAGMA cache_size=1')\n"
                "db.execute('BEGIN IMMEDIATE')\n"
                "db.execute('UPDATE storage SET data=? WHERE identity=?', ('x'*2000000, 'owned'))\n"
                "os._exit(0)\n")
        subprocess.run([sys.executable, '-c', code, str(self.manager.database)], check=True, timeout=10)
        self.assertTrue(Path(str(self.manager.database) + '-journal').exists())
        restarted = storage.Manager(self.root, self.projects)
        # Ordinary polling remains read-only. The background scan must recover
        # without waiting for a new execution/allocation to touch the database.
        with self.assertRaises(storage.StorageBusy):
            restarted.inventory(active_execution_ids=())
        service = StorageService(Jobs(), Jobs(), manager=restarted)
        observed = service.scan()
        self.assertTrue(observed['available'], observed)
        self.assertEqual(len(observed['inventory']['entries']), 1)
        self.assertEqual(restarted.binding('owned'), owner)
        restarted.allocate('new-owner', project=str(self.project), execution_id='e' * 32)
        self.assertEqual(len(restarted.inventory(active_execution_ids={self.identity, 'e' * 32})['entries']), 2)

    def test_recovery_preserves_missing_stores_and_refuses_a_live_writer(self):
        self.assertFalse(self.manager.recover())
        self.assertFalse(self.root.exists())
        self.owned()
        owner = self.manager.binding('owned')
        with self.manager._write() as writer:
            # A valid active rollback journal also belongs to its current writer.
            writer.execute('UPDATE storage SET data=? WHERE identity=?', (json.dumps(dict(owner, last_used=0)), 'owned'))
            self.assertTrue(Path(str(self.manager.database) + '-journal').exists())
            with self.assertRaises(storage.StorageBusy):
                self.manager.recover()
        self.assertEqual(self.manager.binding('owned')['state'], 'active')

    def test_sqlite_contention_is_retryable_and_recovery_preserves_existing_owners(self):
        self.owned()
        with closing(sqlite3.connect(self.manager.database)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            with self.assertRaises(storage.StorageBusy):
                self.manager.allocate('new-owner', project=str(self.project), execution_id='e' * 32)
            writer.rollback()
        self.manager.allocate('new-owner', project=str(self.project), execution_id='e' * 32)
        self.assertEqual(self.manager.binding('owned')['execution_id'], self.identity)

    def test_kernel_registry_lock_contention_does_not_change_ownership(self):
        self.owned()
        with storage._FileLock(self.root / 'registry.lock'):
            with self.assertRaises(storage.StorageBusy):
                self.manager.close_execution(self.identity)
        self.assertEqual(self.manager.binding('owned')['state'], 'active')
        self.manager.close_execution(self.identity)
        self.assertEqual(self.manager.binding('owned')['state'], 'closed')

    def assert_tracked_protected(self, manager, enclosing):
        path = self.owned(manager)
        source = path / 'committed.rs'
        source.write_text('keep me')
        self.git(enclosing, 'add', '-f', '--', source.relative_to(enclosing).as_posix())
        self.git(enclosing, 'commit', '-qm', 'committed storage misconfiguration')
        manager.close_execution(self.identity)
        result = manager.reclaim(['owned'], active_execution_ids=(), dry_run=False)
        self.assertEqual(result['reclaimed'], [])
        self.assertIn('Committed', result['skipped'][0]['reason'])
        self.assertEqual(source.read_text(), 'keep me')

    def test_store_inside_an_unrelated_repository_protects_committed_files(self):
        other = self.base / 'unrelated'
        self.repository(other)
        self.assert_tracked_protected(storage.Manager(other / 'managed', self.projects), other)

    def test_store_inside_a_nested_repository_uses_the_nearest_git_boundary(self):
        nested = self.project / 'nested'
        self.repository(nested)
        self.assert_tracked_protected(storage.Manager(nested / 'managed', self.projects), nested)

    def test_repository_created_inside_output_is_never_reclaimed(self):
        path = self.owned()
        self.repository(path / 'nested')
        self.manager.close_execution(self.identity)
        result = self.manager.reclaim(['owned'], active_execution_ids=(), dry_run=False)
        self.assertIn('Git repository', result['skipped'][0]['reason'])
        self.assertTrue((path / 'nested/README.md').exists())

    def test_store_inside_git_control_metadata_is_never_reclaimed(self):
        manager = storage.Manager(self.project / '.git/managed', self.projects)
        path = self.owned(manager)
        (path / 'object').write_text('keep')
        manager.close_execution(self.identity)
        result = manager.reclaim(['owned'], active_execution_ids=(), dry_run=False)
        self.assertIn('Git metadata', result['skipped'][0]['reason'])
        self.assertTrue((path / 'object').exists())

    def test_nested_repository_does_not_hide_files_tracked_by_outer_checkout(self):
        nested = self.project / 'nested'
        manager = storage.Manager(nested / 'managed', self.projects)
        path = self.owned(manager)
        source = path / 'outer-tracked.rs'
        source.write_text('keep')
        self.git(self.project, 'add', '-f', '--', source.relative_to(self.project).as_posix())
        self.git(self.project, 'commit', '-qm', 'outer owns this source')
        self.repository(nested)
        manager.close_execution(self.identity)
        result = manager.reclaim(['owned'], active_execution_ids=(), dry_run=False)
        self.assertIn('Committed', result['skipped'][0]['reason'])
        self.assertTrue(source.exists())

    def test_reclone_at_the_same_path_gets_another_repository_scope(self):
        previous = package_cache.repository_key(self.project)
        self.project.rename(self.base / 'previous-clone')
        self.repository(self.project)
        self.assertNotEqual(previous, package_cache.repository_key(self.project))

    def test_repository_identity_ignores_git_environment_overrides(self):
        expected = package_cache.repository_key(self.project)
        with patch.dict(os.environ, GIT_DIR=str(self.base / 'missing'), GIT_WORK_TREE=str(self.base)):
            self.assertEqual(package_cache.repository_key(self.project), expected)

    def test_output_allocation_does_not_require_python_312_junction_api(self):
        with patch.object(Path, 'is_junction', create=True, side_effect=AssertionError('Python 3.12 API')):
            binding = self.prepare()
        output = Path(self.manager.binding('output:agent:' + self.identity)['path']) / 'data'
        self.assertEqual(binding['outputs'], [str(output)])
        self.assertEqual(binding['environment']['HERDR_BUILD_OUTPUT_ROOT'], str(output))
        self.assertTrue(output.is_dir())

    def test_running_clean_can_remove_and_recreate_data_without_losing_ownership(self):
        binding = self.prepare()
        output = Path(binding['outputs'][0])
        code = ('import shutil,time; from pathlib import Path\n'
                'path=Path(' + repr(str(output)) + ')\nshutil.rmtree(path)\ntime.sleep(.3)\n'
                'path.mkdir()\n(path/"rebuilt").write_text("new output")\n')
        child = subprocess.Popen([sys.executable, '-c', code])
        missing_observed = []
        status = build_capacity.storage_status
        def observe(path):
            if not output.exists():
                missing_observed.append(True)
            return status(path)
        try:
            with patch.object(build_capacity, 'storage_status', side_effect=observe):
                result = build_capacity.wait_for_process(child, timeout=10, storage=self.project,
                    environment=binding['environment'], interval=.01)
            self.assertEqual(result, 0)
            self.assertTrue(missing_observed, 'The monitor must observe the absent data window.')
            self.assertEqual((output / 'rebuilt').read_text(), 'new output')
            self.manager.binding('output:agent:' + self.identity)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def test_private_cache_git_dependencies_are_reclaimable_but_source_stays(self):
        binding = self.prepare()
        identity = 'cache:agent:' + self.identity
        cache = Path(self.manager.binding(identity)['path'])
        git_home = Path(binding['environment']['CARGO_HOME']) / 'git'
        self.repository(git_home / 'checkouts/dependency/revision')
        database = git_home / 'db/dependency'
        database.parent.mkdir(parents=True)
        self.git(self.project, 'init', '--bare', '-q', str(database))
        self.manager.close_execution(self.identity)
        entry = self.manager.inventory(active_execution_ids=(), ids=[identity])['entries'][0]
        self.assertTrue(entry['eligible'], entry['reason'])
        result = self.manager.reclaim([identity], active_execution_ids=(), dry_run=not storage.ANCHORED_DELETION)
        self.assertEqual(result['skipped'], [])
        self.assertIn(identity, result['candidates'])
        if storage.ANCHORED_DELETION:
            self.assertFalse(cache.exists())
        self.assertEqual((self.project / 'README.md').read_text(), 'source')

    def test_private_cache_exception_never_waives_an_enclosing_repository_index(self):
        manager = storage.Manager(self.project / 'managed', self.projects)
        path = manager.allocate('private', kind='execution-cache', project=str(self.project), execution_id=self.identity)
        dependency = path / 'data/dependency'
        dependency.mkdir(parents=True)
        source = dependency / 'README.md'
        source.write_text('source')
        self.git(self.project, 'add', '-f', '--', source.relative_to(self.project).as_posix())
        self.git(self.project, 'commit', '-qm', 'source cannot become disposable cache')
        self.repository(dependency)
        manager.close_execution(self.identity)
        entry = manager.inventory(active_execution_ids=())['entries'][0]
        self.assertFalse(entry['eligible'])
        self.assertIn('Committed', entry['reason'])
        self.assertFalse(entry.get('repairable', False))

    def test_grants_are_registered_data_children_and_exclude_pool_and_owner_files(self):
        binding = self.prepare()
        grants = execution_storage.grants(binding, projects=self.projects)
        self.assertTrue(grants)
        self.assertTrue(all(Path(path).name == 'data' for path in grants))
        self.assertNotIn(binding['manifest']['shared_root'], grants)
        for identity in binding['objects']:
            self.assertNotIn(self.manager.binding(identity)['path'], grants)
        forged = copy.deepcopy(binding)
        forged['manifest']['execution_root'] = str(self.base)
        with self.assertRaisesRegex(ValueError, 'private cache grant'):
            execution_storage.grants(forged, projects=self.projects)
        missing = copy.deepcopy(binding)
        missing['objects'] = [identity for identity in missing['objects'] if not identity.startswith('cache:')]
        with self.assertRaisesRegex(ValueError, 'private cache grant'):
            execution_storage.grants(missing, projects=self.projects)

    def test_preparation_failure_after_allocation_closes_disposable_owners(self):
        with patch.object(environment_adapters, 'execution_caches', side_effect=ValueError('adapter setup failed')):
            with self.assertRaisesRegex(ValueError, 'adapter setup failed'):
                self.prepare()
        rows = self.manager.inventory(active_execution_ids=())['entries']
        self.assertTrue(rows)
        self.assertTrue(all(row['state'] != 'active' for row in rows))

    def test_layout_validation_failure_allocates_nothing(self):
        with patch.object(package_cache, 'configure', side_effect=ValueError('invalid layout')):
            with self.assertRaisesRegex(ValueError, 'invalid layout'):
                self.prepare()
        self.assertEqual(self.manager.inventory(active_execution_ids=())['entries'], [])

    def test_private_only_adapter_guidance_needs_no_shared_pool(self):
        import session_workspace
        binding = execution_storage.prepare([dict(name='node', component='.', storage_contract=1)],
            self.project, lane='agent', execution_id=self.identity, projects=self.projects)
        guidance = session_workspace.guidance(dict(scratch_directory=str(self.base / 'scratch'), storage_binding=binding))
        self.assertIn(binding['environment']['npm_config_cache'], guidance)
        self.assertIsNone(binding['pool'])

    def test_malformed_registry_row_does_not_block_new_allocations_and_is_repairable(self):
        self.owned()
        with closing(sqlite3.connect(self.manager.database)) as db, db:
            db.execute('INSERT INTO storage VALUES (?,?)', ('broken', '{'))
        retained = self.root / 'unmanaged.txt'
        retained.write_text('keep')
        self.owned(identity='new-execution')
        row = next(row for row in self.manager.inventory(active_execution_ids=())['entries'] if row['id'] == 'broken')
        self.assertTrue(row['repairable'])
        with self.assertRaisesRegex(storage.StorageError, 'Review'):
            self.manager.forget_invalid('broken', row['row_digest'])
        result = self.manager.forget_invalid('broken', row['row_digest'], reviewed=True)
        self.assertFalse(result['files_deleted'])
        self.assertEqual(retained.read_text(), 'keep')

    def test_registry_above_old_row_cap_does_not_disable_allocation(self):
        self.owned()
        with closing(sqlite3.connect(self.manager.database)) as db, db:
            db.executemany('INSERT INTO storage VALUES (?,?)', [(f'bad-{i}', '{') for i in range(10001)])
        with patch.object(self.manager, '_records', side_effect=AssertionError('allocation scanned every row')):
            self.owned(identity='after-cap')
        self.assertTrue(self.manager.binding('after-cap'))

    def test_semantically_invalid_paths_projects_and_legacy_contracts_are_metadata_only_repairs(self):
        path = self.owned()
        original = self.manager.binding('owned')
        cases = [dict(path=str(self.base / 'outside')), dict(project=str(self.base / 'outside')),
                 dict(legacy=True, adapter='retired-sdk', output='build')]
        for changes in cases:
            with self.subTest(changes=changes):
                with closing(sqlite3.connect(self.manager.database)) as db, db:
                    db.execute('INSERT OR REPLACE INTO storage VALUES (?,?)', ('owned', json.dumps(dict(original, **changes))))
                entry = self.manager.inventory(active_execution_ids=())['entries'][0]
                self.assertTrue(entry['repairable'])
                self.assertFalse(entry['eligible'])
                result = self.manager.forget_invalid('owned', entry['row_digest'], reviewed=True)
                self.assertFalse(result['files_deleted'])
                self.assertTrue((path / storage.OWNER_FILE).exists())

    def test_owner_receipt_repair_is_bound_to_the_exact_reviewed_bytes(self):
        path = self.owned()
        marker = path / storage.OWNER_FILE
        marker.write_text('{"wrong":"first"}')
        entry = self.manager.inventory(active_execution_ids=())['entries'][0]
        self.assertTrue(entry['repairable'])
        marker.write_text('{"wrong":"second"}')
        with self.assertRaisesRegex(storage.StorageError, 'changed'):
            self.manager.forget_invalid('owned', entry['row_digest'], reviewed=True)
        current = self.manager.inventory(active_execution_ids=())['entries'][0]
        self.manager.forget_invalid('owned', current['row_digest'], reviewed=True)
        self.assertEqual(marker.read_text(), '{"wrong":"second"}')

    def test_activity_and_missing_receipts_are_not_metadata_repair_shortcuts(self):
        path = self.owned()
        entry = self.manager.inventory(active_execution_ids={self.identity})['entries'][0]
        self.assertFalse(entry.get('repairable', False))
        with self.assertRaisesRegex(storage.StorageError, 'valid storage owner'):
            self.manager.forget_invalid('owned', 'f' * 64, reviewed=True)
        (path / storage.OWNER_FILE).unlink()
        entry = self.manager.inventory(active_execution_ids=())['entries'][0]
        self.assertFalse(entry.get('repairable', False))
        with self.assertRaisesRegex(storage.StorageError, 'Filesystem failures'):
            self.manager.forget_invalid('owned', 'f' * 64, reviewed=True)

    def test_invalid_registry_field_types_are_isolated_and_repairable(self):
        self.owned()
        original = self.manager.binding('owned')
        for field, value in (('owner', []), ('path', 12), ('kind', []), ('generation', True),
                             ('last_used', 'bad'), ('closed_at', float('inf'))):
            with self.subTest(field=field):
                record = dict(original, **{field: value})
                with closing(sqlite3.connect(self.manager.database)) as db, db:
                    db.execute('UPDATE storage SET data=? WHERE identity=?', (json.dumps(record), 'owned'))
                row = self.manager.inventory(active_execution_ids=())['entries'][0]
                self.assertTrue(row['repairable'])
                self.manager.maintain(active_execution_ids=())
        self.manager.forget_invalid('owned', row['row_digest'], reviewed=True)
        self.assertTrue(Path(original['path']).exists())

    def test_unrelated_active_execution_does_not_pin_a_download_pool(self):
        self.manager.allocate('pool', kind='download-cache', project=str(self.project), shared=True)
        rows = self.manager.inventory(active_execution_ids={self.identity})['entries']
        self.assertTrue(next(row for row in rows if row['id'] == 'pool')['eligible'])
        with self.manager.lease('pool'):
            row = next(row for row in self.manager.inventory(active_execution_ids={self.identity})['entries'] if row['id'] == 'pool')
            self.assertFalse(row['eligible'])

    def test_retention_budget_excludes_active_bytes(self):
        active = self.owned()
        (active / 'large').write_bytes(b'x' * 100000)
        closed = self.manager.allocate('small', project=str(self.project), execution_id='e' * 32)
        (closed / 'small').write_bytes(b'x')
        self.manager.close_execution('e' * 32)
        with patch.object(self.manager, 'reclaim', return_value={}) as reclaim:
            self.manager.maintain(active_execution_ids={self.identity}, output_budget_bytes=16384,
                                  max_closed_run_bytes=16384, idle_seconds=86400)
        self.assertEqual(reclaim.call_args.args[0], [])

    def test_supervisor_lease_protects_output_after_job_state_closes(self):
        # This pins the supervisor context lifetime; the fork test below proves
        # descriptor inheritance independently after that context is released.
        binding = self.prepare()
        environment = dict(os.environ, **binding['environment'])
        process = build_capacity.start_process([sys.executable, '-c', 'import time; time.sleep(30)'], env=environment)
        try:
            self.manager.close_execution(self.identity)
            rows = self.manager.inventory(active_execution_ids=())['entries']
            self.assertTrue(all(not row['eligible'] for row in rows if row['execution_id'] == self.identity))
        finally:
            process.kill()
            process.wait()
            build_capacity.release_process_storage(process)

    @unittest.skipUnless(storage.ANCHORED_DELETION, 'Requires native Linux fork and inherited locks.')
    def test_descendant_keeps_lease_after_supervisor_and_pane_exit(self):
        import signal
        binding = self.prepare()
        marker = self.base / 'child.pid'
        code = ('import os,time; from pathlib import Path\nchild=os.fork()\n'
                'if child == 0:\n Path(' + repr(str(marker)) + ').write_text(str(os.getpid()))\n'
                ' time.sleep(30)\n os._exit(0)\nos._exit(0)\n')
        process = build_capacity.start_process([sys.executable, '-c', code],
            env=dict(os.environ, **binding['environment']), start_new_session=True)
        try:
            process.wait(timeout=5)
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            build_capacity.release_process_storage(process)
            self.manager.close_execution(self.identity)
            rows = self.manager.inventory(active_execution_ids=())['entries']
            self.assertTrue(all(not row['eligible'] for row in rows if row['execution_id'] == self.identity))
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            build_capacity.release_process_storage(process)

    def test_close_harvest_remains_active_until_cache_transfer_finishes(self):
        binding = self.prepare()
        job = dict(id=self.identity, kind='launch', state='finished', session_closed_at='closed',
                   worktree_path=str(self.project), storage_binding=binding, storage_finish=dict(state='pending'))
        jobs = Jobs([job])
        service = StorageService(jobs, Jobs(), manager=self.manager)
        entered, resume = threading.Event(), threading.Event()
        def harvest(*unused, **options):
            entered.set()
            if not resume.wait(5):
                raise RuntimeError('harvest test timed out')
            return dict(files=0, errors=[])
        failures = []
        def finish():
            try:
                service._closed_executions()
            except Exception as error:
                failures.append(error)
        with patch.object(package_cache, 'harvest', side_effect=harvest):
            thread = threading.Thread(target=finish)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertIn(self.identity, service.active_ids())
                rows = self.manager.inventory(active_execution_ids=service.active_ids)['entries']
                self.assertTrue(all(row['state'] == 'active' for row in rows if row['execution_id'] == self.identity))
            finally:
                resume.set()
                thread.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(jobs.values[0]['storage_finish']['state'], 'complete')
        self.assertNotIn(self.identity, service.active_ids())

    def test_cleanup_preview_has_exact_per_root_paths_and_sizes_and_busy_is_prompt(self):
        path = self.owned()
        (path / 'object').write_bytes(b'object')
        self.manager.close_execution(self.identity)
        service = StorageService(Jobs(), Jobs(), manager=self.manager)
        preview = service.action(dict(action='preview', ids=['owned']))['preview']
        self.assertEqual(preview['entries'][0]['path'], str(path))
        self.assertEqual(preview['entries'][0]['id'], 'owned')
        self.assertGreater(preview['entries'][0]['bytes'], 0)
        with service.operation:
            with self.assertRaisesRegex(ValueError, 'maintenance is busy'):
                service.action(dict(action='preview', ids=['owned']))

    @unittest.skipUnless(storage.ANCHORED_DELETION, 'Requires native descriptor-anchored deletion.')
    def test_parent_replacement_cannot_redirect_anchored_deletion(self):
        path = self.owned()
        (path / 'object').write_bytes(b'disposable')
        self.manager.close_execution(self.identity)
        outside = self.base / 'important'
        outside.mkdir()
        (outside / 'source').write_text('keep')
        original = storage._remove_owned
        def swapped(candidate, measured, owner):
            saved = candidate.with_name(candidate.name + '-original')
            candidate.rename(saved)
            candidate.symlink_to(outside, target_is_directory=True)
            try:
                return original(candidate, measured, owner)
            finally:
                candidate.unlink()
                saved.rename(candidate)
        with patch.object(storage, '_remove_owned', side_effect=swapped):
            result = self.manager.reclaim(['owned'], active_execution_ids=(), dry_run=False)
        self.assertEqual(result['reclaimed'], [])
        self.assertEqual((outside / 'source').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
