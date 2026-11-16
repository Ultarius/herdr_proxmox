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


# Every browser suite is compiled on its own before it runs, whatever its
# length, so a suite's cost is its size plus this fixed share.
SUITE_OVERHEAD = 20_000


def browser_shards(directory, total):
    """Split the browser-only Dart suites into ``total`` shards of similar weight.

    Discovery, not a list: a suite is every ``*_test.dart`` annotated
    ``@TestOn('browser')``; the others run on the VM in the web build. Any
    other ``@TestOn`` is refused, because such a suite would run in neither
    place without anyone noticing. Largest suites are placed first, each on the
    lightest shard, and the first shard starts with one average suite of load
    because it also runs the keyboard-navigation check. The result depends only
    on file names and sizes, so every shard job computes the same plan.
    """
    directory = Path(directory)
    suites = []
    for path in sorted(directory.glob('*_test.dart')):
        annotations = re.findall(r'@TestOn\(([^)]*)\)', path.read_text(encoding='utf-8'))
        if not annotations:
            continue
        if annotations != ["'browser'"]:
            raise ValueError(f"{path.name}: only @TestOn('browser') is planned for the browser shards; "
                             'another platform selector would run in no CI job.')
        suites.append((path.stat().st_size + SUITE_OVERHEAD, path.name))
    if type(total) is not int or total < 1:
        raise ValueError('The shard count must be a positive number.')
    if len(suites) < total:
        raise ValueError(f'{len(suites)} browser suite(s) cannot fill {total} shards; lower the shard count.')
    loads = [0] * total
    loads[0] = sum(weight for weight, _ in suites) // len(suites)
    shards = [[] for _ in range(total)]
    for weight, name in sorted(suites, key=lambda suite: (-suite[0], suite[1])):
        lightest = min(range(total), key=lambda index: (loads[index], index))
        loads[lightest] += weight
        shards[lightest].append(name)
    if not all(shards):
        raise ValueError('A browser shard would be empty; lower the shard count.')
    return [sorted(names) for names in shards]


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
    shards = commands.add_parser('browser-shards', help='Print the browser suites one shard runs, one per line.')
    shards.add_argument('--directory', type=Path, required=True)
    shards.add_argument('--index', type=int, required=True, help='Zero-based shard, as in strategy.job-index.')
    shards.add_argument('--total', type=int, required=True)
    args = parser.parse_args(argv)
    if args.action == 'browser-shards':
        plan = browser_shards(args.directory, args.total)
        if not 0 <= args.index < len(plan):
            raise ValueError('The shard index is outside the shard count.')
        # Forward slashes: the paths go to dart test on the runner.
        print('\n'.join((args.directory / name).as_posix() for name in plan[args.index]))
        return 0
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
