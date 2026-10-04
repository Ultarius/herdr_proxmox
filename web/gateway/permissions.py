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


def prepare_permissions(profile, output_root, home=None):
    mode = permission_mode(profile, profile['runtime'])
    paths = accessible_paths(profile, profile['runtime'])
    if profile['runtime'] != 'opencode' or (mode == 'default' and not paths):
        return []
    name = 'herdr-dashboard-' + profile['id']
    if mode == 'dashboard_outputs':
        paths = [str(Path(output_root) / folder / '**')
                 for folder in ('chat-replies', 'discussion-artifacts')] + paths
    permission = 'allow' if mode == 'full_autonomy' else {
        'external_directory': {pattern: 'allow' for pattern in paths}
    }
    path = Path(home or Path.home()) / '.config/opencode/agents' / (name + '.md')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # JSON objects and strings are valid YAML values in OpenCode's frontmatter.
    contents = ('---\ndescription: Dashboard-managed permission policy\nmode: primary\n'
                'permission: ' + json.dumps(permission) + '\n---\n'
                'Follow the conversation instructions and assigned persona.\n')
    if path.is_symlink():
        raise ValueError('Dashboard permission file must not be a symlink.')
    path.write_text(contents, encoding='utf-8')
    path.chmod(0o600)
    return ['--agent', name]
