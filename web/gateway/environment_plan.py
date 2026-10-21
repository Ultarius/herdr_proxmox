"""A project environment plan: what preparing this repository would involve.

The plan is the boundary between discovering a requirement and executing
anything. It is built from the resolved requirement report and the capability
registry, names one exact version per requirement, and states for each whether
Herdr can reuse, acquire or must refuse it. Building a plan executes nothing:
it reads declarations, consults the registry and hashes the result. Nothing here
downloads, installs, writes an environment or spawns a subprocess, which is what
makes a plan safe to compute on a dashboard poll.

Three decisions this module makes, and why they are the ones that matter:

* **A version is chosen once and frozen.** Discovery keeps reporting new
  upstream versions; an active task does not silently roll to a newer release.
  The selection is derived from the requirement and the available artifacts, so
  the same inputs always yield the same plan and the same digest.
* **Missing and absent are different.** A requirement Herdr can satisfy only by
  acquiring is `acquire`, not `blocked`; a requirement no adapter can serve is
  `unsupported`, with the remedy recorded. Neither is a failure to hide, and
  neither is collapsed into the other.
* **The digest covers what can affect execution.** Every field that could change
  what a worker would do is included, so two plans with the same digest are
  interchangeable and a different digest means a different environment. Free-text
  timestamps are excluded for exactly that reason.

This is work package 1 of the automatic-environments plan: contracts,
selection and identity. Acquisition (work package 2) and durable preparation
(work package 3) consume this plan and are deliberately not implemented here.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path

import dependency_constraints
import environment_capabilities
import project_requirements

# Descriptive schema version, not a compatibility gate. The digest identifies
# executable policy changes; adapter versions enforce SDK contract compatibility.
PLAN_VERSION = 2

# States a single capability requirement can be in. These are the only words a
# plan uses for "what happens next", so a dashboard and a worker cannot disagree
# about whether something is installable.
READY = 'ready'            # an installed version satisfies the constraint
ACQUIRE = 'acquire'        # a version exists to install and would satisfy it
UNSUPPORTED = 'unsupported'  # Herdr cannot obtain a satisfying version here
UNRESOLVED = 'unresolved'  # no exact version could be chosen from the evidence

STATES = (READY, ACQUIRE, UNSUPPORTED, UNRESOLVED)

# Internal outcome of selection, before the capability is taken into account: the
# requirement names a version Herdr cannot obtain. Whether that is `unsupported`
# or `unresolved` depends on the capability, so it is decided one step up.
UNOBTAINABLE = 'unobtainable'

EXACT = re.compile(r'\d+(?:\.\d+)*')


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(plan):
    """A stable identity for a plan, covering every execution-affecting field.

    `at` is the only field excluded: it records when the plan was built, not what
    it would do, so two builds of the same intent must share a digest. Every
    other field participates, including prose, because a worker that is told a
    different reason has been handed a different plan.
    """
    payload = {key: value for key, value in plan.items() if key not in ('digest', 'at')}
    return hashlib.sha256(canonical(payload).encode('utf-8')).hexdigest()


def _exact(value):
    """Whether a string is a single exact version rather than a range."""
    text = str(value or '').strip()
    return bool(text) and bool(EXACT.fullmatch(text))


def _satisfies(version, constraint, notation):
    if not version:
        return dependency_constraints.UNDECIDABLE
    return dependency_constraints.evaluate(constraint or '', version, notation)


def _artifact_versions(release_entries, tool):
    """Exact versions Herdr could install for a tool, from the release catalog.

    Installed versions and downloadable versions are different sets; this is the
    downloadable one, and it is deliberately separate from any PATH measurement.
    """
    entry = (release_entries or {}).get(tool)
    if not entry or not entry.get('acquirable') or not _exact(entry.get('version')):
        return []
    return [entry['version']]


def _pick_constraint(requirement):
    """The governing constraint for one requirement, from its own evidence.

    Precedence is deliberate and fixed: an operator override wins, then a
    repository-declared constraint, then a single unambiguous selection. A range
    that is merely compatible (`>=20 <23`) is still a constraint and is
    evaluated as one; an exact pin is a selection and is compared as one.
    """
    if requirement.get('override'):
        return requirement['override'].get('constraint') or '', 'override'
    discovered = requirement.get('discovered') or []
    for item in discovered:
        if item.get('constraint'):
            return item['constraint'], 'repository_constraint'
    selections = sorted({item['selected'] for item in discovered if item.get('selected')})
    if len(selections) == 1:
        return selections[0], 'repository_selection'
    return '', 'none'


def _select_version(requirement, installed, artifacts):
    """Choose one exact version for a requirement, or report why none can be chosen.

    The installed set is preferred only when it satisfies the constraint: an
    already-installed version that misses the project's range is not a usable
    environment, and quietly keeping it would be the exact overstatement this
    work exists to prevent. `artifacts` are versions the release can install,
    which is a different set from what is installed, and the two are never merged.
    """
    constraint, basis = _pick_constraint(requirement)
    notation = requirement.get('notation') or 'semver'
    conflicts = [item for item in requirement.get('conflicts') or [] if item.get('kind')]
    override = requirement.get('override')
    if override:
        conflicts = [item for item in conflicts if item.get('kind') != 'override']
    declarations = ([(override['constraint'], override.get('notation') or notation)] if override else
                    [declaration for item in requirement.get('discovered') or []
                     for declaration in project_requirements.declarations(item, notation)])

    def satisfies(version):
        return all(_satisfies(version, value, syntax) == dependency_constraints.SATISFIED
                   for value, syntax in declarations)

    def result(version, state, reason, selected_from):
        return dict(version=version or '', constraint=constraint, notation=notation,
                    basis=basis, state=state, reason=reason, selected_from=selected_from,
                    conflicts=conflicts)

    if conflicts:
        # A project that contradicts itself has no single version; reporting the
        # conflict is more useful than picking one of its declarations.
        return result('', UNRESOLVED,
                      'The project declares conflicting requirements; resolve them first.', basis)

    if not constraint:
        return result('', UNRESOLVED,
                      'No version was declared, so no exact version can be selected.', basis)

    for version in installed:
        if satisfies(version):
            if any(item.get('resolution') for item in requirement.get('discovered') or []):
                return result('', UNRESOLVED,
                    'Installed SDKs need selection verification in the component invocation directory.', basis)
            return result(version, READY, '', 'installed')

    for version in artifacts:
        if satisfies(version):
            return result(version, ACQUIRE,
                          f'{version} is installable and would satisfy the declared constraint.',
                          'artifact')

    if artifacts:
        return result('', UNOBTAINABLE,
                      'This release installs ' + ', '.join(artifacts) +
                      ', which does not satisfy the declared constraint.', basis)
    if installed:
        # An installed version that misses the range is still a fact worth
        # naming: the operator needs to know what is present and why it was
        # refused, not merely that something was unavailable.
        return result('', UNOBTAINABLE,
                      'Installed ' + ', '.join(installed) +
                      ' does not satisfy the declared constraint, and this release installs '
                      'no alternative.', basis)
    return result('', UNOBTAINABLE,
                  'No installed version and no installable artifact is available.', basis)


def _requirement_status(requirement, capability_record, installed, artifacts):
    """Fold capability and selection into one state with an actionable reason."""
    chosen = _select_version(requirement, installed, artifacts)
    if not capability_record['known']:
        return dict(chosen, state=UNSUPPORTED, version='',
                    reason=capability_record['reason'] or 'No adapter is registered.')
    if not capability_record['supported']:
        return dict(chosen, state=UNSUPPORTED, version='',
                    reason=capability_record['reason'])
    if chosen['state'] in (READY, ACQUIRE, UNRESOLVED):
        return chosen
    # A requirement with a declared version that Herdr cannot obtain here. This
    # is `unsupported`, not `unresolved`: the version is known, nothing is
    # queued, and the next step is a manual install or a new adapter.
    return dict(chosen, state=UNSUPPORTED,
                reason=chosen['reason'] or
                f"Herdr has no installer for {capability_record['label']} on this host.")


def _installed_versions(capability_record, measure=None):
    """Exact versions of a capability already present on this host.

    Uses the shared probe where one is supplied so the plan reads the same
    measurements the assessment does; the plan itself never runs a probe.
    """
    if measure is None:
        return []
    versions = []
    for name in capability_record['providers']:
        found, _detail = measure(name)
        for version in found if isinstance(found, list) else [found]:
            if version and _exact(version) and version not in versions:
                versions.append(version)
    return versions


def measurements(report):
    """Reuse all host observations already made by analysis, without probing again."""
    def measure(name):
        versions, details = [], []
        for requirement in report.get('requirements', []):
            capability = environment_capabilities.capability_for(requirement.get('tool'))
            if name not in environment_capabilities.providers(capability):
                continue
            assessment = requirement.get('assessment') or {}
            observed = [assessment.get('measured'),
                        *(item.get('measured') for item in assessment.get('candidates') or [])]
            for version in observed:
                if version and version not in versions:
                    versions.append(version)
            if assessment.get('detail'):
                details.append(assessment['detail'])
        return versions, ' '.join(details) or f'No executable providing {name} was measured.'
    return measure


def source_components(report):
    """Heuristically waive inferred JS/TS beside non-Node manifest projects.

    Other inferred languages remain SDK requirements. An actual Node manifest
    or an operator's Node override always takes precedence over this exemption.
    """
    components = report.get('components') or []
    manifests = [item for item in components if item.get('basis') == 'manifest']
    required = {environment_capabilities.capability_for(req['tool'])
                for item in manifests for req in item.get('requirements', [])}
    overridden = {environment_capabilities.capability_for(req['tool'])
                  for req in report.get('requirements', []) if req.get('override')}
    if not manifests or 'node' in required or 'node' in overridden:
        return []
    return [dict(path=item['path'], capability='node', basis='inferred',
                 reason='Inferred JS/TS beside a non-Node project is treated as source-only. '
                        'This heuristic can include Node servers or tools: add a package.json '
                        'or a Node.js override if this component needs a Node runtime.')
            for item in components if item.get('basis') == 'inferred'
            and any(environment_capabilities.capability_for(req['tool']) == 'node'
                    for req in item.get('requirements', []))]


def scoped_requirements(report, *, sources=None):
    """Keep each component's SDK selection, instead of flattening a monorepo."""
    if not report.get('components'):
        return report.get('requirements') or []
    overrides = {environment_capabilities.capability_for(item['tool']): item.get('override')
                 for item in report.get('requirements', []) if item.get('override')}
    found = {}
    sources = source_components(report) if sources is None else sources
    assets = {(item['path'], item['capability']) for item in sources}
    for component in report['components']:
        for requirement in component['requirements']:
            tool = requirement['tool']
            capability = environment_capabilities.capability_for(tool)
            if (component['path'], capability) in assets:
                continue
            key = (component['path'], capability)
            item = found.setdefault(key, dict(tool=tool, label=requirement['label'],
                purpose=requirement.get('purpose', ''), notation=requirement.get('notation', 'semver'),
                executable=requirement.get('executable', ''), components=[component['path']],
                override=overrides.get(capability), discovered=[], conflicts=[]))
            item['discovered'].append(dict(requirement, component=component['path']))
            item['conflicts'].extend(requirement.get('conflicts') or [])
    # Overrides for capabilities not discovered are still explicit requirements.
    present = {environment_capabilities.capability_for(item['tool']) for item in found.values()}
    for item in report.get('requirements', []):
        if item.get('override') and environment_capabilities.capability_for(item['tool']) not in present:
            found[('.', item['tool'])] = item
    return list(found.values())


