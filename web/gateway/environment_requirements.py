"""Archive SDK planning shared by registered acquisition adapters."""
import re

import environment_adapters
import environment_plan
import project_requirements


def plan_archive(item, *, paths, read, revision, resolve, specifications):
    # Existing PATH measurements do not prove contextual SDK selection.
    # Freeze a publisher artifact and verify it in the recorded cwd.
    selected = {entry.get('selected') for entry in item['evidence'] if entry.get('selected')}
    version = next(iter(selected)) if len(selected) == 1 else item.get('version')
    override = item.get('override')
    if override:
        version = override['constraint'] if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', override.get('constraint', '')) else item.get('version')
    if not version and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', item['constraint'] or ''):
        version = item['constraint']
    conflicts = [conflict for conflict in item['conflicts'] if not override or conflict.get('kind') != 'override']
    if conflicts or not version:
        item.update(state='unresolved', version='', reason='A single exact SDK selection is required for acquisition.')
        return
    if any(entry.get('selection_lookup', {}).get('status') == 'unresolved'
           for entry in item['evidence'] if entry.get('selection_lookup')):
        item.update(state='unresolved', reason='The SDK selection could not be read.')
        return
    try:
        components = set()
        for entry in item['evidence']:
            lookup = entry.get('selection_lookup') or {}
            requested = lookup.get('components', [])
            if not isinstance(requested, list):
                raise ValueError('Optional SDK components must be a list.')
            if requested and item['capability'] != 'rust-toolchain':
                raise ValueError('Optional components are supported only for the Rust toolchain adapter.')
            if item['capability'] == 'rust-toolchain':
                if any(not isinstance(name, str) or name not in environment_adapters.RUST_COMPONENTS for name in requested):
                    raise ValueError('Only rustfmt and clippy are supported optional Rust components.')
                components.update(requested)
        key = (item['capability'], version, tuple(sorted(components)))
        if key not in specifications:
            specifications[key] = (resolve(item['capability'], version, components=sorted(components))
                                   if components else resolve(item['capability'], version))
        specification = specifications[key]
        for entry in item['evidence']:
            lookup = entry.get('selection_lookup') or {}
            if lookup.get('targets') or lookup.get('path') or lookup.get('profile') not in (None, 'minimal'):
                raise ValueError('This Rust component needs extra targets, a custom path or a non-minimal profile not yet supported by the acquisition adapter.')
            declarations = project_requirements.declarations(override or entry, item['notation'])
            if any(environment_plan._satisfies(version, constraint, notation or item['notation']) != 'satisfied'
                   for constraint, notation in declarations):
                raise ValueError('The SDK selection does not satisfy its component declarations.')
            resolution = entry.get('resolution') or {}
            if resolution.get('policies', {}).get('paths'):
                raise ValueError('Custom .NET SDK search paths need a managed activation adapter.')
        item.update(state='acquire', version=version, artifact=specification,
                    activation='available', activation_reason='',
                    reason='The exact publisher SDK can be prepared and contextually verified.')
    except (ValueError, OSError) as error:
        item.update(state='unsupported', reason=str(error)[:500])
