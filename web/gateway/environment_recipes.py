"""Bounded check proposals; discovery proposes, an administrator selects.

No repository command executes while planning. Native recipes remain proposals
even when conventional, and their arguments/cwd are frozen before validation.
"""
import environment_adapters
from environment_providers import invoke


def candidates(report, paths):
    result = []
    paths = set(paths)
    for component in report.get('components', []):
        directory = component['path']
        for index, proposal in enumerate(component.get('candidate_commands', [])):
            arguments = list(proposal['command'])
            capability = environment_adapters.commands().get(arguments[0])
            adapter = environment_adapters.ADAPTERS.get(capability)
            if adapter and adapter.candidate_arguments:
                arguments = invoke(adapter.candidate_arguments, arguments, directory, paths)
                if arguments is None:
                    continue
            result.append(dict(id=f'{directory}:{index}:{proposal["purpose"]}', cwd=directory,
                arguments=arguments, source=proposal['source'], purpose=proposal['purpose'],
                evidence=proposal.get('evidence', ''), derived=proposal.get('derived') is True))
    return result[:100]


def select(plan, identifiers, actor):
    if (not isinstance(identifiers, list) or len(identifiers) > 20
            or any(not isinstance(value, str) for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Select up to 20 distinct proposed check identities.')
    proposals = {item['id']: item for item in plan.get('check_candidates', [])}
    if set(identifiers) - proposals.keys():
        raise ValueError('A selected check is not a proposal in this committed plan.')
    return [dict(proposals[identifier], selected_by=actor) for identifier in identifiers]


def automatic(plan):
    """Only named build/test entry points, under separate project-code permission."""
    found = []
    for item in plan.get('check_candidates', []):
        capability = environment_adapters.commands().get(item['arguments'][0])
        adapter = environment_adapters.ADAPTERS.get(capability)
        if adapter and adapter.automatic_check and invoke(adapter.automatic_check, item):
            found.append(item['id'])
    return found[:20]
