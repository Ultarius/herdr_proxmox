import importlib.util
import json
import hashlib
import io
import shutil
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
import sys
from unittest.mock import MagicMock, patch, call
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))

spec = importlib.util.spec_from_file_location('updates', Path(__file__).parents[1] / 'web/gateway/updates.py')
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)
worker_spec = importlib.util.spec_from_file_location('update_worker', Path(__file__).parents[1] / 'install/dashboard-update.py')
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class UpdateTests(unittest.TestCase):
    def legacy_backup(self, state, name='backup-1'):
        backup = state / name
        for relative, contents in {
            'web/gateway/server.py': 'gateway', 'web/public/index.html': 'dashboard',
            'config/token': 'saved token', 'VERSION': 'v0.0.55',
            'BUILD.json': json.dumps({'build_id': 'v0.0.55', 'deployment_mode': 'release'}),
        }.items():
            path = backup / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        return backup

    def setUp(self):
        # Deployment tests must not depend on the developer machine's disk.
        disk = patch.object(worker.shutil, 'disk_usage', return_value=MagicMock(free=10**12))
        disk.start()
        self.addCleanup(disk.stop)
        inodes = patch.object(worker.os, 'statvfs', return_value=MagicMock(f_files=10**6, f_favail=10**6), create=True)
        inodes.start()
        self.addCleanup(inodes.stop)

    def test_manifest_refuses_changed_added_or_missing_files_before_restore(self):
        for mutation in ('changed', 'added', 'missing'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                state = Path(folder)
                backup = self.legacy_backup(state)
                (backup / 'manifest.json').write_text(json.dumps(worker.backup_inventory(backup)))
                self.assertEqual(worker.verify_backup(backup)['method'], 'manifest')
                if mutation == 'changed':
                    (backup / 'config/token').write_text('wrong token')
                elif mutation == 'added':
                    (backup / 'config/extra').write_text('unexpected')
                else:
                    (backup / 'web/gateway/server.py').unlink()
                with patch.object(worker, 'service') as service, patch.object(worker.shutil, 'rmtree') as remove:
                    with self.assertRaisesRegex(ValueError, 'manifest does not match'):
                        worker.restore(backup)
                    service.assert_not_called()
                    remove.assert_not_called()

    def test_legacy_backup_requires_a_valid_completion_witness(self):
        for witness in ('missing', 'invalid', 'empty', 'valid'):
            with self.subTest(witness=witness), tempfile.TemporaryDirectory() as folder, \
                    patch.object(worker, 'ROOT', Path(folder) / 'installed'):
                backup = self.legacy_backup(Path(folder))
                identity = backup / 'BUILD.json'
                if witness == 'missing':
                    identity.unlink()
                elif witness == 'invalid':
                    identity.write_text('{')
                elif witness == 'empty':
                    identity.write_text('{}')
                if witness == 'valid':
                    self.assertEqual(worker.verify_backup(backup)['method'], 'legacy-witness')
                else:
                    with self.assertRaisesRegex(ValueError, 'Legacy backup is unverified'):
                        worker.verify_backup(backup)

    def test_manifest_backups_cannot_remove_newer_live_deployment_parts(self):
        for name in ('install', 'VERSION', '.fvmrc', 'BUILD.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
                state = Path(folder)
                backup = self.legacy_backup(state)
                root = self.legacy_backup(state, 'installed')
                target = root / name
                if name == 'install':
                    target.mkdir()
                elif not target.exists():
                    target.write_text('live part')
                (backup / name).unlink(missing_ok=True)
                (backup / 'manifest.json').write_text(json.dumps(worker.backup_inventory(backup)))
                mocks.enter_context(patch.object(worker, 'ROOT', root))
                mocks.enter_context(patch.object(worker, 'CONFIG', root / 'config'))
                mocks.enter_context(patch.object(worker, 'STATE', state))
                service = mocks.enter_context(patch.object(worker, 'service'))
                with self.assertRaisesRegex(ValueError, 'missing ' + name.replace('.', r'\.')):
                    worker.restore(backup)
                self.assertTrue(target.exists())
                service.assert_not_called()

    def test_double_configuration_rename_failure_leaves_service_stopped_and_blocks_retries(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = self.legacy_backup(state)
            root = self.legacy_backup(state, 'installed')
            (root / 'config/github.json').write_text('current credential')
            rename = Path.rename
            def fail_configuration(path, target):
                if path.name in ('replacement', 'original'):
                    raise OSError('configuration rename failed')
                return rename(path, target)
            for name, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                mocks.enter_context(patch.object(worker, name, value))
            service = mocks.enter_context(patch.object(worker, 'service'))
            warning = mocks.enter_context(patch.object(worker, 'warn'))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            mocks.enter_context(patch.object(Path, 'rename', fail_configuration))
            with self.assertRaisesRegex(OSError, 'configuration rename failed'):
                worker.restore(backup)
            self.assertEqual(service.call_args_list, [call('stop')])
            self.assertFalse((root / 'config').exists())
            retained = list(root.glob('herdr-restore-*/original'))
            self.assertEqual(len(retained), 1)
            self.assertEqual((retained[0] / 'github.json').read_text(), 'current credential')
            self.assertTrue(any('left stopped' in item.args[0] for item in warning.call_args_list))
            service.reset_mock()
            with self.assertRaisesRegex(ValueError, 'recovery required'):
                worker.restore(backup)
            service.assert_not_called()
            self.assertEqual(list(root.glob('herdr-restore-*/original')), retained)

    def test_restore_preserves_current_repository_registry_and_credentials_as_one_set(self):
        for scenario in ('created', 'rotated', 'removed', 'absent'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
                state = Path(folder)
                backup = self.legacy_backup(state)
                root = self.legacy_backup(state, 'installed')
                current = root / 'config/repository-connections'
                previous = backup / 'config/repository-connections'
                old = {'connections.json': '{"connections":["old"],"bindings":{"app":"old"}}',
                       'old/token.json': '{"token":"old-token"}'}
                for name, content in old.items():
                    target = previous / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                expected = dict(old)
                if scenario == 'created':
                    expected.update({'connections.json': '{"connections":["old","new"],"bindings":{"app":"new"}}',
                                     'new/identity': 'new private key', 'new/identity.pub': 'new public key',
                                     'new/known_hosts': 'verified host'})
                elif scenario == 'rotated':
                    expected['old/token.json'] = '{"token":"rotated-token"}'
                else:
                    expected = {'connections.json': '{"connections":[],"bindings":{}}'} if scenario == 'removed' else {}
                for name, content in expected.items():
                    target = current / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                    target.chmod(0o600)
                for directory in [current, *(p for p in current.rglob('*') if p.is_dir())]:
                    if directory.exists():
                        directory.chmod(0o700)
                (root / 'config/github.json').write_text('{"token":"current-publishing-token"}')
                for name, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                    mocks.enter_context(patch.object(worker, name, value))
                mocks.enter_context(patch.object(worker, 'service'))
                mocks.enter_context(patch.object(worker.subprocess, 'run'))
                worker.restore(backup)
                actual = {p.relative_to(current).as_posix(): p.read_text()
                          for p in current.rglob('*') if p.is_file()}
                self.assertEqual(actual, expected)
                self.assertEqual(current.exists(), scenario != 'absent')
                self.assertIn('current-publishing-token', (root / 'config/github.json').read_text())
                if sys.platform != 'win32' and current.exists():
                    self.assertEqual(stat.S_IMODE(current.stat().st_mode), 0o700)
                    for path in current.rglob('*'):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_restore_refuses_linked_repository_credentials_before_removing_live_files(self):
        if sys.platform == 'win32':
            self.skipTest('Creating symlinks requires Windows developer mode.')
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = self.legacy_backup(state)
            root = self.legacy_backup(state, 'installed')
            (root / 'config/repository-connections').symlink_to(backup / 'config', target_is_directory=True)
            for name, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                mocks.enter_context(patch.object(worker, name, value))
            service = mocks.enter_context(patch.object(worker, 'service'))
            with self.assertRaisesRegex(ValueError, 'symbolic link'):
                worker.restore(backup)
            self.assertEqual((root / 'web/gateway/server.py').read_text(), 'gateway')
            service.assert_not_called()

    def test_preserved_copy_failure_leaves_live_credentials_intact(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = self.legacy_backup(state)
            root = self.legacy_backup(state, 'installed')
            current = root / 'config/repository-connections'
            current.mkdir()
            (current / 'connections.json').write_text('current registry')
            original_copy = worker.shutil.copytree
            def copy(source, destination, *args, **kwargs):
                if Path(source) == current:
                    raise OSError('preserved copy failed')
                return original_copy(source, destination, *args, **kwargs)
            for name, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker, 'service'))
            mocks.enter_context(patch.object(worker.shutil, 'copytree', side_effect=copy))
            with self.assertRaisesRegex(OSError, 'preserved copy failed'):
                worker.restore(backup)
            self.assertEqual((current / 'connections.json').read_text(), 'current registry')

    def test_replacement_rename_failure_restores_original_and_budgets_beside_config(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = self.legacy_backup(state)
            root = self.legacy_backup(state, 'installed')
            (root / 'config/github.json').write_text('current credential')
            rename = Path.rename
            def fail_replacement(path, target):
                if path.name == 'replacement':
                    raise OSError('rename failed')
                return rename(path, target)
            for key, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                mocks.enter_context(patch.object(worker, key, value))
            mocks.enter_context(patch.object(worker, 'service'))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            mocks.enter_context(patch.object(Path, 'rename', fail_replacement))
            budget = mocks.enter_context(patch.object(worker, 'require_storage'))
            with self.assertRaisesRegex(OSError, 'rename failed'):
                worker.restore(backup)
            self.assertEqual((root / 'config/github.json').read_text(), 'current credential')
            staged = [entry for record in budget.call_args_list for entry in record.args[0]
                      if entry[0].name.startswith('herdr-restore-')]
            self.assertEqual(len(staged), 1)
            self.assertEqual(staged[0][0].parent, root)
            self.assertGreaterEqual(staged[0][1], worker.footprint(backup / 'config')[0] + 4096)

    def test_wrong_type_reserved_paths_are_rejected_before_stopping(self):
        for live in (True, False):
            for name in (*worker.PRESERVED_CONFIG, *worker.PRESERVED_DIRECTORIES):
                with self.subTest(live=live, name=name), tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
                    state = Path(folder)
                    backup = self.legacy_backup(state)
                    root = self.legacy_backup(state, 'installed')
                    target = (root if live else backup) / 'config' / name
                    if name in worker.PRESERVED_CONFIG:
                        target.mkdir()
                    else:
                        target.write_text('wrong type')
                    for key, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                        mocks.enter_context(patch.object(worker, key, value))
                    service = mocks.enter_context(patch.object(worker, 'service'))
                    with self.assertRaises(ValueError):
                        worker.restore(backup)
                    service.assert_not_called()

    def test_failed_configuration_restore_keeps_current_repository_credentials(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = self.legacy_backup(state)
            root = self.legacy_backup(state, 'installed')
            current = root / 'config/repository-connections'
            (current / 'connection').mkdir(parents=True)
            (current / 'connections.json').write_text('{"connections":["connection"]}')
            (current / 'connection/token.json').write_text('{"token":"rotated-token"}')
            copytree = worker.shutil.copytree

            def copy(source, destination, *args, **kwargs):
                if Path(source) == backup / 'config':
                    raise OSError('interrupted configuration copy')
                return copytree(source, destination, *args, **kwargs)

            for name, value in (('ROOT', root), ('CONFIG', root / 'config'), ('STATE', state)):
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker, 'service'))
            mocks.enter_context(patch.object(worker.shutil, 'copytree', side_effect=copy))
            with self.assertRaisesRegex(OSError, 'interrupted configuration copy'):
                worker.restore(backup)
            self.assertEqual((current / 'connection/token.json').read_text(), '{"token":"rotated-token"}')
            self.assertEqual((current / 'connections.json').read_text(), '{"connections":["connection"]}')

    def test_legacy_backup_cannot_retain_a_newer_installer(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            backup = self.legacy_backup(state)
            root = state / 'installed'
            (root / 'install').mkdir(parents=True)
            with patch.object(worker, 'ROOT', root), patch.object(worker, 'service') as service:
                with self.assertRaisesRegex(ValueError, 'refusing a mixed deployment'):
                    worker.restore(backup)
                service.assert_not_called()

    def test_corrupt_backup_database_is_refused_without_changing_live_files(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            backup = self.legacy_backup(state)
            (backup / 'config/organizations.sqlite3').write_bytes(b'truncated database')
            before = {p.relative_to(backup): p.read_bytes() for p in backup.rglob('*') if p.is_file()}
            with patch.object(worker, 'ROOT', state / 'installed'), patch.object(worker, 'service') as service:
                with self.assertRaisesRegex(ValueError, 'configuration database is invalid'):
                    worker.restore(backup)
                service.assert_not_called()
            self.assertEqual(before, {p.relative_to(backup): p.read_bytes() for p in backup.rglob('*') if p.is_file()})

    def test_sqlite_validation_includes_wal_without_writing_backup_sidecars(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)
            database = config / 'organizations.sqlite3'
            connection = worker.sqlite3.connect(database)
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE evidence (value TEXT)')
                connection.commit()
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                connection.execute("INSERT INTO evidence VALUES ('committed WAL evidence')")
                connection.commit()
                before = {p.name: p.read_bytes() for p in config.iterdir()}
                self.assertTrue(before['organizations.sqlite3-wal'])
                worker.verify_config(config)
                self.assertEqual(before, {p.name: p.read_bytes() for p in config.iterdir()})
            finally:
                connection.close()

    def test_rollback_selects_verified_legacy_backup_and_records_sidecar(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = self.legacy_backup(state, 'backup-2')
            incomplete = self.legacy_backup(state, 'backup-10')
            (incomplete / 'BUILD.json').unlink()
            self.legacy_backup(state, 'backup-99.partial')
            before = worker.backup_inventory(backup)
            mocks.enter_context(patch.object(worker, 'STATE', state))
            mocks.enter_context(patch.object(worker, 'ROOT', state / 'installed'))
            mocks.enter_context(patch.object(worker, 'CONFIG', state / 'config'))
            mocks.enter_context(patch.object(worker, 'deployment_storage'))
            mocks.enter_context(patch.object(worker, 'service'))
            mocks.enter_context(patch.object(worker, 'backup_current', return_value=state / 'backup-100'))
            restored = mocks.enter_context(patch.object(worker, 'restore', side_effect=worker.verify_backup))
            mocks.enter_context(patch.object(worker, 'wait_for_ready'))
            mocks.enter_context(patch.object(worker, 'warn'))
            worker.rollback()
            restored.assert_called_once_with(backup)
            saved = json.loads((state / 'status.json').read_text())
            self.assertEqual(saved['backup_verification']['method'], 'legacy-witness')
            self.assertEqual(saved['skipped_backups'][0]['backup'], 'backup-10')
            registry = json.loads((state / 'backups.json').read_text())
            self.assertEqual(registry['backup-2']['method'], 'legacy-witness')
            self.assertEqual(before, worker.backup_inventory(backup))
            # Certificates must not bypass a fresh content check.
            (backup / 'config/token').write_text('changed after certification')
            with self.assertRaisesRegex(ValueError, 'changed since certification'):
                worker.verify_backup(backup)
            (backup / 'config/token').write_text('saved token')
            (backup / 'BUILD.json').write_text('{')
            with self.assertRaisesRegex(ValueError, 'Legacy backup is unverified'):
                worker.verify_backup(backup)

    def test_failure_after_build_identity_never_publishes_partial_backup(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            root = self.legacy_backup(state, 'installed')
            mocks.enter_context(patch.object(worker, 'ROOT', root))
            mocks.enter_context(patch.object(worker, 'CONFIG', root / 'config'))
            mocks.enter_context(patch.object(worker, 'STATE', state))
            mocks.enter_context(patch.object(worker, 'backup_inventory', side_effect=OSError(28, 'No space left on device')))
            with self.assertRaisesRegex(OSError, 'No space left on device'):
                worker.backup_current()
            self.assertFalse(list(state.glob('backup-*')))

    def test_enospc_does_not_claim_that_available_space_proves_a_quota(self):
        for error in (OSError(28, 'No space left on device'),
                      shutil.Error([('source', 'destination', '[Errno 28] No space left on device')])):
            message = worker.failure_message(error)
            self.assertIn('could not allocate filesystem space', message)
            self.assertNotIn('exceeded a storage quota', message)
            self.assertIn('free space alone does not identify the cause', message)

    def test_failed_backup_is_never_a_rollback_candidate(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails), tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
                base = Path(folder)
                root, config, state = base / 'installed', base / 'config', base / 'state'
                for path in (root / 'web', config, state):
                    path.mkdir(parents=True)
                (root / 'web/server.py').write_text('gateway')
                (config / 'token').write_text('original token')
                for name, value in [('ROOT', root), ('CONFIG', config), ('STATE', state)]:
                    mocks.enter_context(patch.object(worker, name, value))
                original_copy = shutil.copytree
                def copy(source, destination, **kwargs):
                    if source == config:
                        destination.mkdir()
                        (destination / 'token').write_text('truncated')
                        raise OSError(28, 'No space left on device')
                    return original_copy(source, destination, **kwargs)
                mocks.enter_context(patch.object(worker.shutil, 'copytree', side_effect=copy))
                if cleanup_fails:
                    mocks.enter_context(patch.object(worker.shutil, 'rmtree', side_effect=OSError('cleanup failed')))
                mocks.enter_context(patch.object(worker, 'warn'))
                with self.assertRaisesRegex(OSError, 'No space left on device'):
                    worker.backup_current()
                self.assertEqual(len(list(state.glob('*.partial'))), int(cleanup_fails))
                with patch.object(worker, 'service') as service:
                    with self.assertRaisesRegex(ValueError, 'No deployment backup'):
                        worker.rollback()
                    service.assert_not_called()
                self.assertEqual((config / 'token').read_text(), 'original token')

    def test_backup_publishes_only_after_the_last_copy(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, config, state = base / 'installed', base / 'config', base / 'state'
            for path in (root / 'web', config, state):
                path.mkdir(parents=True)
            (config / 'token').write_text('saved token')
            (root / 'VERSION').write_text('v0.0.55')
            for name, value in [('ROOT', root), ('CONFIG', config), ('STATE', state)]:
                mocks.enter_context(patch.object(worker, name, value))
            original_tree, original_file = shutil.copytree, shutil.copy2
            def unpublished():
                self.assertTrue(list(state.glob('*.partial')))
                self.assertFalse(any(worker.re.fullmatch(r'backup-[0-9]+', p.name) for p in state.iterdir()))
            def tree(source, destination, **kwargs):
                unpublished()
                return original_tree(source, destination, **kwargs)
            def file(source, destination, **kwargs):
                unpublished()
                return original_file(source, destination, **kwargs)
            mocks.enter_context(patch.object(worker.shutil, 'copytree', side_effect=tree))
            mocks.enter_context(patch.object(worker.shutil, 'copy2', side_effect=file))
            backup = worker.backup_current()
            self.assertEqual((backup / 'config/token').read_text(), 'saved token')
            self.assertEqual((backup / 'VERSION').read_text(), 'v0.0.55')
            self.assertFalse(list(state.glob('*.partial')))

    def test_upgrade_budgets_config_only_in_backup_and_accounts_for_metadata(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, config, state, source = (base / name for name in ('installed', 'config', 'state', 'source'))
            for path in (root, config, state, source):
                path.mkdir()
            for name, value in [('ROOT', root), ('CONFIG', config), ('STATE', state)]:
                mocks.enter_context(patch.object(worker, name, value))
            mib = 1024 * 1024
            sizes = {root / 'web': 20 * mib, config: 100 * mib, source / 'web': 15 * mib}
            mocks.enter_context(patch.object(worker, 'footprint', side_effect=lambda path: (sizes.get(path, 0), 1)))
            mocks.enter_context(patch.object(worker.shutil, 'disk_usage', return_value=MagicMock(free=worker.STORAGE_RESERVE + 140 * mib)))
            worker.deployment_storage(source)
            with self.assertRaisesRegex(ValueError, 'Not enough disk space'):
                worker.deployment_storage(source, config)
            sizes[source / 'VERSION'] = 10 * mib
            with self.assertRaisesRegex(ValueError, 'Not enough disk space'):
                worker.deployment_storage(source)

    def test_storage_samples_each_destination_and_reports_both_shortages(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / 'first', Path(folder) / 'second'
            first.mkdir()
            second.mkdir()
            with patch.object(worker.shutil, 'disk_usage', side_effect=lambda path:
                              MagicMock(free=0 if path == second else 10**12)), \
                    patch.object(worker.os, 'statvfs', return_value=MagicMock(f_files=100, f_favail=0)):
                # First verify different destination reports on one device.
                with patch.object(worker.os, 'statvfs', return_value=MagicMock(f_files=0)):
                    with self.assertRaisesRegex(ValueError, 'second'):
                        worker.require_storage([(first, 10, 1), (second, 10, 1)])
                with self.assertRaises(ValueError) as failure:
                    worker.require_storage([(second, 10, 1)])
                self.assertIn('MiB available', str(failure.exception))
                self.assertIn('free inodes', str(failure.exception))

    def test_footprint_refuses_symlinks_and_explains_disappearing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child = root / 'file'
            child.write_text('contents')
            original = Path.lstat
            with patch.object(Path, 'lstat', lambda path: MagicMock(st_mode=stat.S_IFLNK) if path == child else original(path)):
                with self.assertRaisesRegex(ValueError, 'symbolic link'):
                    worker.footprint(root)
            def disappearing(path):
                if path == child:
                    raise FileNotFoundError(path)
                return original(path)
            with patch.object(Path, 'lstat', disappearing):
                with self.assertRaisesRegex(ValueError, 'changed while measuring'):
                    worker.footprint(root)

    def test_failure_reporting_cannot_mask_the_primary_error(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            request = base / 'request.json'
            request.write_text('{}')
            mocks.enter_context(patch.object(worker, 'STATE', base / 'state'))
            mocks.enter_context(patch.object(worker, 'REQUEST', request))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            mocks.enter_context(patch.object(worker, 'read_request', side_effect=ValueError('primary refusal')))
            mocks.enter_context(patch.object(worker, 'status', side_effect=OSError(28, 'No space left on device')))
            mocks.enter_context(patch.object(Path, 'unlink', side_effect=OSError('request cleanup failed')))
            with patch.object(worker, 'warn') as warn:
                with self.assertRaisesRegex(ValueError, 'primary refusal'):
                    worker.main()
            output = ' '.join(str(c.args[0]) for c in warn.call_args_list)
            self.assertIn('primary refusal', output)
            self.assertIn('request cleanup failed', output)
            self.assertIn('recovery details', output)

    def test_running_status_disk_failure_is_diagnosed_before_any_installation(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            request = base / 'request.json'
            request.write_text('{"version":"v0.0.55"}')
            for name, value in [('ROOT', base / 'installed'), ('STATE', base / 'state'), ('REQUEST', request)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            failure = OSError(28, 'No space left on device')
            mocks.enter_context(patch.object(worker, 'status', side_effect=failure))
            service = mocks.enter_context(patch.object(worker, 'service'))
            with patch.object(worker, 'warn') as warn:
                with self.assertRaises(OSError) as caught:
                    worker.main()
            self.assertIs(caught.exception, failure)
            self.assertIn('expand the container disk', warn.call_args_list[0].args[0])
            service.assert_not_called()

    def test_audit_failure_does_not_invalidate_saved_status(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'STATE', Path(folder)):
            original = Path.open
            def open_path(path, *args, **kwargs):
                if path.name == 'audit.jsonl':
                    raise OSError(28, 'No space left on device')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'open', open_path), patch.object(worker, 'warn') as warn:
                worker.status('complete', backup='retained')
            self.assertEqual(json.loads((Path(folder) / 'status.json').read_text())['backup'], 'retained')
            warn.assert_called_once()

    def test_download_uses_announced_size_but_keeps_streamed_limits(self):
        for length in ('7', None, '3'):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as folder:
                response = MagicMock()
                response.__enter__.return_value = response
                response.headers = {} if length is None else {'Content-Length': length}
                response.read.side_effect = io.BytesIO(b'content').read
                target = Path(folder) / 'archive'
                with patch.object(worker, 'urlopen', return_value=response), patch.object(worker, 'require_storage') as capacity:
                    worker.download('https://example.test/archive', target, 1000)
                self.assertEqual(target.read_bytes(), b'content')
                self.assertEqual(capacity.call_args_list[0].args[0], [(target.parent, int(length) if length else 1000, 1)])
                self.assertEqual(capacity.call_count, 2 if length == '3' else 1)
        with tempfile.TemporaryDirectory() as folder:
            response = MagicMock()
            response.__enter__.return_value = response
            target = Path(folder) / 'archive'
            for headers in ({'Content-Length': '1001'}, {}):
                response.headers = headers
                response.read.side_effect = io.BytesIO(b'x' * 1001).read
                with patch.object(worker, 'urlopen', return_value=response), patch.object(worker, 'require_storage'):
                    with self.assertRaisesRegex(ValueError, 'size limit'):
                        worker.download('https://example.test/archive', target, 1000)

    def test_quota_exceeded_has_storage_recovery_guidance(self):
        for error in (OSError(worker.errno.EDQUOT, 'Disk quota exceeded'),
                      shutil.Error([('source', 'destination', '[Errno 122] Disk quota exceeded')])):
            self.assertIn('storage quota', worker.failure_message(error))
            self.assertIn('allowance', worker.failure_message(error))

    def test_rollback_safety_restore_failure_preserves_primary_error(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            state = Path(folder)
            backup = state / 'backup-1'
            safety = state / 'backup-2'
            (backup / 'web').mkdir(parents=True)
            (backup / 'config').mkdir()
            mocks.enter_context(patch.object(worker, 'STATE', state))
            mocks.enter_context(patch.object(worker, 'deployment_storage'))
            mocks.enter_context(patch.object(worker, 'verify_backup', return_value={'method': 'manifest'}))
            mocks.enter_context(patch.object(worker, 'record_backup_verification'))
            mocks.enter_context(patch.object(worker, 'backup_current', return_value=safety))
            mocks.enter_context(patch.object(worker, 'service'))
            primary = ValueError('primary rollback failure')
            mocks.enter_context(patch.object(worker, 'restore', side_effect=[primary, OSError(28, 'No space left on device')]))
            mocks.enter_context(patch.object(worker, 'warn'))
            with self.assertRaisesRegex(ValueError, 'primary rollback failure') as caught:
                worker.rollback()
            self.assertIs(caught.exception, primary)
            self.assertEqual(primary.recovery_backup, str(safety))
            self.assertIn('expand the container disk', primary.recovery_error)

    def test_storage_sums_allocations_on_the_same_filesystem(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker.shutil, 'disk_usage', return_value=MagicMock(free=worker.STORAGE_RESERVE + 25)):
                with self.assertRaisesRegex(ValueError, 'Not enough disk space'):
                    worker.require_storage([(Path(folder), 10, 1), (Path(folder) / 'new', 20, 1)])
            with patch.object(worker.os, 'statvfs', return_value=MagicMock(f_files=10000, f_favail=1025)):
                with self.assertRaisesRegex(ValueError, 'free inodes'):
                    worker.require_storage([(Path(folder), 0, 2)])

    def test_storage_failure_keeps_running_installation_and_previous_backup(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, state, config = base / 'installed', base / 'state', base / 'config'
            for path in (root / 'web', state, config):
                path.mkdir(parents=True)
            (root / 'web/server.py').write_text('original')
            (state / 'status.json').write_text(json.dumps({'backup': 'previous-backup'}))
            request = base / 'request.json'
            request.write_text('{"version":"v0.0.55"}')
            package = base / 'package'
            package.mkdir()
            (package / 'VERSION').write_text('v0.0.55')
            for name, value in [('ROOT', root), ('STATE', state), ('CONFIG', config), ('REQUEST', request)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            download = mocks.enter_context(patch.object(worker, 'download'))
            mocks.enter_context(patch.object(worker, 'sha256', return_value='a' * 64))
            mocks.enter_context(patch.object(worker, 'extract', return_value=package))
            mocks.enter_context(patch.object(worker, 'release_identity', return_value={}))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            # A valid checksum is still required before the installation check.
            download.side_effect = lambda url, target, limit: target.write_text('a' * 64 + '  herdr-proxmox.tar.gz')
            mocks.enter_context(patch.object(worker.shutil, 'disk_usage', side_effect=lambda path:
                                            MagicMock(free=0 if Path(path) == root else 10**12)))
            service = mocks.enter_context(patch.object(worker, 'service'))
            backup = mocks.enter_context(patch.object(worker, 'backup_current'))
            replace = mocks.enter_context(patch.object(worker, 'replace_install'))
            with self.assertRaisesRegex(ValueError, 'expand the container disk'):
                worker.main()
            service.assert_not_called()
            backup.assert_not_called()
            replace.assert_not_called()
            self.assertEqual((root / 'web/server.py').read_text(), 'original')
            saved = json.loads((state / 'status.json').read_text())
            self.assertEqual((saved['state'], saved['backup'], saved['rolled_back']), ('failed', 'previous-backup', False))

    def test_copytree_disk_full_error_is_actionable(self):
        error = shutil.Error([('source', 'destination', '[Errno 28] No space left on device')])
        self.assertIn('expand the container disk', worker.failure_message(error))

    def test_failed_recovery_retains_primary_error_and_backup(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            request = base / 'request.json'
            request.write_text('{"version":"v0.0.55"}')
            source = base / 'package'
            source.mkdir()
            (source / 'VERSION').write_text('v0.0.55')
            backup_path = base / 'backup-1'
            for name, value in [('ROOT', base / 'installed'), ('STATE', base / 'state'), ('REQUEST', request)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            download = mocks.enter_context(patch.object(worker, 'download'))
            download.side_effect = lambda url, target, limit: target.write_text('a' * 64 + '  herdr-proxmox.tar.gz')
            mocks.enter_context(patch.object(worker, 'sha256', return_value='a' * 64))
            mocks.enter_context(patch.object(worker, 'extract', return_value=source))
            mocks.enter_context(patch.object(worker, 'release_identity', return_value={}))
            mocks.enter_context(patch.object(worker, 'deployment_storage'))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            mocks.enter_context(patch.object(worker, 'service'))
            mocks.enter_context(patch.object(worker, 'backup_current', return_value=backup_path))
            mocks.enter_context(patch.object(worker, 'replace_install', side_effect=OSError(28, 'No space left on device')))
            restored = mocks.enter_context(patch.object(worker, 'restore', side_effect=ValueError('recovery unavailable')))
            with self.assertRaisesRegex(OSError, 'No space left on device'):
                worker.main()
            restored.assert_called_once_with(backup_path, recovery=True)
            saved = json.loads((base / 'state/status.json').read_text())
            self.assertFalse(saved['rolled_back'])
            self.assertEqual(saved['backup'], str(backup_path))
            self.assertEqual(saved['recovery_error'], 'recovery unavailable')
            self.assertIn('expand the container disk', saved['error'])

    def test_extraction_refuses_low_storage_before_writing_package_files(self):
        with tempfile.TemporaryDirectory() as folder:
            staging = Path(folder)
            archive = staging / 'release.tar'
            with tarfile.open(archive, 'w') as package:
                package.add(staging, arcname='herdr-proxmox', recursive=False)
            with patch.object(worker.shutil, 'disk_usage', return_value=MagicMock(free=0)):
                with self.assertRaisesRegex(ValueError, 'Not enough disk space'):
                    worker.extract(archive, staging)
            self.assertFalse((staging / 'herdr-proxmox').exists())

    def test_rollback_refuses_low_storage_before_stopping_service(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / 'backup-1/web').mkdir(parents=True)
            (state / 'backup-1/config').mkdir()
            with patch.object(worker, 'STATE', state), patch.object(worker, 'ROOT', state / 'installed'), \
                    patch.object(worker, 'CONFIG', state / 'config'), \
                    patch.object(worker, 'verify_backup', return_value={'method': 'manifest'}), \
                    patch.object(worker, 'record_backup_verification'), \
                    patch.object(worker.shutil, 'disk_usage', return_value=MagicMock(free=0)), \
                    patch.object(worker, 'service') as service:
                with self.assertRaisesRegex(ValueError, 'Not enough disk space'):
                    worker.rollback()
                service.assert_not_called()

    def test_missing_routes_are_diagnosed_without_modifying_network(self):
        error = updates.URLError(updates.socket.gaierror(-3, 'DNS unavailable'))
        def command(args, **kwargs):
            return MagicMock(returncode=2 if 'get' in args else 0, stdout='')
        with patch.object(updates.subprocess, 'run', side_effect=command) as run:
            message = updates.lookup_diagnostic(error)
        self.assertIn('No route to the Internet', message)
        self.assertIn('survives a reboot', message)
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['ip', '-4', 'route', 'get', '8.8.8.8'],
            ['ip', '-4', 'route', 'show', 'default'],
            ['ip', '-6', 'route', 'get', '2001:4860:4860::8888'],
            ['ip', '-6', 'route', 'show', 'default']])
        self.assertIn('IPv4 default route: not configured', message)
        self.assertIn('ip route add default is temporary', message)
        self.assertTrue(all(c.kwargs['timeout'] == updates.ROUTE_TIMEOUT for c in run.call_args_list))

    def test_dns_diagnosis_handles_ipv6_and_unavailable_ip_command(self):
        error = updates.URLError(updates.socket.gaierror(-3, 'DNS unavailable'))
        for results in ([MagicMock(returncode=2), MagicMock(returncode=0, stdout=''),
                         MagicMock(returncode=0), MagicMock(returncode=0, stdout='default via fe80::1 dev eth0')], FileNotFoundError()):
            with patch.object(updates.subprocess, 'run', side_effect=results):
                self.assertIn('DNS lookup failed', updates.lookup_diagnostic(error))
        with patch.object(updates.subprocess, 'run') as run:
            self.assertIsNone(updates.lookup_diagnostic(updates.HTTPError('https://api.github.com', 403, 'Forbidden', {}, None)))
            run.assert_not_called()

    def test_configured_routes_are_not_claimed_reachable(self):
        for lookup in (0, 2):
            def command(args, **kwargs):
                return MagicMock(returncode=lookup if 'get' in args else 0,
                                 stdout='default via 10.0.0.1 dev eth0' if args[1] == '-4' else '')
            with patch.object(updates.subprocess, 'run', side_effect=command):
                message = updates.lookup_diagnostic(TimeoutError('Timed out'))
            self.assertIn('default via 10.0.0.1 dev eth0', message)
            self.assertIn('Gateway reachability was not tested', message)
            self.assertNotIn('No route to the Internet was found', message)

    def test_probe_limitations_are_reported(self):
        for error, expected in ((FileNotFoundError(), 'ip is unavailable'),
                                (updates.subprocess.TimeoutExpired('ip', 2), 'timed out')):
            with patch.object(updates.subprocess, 'run', side_effect=error):
                message = updates.lookup_diagnostic(updates.socket.gaierror(-3, 'DNS unavailable'))
            self.assertIn(expected, message)
            self.assertIn('Routing could not be fully checked', message)
            self.assertNotIn('No route to the Internet was found', message)

    def test_failures_log_on_change_count_retries_and_reset_on_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = updates.Updates(root, root / 'queue', root / 'status.json')
            error = updates.URLError(updates.socket.gaierror(-3, 'DNS unavailable'))
            release = dict(tag_name='v0.0.2', html_url='https://example.test/release')
            with patch.object(updates, 'latest_release', side_effect=[error, error, TimeoutError('Timed out'), release, error]) as fetch, \
                    patch.object(updates, 'lookup_diagnostic', return_value='Check gateway'), \
                    patch.object(updates.logging, 'getLogger') as logger:
                first = service.snapshot()
                self.assertEqual(first['check_failures'], 1)
                self.assertIn('T', first['check_failed_since'])
                self.assertEqual(service.snapshot()['check_failures'], 1)
                self.assertEqual(fetch.call_count, 1)  # Cached reads are not failed attempts.
                second = service.snapshot(force=True)
                self.assertEqual(second['check_failures'], 2)
                self.assertEqual(first['check_failed_since'], second['check_failed_since'])
                self.assertEqual(service.snapshot(force=True)['check_failures'], 3)
                recovered = service.snapshot(force=True)
                self.assertEqual(recovered['check_failures'], 0)
                self.assertIsNone(recovered['check_failed_since'])
                self.assertEqual(service.snapshot(force=True)['check_failures'], 1)
                self.assertEqual([c.args[0] for c in logger.return_value.log.call_args_list],
                                 [updates.logging.WARNING, updates.logging.DEBUG, updates.logging.WARNING, updates.logging.WARNING])
                logger.return_value.info.assert_called_once_with(
                    'Release lookup recovered after %d failures since %s', 3, first['check_failed_since'])

    def test_manual_retry_recovers_immediately_and_clears_diagnosis(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = updates.Updates(root, root / 'queue', root / 'status.json')
            release = dict(tag_name='v0.0.2', html_url='https://example.test/release')
            with patch.object(updates, 'latest_release', side_effect=[
                    updates.URLError(updates.socket.gaierror(-3, 'DNS unavailable')), release]) as fetch, \
                    patch.object(updates, 'lookup_diagnostic', return_value='Missing gateway'):
                self.assertEqual(service.snapshot()['check_diagnostic'], 'Missing gateway')
                service.snapshot()
                self.assertEqual(fetch.call_count, 1)
                recovered = service.snapshot(force=True)
                self.assertEqual(recovered['latest'], 'v0.0.2')
                self.assertIsNone(recovered['check_error'])
                self.assertIsNone(recovered['check_diagnostic'])
                self.assertEqual(fetch.call_count, 2)

    def test_release_source_provenance_is_preserved_and_mismatches_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with patch.object(worker, 'resolve_tag_sha', return_value=None):
                self.assertIsNone(worker.release_identity(source, 'v0.0.24')['source_sha'])
            metadata = dict(build_id='v0.0.24', source_sha='c' * 40, deployment_mode='release')
            (source / 'BUILD.json').write_text(json.dumps(metadata))
            self.assertEqual(worker.release_identity(source, 'v0.0.24')['source_sha'], 'c' * 40)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                worker.release_identity(source, 'v0.0.25')
            metadata['source_sha'] = 'invalid'
            (source / 'BUILD.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                worker.release_identity(source, 'v0.0.24')

    def test_offline_release_lookup_keeps_local_recovery_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / 'queue'
            queue.mkdir()
            state = root / 'status.json'
            state.write_text(json.dumps({'state': 'running', 'backup': 'backup-1'}))
            service = updates.Updates(root, queue, state)
            with patch.object(updates, 'latest_release', side_effect=OSError('offline')):
                data = service.snapshot()
            self.assertEqual(data['state'], 'running')
            self.assertTrue(data['local_supported'])
            self.assertTrue(data['rollback_available'])
            self.assertIsNone(data['latest'])
            self.assertFalse(data['available'])
            self.assertEqual(data['check_error'], 'offline')

    def test_worker_artifact_root_matches_the_validation_runner(self):
        from validation import ValidationRuns
        runner = ValidationRuns('/home/herdr/projects', lambda _: None)
        self.assertEqual(worker.LOCAL_ARTIFACTS, runner.root)

    def test_readiness_never_falls_back_to_an_unauthenticated_root_page(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'CONFIG', Path(directory)), patch.object(worker, 'urlopen') as request:
            with self.assertRaisesRegex(ValueError, 'requires the gateway token'):
                worker.wait_for_ready('a' * 32)
            request.assert_not_called()

    def test_failed_rollback_restores_the_quiesced_safety_backup(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as mocks:
            state = Path(directory)
            old = state / 'backup-1'
            (old / 'web').mkdir(parents=True)
            (old / 'config').mkdir()
            safety = state / 'backup-2'
            mocks.enter_context(patch.object(worker, 'STATE', state))
            mocks.enter_context(patch.object(worker, 'verify_backup', return_value={'method': 'manifest'}))
            mocks.enter_context(patch.object(worker, 'record_backup_verification'))
            service = mocks.enter_context(patch.object(worker, 'service'))
            def backup():
                service.assert_called_with('stop')
                return safety
            mocks.enter_context(patch.object(worker, 'backup_current', side_effect=backup))
            restored = mocks.enter_context(patch.object(worker, 'restore'))
            mocks.enter_context(patch.object(worker, 'wait_for_ready', side_effect=ValueError('unhealthy')))
            with self.assertRaisesRegex(ValueError, 'unhealthy') as failure:
                worker.rollback()
            self.assertEqual(restored.call_args_list, [call(old), call(safety, recovery=True)])
            self.assertEqual(failure.exception.recovery_backup, str(safety))

    def test_release_cannot_replace_local_deployment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'queue').mkdir()
            (root / 'VERSION').write_text('v0.0.1')
            (root / 'BUILD.json').write_text('{"deployment_mode":"local"}')
            service = updates.Updates(root, root / 'queue', root / 'status.json')
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v0.0.3', html_url='https://example.test')):
                self.assertFalse(service.snapshot()['supported'])
                with self.assertRaisesRegex(ValueError, 'local deployment'):
                    service.install({'version': 'v0.0.3'})
            self.assertFalse((root / 'queue/request.json').exists())

    def test_manual_check_bypasses_background_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = updates.Updates(root, root / 'queue', root / 'status.json')
            old = dict(tag_name='v0.0.2', html_url='https://example.test/old')
            new = dict(tag_name='v0.0.3', html_url='https://example.test/new')
            with patch.object(updates, 'latest_release', side_effect=[old, new]) as fetch:
                self.assertEqual(service.snapshot()['latest'], 'v0.0.2')
                self.assertEqual(service.snapshot()['latest'], 'v0.0.2')
                self.assertEqual(service.snapshot(force=True)['latest'], 'v0.0.3')
                self.assertEqual(fetch.call_count, 2)

    def test_refresh_does_not_block_cached_snapshot_and_failure_keeps_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = updates.Updates(root, root / 'queue', root / 'status.json')
            release = dict(tag_name='v0.0.2', html_url='https://example.test/old')
            with patch.object(updates, 'latest_release', return_value=release):
                service.snapshot()
            entered, finish = threading.Event(), threading.Event()
            def slow_fetch():
                entered.set()
                if not finish.wait(3):
                    raise RuntimeError('Test refresh timed out')
                raise OSError('Network unavailable')
            with patch.object(updates, 'latest_release', side_effect=slow_fetch) as fetch:
                worker_thread = threading.Thread(target=lambda: service.snapshot(force=True))
                worker_thread.start()
                self.assertTrue(entered.wait(2))
                try:
                    self.assertEqual(service.snapshot()['latest'], 'v0.0.2')
                finally:
                    finish.set()
                    worker_thread.join(3)
                self.assertFalse(worker_thread.is_alive())
                self.assertEqual(service.snapshot()['latest'], 'v0.0.2')
                self.assertEqual(fetch.call_count, 1)

    def test_bad_checksum_does_not_stop_gateway(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            request = base / 'request.json'
            request.write_text('{"version":"v0.2.0"}')
            mocks.enter_context(patch.object(worker, 'STATE', base / 'state'))
            mocks.enter_context(patch.object(worker, 'REQUEST', request))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            def download(url, destination, limit):
                destination.write_text('0' * 64 + '  herdr-proxmox.tar.gz' if url.endswith('.sha256') else 'corrupt archive')
            mocks.enter_context(patch.object(worker, 'download', side_effect=download))
            def inspect_service(action):
                if action == 'start' and (root / 'VERSION').read_text() == 'v0.2.0':
                    self.assertEqual((root / 'install/web-install.sh').read_text(), 'new installer')
                    self.assertTrue((root / 'install/sdk-install.py').exists())
            service = mocks.enter_context(patch.object(worker, 'service', side_effect=inspect_service))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                worker.main()
            service.assert_not_called()
            self.assertEqual(json.loads((base / 'state/status.json').read_text())['state'], 'failed')

    def test_replacement_enforces_root_ownership_on_the_posix_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, source = base / 'installed', base / 'source'
            for directory in (root, source):
                (directory / 'web/gateway').mkdir(parents=True)
                (directory / 'web/public').mkdir()
            (root / 'web/gateway/server.py').write_text('old gateway', encoding='utf-8')
            (source / 'web/gateway/server.py').write_text('new gateway', encoding='utf-8')
            (source / 'web/public/index.html').write_text('new dashboard', encoding='utf-8')
            (source / 'VERSION').write_text('v0.2.0', encoding='utf-8')
            ownership = MagicMock()
            # Select only the updater's POSIX branch. pathlib/shutil keep the
            # host filesystem; the privileged chown operation is intercepted.
            posix = SimpleNamespace(name='posix', chown=ownership)
            original_chmod = Path.chmod
            with patch.object(worker, 'ROOT', root), patch.object(worker, 'PUBLISHING_POLICY', base / 'policy.json'), \
                    patch.object(worker, 'os', posix), \
                    patch.object(Path, 'chmod', autospec=True, side_effect=original_chmod) as chmod:
                worker.replace_install(source, {'build_id':'v0.2.0'})
                self.assertEqual(ownership.call_args_list, [call(root, 0, 0), call(root / 'web', 0, 0)])
                self.assertIn(call(root, 0o755), chmod.call_args_list)
                self.assertIn(call(root / 'web', 0o755), chmod.call_args_list)
                self.assertIn(call(root / 'web/gateway/server.py', 0o644), chmod.call_args_list)
                self.assertEqual((root / 'web/gateway/server.py').read_text(encoding='utf-8'), 'new gateway')
                # Production must still fail if ownership cannot be established.
                ownership.side_effect = PermissionError('root required')
                (source / 'web/gateway/server.py').write_text('must not replace', encoding='utf-8')
                with self.assertRaisesRegex(PermissionError, 'root required'):
                    worker.replace_install(source, {'build_id':'v0.3.0'})
                self.assertEqual((root / 'web/gateway/server.py').read_text(encoding='utf-8'), 'new gateway')

    def test_failed_startup_restores_files_and_database(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, state, config = base / 'installed', base / 'state', base / 'config'
            request = base / 'request.json'
            (root / 'web/gateway').mkdir(parents=True)
            (root / 'web/public').mkdir()
            (root / 'web/gateway/server.py').write_text('old gateway')
            (root / 'VERSION').write_text('v0.1.0')
            (root / 'install').mkdir()
            (root / 'install/web-install.sh').write_text('old installer')
            (root / '.fvmrc').write_text('{"flutter":"1.2.3"}')
            config.mkdir()
            with worker.sqlite3.connect(config / 'organizations.sqlite3') as database:
                database.execute('CREATE TABLE evidence (value TEXT)')
                database.execute("INSERT INTO evidence VALUES ('original database')")
            database.close()
            original_database = (config / 'organizations.sqlite3').read_bytes()
            request.write_text('{"version":"v0.2.0"}')
            package = base / 'herdr-proxmox'
            for name in ('web/gateway/server.py', 'web/public/index.html', 'web/public/main.dart.js', 'web/public/dashboard/index.html'):
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('')
            (package / 'install').mkdir()
            (package / 'install/web-install.sh').write_text('new installer')
            (package / 'install/sdk-install.py').write_text('new sdk worker')
            (package / 'install/toolchains.json').write_text('{}')
            (package / '.fvmrc').write_text('{"flutter":"4.5.6"}')
            (package / 'VERSION').write_text('v0.2.0')
            archive = base / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as file:
                file.add(package, arcname='herdr-proxmox')
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            def download(url, destination, limit):
                if url.endswith('.sha256'):
                    destination.write_text(checksum + '  herdr-proxmox.tar.gz')
                else:
                    shutil.copyfile(archive, destination)
            for name, value in [('ROOT', root), ('STATE', state), ('CONFIG', config), ('REQUEST', request)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            mocks.enter_context(patch.object(tarfile.TarFile, 'chown'))
            # The fixture simulates root; do not attempt a real ownership change
            # on the unprivileged CI runner's temporary installation.
            ownership = mocks.enter_context(patch.object(worker.os, 'chown', create=True))
            mocks.enter_context(patch.object(worker, 'download', side_effect=download))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            def inspect_service(action):
                if action == 'start' and (root / 'VERSION').read_text() == 'v0.2.0':
                    self.assertEqual((root / 'install/web-install.sh').read_text(), 'new installer')
                    self.assertTrue((root / 'install/sdk-install.py').exists())
                    self.assertEqual(json.loads((root / '.fvmrc').read_text())['flutter'], '4.5.6')
            service = mocks.enter_context(patch.object(worker, 'service', side_effect=inspect_service))
            mocks.enter_context(patch.object(worker, 'urlopen', side_effect=OSError('unavailable')))
            mocks.enter_context(patch.object(worker.time, 'sleep'))
            with self.assertRaisesRegex(ValueError, 'startup check'):
                worker.main()
            self.assertEqual((root / 'install/web-install.sh').read_text(), 'old installer')
            self.assertFalse((root / 'install/sdk-install.py').exists())
            self.assertEqual(json.loads((root / '.fvmrc').read_text())['flutter'], '1.2.3')
            self.assertEqual((root / 'VERSION').read_text(), 'v0.1.0')
            self.assertEqual((root / 'web/gateway/server.py').read_text(), 'old gateway')
            self.assertEqual((config / 'organizations.sqlite3').read_bytes(), original_database)
            self.assertTrue(json.loads((state / 'status.json').read_text())['rolled_back'])
            self.assertEqual([call.args[0] for call in service.call_args_list], ['stop', 'start', 'stop', 'start'])
            if worker.os.name != 'nt':
                ownership.assert_has_calls([call(root, 0, 0), call(root / 'web', 0, 0)])

    def test_version_check_request_and_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = root / 'queue'
            queue.mkdir()
            (root / 'VERSION').write_text('v0.1.0')
            release = {'tag_name': 'v0.2.0', 'html_url': 'https://github.com/Ultarius/herdr_proxmox/releases/tag/v0.2.0'}
            service = updates.Updates(root, queue, root / 'status.json')
            with patch.object(updates, 'latest_release', return_value=release) as fetch:
                self.assertTrue(service.snapshot()['available'])
                with self.assertRaises(ValueError):
                    service.install({'version': 'v0.3.0'})
                self.assertFalse((queue / 'request.json').exists())
                service.install({'version': 'v0.2.0'})
                self.assertEqual(json.loads((queue / 'request.json').read_text()), {'version': 'v0.2.0'})
                with self.assertRaises(ValueError):
                    service.install({'version': 'v0.2.0'})
                self.assertEqual(fetch.call_count, 1)

    def test_no_downgrade_or_same_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = {'tag_name': 'v1.2.0', 'html_url': ''}
            service = updates.Updates(root, root, root / 'status.json')
            with patch.object(updates, 'latest_release', return_value=release):
                for version in ('v1.2.0', 'v1.10.0'):
                    (root / 'VERSION').write_text(version)
                    self.assertFalse(service.snapshot()['available'])

    def test_failed_same_version_release_can_be_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = root / 'queue'
            queue.mkdir()
            state = root / 'status.json'
            (root / 'VERSION').write_text('v0.0.55')
            service = updates.Updates(root, queue, state)
            with patch.object(updates, 'latest_release', return_value={'tag_name': 'v0.0.55', 'html_url': ''}):
                # Cover the legacy worker status shown by existing containers.
                for mode in (None, 'release'):
                    state.write_text(json.dumps({'state': 'failed', **({'mode': mode} if mode else {})}))
                    self.assertTrue(service.snapshot()['available'])
                    self.assertEqual(service.install({'version': 'v0.0.55'})['state'], 'queued')
                    self.assertEqual(json.loads((queue / 'request.json').read_text())['version'], 'v0.0.55')
                    with self.assertRaisesRegex(ValueError, 'already running'):
                        service.install({'version': 'v0.0.55'})
                    (queue / 'request.json').unlink()
                for mode in ('alpha', 'local', 'rollback', 'provenance'):
                    state.write_text(json.dumps({'state': 'failed', 'mode': mode}))
                    self.assertFalse(service.snapshot()['available'])
                state.write_text(json.dumps({'state': 'failed', 'mode': 'release', 'version': 'v0.0.54'}))
                self.assertFalse(service.snapshot()['available'])
                state.write_text(json.dumps({'state': 'failed', 'mode': 'release'}))
                (root / 'VERSION').write_text('v0.0.56')
                self.assertFalse(service.snapshot()['available'])

    def test_promote_and_rollback_queue_fixed_requests_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = root / 'queue'
            queue.mkdir()
            state = root / 'status.json'
            service = updates.Updates(root, queue, state)
            manifest = {'build_id': 'a' * 32, 'sha256': 'b' * 64, 'deployment_mode': 'local'}
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v0.2.0', html_url='')):
                with self.assertRaisesRegex(ValueError, 'retained local deployment'):
                    service.promote({'build_id': 'nope'}, root / 'deployment.tar.gz')
                with self.assertRaisesRegex(ValueError, 'No completed deployment backup'):
                    service.rollback()
                self.assertFalse(service.snapshot()['rollback_available'])
                self.assertEqual(service.promote(manifest, root / 'deployment.tar.gz', 'damien'), {'state': 'queued', 'mode': 'local', 'build_id': 'a' * 32})
                request = json.loads((queue / 'request.json').read_text())
                self.assertEqual(request['mode'], 'local')
                self.assertEqual((request['build_id'], request['sha256'], request['actor']), ('a' * 32, 'b' * 64, 'damien'))
                self.assertTrue(request['path'].endswith('deployment.tar.gz'))
                with self.assertRaisesRegex(ValueError, 'already running'):
                    service.promote(manifest, root / 'deployment.tar.gz')
                with self.assertRaisesRegex(ValueError, 'already running'):
                    service.rollback()
            (queue / 'request.json').unlink()
            state.write_text(json.dumps({'state': 'complete', 'backup': str(root / 'backup-1')}))
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v0.2.0', html_url='')):
                info = service.snapshot()
                self.assertTrue(info['rollback_available'])
                self.assertEqual(service.rollback('kit'), {'state': 'queued', 'mode': 'rollback'})
            self.assertEqual(json.loads((queue / 'request.json').read_text()), {'mode': 'rollback', 'actor': 'kit'})
            (queue / 'request.json').unlink()
            self.assertEqual(service.promote(manifest, root / 'deployment.tar.gz')['build_id'], 'a' * 32)

    def test_worker_reads_local_and_rollback_requests_strictly(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            artifacts = base / 'artifacts'
            run_id = 'c' * 32
            (artifacts / run_id).mkdir(parents=True)
            archive = artifacts / run_id / 'deployment.tar.gz'
            archive.write_bytes(b'package')
            request = base / 'request.json'
            mocks.enter_context(patch.object(worker, 'REQUEST', request))
            mocks.enter_context(patch.object(worker, 'LOCAL_ARTIFACTS', artifacts))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            request.write_text(json.dumps(dict(mode='local', build_id=run_id, sha256=digest, path=str(archive), actor='damien')))
            parsed = worker.read_request()
            self.assertEqual((parsed['mode'], parsed['build_id'], parsed['actor']), ('local', run_id, 'damien'))
            request.write_text(json.dumps(dict(mode='local', build_id=run_id, sha256=digest,
                                               path=str(artifacts / 'other' / 'deployment.tar.gz'))))
            with self.assertRaisesRegex(ValueError, 'retained build directory'):
                worker.read_request()
            request.write_text(json.dumps(dict(mode='local', build_id='not-hex', sha256=digest, path=str(archive))))
            with self.assertRaisesRegex(ValueError, 'build ID'):
                worker.read_request()
            request.write_text(json.dumps(dict(mode='rollback', actor='damien')))
            self.assertEqual(worker.read_request(), {'mode': 'rollback', 'actor': 'damien'})
            request.write_text('{"version":"not-a-version"}')
            with self.assertRaisesRegex(ValueError, 'release version'):
                worker.read_request()
            request.write_text('{"mode":"unexpected","version":"v0.2.0"}')
            with self.assertRaisesRegex(ValueError, 'deployment mode'):
                worker.read_request()

    def test_local_promotion_installs_the_approved_package(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, state, config, artifacts = base / 'installed', base / 'state', base / 'config', base / 'artifacts'
            run_id = 'a' * 32
            (root / 'web/gateway').mkdir(parents=True)
            (root / 'web/public').mkdir()
            (root / 'web/gateway/server.py').write_text('old gateway')
            (root / 'VERSION').write_text('v0.1.0')
            (root / 'BUILD.json').write_text(json.dumps(dict(build_id='v0.1.0', deployment_mode='release')))
            config.mkdir()
            (config / 'token').write_text('secret')
            package = base / 'pkg'
            for name in ('web/gateway/server.py', 'web/public/index.html', 'web/public/main.dart.js', 'web/public/dashboard/index.html'):
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('new ' + name)
            (package / 'VERSION').write_text('v0.9.9')
            (package / 'BUILD.json').write_text(json.dumps(dict(build_id=run_id, source_sha='b' * 40, deployment_mode='local')))
            # The release carries its version declaration and the catalog that
            # reads it; a package without them is refused before promotion.
            (package / '.fvmrc').write_text('{"flutter":"3.44.8"}')
            (package / 'install').mkdir()
            (package / 'install/toolchains.json').write_text('{}')
            (artifacts / run_id).mkdir(parents=True)
            archive = artifacts / run_id / 'deployment.tar.gz'
            with tarfile.open(archive, 'w:gz') as file:
                file.add(package, arcname='herdr-proxmox')
            request = base / 'request.json'
            request.write_text(json.dumps(dict(mode='local', build_id=run_id,
                                               sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                                               path=str(archive), actor='damien')))
            for name, value in [('ROOT', root), ('STATE', state), ('CONFIG', config), ('REQUEST', request), ('LOCAL_ARTIFACTS', artifacts)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            mocks.enter_context(patch.object(tarfile.TarFile, 'chown'))
            # The fixture simulates root; do not attempt a real ownership change
            # on the unprivileged CI runner's temporary installation.
            ownership = mocks.enter_context(patch.object(worker.os, 'chown', create=True))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            mocks.enter_context(patch.object(worker, 'service'))
            ready = mocks.enter_context(patch.object(worker, 'wait_for_ready'))
            worker.main()
            ready.assert_called_once_with(run_id)
            if worker.os.name != 'nt':
                ownership.assert_has_calls([call(root, 0, 0), call(root / 'web', 0, 0)])
            self.assertEqual((root / 'web/gateway/server.py').read_text(), 'new web/gateway/server.py')
            self.assertEqual(json.loads((root / 'BUILD.json').read_text())['build_id'], run_id)
            self.assertFalse(request.exists())
            saved = json.loads((state / 'status.json').read_text())
            self.assertEqual((saved['state'], saved['mode'], saved['build_id']), ('complete', 'local', run_id))
            self.assertEqual((Path(saved['backup']) / 'web/gateway/server.py').read_text(), 'old gateway')
            history = [json.loads(line) for line in (state / 'audit.jsonl').read_text().splitlines()]
            self.assertEqual([entry['state'] for entry in history], ['running', 'complete'])
            self.assertTrue(all(entry['actor'] == 'damien' for entry in history))

    def test_rollback_restores_the_newest_backup_and_keeps_a_safety_copy(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, state, config = base / 'installed', base / 'state', base / 'config'
            (root / 'web/gateway').mkdir(parents=True)
            (root / 'web/gateway/server.py').write_text('current gateway')
            (root / 'VERSION').write_text('v0.2.0')
            (root / 'BUILD.json').write_text(json.dumps(dict(build_id='v0.2.0', deployment_mode='release')))
            config.mkdir()
            (config / 'token').write_text('secret')
            old = state / 'backup-1'
            (old / 'web/gateway').mkdir(parents=True)
            (old / 'web/gateway/server.py').write_text('old gateway')
            (old / 'web/public').mkdir()
            (old / 'web/public/index.html').write_text('old dashboard')
            (old / 'config').mkdir()
            (old / 'config/token').write_text('old secret')
            (old / 'VERSION').write_text('v0.1.0')
            (old / 'BUILD.json').write_text(json.dumps(dict(build_id='v0.1.0', deployment_mode='release')))
            request = base / 'request.json'
            request.write_text(json.dumps(dict(mode='rollback', actor='damien')))
            for name, value in [('ROOT', root), ('STATE', state), ('CONFIG', config), ('REQUEST', request)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            mocks.enter_context(patch.object(worker, 'service'))
            ready = mocks.enter_context(patch.object(worker, 'wait_for_ready'))
            worker.main()
            ready.assert_called_once_with('v0.1.0')
            self.assertEqual((root / 'web/gateway/server.py').read_text(), 'old gateway')
            self.assertEqual((root / 'VERSION').read_text(), 'v0.1.0')
            saved = json.loads((state / 'status.json').read_text())
            self.assertEqual((saved['state'], saved['mode'], saved['restored']), ('complete', 'rollback', str(old)))
            safety = Path(saved['safety'])
            self.assertEqual((safety / 'web/gateway/server.py').read_text(), 'current gateway')
            self.assertEqual(saved['backup'], str(safety))

    def test_readiness_requires_the_expected_running_build(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            config = Path(folder)
            (config / 'token').write_text('secret')
            response = MagicMock()
            response.status = 200
            response.read.return_value = json.dumps({'build_id': 'x' * 32}).encode()
            response.__enter__.return_value = response
            mocks.enter_context(patch.object(worker, 'CONFIG', config))
            mocks.enter_context(patch.object(worker, 'urlopen', return_value=response))
            mocks.enter_context(patch.object(worker.time, 'sleep'))
            worker.wait_for_ready('x' * 32)
            with self.assertRaisesRegex(ValueError, 'startup check'):
                worker.wait_for_ready('y' * 32)

    def test_release_tag_resolution_handles_light_and_annotated_tags(self):
        responses = {
            'tags/v0.0.24': {'object': {'type': 'commit', 'sha': 'a' * 40}},
            'tags/v0.0.25': {'object': {'type': 'tag', 'sha': 'c' * 40, 'url': 'https://api.example.test/tag/25'}},
            'git/tags/' + 'c' * 40: {'object': {'type': 'commit', 'sha': 'b' * 40}},
        }
        def respond(request, timeout=None):
            key = next(name for name in responses if name in request.full_url)
            response = MagicMock()
            response.read.return_value = json.dumps(responses[key]).encode()
            response.__enter__.return_value = response
            return response
        with patch.object(worker, 'urlopen', side_effect=respond):
            self.assertEqual(worker.resolve_tag_sha('v0.0.24'), 'a' * 40)
            self.assertEqual(worker.resolve_tag_sha('v0.0.25'), 'b' * 40)
        with patch.object(worker, 'urlopen', side_effect=OSError('offline')):
            self.assertIsNone(worker.resolve_tag_sha('v0.0.24'))

    def test_release_identity_resolves_the_tag_when_the_package_has_none(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            with patch.object(worker, 'resolve_tag_sha', return_value='c' * 40) as resolve:
                identity = worker.release_identity(source, 'v0.0.9')
            resolve.assert_called_once_with('v0.0.9')
            self.assertEqual(identity['source_sha'], 'c' * 40)
            self.assertEqual(identity['deployment_mode'], 'release')

    def test_provenance_repair_records_the_tag_commit_without_restarting(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as mocks:
            base = Path(folder)
            root, state = base / 'installed', base / 'state'
            root.mkdir()
            state.mkdir()
            (state / 'status.json').write_text(json.dumps({'backup': 'previous-backup'}))
            (root / 'BUILD.json').write_text(json.dumps(dict(
                build_id='v0.0.24', package_version='v0.0.24', deployment_mode='release', source_sha=None)))
            request = base / 'request.json'
            request.write_text('{"mode":"provenance","actor":"damien"}')
            for name, value in [('ROOT', root), ('STATE', state), ('REQUEST', request)]:
                mocks.enter_context(patch.object(worker, name, value))
            mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
            service = mocks.enter_context(patch.object(worker, 'service'))
            mocks.enter_context(patch.object(worker, 'resolve_tag_sha', return_value='d' * 40))
            worker.main()
            service.assert_not_called()
            self.assertEqual(json.loads((root / 'BUILD.json').read_text())['source_sha'], 'd' * 40)
            saved = json.loads((state / 'status.json').read_text())
            self.assertEqual(saved['backup'], 'previous-backup')
            self.assertEqual((saved['state'], saved['mode'], saved['source_sha']), ('complete', 'provenance', 'd' * 40))

    def test_provenance_gateway_action_requires_an_unresolved_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = root / 'queue'
            queue.mkdir()
            (root / 'VERSION').write_text('v0.0.24')
            service = updates.Updates(root, queue, root / 'status.json')
            (root / 'BUILD.json').write_text(json.dumps(dict(
                build_id='v0.0.24', package_version='v0.0.24', deployment_mode='release', source_sha=None)))
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v0.0.24', html_url='')):
                self.assertTrue(service.snapshot()['provenance_repairable'])
            self.assertEqual(service.provenance('damien'), {'state': 'queued', 'mode': 'provenance'})
            self.assertEqual(json.loads((queue / 'request.json').read_text()), {'mode': 'provenance', 'actor': 'damien'})
            (queue / 'request.json').unlink()
            (root / 'BUILD.json').write_text(json.dumps(dict(
                build_id='v0.0.24', deployment_mode='release', source_sha='e' * 40)))
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v0.0.24', html_url='')):
                self.assertFalse(service.snapshot()['provenance_repairable'])
            with self.assertRaisesRegex(ValueError, 'unresolved source provenance'):
                service.provenance()

    def test_completed_provenance_status_does_not_break_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'VERSION').write_text('v0.0.24')
            (root / 'status.json').write_text(json.dumps(dict(
                state='complete', mode='provenance', source_sha='a' * 40)))
            service = updates.Updates(root, root / 'queue', root / 'status.json')
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v0.0.24', html_url='')):
                self.assertEqual(service.snapshot()['state'], 'complete')

    def test_tag_resolution_rejects_non_commit_and_cycles(self):
        response = MagicMock()
        response.__enter__.return_value = response
        for kind in ('tree', 'tag'):
            response.read.return_value = json.dumps({'object': {'type': kind, 'sha': 'a' * 40}}).encode()
            with patch.object(worker, 'urlopen', return_value=response) as request:
                self.assertIsNone(worker.resolve_tag_sha('v0.0.24'))
                self.assertLessEqual(request.call_count, 2)
