"""Per-profile OpenCode permissions and Codex launch policy."""
import json
import re
from pathlib import Path

CODEX_NO_APPROVAL = 'codex_no_approval'
MODES = {
    'codex': ('default', CODEX_NO_APPROVAL),
    'opencode': ('default', 'dashboard_outputs', 'full_autonomy'),
}
# The id becomes part of a file name, so it must not carry separators or
# traversal segments. Generated ids are 32-character hex and always match.
IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,64}')


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
    if mode not in MODES.get(runtime, ('default',)):
        raise ValueError('Choose a supported permission mode for this runtime.')
    return mode


OUTPUT_FOLDERS = ('chat-replies', 'discussion-artifacts')
ASSISTANCE_OUTPUT = re.compile(r'environment-assistance/[a-f0-9]{32}')


def _managed_paths(managed_directories, managed_root):
    paths = []
    for directory in managed_directories:
        path = Path(directory)
        if not path.is_absolute() or '..' in path.parts or any(c in str(path) for c in ('\0', '\n', '\r', '*', '?')):
            raise ValueError('Managed execution grants require exact absolute directory paths.')
        if managed_root is None:
            raise ValueError('Managed execution grants require a verified storage root.')
        base = Path(managed_root).absolute() / 'objects'
        try:
            relative = path.relative_to(base)
        except ValueError:
            raise ValueError('A managed grant is outside the verified storage root.') from None
        if len(relative.parts) != 2 or not re.fullmatch(r'[a-f0-9]{64}', relative.parts[0]) or relative.parts[1] != 'data':
            raise ValueError('Managed grants cover only registered data children, never control directories.')
        from workspace_storage import _safe_chain
        _safe_chain(path)
        paths.append(path)
    return list(dict.fromkeys(paths))


def _output_paths(root, output_directories):
    paths = []
    for folder in output_directories:
        if not isinstance(folder, str) or (folder not in OUTPUT_FOLDERS and not ASSISTANCE_OUTPUT.fullmatch(folder)):
            raise ValueError('Unknown dashboard output folder.')
        paths.append(root / folder)
    return list(dict.fromkeys(paths))


def _configured_writable_roots(home):
    """Writable roots the operator set in Codex's own configuration."""
    import os
    import tomllib
    directory = Path(os.environ.get('CODEX_HOME') or Path(home or Path.home()) / '.codex')
    try:
        settings = tomllib.loads((directory / 'config.toml').read_text(encoding='utf-8'))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return []
    roots = (settings.get('sandbox_workspace_write') or {}).get('writable_roots')
    return [item for item in roots if isinstance(item, str)] if isinstance(roots, list) else []


def _codex_writable_roots(outputs, managed, sandbox_set, home):
    """Provision the exact output directories before handing them to Codex.

    ``--add-dir`` is only for a launch that sets the workspace-write sandbox
    itself: Codex refuses the flag, and does not start, when the effective
    sandbox is read-only, which is how it opens a folder it does not trust.
    Otherwise the roots go in as configuration, which Codex applies in
    workspace-write and ignores in read-only. That replaces a configured list,
    so the operator's own roots are kept in it."""
    from workspace_storage import _safe_chain
    for directory in outputs:
        _safe_chain(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _safe_chain(directory)
    roots = [str(path) for path in dict.fromkeys([*outputs, *managed])]
    if not roots:
        return []
    if sandbox_set:
        return [argument for path in roots for argument in ('--add-dir', path)]
    values = list(dict.fromkeys([*_configured_writable_roots(home), *roots]))
    return ['--config', 'sandbox_workspace_write.writable_roots=' + json.dumps(values)]


def prepare_permissions(profile, output_root, home=None, output_directories=(), managed_directories=(), managed_root=None):
    # The profile id is interpolated into a file name, so a crafted id with a
    # separator or '..' would write outside the agent directory.
    if not isinstance(profile.get('id'), str) or not IDENTITY.fullmatch(profile['id']):
        raise ValueError('Profile identity is not usable for a permission file name.')
    mode = permission_mode(profile, profile['runtime'])
    paths = accessible_paths(profile, profile['runtime'])
    variant = bool(profile.get('provider') and profile.get('model') and profile.get('reasoning'))
    if profile['runtime'] not in ('codex', 'opencode'):
        return []
    managed = _managed_paths(managed_directories, managed_root)
    root = Path(output_root).absolute()
    outputs = _output_paths(root, output_directories)
    if profile['runtime'] == 'codex':
        sandbox = ['--sandbox', 'workspace-write', '--ask-for-approval', 'never'] if mode == CODEX_NO_APPROVAL else []
        return sandbox + _codex_writable_roots(outputs, managed, bool(sandbox), home)
    if mode == 'dashboard_outputs':
        outputs = [*(root / folder for folder in OUTPUT_FOLDERS), *outputs]
    paths = [str(path / '**') for path in outputs] + paths
    paths += [str(path / '**') for path in managed]
    paths = list(dict.fromkeys(paths))
    if mode == 'default' and not paths and not variant:
        return []
    name = 'herdr-dashboard-' + profile['id']
    permission = {} if mode == 'default' and not paths else 'allow' if mode == 'full_autonomy' else {
        'external_directory': {pattern: 'allow' for pattern in paths}
    }
    path = Path(home or Path.home()) / '.config/opencode/agents' / (name + '.md')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # JSON objects and strings are valid YAML values in OpenCode's frontmatter.
    # A Markdown body is a system-prompt override in OpenCode, even if its
    # wording looks neutral. Keep it empty so provider instructions survive.
    contents = ('---\ndescription: Dashboard-managed permission policy\nmode: primary\n'
                'permission: ' + json.dumps(permission) + '\n' +
                ('model: ' + json.dumps(profile['provider'] + '/' + profile['model']) + '\nvariant: ' + json.dumps(profile['reasoning']) + '\n' if variant else '') + '---\n')
    if path.is_symlink():
        raise ValueError('Dashboard permission file must not be a symlink.')
    path.write_text(contents, encoding='utf-8')
    path.chmod(0o600)
    return ['--agent', name]
