"""Bounded observations of OpenCode's visible folder permission prompt.

Terminal text is not a permission API. These details are for display only;
responses remain bound to the original console token and require an operator.
"""
import re


_ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
_PATH = re.compile(r'^(?:/|~/|\$HOME/|[A-Za-z]:[\\/])')
_TITLE = re.compile(r'^[\s△▲⚠!]*Permission required\s*$')
_DIRECTORY = re.compile(r'^(?:[←↳]\s*)?Access external directory\s+(.+)$')


def _line(value):
    value = value.strip()
    # Remove terminal gutters, preserving a literal | at the end of a path.
    if value[:1] in ('│', '┃', '┆', '|'):
        border, value = value[0], value[1:].lstrip()
        if value == border:
            return ''
        if value.endswith(' ' + border):
            value = value[:-1].rstrip()
    return value


def folder_access_request(output, status):
    """Return explicit patterns from the last complete, blocked folder panel.

    Unknown, partial or obsolete panels fall back to the original console.
    Never derive access from commands, infer a grant, or resolve these paths.
    """
    if status != 'blocked' or not isinstance(output, str):
        return None
    # Strip before cutting: a cut inside OSC/CSI could display escape fragments
    # as part of a path. CLI reads are already bounded to 160 visible lines.
    visible = _ANSI.sub('', output)[-20000:]
    visible = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', visible)
    lines = [_line(line) for line in visible.splitlines()]
    titles = [i for i, line in enumerate(lines) if _TITLE.fullmatch(line)]
    if not titles:
        return None
    panel = [line for line in lines[titles[-1] + 1:] if line and not all(c in '─━┌┐└┘╭╮╰╯' for c in line)]
    if len(panel) < 4:
        return None
    directory = _DIRECTORY.fullmatch(panel[0])
    if not directory or not _PATH.match(directory[1]) or panel[1] != 'Patterns':
        return None
    paths, truncated, menu = [], False, False
    for index, line in enumerate(panel[2:], 2):
        if all(option in line for option in ('Allow once', 'Allow always', 'Reject')):
            # A subsequent command/question means this panel is retained history.
            trailing = panel[index + 1:]
            if any(not (s.startswith('ctrl+') or s.startswith('⇆')) for s in trailing):
                return None
            menu = True
            break
        if not line.startswith('- ') or not _PATH.match(line[2:]):
            return None  # Wrapped/unknown layouts must be reviewed in the console.
        path = line[2:]
        if len(path) > 1000:
            truncated = True
        elif path not in paths:
            if len(paths) < 20:
                paths.append(path)
            else:
                truncated = True
    if not menu or not paths:
        return None
    return dict(paths=paths, truncated=truncated, source='visible_terminal')
