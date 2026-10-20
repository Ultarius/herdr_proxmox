"""Publication routing and exact-revision release delivery evidence.

The route is frozen on task creation. Configuration changes affect future work;
legacy task publication and its delivery-inspection barrier are never silently
redirected during upgrade. Release evidence does not rewrite task lifecycle.
"""
import json

MODES = ('release', 'both', 'task')


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS delivery_migrations (id TEXT PRIMARY KEY)')
    if db.execute("SELECT 1 FROM delivery_migrations WHERE id='release-first-v1'").fetchone():
        return
    organizations = {row[0] for row in db.execute('SELECT id FROM policies')}
    organizations.update(row[0] for row in db.execute(
        "SELECT DISTINCT json_extract(data, '$.organization_id') FROM tasks WHERE json_extract(data, '$.organization_id') IS NOT NULL"))
    for identifier in organizations:
        row = db.execute('SELECT data FROM policies WHERE id=?', (identifier,)).fetchone()
        policy = json.loads(row[0]) if row else {}
        policy.setdefault('publication_mode', 'task')
        db.execute('INSERT OR REPLACE INTO policies VALUES (?,?)', (identifier, json.dumps(policy)))
    for identifier, raw in db.execute('SELECT id,data FROM tasks').fetchall():
        task = json.loads(raw)
        if 'publication_mode' not in task:
            task.update(publication_mode='task', publication_mode_basis='legacy_migration')
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task), identifier))
    db.execute("INSERT INTO delivery_migrations VALUES ('release-first-v1')")


def mode(task):
    # Pre-migration/imported records retain the original task route.
    value = task.get('publication_mode', 'task')
    if value not in MODES:
        raise ValueError('Task publication mode is invalid; inspect its recorded delivery route.')
    return value


def require_task_publication(task):
    if mode(task) == 'release':
        raise ValueError('This task uses release delivery. Publish its release candidate or choose Release this task only.')


def selected(policy, repository):
    value = (policy.get('publication_repositories') or {}).get(repository, policy.get('publication_mode', 'release'))
    if value not in MODES:
        raise ValueError('Choose release, both or task publication mode.')
    return value


def receipts_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS release_deliveries '
               '(task_id TEXT NOT NULL, release_id TEXT NOT NULL, manifest TEXT NOT NULL, data TEXT NOT NULL, '
               'PRIMARY KEY(task_id,release_id,manifest))')


def record_receipts(db, release):
    if release['state'] != 'published':
        return
    revision = release.get('revision') or {}
    if not revision.get('digest') or not release.get('published_at'):
        return
    pull = release.get('pull_request') or {}
    stage = (pull.get('remote_state') or 'pr_open') if pull.get('state') == 'complete' else 'branch_published'
    for member in revision.get('members', []):
        if (release.get('results') or {}).get(member['task_id'], {}).get('state') != 'incorporated':
            continue
        receipt = dict(release_id=release['id'], version=release['version'], manifest=revision['digest'],
            candidate_sha=member['candidate_sha'], combined_sha=release['resulting_sha'],
            policy_digest=revision['policy_digest'], stage=stage, published_at=release['published_at'],
            publication=release['publication'])
        if pull.get('state') == 'complete':
            receipt['pull'] = {k: pull.get(k) for k in ('number', 'url', 'head_sha', 'merge_sha', 'merged_at', 'checked_at')}
        db.execute('INSERT OR REPLACE INTO release_deliveries VALUES (?,?,?,?)',
                   (member['task_id'], release['id'], revision['digest'], json.dumps(receipt)))


def decorate(db, tasks):
    if not tasks:
        return
    records = {task['id']: task for task in tasks}
    for task in tasks:
        task['task_publication_allowed'] = mode(task) != 'release'
        task['release_deliveries'] = []
    rows = db.execute('SELECT task_id,data FROM (SELECT task_id,data, ROW_NUMBER() OVER '
        '(PARTITION BY task_id ORDER BY rowid DESC) AS rank FROM release_deliveries WHERE task_id IN (' +
        ','.join('?' for _ in records) + ')) WHERE rank<=10', tuple(records))
    for identifier, raw in rows:
        receipt = json.loads(raw)
        receipt['current_candidate_matches'] = receipt['candidate_sha'] == records[identifier].get('head_sha')
        records[identifier]['release_deliveries'].append(receipt)
