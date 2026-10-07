"""Running deployment identity; never infer a build SHA from a project checkout."""
import json
import os
from pathlib import Path
import re


def snapshot(root=None):
    root = Path(root or os.environ.get('HERDR_DEPLOYMENT_ROOT', '/opt/herdr-web'))
    try:
        data = json.loads((root / 'BUILD.json').read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        version = (root / 'VERSION').read_text().strip()[:120]
    except OSError:
        version = None
    sha = data.get('source_sha')
    build_id = data.get('build_id')
    return dict(build_id=build_id if isinstance(build_id, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,120}', build_id) else None,
                source_sha=sha if isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', sha) else None,
                gateway_version=version, package_version=version,
                deployment_mode='local' if data.get('deployment_mode') == 'local' else 'release')
