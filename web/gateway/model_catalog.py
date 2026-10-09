"""Model catalogs for the agent runtime CLIs.

OpenCode enumerates its own catalog, so `discover_models` shells out to
`opencode models --verbose` for the account the operator connected. Codex and
Claude expose no equivalent command, with curated suggestions for the other runtimes. OpenCode discovery is
authoritative: only models and variants reported by the CLI are offered.
"""
from project_files import project_directory
import json
import os
import re
import subprocess
import hashlib
import threading
import time
from pathlib import Path

CACHE_TTL = 86400
CACHE_STALE_LIMIT = 7 * CACHE_TTL
CACHE_LOCK = threading.RLock()

RUNTIMES = ('codex', 'claude', 'opencode', 'agy')

DEFAULT_EFFORT = {'id': '', 'label': 'Model default'}

# Claude documents exactly these five effort levels. Codex accepts the OpenAI
# level names, which start one step lower at minimal.
EFFORTS = {
    'claude': ('low', 'medium', 'high', 'xhigh', 'max'),
    'codex': ('minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
    'opencode': ('minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
}
EFFORT_LABELS = {'minimal': 'Minimal', 'low': 'Low', 'medium': 'Medium', 'high': 'High',
                 'xhigh': 'Extra high', 'max': 'Max'}

# Bare model IDs for the single-provider CLIs, and provider/model IDs for
# OpenCode, whose --model flag always takes provider_id/model_id.
MODELS = {
    'claude': (
        ('claude-opus-5-5', 'Claude Opus 5.5'),
        ('claude-sonnet-5-5', 'Claude Sonnet 5.5'),
        ('claude-fable-5-1', 'Claude Fable 5.1'),
        ('claude-haiku-4-5', 'Claude Haiku 4.5'),
        ('claude-opus-5', 'Claude Opus 5'),
        ('claude-sonnet-5', 'Claude Sonnet 5'),
        ('claude-opus-4-8', 'Claude Opus 4.8'),
        ('claude-sonnet-4-6', 'Claude Sonnet 4.6'),
    ),
    'codex': (
        ('gpt-6-astra', 'GPT-6 Astra'),
        ('gpt-6.1-sol', 'GPT-6.1 Sol'),
        ('gpt-6-sol', 'GPT-6 Sol'),
        ('gpt-6-luna', 'GPT-6 Luna'),
    ),
    'opencode': (
        ('anthropic', 'claude-opus-5-5', 'Claude Opus 5.5'),
        ('anthropic', 'claude-sonnet-5-5', 'Claude Sonnet 5.5'),
        ('anthropic', 'claude-haiku-4-5', 'Claude Haiku 4.5'),
        ('anthropic', 'claude-sonnet-4-6', 'Claude Sonnet 4.6'),
        ('openai', 'gpt-6-astra', 'GPT-6 Astra'),
        ('openai', 'gpt-6.1-sol', 'GPT-6.1 Sol'),
        ('openai', 'gpt-6-luna', 'GPT-6 Luna'),
    ),
}


def efforts(runtime):
    """Dropdown options for reasoning effort, defaulting to the model choice."""
    return [dict(DEFAULT_EFFORT), *({'id': level, 'label': EFFORT_LABELS[level]}
                                    for level in EFFORTS.get(runtime, ()))]


def curated(runtime):
    """Maintained dropdown entries for one runtime. Antigravity has none."""
    levels = list(EFFORTS.get(runtime, ()))
    models = []
    for entry in (() if runtime == 'opencode' else MODELS.get(runtime, ())):
        provider, identifier, name = (entry if len(entry) == 3 else ('', entry[0], entry[1]))
        models.append({'provider': provider, 'id': identifier, 'name': name, 'reasoning': list(levels)})
    return {'runtime': runtime, 'models': models, 'efforts': efforts(runtime), 'source': 'curated'}


def snapshot():
    return {'runtimes': {runtime: curated(runtime) for runtime in RUNTIMES}}


def merge(runtime, discovered):
    """Curated entries first, then live ones replacing their curated twin."""
    catalog = {(model['provider'], model['id']): model for model in curated(runtime)['models']}
    for model in discovered:
        catalog[(model.get('provider', ''), model['id'])] = model
    return list(catalog.values())


def parse_models(output):
    output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
    models = []
    decoder = json.JSONDecoder()
    cursor = 0
    while cursor < len(output):
        end = output.find('\n', cursor)
        if end == -1:
            end = len(output)
        identifier = output[cursor:end].strip()
        cursor = end + 1
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/-]+', identifier):
            continue
        metadata = {}
        while cursor < len(output) and output[cursor].isspace():
            cursor += 1
        if cursor < len(output) and output[cursor] == '{':
            try:
                metadata, cursor = decoder.raw_decode(output, cursor)
                if not isinstance(metadata, dict):
                    raise ValueError()
            except ValueError as error:
                position = getattr(error, 'pos', cursor)
                line = output.count('\n', 0, position) + 1
                digest = hashlib.sha256(output.encode()).hexdigest()[:12]
                raise ValueError(f'OpenCode returned incomplete model metadata for {identifier}; line {line}, offset {position}, output length {len(output)}, digest {digest}. Refresh models to retry.') from error
        provider, model = identifier.split('/', 1)
        variants = metadata.get('variants', {})
        if isinstance(variants, dict):
            variants = [key for key, value in variants.items() if not isinstance(value, dict) or not value.get('disabled')]
        elif isinstance(variants, list):
            variants = [v.get('id') for v in variants if isinstance(v, dict) and not v.get('disabled')]
        else:
            variants = []
        models.append({'provider': provider, 'id': model, 'name': metadata.get('name', model),
                       'reasoning': sorted(v for v in variants if isinstance(v, str))})
    return list({(m['provider'], m['id']): m for m in models}.values())


def _discover_models(cli, projects, body):
    if body.get('runtime') != 'opencode':
        raise ValueError('Model discovery is currently supported for OpenCode.')
    project = project_directory(projects, body.get('project', str(projects)))
    env = dict(os.environ, HOME=str(cli.home))
    try:
        result = subprocess.run([str(cli.binary('opencode')), 'models', '--verbose'],
                                cwd=project, env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('OpenCode model discovery could not run or timed out. Check the CLI installation and try again.') from error
    if result.returncode:
        raise ValueError('OpenCode model discovery failed. Check the CLI accounts configuration and gateway logs.')
    if len(result.stdout) > 8_000_000:
        raise ValueError('OpenCode model catalog exceeds the supported size.')
    try:
        models = parse_models(result.stdout)
    except ValueError:
        # Enumeration is read-only: retry one malformed response, never a prompt.
        try:
            retry = subprocess.run([str(cli.binary('opencode')), 'models', '--verbose'],
                                   cwd=project, env=env, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('OpenCode catalog retry failed or timed out.') from error
        if retry.returncode or len(retry.stdout) > 8_000_000:
            raise ValueError('OpenCode catalog retry failed or exceeds size limit.')
        models = parse_models(retry.stdout)
    if not models:
        raise ValueError('OpenCode returned no models. Check the installed CLI version and provider configuration.')
    # A failed version probe must not be reported as an explicit v1 rejection.
    try:
        probe = subprocess.run([str(cli.binary('opencode')), '--version'], capture_output=True, text=True, timeout=5)
        version = probe.stdout.strip() if probe.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        version = ''
    major = re.match(r'v?(\d+)\.', version)
    return {'runtime': 'opencode', 'models': models, 'efforts': [dict(DEFAULT_EFFORT)],
            'supports_variants': bool(major and (int(major[1]) >= 2 or tuple(map(int, re.findall(r'\d+', version)[:3])) >= (1, 18, 35))), 'version': version,
            'source': 'opencode models --verbose'}


def discover_models(cli, projects, body):
    if body.get('runtime') != 'opencode':
        raise ValueError('Model discovery is currently supported for OpenCode.')
    project = project_directory(projects, body.get('project', str(projects)))
    binary = Path(cli.binary('opencode'))
    def identity(path):
        try:
            stat = path.stat()
            return [str(path), stat.st_mtime_ns, stat.st_size]
        except OSError:
            return [str(path), None]
    # Account/config and binary changes invalidate previously successful discovery.
    home = Path(cli.home)
    watched = [binary, home / '.local/share/opencode/auth.json',
               home / '.config/opencode/opencode.json', home / '.config/opencode/opencode.jsonc',
               project / 'opencode.json', project / 'opencode.jsonc']
    key = hashlib.sha256(json.dumps([str(project), *map(identity, watched)]).encode()).hexdigest()
    cache = home / '.config/herdr-web/model-cache' / (key + '.json')
    with CACHE_LOCK:
        cached = None
        try:
            if cache.stat().st_size > 8_000_000:
                raise ValueError('Cached model catalog exceeds size limit.')
            saved = json.loads(cache.read_text(encoding='utf-8'))
            age = time.time() - saved['checked_at']
            if (isinstance(saved.get('models'), list) and saved['models']
                    and all(isinstance(m, dict) and isinstance(m.get('id'), str) and isinstance(m.get('provider'), str) and isinstance(m.get('reasoning'), list) for m in saved['models'])
                    and isinstance(saved.get('supports_variants'), bool) and 0 <= age <= CACHE_STALE_LIMIT):
                cached = saved
        except (OSError, ValueError, TypeError, KeyError):
            pass
        missing = bool(cached and body.get('model') and not any(m['id'] == body['model'] and m['provider'] == body.get('provider') for m in cached['models']))
        if cached and not body.get('force') and time.time() < cached.get('retry_after', 0):
            return dict(cached, cached=True, stale=True)
        if cached and age < CACHE_TTL and not body.get('force') and not (missing and age >= 60):
            return dict(cached, cached=True, stale=False)
        try:
            result = _discover_models(cli, projects, dict(body, project=str(project)))
        except ValueError as error:
            if cached and not body.get('force'):
                cached.update(retry_after=time.time() + 60, refresh_error=str(error)[:500])
                temporary = cache.with_suffix('.tmp')
                temporary.write_text(json.dumps(cached), encoding='utf-8')
                temporary.replace(cache)
                return dict(cached, cached=True, stale=True)
            raise
        result['checked_at'] = time.time()
        cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = cache.with_suffix('.tmp')
        temporary.write_text(json.dumps(result), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(cache)
        return dict(result, cached=False, stale=False)


def validate_selection(cli, projects, profile):
    if profile.get('runtime') != 'opencode' or not profile.get('model'):
        return
    if not profile.get('provider'):
        # Report the real configuration error instead of a catalog miss.
        raise ValueError('OpenCode requires both a provider and a model, or leave both at CLI defaults.')
    project = profile.get('project')
    if not isinstance(project, str) or not project:
        raise ValueError('This agent has no project directory, so its saved model cannot be checked. '
                         'Set a project or clear the model to use the CLI default.')
    catalog = discover_models(cli, projects, {'runtime': 'opencode', 'project': project})
    selected = next((m for m in catalog['models'] if m['provider'] == profile['provider'] and m['id'] == profile['model']), None)
    if (selected is None or profile.get('reasoning') and profile['reasoning'] not in selected['reasoning']) and catalog.get('cached') and time.time() - catalog.get('checked_at', 0) >= 60 and time.time() >= catalog.get('retry_after', 0):
        catalog = discover_models(cli, projects, {'runtime': 'opencode', 'project': project, 'force': True})
        selected = next((m for m in catalog['models'] if m['provider'] == profile['provider'] and m['id'] == profile['model']), None)
    if selected is None:
        raise ValueError(f"Selected OpenCode provider/model is unavailable: "
                         f"{profile['provider']}/{profile['model']}. Connect the matching provider in CLI accounts, "
                         'then refresh models. OpenCode Go uses opencode-go; opencode selects Zen.')
    if profile.get('reasoning'):
        if not catalog['supports_variants']:
            raise ValueError('This OpenCode version does not support managed agent variants. Upgrade OpenCode or choose Model default.')
        if profile['reasoning'] not in selected['reasoning']:
            raise ValueError('Selected reasoning variant is unavailable for this model. Refresh models before launching.')
