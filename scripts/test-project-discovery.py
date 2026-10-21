#!/usr/bin/env python3
"""Run Linux tests with native filesystem prerequisites and no skips.

Windows can test pure constraint/lookup logic, but it cannot prove openat-style
filesystem containment. CI uses --all to discover the complete Python suite;
the default selection is a convenient focused discovery/coordinator check.
"""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'test_project_access', 'test_project_explorer', 'test_project_inventory',
    'test_project_requirements', 'test_project_analysis', 'test_project_toolchains',
    'test_dependency_constraints', 'test_dotnet_resolution',
    'test_integration_coordinator', 'test_environment_plan', 'test_environment_preparation',
    'test_environment_guardrails', 'test_environment_admission', 'test_environment_adoption',
    'test_environment_python', 'test_environment_contracts', 'test_rust_project_environment',
)


def source_fingerprint(root=ROOT):
    """Identify the runner, Python sources/tests and bundled skill guidance.

    This is a scoped source comparison, not an identity for every full-suite
    input or the execution environment (which is reported separately).
    """
    inputs = {root / 'scripts/test-project-discovery.py', root / 'install/python-tools.json',
              *(root / 'web/gateway').rglob('*.py'),
              *(root / 'tests').rglob('*.py'),
              *(root / 'web/gateway/skills').rglob('*.md')}
    digest = hashlib.sha256()
    for path in sorted(inputs):
        name = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(len(content).to_bytes(8, 'big') + content)
    return digest.hexdigest()


def require_symlink_rejection(open_project, root):
    try:
        with open_project(root, Path('child/probe')):
            raise RuntimeError('An ancestor symlink was followed.')
    except ValueError as error:
        if 'contains a symbolic link' not in str(error):
            raise RuntimeError(f'Unexpected ancestor-symlink rejection: {error}') from error


def preflight():
    sys.path.insert(0, str(ROOT / 'web/gateway'))
    from project_access import ANCHORED_ACCESS, open_project
    if sys.platform != 'linux' or not ANCHORED_ACCESS:
        raise RuntimeError('These tests require Linux with real O_NOFOLLOW, dir_fd and descriptor scanning. '
                           'Use the Ubuntu CI job or a disposable Linux checkout; do not mock these capabilities.')
    print(f'Python: {platform.python_version()} | kernel: {platform.release()} | uid: {os.getuid()}', flush=True)
    print(f'Temporary filesystem: {tempfile.gettempdir()}', flush=True)
    for tool in ('git', 'bash'):
        executable = shutil.which(tool)
        if executable is None:
            raise RuntimeError(f'{tool} is required for the discovery/coordinator fixtures.')
        result = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(f'{tool} could not be executed: {executable}')
        print(result.stdout.splitlines()[0], flush=True)

    # Probe the actual temporary filesystem as well as Python's feature flags.
    # Some Windows-mounted filesystems cannot retain an open directory across a
    # rename, even when the process itself runs under a Linux kernel.
    with tempfile.TemporaryDirectory(prefix='herdr-discovery-preflight-') as folder:
        base = Path(folder)
        root = base / 'project'
        child = root / 'child'
        child.mkdir(parents=True)
        outside = base / 'outside'
        outside.mkdir()
        (child / 'probe').write_bytes(b'inside')
        (outside / 'probe').write_bytes(b'outside')
        with open_project(root, Path('child')) as parent:
            child.rename(root / 'original')
            child.symlink_to(outside, target_is_directory=True)
            descriptor = os.open('probe', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(descriptor, 'rb') as stream:
                if stream.read() != b'inside':
                    raise RuntimeError('The temporary filesystem redirected an anchored directory read.')
        require_symlink_rejection(open_project, root)
    print('Native ancestor-rename and no-follow probe: PASS', flush=True)

    # HEAD alone does not identify an uncommitted test run. Include the current
    # source bytes so local evidence can be compared with a later CI checkout.
    print(f'Source fingerprint (runner, gateway, Python tests and skill Markdown): {source_fingerprint()}', flush=True)


def load_suite(all_tests=False, root=ROOT):
    sys.path.insert(0, str(root / 'tests'))
    loader = unittest.TestLoader()
    if all_tests:
        return loader.discover(str(root / 'tests'))
    return unittest.TestSuite(loader.loadTestsFromName(name) for name in MODULES)


def run_suite(suite, verbose=False):
    result = unittest.TextTestRunner(verbosity=2 if verbose else 1).run(suite)
    if result.skipped:
        print('NOT VERIFIED: the Linux suite skipped tests:', file=sys.stderr)
        for test, reason in result.skipped:
            print(f'  {test.id()}: {reason}', file=sys.stderr)
    if result.testsRun == 0:
        print('NOT VERIFIED: no tests were discovered.', file=sys.stderr)
    return 0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight-only', action='store_true', help='Check the real Linux test prerequisites only.')
    parser.add_argument('--all', action='store_true', help='Discover every Python test; any skip fails the run.')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args(argv)
    try:
        preflight()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'NOT VERIFIED: {error}', file=sys.stderr)
        print('Use a native Linux temporary directory and installed Git/Bash; never bypass the boundary guard.', file=sys.stderr)
        return 2
    if args.preflight_only:
        return 0
    return run_suite(load_suite(args.all), args.verbose)


if __name__ == '__main__':
    raise SystemExit(main())
