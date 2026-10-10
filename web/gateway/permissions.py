"""Per-profile OpenCode permission policies; no global configuration mutation."""
import json
from pathlib import Path

MODES = ('default', 'dashboard_outputs', 'full_autonomy')


def accessible_paths(body, runtime):
    paths = body.get('accessible_paths', [])
    if (not isinstance(paths, list) or len(paths) > 20 or
        any(not isinstance(p, str) or not p.strip() or len(p) > 500 or
            any(c in p for c in ('\n', '\r', '\x00')) or
            not p.startswith(('/', '~/', '$HOME/')) for p in paths)):
        raise ValueError('Use up to 20 absolute or home-relative path patterns, each under 500 characters.')
    if paths and runtime != 'opencode':
        raise ValueError('Accessible path patterns currently support OpenCode only.')
    return list(dict.fromkeys(p.strip() for p in paths))


def permission_mode(body, runtime):
    mode = body.get('permission_mode', 'default')
    if mode not in MODES:
        raise ValueError('Choose a supported permission mode.')
    if runtime != 'opencode' and mode != 'default':
        raise ValueError('Dashboard permission policies currently support OpenCode only.')
    return mode


def prepare_permissions(profile, output_root, home=None, output_directories=()):
    mode = permission_mode(profile, profile['runtime'])
    paths = accessible_paths(profile, profile['runtime'])
    variant = bool(profile.get('provider') and profile.get('model') and profile.get('reasoning'))
    if profile['runtime'] != 'opencode':
        return []
    root = Path(output_root)
    if mode == 'dashboard_outputs':
        granted = ('chat-replies', 'discussion-artifacts')
    else:
        # A run the gateway tells to save a reply or artifact must be able to
        # write there whatever mode was chosen. The output path is part of the
        # protocol, not an optional grant: without it a read-only discussion
        # stalls on an interactive permission prompt the gateway can never
        # answer, and the meeting never finalizes.
        granted = tuple(output_directories)
    paths = [str(root / folder / '**') for folder in granted] + paths
    if mode == 'default' and not paths and not variant:
        return []
    name = 'herdr-dashboard-' + profile['id']
    permission = {} if mode == 'default' and not paths else 'allow' if mode == 'full_autonomy' else {
        'external_directory': {pattern: 'allow' for pattern in paths}
    }
    path = Path(home or Path.home()) / '.config/opencode/agents' / (name + '.md')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # JSON objects and strings are valid YAML values in OpenCode's frontmatter.
    contents = ('---\ndescription: Dashboard-managed permission policy\nmode: primary\n'
                'permission: ' + json.dumps(permission) + '\n' +
                ('model: ' + json.dumps(profile['provider'] + '/' + profile['model']) + '\nvariant: ' + json.dumps(profile['reasoning']) + '\n' if variant else '') + '---\n'
                'Follow the conversation instructions and assigned persona.\n')
    if path.is_symlink():
        raise ValueError('Dashboard permission file must not be a symlink.')
    path.write_text(contents, encoding='utf-8')
    path.chmod(0o600)
    return ['--agent', name]
