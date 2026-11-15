import importlib.util
import json
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location('flutter_release', Path(__file__).parents[1] / 'install/flutter-release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FlutterReleaseTests(unittest.TestCase):
    def release(self, **changes):
        return dict(dict(version='3.47.9', channel='stable', dart_sdk_arch='x64',
                         archive='stable/linux/flutter_linux_3.47.9-stable.tar.xz', sha256='a' * 64), **changes)

    def test_exact_pinned_version_and_architecture(self):
        manifest = {'releases': [self.release(version='3.50.0'), self.release(dart_sdk_arch='arm64'), self.release()]}
        archive, checksum = module.select_release(manifest, '3.47.9', 'x64')
        self.assertIn('3.47.9', archive)
        self.assertEqual(checksum, 'a' * 64)
        with self.assertRaises(ValueError):
            module.select_release(manifest, 'missing', 'x64')

    def test_untrusted_path_or_missing_checksum_is_rejected(self):
        for changes in ({'archive': '../escape.tar.xz'}, {'archive': 'https://other/sdk.tar.xz'}, {'sha256': ''}):
            with self.assertRaises(ValueError):
                module.select_release({'releases': [self.release(**changes)]}, '3.47.9', 'x64')

    def test_installer_reads_the_declared_version_instead_of_its_own_pin(self):
        root = Path(__file__).parents[1]
        installer = (root / 'install/dev-tools-install.sh').read_text()
        build = (root / 'scripts/build-web.sh').read_text()
        # One pin, in .fvmrc. A literal in either script is a second copy that
        # goes stale the moment the project moves.
        for name, text in (('installer', installer), ('build', build)):
            self.assertIn('.fvmrc', text, name)
            self.assertNotRegex(text, r'3\.\d+\.\d+', name)
        self.assertLess(installer.index('sha256sum --check'), installer.index('tar --no-same-owner'))

    def test_every_site_derives_the_pin_from_the_project_declaration(self):
        # .fvmrc is the declared source of truth. Everything that needs the
        # version must read it: the installer, the build guard, CI, and the
        # catalog. Nothing may state a version of its own.
        root = Path(__file__).parents[1]
        pinned = '3.47.9'
        self.assertEqual(json.loads((root / '.fvmrc').read_text())['flutter'], pinned)
        ci = (root / '.github/workflows/ci.yml').read_text()
        self.assertIn('.fvmrc', ci)
        self.assertNotRegex(ci, r'3\.\d+\.\d+')
        queue = (root / 'web/gateway/build_queue.py').read_text()
        self.assertNotIn('flutter-', queue)
        catalog = json.loads((root / 'install/toolchains.json').read_text())
        entry = catalog['flutter']
        # The catalog locates and installs; it does not carry the version.
        self.assertEqual(entry['version_source'], {'file': '.fvmrc', 'key': 'flutter'})
        self.assertNotIn('version', entry)
        self.assertIn('{version}', entry['install_root'])
        self.assertIn('{version}', entry['executable'])
        self.assertEqual(entry['script'], 'install/dev-tools-install.sh')
        self.assertTrue((root / entry['script']).is_file())
        for name, value in catalog.items():
            self.assertFalse(value['script'].startswith('/'), name)
            self.assertNotIn('..', value['script'], name)
            self.assertIn('version_source', value, name)
        # The declared version must be resolvable through the catalog the same
        # way the gateway resolves it.
        sys.path.insert(0, str(root / 'web/gateway'))
        import toolchain_policy
        resolved, declared_by = toolchain_policy.declared_version(entry, root)
        self.assertEqual((resolved, declared_by), (pinned, '.fvmrc#flutter'))
        self.assertEqual(toolchain_policy.expand(entry, 'executable', resolved),
                         f'/home/herdr/.local/share/flutter-{pinned}/bin/flutter')

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
