"""SDK contracts shared by acquisition, adoption, activation and dispatch.

The registry owns ecosystem knowledge; workers own transactions and permissions.
An activation contract does not imply a trusted publisher installer. Node is the
first opt-in assisted adapter: its provenance remains an agent claim, while its
adopted bytes and contextual probes are independently recorded by the gateway.
"""
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tomllib


@dataclass(frozen=True)
class Adapter:
    capability: str
    executable: str
    commands: tuple
    inventory_arguments: tuple
    context_arguments: tuple
    version_pattern: str
    root_parents: int
    selection_files: tuple = ()
    companion: str = ''
    companion_pattern: str = r'([0-9]+\.[0-9]+\.[0-9]+)'
    resolver: object = None
    unpacker: object = None
    assisted: bool = False
    variables: tuple = ()
    version: int = 1
    dispatch_variables: tuple = ()
    provider: str = 'archive'
    plan_requirement: object = 'environment_requirements:plan_archive'
    materialize_entry: object = None
    evidence_path: object = None
    execution_variables: tuple = ()
    aliases: tuple = ()
    operations: tuple = ()
    automatic_check: object = None
    cache_variables: tuple = ()
    cache_directories: tuple = ()
    candidate_arguments: object = None
    companion_label: str = ''
    capacity_variables: tuple = ()
    component_commands: tuple = ()
    component_verifier: object = None
    build_profiles: tuple = ()
    output_variables: tuple = ()
    download_cache: object = None
    shared_downloads: bool = False
    build_guidance: tuple = ()
    output_guidance: tuple = ()
    capacity_guidance: str = ''
    capacity_installer: object = None
    legacy_output_directories: tuple = ()

    def build_environment(self, entry):
        """Execution settings are frozen with the plan, never inferred for old policies."""
        mode = entry.get('build_mode')
        if mode is None or not self.build_profiles:
            return {}
        profiles = dict(self.build_profiles)
        if mode not in profiles:
            raise ValueError('Unknown build mode for ' + self.capability)
        return dict(profiles[mode])

    def output_environment(self, directory):
        return {key: value.format(outputs=directory) for key, value in self.output_variables}

    def root(self, executable):
        return Path(executable).parents[self.root_parents]

    def environment(self, executable, execution_environment=None):
        variables = self.variables + (self.execution_variables if execution_environment else ())
        return {key: value.format(root=self.root(executable), executable=executable,
                                 environment=execution_environment)
                for key, value in variables}

    def command(self, executable, name):
        if name not in self.commands:
            raise ValueError('No approved command binding for ' + str(name))
        if name == Path(self.executable).name or name in self.aliases:
            return str(executable)
        return str(Path(executable).with_name(name))

    def bound_commands(self, entry):
        """Advertise optional commands only when their receipt probe succeeded."""
        receipt = entry.get('artifact_receipt') or {}
        verified = (receipt.get('probe') or {}).get('components') or {}
        optional = {command: probe for probe, commands in self.component_commands for command in commands}
        return [command for command in self.commands
                if command not in optional or optional[command] in verified]

    def bound_command(self, entry, name):
        """Resolve primary, companion or receipt-verified optional commands."""
        executable = self.command(entry['executables'][0], name)
        if executable == entry['executables'][0]:
            return executable
        optional = {command for _, commands in self.component_commands for command in commands}
        if name in optional and name in self.bound_commands(entry):
            return executable
        companions = [item for item in entry.get('companions', [])
                      if item['name'] == name and executable in item.get('executables', [])]
        if len(companions) != 1:
            raise ValueError('The component has no verified companion binding for ' + name)
        return executable

    def matches_evidence_path(self, entry, observed, receipts):
        if self.evidence_path:
            from environment_providers import invoke
            return invoke(self.evidence_path, entry, observed, receipts) is True
        return observed.get('path') in entry['executables']

    def cache_environment(self, caches, *, create=False):
        caches = Path(caches).absolute()
        if create:
            for name in self.cache_directories:
                (caches / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        return {key: value.format(caches=caches) for key, value in self.cache_variables}


def native_check(item):
    arguments = item['arguments']
    return (item['source'] == 'convention' and len(arguments) in (2, 3)
            and arguments[1] in ('build', 'test'))


def node_check(item):
    return item['source'] == 'package.json#scripts' and item['arguments'] in (
        ['npm', 'run', 'build'], ['npm', 'run', 'test'])


def rust_check(item):
    from project_requirements import HEADLESS_CARGO_SOURCE
    return (item['source'] in ('convention', HEADLESS_CARGO_SOURCE)
            and item['arguments'] in (['cargo', 'test'], ['cargo', 'build'],
                                     ['cargo', 'test', '--no-default-features'],
                                     ['cargo', 'build', '--no-default-features']))


def dotnet_candidate(arguments, directory, paths):
    projects = sorted(name for name in paths if PurePosixPath(name).parent.as_posix() == directory
                      and name.lower().endswith(('.sln', '.slnx', '.csproj', '.fsproj', '.vbproj')))
    solutions = [name for name in projects if name.lower().endswith(('.sln', '.slnx'))]
    projects = solutions or projects
    # Never guess between competing entry points in the same directory.
    return [*arguments, PurePosixPath(projects[0]).name] if len(projects) == 1 else None


def dotnet(version, target, read, artifact):
    url = 'https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/' + '.'.join(version.split('.')[:2]) + '/releases.json'
    data = json.loads(read(url))
    candidates = []
    for release in data.get('releases', []):
        candidates.extend(release.get('sdks', []))
        if isinstance(release.get('sdk'), dict):
            candidates.append(release['sdk'])
    rid = 'linux-' + target['architecture']
    files = [item for sdk in candidates if sdk.get('version') == version
             for item in sdk.get('files', [])
             if item.get('rid') == rid and str(item.get('url', '')).endswith('.tar.gz')]
    if not files:
        raise ValueError(f'The publisher has no .NET SDK {version} artifact for {rid}.')
    item = files[0]
    return url, [artifact(item['url'], item.get('hash'), 'sha512', component='sdk')]


RUST_COMPONENTS = {'rustfmt': 'rustfmt-preview', 'clippy': 'clippy-preview'}


def rust(version, target, read, artifact, *, components=()):
    if not isinstance(components, (list, tuple)) or any(not isinstance(name, str) or name not in RUST_COMPONENTS for name in components):
        raise ValueError('Only rustfmt and clippy are supported optional Rust components.')
    url = 'https://static.rust-lang.org/dist/channel-rust-' + version + '.toml'
    data = tomllib.loads(read(url).decode('utf-8'))
    triple = ('x86_64' if target['architecture'] == 'x64' else 'aarch64') + '-unknown-linux-gnu'
    archives = []
    for package in ('rustc', 'cargo', 'rust-std', *(RUST_COMPONENTS[name] for name in sorted(set(components)))):
        item = data.get('pkg', {}).get(package, {}).get('target', {}).get(triple, {})
        if not item.get('available'):
            raise ValueError(f'Rust {version} has no {package} artifact for {triple}.')
        archives.append(artifact(item.get('xz_url', ''), item.get('xz_hash'), 'sha256', component=package))
    return url, archives


def flat(archive, prefix, stage, index, remaining, unpack):
    return unpack(archive, prefix, remaining)


def rust_components(archive, prefix, stage, index, remaining, unpack):
    extracted = stage / f'unpacked-{index}'
    extracted.mkdir()
    total = unpack(archive, extracted, remaining)
    roots = list(extracted.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError('Rust distribution has an unsupported archive layout.')
    for component in (roots[0] / 'components').read_text(encoding='utf-8').splitlines():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', component):
            raise ValueError('Rust distribution names an invalid component.')
        shutil.copytree(roots[0] / component, prefix, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('manifest.in'))
    return total


def verify_rust_components(specification, root, run=None):
    """Rust publisher packages must supply runnable requested add-ons."""
    runner = subprocess.run if run is None else run
    requested = {entry.get('component') for entry in specification.get('archives', [])}
    result = {}
    for package, command, companion in (('rustfmt-preview', 'rustfmt', 'cargo-fmt'),
                                         ('clippy-preview', 'clippy-driver', 'cargo-clippy')):
        if package not in requested:
            continue
        executable = Path(root) / 'bin' / command
        if not executable.is_file() or not executable.with_name(companion).is_file():
            raise ValueError(f'The requested Rust component {command} is missing.')
        observed = runner([str(executable), '--version'], cwd=root,
                          capture_output=True, text=True, timeout=30)
        if observed.returncode or not re.search(r'\d+\.\d+\.\d+', observed.stdout or ''):
            raise ValueError(f'The requested Rust component {command} did not report a version.')
        result[command] = observed.stdout.strip()[:500]
    return result


ADAPTERS = {
    'python': Adapter('python', 'bin/python3', ('python', 'python3'), ('--version',), ('--version',),
        r'^Python ([0-9]+\.[0-9]+\.[0-9]+)\s*$', 1, ('.python-version',),
        provider='pinned_uv', plan_requirement='environment_python:plan_requirement',
        materialize_entry='environment_python_execution:materialize_entry',
        evidence_path='environment_python_execution:evidence_path', aliases=('python', 'python3'),
        operations=('restore',), automatic_check='environment_python_execution:automatic_check', version=2,
        execution_variables=(('VIRTUAL_ENV', '{environment}'), ('UV_PROJECT_ENVIRONMENT', '{environment}'),
                             ('UV_PYTHON', '{executable}'), ('UV_PYTHON_DOWNLOADS', 'never')),
        variables=(('PYTHONNOUSERSITE', '1'), ('PYTHONDONTWRITEBYTECODE', '1'), ('PYTHONHOME', ''), ('PYTHONPATH', ''), ('PIP_REQUIRE_VIRTUALENV', '1')),
        dispatch_variables=(('PYTHONNOUSERSITE', '1'), ('PYTHONDONTWRITEBYTECODE', '1'), ('PYTHONHOME', ''), ('PYTHONPATH', ''),
                            ('PIP_REQUIRE_VIRTUALENV', '1'), ('UV_PYTHON_DOWNLOADS', 'never'))),
    'dotnet-sdk': Adapter('dotnet-sdk', 'dotnet', ('dotnet',), ('--list-sdks',), ('--version',),
        r'^([0-9]+\.[0-9]+\.[0-9]+)\s+\[', 0, ('global.json',), resolver=dotnet, unpacker=flat,
        variables=(('DOTNET_ROOT', '{root}'), ('DOTNET_MULTILEVEL_LOOKUP', '0')),
        dispatch_variables=(('DOTNET_ROOT', ''), ('DOTNET_MULTILEVEL_LOOKUP', '0')), automatic_check=native_check,
        candidate_arguments=dotnet_candidate, cache_directories=('dotnet',),
        cache_variables=(('DOTNET_CLI_HOME', '{caches}/dotnet'), ('DOTNET_CLI_TELEMETRY_OPTOUT', '1'),
                         ('DOTNET_SKIP_FIRST_TIME_EXPERIENCE', '1'))),
    'rust-toolchain': Adapter('rust-toolchain', 'bin/rustc',
        ('rustc', 'cargo', 'rustfmt', 'cargo-fmt', 'clippy-driver', 'cargo-clippy'), ('-Vv',), ('-Vv',),
        r'^release: ([0-9]+\.[0-9]+\.[0-9]+)$', 1, ('rust-toolchain', 'rust-toolchain.toml'),
        companion='cargo', companion_label='Cargo', companion_pattern=r'^cargo ([0-9]+\.[0-9]+\.[0-9]+)\b',
        resolver=rust, unpacker=rust_components, variables=(('RUSTC', '{executable}'),),
        dispatch_variables=(('RUSTC', ''),), automatic_check=rust_check, version=2,
        cache_directories=('cargo',), cache_variables=(('CARGO_HOME', '{caches}/cargo'),),
        capacity_variables=(('CARGO_BUILD_JOBS', 'HERDR_BUILD_JOBS'), ('RUST_TEST_THREADS', 'HERDR_TEST_THREADS')),
        capacity_installer='build_capacity:install_cargo',
        capacity_guidance='Cargo commands use the session PATH wrapper, which queues heavy work and caps compiler/test parallelism.',
        build_profiles=(('compact', (('CARGO_PROFILE_DEV_DEBUG', 'line-tables-only'),
                                    ('CARGO_PROFILE_TEST_DEBUG', 'line-tables-only'),
                                    ('CARGO_INCREMENTAL', '0'))),
                        ('full', (('CARGO_PROFILE_DEV_DEBUG', '2'),
                                  ('CARGO_PROFILE_TEST_DEBUG', '2'), ('CARGO_INCREMENTAL', '1')))),
        output_variables=(('CARGO_TARGET_DIR', '{outputs}'),), download_cache='package_cache:cargo', shared_downloads=True,
        build_guidance=(('compact', 'Rust compact mode keeps file/line backtraces and disables incremental compilation. '
                         'Assertions and overflow checks retain the project\'s profile settings.'),
                        ('full', 'Rust full mode enables full debugger information and incremental compilation; it uses more disk.')),
        output_guidance=('Use CARGO_TARGET_DIR for compiler output. Do not force Cargo back into target/ or create duplicate target directories.',),
        legacy_output_directories=('target/debug',),
        component_commands=(('rustfmt', ('rustfmt', 'cargo-fmt')),
                            ('clippy-driver', ('clippy-driver', 'cargo-clippy'))),
        component_verifier=verify_rust_components),
    'node': Adapter('node', 'bin/node', ('node', 'npm'), ('--version',), ('--version',),
        r'^v([0-9]+\.[0-9]+\.[0-9]+)\s*$', 1, companion='npm', assisted=True, automatic_check=node_check,
        download_cache='package_cache:npm'),
}


def get(capability):
    adapter = ADAPTERS.get(capability)
    if adapter is None:
        raise ValueError('No SDK adapter exists for ' + str(capability))
    return adapter


def commands():
    return {command: adapter.capability for adapter in ADAPTERS.values() for command in adapter.commands}


def capacity_environment(toolchains, budget):
    """Translate neutral budget signals only for declared, supported adapters."""
    return {variable: budget[source] for entry in toolchains
            if (adapter := ADAPTERS.get(entry.get('name'))) is not None
            for variable, source in adapter.capacity_variables}


def session_tools(toolchains):
    """Bound command metadata for prompts; no executable paths or inferred SDKs."""
    result = []
    for entry in toolchains:
        adapter = ADAPTERS.get(entry['name'])
        record = dict(name=entry['name'], required=entry.get('required'), component=entry.get('component'),
                      commands=adapter.bound_commands(entry) if adapter else [])
        optional = dict(build_mode=entry.get('build_mode'),
                        build_guidance=dict(adapter.build_guidance).get(entry.get('build_mode'), '') if adapter else '',
                        capacity_guidance=adapter.capacity_guidance if adapter else '')
        record.update({key: value for key, value in optional.items() if value})
        result.append(record)
    return result


def install_wrappers(entries, directory):
    """Adapters install their command brokers into one execution-owned PATH root."""
    from environment_providers import invoke
    directories = {invoke(adapter.capacity_installer, directory) for name in dict.fromkeys(entry['name'] for entry in entries)
                   if (adapter := get(name)).capacity_installer}
    directories.discard(None)
    if len(directories) > 1:
        raise ValueError('Adapter command wrappers did not agree on one execution PATH directory.')
    return next(iter(directories), None)


def legacy_output_contracts():
    """Only release-owned adapter declarations may identify old disposable roots."""
    return [(name, relative) for name, adapter in ADAPTERS.items() for relative in adapter.legacy_output_directories]


def execution_caches(entries, caches, *, create=False):
    """Only selected adapters allocate caches or contribute process variables."""
    if create:
        # Shell activation markers also need the execution root when an adapter
        # has no SDK-specific caches (for example, a single Node binding).
        Path(caches).mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {}
    for name in dict.fromkeys(entry['name'] for entry in entries):
        result.update(get(name).cache_environment(caches, create=create))
    return result


def managed():
    import environment_providers
    return tuple(name for name, adapter in ADAPTERS.items()
                 if adapter.resolver is not None or environment_providers.get(adapter.provider).select)