def build(report, release_entries=None, measure=None, plan_id=None):
    """Build an environment plan from a resolved requirement report.

    `report` is the output of `ProjectAnalysis.effective` (or `report`): it
    already carries the per-tool requirements, their evidence and their host
    assessment. This function does not re-read the repository and does not probe
    the host; it transforms a decision the analysis already made into the
    immutable description a worker would execute.
    """
    source = report.get('source') or {}
    if source.get('scope') != 'committed':
        # The document is explicit: a working-tree report is a preview and must
        # not authorise preparation. The plan still builds, marked as such, so an
        # operator can see what a commit would require.
        revision = source.get('revision') or ''
        preview = True
    else:
        revision = source.get('revision') or ''
        preview = False
    release_entries = release_catalog_view(release_entries)
    # Effective reports already carry the classification displayed by the UI.
    # Raw discovery reports are also accepted by portable parser/planner callers.
    sources = report.get('source_components')
    if sources is None:
        sources = source_components(report)

    requirements = []
    for requirement in scoped_requirements(report, sources=sources):
        tool = requirement.get('tool') or ''
        capability_record = environment_capabilities.describe(tool)
        capability = capability_record['id']
        artifacts = _artifact_versions(release_entries, tool)
        installed = _installed_versions(capability_record, measure)
        status = _requirement_status(requirement, capability_record, installed, artifacts)
        components = requirement.get('components') or []
        requirements.append(dict(
            tool=tool, capability=capability, label=requirement.get('label') or tool,
            purpose=requirement.get('purpose') or '',
            components=list(components),
            executable=requirement.get('executable') or '',
            evidence=requirement.get('discovered') or [],
            override=requirement.get('override'),
            **status,
            installed=installed, artifacts=artifacts,
        ))

    plan = dict(
        plan_version=PLAN_VERSION,
        project=report.get('project') or '',
        revision=revision,
        preview=preview,
        host=environment_capabilities.host(),
        policy_revision=report.get('analyzer') or source.get('analyzer') or 0,
        requirements=requirements,
        source_components=sources,
        blockers=[_blocker(item) for item in requirements if item['state'] != READY],
        at=now(),
    )
    plan['plan_id'] = plan_id or plan['project']
    plan['digest'] = digest(plan)
    return plan


