"""The SDK install client and its root worker; no service or installer runs."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from sdk_install import SdkInstall

worker_spec = importlib.util.spec_from_file_location('sdk_install_worker', Path(__file__).parents[1] / 'install/sdk-install.py')
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class SdkClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.queue, self.flutter = root / 'queue', root / 'flutter'
        self.queue.mkdir()
        self.service = SdkInstall(self.queue, root / 'status.json', self.flutter)

    def test_installation_is_queued_once_and_visible(self):
        status = self.service.snapshot()
        self.assertEqual(status['state'], 'idle')
        self.assertTrue(status['supported'])
        self.assertFalse(status['installed'])
        self.assertEqual(self.service.install({})['state'], 'queued')
        self.assertEqual(self.service.snapshot()['state'], 'queued')
        request = json.loads((self.queue / 'request.json').read_text())
        self.assertEqual(request, {'action': 'install'})
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.service.install({})

    def test_malformed_status_is_reported_without_breaking_polling(self):
        self.service.state.write_text('{broken')
        self.assertEqual(self.service.snapshot()['state'], 'failed')

    def test_options_are_rejected_and_an_installed_sdk_is_not_reinstalled(self):
        with self.assertRaises(ValueError):
            self.service.install({'version': 'latest'})
        self.flutter.write_text('#!/bin/sh\n')
        with self.assertRaisesRegex(ValueError, 'already installed'):
            self.service.install({})

    def test_missing_service_and_failed_state_are_explicit(self):
        root = Path(self.temp.name)
        unsupported = SdkInstall(root / 'missing', root / 'status.json', self.flutter)
        with self.assertRaisesRegex(ValueError, 'root first'):
            unsupported.install({})
        (root / 'status.json').write_text(json.dumps({'state': 'failed', 'error': 'network'}))
        self.assertEqual(self.service.install({})['state'], 'queued')


class SdkWorkerTests(unittest.TestCase):
    def test_only_the_bare_install_action_is_accepted(self):
        self.assertEqual(worker.parse_request('{"action": "install"}'), {'action': 'install'})
        for text in ('[]', '{}', '{"action": "other"}', '{"action": "install", "version": "v1"}'):
            with self.assertRaises(ValueError):
                worker.parse_request(text)

    def test_worker_runs_the_release_installer_and_records_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, request, installer = root / 'state', root / 'request/request.json', root / 'dev-tools-install.sh'
            request.parent.mkdir()
            installer.write_text('echo hi')
            request.write_text('{"action": "install"}')
            with patch.object(worker, 'STATE', state), patch.object(worker, 'REQUEST', request), \
                    patch.object(worker, 'INSTALLER', installer), \
                    patch.object(worker.os, 'geteuid', return_value=0, create=True), \
                    patch.object(worker, 'install', return_value='Flutter ready') as install:
                worker.main()
            install.assert_called_once_with()
            self.assertFalse(request.exists())
            self.assertEqual(json.loads((state / 'status.json').read_text()),
                             {'state': 'complete', 'output': 'Flutter ready'})

    def test_worker_reports_failure_without_leaving_the_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, request, installer = root / 'state', root / 'request/request.json', root / 'dev-tools-install.sh'
            request.parent.mkdir()
            installer.write_text('exit 1')
            request.write_text('{"action": "install"}')
            with patch.object(worker, 'STATE', state), patch.object(worker, 'REQUEST', request), \
                    patch.object(worker, 'INSTALLER', installer), \
                    patch.object(worker.os, 'geteuid', return_value=0, create=True), \
                    patch.object(worker, 'install', side_effect=ValueError('SDK installation failed.')):
                with self.assertRaises(ValueError):
                    worker.main()
            self.assertFalse(request.exists())
            saved = json.loads((state / 'status.json').read_text())
            self.assertEqual(saved['state'], 'failed')
            self.assertIn('failed', saved['error'])

    def test_installer_failure_keeps_stderr_after_noisy_stdout(self):
        result = Mock(returncode=1, stdout='apt output ' * 1000, stderr='Pinned SDK release is unavailable')
        with patch.object(worker.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'Pinned SDK release is unavailable'):
                worker.install()


if __name__ == '__main__':
    unittest.main()
