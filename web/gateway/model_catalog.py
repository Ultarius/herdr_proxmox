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
            except ValueError:
                raise ValueError('OpenCode returned incomplete model metadata.')
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


def discover_models(cli, projects, body):
    if body.get('runtime') != 'opencode':
        raise ValueError('Model discovery is currently supported for OpenCode.')
    project = project_directory(projects, body.get('project', str(projects)))
    env = dict(os.environ, HOME=str(cli.home))
    result = subprocess.run([str(cli.binary('opencode')), 'models', '--verbose'],
                            cwd=project, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('OpenCode model discovery failed. Check the CLI accounts configuration and gateway logs.')
    if len(result.stdout) > 8_000_000:
        raise ValueError('OpenCode model catalog exceeds the supported size.')
    models = parse_models(result.stdout)
    if not models:
        raise ValueError('OpenCode returned no models. Check the installed CLI version and provider configuration.')
    version = subprocess.run([str(cli.binary('opencode')), '--version'], capture_output=True, text=True, timeout=5).stdout.strip()
    major = re.match(r'v?(\d+)\.', version)
    return {'runtime': 'opencode', 'models': models, 'efforts': [dict(DEFAULT_EFFORT)],
            'supports_variants': bool(major and int(major[1]) >= 2), 'version': version,
            'source': 'opencode models --verbose'}


def validate_selection(cli, projects, profile):
    if profile['runtime'] != 'opencode' or not profile.get('model'):
        return
    catalog = discover_models(cli, projects, {'runtime': 'opencode', 'project': profile['project']})
    selected = next((m for m in catalog['models'] if m['provider'] == profile['provider'] and m['id'] == profile['model']), None)
    if selected is None:
        raise ValueError('Selected OpenCode model is unavailable. Refresh models before launching.')
    if profile.get('reasoning'):
        if not catalog['supports_variants']:
            raise ValueError('Explicit reasoning variants require OpenCode v2 for interactive launches. Choose Model default or upgrade OpenCode.')
        if profile['reasoning'] not in selected['reasoning']:
            raise ValueError('Selected reasoning variant is unavailable for this model. Refresh models before launching.')
