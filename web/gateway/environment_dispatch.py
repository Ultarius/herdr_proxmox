"""One managed command dispatcher; choose an approved SDK by invocation cwd.

The dispatcher never downloads, guesses a version, or changes global defaults.
An explicit component is required outside known scopes. Receipts and native
validation still prove SDK correctness independently of interactive command use.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys

import environment_plan
import environment_adapters
import toolchain_policy

TOOLS = environment_adapters.commands()


def choose(manifest, tool, cwd):
    capability = TOOLS.get(tool)
    if capability is None:
        raise ValueError('No managed dispatcher exists for ' + str(tool))
    root, cwd = Path(manifest['checkout']).resolve(), Path(cwd).resolve()
    if not cwd.is_relative_to(root):
        raise ValueError('Run this command inside its recorded component checkout.')
    matches = []
    for entry in manifest['toolchains']:
        component = PurePosixPath(entry['component'])
        if component.is_absolute() or '..' in component.parts:
            raise ValueError('A dispatch component escapes its checkout.')
        scope = root / Path(*component.parts)
        if entry['name'] == capability and cwd.is_relative_to(scope):
            matches.append((len(component.parts), entry))
    if not matches:
        raise ValueError('No approved component binding applies here; change to the component directory first.')
    depth = max(item[0] for item in matches)
    entries = [entry for size, entry in matches if size == depth]
    identities = {(entry['required'], tuple(entry['executables'])) for entry in entries}
    if len(identities) != 1:
        raise ValueError('This component has conflicting SDK bindings; resolve its declarations first.')
    return entries[0]


def conflicting(toolchains):
    """Capabilities whose components disagree, and so need cwd dispatch.

    A capability with one agreed selection keeps its prepared directory on PATH.
    Routing it through a shim would refuse ordinary commands from a checkout root
    that is not itself a component, for a conflict it has nothing to do with.
    """
    selected = {}
    for entry in toolchains:
        selected.setdefault(entry['name'], set()).add((entry['required'], tuple(entry['executables'])))
    return {name for name, values in selected.items() if len(values) != 1}


def install(policy, checkout, caches, *, execution_environment=None):
    root = Path(caches) / 'dispatch'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = dict(version=1, checkout=str(Path(checkout).absolute()), caches=str(Path(caches).absolute()),
                   plan_digest=policy['plan_digest'], toolchains=policy['toolchains'])
    if execution_environment:
        payload['execution_environment'] = execution_environment
    encoded = environment_plan.canonical(payload)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    manifest = root / 'bindings.json'
    manifest.write_text(encoded, encoding='utf-8')
    shimmed = conflicting(policy['toolchains'])
    for tool, capability in TOOLS.items():
        if capability not in shimmed:
            continue
        command = shlex.join([sys.executable, str(Path(__file__).absolute()), str(manifest), digest, tool])
        launcher = root / tool
        launcher.write_text('#!/bin/sh\nexec ' + command + ' "$@"\n', encoding='utf-8')
        launcher.chmod(0o700)
    return root, digest


def main(arguments):
    manifest_path, expected, tool, *forwarded = arguments
    with open(manifest_path, 'rb') as stream:
        encoded = stream.read(1_000_001)
    if len(encoded) > 1_000_000 or hashlib.sha256(encoded).hexdigest() != expected:
        raise ValueError('The recorded component dispatcher failed its integrity check.')
    manifest = json.loads(encoded)
    entry = choose(manifest, tool, Path.cwd())
    if entry.get('adapter_version', 1) != environment_adapters.get(entry['name']).version:
        raise ValueError('The SDK adapter contract changed; prepare again.')
    import environment_providers
    environment_providers.verify_entry(entry)
    adapter = environment_adapters.get(entry['name'])
    executable = adapter.command(entry['executables'][0], tool)
    if executable != entry['executables'][0]:
        companions = [item for item in entry.get('companions', [])
                      if item['name'] == tool and executable in item.get('executables', [])]
        if len(companions) != 1:
            raise ValueError('The component has no verified companion binding for ' + tool)
    environment = toolchain_policy.environment(dict(toolchains=[entry]))
    # The prepared shell owns the execution cache/output paths. Dispatch must
    # not replace them with fresh per-component caches on every command.
    if manifest.get('execution_environment'):
        # Preserve exact gateway allocations, including custom storage mounts.
        # Agent processes never infer a new store or acquire GC authority.
        environment.update(manifest['execution_environment'])
    else:
        cache = Path(manifest['caches']) / hashlib.sha256(entry['component'].encode()).hexdigest()[:20]
        environment.update(adapter.cache_environment(cache, create=True))
    os.execve(executable, [executable, *forwarded], environment)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (ValueError, OSError) as error:
        print('Herdr environment: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
