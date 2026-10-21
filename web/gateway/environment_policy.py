"""One approved environment policy for launch, validation and publication.

Resolution reads committed evidence and completed preparation records. It never
downloads, installs or trusts candidate-authored approval rules. Legacy Herdr
self-builds retain their existing policy during the explicit migration.
"""
from pathlib import Path
import sqlite3
import threading

import environment_plan
import project_analysis
import project_git
import project_inventory
import project_requirements
from repository_lock import common_directory
import toolchain_policy

CACHE_LIMIT = 64
TREE_LIMIT = 200_000
# Metadata adds roughly 60 bytes per path. Keep a separate, explicit response
# budget instead of applying the small diff/status budget to an entire tree.
TREE_BYTES = 8_000_000


def tree(root, revision):
    """One listing of the committed tree: mode, kind, blob id and size per path.

    Git's own blob identity is the content fingerprint, so approval does not need
    a read per protected file. The previous form cost two git invocations for
    each of them, and a monorepo resolves its policy on launch, on submission and
    on every delivery check.
    """
    entries = {}
    for record in project_git.git(root, 'ls-tree', '-r', '-z', '-l', revision,
                                  output_limit=TREE_BYTES).split('\0'):
        if not record:
            continue
        meta, tab, name = record.partition('\t')
        fields = meta.split()
        if (not tab or not name or len(fields) != 4 or not fields[0].isdigit()
                or not fields[2]):
            raise ValueError('A committed tree entry could not be read safely.')
        gitlink = fields[0] == '160000' and fields[1] == 'commit' and fields[3] == '-'
        if not gitlink and (fields[1] != 'blob' or not fields[3].isdigit()):
            raise ValueError('A committed tree entry could not be read safely.')
        entries[name] = dict(mode=fields[0], kind=fields[1], blob=fields[2],
                             size=None if gitlink else int(fields[3]))
        if len(entries) > TREE_LIMIT:
            raise ValueError('The committed tree exceeds the approval listing limit.')
    return entries


