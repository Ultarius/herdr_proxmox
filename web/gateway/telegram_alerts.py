"""Bounded owner alerts from authoritative records; never copy output or errors."""
from contextlib import closing
import hashlib
import json

MAX_ALERTS = 200
_cursor = 0


class AlertBatch(list):
    """Namespaces whose absent alerts cannot yet be considered resolved."""
    preserve_prefixes = ()


def label(value, fallback='Unknown'):
    return ' '.join(value.split())[:160] if isinstance(value, str) and value.strip() else fallback


def key(source, *values):
    return source + ':' + hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def collect_alerts(organizations, contributions, coordinator):
    global _cursor
    data = organizations.snapshot(live_status=True)
    alerts = AlertBatch()
    preserve = set()

    def add(source, values, title, lines):
        alerts.append(dict(key=key(source, *values), title=title, message='\n'.join(lines)))

    names = {p['id']: label(p.get('name')) for p in data.get('profiles', [])}
    for profile_id, state in data.get('member_states', {}).items():
        run_id = state.get('run_id')
        if state.get('status') in ('unknown', 'unavailable') and run_id:
            preserve.add(key('agent', run_id, 'blocked'))
        if state.get('status') == 'blocked' and run_id:
            add('agent', (run_id, 'blocked'), 'Agent needs a decision', [
                'Agent: ' + names.get(profile_id, 'Unknown'), 'Run: ' + label(run_id),
                'Open the agent console in Herdr to inspect the request and respond.'])
    for job in data.get('jobs', []):
        state = job.get('state')
        issue = state if state in ('waiting_for_input', 'needs_attention', 'uncertain') else (
            'blocked' if (job.get('delivery') or {}).get('stage') == 'blocked' else None)
        if issue:
            add('job', (job['id'], issue), 'Agent job needs attention', [
                'Agent: ' + names.get(job.get('profile_id'), 'See the job in Herdr'),
                'Job: ' + label(job.get('id')), 'State: ' + issue.replace('_', ' '),
                'Open the job in Herdr to inspect the issue and choose an action.'])

    # Avoid Contributions.snapshot's repository/GitHub probes. Only durable
    # records are needed, including when those services are unreachable.
    with closing(contributions.connect()) as db:
        rows = db.execute("SELECT data FROM tasks WHERE json_extract(data, '$.state') NOT IN "
                          "('completed','merged','closed','cancelled') ORDER BY rowid DESC LIMIT 1001").fetchall()
    if len(rows) > 1000:
        preserve.add('task-')
    for row in rows[:1000]:
        task = json.loads(row[0])
        context = ['Task: ' + label(task.get('title')), 'Task ID: ' + label(task.get('id')),
                   'Project: ' + label(task.get('repository')),
                   'Open the task in Herdr to inspect evidence and choose an action.']
        candidate = task.get('head_sha')
        if task.get('state') == 'review_ready' and not task.get('auto_publish'):
            add('task-review', (task['id'], candidate), 'Task ready for review', context)
        if task.get('automation_error'):
            add('task-automation', (task['id'], candidate, task.get('state')), 'Task automation needs attention', context)
        if (task.get('delivery_workflow') or {}).get('state') == 'attention':
            add('task-delivery', (task['id'], candidate), 'Task delivery needs attention', context)
        repair = task.get('pr_repair') or {}
        if repair.get('state') in ('waiting_for_input', 'monitoring_unavailable', 'validation_failed', 'needs_attention'):
            add('task-repair', (task['id'], repair.get('id'), repair.get('state'), repair.get('candidate')),
                'Task repair needs attention', context)
        build = (task.get('builds') or {}).get(candidate) or {}
        if build.get('blocker') == 'policy_changed':
            add('task-environment', (task['id'], candidate), 'Validation environment needs review', context)

    # The public coordinator snapshot exposes only the latest 100 events.
    events = coordinator.snapshot().get('events', [])
    if len(events) >= 100:
        preserve.add('integration:')
    for event in events:
        state = event.get('state')
        if state in ('blocked', 'needs_attention', 'validation_pending', 'validation_failed'):
            add('integration', (event['id'], state), 'Integration needs attention', [
                'Project: ' + label(event.get('repository')), 'Agent: ' + label(event.get('name')),
                'Integration: ' + label(event.get('id')), 'State: ' + state.replace('_', ' '),
                'Open integration blockers in Herdr to inspect evidence and review available actions.'])
    if len(alerts) > MAX_ALERTS:
        start = _cursor % len(alerts)
        alerts[:] = alerts[start:] + alerts[:start]
        preserve.update(a['key'] for a in alerts[MAX_ALERTS:])
        _cursor = (start + MAX_ALERTS) % len(alerts)
        del alerts[MAX_ALERTS:]
    alerts.preserve_prefixes = tuple(sorted(preserve))
    return alerts
