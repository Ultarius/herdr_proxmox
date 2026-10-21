"""Derive what a project needs from the files it already maintains.

This is tier 1: a declarative registry plus a handful of reusable readers. It
answers "what does this repository say about itself" and nothing more. Every
fact it reports carries the file and the field it came from, and every question
it could not settle is listed as unresolved rather than filled in.

The distinctions that matter for what the answer means:

* a **manifest** is evidence about a toolchain
* a **lockfile** is dependency-resolution evidence, not a version requirement
* a **config file** is configured tooling — not necessarily installed, and not
  necessarily compatible
* a **script** yields a candidate command, which is a proposal to run something
  later, never a claim that it was run

Nothing here executes repository code, resolves dependencies, or reads a file
it was not told to read. Configuration that is computed rather than declared —
MSBuild conditions, Gradle source sets, JavaScript config modules — is reported
as unresolved, because evaluating it means running it.
"""
from pathlib import Path, PurePosixPath
import json
import os
import stat
import re
import tomllib
import xml.etree.ElementTree as ElementTree

import dependency_constraints

MAX_MANIFEST_BYTES = 256 * 1024
MAX_MANIFESTS = 200
MAX_CONFIGURED = 12
EVIDENCE_LIMIT = 5
MAX_COMMANDS = 12
KEY = re.compile(r'[A-Za-z0-9_.\-\[\]]{1,120}')


def _source(file, field='', kind='json', role='constraint', minimum=False, notation=''):
    """One place a requirement can be declared.

    `role` separates a compatibility range the project states (`constraint`)
    from the version it selects (`selection`). They are different facts and a
    report that keeps only one of them cannot show a conflict between them.
    `minimum` marks a declaration that states a floor rather than a range, which
    is what Cargo's `rust-version` does. `notation` names the constraint language
    of this one source when it differs from the requirement's default: one tool
    can be declared in two languages at once, as PEP 621 and Poetry both are in
    a single pyproject.toml.
    """
    return dict(file=file, field=field, kind=kind, role=role, minimum=minimum, notation=notation)


# The registry. A component exists where a marker is present; the rest is data.
# Each requirement is a separate record with its own tool: an FVM pin names
# Flutter, and reading it into the Dart requirement would substitute one SDK's
# version for another's.
ECOSYSTEMS = (
    dict(id='node', label='Node.js', markers=('package.json',),
         requirements=(
             dict(tool='node', label='Node.js', purpose='runtime', executable='node',
                  notation='semver', sources=(
                      _source('package.json', 'engines.node'),
                      _source('.nvmrc', kind='text', role='selection'),
                      _source('.node-version', kind='text', role='selection'))),
         ),
         managers={'package-lock.json': 'npm', 'pnpm-lock.yaml': 'pnpm',
                   'yarn.lock': 'yarn', 'bun.lockb': 'bun'},
         dependency_fields=('dependencies', 'devDependencies', 'peerDependencies'),
         scripts=('test', 'build', 'lint', 'typecheck', 'format', 'start'),
         checks=(('test', ('npm', 'test')), ('build', ('npm', 'run', 'build')))),
    dict(id='python', label='Python', markers=('pyproject.toml', 'requirements.txt', 'setup.py'),
         requirements=(
             dict(tool='python', label='Python', purpose='runtime', executable='python3',
                  notation='pep440', sources=(
                      _source('pyproject.toml', 'project.requires-python', kind='toml'),
                      _source('pyproject.toml', 'tool.poetry.dependencies.python', kind='toml',
                              notation='poetry'),
                      _source('.python-version', kind='text', role='selection'))),
         ),
         managers={'poetry.lock': 'poetry', 'Pipfile.lock': 'pipenv',
                   'uv.lock': 'uv', 'requirements.txt': 'pip'},
         dependency_fields=('project.dependencies',),
         checks=(('test', ('python', '-m', 'pytest')),)),
    dict(id='php', label='PHP', markers=('composer.json',),
         requirements=(
             dict(tool='php', label='PHP', purpose='runtime', executable='php',
                  notation='composer', sources=(_source('composer.json', 'require.php'),)),
         ),
         managers={'composer.lock': 'composer'},
         dependency_fields=('require', 'require-dev'),
         checks=(('test', ('composer', 'test')),)),
    dict(id='go', label='Go', markers=('go.mod',),
         requirements=(
             dict(tool='go', label='Go', purpose='toolchain', executable='go',
                  notation='gomod', sources=(_source('go.mod', 'go', kind='gomod'),)),
         ),
         managers={'go.sum': 'go modules'},
         dependency_fields=(),
         scripts=(),
         checks=(('test', ('go', 'test', './...')), ('build', ('go', 'build', './...')))),
    dict(id='rust', label='Rust', markers=('Cargo.toml',),
         requirements=(
             # `rust-version` is a minimum supported compiler version, not a
             # toolchain selection, and Cargo is not the thing it versions.
             dict(tool='rust', label='Rust', purpose='compiler', executable='rustc',
                  notation='semver', sources=(
                      _source('Cargo.toml', 'package.rust-version', kind='toml', minimum=True),)),
             dict(tool='rust-toolchain', label='Rust toolchain', purpose='toolchain selection',
                  executable='rustup', notation='', sources=(
                      _source('rust-toolchain.toml', 'toolchain.channel', kind='toml',
                              role='selection'),
                      _source('rust-toolchain', kind='rust-toolchain', role='selection'))),
         ),
         managers={'Cargo.lock': 'cargo'},
         dependency_fields=('dependencies',),
         checks=(('test', ('cargo', 'test')), ('build', ('cargo', 'build')))),
    dict(id='dotnet', label='.NET', markers=('global.json',),
         # A project file names a component even when the SDK is not pinned;
         # the SDK version then stays unresolved rather than being guessed from
         # a target framework, which is a different fact.
         suffix_markers=('.csproj', '.fsproj', '.vbproj', '.sln', '.slnx', '.esproj'),
         requirements=(
             dict(tool='dotnet', label='.NET SDK', purpose='SDK', executable='dotnet',
                  notation='dotnet', sources=(_source('global.json', 'sdk.version'),)),
         ),
         managers={'packages.lock.json': 'nuget',
                   'Directory.Packages.props': 'central package management'},
         dependency_fields=(),
         checks=(('test', ('dotnet', 'test')), ('build', ('dotnet', 'build')))),
    dict(id='dart', label='Dart', markers=('pubspec.yaml',),
         requirements=(
             dict(tool='dart', label='Dart', purpose='language SDK', executable='dart',
                  notation='pub', sources=(
                      _source('pubspec.yaml', 'environment.sdk', kind='yaml'),)),
             dict(tool='flutter', label='Flutter', purpose='application SDK', executable='flutter',
                  notation='pub', sources=(
                      _source('.fvmrc', 'flutter', role='selection'),
                      _source('pubspec.yaml', 'environment.flutter', kind='yaml'))),
         ),
         managers={'pubspec.lock': 'pub'},
         dependency_fields=('dependencies', 'dev_dependencies'),
         checks=(('test', ('dart', 'test')), ('analyze', ('dart', 'analyze')))),
)

