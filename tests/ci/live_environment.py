#!/usr/bin/env python3
"""Pinned real-daemon, real-SDK qualification with an owned credential-free agent.

Runs only on the release qualification executors, never against a user's session.
The stub supplies terminal readiness; SDK acquisition, admission, process binding,
native validation and publication evidence use the production gateway code.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'web/gateway'))
from environment_admission import EnvironmentAdmission
from environment_preparation import EnvironmentPreparation
from environment_policy import EnvironmentPolicies
from organizations import OrganizationStore
from project_analysis import ProjectAnalysis
from validation import ValidationRuns
from contributions import Contributions
import toolchain_policy


def stub():
    if '--version' in sys.argv:
        print('codex-cli 0.0.1')
        return
    import tty
    tty.setraw(sys.stdin.fileno())
    print('OpenAI Codex (v0.0.1)\r\nmodel: qualification\r\n› ', flush=True)
    subprocess.run([os.environ['HERDR_QUALIFICATION_BIN'], 'pane', 'report-agent', os.environ['HERDR_PANE_ID'],
        '--source', 'environment-qualification', '--agent', 'codex', '--state', 'idle'],
        check=True, stdout=subprocess.DEVNULL)
    for line in sys.stdin:
        if line.strip() == 'exit':
            break


def download(destination):
    catalog = json.loads(Path(__file__).with_name('herdr-runtime.json').read_text())
    key = 'linux-' + platform.machine().lower()
    asset = catalog['assets'][key]
    digest = hashlib.sha256()
    count = 0
    with urlopen(asset['url'], timeout=90) as response, destination.open('wb') as stream:
        if not response.url.startswith(('https://github.com/', 'https://release-assets.githubusercontent.com/')):
            raise ValueError('Unexpected Herdr artifact download destination.')
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > 128 * 1024 * 1024:
                raise ValueError('Herdr artifact exceeds the qualification download limit.')
            digest.update(block)
            stream.write(block)
    if digest.hexdigest() != asset['sha256']:
        raise ValueError('The pinned Herdr artifact checksum does not match.')
    destination.chmod(0o700)
    version = subprocess.check_output([str(destination), '--version'], text=True, timeout=10)
    if catalog['version'] not in version:
        raise ValueError('The downloaded Herdr version does not match the pinned qualification runtime.')


@contextmanager
def runtime(root):
    binary = root / 'herdr'
    download(binary)
    previous = dict(os.environ)
    bindir = root / 'bin'
    bindir.mkdir()
    # Own executable, own HOME and socket. No paid model or production account.
    executable = bindir / 'codex'
    # Keep the canonical agent executable name in the process argv. A shell
    # wrapper that execs an unrelated script loses Herdr's foreground identity.
    executable.write_text('#!' + sys.executable + '\nimport runpy, sys\nsys.argv.insert(1, "--stub")\n'
                          + 'runpy.run_path(' + repr(str(Path(__file__).absolute())) + ', run_name="__main__")\n')
    executable.chmod(0o700)
    directories = {key: str(root / name) for key, name in (
        ('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
        ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache'), ('XDG_RUNTIME_DIR', 'run'))}
    for directory in directories.values():
        Path(directory).mkdir(mode=0o700)
    config = root / 'herdr.toml'
    config.write_text('')
    os.environ.update(directories, HERDR_SOCKET_PATH=str(root / 'herdr.sock'),
                      HERDR_SESSION='environment-qualification', HERDR_QUALIFICATION_BIN=str(binary),
                      HERDR_CONFIG_PATH=str(config), SHELL=shutil.which('bash') or '/bin/sh', TERM='xterm-256color',
                      PATH=str(bindir) + os.pathsep + previous.get('PATH', ''))
    log = (root / 'daemon.log').open('w')
    daemon = subprocess.Popen([str(binary), 'server'], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    def command(*arguments, **options):
        result = subprocess.run([str(binary), *arguments], capture_output=True, text=True,
                                timeout=options.get('timeout', 20))
        if result.returncode:
            raise ValueError(result.stderr or result.stdout)
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError:
            if arguments[:2] in (('pane', 'run'), ('pane', 'close')):
                return {'admitted': True}
            raise ValueError('Unexpected response from ' + ' '.join(arguments[:2]) + ': ' + result.stdout[:200])
        if 'error' in document:
            raise ValueError(str(document['error']))
        return document.get('result', document)
    try:
        until = time.monotonic() + 20
        while True:
            try:
                command('workspace', 'list')
                break
            except (ValueError, OSError):
                if time.monotonic() >= until:
                    raise
                time.sleep(.1)
        yield command
    finally:
        try:
            command('server', 'stop')
            daemon.wait(timeout=10)
        except (ValueError, OSError, subprocess.SubprocessError):
            daemon.terminate()
            daemon.wait(timeout=10)
        log.close()
        os.environ.clear()
        os.environ.update(previous)


def qualify(root, command, name, files, assisted=False):
    from types import SimpleNamespace
    os.environ['HERDR_WORKSPACE_STORAGE_ROOT'] = str(root / 'build-storage')
    projects = root / 'projects'
    projects.mkdir(exist_ok=True)
    repository = projects / name
    repository.mkdir()
    for path, content in files.items():
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    def git(*arguments):
        return subprocess.check_output(['git', '-C', str(repository), *arguments], text=True).strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Qualification', '-c', 'user.email=qualification@example.invalid', 'commit', '-qm', 'fixture')
    preparation = EnvironmentPreparation(ProjectAnalysis(projects), root / (name + '-plans.sqlite3'), root / 'sdks')
    organizations = OrganizationStore(root / (name + '-org.sqlite3'), projects, command)
    organizations.contributions = SimpleNamespace(policy=lambda _: dict(auto_prepare=True,
        auto_prepare_checks=True, auto_prepare_agent=assisted, auto_prepare_revision='release-qualification'))
    admission = EnvironmentAdmission(organizations, preparation)
    organizations.environment_admission = admission
    preparation.admission = admission
    policies = EnvironmentPolicies(projects, preparation.store)
    previous = toolchain_policy.PROJECT_RESOLVER
    toolchain_policy.PROJECT_RESOLVER = policies.resolve
    try:
        org = organizations.action('save', dict(request_id='qualification-org', name='Qualification', purpose='Test', instructions='Test'))['id']
        profile = organizations.action('hire', dict(request_id='qualification-agent', organization_id=org,
            name='Qualifier', role='Engineer', runtime='codex', persona='Qualification', project=str(repository), manager_id=''))['id']
        identity = organizations.action('launch', dict(request_id='qualification-launch', organization_id=org, profile_id=profile))['id']
        organizations.wait_idle(timeout=20)
        admission.tick()
        if assisted:
            waiter = preparation.store.waiter(identity)
            helper = preparation.store.assistance(waiter['id'])
            if not helper or helper['state'] != 'monitoring':
                raise RuntimeError('The dedicated preparation agent was not admitted: ' + json.dumps(dict(helper=helper, jobs=organizations.job_records())))
            # The credential-free CLI stub never installs software. This fixture
            # qualifies the real daemon protocol, not Node distribution trust.
            allocation = organizations.path.parent / 'environment-assistance' / helper['id']
            source = preparation.store.get(helper['plan_digest'])
            tools = []
            for index, requirement in enumerate(source['requirements']):
                if requirement.get('preparation_method') != 'agent':
                    continue
                prefix = allocation / f'sdk-{index}' / 'bin'
                prefix.mkdir()
                for tool, version in (('node', 'v20.19.0'), ('npm', '10.8.0')):
                    executable = prefix / tool
                    executable.write_text('#!/bin/sh\necho ' + version + '\n')
                    executable.chmod(0o755)
                tools.append(dict(index=index, provenance=dict(source='https://example.invalid/fixture', method='qualification fixture')))
            (allocation / 'result.json').write_text(json.dumps(dict(token=helper['token'], plan_digest=source['digest'], tools=tools)))
            admission.tick()
        preparation.tick()
        admission.tick()
        organizations.wait_idle(timeout=100)
        job = next(item for item in organizations.job_records() if item['id'] == identity)
        if job['state'] != 'persona_sent' or job.get('environment_binding', {}).get('state') != 'inherited':
            raise RuntimeError(json.dumps(job))
        runner = ValidationRuns(projects, lambda _: None, root=root / ('validation-' + name))
        frozen = job['toolchain_policy']
        # All SDK qualifications share the storage registry. Each validation
        # needs its own execution identity, just like a submitted gateway run.
        run_id = uuid.uuid4().hex
        run = dict(id=run_id, event_id=hashlib.sha256(run_id.encode()).hexdigest(), repository=name, path=str(repository),
            target=job['start_sha'], actor='qualification', started_at='qualification',
            frozen_plan_digest=frozen['plan_digest'], frozen_policy_digest=frozen['digest'])
        output = runner.root / run['id']
        output.mkdir(parents=True)
        runner._execute(run, output)
        policy = policies.resolve(repository)
        contribution = Contributions.__new__(Contributions)
        contribution.store = organizations
        contribution.path_for = lambda task: repository
        task = dict(run_id=identity, organization_id=org)
        if run['state'] != 'complete' or not contribution.policy_matches(task, run):
            raise RuntimeError(json.dumps(run))
        for invalid in (dict(run, plan_digest='wrong'), dict(run, toolchain_evidence=[])):
            if contribution.policy_matches(task, invalid):
                raise RuntimeError('Publication accepted missing or mismatched environment evidence.')
        detail = 'real daemon assisted adoption and binding with fixture SDK passed' if assisted else 'real daemon admission, SDK inheritance, native validation and evidence gate passed'
        print(name + ': ' + detail, flush=True)
    finally:
        organizations.close()
        toolchain_policy.PROJECT_RESOLVER = previous


def main():
    import environment_artifacts
    environment_artifacts.host_target()
    if os.geteuid() == 0:
        raise ValueError('Live qualification must run unprivileged.')
    with tempfile.TemporaryDirectory(prefix='herdr-live-environment-') as folder:
        root = Path(folder)
        with runtime(root) as command:
            qualify(root, command, 'dotnet', {
                'global.json': '{"sdk":{"version":"8.0.100","rollForward":"disable"}}',
                'Api/App.csproj': '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
                'Api/App.cs': 'public class Example {}'})
            qualify(root, command, 'node-protocol', {
                'package.json': json.dumps(dict(engines=dict(node='>=20 <23'), scripts=dict(test='echo fixture')))}, assisted=True)
            qualify(root, command, 'rust', {
                'rust-toolchain.toml': '[toolchain]\nchannel="1.85.1"\nprofile="minimal"\n',
                'Cargo.toml': '[package]\nname="qualification"\nversion="0.1.0"\nedition="2021"\nrust-version="1.70"\n',
                'src/lib.rs': '#[test] fn works() { assert_eq!(2 + 2, 4); }'})


if __name__ == '__main__':
    stub() if '--stub' in sys.argv else main()
