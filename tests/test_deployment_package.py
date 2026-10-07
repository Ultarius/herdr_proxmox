import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import deployment_package


class DeploymentPackageTests(unittest.TestCase):
    def test_matched_package_keeps_identity_and_never_grants_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in deployment_package.REQUIRED:
                path = root / 'tree' / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('built')
            result = deployment_package.retain(root / 'tree', root, {'id': 'a'*32, 'target': 'b'*40, 'exit_code': 0})
            self.assertFalse(result['deployment_authorized'])
            self.assertFalse(result['required_checks_verified'])
            with tarfile.open(root / 'deployment.tar.gz') as archive:
                identity = json.load(archive.extractfile('herdr-proxmox/BUILD.json'))
                self.assertEqual(identity['source_sha'], 'b'*40)
                for relative in deployment_package.REQUIRED:
                    self.assertIn('herdr-proxmox/' + relative, archive.getnames())
            with patch.object(deployment_package, 'LIMIT', 1):
                with self.assertRaisesRegex(ValueError, 'limit'):
                    deployment_package.retain(root / 'tree', root, {'id': 'x', 'target': 'y'})

    def test_static_output_is_insufficient_for_full_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                deployment_package.retain(Path(directory), Path(directory), {})