def _blocker(requirement):
    """The single next action for a requirement that is not ready."""
    state = requirement['state']
    if state == ACQUIRE:
        remedy = 'Install the selected version, then re-verify.'
    elif state == UNSUPPORTED:
        remedy = 'Herdr cannot prepare this capability here; install it manually or add an adapter.'
    elif state == UNRESOLVED:
        remedy = 'Resolve the requirement (declare a version or remove the conflict) and re-plan.'
    else:
        remedy = ''
    return dict(capability=requirement['capability'], state=state,
                version=requirement['version'], reason=requirement['reason'], remedy=remedy)


def release_catalog_view(entries=None):
    """Normalise a caller-supplied release catalog into the shape used here."""
    if entries is None:
        return environment_capabilities.release_catalog()
    return {name: dict(entry, acquirable=bool(entry.get('acquirable')))
            for name, entry in entries.items() if isinstance(entry, dict)}


def summary(plan):
    """One line per capability, for a dashboard or a prompt."""
    return [f"{item['capability']} {item['version'] or 'unresolved'} [{item['state']}]"
            for item in plan.get('requirements', [])]


def bindings(plan, receipt=None):
    """Translate an approved plan and matching SDK receipt into policy entries.

    No receipt means an explicit missing binding, never an empty policy. This
    names verified installation paths; only execution-time probes prove reuse.
    """
    valid = bool(receipt and receipt.get('state') == 'ready'
                 and receipt.get('digest') == plan['digest']
                 and receipt.get('revision') == plan['revision'])
    entries, blockers = [], []
    unbound = {}
    for item in plan.get('requirements', []):
        for component in item.get('components') or ['.']:
            matches = [value for value in receipt.get('receipts', [])
                       if value.get('capability') == item['capability']
                       and value.get('version') == item['version']
                       and component in value.get('components', [])
                       and value.get('specification') == item.get('artifact')] if valid else []
            executable = (matches[0].get('probe') or {}).get('executable') if len(matches) == 1 else None
            if executable and not Path(executable).is_absolute():
                executable = None
            evidence = [observation for source in item.get('evidence', [])
                        for observation in source.get('observations', []) if not observation.get('unreadable')]
            declared_by = ', '.join(sorted({observation['file'] + '#' + str(observation.get('field', ''))
                                           for observation in evidence})) or 'repository discovery'
            entry = dict(name=item['capability'], label=item['label'], required=item.get('version') or item.get('constraint') or 'unresolved',
                         component=component, cwd=component, declared_by=declared_by,
                         rule=dict(evidence=item.get('evidence', []), override=item.get('override')),
                         adapter_version=item.get('adapter_version', 1),
                         executables=[executable] if executable else [], source='prepared' if executable else 'unavailable')
            if executable:
                import environment_adapters
                adapter = environment_adapters.get(item['capability'])
                if item.get('artifact', {}).get('provider') == 'agent_adopted':
                    entry['adoption'] = item['artifact']
                if item.get('restore_recipes'):
                    recipes = [value for value in item['restore_recipes'] if value['component'] == component]
                    if len(recipes) != 1 or not adapter.materialize_entry:
                        raise ValueError('The component has no unambiguous execution recipe; prepare again.')
                    entry['restore_recipe'] = recipes[0]
                    entry['protected_inputs'] = sorted(recipes[0].get('inputs', {}))
                import environment_providers
                if (adapter.materialize_entry or adapter.component_commands
                        or environment_providers.get(adapter.provider).verify_entry):
                    entry['artifact_receipt'] = matches[0]
                if adapter.companion:
                    version = matches[0]['probe'].get(adapter.companion + '_version')
                    if version:
                        entry['companions'] = [dict(name=adapter.companion, required=version,
                            executables=[adapter.command(executable, adapter.companion)], component=component)]
                    else:
                        blockers.append(f'{component}: the receipt has no {adapter.companion} version evidence; prepare again.')
            entries.append(entry)
            if not executable:
                record = unbound.setdefault(item['capability'], dict(item=item, components=[]))
                record['components'].append(component)
    # One blocker per capability, not per component: a monorepo with forty Node
    # packages would otherwise bury the reason the environment cannot be approved
    # under forty copies of itself.
    for capability in sorted(unbound):
        blockers.append(_unbound(unbound[capability]['item'], unbound[capability]['components']))
    return dict(toolchains=entries, blockers=blockers, validation_blockers=[],
                plan_digest=plan['digest'], checks=[], declarations={}, lockfiles={},
                recipes=plan.get('selected_checks') or [],
                restore_authorized=(plan.get('automatic_policy', {}).get('auto_prepare_restore') is True
                    or plan.get('restore_permission', {}).get('allowed') is True))


