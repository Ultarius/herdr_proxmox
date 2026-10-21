"""The production entry point for automatic project discovery.

Discovery is expensive — a bounded walk, then a bounded read of every manifest —
so it is cached, and the cache is explicit about what it can and cannot know.

Two scopes, because they answer different questions and are trusted differently:

* a **committed** report resolves an exact revision, enumerates that revision's
  tree through Git and reads that revision's blobs. It cannot include an
  uncommitted edit, which is the whole reason it exists: approving, validating
  and provisioning must not act on a working-tree declaration.
* a **working-tree** report includes uncommitted edits. It has no cheap identity,
  so it is cached for a short window and a refresh bypasses it.

A revision that does not resolve in the project is refused rather than labelled.
Reports carry their scope, revision and freshness so a reader can tell which
answer they are looking at.
"""
from datetime import datetime, timezone
from pathlib import Path
import threading
import time

import dependency_constraints
import environment_capabilities
import project_inventory
import project_requirements

# Only working-tree reports have a window: a committed report's key includes the
# resolved revision, so a new commit is a cache miss rather than a stale hit.
WORKING_TREE_TTL = 30.0
CACHE_LIMIT = 64
# Bumped whenever a change alters what a report means, so old entries are unused
# rather than reinterpreted. Version 7 includes bounded README headless Cargo
# proposals alongside declared manifests and the source-only component census.
ANALYZER_VERSION = 7


def stamp():
    return datetime.now(timezone.utc).isoformat()


def committed_inventory(root, revision):
    """The file list of one committed revision, enumerated by Git."""
    import project_git
    output = project_git.git(root, 'ls-tree', '-r', '-z', '--name-only', revision)
    return project_inventory.from_paths([item for item in output.split('\0') if item])


def committed_reader(root, revision):
    """Read one file's bytes from a committed revision.

    Git's tree mode already excludes symlinks: only `100644` and `100755` blobs
    are regular files, which is the committed equivalent of refusing to follow a
    link in the working tree.
    """
    import project_git
    from project_requirements import MAX_MANIFEST_BYTES

    def read(name, limit=MAX_MANIFEST_BYTES):
        if not isinstance(name, str) or '..' in name:
            return None
        records = project_git.git(root, 'ls-tree', revision, '--', ':(literal)' + name).strip()
        if not records.startswith(('100644 blob ', '100755 blob ')):
            return None
        return project_git.git(root, 'show', revision + ':' + name).encode('utf-8', 'replace')[:limit + 1]

    return read


