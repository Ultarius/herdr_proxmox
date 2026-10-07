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

    def test_the_product_build_requires_only_its_own_pinned_toolchain(self):
        # Tools an agent may install for its own task. The product must never make
        # one of them a build or provisioning requirement: the gateway validates
        # every worker merge by running the repository's own scripts, so a required
        # task tool would stall the integration loop whenever it is not installed.
        task_tools = ('actionlint', 'shellcheck', 'golangci')
        product_toolchain = ('scripts/build-web.sh', 'install/dev-tools-install.sh',
                             'web/gateway/sdk_install.py', 'web/gateway/validation.py')

        root = Path(__file__).parents[1]
        for name in product_toolchain:
            path = root / name
            self.assertTrue(path.is_file(), name)
            # Comments explain the rule and must not trip it; only real code counts.
            code = [line for line in path.read_text().splitlines()
                    if not line.lstrip().startswith('#')]
            for line in code:
                for tool in task_tools:
                    self.assertNotRegex(line, r'\b' + tool + r'\b',
                                        f'{name} must not require a task tool: {line.strip()}')

    def test_installer_uses_release_bucket_not_engine_artifact_prefix(self):
        installer = (Path(__file__).parents[1] / 'install/dev-tools-install.sh').read_text()
        self.assertIn('origin=https://storage.googleapis.com/flutter_infra_release/releases\n', installer)
        self.assertNotIn('flutter_infra_release/flutter/releases', installer)
        self.assertIn('$origin/releases_linux.json', installer)
        self.assertIn('$origin/${release[0]}', installer)

    def test_flutter_initialization_does_not_inherit_root_environment_or_cwd(self):
        installer = (Path(__file__).parents[1] / 'install/dev-tools-install.sh').read_text()
        self.assertIn('runuser -u herdr -- env -i HOME=/home/herdr USER=herdr LOGNAME=herdr', installer)
        self.assertLess(installer.index('cd /home/herdr'), installer.index('flutter_as_herdr config'))
        self.assertIn('flutter_as_herdr --version --machine', installer)
        self.assertNotIn('runuser -u herdr -- env HOME=', installer)
