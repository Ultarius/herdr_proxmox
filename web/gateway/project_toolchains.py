"""A project's declared tool requirement, assessed against this host.

Three answers, kept apart because they fail separately:

* the **requirement** — what the project or its operator states, in a notation
* the **resolution** — every executable that could serve it, and what each measured
* the **assessment** — whether a measurement satisfies the constraint, and
  whether this release could install something that would

There is exactly one interpretation of a constraint in this gateway:
`dependency_constraints.evaluate`, dispatched on the requirement's notation. An
override used to be compared by a private prefix matcher here, which meant the
same string could mean two things depending on which code path read it.

An installer *existing* is not the same as an installer being able to *satisfy*
the requirement. The release pins one version; a project may need another, and
only a matching pair authorizes automatic provisioning.
"""
from pathlib import Path
import re
import shutil
import subprocess

import dependency_constraints

NAME = re.compile(r'[a-z][a-z0-9_.+-]{0,63}')
PROBE_TIMEOUT = 20
OUTPUT_LIMIT = 4000

# How a tool is asked for its version. Most answer `--version`; `go` selects its
# version through a subcommand, and a probe that fails is reported as
# unmeasured rather than guessed at. A tool with no entry gets the common form.
PROBES = {'go': ('version',)}
DEFAULT_PROBE = ('--version',)
# The requirement identifies a runtime, not necessarily its command spelling.
# Debian commonly provides python3 without a python alias; retain both probes
# rather than declaring the runtime absent or assuming they are the same SDK.
EXECUTABLES = {'python': ('python3', 'python'),
               'rust': ('rustc',), 'rust-toolchain': ('rustc',)}


def probe_arguments(name):
    """The arguments that make a tool report its version."""
    return PROBES.get(name, DEFAULT_PROBE)

# The notations a requirement can be stated in. Shared with the evaluator so an
# override cannot name one the gateway cannot interpret.
NOTATIONS = tuple(sorted(set(dependency_constraints.OPERATORS) - {'python-version'}))
DEFAULT_NOTATION = 'semver'
ORIGIN = 'operator_override'


def tool_name(value):
    """A declarable tool identifier."""
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Name a tool as a lowercase identifier, for example php or flutter.')
    return value


def tool_constraint(value):
    """A declared constraint. Empty means the project states none."""
    if value is None or str(value).strip() == '':
        return ''
    text = str(value).strip()
    if len(text) > 120 or '\x00' in text or any(c in text for c in '\n\r'):
        raise ValueError('Declare a version or range under 120 characters, or leave it empty.')
    return text


def tool_notation(value):
    if value is None or value == '':
        return DEFAULT_NOTATION
    if not isinstance(value, str) or value not in NOTATIONS:
        raise ValueError('Choose a supported version notation: ' + ', '.join(NOTATIONS) + '.')
    return value


def record(body, actor, stamp, repository):
    """One operator override, in the shape every requirement shares.

    Legacy records carry `version` and no notation; they are upgraded on read
    rather than rewritten, so an existing declaration keeps its meaning.
    """
    return dict(repository=repository, origin=ORIGIN, toolchain=tool_name(body.get('toolchain')),
                constraint=tool_constraint(body.get('constraint', body.get('version'))),
                notation=tool_notation(body.get('notation')),
                actor=actor, at=stamp)


def normalized(saved):
    """A stored record in the current shape, whoever wrote it."""
    if not saved:
        return None
    return dict(saved,
                constraint=saved.get('constraint', saved.get('version', '')),
                notation=saved.get('notation', DEFAULT_NOTATION),
                origin=saved.get('origin', ORIGIN),
                actor=saved.get('actor', saved.get('declared_by', '')),
                at=saved.get('at', saved.get('declared_at', '')))


def locate(name, entry, which=None):
    """Every executable that could serve this tool, release first.

    The release's own path is version-pinned, so it exists only when that exact
    SDK was installed. A PATH candidate is an available option, not proof that a
    build will use it when the configured environment selects something else.
    """
    which = shutil.which if which is None else which
    found = []
    configured = (entry or {}).get('executable')
    if isinstance(configured, str) and configured:
        found.append(dict(kind='release', path=configured,
                          present=Path(configured).is_file(), measured=None, error=''))
    for command in EXECUTABLES.get(name, (name,)):
        on_path = which(command)
        if on_path and on_path not in [item['path'] for item in found]:
            found.append(dict(kind='path', path=on_path, present=True, measured=None, error=''))
    return found


