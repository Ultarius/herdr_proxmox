"""Real worktree identities and independent, lock-verified archive reuse."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import environment_adapters
import package_cache


class PackageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / 'project'
        self.checkout.mkdir()
        self.git('init')
        self.git('config', 'user.name', 'Cache test')
        self.git('config', 'user.email', 'cache@example.invalid')
        (self.checkout / 'README.md').write_text('fixture', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'fixture')
        self.shared = self.root / 'shared'
        self.execution = self.root / 'execution'
        self.archive = b'compressed crate fixture, never extracted by the cache worker'
        self.checksum = sha256(self.archive).hexdigest()
        self.lock()

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.checkout), *arguments], stderr=subprocess.DEVNULL)

    def lock(self, *, checksum=None, source='registry+https://github.com/rust-lang/crates.io-index'):
        (self.checkout / 'Cargo.lock').write_text(
            'version = 3\n[[package]]\nname = "fixture"\nversion = "1.2.3"\n'
            f'source = "{source}"\nchecksum = "{checksum or self.checksum}"\n', encoding='utf-8')

    def configure(self, hook='cargo', *, checkout=None, execution=None, lane='agent', create=True,
                  component='.', name='fixture-sdk'):
        # A fixture capability proves the generic configuration is adapter-owned.
        adapter = SimpleNamespace(download_cache='package_cache:' + hook)
        with patch.dict(environment_adapters.ADAPTERS, {name: adapter}):
            return package_cache.configure([dict(name=name, component=component)],
                execution or self.execution, checkout or self.checkout, lane=lane,
                shared_root=self.shared, create=create)

    def archive_path(self, configuration):
        home = Path(configuration['environment']['CARGO_HOME'])
        path = home / 'registry' / 'cache' / 'registry.example-0123456789abcdef' / 'fixture-1.2.3.crate'
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_worktrees_reuse_a_repository_scope_but_another_repository_does_not(self):
        linked = self.root / 'linked-worktree'
        self.git('worktree', 'add', '-b', 'linked', str(linked))
        first = self.configure('npm')
        second = self.configure('npm', checkout=linked, execution=self.root / 'execution-2')
        self.assertEqual(first['manifest']['repository'], second['manifest']['repository'])
        self.assertNotEqual(first['environment'], second['environment'])
        other = self.root / 'other'
        other.mkdir()
        subprocess.check_call(['git', '-C', str(other), 'init'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        third = self.configure('npm', checkout=other)
        self.assertNotEqual(first['manifest']['repository'], third['manifest']['repository'])

    def test_agent_and_validation_downloads_have_separate_scopes(self):
        agent = self.configure('npm')
        validation = self.configure('npm', lane='validation', execution=self.root / 'validation')
        self.assertNotEqual(agent['environment']['npm_config_cache'], validation['environment']['npm_config_cache'])
        self.assertIn('installed dependency trees', agent['guidance'][0])
        self.assertIn('no dependency restore or installation permission', agent['guidance'][0])
        with self.assertRaisesRegex(ValueError, 'execution lane'):
            self.configure(lane='untrusted-lane')

    def test_preview_does_not_create_cache_directories(self):
        configuration = self.configure(create=False)
        self.assertEqual(Path(configuration['environment']['CARGO_HOME']), self.execution / 'cargo')
        self.assertFalse(self.execution.exists())
        self.assertFalse(self.shared.exists())

    def test_only_selected_adapter_hooks_supply_cache_settings(self):
        configuration = package_cache.configure([dict(name='unknown')], self.execution,
            self.checkout, lane='agent', shared_root=self.shared, create=True)
        self.assertEqual(configuration['environment'], {})
        self.assertEqual(configuration['manifest']['caches'], [])
        self.assertFalse(self.shared.exists())
        self.assertFalse(self.execution.exists())

    def test_python_cache_contract_supplies_explicit_pip_arguments(self):
        configuration = self.configure('python')
        environment = configuration['environment']
        self.assertEqual(package_cache.pip_arguments(configuration['manifest']),
                         ['--cache-dir', environment['PIP_CACHE_DIR']])
        self.assertNotEqual(environment['PIP_CACHE_DIR'], environment['UV_CACHE_DIR'])
        self.assertTrue(Path(environment['PIP_CACHE_DIR']).is_dir())
        self.assertEqual(environment['UV_LINK_MODE'], 'copy')
        self.assertNotIn('VIRTUAL_ENV', environment)

    def test_archives_are_lock_verified_published_and_copied_without_shared_inodes(self):
        first = self.configure()
        source = self.archive_path(first)
        source.write_bytes(self.archive)
        private = Path(first['environment']['CARGO_HOME'])
        (private / 'config.toml').write_text('private configuration', encoding='utf-8')
        (private / 'credentials.toml').write_text('private credentials', encoding='utf-8')
        unpacked = private / 'registry' / 'src' / 'registry.example-0123456789abcdef' / 'fixture-1.2.3'
        unpacked.mkdir(parents=True)
        (unpacked / 'lib.rs').write_text('private unpacked source', encoding='utf-8')
        published = package_cache.harvest(first['manifest'], self.checkout)
        self.assertEqual(published, dict(files=1, bytes=len(self.archive), errors=[]))
        pooled = list(self.shared.rglob('*.crate'))
        self.assertEqual(len(pooled), 1)
        self.assertEqual(pooled[0].read_bytes(), self.archive)
        self.assertFalse(list(self.shared.rglob('config.toml')))
        self.assertFalse(list(self.shared.rglob('credentials.toml')))
        self.assertFalse(list(self.shared.rglob('lib.rs')))
        second = self.configure(execution=self.root / 'second-execution')
        seeded = package_cache.seed(second['manifest'], self.checkout)
        self.assertEqual(seeded, dict(files=1, bytes=len(self.archive), errors=[]))
        destination = self.archive_path(second)
        self.assertEqual(destination.read_bytes(), self.archive)
        self.assertFalse(os.path.samefile(source, pooled[0]))
        self.assertFalse(os.path.samefile(destination, pooled[0]))
        destination.write_bytes(b'mutated private cache')
        self.assertEqual(pooled[0].read_bytes(), self.archive)
        self.assertEqual(package_cache.seed(second['manifest'], self.checkout)['files'], 1)

    def test_corrupt_private_archive_never_enters_shared_pool(self):
        configuration = self.configure()
        self.archive_path(configuration).write_bytes(b'wrong archive')
        result = package_cache.harvest(configuration['manifest'], self.checkout)
        self.assertEqual(result['files'], 0)
        self.assertIn('does not match Cargo.lock', result['errors'][0])
        self.assertEqual(list(self.shared.rglob('*.crate')), [])

    def test_corrupt_shared_archive_is_not_seeded(self):
        first = self.configure()
        self.archive_path(first).write_bytes(self.archive)
        package_cache.harvest(first['manifest'], self.checkout)
        next(self.shared.rglob('*.crate')).write_bytes(b'corrupt pool')
        second = self.configure(execution=self.root / 'second-execution')
        result = package_cache.seed(second['manifest'], self.checkout)
        self.assertEqual(result['files'], 0)
        self.assertIn('does not match Cargo.lock', result['errors'][0])
        self.assertEqual(list(Path(second['environment']['CARGO_HOME']).rglob('*.crate')), [])

    def test_checksum_and_registry_source_changes_do_not_reuse_old_archives(self):
        first = self.configure()
        self.archive_path(first).write_bytes(self.archive)
        package_cache.harvest(first['manifest'], self.checkout)
        second = self.configure(execution=self.root / 'second-execution')
        self.lock(checksum='a' * 64)
        self.assertEqual(package_cache.seed(second['manifest'], self.checkout), dict(files=0, bytes=0, errors=[]))
        self.lock(source='registry+https://different.example/index')
        self.assertEqual(package_cache.seed(second['manifest'], self.checkout), dict(files=0, bytes=0, errors=[]))

    def test_workspace_lockfile_is_found_above_a_component(self):
        (self.checkout / 'app').mkdir()
        configuration = self.configure(component='app')
        self.archive_path(configuration).write_bytes(self.archive)
        self.assertEqual(package_cache.harvest(configuration['manifest'], self.checkout)['files'], 1)

    def test_unlocked_or_git_dependencies_are_not_published(self):
        configuration = self.configure()
        self.archive_path(configuration).write_bytes(self.archive)
        self.lock(source='git+https://example.invalid/source')
        self.assertEqual(package_cache.harvest(configuration['manifest'], self.checkout)['files'], 0)
        (self.checkout / 'Cargo.lock').unlink()
        self.assertEqual(package_cache.harvest(configuration['manifest'], self.checkout)['files'], 0)

    def test_transfer_budget_limits_new_cache_bytes(self):
        configuration = self.configure()
        self.archive_path(configuration).write_bytes(self.archive)
        result = package_cache.harvest(configuration['manifest'], self.checkout, max_bytes=len(self.archive) - 1)
        self.assertEqual(result, dict(files=0, bytes=0, errors=[]))
        self.assertEqual(list(self.shared.rglob('*.crate')), [])

    def test_other_repository_cannot_adopt_a_saved_manifest(self):
        configuration = self.configure()
        other = self.root / 'other'
        other.mkdir()
        subprocess.check_call(['git', '-C', str(other), 'init'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = package_cache.seed(configuration['manifest'], other)
        self.assertIn('another repository', result['errors'][0])

    def test_managed_directories_and_private_archives_reject_symlinks(self):
        linked = self.root / 'linked-cache'
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('This host does not allow creating symlinks.')
        with self.assertRaisesRegex(ValueError, 'without links'):
            package_cache.configure([], self.execution, self.checkout, lane='agent', shared_root=linked)
        configuration = self.configure()
        source = self.archive_path(configuration)
        payload = outside / 'payload'
        payload.write_bytes(self.archive)
        source.symlink_to(payload)
        result = package_cache.harvest(configuration['manifest'], self.checkout)
        self.assertEqual(result['files'], 0)
        self.assertTrue(result['errors'])
        self.assertEqual(list(self.shared.rglob('*.crate')), [])

    def test_shared_cache_cannot_be_a_project_tree_or_component_escape(self):
        with self.assertRaisesRegex(ValueError, 'outside project checkouts'):
            package_cache.configure([], self.execution, self.checkout, lane='agent', shared_root=self.checkout / 'cache')
        with self.assertRaisesRegex(ValueError, 'stay within its checkout'):
            self.configure(component='../outside')
        with self.assertRaisesRegex(ValueError, 'must be separate'):
            self.configure(execution=self.shared / 'execution')

    def test_malformed_lockfile_does_not_publish_any_archive(self):
        configuration = self.configure()
        self.archive_path(configuration).write_bytes(self.archive)
        (self.checkout / 'Cargo.lock').write_text('package = ["invalid entry"]', encoding='utf-8')
        result = package_cache.harvest(configuration['manifest'], self.checkout)
        self.assertEqual(result['files'], 0)
        self.assertIn('unsupported package entry', result['errors'][0])
        self.assertEqual(list(self.shared.rglob('*.crate')), [])

    def test_new_cache_directories_are_owner_only_on_posix(self):
        if os.name != 'posix':
            self.skipTest('POSIX mode bits are not enforced on this platform.')
        configuration = self.configure()
        for directory in (self.shared, self.execution, Path(configuration['environment']['CARGO_HOME'])):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
