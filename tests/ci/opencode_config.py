"""Credential-free checks of generated agent files in the installed OpenCode CLI.

Run: python3 tests/ci/opencode_config.py --gateway /opt/herdr-web/web/gateway
No prompt, tool execution, authentication or Herdr pane is involved.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def isolated_environment(home):
    # Do not inherit provider credentials, plugins, inline config or a user's
    # OpenCode state. Windows needs its system paths to launch the native CLI.
    keep = ('PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP')
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), OPENCODE_TEST_HOME=str(home),
               XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'),
               XDG_CACHE_HOME=str(home / '.cache'), XDG_STATE_HOME=str(home / '.local/state'),
               OPENCODE_CONFIG_DIR=str(home / '.config/opencode'),
               OPENCODE_DISABLE_MODELS_FETCH='true', OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
               OPENCODE_DISABLE_LSP_DOWNLOAD='true', OPENCODE_DISABLE_AUTOUPDATE='true', CI='true')
    return env


def check(binary, gateway):
    sys.path.insert(0, str(Path(gateway).resolve()))
    from permissions import prepare_permissions
    from organizations import launch_arguments
    with tempfile.TemporaryDirectory(prefix='herdr-opencode-config-') as directory:
        home = Path(directory)
        project = home / 'project'
        project.mkdir()
        config = home / '.config/opencode'
        config.mkdir(parents=True)
        # Deterministic local provider metadata. Debug commands never generate
        # a message, and its loopback endpoint cannot reach a paid provider.
        (config / 'opencode.json').write_text(json.dumps(dict(
            model='herdr-probe/probe', autoupdate=False, plugin=[],
            permission=dict(bash='deny', edit='ask', external_directory={'*': 'ask'}),
            provider={'herdr-probe': dict(npm='@ai-sdk/openai-compatible', name='Smoke fixture',
                options=dict(baseURL='http://127.0.0.1:9/v1', apiKey='fixture-not-a-secret'),
                models={'probe': dict(name='Smoke fixture', limit=dict(context=8192, output=1024),
                                      variants={'high': dict(reasoningEffort='high')})})})), encoding='utf-8')
        env = isolated_environment(home)
        def cli(*args):
            result = subprocess.run([str(binary), *args], cwd=project, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
            if result.returncode:
                # Never dump raw resolved config; the probe reports only fixture
                # assertions and exit codes, even when run beside real credentials.
                raise AssertionError(f'OpenCode {args[0]} exited {result.returncode}')
            return result.stdout
        version = cli('--version').strip()
        output = home / 'outputs'
        source = home / 'projects/game'
        source.mkdir(parents=True, exist_ok=True)
        cases = [
            ('default', {}, ('chat-replies',)),
            ('model', dict(provider='herdr-probe', model='probe'), ('chat-replies',)),
            ('reasoning', dict(provider='herdr-probe', model='probe', reasoning='high'), ('chat-replies',)),
            ('discussion', {}, ('chat-replies', 'discussion-artifacts')),
            ('permissions', dict(permission_mode='dashboard_outputs', accessible_paths=['/herdr-smoke-shared/**']), ('chat-replies',)),
            ('autonomy', dict(permission_mode='full_autonomy'), ('chat-replies',)),
        ]
        for case, settings, directories in cases:
            profile = dict(id='smoke-' + case, runtime='opencode', **settings)
            arguments = launch_arguments(profile) + prepare_permissions(
                profile, output, home=home, output_directories=directories)
            name = 'herdr-dashboard-' + profile['id']
            expected_args = (['--model', 'herdr-probe/probe'] if settings.get('model') else []) + ['--agent', name]
            assert arguments == expected_args, case + ': incorrect launch arguments'
            resolved = json.loads(cli('debug', 'agent', name))
            assert resolved['name'] == name and resolved['mode'] == 'primary', case + ': wrong agent'
            assert not resolved.get('prompt'), case + ': overrides provider system prompt'
            rules = resolved['permission']
            def last(permission, pattern='*'):
                matches = [r['action'] for r in rules if r['permission'] in ('*', permission)
                           and r['pattern'] in ('*', pattern)]
                assert matches, case + ': missing permission rules'
                return matches[-1]
            if settings.get('permission_mode') == 'full_autonomy':
                assert last('bash') == 'allow', case + ': explicit autonomy not applied'
            else:
                assert last('bash') == 'deny', case + ': inherited bash denial was lost'
                assert last('edit') == 'ask', case + ': inherited edit decision was lost'
                assert resolved['tools']['bash'] is False, case + ': denied tool is enabled'
                assert last('external_directory') == 'ask', case + ': outside paths allowed'
                granted = ('chat-replies', 'discussion-artifacts') if case == 'permissions' else directories
                for folder in granted:
                    assert last('external_directory', str(output / folder / '**')) == 'allow', case + ': missing output grant'
                if case == 'permissions':
                    assert last('external_directory', '/herdr-smoke-shared/**') == 'allow', 'Custom path grant lost'
                if case == 'discussion':
                    assert last('external_directory', str(source / '**')) == 'ask', 'Source project access was widened'
                    assert last('external_directory', str(source.parent / '**')) == 'ask', 'Projects parent allowed'
                    assert last('external_directory', str(source.parent / 'other/**')) == 'ask', 'Sibling project allowed'
            if case == 'reasoning':
                assert resolved.get('model') == dict(providerID='herdr-probe', modelID='probe'), 'Reasoning model lost'
                assert resolved.get('variant') == 'high', 'Reasoning variant lost'
            else:
                assert not resolved.get('variant'), case + ': invented reasoning variant'
                assert not resolved.get('model'), case + ': agent file overrides CLI/default model'
            print(f'OpenCode {version}: {case} configuration verified', flush=True)
        for runtime in ('codex', 'claude'):
            assert prepare_permissions(dict(id=runtime, runtime=runtime), output, home=home,
                                       output_directories=('chat-replies',)) == []
        assert not (home / '.local/share/opencode/auth.json').exists(), 'Probe created credentials'
        return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default=shutil.which('opencode'))
    parser.add_argument('--gateway', default=str(Path(__file__).resolve().parents[2] / 'web/gateway'))
    args = parser.parse_args()
    if not args.binary:
        parser.error('Install OpenCode or pass --binary; this check must not silently skip.')
    check(args.binary, args.gateway)


if __name__ == '__main__':
    main()
