"""Real directories, leases and Git worktrees for managed storage retention."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import workspace_storage as storage


@unittest.skipUnless(storage.ANCHORED_DELETION, 'Requires native descriptor-anchored deletion; qualify on Linux.')
class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.projects = self.base / 'projects'
        self.projects.mkdir()
        self.project = self.projects / 'game'
        self.project.mkdir()
        self.manager = storage.Manager(self.base / 'managed', self.projects)
        self.first, self.second = 'a' * 32, 'b' * 32

    def allocate(self, identity='output-a', execution=None, **kwargs):
        return self.manager.allocate(identity, project=str(self.project),
                                     execution_id=execution or self.first, **kwargs)

    def write(self, path, name='object.bin', data=b'x' * 8192):
        (path / name).write_bytes(data)

    def inventory(self, active=()):
        return self.manager.inventory(active_execution_ids=active)

    def reclaim(self, ids=('output-a',), **kwargs):
        return self.manager.reclaim(ids, active_execution_ids=(), **kwargs)

    def test_inventory_does_not_create_missing_registry(self):
        self.assertEqual(self.inventory()['entries'], [])
        self.assertFalse(self.manager.root.exists())

    def test_allocation_claims_only_exact_empty_objects_root_and_reuses_owner(self):
        path = self.allocate()
        self.assertEqual(path.parent, self.manager.root / 'objects')
        marker = json.loads((path / storage.OWNER_FILE).read_text())
        self.write(path)
        self.assertEqual(self.allocate(), path)
        self.assertEqual(json.loads((path / storage.OWNER_FILE).read_text()), marker)
        other = self.manager.root / 'objects' / storage._key('other')
        other.mkdir()
        self.write(other, 'unmanaged-source.rs')
        with self.assertRaisesRegex(storage.StorageError, 'unmanaged contents'):
            self.manager.register('other', other, project=str(self.project), execution_id=self.first)
        with self.assertRaisesRegex(storage.StorageError, 'exact allocated'):
            self.manager.register('escape', self.project, project=str(self.project), execution_id=self.first)
        with self.assertRaisesRegex(storage.StorageError, 'another execution'):
            self.allocate(execution=self.second)

    def test_unknown_activity_active_metadata_and_authoritative_active_ids_prevent_deletion(self):
        path = self.allocate()
        self.write(path)
        self.assertIn('not been closed', self.reclaim(dry_run=False)['skipped'][0]['reason'])
        self.manager.close_execution(self.first)
        unknown = self.manager.reclaim(['output-a'], dry_run=False)
        self.assertIn('unknown', unknown['skipped'][0]['reason'])
        active = self.manager.reclaim(['output-a'], active_execution_ids={self.first}, dry_run=False)
        self.assertIn('active', active['skipped'][0]['reason'])
        self.assertTrue((path / 'object.bin').exists())

    def test_dry_run_preview_and_actual_reclamation_preserve_evidence_outside_object(self):
        path = self.allocate()
        self.write(path)
        evidence = self.project / 'run.json'
        evidence.write_text('{"validation":"passed"}')
        self.manager.close_execution(self.first)
        entry = self.inventory()['entries'][0]
        self.assertTrue(entry['eligible'])
        preview = self.reclaim(expected={entry['id']: entry['generation']})
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['candidates'], ['output-a'])
        self.assertEqual(preview['reclaimed'], [])
        self.assertTrue(path.exists())
        result = self.reclaim(dry_run=False, expected={entry['id']: entry['generation']})
        self.assertEqual(result['reclaimed'], ['output-a'])
        self.assertGreater(result['reclaimed_bytes'], 0)
        self.assertFalse(path.exists())
        self.assertTrue(evidence.exists())
        self.assertEqual(self.inventory()['entries'], [])

    def test_resumed_allocation_reactivates_closed_root_and_invalidates_preview(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        previous = self.inventory()['entries'][0]
        self.allocate()
        self.manager.close_execution(self.first)
        result = self.reclaim(dry_run=False, expected={'output-a': previous['generation']})
        self.assertIn('changed since the preview', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())

    def test_live_lease_prevents_reclamation_and_does_not_wait(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        with self.manager.lease('output-a') as leased:
            self.assertEqual(leased, path)
            self.assertFalse(self.inventory()['entries'][0]['eligible'])
            result = self.reclaim(dry_run=False)
            self.assertIn('in use', result['skipped'][0]['reason'])
            self.assertTrue(path.exists())
        self.assertEqual(self.reclaim(dry_run=False)['reclaimed'], ['output-a'])

    def test_callable_activity_rechecked_after_directory_scan(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        calls = []

        def active():
            calls.append(True)
            return () if len(calls) == 1 else {self.first}

        result = self.manager.reclaim(['output-a'], active_execution_ids=active, dry_run=False)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(result['reclaimed'], [])
        self.assertTrue(path.exists())

    def test_modified_ownership_and_hardlinks_fail_closed(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        outside = self.base / 'important'
        outside.write_text('retained')
        os.link(outside, path / 'linked')
        result = self.reclaim(dry_run=False)
        self.assertIn('hard-linked', result['skipped'][0]['reason'])
        (path / 'linked').unlink()
        marker = path / storage.OWNER_FILE
        owner = json.loads(marker.read_text())
        owner['owner'] = '0' * 32
        marker.write_text(json.dumps(owner))
        result = self.reclaim(dry_run=False)
        self.assertIn('does not match', result['skipped'][0]['reason'])
        self.assertEqual(outside.read_text(), 'retained')
        self.assertTrue(path.exists())

    def test_complete_internal_hardlink_group_is_counted_once_and_reclaimed(self):
        path = self.allocate()
        self.write(path)
        initial = storage._scan(path)
        (path / 'deps').mkdir()
        os.link(path / 'object.bin', path / 'deps' / 'hashed-object.bin')
        self.manager.close_execution(self.first)
        entry = self.inventory()['entries'][0]
        self.assertTrue(entry['eligible'], entry['reason'])
        self.assertEqual(entry['files'], 2)
        self.assertEqual(entry['bytes'], initial['bytes'])
        self.assertEqual(self.reclaim(dry_run=False)['reclaimed'], ['output-a'])
        self.assertFalse(path.exists())

    def test_hardlink_group_with_additional_external_member_is_retained(self):
        path = self.allocate()
        self.write(path)
        os.link(path / 'object.bin', path / 'internal-link')
        external = self.base / 'preserved-link'
        os.link(path / 'object.bin', external)
        self.manager.close_execution(self.first)
        result = self.reclaim(dry_run=False)
        self.assertIn('outside their owned root', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())
        self.assertEqual(external.read_bytes(), b'x' * 8192)

    def test_inode_link_count_is_part_of_reclamation_fingerprint(self):
        path = self.allocate()
        self.write(path)
        before = storage._scan(path)['fingerprint']
        external = self.base / 'external-link'
        os.link(path / 'object.bin', external)
        with self.assertRaisesRegex(storage.StorageError, 'outside their owned root'):
            storage._scan(path)
        external.unlink()
        self.assertEqual(storage._scan(path)['fingerprint'], before)

    def symlink(self, target, path, directory=True):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest('Native symlink creation is unavailable to this test user.')

    def test_symlink_descendants_are_never_followed_or_deleted(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        outside = self.base / 'important'
        outside.mkdir()
        self.write(outside, 'source.py')
        self.symlink(outside, path / 'linked')
        result = self.reclaim(dry_run=False)
        self.assertIn('symlink', result['skipped'][0]['reason'])
        self.assertTrue((outside / 'source.py').exists())
        self.assertTrue(path.exists())

    @unittest.skipIf(os.name == 'nt', 'Internal package links are enabled only with Unix unlink semantics.')
    def test_relative_internal_package_links_can_be_reclaimed_without_following_them(self):
        path = self.allocate(kind='execution-cache')
        (path / 'package').mkdir()
        self.write(path / 'package', 'tool.js')
        (path / '.bin').mkdir()
        (path / '.bin/tool').symlink_to('../package/tool.js')
        self.manager.close_execution(self.first)
        self.assertTrue(self.inventory()['entries'][0]['eligible'])
        self.assertEqual(self.reclaim(dry_run=False)['reclaimed'], ['output-a'])

    @unittest.skipIf(os.name == 'nt', 'Internal package links are enabled only with Unix unlink semantics.')
    def test_absolute_internal_link_is_retained_before_quarantine_changes_its_target(self):
        path = self.allocate(kind='execution-cache')
        self.write(path)
        (path / 'absolute').symlink_to(path / 'object.bin')
        self.manager.close_execution(self.first)
        result = self.reclaim(dry_run=False)
        self.assertIn('absolute symlink', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())

    def test_symlink_ancestor_and_storage_root_replacement_are_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        link = self.base / 'alias'
        self.symlink(outside, link)
        unsafe = storage.Manager(link / 'managed', self.projects)
        with self.assertRaisesRegex(storage.StorageError, 'symlink'):
            unsafe.allocate('x', kind='download-cache')
        self.assertFalse((outside / 'managed').exists())
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        moved = path.with_name(path.name + '-preserved')
        path.rename(moved)
        self.symlink(outside, path)
        result = self.reclaim(dry_run=False)
        self.assertIn('symlink', result['skipped'][0]['reason'])
        self.assertTrue(moved.exists())

    def test_reparse_points_are_rejected_even_without_symlink_mode(self):
        class Junction:
            st_file_attributes = 0x400
        self.assertTrue(storage._reparse(Junction()))

    def test_budget_reclaims_closed_outputs_and_execution_caches_but_retains_active_run(self):
        old = self.allocate()
        private = self.allocate('cache-a', kind='execution-cache')
        active = self.allocate('output-b', execution=self.second)
        for path in (old, private, active):
            self.write(path)
        self.manager.close_execution(self.first)
        result = self.manager.maintain(active_execution_ids={self.second}, output_budget_bytes=0,
                                       max_closed_run_bytes=0, idle_seconds=999999)
        self.assertEqual(set(result['reclaimed']), {'output-a', 'cache-a'})
        self.assertTrue(active.exists())

    def test_download_cache_has_separate_budget_and_respects_its_own_lease(self):
        path = self.manager.allocate('downloads', kind='download-cache', shared=True)
        self.write(path)
        with self.manager.lease('downloads'):
            blocked = self.manager.maintain(active_execution_ids={self.first}, pressure=True)
            self.assertEqual(blocked['reclaimed'], [])
        active = self.allocate()
        result = self.manager.maintain(active_execution_ids={self.first}, shared_budget_bytes=0,
                                       output_budget_bytes=999999, max_closed_run_bytes=999999,
                                       idle_seconds=999999)
        self.assertEqual(result['reclaimed'], ['downloads'])
        self.assertTrue(active.exists())

    def test_idle_retention_reclaims_only_closed_owned_roots(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        unmanaged = self.project / 'node_modules'
        unmanaged.mkdir()
        self.write(unmanaged, 'dependency.js')
        result = self.manager.maintain(active_execution_ids=(), idle_seconds=0)
        self.assertEqual(result['reclaimed'], ['output-a'])
        self.assertTrue((unmanaged / 'dependency.js').exists())

    def test_output_budget_reclaims_oldest_closed_root_only(self):
        with patch.object(storage.time, 'time', return_value=1000):
            first = self.allocate()
            self.write(first)
            self.manager.close_execution(self.first)
        with patch.object(storage.time, 'time', return_value=2000):
            second = self.allocate('output-b', execution=self.second)
            self.write(second)
            self.manager.close_execution(self.second)
        total = self.inventory()['output_bytes']
        result = self.manager.maintain(active_execution_ids=(), output_budget_bytes=total - 1,
                                       max_closed_run_bytes=total, idle_seconds=10 ** 12)
        self.assertEqual(result['reclaimed'], ['output-a'])
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_registry_and_marker_reject_hardlinked_metadata(self):
        path = self.allocate()
        self.write(path)
        self.manager.close_execution(self.first)
        backup = self.base / 'marker-copy'
        os.link(path / storage.OWNER_FILE, backup)
        result = self.reclaim(dry_run=False)
        self.assertIn('one link', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())
        backup.unlink()
        os.link(self.manager.database, self.base / 'database-copy')
        with self.assertRaisesRegex(storage.StorageError, 'one link'):
            self.inventory()

    @unittest.skipIf(os.name == 'nt', 'POSIX private modes are not enforced through Windows chmod.')
    def test_new_registry_roots_and_ownership_receipts_are_private(self):
        path = self.allocate()
        for directory in (self.manager.root, self.manager.root / 'objects', path):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for file in (self.manager.database, path / storage.OWNER_FILE):
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def git(self, *arguments, cwd=None):
        result = subprocess.run(['git', '-C', str(cwd or self.project), *arguments], capture_output=True, check=True)
        return result.stdout.decode().strip()

    def legacy(self):
        try:
            self.git('init')
        except FileNotFoundError:
            self.skipTest('Git is not installed.')
        self.git('config', 'user.name', 'Storage test')
        self.git('config', 'user.email', 'storage@example.invalid')
        (self.project / 'source.rs').write_text('fn main() {}')
        self.git('add', 'source.rs')
        self.git('commit', '-m', 'Initial source')
        checkout = self.projects / '.herdr-worktrees' / self.first
        self.git('worktree', 'add', '--detach', str(checkout))
        debug = checkout / 'target' / 'debug'
        debug.mkdir(parents=True)
        self.write(debug)
        (checkout / 'target' / 'test-saves').mkdir()
        self.write(checkout / 'target' / 'test-saves', 'captured-save.json')
        return checkout, debug

    def test_legacy_adoption_requires_review_inactivity_and_unchanged_exact_preview(self):
        checkout, debug = self.legacy()
        with self.assertRaisesRegex(storage.StorageError, 'inactive'):
            self.manager.preview_legacy(self.first, project=str(self.project))
        with self.assertRaisesRegex(storage.StorageError, 'inactive'):
            self.manager.preview_legacy(self.first, project=str(self.project), active_execution_ids={self.first})
        preview = self.manager.preview_legacy(self.first, project=str(self.project), active_execution_ids=())
        self.assertFalse(self.manager.root.exists())
        with self.assertRaisesRegex(storage.StorageError, 'Review'):
            self.manager.adopt_legacy(preview, active_execution_ids=())
        self.write(debug, 'new.o')
        with self.assertRaisesRegex(storage.StorageError, 'changed since'):
            self.manager.adopt_legacy(preview, reviewed=True, active_execution_ids=())
        fresh = self.manager.preview_legacy(self.first, project=str(self.project), active_execution_ids=())
        record = self.manager.adopt_legacy(fresh, reviewed=True, active_execution_ids=())
        self.assertTrue(record['legacy'])
        result = self.manager.reclaim([record['id']], active_execution_ids=(), dry_run=False)
        self.assertEqual(result['reclaimed'], [record['id']])
        self.assertFalse(debug.exists())
        self.assertTrue((checkout / 'source.rs').exists())
        self.assertTrue((checkout / 'target/test-saves/captured-save.json').exists())
        self.assertTrue((self.project / 'source.rs').exists())

    def test_legacy_rejects_committed_outputs_and_wrong_source_repository(self):
        checkout, debug = self.legacy()
        self.git('add', '-f', 'target/debug/object.bin', cwd=checkout)
        with self.assertRaisesRegex(storage.StorageError, 'Committed'):
            self.manager.preview_legacy(self.first, project=str(self.project), active_execution_ids=())
        self.git('reset', '--', 'target/debug/object.bin', cwd=checkout)
        other = self.projects / 'other'
        other.mkdir()
        self.git('init', cwd=other)
        with self.assertRaisesRegex(storage.StorageError, 'different project'):
            self.manager.preview_legacy(self.first, project=str(other), active_execution_ids=())

    def test_tracked_file_inside_managed_root_is_never_reclaimed(self):
        self.legacy()
        nested = storage.Manager(self.project / 'generated', self.projects)
        path = nested.allocate('nested', project=str(self.project), execution_id=self.first)
        self.write(path, 'committed.rs')
        relative = (path / 'committed.rs').relative_to(self.project).as_posix()
        self.git('add', '-f', relative)
        nested.close_execution(self.first)
        result = nested.reclaim(['nested'], active_execution_ids=(), dry_run=False)
        self.assertIn('Committed', result['skipped'][0]['reason'])
        self.assertTrue((path / 'committed.rs').exists())

    def test_failed_deletion_retains_owned_quarantine_for_safe_retry(self):
        path = self.allocate()
        (path / 'dependencies').mkdir()
        self.write(path / 'dependencies')
        self.manager.close_execution(self.first)
        with patch.object(storage.shutil, 'rmtree', side_effect=OSError('transient failure')):
            result = self.reclaim(dry_run=False)
        self.assertIn('transient failure', result['skipped'][0]['reason'])
        self.assertFalse(path.exists())
        entry = self.inventory()['entries'][0]
        self.assertEqual(entry['state'], 'quarantined')
        self.assertTrue(entry['eligible'])
        result = self.reclaim(dry_run=False)
        self.assertEqual(result['reclaimed'], ['output-a'])


if __name__ == '__main__':
    unittest.main()