def measure(name, executable, run=None):
    """What an installed executable actually reports, or why it could not say."""
    if not executable:
        return None, 'No executable was found.'
    runner = subprocess.run if run is None else run
    try:
        result = runner([str(executable), *probe_arguments(name)], capture_output=True, text=True,
                        timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return None, f'{name} could not be run: {error}'[:300]
    output = ((result.stdout or '') + (result.stderr or ''))[:OUTPUT_LIMIT]
    found = dependency_constraints.VERSION_ONLY.search(output)
    if result.returncode or not found:
        return None, f'{name} did not report a readable version.'
    return found.group(0), ''


def offer_for(name, constraint, notation, entry):
    """What this release could install, and whether that would satisfy the requirement."""
    if not entry:
        return None
    version = str(entry.get('required') or '')
    if not version:
        return dict(version='', satisfies=False,
                    reason=f'This release declares no version for {name}, so it cannot install it.')
    verdict = (dependency_constraints.evaluate(constraint, version, notation)
               if constraint else dependency_constraints.SATISFIED)
    satisfies = verdict == dependency_constraints.SATISFIED
    return dict(version=version, satisfies=satisfies,
                reason='' if satisfies else
                       f'This release installs {name} {version}, which does not satisfy {constraint}.')


def environment(name, constraint, notation, entry, catalog, which=None, run=None):
    """What this host offers for one requirement, apart from who stated it.

    Separated from `assess` so the answer can be cached: it depends on the tool,
    the constraint and the host, never on the project that asked. Two projects
    requiring the same range share one measurement.
    """
    candidates = locate(name, entry, which)
    for candidate in candidates:
        if not candidate['present']:
            candidate['error'] = 'The configured executable is not installed.'
            continue
        candidate['measured'], candidate['error'] = measure(name, candidate['path'], run)
    usable = next((item for item in candidates if item['measured']), None)
    measured = usable['measured'] if usable else None
    if usable is None:
        verdict = 'missing'
        detail = (f'No {name} executable answered a version request. '
                  + ' '.join(sorted({item['error'] for item in candidates if item['error']})))
    else:
        verdict = dependency_constraints.evaluate(constraint, measured, notation)
        detail = {
            'satisfied': f'{name} {measured} satisfies {constraint}.'
                         if constraint else
                         f'{name} {measured} is installed; the project declares no constraint.',
            'violated': f'{name} {measured} does not satisfy {constraint}.',
            'undecidable': f'{name} {measured} was measured, but {constraint} could not be '
                           f'evaluated in {notation} notation.',
        }[verdict]
    available = bool(entry) and name in (catalog or {})
    offer = offer_for(name, constraint, notation, entry) if available else None
    return dict(candidates=candidates, selected=usable['path'] if usable else '',
                measured=measured, verdict=verdict, detail=detail.strip(),
                installer=available,
                # Only a pair that would actually satisfy the requirement may
                # authorize an installation. Existence is not satisfaction.
                provisionable=bool(offer and offer['satisfies']),
                offer=offer)


def assess(saved, entry=None, catalog=None, which=None, run=None):
    """Assess one requirement against this host, with every candidate measured."""
    requirement = normalized(saved)
    return dict(requirement, **environment(requirement['toolchain'], requirement['constraint'],
                                           requirement['notation'], entry, catalog, which, run))


def snapshot(records, entries=None, catalog=None, which=None, run=None):
    """Every override, assessed, with the release's own scope kept separate."""
    entries = entries or {}
    projects = {}
    for repository, saved in sorted((records or {}).items()):
        requirement = normalized(saved)
        projects[repository] = assess(saved, entries.get(requirement['toolchain']),
                                      catalog, which, run)
    return dict(projects=projects,
                # Herdr's own scope, labelled so a client cannot present it as a
                # requirement of a project the operator selected.
                release_scope='Herdr build tools requested by the deployed release.',
                installed=sorted(entries),
                notations=sorted(NOTATIONS))
