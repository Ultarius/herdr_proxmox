"""Real Git scratch allocation and ordered shell activation, without a model."""
import json
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import environment_launch
import session_workspace


class SessionWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name).resolve() / 'project with spaces'
        self.checkout.mkdir()
        self.git('init')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.checkout), *args], stderr=subprocess.DEVNULL)

    def test_scratch_is_session_specific_ignored_and_preserves_existing_files(self):
        exclude = self.checkout / '.git/info/exclude'
        exclude.write_bytes(b'# existing\nlocal-only')
        first = session_workspace.prepare(self.checkout, 'a' * 32)
        second = session_workspace.prepare(self.checkout, 'b' * 32)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_relative_to(self.checkout))
        (first / 'saved.txt').write_text('retained', encoding='utf-8')
        self.assertEqual(session_workspace.prepare(self.checkout, 'a' * 32), first)
        self.assertEqual((first / 'saved.txt').read_text(encoding='utf-8'), 'retained')
        self.assertEqual(self.git('status', '--porcelain'), b'')
        self.assertTrue(exclude.read_bytes().startswith(b'# existing\nlocal-only\n'))
        self.assertEqual(exclude.read_bytes().count(b'/.ci-cache/herdr-scratch/'), 1)
        (self.checkout / '.ci-cache/other-cache').write_text('unrelated', encoding='utf-8')
        self.assertIn(b'other-cache', self.git('status', '--porcelain', '--untracked-files=all'))

    def test_tracked_reserved_path_and_malformed_identity_are_refused(self):
        reserved = self.checkout / '.ci-cache/herdr-scratch'
        reserved.mkdir(parents=True)
        (reserved / 'owned.txt').write_text('tracked', encoding='utf-8')
        self.git('add', '.')
        with self.assertRaisesRegex(ValueError, 'tracked files'):
            session_workspace.prepare(self.checkout, 'a' * 32)
        for identity in ('../escape', 'a/b', 'x' * 32, '', None):
            with self.assertRaisesRegex(ValueError, 'execution identity'):
                session_workspace.prepare(self.checkout, identity)

    def test_read_only_git_metadata_explains_the_writable_scratch_requirement(self):
        for error in (PermissionError('Access denied'), OSError(errno.EROFS, 'Read-only filesystem')):
            with self.subTest(error=error), patch('worker_guidance.ignore_directory', side_effect=error):
                with self.assertRaisesRegex(ValueError, 'checkout and Git exclusion metadata must be writable'):
                    session_workspace.prepare(self.checkout, 'a' * 32)
            self.assertFalse((self.checkout / '.ci-cache').exists())

    def test_nested_project_scratch_does_not_ignore_other_directories(self):
        nested = self.checkout / 'part [test]'
        nested.mkdir()
        scratch = session_workspace.prepare(nested, 'a' * 32)
        (scratch / 'temporary').write_text('snapshot', encoding='utf-8')
        self.assertEqual(self.git('status', '--porcelain'), b'')
        other = self.checkout / 'part t/.ci-cache/herdr-scratch'
        other.mkdir(parents=True)
        (other / 'keep-visible').write_text('unrelated', encoding='utf-8')
        self.assertIn(b'keep-visible', self.git('status', '--porcelain', '--untracked-files=all'))

    @unittest.skipUnless(os.name != 'nt', 'Native symlinks require POSIX or Windows developer privileges.')
    def test_linked_scratch_parent_is_refused_without_writing_outside(self):
        outside = self.checkout.parent / 'outside'
        outside.mkdir()
        (self.checkout / '.ci-cache').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'regular directories'):
            session_workspace.prepare(self.checkout, 'a' * 32)
        self.assertEqual(list(outside.iterdir()), [])

    def test_non_git_directory_can_allocate_without_git_or_config_changes(self):
        checkout = self.checkout.parent / 'plain'
        checkout.mkdir()
        scratch = session_workspace.prepare(checkout, 'a' * 32, repository=False)
        self.assertTrue(scratch.is_dir())
        self.assertFalse((checkout / '.git').exists())

    def test_unbound_and_non_rust_sessions_have_generic_budgets_without_cargo_shims(self):
        for name in (None, 'python', 'node', 'dotnet-sdk'):
            with self.subTest(toolchain=name):
                scratch = self.checkout / (name or 'unbound')
                scratch.mkdir()
                run = dict(scratch_directory=str(scratch))
                if name:
                    run['toolchain_policy'] = dict(policy=dict(toolchains=[dict(name=name, required='1.2.3')]))
                environment = session_workspace.environment(scratch, run)
                self.assertIn(environment['HERDR_BUILD_JOBS'], ('1', '2'))
                self.assertEqual(environment['HERDR_TEST_THREADS'], '1')
                self.assertNotIn('CARGO_BUILD_JOBS', environment)
                self.assertNotIn('RUST_TEST_THREADS', environment)
                self.assertIsNone(session_workspace.install_wrappers(scratch, run))
                self.assertEqual(list(scratch.iterdir()), [])
                guidance = session_workspace.guidance(run) + session_workspace.tool_guidance()
                self.assertNotIn('Cargo', guidance)
                self.assertNotIn('rustup', guidance)
                self.assertIn('missing_toolchain', guidance)
                self.assertIn('wrappers on PATH when present', guidance)
                if name:
                    self.assertIn('Bound ' + name, guidance)
                else:
                    self.assertNotIn('Bound ', guidance)

    def test_rust_budget_aliases_and_wrapper_follow_the_execution_binding(self):
        scratch = self.checkout / 'scratch'
        scratch.mkdir()
        run = dict(scratch_directory=str(scratch), toolchain_policy=dict(policy=dict(toolchains=[dict(name='rust-toolchain')])))
        environment = session_workspace.environment(scratch, run)
        self.assertEqual(environment['CARGO_BUILD_JOBS'], environment['HERDR_BUILD_JOBS'])
        self.assertEqual(environment['RUST_TEST_THREADS'], environment['HERDR_TEST_THREADS'])
        self.assertIsNotNone(session_workspace.install_wrappers(scratch, run))
        self.assertTrue((scratch / 'commands/cargo').is_file())
        self.assertIn('Cargo commands use the session PATH wrapper', session_workspace.guidance(run))
        # A materialized execution policy takes precedence over the declaration.
        run['execution_toolchain_policy'] = dict(policy=dict(toolchains=[dict(name='python')]))
        other = self.checkout / 'other-scratch'
        other.mkdir()
        self.assertIsNone(session_workspace.install_wrappers(other, run))
        self.assertNotIn('CARGO_BUILD_JOBS', session_workspace.environment(other, run))
        self.assertNotIn('Cargo', session_workspace.guidance(run))

    def test_tool_instructions_follow_adapter_metadata_without_guessing_unknown_commands(self):
        from dataclasses import replace
        import environment_adapters
        adapter = replace(environment_adapters.get('node'), commands=('node', 'npm', 'npx'))
        run = dict(scratch_directory=str(self.checkout), toolchain_policy=dict(policy=dict(toolchains=[
            dict(name='node', component='client'), dict(name='other-sdk', component='server')])))
        with patch.dict(environment_adapters.ADAPTERS, node=adapter):
            guidance = session_workspace.guidance(run)
            self.assertIn('prepared commands node, npm, npx', guidance)
            self.assertNotIn('Bound other-sdk', guidance)
            context = environment_adapters.session_tools(session_workspace.toolchains(run))
        self.assertEqual(context[1]['name'], 'other-sdk')
        self.assertEqual(context[1]['commands'], [])
        self.assertNotIn('capacity_guidance', context[1])
        for entry in context:
            self.assertNotIn('capacity_wrapper', entry)

    def test_real_shell_inherits_tmpdir_without_mutating_gateway_environment(self):
        bash = shutil.which('bash')
        if os.name == 'nt':
            bash = r'C:\Program Files\Git\bin\bash.exe'
        if not bash or not Path(bash).is_file():
            self.skipTest('A real Bash shell is required.')
        scratch = session_workspace.prepare(self.checkout, 'a' * 32)
        environment = session_workspace.environment(scratch)
        before = {name: os.environ.get(name) for name in environment}
        store = Mock()
        observed = []
        def command(*args):
            self.assertEqual(args[:3], ('pane', 'run', 'w1:p1'))
            # The actual shell exports the values, a child reads them, and
            # the same command then writes the activation barrier marker.
            import shlex
            probe = shlex.quote(sys.executable.replace('\\', '/')) + ' -c ' + shlex.quote(
                'import os,json; print(json.dumps({k:os.environ.get(k) for k in '
                + repr(list(environment)) + '}))')
            exports, marker = args[3].rsplit(' && ', 1)
            result = subprocess.run([bash, '-c', exports + ' && ' + probe + ' && ' + marker],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed.append(json.loads(result.stdout))
        store.command.side_effect = command
        environment_launch.activate_shell(store, 'w1:p1', environment, scratch)
        self.assertEqual(observed, [environment])
        self.assertEqual({name: os.environ.get(name) for name in environment}, before)
        self.assertEqual(list(scratch.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