# Framework identification is evidence-based and separate from the runtime: a
# React application needs Node, but finding Node does not mean it is React.
# Matched on the exact package name — no suffix or scope guessing, because a
# partial match would be the same silent substitution this design exists to
# avoid.
FRAMEWORKS = {
    'node': {'react': ('react', 'React'), 'next': ('next', 'Next.js'),
             '@angular/core': ('angular', 'Angular'), 'vue': ('vue', 'Vue'),
             'svelte': ('svelte', 'Svelte'), 'express': ('express', 'Express'),
             '@nestjs/core': ('nestjs', 'NestJS'), 'vite': ('vite', 'Vite'),
             'webpack': ('webpack', 'Webpack'), 'typescript': ('typescript', 'TypeScript'),
             'jest': ('jest', 'Jest'), 'vitest': ('vitest', 'Vitest'),
             'playwright': ('playwright', 'Playwright'),
             '@playwright/test': ('playwright', 'Playwright'),
             'electron': ('electron', 'Electron'), 'react-native': ('react-native', 'React Native'),
             'expo': ('expo', 'Expo'), 'nuxt': ('nuxt', 'Nuxt'), 'astro': ('astro', 'Astro')},
    'python': {'django': ('django', 'Django'), 'flask': ('flask', 'Flask'),
               'fastapi': ('fastapi', 'FastAPI'), 'pytest': ('pytest', 'pytest'),
               'numpy': ('numpy', 'NumPy'), 'pandas': ('pandas', 'pandas'),
               'torch': ('pytorch', 'PyTorch'), 'airflow': ('airflow', 'Airflow')},
    'php': {'laravel/framework': ('laravel', 'Laravel'),
            'symfony/framework-bundle': ('symfony', 'Symfony'),
            'phpunit/phpunit': ('phpunit', 'PHPUnit'),
            'slim/slim': ('slim', 'Slim')},
    'rust': {'tokio': ('tokio', 'Tokio'), 'axum': ('axum', 'Axum'),
             'actix-web': ('actix-web', 'Actix Web'), 'serde': ('serde', 'Serde'),
             'bevy': ('bevy', 'Bevy'), 'tauri': ('tauri', 'Tauri')},
    'dart': {'flutter': ('flutter', 'Flutter')},
    'go': {},
    'dotnet': {},
}

# Configured tooling: a config file names a tool, which is not the same as that
# tool being installed or compatible.
CONFIGURED = (
    ('tsconfig.json', 'typescript', 'TypeScript compiler configuration'),
    ('angular.json', 'angular-cli', 'Angular CLI workspace'),
    ('vite.config.ts', 'vite', 'Vite build configuration'),
    ('next.config.js', 'next', 'Next.js configuration'),
    ('webpack.config.js', 'webpack', 'Webpack configuration'),
    ('jest.config.js', 'jest', 'Jest configuration'),
    ('playwright.config.ts', 'playwright', 'Playwright configuration'),
    ('Dockerfile', 'docker', 'Container build configuration'),
    ('docker-compose.yml', 'docker-compose', 'Compose service configuration'),
    ('Makefile', 'make', 'Make targets'),
    ('CMakeLists.txt', 'cmake', 'CMake build configuration'),
    ('terraform.tf', 'terraform', 'Terraform configuration'),
    ('.github/workflows/ci.yml', 'github-actions', 'CI workflow'),
    ('pytest.ini', 'pytest', 'pytest configuration'),
    ('tox.ini', 'tox', 'tox environments'),
    ('phpunit.xml', 'phpunit', 'PHPUnit configuration'),
    ('Directory.Build.props', 'msbuild', 'MSBuild shared properties'),
    ('build.gradle', 'gradle', 'Gradle build script'),
)

# Manifests whose values are computed rather than declared. Reading them
# establishes that the toolchain is configured, never what version it needs.
COMPUTED = (
    'build.gradle', 'build.gradle.kts', 'settings.gradle', 'webpack.config.js',
    'rollup.config.js', 'next.config.js', 'Directory.Build.props',
    'Directory.Build.targets', 'conftest.py', 'vite.config.ts', 'vite.config.js',
)


