"""Boot the real store/watcher/coordinator graph without binding a live port."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import server


class GatewayBootTests(unittest.TestCase):
    def test_boot_with_and_without_build_service(self):
        for enabled in (False, True):
            with self.subTest(service=enabled), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                projects = root / 'projects'
                projects.mkdir()
                token = root / 'token'
                token.write_text('a' * 64)
                config = root / 'build-service.json'
                if enabled:
                    config.write_text(json.dumps(dict(projects=str(projects), queue=str(root / 'queue'))))
                def inspect_graph(bind, port, policy, secret, services, **kwargs):
                    self.assertIs(services['integration'].coordinator, services['coordinator'])
                    self.assertEqual(services['integration'].build_results, services['validation'].snapshot)
                    self.assertEqual(services['validation'].queue is not None, enabled)
                    self.assertEqual(services['validation'].snapshot()['executor'], 'service' if enabled else 'gateway')
                    # Task publication and task builds must share the real graph.
                    self.assertIs(services['contributions'].validation, services['validation'])
                    self.assertIs(services['contributions'].github, services['github'])
                    self.assertIs(services['contributions'].store, services['organizations'])
                    self.assertEqual(services['contributions'].snapshot()['tasks'], [])
                    services['integration'].schedule_builds()
                    self.assertEqual(services['coordinator'].snapshot()['events'], [])
                with patch.multiple(server, PROJECTS=projects, TOKEN_FILE=token,
                                    DATABASE=root / 'organizations.sqlite3', BUILD_SERVICE_CONFIG=config), \
                        patch.object(server, 'serve_gateway', side_effect=inspect_graph) as serve, \
                        patch.object(server.IntegrationWatcher, '_loop', return_value=None):
                    server.main()
                serve.assert_called_once()
