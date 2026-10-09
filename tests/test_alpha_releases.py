import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from test_updates import updates, worker

spec = importlib.util.spec_from_file_location('alpha_release', Path(__file__).parents[1] / 'scripts/alpha-release.py')
alpha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alpha)


class AlphaTests(unittest.TestCase):
    def test_trigger_is_exact_and_branch_identity_does_not_collide(self):
        for branch, subject in [('main', 'alpha: test'), ('alpha/', 'alpha: test'),
                                ('alpha/test', 'fix: alpha: test'), ('alpha/test', 'Alpha: test')]:
            with self.assertRaises(ValueError):
                alpha.alpha_tag(branch, subject, 42)
        first = alpha.alpha_tag('alpha/foo/bar', 'alpha: test', 42)
        second = alpha.alpha_tag('alpha/foo-bar', 'alpha: test', 42)
        self.assertNotEqual(first, second)
        self.assertTrue(updates.ALPHA.fullmatch(first))
        self.assertEqual(first, alpha.alpha_tag('alpha/foo/bar', 'alpha: test', 42))
        with self.assertRaises(ValueError):
            alpha.alpha_tag('alpha/foo', 'alpha: test', '../42')

    def test_catalog_separates_stable_draft_and_incomplete_alphas(self):
        tag = alpha.alpha_tag('alpha/login', 'alpha: login', 42)
        release = dict(tag_name=tag, prerelease=True, draft=False, published_at='2026-10-09',
                       assets=[{'name': name} for name in updates.ASSETS])
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps([
            release, dict(release, draft=True), dict(release, tag_name='v1.0.0', prerelease=False),
            dict(release, tag_name='alpha-other', assets=[]), dict(release, prerelease=False)
        ]).encode()
        with patch.object(updates, 'urlopen', return_value=response):
            self.assertEqual(updates.alpha_releases(), [release])
            self.assertEqual(updates.latest_release()['tag_name'], 'v1.0.0')

    def test_alpha_queue_requires_confirmation_and_available_exact_tag(self):
        tag = alpha.alpha_tag('alpha/login', 'alpha: login', 42)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / 'queue'
            queue.mkdir()
            service = updates.Updates(root, queue, root / 'status.json')
            body = dict(channel='alpha', version=tag)
            with self.assertRaisesRegex(ValueError, 'confirmation'):
                service.install(body)
            body['confirm_alpha'] = True
            with patch.object(updates, 'alpha_releases', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'no longer'):
                    service.install(body)
            with patch.object(updates, 'alpha_releases', return_value=[{'tag_name': tag}]):
                service.install(body)
                self.assertEqual(json.loads((queue / 'request.json').read_text()),
                                 dict(mode='alpha', version=tag))
                with self.assertRaisesRegex(ValueError, 'already running'):
                    service.install(body)
                (queue / 'request.json').unlink()
                (root / 'BUILD.json').write_text(json.dumps({'deployment_mode': 'local'}))
                with self.assertRaisesRegex(ValueError, 'local deployment'):
                    service.install(body)

    def test_worker_rejects_alpha_through_stable_mode_and_requires_metadata(self):
        tag = alpha.alpha_tag('alpha/login', 'alpha: login', 42)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'request.json'
            with patch.object(worker, 'REQUEST', request):
                request.write_text(json.dumps({'version': tag}))
                with self.assertRaises(ValueError):
                    worker.read_request()
                request.write_text(json.dumps(dict(mode='alpha', version=tag)))
                self.assertEqual(worker.read_request()['mode'], 'alpha')
            with self.assertRaisesRegex(ValueError, 'require source identity'):
                worker.release_identity(root, tag)
            identity = dict(build_id=tag, source_sha='a' * 40, deployment_mode='release',
                            channel='alpha', source_branch='alpha/login')
            (root / 'BUILD.json').write_text(json.dumps(identity))
            self.assertEqual(worker.release_identity(root, tag)['source_branch'], 'alpha/login')
            identity['channel'] = 'stable'
            (root / 'BUILD.json').write_text(json.dumps(identity))
            with self.assertRaisesRegex(ValueError, 'channel'):
                worker.release_identity(root, tag)

    def test_stable_can_replace_an_installed_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'VERSION').write_text(alpha.alpha_tag('alpha/login', 'alpha: login', 42))
            service = updates.Updates(root, root, root / 'status.json')
            with patch.object(updates, 'latest_release', return_value=dict(tag_name='v1.0.0', html_url='')):
                self.assertTrue(service.snapshot()['available'])

    def test_alpha_source_mismatch_fails_before_service_stop(self):
        tag = alpha.alpha_tag('alpha/login', 'alpha: login', 42)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed, staging, state = root / 'installed', root / 'staging', root / 'state'
            installed.mkdir()
            staging.mkdir()
            state.mkdir()
            (staging / 'VERSION').write_text(tag)
            (staging / 'BUILD.json').write_text(json.dumps(dict(
                build_id=tag, source_sha='a' * 40, deployment_mode='release',
                channel='alpha', source_branch='alpha/login')))
            request = root / 'request.json'
            request.write_text(json.dumps(dict(mode='alpha', version=tag)))
            def download(url, target, limit):
                target.write_text('b' * 64 + '  herdr-proxmox.tar.gz' if url.endswith('.sha256') else 'archive')
            from contextlib import ExitStack
            with ExitStack() as mocks:
                for name, value in [('ROOT', installed), ('STATE', state), ('REQUEST', request)]:
                    mocks.enter_context(patch.object(worker, name, value))
                mocks.enter_context(patch.object(worker.os, 'geteuid', return_value=0, create=True))
                mocks.enter_context(patch.object(worker, 'download', side_effect=download))
                mocks.enter_context(patch.object(worker, 'sha256', return_value='b' * 64))
                mocks.enter_context(patch.object(worker, 'extract', return_value=staging))
                mocks.enter_context(patch.object(worker, 'resolve_tag_sha', return_value='c' * 40))
                service = mocks.enter_context(patch.object(worker, 'service'))
                with self.assertRaisesRegex(ValueError, 'immutable tag'):
                    worker.main()
                service.assert_not_called()
                self.assertEqual(json.loads((state / 'status.json').read_text())['state'], 'failed')