def memoized_reader(read):
    """One read per manifest per analysis.

    A single manifest is consulted several times — once per declared
    requirement, once per dependency field and once for the script list — and in
    the committed scope every read is two Git invocations. The latter is the
    same fact established repeatedly, so it is established once and shared.
    """
    cache = {}

    def cached(name, limit=MAX_MANIFEST_BYTES):
        key = (name, limit)
        if key not in cache:
            try:
                cache[key] = read(name, limit)
            except TypeError:
                # A reader that does not take a limit; `_bounded` applies the cap
                # afterwards, and the memo still holds the single read.
                cache[key] = read(name)
        return cache[key]

    return cached


def _bounded(read, name):
    """Read one manifest by name, or None. Never raises for absent or odd files.

    The cap is applied while reading when the caller supports it, so a huge or
    growing file cannot be materialized in full before being refused.
    """
    try:
        data = read(name, MAX_MANIFEST_BYTES)
    except TypeError:
        # A reader that does not take a limit; read then bound, and record that
        # the check happened afterwards rather than silently assuming it did not.
        try:
            data = read(name)
        except (OSError, ValueError, FileNotFoundError):
            return None
    except (OSError, ValueError, FileNotFoundError):
        return None
    if isinstance(data, bytes):
        if len(data) > MAX_MANIFEST_BYTES:
            return None
        return data.decode('utf-8', 'replace')
    return data if isinstance(data, str) else None


def _descend(text, field, load):
    if not KEY.fullmatch(field):
        return None
    try:
        value = load(text)
    except (ValueError, TypeError):
        return None
    for part in field.split('.'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def json_value(text, field):
    """A scalar at a dotted key. A nested object is not a value."""
    value = _descend(text, field, json.loads)
    return value if isinstance(value, (str, int, float)) else None


def toml_value(text, field):
    value = _descend(text, field, tomllib.loads)
    return value if isinstance(value, (str, int, float)) else None


def json_section(text, field):
    """A nested object or array at a dotted key, for enumeration."""
    value = _descend(text, field, json.loads)
    return value if isinstance(value, (dict, list)) else None


def toml_section(text, field):
    value = _descend(text, field, tomllib.loads)
    return value if isinstance(value, (dict, list)) else None


YAML_KEY = re.compile(r'^([A-Za-z0-9_.\-]+):\s*(.*)$')


def yaml_scalar(text, field):
    """A nested scalar from simple YAML, by indentation.

    Deliberately not a YAML parser: it handles `a:` / `  b: value` and nothing
    else, because the alternative is depending on a format this gateway does not
    ship. A value it cannot reach is left unresolved, which is the honest answer.
    """
    if not KEY.fullmatch(field):
        return None
    wanted = field.split('.')
    depth = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.strip().startswith(('-', '?', '%', '|', '>')):
            return None  # block content: not a scalar path
        indent = len(line) - len(line.lstrip(' '))
        if indent != depth * 2:
            if indent < depth * 2:
                depth = indent // 2
            else:
                return None
        found = YAML_KEY.match(line.strip())
        if not found:
            return None
        key, value = found.group(1), found.group(2).strip()
        if key != wanted[depth]:
            if indent == 0:
                depth = 0
            continue
        if depth == len(wanted) - 1:
            if not value:
                return None
            return value.strip('"\'')
        depth += 1
    return None


def gomod_value(text, field):
    """`go 1.21` from go.mod, and nothing else from it."""
    if field != 'go':
        return None
    for raw in text.splitlines():
        found = re.match(r'^go\s+(\d+(?:\.\d+)*)\s*$', raw.strip())
        if found:
            return found.group(1)
    return None


def yaml_keys(text, field):
    """The child keys of a mapping in simple YAML, by indentation.

    `dependencies:` followed by `  flutter:` is how a pubspec names its
    packages. Like `yaml_scalar` this is deliberately not a YAML parser: a
    structure it cannot follow yields nothing, which leaves a framework
    unidentified rather than invented.
    """
    if not KEY.fullmatch(field):
        return None
    lines = text.splitlines()
    indent = 0
    for wanted in field.split('.'):
        found = None
        for position, raw in enumerate(lines):
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            current = len(line) - len(line.lstrip(' '))
            if current < indent:
                return None  # the mapping ended before the key was found
            if current != indent:
                continue
            match = YAML_KEY.match(line.strip())
            if match and match.group(1) == wanted:
                found = position
                break
        if found is None:
            return None
        lines = lines[found + 1:]
        indent += 2
    keys = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        current = len(line) - len(line.lstrip(' '))
        if current < indent:
            break
        if current != indent:
            continue
        match = YAML_KEY.match(line.strip())
        if match:
            keys.append(match.group(1))
    return keys or None


def rust_toolchain_value(text, field):
    if text.lstrip().startswith('['):
        return toml_value(text, 'toolchain.channel')
    value = text.strip()
    return value if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value) else None


READERS = {'json': json_value, 'toml': toml_value, 'yaml': yaml_scalar,
           'rust-toolchain': rust_toolchain_value,
           'gomod': gomod_value, 'text': lambda text, field: text.strip()}
SECTIONS = {'json': json_section, 'toml': toml_section, 'yaml': yaml_keys}
DEPENDENCY_SOURCES = (('package.json', 'json'), ('composer.json', 'json'),
                      ('pyproject.toml', 'toml'), ('pubspec.yaml', 'yaml'))


def _observations(requirement, present, read):
    """Every declaration of one requirement that is actually present.

    A component's files are read through the paths it owns, never by bare name:
    reading `package.json` for a nested component would silently return the
    repository root's, and an accidental root read must never be mistaken for
    inheritance.
    """
    found = []
    for source in requirement.get('sources', ()):
        if source['file'] not in present:
            continue
        path = present[source['file']]
        text = _bounded(read, path)
        if text is None:
            found.append(dict(file=path, field=source.get('field', ''), value=None,
                              role=source['role'], notation=source.get('notation') or requirement.get('notation', ''),
                              unreadable=True,
                              reason=path + ' could not be read or exceeds the size limit.'))
            continue
        value = READERS.get(source.get('kind', 'json'), json_value)(text, source.get('field', ''))
        if value is None or str(value).strip() == '':
            continue
        value = str(value).strip()
        if source.get('minimum') and re.fullmatch(r'\d+(?:\.\d+)*', value):
            value = '>=' + value
        found.append(dict(file=path, field=source.get('field', ''), value=value,
                          role=source['role'], notation=source.get('notation') or requirement.get('notation', ''),
                          unreadable=False, reason=''))
    return found