def _unbound(item, components):
    """Why a capability has no usable binding, and what would actually change that.

    A capability with no managed activation adapter cannot be prepared, so
    "prepare this environment first" would be advice nobody can follow. The two
    states are different facts and the reader needs the one that applies.
    """
    where = ', '.join(components[:4])
    if len(components) > 4:
        where += f' and {len(components) - 4} more'
    # Preparation records `activation`; a plan built before preparation still has
    # to answer whether preparing could ever produce a binding, so the declared
    # managed set is consulted rather than assumed absent.
    if item.get('state') == UNRESOLVED:
        return (f"{item['label']} is unresolved ({where}): "
                + (str(item.get('reason') or 'resolve the SDK selection before preparation').rstrip('.')) + '.')
    capability = environment_capabilities.registry(item['capability']) or {}
    unavailable = item.get('activation') == 'unavailable' or (
        item.get('activation') is None and 'activate' not in capability.get('operations', ()))
    if not unavailable:
        return (f"{item['label']} is not prepared yet ({where}); "
                'prepare this environment before validation can verify it.')
    reason = str(item.get('activation_reason') or capability.get('activation_reason') or '').strip().rstrip('.')
    return (f"{item['label']} cannot be verified ({where}): "
            + (reason or 'no managed activation adapter is available') + '.')


