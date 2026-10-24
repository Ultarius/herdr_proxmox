"""Deployment policy is optional operator data, independent of credential reads."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from github_api import GitHub
from publication_policy import arm, guard_path, load
if __package__:
    from . import test_updates as update_fixtures
else:
    import test_updates as update_fixtures


class DeploymentPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / 'etc/herdr/publishing-policy.json'
        self.policy.parent.mkdir(parents=True)
        self.credentials = self.root / 'github.json'
        self.token = 'ghp_' + 'x' * 40
        self.credentials.write_text(json.dumps(dict(token=self.token, repositories=['ordinary/project'],
            publication={'ordinary/project': dict(mode='direct', publishing_repository='ordinary/project')})), encoding='utf-8')
        self.credentials.chmod(0o600)
        self.api = GitHub(self.credentials, policy_path=self.policy)
        self.record = dict(upstream='SourceOwner/project', fork='ForkOwner/project',
                           mode='fork_only', arrangement='manual_adoption')

    def write_policy(self, records=None):
        self.policy.write_text(json.dumps(dict(version=1, protected=[self.record] if records is None else records)), encoding='utf-8')

    def test_missing_policy_means_no_protected_projects_and_ordinary_publication_works(self):
        self.assertEqual(load(self.policy, require_root=True), {})
        self.assertIsNone(self.api.protected_policy('ordinary/project'))
        self.assertEqual(self.api.publication_mode('ordinary/project'), 'automatic')
        self.assertTrue(self.api.snapshot()['configured'])
        self.api.allow_publishing('ordinary/project', 'ordinary/project')
        self.assertEqual(self.api.publication_options(), {'ordinary/project': ['ordinary/project']})

    def test_default_is_external_and_environment_override_does_not_use_release_example(self):
        with patch.dict(os.environ):
            os.environ.pop('HERDR_PUBLISHING_POLICY', None)
            self.assertEqual(GitHub(self.credentials).policy_path, Path('/etc/herdr/publishing-policy.json'))
        with patch.dict(os.environ, HERDR_PUBLISHING_POLICY=str(self.policy)):
            self.assertEqual(GitHub(self.credentials).policy_path, self.policy)
            self.assertEqual(GitHub(self.credentials, require_root_policy=True).policy_path, Path('/etc/herdr/publishing-policy.json'))

    def test_present_invalid_policy_keeps_credentials_reads_and_snapshot_available_but_blocks_writes(self):
        self.policy.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.api.configuration()['token'], self.token)
        with self.assertRaisesRegex(ValueError, 'present but invalid'):
            self.api.validate_policy()
        snapshot = self.api.snapshot()
        self.assertFalse(snapshot['configured'])
        self.assertTrue(snapshot['publication_disabled'])
        self.assertIn('protected_publication_error', snapshot)
        self.assertNotIn(self.token, json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.api.allow_publishing('ordinary/project', 'ordinary/project')
        network = MagicMock()
        response = network.open.return_value.__enter__.return_value
        response.read.return_value = b'{}'
        response.headers = {}
        with patch('github_api.build_opener', return_value=network):
            self.assertEqual(self.api.request('/repos/ordinary/project'), {})
            count = network.open.call_count
            with self.assertRaisesRegex(ValueError, 'policy'):
                self.api.request('/repos/ordinary/project/pulls', 'POST', {})
            self.assertEqual(network.open.call_count, count)

    def test_push_only_askpass_is_independent_of_an_invalid_operator_policy(self):
        self.policy.write_text('invalid', encoding='utf-8')
        self.credentials.write_text(json.dumps(dict(token=self.token)), encoding='utf-8')
        helper = Path(__file__).parents[1] / 'web/gateway/github_askpass.py'
        env = dict(os.environ, HERDR_GITHUB_CONFIG=str(self.credentials),
                   HERDR_REPOSITORY='ordinary/project', HERDR_PUBLISHING_POLICY=str(self.policy))
        result = subprocess.run([sys.executable, str(helper), "Password for 'https://x-access-token@github.com/ordinary/project.git': "],
                                capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), self.token)

    def test_invalid_segments_duplicate_forks_and_unsupported_arrangements_are_rejected(self):
        invalid = [[dict(self.record, upstream='../project')], [dict(self.record, fork='owner/..')],
                   [dict(self.record, fork='./project')], [dict(self.record, arrangement='automatic')],
                   [self.record, dict(self.record, upstream='AnotherOwner/project')],
                   [self.record, dict(self.record, upstream='AnotherOwner/project', fork=self.record['upstream'])]]
        for records in invalid:
            with self.subTest(records=records):
                self.write_policy(records)
                with self.assertRaisesRegex(ValueError, 'policy'):
                    load(self.policy)
        self.write_policy([])
        self.assertEqual(load(self.policy), {})

    def test_root_permission_rules_accept_0444_and_0644_but_refuse_writable_ancestors(self):
        self.write_policy()
        # Exercise ownership/mode predicates on every host. Real deployment
        # ownership and anchored filesystem checks remain Linux CI concerns.
        for mode in (0o444, 0o644):
            def metadata(path):
                return SimpleNamespace(st_uid=0, st_size=1000, st_mode=(stat.S_IFREG | mode) if path == self.policy else (stat.S_IFDIR | 0o755))
            with self.subTest(mode=mode), patch.object(Path, 'lstat', metadata):
                self.assertIn('sourceowner/project', load(self.policy, require_root=True, check_guard=False))
        for invalid in ('owner', 'mode', 'ancestor', 'symlink'):
            def metadata(path):
                is_file = path == self.policy
                kind = stat.S_IFLNK if invalid == 'symlink' and is_file else stat.S_IFREG if is_file else stat.S_IFDIR
                mode = 0o666 if invalid == 'mode' and is_file else 0o777 if invalid == 'ancestor' and not is_file else 0o644 if is_file else 0o755
                return SimpleNamespace(st_uid=1000 if invalid == 'owner' and is_file else 0, st_size=1000, st_mode=kind | mode)
            with self.subTest(invalid=invalid), patch.object(Path, 'lstat', metadata):
                with self.assertRaisesRegex(ValueError, 'policy'):
                    load(self.policy, require_root=True, check_guard=False)

    def test_unreadable_policy_is_not_treated_as_absent(self):
        self.write_policy()
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError, 'policy'):
                load(self.policy)

    def test_enabled_marker_prevents_missing_or_empty_policy_across_restarts(self):
        self.write_policy()
        arm(self.policy, require_root=False)
        self.assertEqual(guard_path(self.policy).read_text(encoding='utf-8'), 'required\n')
        for state in ('missing', 'empty'):
            with self.subTest(state=state):
                self.policy.unlink(missing_ok=True)
                if state == 'empty':
                    self.write_policy([])
                restarted = GitHub(self.credentials, policy_path=self.policy)
                with self.assertRaisesRegex(ValueError, 'was enabled'):
                    restarted.allow_publishing('ordinary/project', 'ordinary/project')
                self.assertTrue(restarted.snapshot()['publication_disabled'])
                self.assertEqual(restarted.configuration()['token'], self.token)
        # Root can deliberately reset protection by removing both policy and
        # marker, followed by a restart. A running client never forgets intent.
        guard_path(self.policy).chmod(0o600)
        guard_path(self.policy).unlink()
        self.assertEqual(load(self.policy), {})

    def test_malformed_marker_is_not_treated_as_no_protection(self):
        self.write_policy([])
        for content in ('', 'unknown', 'required=false'):
            with self.subTest(content=content):
                guard_path(self.policy).write_text(content, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'marker.*invalid'):
                    load(self.policy)
                with self.assertRaisesRegex(ValueError, 'marker.*invalid'):
                    arm(self.policy, require_root=False)

    def test_invalid_policy_cannot_create_an_enabled_marker(self):
        self.policy.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid'):
            arm(self.policy, require_root=False)
        self.assertFalse(guard_path(self.policy).exists())
        self.assertEqual(list(self.policy.parent.glob('.publishing-policy-*')), [])

    def test_running_client_does_not_forget_a_previously_protected_policy(self):
        self.write_policy()
        self.assertEqual(self.api.validate_policy()['sourceowner/project'], self.record)
        self.policy.unlink()
        with self.assertRaisesRegex(ValueError, 'Previously enabled'):
            self.api.validate_policy()

    def test_root_protected_policy_requires_explicit_provisioning(self):
        self.write_policy()
        def metadata(path):
            if path == guard_path(self.policy):
                raise FileNotFoundError()
            return SimpleNamespace(st_uid=0, st_size=1000, st_mode=(stat.S_IFREG | 0o644) if path == self.policy else (stat.S_IFDIR | 0o755))
        with patch.object(Path, 'lstat', metadata):
            with self.assertRaisesRegex(ValueError, 'enabled by root'):
                load(self.policy, require_root=True)

    def test_snapshot_uses_one_consistent_policy_mapping(self):
        self.credentials.write_text(json.dumps(dict(token='github_pat_'+'x'*40, pr_token='github_pat_'+'y'*40,
            pr_login='source-owner', repositories=[self.record['upstream']],
            publication={self.record['upstream']: dict(mode='fork', publishing_repository=self.record['fork'])})), encoding='utf-8')
        with patch.object(self.api, 'validate_policy', side_effect=[{'sourceowner/project':self.record}, {}]) as reads:
            snapshot = self.api.snapshot()
        reads.assert_called_once()
        self.assertEqual(snapshot['publication'][self.record['upstream']]['arrangement'], 'manual_adoption')
        self.assertEqual(snapshot['protected_publication_state'], 'enabled')

    def test_fresh_snapshot_distinguishes_absent_and_deliberately_empty_policy(self):
        self.assertEqual(self.api.snapshot()['protected_publication_state'], 'absent')
        self.write_policy([])
        self.assertEqual(self.api.snapshot()['protected_publication_state'], 'empty')

    def test_validator_imports_without_gateway_application_dependencies(self):
        source = Path(__file__).parents[1] / 'web/gateway/publication_policy.py'
        script = "import runpy,sys; runpy.run_path(sys.argv[1]); assert not any(name in sys.modules for name in ('project_requirements','environment_capabilities','github_api'))"
        result = subprocess.run([sys.executable, '-I', '-c', script, str(source)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_update_requires_legacy_mapping_migration_and_enabled_external_policy(self):
        installed = self.root / 'installed'
        legacy = installed / 'web/gateway/publishing-policy.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(dict(version=1, protected=[self.record])), encoding='utf-8')
        worker = update_fixtures.worker
        with patch.object(worker, 'ROOT', installed), patch.object(worker, 'PUBLISHING_POLICY', self.policy):
            with self.assertRaisesRegex(ValueError, 'must be migrated'):
                worker.guard_publishing_policy_migration(require_root=False)
            self.write_policy([])
            with self.assertRaisesRegex(ValueError, 'must be migrated'):
                worker.guard_publishing_policy_migration(require_root=False)
            self.write_policy([dict(self.record, fork='DifferentOwner/project')])
            with self.assertRaisesRegex(ValueError, 'must be migrated'):
                worker.guard_publishing_policy_migration(require_root=False)
            self.write_policy()
            with self.assertRaisesRegex(ValueError, 'root provisioning'):
                worker.guard_publishing_policy_migration(require_root=False)
            arm(self.policy, require_root=False)
            worker.guard_publishing_policy_migration(require_root=False)
            self.assertEqual(load(self.policy)['sourceowner/project'], self.record)

    def test_update_preflight_refuses_before_downloading_or_stopping_gateway(self):
        worker = update_fixtures.worker
        request = self.root / 'request.json'
        request.write_text('{}', encoding='utf-8')
        with patch.object(worker, 'ROOT', self.root / 'installed'), patch.object(worker, 'STATE', self.root / 'state'), \
                patch.object(worker, 'REQUEST', request), patch.object(worker.os, 'geteuid', return_value=0, create=True), \
                patch.object(worker, 'read_request', return_value={'mode':'release','version':'v0.1.0'}), \
                patch.object(worker, 'guard_publishing_policy_migration', side_effect=ValueError('Publishing policy must be migrated.')), \
                patch.object(worker, 'service') as service, patch.object(worker, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'must be migrated'):
                worker.main()
        service.assert_not_called()
        download.assert_not_called()

    def test_replacing_release_tree_preserves_external_operator_policy(self):
        self.write_policy()
        original = self.policy.read_bytes()
        source, installed = self.root / 'source', self.root / 'installed'
        for base in (source, installed):
            (base / 'web/gateway').mkdir(parents=True)
            (base / 'web/public').mkdir()
        (source / 'web/gateway/publishing-policy.json').write_text('{"version":1,"protected":[]}', encoding='utf-8')
        (source / 'VERSION').write_text('v0.0.1', encoding='utf-8')
        worker = update_fixtures.worker
        with patch.object(worker, 'ROOT', installed), patch.object(worker.os, 'chown', create=True):
            worker.replace_install(source, {'build_id':'test'})
        self.assertEqual(self.policy.read_bytes(), original)
        self.assertEqual(load(self.policy)['sourceowner/project']['fork'], 'ForkOwner/project')


if __name__ == '__main__':
    unittest.main()
