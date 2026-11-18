"""Trust a managed project for Codex before an agent starts there.

Codex asks "Trust this folder?" the first time it runs in a project and waits
for a key press. A gateway-launched agent has nobody at the keyboard, so its
first prompt cannot be delivered. For project roots explicitly approved by
the deployment operator, the gateway can record the same user config entry
as Codex's "Trust and continue" choice before a launch.

An existing entry for the root, trusted or not, is left alone so an operator's
own choice wins, and a configuration file that does not parse, or is a
symbolic link, is never rewritten.
"""
from dataclasses import dataclass
import json
import logging
import os
import tempfile
import threading
import tomllib
from pathlib import Path

_LOCK = threading.Lock()


def trust_root(project, projects):
    """The folder Codex asks about: the repository root inside managed
    projects, else the project folder itself; None outside managed projects."""
    source = Path(project).resolve()
    managed = Path(projects).resolve()
    if not source.is_dir() or not source.is_relative_to(managed) or source == managed:
        return None
    for parent in (source, *source.parents):
        if not parent.is_relative_to(managed) or parent == managed:
            break
        marker = parent / '.git'
        if marker.is_symlink():
            return None
        if marker.is_dir():
            return parent
        if marker.is_file():
            from project_git import git
            try:
                common = Path(git(parent, 'rev-parse', '--path-format=absolute', '--git-common-dir',
                                  timeout=3).strip()).resolve()
            except ValueError:
                return None
            root = common.parent
            return root if common.name == '.git' and root != managed and root.is_relative_to(managed) else None
    return source


@dataclass(frozen=True)
class ChildrenOf:
    """Every project root directly inside ``path``, as ``"ventures/*"`` approves.

    The gateway creates a new notebook for every venture and for the studio,
    each a new root Codex asks about. One folder entry approves them all,
    without approving the folder itself or anything deeper."""
    path: Path


def is_approved(root, roots):
    return root in roots or any(isinstance(entry, ChildrenOf) and root.parent == entry.path for entry in roots)


def approved_roots(value, projects):
    """Read project roots from a deployment setting, relative to the managed
    directory or absolute. An entry ending in ``/*`` approves each project
    directly inside that folder."""
    if not value:
        return frozenset()
    managed = Path(projects).resolve()
    try:
        names = json.loads(value)
        if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() or '\0' in name
                                               for name in names):
            raise ValueError
        roots = set()
        for name in names:
            folder = name.strip().rstrip('/')
            if folder.endswith('/*'):
                parent = (managed / folder[:-2]).resolve()
                # A folder of projects, never the projects directory itself.
                if parent == managed or not parent.is_relative_to(managed) or '*' in folder[:-2]:
                    raise ValueError
                roots.add(ChildrenOf(parent))
                continue
            root = (managed / folder).resolve()
            if root == managed or not root.is_relative_to(managed) or '*' in folder:
                raise ValueError
            roots.add(root)
    except (OSError, ValueError, RuntimeError) as error:
        raise ValueError('HERDR_CODEX_TRUST_PROJECTS must be a JSON list of project roots inside HERDR_PROJECTS, '
                         'or folders of them written as "ventures/*".') from error
    return frozenset(roots)


def ensure_trusted(project, projects, home=None, *, approved=(), codex_home=None):
    """Record Codex trust for the project's root; True when an entry was added."""
    if not approved:
        return False
    root = trust_root(project, projects)
    if root is None or not is_approved(root, approved):
        return False
    directory = Path(codex_home) if codex_home else Path(home or Path.home()) / '.codex'
    if not directory.is_absolute():
        return False
    config = directory / 'config.toml'
    with _LOCK:
        if directory.is_symlink() or config.is_symlink() or (config.exists() and not config.is_file()):
            logging.warning('Codex trust not recorded: %s is not a regular configuration file.', config)
            return False
        try:
            original = config.read_bytes() if config.exists() else None
            text = (original or b'').decode('utf-8')
            current = tomllib.loads(text)
        except (UnicodeError, tomllib.TOMLDecodeError):
            logging.warning('Codex trust not recorded: %s does not parse.', config)
            return False
        projects_table = current.get('projects')
        if projects_table is not None and not isinstance(projects_table, dict):
            return False
        for name, entry in (projects_table or {}).items():
            path = Path(name)
            if path.is_absolute() and (root == path or (root.is_relative_to(path)
                    and isinstance(entry, dict) and entry.get('trust_level') == 'untrusted')):
                return False
        addition = ('\n' if text and not text.endswith('\n') else '') + (
            '\n' if text else '') + '[projects.' + json.dumps(str(root), ensure_ascii=False) + ']\ntrust_level = "trusted"\n'
        updated = text + addition
        # Never write a file Codex could not read back.
        try:
            tomllib.loads(updated)
        except tomllib.TOMLDecodeError:
            return False
        config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=config.parent, prefix='.config.toml.', suffix='.tmp')
        try:
            with os.fdopen(handle, 'w', encoding='utf-8', newline='') as stream:
                stream.write(updated)
            os.chmod(temporary, 0o600)
            if config.is_symlink() or (config.read_bytes() if config.exists() else None) != original:
                return False
            os.replace(temporary, config)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