def declarations(requirement, notation=''):
    """Every readable constraint/selection with its own syntax, across consumers.

    A requirement's headline is a display choice. It cannot replace the other
    observations in host assessment, plan selection or adopted-SDK verification.
    Older records and contextual resolvers may only provide the headline fields.
    """
    syntax = requirement.get('notation') or notation
    found = [(item['value'], item.get('notation') or syntax)
             for item in requirement.get('observations', [])
             if not item.get('unreadable') and item.get('value')
             and item.get('role') in ('constraint', 'selection')]
    for key in ('constraint', 'selected'):
        value = requirement.get(key)
        if value and not any(existing == value for existing, _ in found):
            found.append((value, syntax))
    return list(dict.fromkeys(found))


def _conflicts(requirement, observations):
    """Disagreements inside one requirement's own declarations.

    Two selections, two constraints, or a selection the constraint forbids are
    all contradictions the project states about itself. Reporting the first
    usable value and discarding the rest would hide each of them, and a
    conflict is exactly the thing an operator has to resolve by hand.
    """
    conflicts = []
    for role in ('constraint', 'selection'):
        values = sorted({item['value'] for item in observations
                         if item['role'] == role and not item['unreadable']})
        if len(values) > 1:
            conflicts.append(dict(kind='disagreement', role=role, values=values,
                                  reason='Two declarations of the same requirement disagree: '
                                         + ' and '.join(values) + '.'))
    constraints = [(item['value'], item.get('notation') or requirement.get('notation', ''))
                   for item in observations if item['role'] == 'constraint' and not item['unreadable']]
    selections = sorted({item['value'] for item in observations
                         if item['role'] == 'selection' and not item['unreadable']})
    for constraint, notation in constraints:
        for selected in selections:
            verdict = dependency_constraints.evaluate(constraint, selected,
                                                      notation)
            if verdict == dependency_constraints.VIOLATED:
                conflicts.append(dict(kind='unsatisfied', role='selection', values=[selected],
                                      reason=f'The selected {selected} does not satisfy the declared '
                                             f'{constraint}.'))
    return conflicts


def manifest_reader(root):
    """Bounded regular-file reads through the shared anchored project boundary."""
    from project_access import open_project, relative_path
    root = Path(root)

    def read(name, limit=MAX_MANIFEST_BYTES):
        try:
            relative = relative_path(name)
            with open_project(root, relative) as descriptor:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    return None
                with os.fdopen(os.dup(descriptor), 'rb') as handle:
                    return handle.read(limit + 1)
        except (OSError, ValueError):
            return None

    return read


def requirements_for(ecosystem, present, read, component='.', paths=()):
    """Every requirement one component states, with its competing declarations."""
    resolved = []
    for requirement in ecosystem.get('requirements', ()):
        sources = present
        lookup = None
        if requirement['tool'] in ('flutter', 'rust-toolchain'):
            # FVM and rustup search the invocation directory and its ancestors. Assume
            # the component directory and stop at the repository boundary;
            # neither a gateway cwd nor a host-global pin describes this repo.
            location = PurePosixPath(component)
            search = []
            # rustup prefers the legacy filename when both exist in one directory.
            filenames = ('.fvmrc',) if requirement['tool'] == 'flutter' else ('rust-toolchain', 'rust-toolchain.toml')
            while True:
                search.extend((location / filename).as_posix() for filename in filenames)
                if location == PurePosixPath('.'):
                    break
                location = location.parent
            candidates = [name for name in search if name in paths]
            for filename in reversed(filenames):
                if filename in present and present[filename] not in candidates:
                    candidates.insert(0, present[filename])
            sources = dict(present)
            if candidates:
                for filename in filenames:
                    sources.pop(filename, None)
                sources[PurePosixPath(candidates[0]).name] = candidates[0]
                lookup = dict(directory=component, evidence=candidates[0],
                              shadowed=candidates[1:], boundary='repository root',
                              kind='fvm' if requirement['tool'] == 'flutter' else 'rustup', status='resolved')
                if requirement['tool'] == 'rust-toolchain':
                    text = _bounded(read, candidates[0])
                    try:
                        config = tomllib.loads(text or '').get('toolchain', {})
                        for field in ('components', 'targets', 'profile', 'path'):
                            if field in config:
                                lookup[field] = config[field]
                    except (ValueError, TypeError):
                        pass
        observations = _observations(requirement, sources, read)
        if lookup and not any(item['file'] == lookup['evidence'] and not item['unreadable']
                              for item in observations):
            lookup['status'] = 'unresolved'
            observations.append(dict(file=lookup['evidence'],
                                     field='flutter' if requirement['tool'] == 'flutter' else 'toolchain.channel', value=None,
                                     role='selection', unreadable=True,
                                     reason=('The nearest .fvmrc has no readable Flutter selection; '
                                             if requirement['tool'] == 'flutter' else
                                             'The nearest Rust toolchain file has no readable selection; ')
                                            + 'ancestor pins are not substituted.'))
        readable = [item for item in observations if not item['unreadable']]
        declared = next((item for item in readable if item['role'] == 'constraint'), None)
        constraint = declared['value'] if declared is not None else ''
        # The notation travels with the declaration that was read: a PEP 621
        # range and a Poetry caret in one file are different languages.
        notation = requirement.get('notation', '')
        if declared is not None and declared.get('notation'):
            notation = declared['notation']
        selection = next((item['value'] for item in readable if item['role'] == 'selection'), '')
        conflicts = _conflicts(dict(requirement, notation=notation), observations)
        # Where a declaration lives and whether a requirement was extracted from
        # it are separate facts. An unreadable manifest established a location,
        # not a version.
        established = bool(readable)
        if established:
            reason = ''
        elif observations:
            reason = ' '.join(dict.fromkeys(item['reason'] for item in observations if item['reason']))
        else:
            tried = [source['file'] + ('#' + source['field'] if source.get('field') else '')
                     for source in requirement.get('sources', ())]
            reason = 'No version declaration was found in ' + ', '.join(tried) + '.'
        item = dict(tool=requirement['tool'], label=requirement['label'],
                             purpose=requirement.get('purpose', ''),
                             notation=notation,
                             executable=requirement.get('executable', ''),
                             constraint=constraint, selected=selection,
                             observations=observations, conflicts=conflicts,
                             established=established, reason=reason)
        if lookup:
            item['selection_lookup'] = lookup
        resolved.append(item)
    return resolved


