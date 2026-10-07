from pathlib import Path
import json
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import build_identity


class BuildIdentityTests(unittest.TestCase):
    def test_legacy_version_is_exposed_without_inventing_source_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'VERSION').write_text('v0.0.23')
            data = build_identity.snapshot(root)
            self.assertEqual(data['build_id'], 'v0.0.23')
            self.assertIsNone(data['source_sha'])

    def test_identity_is_deployment_metadata_not_repository_head(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(build_identity.snapshot(root)['source_sha'])
            for invalid in ('[]', 'null', '"invalid"'):
                (root / 'BUILD.json').write_text(invalid)
                self.assertIsNone(build_identity.snapshot(root)['build_id'])
            (root / 'VERSION').write_text('v0.0.20')
            (root / 'BUILD.json').write_text(json.dumps(dict(build_id='local-123', source_sha='a' * 40, deployment_mode='local')))
            data = build_identity.snapshot(root)
            self.assertEqual(data['source_sha'], 'a' * 40)
            self.assertEqual(data['build_id'], 'local-123')
            self.assertEqual(data['package_version'], 'v0.0.20')
            self.assertEqual(data['deployment_mode'], 'local')
            (root / 'BUILD.json').write_text('{"source_sha":"../invalid","build_id":[] }')
            self.assertIsNone(build_identity.snapshot(root)['build_id'])
            self.assertIsNone(build_identity.snapshot(root)['source_sha'])