class ProjectAnalysis:
    def __init__(self, root, overrides=None, toolchains=None, catalog=None):
        self.root = Path(root)
        # The environment, supplied by the gateway. Absent means "assess
        # nothing", which is the safe default rather than a silent claim.
        self.overrides = overrides or (lambda: {})
        self.toolchains = toolchains or (lambda: {})
        self.catalog = catalog or (lambda: {})
        self.lock = threading.Lock()
        self.cache = {}
        # Host assessments, keyed by what the answer depends on rather than by
        # the project that asked for it.
        self.assessments = {}

    def project(self, value):
        """The managed project a request names, or a refusal.

        The dashboard browses relative to the projects root, so a value that is
        not absolute is resolved against it rather than the gateway's working
        directory. Containment is enforced before descriptor traversal without
        canonicalizing away symlinks that traversal must reject.
        """
        from project_access import open_project, relative_path
        import os
        import stat
        if not isinstance(value, str) or not value:
            raise ValueError('Select a project directory.')
        root = self.root.absolute()
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        # Validate lexically, then pin from the original configured root. A
        # resolve() here would erase a swapped root/ancestor symlink before the
        # shared no-follow boundary had a chance to reject it.
        try:
            relative = relative_path(candidate.relative_to(root))
        except ValueError as error:
            raise ValueError('Select a project inside the projects directory.') from error
        with open_project(root, relative) as descriptor:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise ValueError('Select a project directory.')
        return root / relative

    def verify_revision(self, project, revision):
        """The exact commit a revision names, or a refusal.

        A fabricated revision, a revision from another repository and a path
        that is not a Git checkout all fail here rather than being echoed back
        inside a report that claims to describe them.
        """
        import project_git
        if not isinstance(revision, str) or (len(revision) != 40
                                             or any(c not in '0123456789abcdef' for c in revision)):
            raise ValueError('A revision must be a full commit SHA.')
        try:
            resolved = project_git.git(project, 'rev-parse', '--verify', revision + '^{commit}').strip()
        except (ValueError, OSError) as error:
            raise ValueError('That revision does not exist in this project.') from error
        if resolved != revision:
            raise ValueError('That revision is not a commit in this project.')
        return resolved

    def signature(self, path, revision):
        return (path, revision or '', ANALYZER_VERSION)

    def report(self, value, revision=None, force=False):
        """Discover one managed project, from cache when the key has not moved."""
        project = self.project(value)
        relative = project.relative_to(self.root.absolute()).as_posix()
        resolved = self.verify_revision(project, revision) if revision else None
        key = self.signature(relative, resolved)
        now = time.monotonic()
        with self.lock:
            entry = self.cache.get(key)
            if entry is not None and not force:
                if resolved or now - entry['stored'] < WORKING_TREE_TTL:
                    return entry['report']
        if resolved:
            # Enumerated and read from the commit, never from the checkout.
            report = project_requirements.analyse(
                project, inventory=committed_inventory(project, resolved),
                read=committed_reader(project, resolved))
        else:
            report = project_requirements.analyse(project)
        report['project'] = relative
        report['source'] = dict(scope='committed' if resolved else 'working_tree',
                                revision=resolved or '', analyzer=ANALYZER_VERSION,
                                boundary='repository root',
                                at=stamp(),
                                cache_seconds=0 if resolved else WORKING_TREE_TTL)
        with self.lock:
            if len(self.cache) >= CACHE_LIMIT:
                oldest = sorted(self.cache, key=lambda item: self.cache[item]['stored'])
                for stale in oldest[:len(self.cache) - CACHE_LIMIT + 1]:
                    self.cache.pop(stale, None)
            self.cache[key] = dict(report=report, stored=time.monotonic())
        return report

    def plan(self, value, revision=None, which=None, run=None, force=False, release=None):
        """The environment plan for a project: what preparing it would involve.

        Built from the same resolved report every other consumer reads, so the
        plan cannot describe a different project than the page or the provisioning
        decision. Analysis can probe installed executables; planning runs no
        project scripts, downloads or installers. It consults the capability
        registry and hashes the resolved report.
        `which`/`run` are the same probe overrides `effective` accepts, so a caller
        (or a test) can supply measurements without spawning a process.
        """
        import environment_plan
        report = self.effective(value, revision=revision, which=which, run=run, force=force)
        # The plan reads measurements from the assessment the report already
        # carries rather than re-probing, so an injected probe is honoured and a
        # real one is not run twice.
        # The release's installer definition is not an artifact/version list.
        return environment_plan.build(report, release_entries=release, measure=environment_plan.measurements(report))

    def effective(self, value, revision=None, which=None, run=None, force=False):
        """One resolved requirement view: discovery, overrides, conflicts, assessment.

        Every consumer of a project's requirement — the card, the readiness
        check, the provisioning decision — reads this rather than assembling its
        own picture. Two callers forming different views is how a discovered
        requirement and a contradictory override can both be "true".

        The override is authoritative because it is an operator decision, but the
        disagreement is always reported, never resolved silently.
        """
        import project_toolchains
        report = self.report(value, revision=revision, force=force)
        override = project_toolchains.normalized(self.overrides().get(report['project']))
        environment = self.toolchains()
        catalog = self.catalog()

        # Discovery is the base; one entry per tool, in a stable order.
        resolved = {}
        for component in report['components']:
            for requirement in component['requirements']:
                entry = resolved.setdefault(requirement['tool'], dict(
                    tool=requirement['tool'], label=requirement['label'],
                    purpose=requirement['purpose'], notation=requirement['notation'],
                    executable=requirement['executable'], components=[],
                    discovered=[], override=None, conflicts=[], observations=[]))
                entry['components'].append(component['path'])
                entry['discovered'].append(dict(component=component['path'],
                                                constraint=requirement['constraint'],
                                                notation=requirement['notation'],
                                                selected=requirement.get('selected', ''),
                                                resolution=requirement.get('resolution'),
                                                selection_lookup=requirement.get('selection_lookup'),
                                                established=requirement['established'],
                                                observations=requirement['observations']))
                entry['observations'].extend(requirement['observations'])
                entry['conflicts'].extend(requirement['conflicts'])

        if override and override['toolchain']:
            name = override['toolchain']
            entry = resolved.setdefault(name, dict(
                tool=name, label=name, purpose='operator override', notation=override['notation'],
                executable='', components=['.'], discovered=[], override=None,
                conflicts=[], observations=[]))
            entry['override'] = dict(toolchain=name, constraint=override['constraint'],
                                     notation=override['notation'], origin=override['origin'],
                                     actor=override['actor'], at=override['at'])
            stated = {item['constraint'] for item in entry['discovered'] if item['constraint']}
            if stated and override['constraint'] and stated != {override['constraint']}:
                entry['conflicts'].append(dict(
                    kind='override', role='override', values=sorted(stated),
                    reason='The operator override declares '
                           f"{override['constraint']}, but the repository declares "
                           + ' and '.join(sorted(stated)) + '. The override applies and this '
                           'disagreement is recorded.'))

        for entry in resolved.values():
            entry['capability'] = environment_capabilities.capability_for(entry['tool'])
            override = entry['override']
            # A selected SDK is evidence too. Ignoring it would call any
            # installed Flutter satisfactory even when FVM pins another one.
            selections = sorted({item['selected'] for item in entry['discovered'] if item['selected']})
            stated = next((item for item in entry['discovered'] if item['constraint']), None)
            constraint = (override['constraint'] if override else
                          stated['constraint'] if stated is not None else
                          selections[0] if len(selections) == 1 else '')
            # The notation belongs to the declaration that stated the constraint,
            # not to whichever component happened to sort first: one pyproject
            # can hold a PEP 621 range here and a Poetry caret in another.
            notation = (override['notation'] if override else
                        stated['notation'] if stated is not None and stated.get('notation') else
                        next((item['notation'] for item in entry['discovered']), 'semver'))
            entry['constraint'] = constraint
            entry['notation'] = notation
            entry['assessment'] = self.assessed(
                dict(repository=report['project'], origin=override['origin'] if override else 'discovery',
                     toolchain=entry['tool'], constraint=constraint, notation=notation,
                     actor=override['actor'] if override else '', at=''),
                environment.get(entry['tool']), catalog, which, run, force)
            if not override:
                # Compatibility ranges and SDK selections both apply, across
                # every component. The summary must not bless just the first
                # component's declaration or lose a pin beside a broad range.
                checks = [declaration for item in entry['discovered']
                          for declaration in project_requirements.declarations(item)]
                assessment = entry['assessment']
                if assessment['measured'] and checks:
                    verdicts = [dependency_constraints.evaluate(value, assessment['measured'], notation)
                                for value, notation in checks]
                    if 'violated' in verdicts:
                        assessment.update(verdict='violated',
                            detail=f"Measured {entry['tool']} {assessment['measured']} does not satisfy "
                                   'every component declaration and SDK selection.')
                    elif 'undecidable' in verdicts:
                        assessment.update(verdict='undecidable',
                            detail='The measured SDK cannot be assessed against every declaration and selection.')
                offer = assessment.get('offer')
                if offer and offer['version'] and not all(
                        dependency_constraints.evaluate(value, offer['version'], notation) == 'satisfied'
                        for value, notation in checks):
                    assessment['offer'] = dict(offer, satisfies=False,
                        reason='The release SDK does not satisfy every component declaration and selection.')
                    assessment['provisionable'] = False
            unreadable_pin = any(
                item.get('selection_lookup', {}).get('status') == 'unresolved'
                for item in entry['discovered'] if item.get('selection_lookup'))
            if not override and (len(selections) > 1 or unreadable_pin):
                # A pin nobody could read leaves the requirement itself unknown,
                # so no installation may be authorized on its behalf. Disagreeing
                # pins are a different case: the offer check above already judged
                # the release's version against every declaration and selection,
                # and it decides whether installing would resolve them all.
                if unreadable_pin:
                    entry['assessment']['provisionable'] = False
                # Unknown selections cannot erase an already proven failure.
                if entry['assessment']['verdict'] != 'violated':
                    entry['assessment'].update(verdict='undecidable',
                        detail='SDK selections disagree or could not be read; no single host verdict is established.')
            if any(item.get('resolution') is not None for item in entry['discovered']):
                # A probe from the gateway's cwd cannot establish which .NET
                # SDK would be selected for a component invocation context.
                entry['assessment'].update(verdict='undecidable', provisionable=False,
                    detail='SDK selection for the recorded lookup context has not been assessed.')

        ordered = [resolved[name] for name in sorted(resolved)]
        import environment_plan
        assets = environment_plan.source_components(dict(report, requirements=ordered))
        asset_keys = {(item['path'], item['capability']) for item in assets}
        # Asset observations remain in the component census, but must not be
        # presented as required host runtimes alongside the plan's exemption.
        ordered = [entry for entry in ordered if not (
            entry['components'] and all(
                (path, environment_capabilities.capability_for(entry['tool'])) in asset_keys
                for path in entry['components']))]
        installable = [entry['tool'] for entry in ordered
                       if entry['assessment']['provisionable']]
        return dict(report, requirements=ordered, source_components=assets, provisionable=installable,
                    project_requirement=dict(
                        source='working_tree' if report['source']['scope'] == 'working_tree' else 'committed',
                        revision=report['source']['revision'],
                        installable=installable,
                        reason=self._provisioning_reason(installable)))

    def assessed(self, record, entry, catalog, which=None, run=None, force=False):
        """One requirement's host answer, measured once per environment.

        A measurement depends on the tool, the constraint and this host — not on
        the project that asked — so it is cached against exactly those and a
        second project requiring the same range reuses it. A caller that brings
        its own `which`/`run` is asking a different question than the cached one
        and is never served from the cache.
        """
        import project_toolchains
        name = record['toolchain']
        if which is not None or run is not None:
            return project_toolchains.assess(record, entry, catalog, which=which, run=run)
        key = (name, record['constraint'], record['notation'],
               str((entry or {}).get('executable') or ''), str((entry or {}).get('required') or ''),
               bool(entry), name in (catalog or {}))
        with self.lock:
            found = self.assessments.get(key)
            if found is not None and not force and time.monotonic() - found['stored'] < WORKING_TREE_TTL:
                return dict(record, **found['environment'])
        environment = project_toolchains.environment(name, record['constraint'], record['notation'],
                                                      entry, catalog)
        with self.lock:
            if len(self.assessments) >= CACHE_LIMIT:
                oldest = sorted(self.assessments, key=lambda item: self.assessments[item]['stored'])
                for stale in oldest[:len(self.assessments) - CACHE_LIMIT + 1]:
                    self.assessments.pop(stale, None)
            self.assessments[key] = dict(environment=environment, stored=time.monotonic())
        return dict(record, **environment)

    @staticmethod
    def _provisioning_reason(installable):
        if len(installable) == 1:
            return ''
        if not installable:
            return ('No requirement of this project can be satisfied by an installer this release '
                    'ships, so an automatic installation cannot resolve the blocker.')
        return ('Several of this project\'s requirements are installable (' + ', '.join(installable)
                + '), so an automatic installation cannot choose one.')