def _scripts(ecosystem, present, read):
    """Commands the repository itself provides, as distinct from suggestions.

    `package.json#scripts` is the project saying how to run its own tests; a
    conventional `npm test` is this gateway proposing something that may not
    exist. The two are labelled differently so a reader can tell them apart.
    """
    names = ecosystem.get('scripts') or ()
    if not names or 'package.json' not in present:
        return []
    text = _bounded(read, present['package.json'])
    if text is None:
        return []
    declared = json_section(text, 'scripts')
    if not isinstance(declared, dict):
        return []
    return [dict(command=['npm', 'run', name], purpose=name,
                 source='package.json#scripts', evidence=present['package.json'])
            for name in names if isinstance(declared.get(name), str)]


def _dependency_names(ecosystem, cwd, read):
    """Package names a component depends on, as framework evidence.

    Reads every manifest shape the registry declares dependencies in, so a
    pubspec's Flutter dependency is reachable rather than registered and never
    looked up.
    """
    names = set()
    for field in ecosystem.get('dependency_fields', ()):
        for candidate, kind in DEPENDENCY_SOURCES:
            if candidate not in cwd:
                continue
            text = _bounded(read, cwd[candidate])
            if text is None:
                continue
            section = SECTIONS[kind](text, field)
            if isinstance(section, dict):
                names.update(str(key).lower() for key in section)
            elif isinstance(section, list):
                names.update(re.split(r'[\s<>=!~\[;]', str(item).lower())[0] for item in section)
    return names


def _frameworks(ecosystem, cwd, read):
    table = FRAMEWORKS.get(ecosystem['id']) or {}
    if not table:
        return []
    found = []
    for name in sorted(_dependency_names(ecosystem, cwd, read)):
        entry = table.get(name)
        if entry:
            found.append(dict(id=entry[0], label=entry[1], evidence='dependencies'))
    return found


def applicability(ecosystem, cwd, read):
    """Whether a component's declared requirements actually apply to it.

    A pure Dart package has a `pubspec.yaml`, so the Dart ecosystem — and with it
    the Flutter requirement — applies by marker alone. Flutter applies only when
    the project depends on it or declares a Flutter version, which is the same
    evidence its framework list uses. Without this, a requirement and a framework
    list could disagree about the same project.
    """
    tools = {requirement['tool'] for requirement in ecosystem.get('requirements', ())}
    if ecosystem['id'] != 'dart':
        return tools
    names = _dependency_names(ecosystem, cwd, read)
    for requirement in ecosystem.get('requirements', ()):
        if requirement['tool'] != 'flutter':
            continue
        # A dependency on Flutter, or a declaration that actually yields a
        # version. `pubspec.yaml` existing is not itself a Flutter declaration.
        declared = any(item['value'] for item in _observations(requirement, cwd, read)
                       if not item['unreadable'])
        if 'flutter' not in names and not declared:
            tools.discard('flutter')
    return tools


def _managers(ecosystem, cwd):
    return [dict(id=manager, lockfile=name, evidence=name)
            for name, manager in sorted(ecosystem.get('managers', {}).items()) if name in cwd]


def component_ecosystems(names):
    """Which registered ecosystems a set of local filenames implicates."""
    lower = {name.lower() for name in names}
    found = []
    for ecosystem in ECOSYSTEMS:
        if any(marker.lower() in lower for marker in ecosystem['markers']):
            found.append(ecosystem)
        elif ecosystem.get('suffix_markers') and any(
                name.endswith(ecosystem['suffix_markers']) for name in lower):
            found.append(ecosystem)
    return found


def ecosystem_evidence(present, ecosystem):
    """Return an actual marker path using the discovery registry's casing rules."""
    markers = {name.lower() for name in ecosystem['markers']}
    suffixes = tuple(name.lower() for name in ecosystem.get('suffix_markers', ()))
    evidence = next((path for name, path in sorted(present.items())
                     if name.lower() in markers or name.lower().endswith(suffixes)), None)
    if evidence is None:
        raise ValueError('No committed manifest evidence for ecosystem ' + ecosystem['id'])
    return evidence


def census(paths):
    """The census' own verdict per path, computed once for the whole project.

    Every scoped view is a projection of this table. Classifying each subset
    separately gave the same file two answers in one response: a header beside a
    project-wide implementation resolved to that language in the coverage
    summary and stayed ambiguous inside the component that owned it, because the
    fallback evidence differed per subset.
    """
    import project_inventory
    return {entry.as_posix(): (kind, value) for entry, kind, value in
            project_inventory.classify_all([PurePosixPath(item) for item in paths])}


