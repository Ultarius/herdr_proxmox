"""Managed Git notebooks for venture simulation.

The gateway owns these files and commits them once per tick. Agents only read
the notebook checkout; they never edit or push it.
"""
import csv
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import re
import shutil
import tempfile

import project_git
from repository_lock import repository_lock


def now():
    return datetime.now(timezone.utc)


def slug(value):
    value = re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')
    return (value or 'venture')[:40]


def _relative(root, value):
    root = Path(root).resolve()
    target = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError('Venture notebook must stay inside the projects folder.')
    return target


def _write(path, content):
    if path.is_symlink():
        raise ValueError('Venture notebook files must not be symbolic links.')
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def provision_studio(root, relative, organization_id):
    """Create the managed checkout used by a Venture Studio group."""
    root = Path(root).resolve()
    target = _relative(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise ValueError('Venture studio path must be a regular directory.')
        if (target / 'studio.md').is_file() and (target / '.git').exists():
            return target
        if any(target.iterdir()):
            raise ValueError('Venture studio notebook already contains files.')
    else:
        target.mkdir(mode=0o700)
        target.chmod(0o700)  # The mode above is subject to the umask.
        created = True
    try:
        project_git.git(target, 'init', '-b', 'main')
        with repository_lock(target):
            _write(target / 'studio.md',
                   '# Venture Studio\n\n'
                   '- Organization: ' + str(organization_id) + '\n'
                   '- Purpose: portfolio review and new-venture scouting.\n'
                   '- Created: ' + now().isoformat() + '\n')
            _write(target / '.gitignore', '.ci-cache/\n')
            project_git.git(target, 'add', '-A')
            project_git.git(target, 'commit', '-m', 'initialize venture studio')
    except Exception:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def provision(root, relative, venture):
    """Create an empty managed notebook; refuse any pre-existing content."""
    root = Path(root).resolve()
    target = _relative(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise ValueError('Venture notebook path must be a regular directory.')
        if any(target.iterdir()):
            raise ValueError('Venture notebook already contains files; choose a new venture.')
    else:
        target.mkdir(mode=0o700)
        target.chmod(0o700)  # The mode above is subject to the umask.
        created = True
    try:
        project_git.git(target, 'init', '-b', 'main')
        with repository_lock(target):
            _write(target / 'venture.md',
                   '# ' + venture['name'] + '\n\n' + venture.get('thesis', '').strip() + '\n\n'
                   + '- Organization: ' + str(venture['organization_id']) + '\n'
                   + '- Architecture: ' + venture['configuration']['simulation']['architecture'] + '\n'
                   + '- Created: ' + now().isoformat() + '\n')
            _write(target / 'product.md', '# Product\n\n' + venture.get('product', '').strip() + '\n')
            _write(target / 'ledger.csv', 'date,capital_before,burn,capital_after,score,tick_id\n')
            _write(target / '.gitignore', '.ci-cache/\n')
            (target / 'outreach').mkdir(exist_ok=True)
            (target / 'outreach' / '.gitkeep').touch()
            project_git.git(target, 'add', '-A')
            project_git.git(target, 'commit', '-m', 'initialize venture notebook')
    except Exception:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Venture notebook file is missing or not regular.')
    return path.read_text(encoding='utf-8')


def _csv_row(text, values):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(values)
    return text + stream.getvalue()


def _commit_owned(target, paths, message):
    """Commit only the new files the gateway wrote; True when one of them changed.

    Agents run as the same OS user and a read-only group is an instruction,
    so a file one drops into the notebook, or stages there, must not enter a
    commit that reads as gateway output."""
    project_git.git(target, 'add', '--', *paths)
    if not project_git.git(target, 'diff', '--cached', '--name-only', '--', *paths).strip():
        return False
    project_git.git(target, 'commit', '-m', message, '--', *paths)
    return True


def _committed(target, name, fallback):
    try:
        return project_git.git(target, 'show', 'HEAD:' + name)
    except ValueError:
        return fallback


def _staged_edits(target, names):
    """``{name: staged text}`` for files whose index entry differs from HEAD.

    None means the index holds no plain version of the file (a staged removal
    or a conflict), which only its owner can resolve."""
    staged = {}
    for name in names:
        try:
            text = project_git.git(target, 'show', ':' + name)
        except ValueError:
            staged[name] = None
            continue
        if text != _committed(target, name, None):
            staged[name] = text
    return staged


def _commit_contents(target, contents, apply, message):
    """Commit exact file contents on top of HEAD; returns the commit.

    Built on a private index, so someone's uncommitted edit in the same file
    stays theirs and enters no gateway commit. An unstaged edit remains in the
    working tree. A staged one stays staged, with ``apply[name]`` adding the
    gateway's lines to it, so committing it later keeps both."""
    head = project_git.git(target, 'rev-parse', 'HEAD').strip()
    staged = _staged_edits(target, contents)
    with tempfile.TemporaryDirectory(prefix='herdr-notebook-') as temporary:
        index, source = Path(temporary) / 'index', Path(temporary) / 'content'

        def blob(text):
            source.write_bytes(text.encode('utf-8'))
            return project_git.git(target, 'hash-object', '-w', '--no-filters', '--', str(source)).strip()
        project_git.git(target, 'read-tree', head, index_file=index)
        for name, text in contents.items():
            project_git.git(target, 'update-index', '--add', '--cacheinfo', '100644,' + blob(text) + ',' + name,
                            index_file=index)
        tree = project_git.git(target, 'write-tree', index_file=index).strip()
        commit = project_git.git(target, 'commit-tree', tree, '-p', head, '-m', message).strip()
        project_git.git(target, 'update-ref', 'HEAD', commit, head)
        plain = [name for name in contents if name not in staged]
        if plain:
            project_git.git(target, 'reset', '-q', '--', *plain)  # The real index follows, the files do not.
        for name, text in staged.items():
            if text is not None:
                project_git.git(target, 'update-index', '--cacheinfo',
                                '100644,' + blob(apply[name](text)) + ',' + name)
    return commit


def write_outreach(root, relative, draft):
    """Write one approved draft to ``outreach/`` and commit it. Returns the relative path."""
    target = _relative(Path(root), relative)
    if target.is_symlink() or not target.is_dir():
        raise ValueError('Venture notebook directory is unavailable.')
    name = draft['date'] + '-' + draft['persona'] + '-' + draft['id'][:8] + '.md'
    with repository_lock(target):
        folder = target / 'outreach'
        if folder.is_symlink():
            raise ValueError('Venture outreach folder must be a regular directory.')
        folder.mkdir(exist_ok=True)
        path = folder / name
        _write(path, '# ' + draft['subject'] + '\n\n'
               + '- Persona: ' + draft['persona'] + '\n- Language: ' + draft['language'] + '\n'
               + '- Stage: ' + str(draft.get('stage', '')) + '\n- Evidence: ' + ', '.join(draft.get('evidence', []))
               + '\n- Simulated score when drafted: ' + str(draft.get('score', '')) + '\n\n'
               + 'Review and send this yourself. Replace every {{placeholder}} first.\n\n---\n\n'
               + draft['body'].strip() + '\n')
        _commit_owned(target, ['outreach/' + name], 'approve outreach ' + draft['id'][:8])
    return 'outreach/' + name


def commit_tick(root, relative, tick, product_changes, pricing):
    """Apply one tick idempotently, then commit only if something changed."""
    target = _relative(Path(root), relative)
    if target.is_symlink() or not target.is_dir():
        raise ValueError('Venture notebook directory is unavailable.')
    with repository_lock(target):
        project_git.git(target, 'rev-parse', '--show-toplevel')
        ledger = target / 'ledger.csv'
        ledger_text = _read(ledger)
        date = tick['simulated_date']
        row = [date, tick['ledger']['capital_before'], tick['ledger']['burn'], tick['ledger']['capital_after'],
               '' if tick['ledger'].get('score') is None else tick['ledger']['score'], tick['id']]

        def has_day(text):
            return any(item.get('date') == date for item in csv.DictReader(io.StringIO(text)))
        if not has_day(ledger_text):
            ledger_text = _csv_row(ledger_text, row)
            _write(ledger, ledger_text)
        product = target / 'product.md'
        product_text = _read(product)
        lines = ['- ' + date + ': [' + change['kind'] + '] ' + change['title'] + ' - '
                 + change['reason'] + ' (evidence: ' + ', '.join(change['evidence']) + ')'
                 for change in product_changes]
        if pricing.get('monthly') is not None:
            lines.append('- ' + date + ': pricing ' + str(pricing['monthly']) + ' per month - '
                         + str(pricing.get('reason', ''))[:600])

        def with_lines(text):
            additions = list(dict.fromkeys(line for line in lines if line not in text))
            return text.rstrip() + '\n' + '\n'.join(additions) + '\n' if additions else text
        updated = with_lines(product_text)
        if updated != product_text:
            _write(product, updated)
        # Measured against HEAD, so a retry after a crash still commits this
        # tick, and a local edit in either file is not part of it.
        contents = {}
        committed_ledger = _committed(target, 'ledger.csv', ledger_text)
        if not has_day(committed_ledger):
            contents['ledger.csv'] = _csv_row(committed_ledger, row)
        committed_product = _committed(target, 'product.md', updated)
        if with_lines(committed_product) != committed_product:
            contents['product.md'] = with_lines(committed_product)
        if not contents:
            return ''
        apply = {'ledger.csv': lambda text: text if has_day(text) else _csv_row(text, row),
                 'product.md': with_lines}
        return _commit_contents(target, contents, apply, 'tick ' + date + ' ' + tick['id'])
