#!/usr/bin/env python3
"""Real publisher/SDK smoke test. Requires unprivileged Linux glibc x64/arm64.

This is deliberately separate from deterministic unit tests: it downloads real
SDK archives, verifies them, activates receipts and builds disposable projects.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'web/gateway'))
import environment_artifacts
from environment_preparation import EnvironmentPreparation
from project_analysis import ProjectAnalysis
from environment_policy import EnvironmentPolicies
import toolchain_policy
from validation import ValidationRuns


def command(arguments, cwd, environment=None):
    subprocess.run(arguments, cwd=cwd, env=environment, check=True, timeout=600)


def main():
    environment_artifacts.host_target()
    if os.geteuid() == 0:
        raise SystemExit('Run SDK smoke tests as an unprivileged user.')
    with tempfile.TemporaryDirectory(prefix='herdr-real-environments-') as folder:
        root = Path(folder)
        projects = root / 'projects'
        projects.mkdir()
        for name, files in {
            'dotnet': {
                'global.json': '{"sdk":{"version":"8.0.100","rollForward":"disable"}}',
                'Api/App.csproj': '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                    '<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
                'Api/App.cs': 'public class Example { public int Answer => 42; }',
            },
            'rust': {
                'rust-toolchain.toml': '[toolchain]\nchannel="1.97.1"\nprofile="minimal"\n'
                    'components=["rustfmt","clippy"]\n',
                'Cargo.toml': '[package]\nname="herdr-environment-smoke"\nversion="0.1.0"\n'
                    'edition="2021"\nrust-version="1.70"\n',
                'src/lib.rs': '#[test]\nfn works() {\n    let actual: u32 = [2, 2].iter().sum();\n'
                    '    assert_eq!(actual, 4);\n}\n',
            },
        }.items():
            checkout = projects / name
            checkout.mkdir()
            for path, content in files.items():
                destination = checkout / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding='utf-8')
            command(['git', 'init', '-q'], checkout)
            command(['git', 'add', '.'], checkout)
            command(['git', '-c', 'user.name=Herdr smoke', '-c', 'user.email=smoke@example.invalid',
                     'commit', '-qm', 'Pinned smoke fixture'], checkout)
            service = EnvironmentPreparation(ProjectAnalysis(projects), root / 'plans.sqlite3', root / 'sdks')
            plan = service.plan(name)
            selected = [item['id'] for item in plan['check_candidates']
                        if item['purpose'] == ('build' if name == 'dotnet' else 'test')]
            if not selected:
                raise RuntimeError('The native adapter proposed no fixture check.')
            plan = service.select(plan['digest'], selected, 'release_qualification')
            job = service.prepare(plan['digest'])
            service.tick()
            completed = service.store.job(job['id'])
            if completed['state'] != 'ready':
                raise RuntimeError(json.dumps(completed))
            # Exercise the product policy -> contextual probes -> selected
            # native recipes -> publication evidence gate, not a parallel build.
            policies = EnvironmentPolicies(projects, service.store)
            toolchain_policy.PROJECT_RESOLVER = policies.resolve
            runner = ValidationRuns(projects, lambda _: None, root=root / ('validation-' + name))
            run = dict(id='a' * 32, event_id='b' * 64, repository=name, path=str(checkout),
                target=plan['revision'], actor='release_qualification', started_at='qualification')
            output = runner.root / run['id']
            output.mkdir(parents=True)
            runner._execute(run, output)
            if run['state'] != 'complete' or not toolchain_policy.evidence_matches(policies.resolve(checkout), run):
                raise RuntimeError(json.dumps(run))
            print(f'{name}: product validation and publication evidence gate passed', flush=True)
            # Independently repeat offline Cargo / explicit .NET restore as
            # fixture qualification; these are not a substitute for the gate.
            receipt = completed['receipts'][0]
            executable = Path(receipt['probe']['executable'])
            environment = dict(os.environ, PATH=str(executable.parent) + os.pathsep + os.environ['PATH'])
            if name == 'dotnet':
                environment.update(DOTNET_ROOT=str(executable.parent), DOTNET_MULTILEVEL_LOOKUP='0')
                command([str(executable), 'restore', 'Api/App.csproj', '--ignore-failed-sources'], checkout, environment)
                command([str(executable), 'build', 'Api/App.csproj', '--no-restore'], checkout, environment)
            else:
                # Real optional publisher archives and executable probes, on
                # both release architectures; no synthetic tarball or runner.
                packages = {item['component'] for item in receipt['specification']['archives']}
                if not {'rustfmt-preview', 'clippy-preview'}.issubset(packages):
                    raise RuntimeError('Rust optional publisher archives were not acquired.')
                if set(receipt['probe'].get('components', {})) != {'rustfmt', 'clippy-driver'}:
                    raise RuntimeError('Real optional Rust command probes are missing.')
                environment.update(RUSTC=str(executable), CARGO_HOME=str(root / 'cargo'))
                command([str(executable.parent / 'cargo'), 'test', '--offline'], checkout, environment)
                command([str(executable.parent / 'cargo'), 'fmt', '--', '--check'], checkout, environment)
                command([str(executable.parent / 'cargo'), 'clippy', '--offline', '--all-targets', '--', '-D', 'warnings'], checkout, environment)
            print(f'{name}: real SDK preparation and project commands passed', flush=True)


if __name__ == '__main__':
    main()
