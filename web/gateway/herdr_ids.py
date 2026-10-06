"""Validate Herdr compact IDs, whose counters include letters (for example wA)."""
import re


def is_workspace_id(value):
    return isinstance(value, str) and re.fullmatch(r'w[0-9A-Za-z]+', value) is not None


def is_pane_id(value, workspace=None):
    if not isinstance(value, str) or re.fullmatch(r'w[0-9A-Za-z]+:p[0-9A-Za-z]+', value) is None:
        return False
    return workspace is None or value.split(':', 1)[0] == workspace
