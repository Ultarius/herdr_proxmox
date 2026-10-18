"""Bind a newly created interactive shell through Herdr's native tab env API.

This records process inheritance, not confinement. Before task prompt delivery,
verification checks what the foreground agent actually inherited. Validation
still probes the effective SDK separately.
Different component SDKs use one cwd-aware dispatcher, never competing PATH entries.
"""
import os
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

import environment_adapters
import environment_dispatch
import toolchain_policy
from herdr_ids import is_pane_id


def binding(frozen, caches):
    """The environment a single agent shell can inherit, or a precise refusal.

    Only gateway-selected variables are sent to the daemon. In particular, the
    gateway's API credentials must not be copied into the pane's environment.
    The daemon continues supplying its normal agent credentials and Herdr ids.
    """
    policy = (frozen or {}).get('policy') or {}
    if not policy.get('plan_digest'):
        return None
    entries = policy.get('toolchains') or []
    identity = dict(plan_digest=policy['plan_digest'], policy_digest=frozen['digest'])
    if not entries or any(not entry.get('executables') for entry in entries):
        return dict(identity, state='unavailable', reason='The approved SDK bindings are not prepared.')
    conflicting = environment_dispatch.conflicting(entries)
    base = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}
    direct = [entry for entry in entries if entry['name'] not in conflicting]
    environment = (toolchain_policy.environment(dict(policy, toolchains=direct), base=base)
                   if direct else dict(base))
    for name in conflicting:
        environment.update(environment_adapters.get(name).dispatch_variables)
    # These caches belong to this execution, never to the shared SDK tree.
    caches = Path(caches).absolute()
    environment.update(environment_adapters.execution_caches(entries, caches))
    environment['HERDR_ENVIRONMENT_PLAN'] = policy['plan_digest']
    return dict(identity, state='requested', mechanism='herdr_tab_env',
                selection='component_dispatch' if conflicting else 'direct', environment=environment)


def configure(store, job, frozen, pane, workspace):
    """Create an owned, bound shell before agent start; never alter a live pane.

    The documented tab.create --env contract configures the daemon's new child,
    unlike setting env on the gateway's `herdr agent start` client process.
    An older runtime rejecting --env is a visible launch failure, never a silent
    fallback to an unbound shell. Existing legacy self-build launches are intact.
    """
    if not re.fullmatch(r'[a-f0-9]{32}', job['id']):
        raise ValueError('An environment binding requires an execution identity.')
    caches = store.path.parent / 'agent-environments' / job['id']
    checkout = job.get('worktree_path') or job.get('source_project') or frozen['checkout']
    from environment_execution import materialize_execution_policy
    from operation_metrics import measured
    with measured('execution.prepare') as timing:
        policy, restores = materialize_execution_policy(frozen.get('policy') or dict(toolchains=[]), checkout, caches,
            authorized=(frozen.get('policy') or {}).get('restore_authorized') is True)
    store.update_job(job['id'], environment_preparation_timing=timing)
    runtime = dict(frozen, policy=policy)
    if restores:
        store.update_job(job['id'], environment_restores=restores, execution_toolchain_policy=runtime)
    record = binding(runtime, caches)
    if record is None or record['state'] != 'requested':
        if record is not None:
            store.update_job(job['id'], environment_binding=record)
        return pane, workspace
    # Some runtime responses omit workspace metadata while still returning the
    # authoritative compact root pane id. Never substitute the focused workspace.
    workspace = workspace or (pane.split(':', 1)[0] if is_pane_id(pane) else None)
    if not workspace or not is_pane_id(pane, workspace):
        raise ValueError('The assigned shell has no verifiable workspace identity.')
    environment_adapters.execution_caches(runtime['policy']['toolchains'], caches, create=True)
    if record['selection'] == 'component_dispatch':
        launchers, digest = environment_dispatch.install(runtime['policy'], checkout, caches)
        record['environment']['PATH'] = str(launchers) + os.pathsep + record['environment']['PATH']
        record['dispatcher_digest'] = digest
    arguments = []
    for key, value in sorted(record['environment'].items()):
        if '\0' in value or '\n' in value or '\r' in value:
            raise ValueError('An SDK launch environment contains an invalid value.')
        arguments.extend(('--env', key + '=' + value))
    store.update_job(job['id'], launch_stage='environment_binding', environment_binding=record)
    created = store.command('tab', 'create', '--workspace', workspace, '--cwd', str(checkout),
                            '--label', 'Prepared environment', '--no-focus', *arguments)
    root = created.get('root_pane') if isinstance(created, dict) else None
    bound_pane = root.get('pane_id') if isinstance(root, dict) else None
    metadata = (created.get('tab') or created.get('workspace') or {}) if isinstance(created, dict) else {}
    owner = metadata.get('workspace_id') if isinstance(metadata, dict) else None
    if (not is_pane_id(bound_pane, workspace) or bound_pane == pane
            or owner is not None and owner != workspace):
        raise ValueError('Herdr did not return a new environment pane in the assigned workspace.')
    bound_workspace = workspace
    # Persist the new pane before closing only the original, newly created shell.
    # A crash remains uncertain under the existing launch recovery policy.
    store.update_job(job['id'], pane_id=bound_pane, workspace_id=bound_workspace,
                     environment_binding=dict(record, pane_id=bound_pane))
    store.command('pane', 'close', pane)
    store.wait_for_shell(bound_pane)
    activate_shell(store, bound_pane, record['environment'], caches)
    return bound_pane, bound_workspace


