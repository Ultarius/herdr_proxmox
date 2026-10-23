"""The toolchain policy a repository declares, and the evidence that proves it ran.

A repository states its SDK requirement once, in a file it already maintains
(`.fvmrc` for Flutter). The catalog in `install/toolchains.json` is responsible
only for locating and installing SDKs, so there is no second version pin to keep
in step. Resolving that declaration into a policy and hashing it gives one value
— the digest — that a task, a verification run and a publication gate can all
compare against.

Nothing here trusts a label. A declared `flutter-3.44.8` cannot prove which
executable ran, so verification probes the real binary and records what it
actually reported.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

CATALOG = Path(__file__).resolve().parents[2] / 'install/toolchains.json'

# A candidate that edits one of these is editing the rules it will be judged by.
# The policy records their content so verification can flag the change for
# explicit review instead of silently adopting the candidate's own rules.
DECLARATIONS = ('.fvmrc', '.github/workflows/ci.yml', 'scripts/validate.sh', 'scripts/build-web.sh')
# Dependency resolution must come from committed lockfiles, not from whatever
# the resolver happened to pick at build time.
LOCKFILES = ('web/dashboard/pubspec.lock', 'web/jaspr_dashboard/pubspec.lock')
CHECKS = ('scripts/validate.sh', 'scripts/build-web.sh')

VERSION = re.compile(r'\d+(?:\.\d+)+')
PROBE_TIMEOUT = 60
PROJECT_RESOLVER = None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def digest(policy):
    """A stable identity for an approved policy, safe to compare and display."""
    return sha256(canonical(policy).encode()).hexdigest()


def catalog(path=None, strict=False):
    try:
        declared = json.loads((CATALOG if path is None else Path(path)).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        if strict:
            raise ValueError('Toolchain catalog is unreadable.') from None
        return {}
    if (not isinstance(declared, dict) or any(
            not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', name)
            or not isinstance(entry, dict) for name, entry in declared.items())):
        if strict:
            raise ValueError('Toolchain catalog contains invalid entries.')
        return {}
    return declared


def declared_version(entry, root, read=None):
    """Read a toolchain's required version from the repository's own declaration."""
    source = entry.get('version_source')
    if not isinstance(source, dict):
        return None, None
    name = source.get('file')
    if (not isinstance(name, str) or not name or '\\' in name
            or Path(name).is_absolute() or '..' in Path(name).parts
            or any((root / Path(*Path(name).parts[:index])).is_symlink()
                   for index in range(1, len(Path(name).parts) + 1))):
        return None, None
    try:
        text = (root / name).read_text(encoding='utf-8') if read is None else read(name).decode('utf-8')
    except OSError:
        return None, None
    key = source.get('key')
    if key:
        try:
            value = json.loads(text)
        except ValueError:
            return None, None
        value = value.get(key) if isinstance(value, dict) else None
    else:
        value = text.strip()
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        return None, None
    return value, f'{name}#{key}' if key else name


def expand(entry, key, version):
    """Substitute the declared version into a catalog path template."""
    template = entry.get(key)
    if not isinstance(template, str):
        return None
    return template.replace('{version}', str(version))


def toolchain(name, entry, root, read=None):
    version, declared_by = declared_version(entry, root, read)
    if version is None:
        return None
    return {'name': name, 'required': version, 'declared_by': declared_by,
            'label': str(entry.get('label') or name),
            'install_root': expand(entry, 'install_root', version),
            'executables': [value for value in
                            (expand(entry, 'executable', version), expand(entry, 'companion', version))
            if value]}


def fingerprint(root, names, read=None):
    found = {}
    for name in names:
        path = root / name
        try:
            if read is None and (path.is_symlink() or not path.is_file()):
                continue
            found[name] = sha256(path.read_bytes() if read is None else read(name)).hexdigest()
        except OSError:
            continue
    return found


def resolve(root, path=None, read=None):
    """The approved policy for a repository, as declared in its own committed files.

    Resolved from the approved checkout rather than from a candidate commit: a
    candidate must not redefine the rules used to approve it.
    """
    root = Path(root)
    declared = catalog(path, strict=True)
    for entry in declared.values():
        source = entry.get('version_source')
        if isinstance(source, dict) and isinstance(source.get('file'), str):
            declaration = root / source['file']
            try:
                present = read(source['file']) is not None if read else declaration.exists() or declaration.is_symlink()
            except FileNotFoundError:
                present = False
            if present:
                if declared_version(entry, root, read)[0] is None:
                    raise ValueError('Repository toolchain declaration is invalid: ' + source['file'])
    toolchains = [found for found in
                  (toolchain(name, entry, root, read) for name, entry in sorted(declared.items()))
                  if found is not None]
    policy = {
        'toolchains': toolchains,
        'checks': list(fingerprint(root, CHECKS, read)) if read else [name for name in CHECKS
                   if (root / name).is_file() and not (root / name).is_symlink()
                   and not (root / 'scripts').is_symlink()],
        'lockfiles': fingerprint(root, LOCKFILES, read),
        'declarations': fingerprint(root, DECLARATIONS, read),
    }
    return policy


def resolve_committed(root, path=None, *, revision=None):
    if PROJECT_RESOLVER is not None and path is None:
        return PROJECT_RESOLVER(root, revision=revision) if revision is not None else PROJECT_RESOLVER(root)
    if revision is None:
        return resolve_legacy_committed(root, path)
    return resolve_legacy_committed(root, path, revision=revision)


def resolve_legacy_committed(root, path=None, *, revision=None):
    """Read one committed tree, so working-tree edits cannot change approval rules."""
    import project_git
    revision = project_git.git(root, 'rev-parse', '--verify', (revision or 'HEAD') + '^{commit}').strip()
    def read(name):
        records = project_git.git(root, 'ls-tree', revision, '--', ':(literal)' + name).strip()
        if not records:
            raise FileNotFoundError(name)
        if not records.startswith(('100644 blob ', '100755 blob ')):
            raise ValueError('Toolchain policy requires committed regular files: ' + name)
        return project_git.git(root, 'show', revision + ':' + name).encode('utf-8')
    return resolve(root, path, read=read)


def changes(approved, candidate):
    """What a candidate changed about the policy it is judged by.

    Returned rather than applied: these edits need explicit review, and until
    then the approved policy is the one that runs.
    """
    changed = []
    for field in ('declarations', 'lockfiles'):
        for name in sorted(set(approved[field]) | set(candidate[field])):
            if candidate[field].get(name) != approved[field].get(name):
                changed.append(name)
    def rules(policy):
        return {(entry.get('component', '.'), entry['name']): (entry['rule']['evidence'] if 'rule' in entry else entry['required'])
                for entry in policy['toolchains']}
    before, after = rules(approved), rules(candidate)
    changed.extend('toolchain:' + (name[1] if name[0] == '.' else '/'.join(name)) for name in before.keys() | after.keys()
                   if before.get(name) != after.get(name))
    return sorted(set(changed))


def candidate_policy(root, approved, revision='HEAD'):
    """Compare candidate inputs without requiring its own preparation receipt.

    Selected recipe evidence is protected by the approved plan. A candidate
    without that plan must still supply its own committed bytes for those paths.
    No commands or SDK selections are copied from the candidate into approval.
    """
    candidate = resolve_committed(root) if revision == 'HEAD' else resolve_committed(root, revision=revision)
    evidence = {item['evidence'].split('#')[0] for item in approved.get('recipes', [])
                if item.get('evidence')}
    if evidence:
        import environment_policy
        try:
            values = environment_policy.declarations(environment_policy.tree(root, revision), evidence)
        except ValueError as error:
            raise ValueError('Selected validation recipe evidence is unavailable or unsafe: ' + str(error)) from error
        candidate = dict(candidate, declarations=dict(candidate['declarations'], **values))
    return candidate


def parse_flutter(text):
    try:
        return str(json.loads(text).get('frameworkVersion') or '') or None
    except (ValueError, AttributeError):
        return None


def parse_dart(text):
    found = VERSION.search(text or '')
    return found.group(0) if found else None


PARSERS = {'flutter': parse_flutter, 'dart': parse_dart}


def probe(name, executable, required, run=None, cwd=None, environment=None):
    """Run the real binary and report what it actually is.

    A declared version is a claim; this is the measurement. It is deliberately
    separate from installation, because the whole point is that an installed
    directory can hold a different SDK than the one the policy requires.
    """
    if not executable:
        return {'name': name, 'required': required, 'actual': None, 'matches': False,
                'path': None, 'detail': 'No executable is configured for this toolchain.'}
    path = Path(executable)
    if not path.is_file():
        return {'name': name, 'required': required, 'actual': None, 'matches': False,
                'path': executable, 'detail': 'The configured SDK is not installed.'}
    parser = PARSERS.get(name, parse_dart)
    arguments = [str(path), '--version', '--machine'] if name == 'flutter' else [str(path), '--version']
    runner = subprocess.run if run is None else run
    try:
        context = {}
        if cwd is not None:
            context['cwd'] = cwd
        if environment is not None:
            context['env'] = environment
        result = runner(arguments, capture_output=True, text=True, timeout=PROBE_TIMEOUT, **context)
        output = (result.stdout or '') + (result.stderr or '')
        actual = parser(output) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {'name': name, 'required': required, 'actual': None, 'matches': False,
                'path': executable, 'detail': f'The configured SDK could not be run: {error}'[:300]}
    if not actual:
        return {'name': name, 'required': required, 'actual': None, 'matches': False,
                'path': executable, 'detail': 'The configured SDK reported no version.'}
    measured = {'name': name, 'required': required, 'actual': actual, 'path': executable,
            'matches': actual == required,
            'detail': (f'{name} {actual} is installed and matches the approved policy.'
                       if actual == required else
                       f'Required {name} {required}; the configured executable reports {actual}.')}
    if name == 'flutter':
        measured['dart_required'] = parse_dart(str(json.loads(output).get('dartSdkVersion') or ''))
    return measured


def verify(policy, run=None, root=None):
    """Probe every toolchain the policy requires, first on PATH order."""
    results = []
    for entry in policy['toolchains']:
        if 'component' in entry:
            executable = next(iter(entry['executables']), None)
            measured = dict(name=entry['name'], component=entry['component'], required=entry['required'],
                            actual=None, matches=False, path=executable, detail='No prepared SDK binding is available.')
            if executable and root is not None:
                cwd = Path(root) / entry['cwd']
                if cwd.resolve().is_relative_to(Path(root).resolve()) and Path(executable).is_file():
                    try:
                        import environment_adapters
                        if entry.get('adapter_version', 1) != environment_adapters.get(entry['name']).version:
                            raise ValueError('The SDK adapter contract changed; prepare again.')
                        import environment_providers
                        environment_providers.verify_entry(entry)
                        runner = subprocess.run if run is None else run
                        result = runner([executable, '--version'], cwd=cwd,
                            env=environment(dict(policy, toolchains=[entry])), capture_output=True,
                            text=True, timeout=PROBE_TIMEOUT)
                        actual = parse_dart((result.stdout or '') + (result.stderr or '')) if result.returncode == 0 else None
                        measured.update(actual=actual, matches=actual == entry['required'],
                                        detail=f"{entry['component']}: required {entry['required']}; measured {actual or 'unavailable'}.")
                    except (ValueError, OSError, subprocess.SubprocessError) as error:
                        measured['detail'] = str(error)[:500]
            results.append(measured)
            for companion in entry.get('companions', []):
                observed = probe(companion['name'], companion['executables'][0], companion['required'], run=run,
                    cwd=Path(root) / entry['cwd'] if root else None,
                    environment=environment(dict(policy, toolchains=[entry])))
                observed['component'] = entry['component']
                results.append(observed)
            continue
        executable = entry['executables'][0] if entry['executables'] else None
        measured = probe(entry['name'], executable, entry['required'], run=run)
        results.append(measured)
        if entry['name'] == 'flutter' and len(entry['executables']) > 1:
            expected = measured.get('dart_required')
            companion = probe('dart', entry['executables'][1], expected, run=run)
            if not expected:
                companion.update(matches=False, detail='Flutter did not report its bundled Dart version.')
            results.append(companion)
    return results


def environment(policy, base=None):
    """The approved SDK first on PATH, so repository scripts select it by name."""
    import os
    value = dict(os.environ if base is None else base)
    directories = []
    for entry in policy['toolchains']:
        for executable in entry['executables']:
            parent = str(Path(executable).parent)
            if parent not in directories:
                directories.append(parent)
        if entry.get('executables') and entry.get('component') is not None:
            import environment_adapters
            value.update(environment_adapters.get(entry['name']).environment(
                entry['executables'][0], entry.get('execution_environment')))
            value.update(environment_adapters.get(entry['name']).build_environment(entry))
    if directories:
        value['PATH'] = os.pathsep.join([*directories, value.get('PATH', '')]).strip(os.pathsep)
    return value


def evidence_matches(policy, evidence):
    """Require complete component evidence, not merely an all([]) or one SDK pass."""
    if policy.get('blockers') or policy.get('validation_blockers'):
        return False
    if not policy['toolchains']:
        return True
    if (evidence.get('policy_changes') or evidence.get('policy_digest') != digest(policy)
            or policy.get('plan_digest') != evidence.get('plan_digest')):
        return False
    if policy.get('plan_digest'):
        checks = evidence.get('checks') or []
        if (evidence.get('required_checks_verified') is not True or not policy.get('recipes')
                or any(not any(check.get('id') == recipe['id'] and check.get('status') == 'passed'
                               and check.get('exit_code') == 0 for check in checks) for recipe in policy['recipes'])):
            return False
    required = {(entry.get('component', '.'), entry['name']): (entry, entry is primary)
                for primary in policy['toolchains'] for entry in [primary, *primary.get('companions', [])]}
    measured = evidence.get('toolchain_evidence') or []
    for key, (entry, primary) in required.items():
        found = [item for item in measured if (item.get('component', '.'), item.get('name')) == key]
        if len(found) != 1 or found[0].get('matches') is not True:
            return False
        if 'component' in entry:
            path_matches = found[0].get('path') in entry['executables']
            if primary:
                import environment_adapters
                path_matches = environment_adapters.get(entry['name']).matches_evidence_path(
                    entry, found[0], evidence.get('environment_restores') or [])
            if found[0].get('actual') != entry['required'] or not path_matches:
                return False
    return bool(measured) and all(item.get('matches') is True for item in measured)


def summary(policy):
    """One line per toolchain, for a prompt or a dashboard row."""
    return [f"{entry['label']} {entry['required']}" for entry in policy['toolchains']]
