"""Build storage orchestration. Ecosystem policy remains in the SDK adapters.

Installed dependencies are private. Only adapter-declared download data is reused
within one repository and trust lane; managed output contains no source/evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import uuid
import time
from contextlib import ExitStack, contextmanager

GIB = 1024 ** 3
DEFAULTS = dict(build_mode='compact', output_budget_bytes=8 * GIB,
                shared_budget_bytes=4 * GIB, max_closed_run_bytes=GIB,
                active_output_limit_bytes=8 * GIB, idle_seconds=86400)


def root():
    return Path(os.environ.get('HERDR_WORKSPACE_STORAGE_ROOT') or
                Path.home() / '.local/share/herdr/workspace-storage').absolute()


def settings(directory=None):
    directory = Path(directory) if directory is not None else root()
    path = directory / 'settings.json'
    result = dict(DEFAULTS)
    if path.is_symlink() or directory.is_symlink():
        raise ValueError('The managed storage configuration must not be linked.')
    try:
        if path.stat().st_size > 4096:
            raise ValueError('Managed storage configuration is too large.')
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return result
    if not isinstance(value, dict):
        raise ValueError('Invalid managed storage configuration.')
    result.update(validate_settings(value))
    return result


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Unknown storage setting.')
    if 'build_mode' in value and value['build_mode'] not in ('compact', 'full'):
        raise ValueError('Choose compact or full build information.')
    for key, amount in value.items():
        if key != 'build_mode' and (type(amount) is not int or amount < 0 or amount > 1024 ** 5):
            raise ValueError('Storage limits must be nonnegative integer byte/second values.')
    return value


def configure_settings(value, directory=None):
    value = validate_settings(value)
    directory = Path(directory) if directory is not None else root()
    updated = dict(settings(directory), **value)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = directory / ('settings-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(updated, stream, sort_keys=True)
        temporary.replace(directory / 'settings.json')
    finally:
        temporary.unlink(missing_ok=True)
    return updated


def manager(projects=None, directory=None):
    from workspace_storage import Manager
    return Manager(directory or root(), projects or Path(os.environ.get('HERDR_PROJECTS', '/home/herdr/projects')))


def _allocate(store, *args, **options):
    from workspace_storage import StorageBusy
    deadline = time.monotonic() + 30
    while True:
        try:
            return store.allocate(*args, **options)
        except StorageBusy:
            if time.monotonic() >= deadline:
                raise StorageBusy('Managed storage maintenance is busy; retry this execution after cleanup completes.') from None
            time.sleep(.1)


def grants(record, *, projects=None):
    """Re-prove registered ownership; grant data children, never control files."""
    if not record or not record.get('objects'):
        return []
    if Path(record['store_root']).absolute() != root():
        raise ValueError('The storage binding belongs to another configured store.')
    store = manager(projects=projects)
    lane, execution = record.get('lane'), record.get('execution_id')
    if lane not in ('agent', 'validation'):
        raise ValueError('Invalid storage binding lane.')
    allowed = {'cache:' + lane + ':' + str(execution), 'output:' + lane + ':' + str(execution)}
    result = []
    for identity in record['objects']:
        if identity not in allowed:
            raise ValueError('A storage grant belongs to another execution.')
        owner = store.binding(identity)
        if owner['execution_id'] != execution or owner['kind'] not in ('execution-cache', 'build-output'):
            raise ValueError('A storage grant has an invalid owner or purpose.')
        data = Path(owner['path']) / 'data'
        from workspace_storage import _safe_chain
        _safe_chain(data)
        result.append(str(data))
    expected_private = store._path('cache:' + lane + ':' + str(execution)) / 'data'
    if (record.get('manifest') or {}).get('execution_root') != str(expected_private):
        raise ValueError('The private cache grant differs from its registered data directory.')
    if any(path not in result for path in record.get('outputs', [])):
        raise ValueError('The output grant differs from its registered data directory.')
    return list(dict.fromkeys(result))


def prepare(entries, checkout, *, lane, execution_id, create=True, projects=None):
    """Allocate only adapter-selected outputs and repository-scoped download pools."""
    import environment_adapters
    import package_cache
    if lane not in ('agent', 'validation'):
        raise ValueError('Unknown execution storage lane.')
    if any(entry.get('storage_contract') not in (None, 1) for entry in entries):
        raise ValueError('The execution storage contract changed; prepare and approve a compatible plan.')
    entries = [entry for entry in entries if entry.get('storage_contract') == 1]
    selected = [environment_adapters.get(entry['name']) for entry in entries]
    result, outputs = {}, []
    if not any(adapter.output_variables or adapter.download_cache for adapter in selected):
        return dict(environment={}, manifest={}, outputs=[], guidance=[], execution_id=execution_id, lane=lane)
    store = manager(projects=projects)
    repository = package_cache.repository_info(checkout)
    project = str(repository['project'])
    cache_id = 'cache:' + lane + ':' + str(execution_id)
    output_id = 'output:' + lane + ':' + str(execution_id)
    pool_id = 'downloads:' + lane + ':' + repository['key']
    private, shared = store._path(cache_id) / 'data', store._path(pool_id) / 'data'
    owns_output = any(adapter.output_variables for adapter in selected)
    owns_pool = any(adapter.shared_downloads for adapter in selected)
    objects = [cache_id] + ([output_id] if owns_output else [])
    # Validate the complete adapter/cache layout and settings before writing.
    policy = settings()
    configured = package_cache.configure(entries, private, checkout, lane=lane, shared_root=shared,
                                         repository=repository, create=False)
    try:
        if create:
            for identity, kind in [(cache_id, 'execution-cache')] + ([(output_id, 'build-output')] if owns_output else []):
                owned = _allocate(store, identity, kind=kind, project=project, execution_id=execution_id)
                _data_directory(owned)
            configured = package_cache.configure(entries, private, checkout, lane=lane, shared_root=shared,
                                                 repository=repository, create=True, create_shared=False)
        result.update(environment_adapters.execution_caches(entries, private, create=create))
        if owns_output:
            generated = store._path(output_id) / 'data'
            for adapter in selected:
                result.update(adapter.output_environment(generated))
            outputs.append(str(generated))
            result.update(HERDR_BUILD_OUTPUT_ROOT=str(generated),
                          HERDR_BUILD_OUTPUT_LIMIT_BYTES=str(policy['active_output_limit_bytes']))
        result.update(configured['environment'])
        result['HERDR_BUILD_STORAGE_ROOT'] = str(root())
        # Only private/output leases reach command processes. Shared archive
        # pools are used solely by the broker under short-lived pool leases.
        locks = [str(store.root / '.locks' / (hashlib.sha256(identity.encode()).hexdigest() + '.lock')) for identity in objects]
        result['HERDR_STORAGE_LEASES'] = json.dumps(locks)
        if create:
            with ExitStack() as leases:
                for identity in objects:
                    leases.enter_context(store.lease(identity))
                if owns_pool:
                    with _pool_access(store, dict(id=pool_id, project=project)) as available:
                        if available:
                            package_cache.seed(configured['manifest'], checkout)
        guidance = [line for adapter in selected for line in adapter.output_guidance]
        return dict(environment=result, manifest=configured['manifest'], outputs=outputs, objects=objects,
                    pool=dict(id=pool_id, project=project) if owns_pool else None,
                    guidance=guidance + configured['guidance'], execution_id=execution_id, lane=lane,
                    store_root=str(store.root), projects=str(store.projects))
    except BaseException:
        if create:
            # Even a failure after the first allocation has a durable closed
            # owner. No launch receipt is needed for later retention to find it.
            try:
                store.close_execution(execution_id)
            except (OSError, ValueError):
                import logging
                logging.exception('Storage preparation abort awaits lifecycle reconciliation: %s', execution_id)
        raise


def _data_directory(owned):
    from workspace_storage import _safe_chain
    data = Path(owned) / 'data'
    _safe_chain(data)
    data.mkdir(exist_ok=True, mode=0o700)
    return data


@contextmanager
def _pool_access(store, pool):
    """Retention racing an optional cache is a miss, never a failed launch."""
    context = None
    try:
        _data_directory(_allocate(store, pool['id'], kind='download-cache', project=pool['project'], shared=True))
        context = store.lease(pool['id'])
        context.__enter__()
    except (OSError, ValueError):
        yield False
        return
    try:
        yield True
    finally:
        context.__exit__(None, None, None)


def finish(record, checkout, *, close=False):
    import package_cache
    if not record or not record.get('objects'):
        return dict(files=0, bytes=0, errors=[])
    store = manager(projects=record.get('projects'), directory=record.get('store_root'))
    with ExitStack() as leases:
        for identity in record['objects']:
            leases.enter_context(store.lease(identity))
        pool = record.get('pool')
        if pool:
            with _pool_access(store, pool) as available:
                result = (package_cache.harvest(record['manifest'], checkout) if available else
                          dict(files=0, bytes=0, errors=['Shared archive pool unavailable; normal downloads remain enabled.']))
        else:
            result = dict(files=0, bytes=0, errors=[])
        if close:
            store.close_execution(record['execution_id'])
        return result


def close_execution(identity, *, projects=None):
    manager(projects=projects).close_execution(identity)