def reported_discrepancies(frozen, receipt):
    """Worker observations are claims; discrepancies never substitute for probes."""
    observations = receipt.get('tools', [])
    if not isinstance(observations, list) or len(observations) > 20:
        raise ValueError('Report at most 20 SDK observations.')
    observations = [*observations, *(item for item in receipt.get('tests', [])
        if isinstance(item, dict) and {'tool', 'component', 'version'}.issubset(item))]
    if len(observations) > 20:
        raise ValueError('Report at most 20 SDK observations across tools and tests.')
    findings = []
    entries = (frozen or {}).get('policy', {}).get('toolchains', [])
    for observation in observations:
        if (not isinstance(observation, dict) or any(not isinstance(observation.get(key), str)
                or len(observation[key]) > 200 for key in ('tool', 'component', 'version'))):
            raise ValueError('SDK observations require bounded tool, component and version fields.')
        capability = environment_capabilities.capability_for(observation['tool'])
        for entry in entries:
            if (environment_capabilities.capability_for(entry['name']) == capability
                    and entry.get('component', '.') == observation['component']
                    and entry['required'] != observation['version']):
                findings.append(dict(kind='reported_sdk_mismatch', tool=capability,
                    component=observation['component'], required=entry['required'],
                    reported=observation['version'], source='worker_report', verified=False))
    return findings