def scoped_languages(classification, path, component_paths):
    """Languages of the files this component owns.

    A file belongs to the nearest component above it, so a workspace package's
    sources are not also counted against the workspace root: otherwise every
    component would claim every nested component's code.
    """
    prefix = '' if path == '.' else path + '/'
    nested = tuple(other + '/' for other in component_paths
                   if other != path and other.startswith(prefix))
    owned = [entry for entry in classification
             if (prefix == '' or entry.startswith(prefix)) and not entry.startswith(nested)]
    return sorted(collection_languages(classification, owned, full=True),
                  key=lambda item: (-item['files'], item['id']))


# Not every project has a manifest. A directory of PHP files with no
# composer.json still needs a PHP runtime, and refusing to say so because no
# manifest exists would be the same failure as claiming one. These inferred
# components carry the language as their evidence and leave the version
# unresolved, because nothing declared one.
LANGUAGE_ECOSYSTEM = {
    'php': 'php', 'javascript': 'node', 'typescript': 'node', 'python': 'python',
    'go': 'go', 'rust': 'rust', 'csharp': 'dotnet', 'fsharp': 'dotnet',
    'vbnet': 'dotnet', 'dart': 'dart',
}

BY_ID = {ecosystem['id']: ecosystem for ecosystem in ECOSYSTEMS}


def ownership_roots(directories):
    """The directories that own the files beneath them, from a set of directories.

    A directory owns its own files and a descendant's, but not a sibling's:
    `index.php` beside `classes/User.php` is one project, while `a/index.php`
    and `b/index.php` are two. Choosing the common ancestor of everything would
    report the second pair as one component and invent a boundary that no
    evidence supports.
    """
    roots = []
    for directory in sorted(directories, key=lambda item: (len(item.parts), item.as_posix())):
        if not any(directory == root or root in directory.parents for root in roots):
            roots.append(directory)
    return roots


def common_directory(entries):
    """The directory a set of entries shares, used for the component's label."""
    if not entries:
        return '.'
    roots = ownership_roots({PurePosixPath(entry).parent for entry in entries})
    return roots[0].as_posix() if len(roots) == 1 else '.'


def inferred_components(classification, components):
    """Components for ecosystems evidenced by source but not covered where it sits.

    Ownership is per directory, not global. A managed `app/composer.json` covers
    PHP under `app/`; it says nothing about `legacy/index.php`, which needs its
    own component elsewhere in the tree.
    """
    owners = [(component['path'], {item['id'] for item in component['ecosystems']})
              for component in components]

    def covered(relative, ecosystem_id):
        for path, ecosystems in owners:
            prefix = '' if path == '.' else path + '/'
            if ecosystem_id in ecosystems and relative.startswith(prefix):
                return True
        return False

    pending = {}
    for entry, (kind, language) in classification.items():
        if kind != 'language':
            continue
        ecosystem_id = LANGUAGE_ECOSYSTEM.get(language)
        if not ecosystem_id or covered(entry, ecosystem_id):
            continue
        pending.setdefault(ecosystem_id, []).append(entry)

    components = []
    for ecosystem_id in sorted(pending):
        owned = sorted(pending[ecosystem_id])
        roots = ownership_roots({PurePosixPath(entry).parent for entry in owned})
        ecosystem = BY_ID[ecosystem_id]
        requirement = (ecosystem.get('requirements') or ({},))[0]
        label = requirement.get('label', ecosystem['label'])
        for root in roots:
            # A root owns what sits beneath it; a sibling root is a different
            # group, because nothing established that they are one project.
            scope = [entry for entry in owned
                     if PurePosixPath(entry).parent == root or root in PurePosixPath(entry).parent.parents]
            directory = root.as_posix()
            names = collection_languages(classification, scope)
            reason = (f'{names} source was found in {directory} but no manifest there '
                      'declares a version.')
            components.append(dict(
                path=directory, basis='inferred',
                languages=sorted(collection_languages(classification, scope, full=True),
                                 key=lambda item: (-item['files'], item['id'])),
                ecosystems=[dict(id=ecosystem_id, label=ecosystem['label'])],
                frameworks=[], configured_tooling=[], candidate_commands=[],
                evidence=dict(manifests=[], lockfiles=[]),
                requirements=[dict(
                    tool=requirement.get('tool', ecosystem_id), label=label,
                    purpose=requirement.get('purpose', ''), notation=requirement.get('notation', ''),
                    executable=requirement.get('executable', ''), constraint='', selected='',
                    observations=[], conflicts=[], established=False, reason=reason)],
                unresolved=[dict(what=label + ' version', reason=reason,
                                 evidence=', '.join(sorted(scope)[:EVIDENCE_LIMIT]))]))
    return components


def collection_languages(classification, owned, full=False):
    """The languages a set of files evidences, as a label or as entries.

    A projection of the one census, so a component and the coverage summary
    cannot disagree about the same file.
    """
    import project_inventory
    counts = {}
    for entry in owned:
        kind, language = classification.get(entry, ('unknown', None))
        if kind == 'language':
            counts[language] = counts.get(language, 0) + 1
    labels = {name: label for name, (label, _) in project_inventory.LANGUAGES.items()}
    if not full:
        return ', '.join(labels.get(name, name) for name in sorted(counts))
    return [dict(id=name, label=labels.get(name, name), files=count)
            for name, count in counts.items()]


