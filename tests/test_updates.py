import importlib.util
import json
import hashlib
import shutil
import tarfile
from pathlib import Path
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
            service = mocks.enter_context(patch.object(worker, 'service'))
            def backup():
                service.assert_called_with('stop')
                return safety
            mocks.enter_context(patch.object(worker, 'backup_current', side_effect=backup))
            restored = mocks.enter_context(patch.object(worker, 'restore'))
            mocks.enter_context(patch.object(worker, 'wait_for_ready', side_effect=ValueError('unhealthy')))
            with self.assertRaisesRegex(ValueError, 'unhealthy') as failure:
                worker.rollback()
            self.assertEqual(restored.call_args_list, [call(old), call(safety)])
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
                self.assertEqual(service.snapshot(force=True)['latest'], 'v0.0.2')
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
            config.mkdir()
            (config / 'organizations.sqlite3').write_bytes(b'original database')
            request.write_text('{"version":"v0.2.0"}')
            package = base / 'herdr-proxmox'
            for name in ('web/gateway/server.py', 'web/public/index.html', 'web/public/main.dart.js', 'web/public/dashboard/index.html'):
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('')
            (package / 'install').mkdir()
            (package / 'install/web-install.sh').write_text('new installer')
            (package / 'install/sdk-install.py').write_text('new sdk worker')
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
            mocks.enter_context(patch.object(worker, 'download', side_effect=download))
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            def inspect_service(action):
                if action == 'start' and (root / 'VERSION').read_text() == 'v0.2.0':
                    self.assertEqual((root / 'install/web-install.sh').read_text(), 'new installer')
                    self.assertTrue((root / 'install/sdk-install.py').exists())
            service = mocks.enter_context(patch.object(worker, 'service', side_effect=inspect_service))
            mocks.enter_context(patch.object(worker, 'urlopen', side_effect=OSError('unavailable')))
            mocks.enter_context(patch.object(worker.time, 'sleep'))
            with self.assertRaisesRegex(ValueError, 'startup check'):
                worker.main()
            self.assertEqual((root / 'install/web-install.sh').read_text(), 'old installer')
            self.assertFalse((root / 'install/sdk-install.py').exists())
            self.assertEqual((root / 'VERSION').read_text(), 'v0.1.0')
            self.assertEqual((root / 'web/gateway/server.py').read_text(), 'old gateway')
            self.assertEqual((config / 'organizations.sqlite3').read_bytes(), b'original database')
            self.assertTrue(json.loads((state / 'status.json').read_text())['rolled_back'])
            self.assertEqual([call.args[0] for call in service.call_args_list], ['stop', 'start', 'stop', 'start'])

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
            mocks.enter_context(patch.object(worker.subprocess, 'run'))
            mocks.enter_context(patch.object(worker, 'service'))
            ready = mocks.enter_context(patch.object(worker, 'wait_for_ready'))
            worker.main()
            ready.assert_called_once_with(run_id)
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
