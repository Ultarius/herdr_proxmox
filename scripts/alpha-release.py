"""Deterministic alpha release identity; invoked only after CI passes."""
import hashlib
import json
import os
from pathlib import Path
import re


def alpha_tag(branch, subject, run):
    if not branch.startswith('alpha/') or not branch[6:] or not subject.startswith('alpha:'):
        raise ValueError('Alpha publishing requires alpha/<feature> and an alpha: commit subject.')
    if not re.fullmatch(r'[1-9][0-9]*', str(run)):
        raise ValueError('Invalid workflow run number.')
    slug = re.sub(r'[^a-z0-9]+', '-', branch[6:].lower()).strip('-')[:48] or 'feature'
    digest = hashlib.sha256(branch.encode()).hexdigest()[:12]
    return f'alpha-{slug}-{digest}-b{run}'


if __name__ == '__main__':
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    subject = event['head_commit']['message'].splitlines()[0]
    tag = alpha_tag(os.environ['GITHUB_REF_NAME'], subject, os.environ['GITHUB_RUN_NUMBER'])
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.write(f'RELEASE_TAG={tag}\nRELEASE_CHANNEL=alpha\n')
