"""Execution-local bindings derived from an immutable approved policy.

Adapters own environment creation and receipt semantics. The shared path owns
permission preflight, policy copying and command rebinding so launch, validation
and future environment providers cannot quietly use different orchestration.
"""
import copy
import subprocess

import environment_adapters
from environment_providers import invoke
from operation_metrics import measured


def requires_restore(plan):
    return any(recipe.get('requires_restore') is not False for item in plan.get('requirements', [])
               for recipe in item.get('restore_recipes', []))


def materialize_execution_policy(policy, checkout, caches, *, authorized=False, run=subprocess.run):
    bound = copy.deepcopy(policy)
    # Refuse the whole execution before any adapter has effects. SDK permission
    # does not authorize project-code restoration in a later component.
    for entry in bound['toolchains']:
        recipe = entry.get('restore_recipe')
        if recipe:
            adapter = environment_adapters.get(entry['name'])
            if not adapter.materialize_entry:
                raise ValueError('No execution materializer exists for ' + entry['name'])
            if recipe.get('requires_restore') is not False and not authorized:
                raise ValueError('Dependency restoration requires separate project-code permission.')
    receipts = []
    for entry in bound['toolchains']:
        if entry.get('restore_recipe'):
            adapter = environment_adapters.get(entry['name'])
            if entry.get('adapter_version', 1) != adapter.version:
                raise ValueError('The SDK adapter contract changed; prepare again.')
            operation = ('dependencies.restore' if entry['restore_recipe'].get('requires_restore') is not False
                         else 'execution.materialize')
            with measured(operation):
                receipt = invoke(adapter.materialize_entry, entry, checkout, caches, authorized=authorized, run=run)
                if not isinstance(receipt, dict) or receipt.get('state') != 'complete':
                    raise ValueError('The execution materializer did not return a completed restore receipt.')
            receipts.append(receipt)
    for check in bound.get('recipes', []):
        entries = [entry for entry in bound['toolchains'] if entry.get('component') == check['cwd']]
        originals = [entry for entry in policy['toolchains'] if entry.get('component') == check['cwd']]
        for original, runtime in zip(originals, entries):
            adapter = environment_adapters.get(original['name'])
            for command in adapter.commands:
                if (original.get('executables') and runtime.get('executables')
                        and check['arguments'][0] == adapter.command(original['executables'][0], command)):
                    check['arguments'][0] = adapter.command(runtime['executables'][0], command)
                    break
        check['bindings'] = entries
    return bound, receipts
