"""Portable configuration transfer, without runtime sessions or credentials."""
from contextlib import closing
from datetime import datetime, timezone
import json
import re

from organizations import model_settings

TABLES = ('organizations', 'profiles', 'groups')
MAX_TRANSFER_BYTES = 2_000_000


def validate_text_fields(item):
    # Match creation limits without trimming: migration must preserve exact text.
    limits = {'name': 120, 'role': 120, 'purpose': 2000, 'instructions': 8000,
              'persona': 8000, 'description': 8000, 'topic': 8000, 'project': 2000,
              'provider': 160, 'model': 160, 'reasoning': 160}
    # Generated facilitator personas prepend instructions to the 8,000-character
    # group description. Ordinary hires still use the creation-time 8,000 cap.
    if item.get('group_id'):
        limits['persona'] = 12000
    for key, limit in limits.items():
        if key in item and (not isinstance(item[key], str) or len(item[key]) > limit or '\x00' in item[key]):
            raise ValueError(f'Invalid {key} (maximum {limit} characters).')
    paths = item.get('accessible_paths', [])
    if not isinstance(paths, list) or len(paths) > 20 or any(
            not isinstance(path, str) or len(path) > 500 or any(c in path for c in '\x00\n\r') for path in paths):
        raise ValueError('Invalid accessible path patterns.')


def export_configuration(store):
    with store.lock, closing(store.connect()) as db, db:
        db.execute('BEGIN')
        records = {table: [json.loads(row['data']) for row in db.execute(f'SELECT data FROM {table} ORDER BY rowid')]
                   for table in TABLES}
    result = {'format': 'herdr-configuration', 'version': 1,
            'exported_at': datetime.now(timezone.utc).isoformat(), **records}
    # Preserve large backups rather than truncating or silently losing records.
    # Import has a separate request limit; callers can warn before downloading.
    result['import_size_supported'] = len(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')) + 40 <= MAX_TRANSFER_BYTES
    return result


def import_configuration(store, bundle):
    if len(json.dumps(bundle, ensure_ascii=False).encode('utf-8')) > MAX_TRANSFER_BYTES:
        raise ValueError('Configuration exceeds the 2 MB import limit.')
    if not isinstance(bundle, dict) or bundle.get('format') != 'herdr-configuration' or bundle.get('version') != 1:
        raise ValueError('Unsupported configuration export format or version.')
    records = {}
    for table in TABLES:
        values = bundle.get(table)
        if not isinstance(values, list) or len(values) > 1000:
            raise ValueError(f'Invalid {table} collection.')
        mapped = {}
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', item['id']):
                raise ValueError(f'Invalid {table} record ID.')
            if item['id'] in mapped or not isinstance(item.get('name'), str):
                raise ValueError(f'Duplicate ID or missing name in {table}.')
            validate_text_fields(item)
            if table == 'profiles':
                # Reuse the creation rules so an imported profile can launch.
                model_settings(item, item.get('runtime'))
            mapped[item['id']] = item
        records[table] = mapped
    def profile(reference, org):
        if not isinstance(reference, str):
            raise ValueError('Invalid agent reference.')
        found = records['profiles'].get(reference)
        if not found or found.get('organization_id') != org:
            raise ValueError('Missing or cross-organization agent reference.')
    for item in records['profiles'].values():
        org = item.get('organization_id')
        if not isinstance(org, str) or org not in records['organizations']:
            raise ValueError('Agent organization is missing.')
        if item.get('runtime') not in ('codex', 'claude', 'opencode', 'agy'):
            raise ValueError('Unsupported agent runtime.')
        seen = {item['id']}
        manager = item.get('manager_id')
        while manager:
            profile(manager, org)
            if manager in seen:
                raise ValueError('Agent reporting cycle.')
            seen.add(manager)
            manager = records['profiles'][manager].get('manager_id')
        group_id = item.get('group_id')
        if group_id and not isinstance(group_id, str):
            raise ValueError('Invalid agent group reference.')
        if group_id and group_id not in records['groups']:
            raise ValueError('Agent group is missing.')
    for item in records['groups'].values():
        org = item.get('organization_id')
        members = item.get('members')
        if not isinstance(org, str) or org not in records['organizations']:
            raise ValueError('Group organization is missing.')
        # Imported groups must satisfy the same invariant as created groups, or
        # a later discussion would fail looking up the first member.
        if (not isinstance(members, list) or not 2 <= len(members) <= 6 or
                not all(isinstance(member, str) for member in members) or len(set(members)) != len(members)):
            raise ValueError('Groups need 2 to 6 distinct member agents.')
        for member in members:
            profile(member, org)
            if records['profiles'][member].get('group_id'):
                raise ValueError('Group members must be agents, not other group conversations.')
        if item.get('facilitator_id'):
            profile(item['facilitator_id'], org)
    with store.lock, closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for table in TABLES:
            existing = {row['id']: json.loads(row['data']) for row in db.execute(f'SELECT id, data FROM {table}')}
            # Same-content collisions are a successful retry after a lost response.
            # Different-content collisions remain fail-closed; never overwrite.
            for key in existing.keys() & records[table].keys():
                if existing[key] != records[table][key]:
                    raise ValueError('Configuration IDs already exist with different content; existing records will not be overwritten.')
        for table in TABLES:
            for item in records[table].values():
                store.put(db, table, item)
    return {'imported': {table: len(records[table]) for table in TABLES}}