def dotnet_sdk(component, paths, read, context=None):
    """Resolve the nearest global.json inside this repository.

    CLI lookup starts at the invocation directory, not necessarily the project
    file. Without an invocation we explicitly assume the component directory;
    callers may provide the CLI directory instead. A nearer file shadows every
    ancestor even when it is malformed or has no sdk.version.
    """
    directory = component if context is None else context
    location = PurePosixPath(directory)
    if location.is_absolute() or '..' in location.parts or '\\' in directory or ':' in directory:
        raise ValueError('The .NET lookup context must be inside the repository.')
    search = []
    while True:
        search.append((location / 'global.json').as_posix())
        if location == PurePosixPath('.'):
            break
        location = location.parent
    present = [name for name in search if name in paths]
    evidence = present[0] if present else ''
    resolution = dict(context=dict(directory=directory,
                                  kind='component_directory' if context is None else 'cli_directory'),
                      boundary='repository root', searched=search, evidence=evidence,
                      inherited=bool(evidence and evidence != (PurePosixPath(component) / 'global.json').as_posix()),
                      shadowed=present[1:], status='unresolved', policies={},
                      boundary_note='Host configuration above the repository root is not inspected.')
    requirement = dict(tool='dotnet', label='.NET SDK', purpose='SDK', notation='dotnet',
                       executable='dotnet', constraint='', selected='', observations=[],
                       conflicts=[], established=False, resolution=resolution,
                       reason='No global.json was found within the repository lookup boundary.')
    if not evidence:
        return requirement
    text = _bounded(read, evidence)
    try:
        data = json.loads(text) if text is not None else None
        sdk = data.get('sdk', {}) if isinstance(data, dict) else None
        if not isinstance(sdk, dict):
            raise ValueError('Invalid SDK object')
    except (ValueError, TypeError):
        requirement['reason'] = evidence + ' is unreadable or malformed; shadowed ancestors are not used.'
        requirement['observations'] = [dict(file=evidence, field='sdk', value=None,
                                            role='selection', unreadable=True, reason=requirement['reason'])]
        return requirement
    policies = {key: sdk[key] for key in ('rollForward', 'allowPrerelease', 'paths') if key in sdk}
    resolution['policies'] = policies
    version = sdk.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        requirement['reason'] = evidence + ' does not declare a supported sdk.version; ancestor versions are shadowed.'
        return requirement
    requirement.update(selected=version, established=True, reason='',
                       observations=[dict(file=evidence, field='sdk.version', value=version,
                                          role='selection', unreadable=False, reason='')])
    resolution['status'] = 'resolved'
    # Lookup is resolved, but reporting a selection is not proof that an SDK
    # will be selected on this host. Never flatten roll-forward into a range.
    resolution['assessment'] = 'unresolved'
    resolution['reason'] = ('SDK availability, roll-forward and prerelease policy have not been evaluated '
                            'against installed SDKs for this lookup context.')
    return requirement


HEADLESS_CARGO_SOURCE = 'README#headless-cargo'


def documented_rust_checks(directory, paths, read):
    """Propose bounded headless Cargo commands from fenced project docs.

    Documentation is evidence, never approval. Ignore shell expressions, extra
    arguments and graphical/smoke commands; the operator still selects checks.
    """
    prefix = '' if directory == '.' else directory + '/'
    for name in ('README.md', 'README.markdown'):
        path = prefix + name
        if path not in paths:
            continue
        document = _bounded(read, path)
        if document is None:
            continue
        fenced, commands = False, []
        for line in document.splitlines():
            if line.strip().startswith('```'):
                fenced = not fenced
                continue
            match = re.fullmatch(r'cargo (test|build) --no-default-features', line.strip()) if fenced else None
            if match and match[1] not in {item['purpose'] for item in commands}:
                commands.append(dict(command=['cargo', match[1], '--no-default-features'],
                                     purpose=match[1], source=HEADLESS_CARGO_SOURCE, evidence=path))
        if commands:
            # Either documented operation establishes the feature selection for
            # its companion. Preserve the README dependency on derived checks.
            for purpose in ('test', 'build'):
                if purpose not in {item['purpose'] for item in commands}:
                    commands.append(dict(command=['cargo', purpose, '--no-default-features'],
                                         purpose=purpose, source=HEADLESS_CARGO_SOURCE,
                                         evidence=path, derived=True))
            return commands
    return []


