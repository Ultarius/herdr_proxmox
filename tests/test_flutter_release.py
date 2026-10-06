import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('flutter_release', Path(__file__).parents[1] / 'install/flutter-release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FlutterReleaseTests(unittest.TestCase):
    def release(self, **changes):
        return dict(dict(version='3.44.8', channel='stable', dart_sdk_arch='x64',
                         archive='stable/linux/flutter_linux_3.44.8-stable.tar.xz', sha256='a' * 64), **changes)

    def test_exact_pinned_version_and_architecture(self):
        manifest = {'releases': [self.release(version='3.45.0'), self.release(dart_sdk_arch='arm64'), self.release()]}
        archive, checksum = module.select_release(manifest, '3.44.8', 'x64')
        self.assertIn('3.44.8', archive)
        self.assertEqual(checksum, 'a' * 64)
        with self.assertRaises(ValueError):
            module.select_release(manifest, 'missing', 'x64')

    def test_untrusted_path_or_missing_checksum_is_rejected(self):
        for changes in ({'archive': '../escape.tar.xz'}, {'archive': 'https://other/sdk.tar.xz'}, {'sha256': ''}):
            with self.assertRaises(ValueError):
                module.select_release({'releases': [self.release(**changes)]}, '3.44.8', 'x64')

    def test_installer_pin_matches_project_build_guard(self):
        root = Path(__file__).parents[1]
        installer = (root / 'install/dev-tools-install.sh').read_text()
        build = (root / 'scripts/build-web.sh').read_text()
        self.assertIn('version=3.44.8', installer)
        self.assertIn('v=="3.44.8"', build)
        self.assertLess(installer.index('sha256sum --check'), installer.index('tar --no-same-owner'))
