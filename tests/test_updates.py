import importlib.util
import json
import hashlib
import shutil
import tarfile
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import ExitStack

spec = importlib.util.spec_from_file_location('updates', Path(__file__).parents[1] / 'web/gateway/updates.py')
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)
worker_spec = importlib.util.spec_from_file_location('update_worker', Path(__file__).parents[1] / 'install/dashboard-update.py')
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class UpdateTests(unittest.TestCase):
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