def activate_shell(store, pane, environment, caches):
    """Reapply exports after startup files, with an execution barrier.

    pane.run acknowledges queue admission, not execution. A fresh marker written
    by that same shell after its exports prevents agent.start from racing them.
    Only gateway-generated values are quoted into this owned, unused shell.
    """
    token = uuid.uuid4().hex
    marker = caches / ('activated-' + token)
    commands = ['export ' + key + '=' + shlex.quote(value) for key, value in sorted(environment.items())]
    commands.append('printf %s ' + shlex.quote(token) + ' > ' + shlex.quote(str(marker)))
    store.command('pane', 'run', pane, ' && '.join(commands))
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            try:
                with marker.open(encoding='ascii') as stream:
                    if stream.read(33) == token:
                        store.wait_for_shell(pane)
                        return
            except FileNotFoundError:
                pass
            time.sleep(.05)
        raise ValueError('Prepared shell activation was not confirmed; no agent was started.')
    finally:
        marker.unlink(missing_ok=True)


def _process_environment(pid):
    """Read only the initial exports inherited by this actual Linux child.

    Reading the shell's /proc environment would miss changes made by shell
    startup files. Reading the foreground agent's exports observes those changes.
    Never retain unrelated environment values, which can contain credentials.
    """
    if sys.platform != 'linux' or type(pid) is not int or pid <= 0:
        raise ValueError('Interactive environment verification requires a Linux process identity.')
    with open(f'/proc/{pid}/environ', 'rb') as stream:
        data = stream.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('The agent process environment exceeds the inspection limit.')
    return dict(item.split('=', 1) for item in data.decode('utf-8', 'strict').split('\0') if '=' in item)


def _process_start(pid):
    with open(f'/proc/{pid}/stat', encoding='utf-8') as stream:
        fields = stream.read(4096).rsplit(')', 1)[-1].split()
    if len(fields) < 20:
        raise ValueError('The agent process identity is incomplete.')
    return fields[19]  # Field 22: kernel start time; pid reuse changes this.


def verify_process(store, job):
    """Check process inheritance before sending the task, not SDK correctness.

    The pane and foreground leader must still agree after inspection. A missing
    process, incomplete runtime metadata or changed exports stops prompt delivery;
    it never converts a requested binding into a claimed verified environment.
    """
    record = job.get('environment_binding') or {}
    if record.get('state') != 'requested':
        return
    pane = job['pane_id']
    def identity():
        info = store.command('pane', 'process-info', '--pane', pane).get('process_info') or {}
        leader = info.get('foreground_process_group_id')
        processes = info.get('foreground_processes') or []
        if (type(leader) is not int or leader <= 0 or leader == info.get('shell_pid')
                or not any(isinstance(item, dict) and item.get('pid') == leader for item in processes)):
            raise ValueError('Cannot verify the foreground agent process for the prepared environment.')
        return leader
    pid = identity()
    try:
        started = _process_start(pid)
        actual = _process_environment(pid)
        if identity() != pid or _process_start(pid) != started:
            raise ValueError('The foreground agent changed during environment verification; no task prompt was sent.')
    except (OSError, UnicodeError) as error:
        raise ValueError('Could not inspect the agent process environment; no task prompt was sent.') from error
    different = [key for key, expected in record['environment'].items() if actual.get(key) != expected]
    if different:
        raise ValueError('The agent did not inherit the prepared environment (' + ', '.join(different) +
                         '); inspect shell startup settings before retrying. No task prompt was sent.')
    store.update_job(job['id'], environment_binding=dict(record, state='inherited', process_id=pid))
