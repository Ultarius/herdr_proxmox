#!/usr/bin/env python3
"""Shared CI runtime selection and small check summaries; no feature test lists."""
import argparse
import json
from pathlib import Path
import re
import sys
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]


def runtime(root=ROOT, native=None, *, select_only=False):
    """The declared Python floor owns compatibility coverage, not workflow YAML."""
    if tomllib is None:
        raise ValueError('The CI interpreter is older than Python 3.11; bootstrap the configured primary interpreter first.')
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    requirement = project.get('requires-python', '')
    match = re.fullmatch(r'>=([0-9]+)\.([0-9]+)', requirement)
    if match is None:
        raise ValueError('CI expects requires-python = ">=major.minor"; review runtime coverage when changing this contract.')
    minimum = tuple(int(value) for value in match.groups())
    native = tuple(sys.version_info[:2] if native is None else native)
    if native < minimum and not select_only:
        raise ValueError('The primary CI interpreter is older than requires-python; update its runner or interpreter.')
    return dict(minimum='.'.join(map(str, minimum)), native='.'.join(map(str, native)),
                compatibility_needed='true' if minimum != native else 'false')


def summarize(report, destination, label):
    """Report measured phases, without copying command lines or tool output."""
    report, destination = Path(report), Path(destination)
    if not report.exists():
        content = f'### {label}\n\nNo check timing report was produced; inspect the job log.\n'
    else:
        if report.is_symlink() or not report.is_file() or report.stat().st_size > 500_000:
            raise ValueError('CI timing report must be a bounded regular file.')
        records = [json.loads(line) for line in report.read_text(encoding='utf-8').splitlines() if line.strip()]
        content = f'### {label}\n\n| Check | Result | Seconds |\n| --- | --- | ---: |\n'
        for record in sorted(records, key=lambda value: value['duration_ms'], reverse=True):
            name = str(record['id']).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
            status = record['status']
            if status not in ('passed', 'failed', 'unavailable', 'not_run'):
                raise ValueError('Unknown CI check status.')
            duration = record['duration_ms']
            if type(duration) is not int or duration < 0:
                raise ValueError('Invalid CI check duration.')
            content += f'| {name} | {status} | {duration / 1000:.2f} |\n'
        content += '\nDurations are measured phases, not a full job duration or a coverage verdict.\n'
    with destination.open('a', encoding='utf-8') as stream:
        stream.write(content + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    selection = commands.add_parser('runtime')
    selection.add_argument('--github-output', type=Path)
    selection.add_argument('--select-only', action='store_true', help='Select the floor before installing it; do not certify this bootstrap runtime.')
    summary = commands.add_parser('summarize')
    summary.add_argument('--report', type=Path, required=True)
    summary.add_argument('--summary', type=Path, required=True)
    summary.add_argument('--label', required=True)
    args = parser.parse_args(argv)
    if args.action == 'runtime':
        values = runtime(select_only=args.select_only)
        print(json.dumps(values, sort_keys=True))
        if args.github_output:
            with args.github_output.open('a', encoding='utf-8') as stream:
                stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    else:
        summarize(args.report, args.summary, args.label)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