def analyse(root, inventory=None, read=None, lookup_context=None):
    """What this project says about itself, component by component."""
    import project_inventory
    root = Path(root)
    inventory = inventory or project_inventory.scan(root, include_paths=True)
    paths = inventory.get('paths') or []
    if read is None:
        read = manifest_reader(root)
    # One read per manifest and one classification per file, however many
    # requirements, components and coverage rows consult them.
    read = memoized_reader(read)
    classification = census(paths)

    # A component is a directory that carries at least one known manifest. The
    # same directory may carry several, and a workspace carries one per package.
    directories = {}
    for name in inventory['manifests']:
        directories.setdefault(PurePosixPath(name).parent.as_posix(), set()).add(PurePosixPath(name).name)
    truncated = len(directories) > MAX_MANIFESTS
    direct = sorted(directories)[:MAX_MANIFESTS]

    components = []
    for path in direct:
        local = sorted(directories[path])
        ecosystems = component_ecosystems(local)
        if not ecosystems:
            continue
        prefix = '' if path == '.' else path + '/'
        present = {name: prefix + name for name in local}
        component = dict(path=path, basis='manifest',
                         languages=scoped_languages(classification, path, direct),
                         ecosystems=[dict(id=item['id'], label=item['label']) for item in ecosystems],
                         frameworks=[], requirements=[], configured_tooling=[],
                         candidate_commands=[], unresolved=[],
                         evidence=dict(manifests=sorted(present.values()), lockfiles=[]))
        lockfiles, frameworks = set(), {}
        for ecosystem in ecosystems:
            applicable = applicability(ecosystem, present, read)
            # Reuse the census index: ancestor membership must not rescan the
            # entire inventory for each component's lookup directory.
            requirements = ([dotnet_sdk(path, classification, read, lookup_context)]
                             if ecosystem['id'] == 'dotnet' else
                             requirements_for(ecosystem, present, read, path, classification))
            for requirement in requirements:
                if requirement['tool'] not in applicable:
                    continue
                component['requirements'].append(requirement)
                for conflict in requirement['conflicts']:
                    component['unresolved'].append(dict(
                        what=requirement['label'] + ' ' + conflict['kind'],
                        reason=conflict['reason'],
                        evidence=', '.join(item['file'] for item in requirement['observations'])))
                if requirement.get('resolution', {}).get('assessment') == 'unresolved':
                    component['unresolved'].append(dict(what='.NET SDK selection',
                        reason=requirement['resolution']['reason'], evidence=requirement['resolution']['evidence']))
                if not requirement['established']:
                    component['unresolved'].append(dict(
                        what=requirement['label'] + ' version', reason=requirement['reason'],
                        evidence=', '.join(sorted(present.values()))))
            lockfiles.update(entry['lockfile'] for entry in _managers(ecosystem, local))
            for entry in _frameworks(ecosystem, present, read):
                frameworks.setdefault(entry['id'], entry)
            provided = _scripts(ecosystem, present, read)
            if ecosystem['id'] == 'rust':
                provided.extend(documented_rust_checks(path, classification, read))
            declared = {entry['purpose'] for entry in provided}
            component['candidate_commands'].extend(provided)
            checks = ecosystem.get('checks', ())
            if ecosystem['id'] == 'dart' and any(
                    framework['id'] == 'flutter' for framework in frameworks.values()):
                checks = (('test', ('flutter', 'test')), ('analyze', ('flutter', 'analyze')))
            for purpose, command in checks:
                # A candidate command: proposed from convention when the project
                # does not provide one, and labelled as a suggestion.
                if purpose in declared:
                    continue
                component['candidate_commands'].append(dict(
                    command=list(command), purpose=purpose, source='convention',
                    evidence=ecosystem_evidence(present, ecosystem)))
        component['frameworks'] = [frameworks[name] for name in sorted(frameworks)]
        component['evidence']['lockfiles'] = sorted(lockfiles)
        component['configured_tooling'] = [
            dict(id=tool, purpose=purpose, evidence=prefix + name)
            for name, tool, purpose in CONFIGURED if prefix + name in paths][:MAX_CONFIGURED]
        for name in COMPUTED:
            if prefix + name in paths:
                component['unresolved'].append(dict(
                    what=name + ' requirements',
                    reason=name + ' is computed rather than declared; evaluating it would run project code.',
                    evidence=prefix + name))
        component['candidate_commands'] = component['candidate_commands'][:MAX_COMMANDS]
        components.append(component)

    components.extend(inferred_components(classification, components))

    # Markers create components; auxiliary declarations and lockfiles are also
    # understood inputs, without themselves proving another component exists.
    registered = {name.lower() for ecosystem in ECOSYSTEMS for name in (
        *ecosystem['markers'], *ecosystem.get('managers', {}),
        *(source['file'] for requirement in ecosystem['requirements']
          for source in requirement.get('sources', ())))}
    registered.update(PurePosixPath(name).name.lower() for name, _, _ in CONFIGURED)
    registered.update(PurePosixPath(name).name.lower() for name in COMPUTED)
    unregistered = sorted({PurePosixPath(name).name for name in inventory['manifests']
                           if PurePosixPath(name).name.lower() not in registered
                           and not any(PurePosixPath(name).name.lower().endswith(ecosystem.get('suffix_markers', ()))
                                       for ecosystem in ECOSYSTEMS)})
    unresolved = []
    if unregistered:
        unresolved.append(dict(what='unregistered manifests',
                               reason='No registry entry reads these, so their toolchains are not derived.',
                               evidence=', '.join(unregistered)))
    if truncated:
        unresolved.append(dict(
            what='component list',
            reason=f'More than {MAX_MANIFESTS} manifest directories were found; the rest were not analysed.',
            evidence=''))
    if not components and inventory['manifests']:
        unresolved.append(dict(what='components',
                               reason='Manifests were found but none matched a registry entry.',
                               evidence=', '.join(inventory['manifests'][:MAX_CONFIGURED])))
    if not components and not inventory['manifests'] and inventory.get('unknown'):
        # An empty component list is a result, not an explanation. Saying which
        # files were seen and understood makes "nothing recognised" different
        # from "nothing here".
        unresolved.append(dict(
            what='languages',
            reason=f"{inventory['unknown']} files were found and none matched a known language "
                   'or manifest, so no requirement could be derived.',
            # The reason states the total and coverage carries the inspectable
            # sample. Joining that sample here would put fifty paths on one line
            # of a finding, so the illustrative evidence stays at the common
            # bound and the count remains the answer.
            evidence=', '.join((inventory.get('unknown_evidence') or [])[:EVIDENCE_LIMIT])))
    if inventory.get('capped_directories'):
        unresolved.append(dict(
            what='directory listing',
            reason=f"{inventory['capped_directories']} directories exceeded the per-directory limit "
                   f'of {project_inventory.MAX_PER_DIRECTORY} entries and were read only in part.',
            evidence=''))
    ambiguous = inventory.get('ambiguous') or []
    if ambiguous:
        unresolved.append(dict(
            what='language',
            reason='Shared file extensions without resolving evidence: '
                   + '; '.join('/'.join(item['candidates']) + ' (' + str(item['files']) + ')'
                               for item in ambiguous),
            evidence=', '.join(ambiguous[0]['evidence'])))
    return dict(components=components, unresolved=unresolved,
                coverage=dict(files=inventory['files'], directories=inventory['directories'],
                              truncated=inventory['truncated'], skipped=inventory['skipped'],
                              generated=inventory.get('generated', 0),
                              unknown=inventory.get('unknown', 0),
                              unknown_evidence=inventory.get('unknown_evidence', []),
                              languages=inventory['languages'],
                              manifests=inventory['manifests']))