class EnvironmentPolicies:
    def __init__(self, projects, store):
        self.projects = Path(projects).resolve()
        self.store = store
        self.cache = {}
        self.lock = threading.Lock()

    def resolve(self, root, *, revision=None, plan_digest=None, receipt_id=None):
        root = Path(root)
        revision = project_git.git(root, 'rev-parse', '--verify', (revision or 'HEAD') + '^{commit}').strip()
        origin = common_directory(root).parent
        project = origin.relative_to(self.projects).as_posix()
        key = (str(origin), revision)
        with self.lock:
            record = self.cache.get(key)
        if record is None:
            entries = tree(root, revision)
            report = project_requirements.analyse(root, inventory=project_inventory.from_paths(
                [name for name, entry in entries.items() if entry['kind'] == 'blob']),
                read=project_analysis.committed_reader(root, revision))
            report.update(project=project, source=dict(scope='committed', revision=revision))
            record = dict(report=report, tree=entries)
            with self.lock:
                if len(self.cache) >= CACHE_LIMIT:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = record
        report, entries = record['report'], record['tree']
        paths = list(entries)
        if (report.get('coverage', {}).get('truncated')
                or any(item.get('what') == 'component list' for item in report.get('unresolved', []))):
            raise ValueError('Project discovery was truncated; approval requires complete evidence.')
        legacy = toolchain_policy.resolve_legacy_committed(root)
        unknown = [item['reason'] for item in report.get('unresolved', [])
                   if item.get('what') in ('unregistered manifests', 'components')]
        submodules = [name for name, entry in entries.items() if entry['kind'] == 'commit']
        if submodules:
            shown = ', '.join(submodules[:4])
            if len(submodules) > 4:
                shown += f' and {len(submodules) - 4} more'
            unknown.append('Submodule contents were not analysed (' + shown +
                           '); resolve their requirements separately before approval.')
        # This is an explicit compatibility route for Herdr's own approved
        # self-build scripts, not a fallback after generic discovery failed.
        # `checks` holds the candidate scripts that are actually present, and the
        # validation runner uses the first one it finds: requiring every candidate
        # name would refuse a fork that ships only scripts/build-web.sh.
        if (not unknown and legacy['toolchains'] and legacy['checks']
                and 'web/dashboard/pubspec.yaml' in paths
                and not any(req['tool'] not in ('flutter', 'dart', 'python')
                            for component in report['components'] for req in component['requirements'])):
            return legacy
        try:
            if plan_digest:
                stored, receipt = self.store.get(plan_digest), self.store.job(receipt_id)
                if (not stored or not receipt or stored['project'] != project or stored['revision'] != revision
                        or receipt.get('digest') != plan_digest or receipt.get('state') != 'ready'):
                    raise ValueError('The recorded environment preparation is not ready for this exact source.')
            else:
                stored, receipt = self.store.prepared(project, revision)
        except sqlite3.Error as error:
            raise ValueError('The approved environment store is unavailable; retry after recovery.') from error
        if stored and environment_plan.digest(stored) != stored.get('digest'):
            raise ValueError('The approved environment plan failed its integrity check.')
        plan = stored or environment_plan.build(report, release_entries={})
        policy = environment_plan.bindings(plan, receipt)
        policy['blockers'].extend('Unresolved project capability: ' + reason for reason in unknown)
        # Legacy pins still work for repositories with no generic components.
        if not policy['blockers'] and not policy['toolchains'] and (legacy['toolchains'] or legacy['checks']):
            return legacy
        protected = [name for name in paths if project_inventory.is_manifest(Path(name).name)
                     or name in toolchain_policy.DECLARATIONS
                     or name.startswith('.github/workflows/')
                     or name.lower().endswith(('.props', '.targets'))
                     or Path(name).name.lower() == 'nuget.config'
                     or name.endswith(('.cargo/config', '.cargo/config.toml', '.config/dotnet-tools.json'))]
        protected = sorted(set(protected) | {name for entry in policy['toolchains']
            for name in entry.get('protected_inputs', [])}
            | {recipe['evidence'].split('#')[0] for recipe in policy.get('recipes', [])
               if recipe.get('evidence')})
        policy['declarations'] = {}
        for name in protected:
            entry = entries.get(name)
            if entry is None:
                raise ValueError('A protected project declaration is missing from the committed tree: ' + name)
            if entry['kind'] != 'blob' or entry['mode'] not in ('100644', '100755'):
                raise ValueError('A protected project declaration is not a committed regular file: ' + name)
            if entry['size'] > project_requirements.MAX_MANIFEST_BYTES:
                raise ValueError('A protected project declaration or recipe evidence exceeds the 256 KiB limit: ' + name)
            policy['declarations'][name] = entry['blob']
        policy['lockfiles'] = legacy['lockfiles']
        policy['checks'] = legacy['checks']
        executable_recipes = []
        for recipe in policy['recipes']:
            bound = [entry for entry in policy['toolchains'] if entry['component'] == recipe['cwd']]
            arguments = list(recipe['arguments'])
            import environment_adapters
            capability = environment_adapters.commands().get(arguments[0])
            sdk = next((entry for entry in bound if entry['name'] == capability and entry['executables']), None)
            if not sdk:
                policy['validation_blockers'].append('No managed command binding for selected check ' + recipe['id'])
                continue
            arguments[0] = environment_adapters.get(capability).command(sdk['executables'][0], arguments[0])
            executable_recipes.append(dict(recipe, arguments=arguments, bindings=bound))
        policy['recipes'] = executable_recipes
        if policy['toolchains'] and not policy['recipes']:
            policy['validation_blockers'].append('No approved validation recipes selected for this environment.')
        if report.get('coverage', {}).get('truncated'):
            policy['blockers'].append('Project discovery was truncated; approval requires complete evidence.')
        return policy
