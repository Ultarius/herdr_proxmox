import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import model_catalog
import server as gateway

VERBOSE = '''anthropic/claude-opus-5-5
{"name":"Claude Opus 5.5","variants":{"high":{},"max":{}}}
openai/gpt-6-astra
{"name":"GPT-6 Astra","variants":{"low":{},"none":{"disabled":true}}}
not-a-model-line
'''


class ModelCatalogTests(unittest.TestCase):
    def test_forward_parser_preserves_multiline_metadata_and_plain_ids(self):
        source = 'provider/plain\nprovider/named\n' + json.dumps({'name': 'Named', 'variants': {'high': {}, 'hidden': {'disabled': True}}}, indent=2) + '\nprovider/final\n'
        parsed = model_catalog.parse_models(source)
        self.assertEqual([m['id'] for m in parsed], ['plain', 'named', 'final'])
        self.assertEqual(parsed[1]['reasoning'], ['high'])
        self.assertEqual(parsed[1]['name'], 'Named')

    def test_selection_rejects_missing_model_invalid_variant_and_v1(self):
        cli = Mock()
        profile = {'runtime': 'opencode', 'project': '.', 'provider': 'openai', 'model': 'test', 'reasoning': 'high'}
        data = {'models': [{'provider': 'openai', 'id': 'test', 'reasoning': ['high']}], 'supports_variants': True}
        with patch.object(model_catalog, 'discover_models', return_value=data):
            model_catalog.validate_selection(cli, Path('.'), profile)
            for change in ({'model': 'missing'}, {'reasoning': 'max'}):
                with self.assertRaises(ValueError):
                    model_catalog.validate_selection(cli, Path('.'), dict(profile, **change))
            # The error names what was attempted, so a Zen/Go mix-up is visible.
            with self.assertRaisesRegex(ValueError, 'openai/missing'):
                model_catalog.validate_selection(cli, Path('.'), dict(profile, model='missing'))
            data['supports_variants'] = False
            with self.assertRaisesRegex(ValueError, 'v2'):
                model_catalog.validate_selection(cli, Path('.'), profile)
            model_catalog.validate_selection(cli, Path('.'), dict(profile, reasoning=''))

    def test_selection_requires_a_provider_and_project_before_discovery(self):
        cli = Mock()
        with patch.object(model_catalog, 'discover_models') as discover:
            for profile, message in (
                    ({'runtime': 'opencode', 'provider': '', 'model': 'gpt-6-astra'}, 'provider and a model'),
                    ({'runtime': 'opencode', 'provider': 'openai', 'model': 'gpt-6-astra'}, 'project directory'),
                    ({'runtime': 'opencode', 'provider': 'openai', 'model': 'gpt-6-astra', 'project': 7}, 'project directory')):
                with self.assertRaisesRegex(ValueError, message):
                    model_catalog.validate_selection(cli, Path('.'), profile)
            discover.assert_not_called()

    def test_discovery_normalizes_missing_binary_and_reports_unknown_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / 'projects'
            projects.mkdir()
            cli = Mock()
            cli.home = root
            cli.binary.return_value = str(root / '.local/bin/opencode')
            body = {'runtime': 'opencode', 'project': str(projects)}
            with patch.object(model_catalog.subprocess, 'run', side_effect=FileNotFoundError('missing')):
                with self.assertRaisesRegex(ValueError, 'could not run'):
                    model_catalog.discover_models(cli, projects, body)
            with patch.object(model_catalog.subprocess, 'run',
                              side_effect=model_catalog.subprocess.TimeoutExpired('opencode', 30)):
                with self.assertRaisesRegex(ValueError, 'could not run'):
                    model_catalog.discover_models(cli, projects, body)
            # A failed version probe stays unknown instead of claiming v1.
            with patch.object(model_catalog.subprocess, 'run',
                              side_effect=[Mock(returncode=0, stdout=VERBOSE, stderr=''),
                                           Mock(returncode=1, stdout='', stderr='denied')]):
                result = model_catalog.discover_models(cli, projects, body)
            self.assertFalse(result['supports_variants'])
            self.assertEqual(result['version'], '')

    def test_every_runtime_reports_dropdown_options(self):
        snapshot = model_catalog.snapshot()['runtimes']
        self.assertEqual(sorted(snapshot), ['agy', 'claude', 'codex', 'opencode'])
        for runtime in ('claude', 'codex'):
            catalog = snapshot[runtime]
            self.assertTrue(catalog['models'], runtime)
            self.assertEqual(catalog['efforts'][0], model_catalog.DEFAULT_EFFORT)
            self.assertTrue(all(model['reasoning'] for model in catalog['models']), runtime)
            self.assertEqual(catalog['efforts'][1]['id'], model_catalog.EFFORTS[runtime][0])
        # Antigravity configures its own models in its own CLI.
        self.assertEqual(snapshot['agy']['models'], [])
        self.assertEqual(snapshot['agy']['efforts'], [model_catalog.DEFAULT_EFFORT])
        self.assertEqual(snapshot['opencode']['models'], [])

    def test_curated_entries_are_unique_and_well_formed(self):
        for runtime in model_catalog.RUNTIMES:
            seen = set()
            for model in model_catalog.curated(runtime)['models']:
                key = (model['provider'], model['id'])
                self.assertNotIn(key, seen)
                seen.add(key)
                self.assertTrue(model['name'])
                self.assertNotIn('/', model['id'])

    def test_parse_models_reads_names_and_enabled_variants(self):
        models = model_catalog.parse_models(VERBOSE)
        self.assertEqual(models, [
            {'provider': 'anthropic', 'id': 'claude-opus-5-5', 'name': 'Claude Opus 5.5',
             'reasoning': ['high', 'max']},
            {'provider': 'openai', 'id': 'gpt-6-astra', 'name': 'GPT-6 Astra',
             'reasoning': ['low']},
        ])

    def test_merge_keeps_curated_entries_and_prefers_discovered_metadata(self):
        merged = model_catalog.merge('opencode', [
            {'provider': 'anthropic', 'id': 'claude-opus-5-5', 'name': 'Renamed by the CLI', 'reasoning': ['max']},
            {'provider': 'openai', 'id': 'brand-new-model', 'name': 'Brand New', 'reasoning': []},
        ])
        by_id = {(m['provider'], m['id']): m for m in merged}
        self.assertEqual(by_id[('anthropic', 'claude-opus-5-5')]['name'], 'Renamed by the CLI')
        self.assertNotIn(('openai', 'gpt-6-luna'), by_id)
        self.assertIn(('openai', 'brand-new-model'), by_id)
        self.assertEqual(len(merged), len(by_id))

    def test_discovery_reports_the_account_catalog_over_the_curated_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / 'projects'
            projects.mkdir()
            cli = Mock()
            cli.home = root
            cli.binary.return_value = str(root / '.local/bin/opencode')
            completed = Mock(returncode=0, stdout=VERBOSE, stderr='')
            with patch.object(model_catalog.subprocess, 'run', side_effect=[completed, Mock(returncode=0, stdout='2.0.0')]) as run:
                result = model_catalog.discover_models(cli, projects, {'runtime': 'opencode',
                                                                       'project': str(projects)})
            self.assertEqual(run.call_args_list[0].args[0][1:], ['models', '--verbose'])
            self.assertEqual(result['source'], 'opencode models --verbose')
            self.assertTrue(result['supports_variants'])
            ids = {model['id'] for model in result['models']}
            self.assertIn('gpt-6-astra', ids)
            self.assertNotIn('claude-sonnet-5-5', ids)

    def test_discovery_is_bounded_to_opencode_and_an_existing_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / 'projects'
            projects.mkdir()
            cli = Mock()
            cli.home = root
            cli.binary.return_value = str(root / '.local/bin/opencode')
            with self.assertRaises(ValueError):
                model_catalog.discover_models(cli, projects, {'runtime': 'codex'})
            with self.assertRaises(ValueError):
                model_catalog.discover_models(cli, projects, {'runtime': 'opencode', 'project': str(root)})
            with self.assertRaises(ValueError):
                model_catalog.discover_models(cli, projects, {'runtime': 'opencode',
                                                             'project': str(projects / 'missing')})
            failed = Mock(returncode=1, stdout='', stderr='denied')
            with patch.object(model_catalog.subprocess, 'run', return_value=failed):
                with self.assertRaises(ValueError):
                    model_catalog.discover_models(cli, projects, {'runtime': 'opencode',
                                                                 'project': str(projects)})

    def test_models_endpoint_requires_a_token_and_serves_the_catalog(self):
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.cli_setup = Mock()
        server.token = 'a' * 48
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        headers = {'Authorization': 'Bearer ' + server.token}
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/api/models')
            self.assertEqual(error.exception.code, 401)
            data = json.load(urlopen(Request(base + '/api/models', headers=headers)))
            self.assertEqual(sorted(data['runtimes']), ['agy', 'claude', 'codex', 'opencode'])
            self.assertTrue(data['runtimes']['codex']['models'])
            # Discovery stays OpenCode-only, and reports why.
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + '/api/models', data=b'{"runtime":"codex"}', headers=headers))
            self.assertEqual(error.exception.code, 400)
            self.assertIn('OpenCode', json.load(error.exception)['error'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
