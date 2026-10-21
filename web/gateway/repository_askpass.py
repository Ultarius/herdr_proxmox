"""Answer only GitHub credential prompts for the explicitly selected repository."""
import os
import re
import sys
from urllib.parse import urlsplit
from github_api import GitHub


def permitted(prompt, repository):
    if not prompt.lower().startswith(('username for ', 'password for ')):
        return False
    match = re.fullmatch(r"(?:username|password) for ['\"](https://[^'\"\s]+)['\"]:\s*", prompt, re.IGNORECASE)
    if match is None:
        return False
    try:
        url = urlsplit(match[1])
        return (url.scheme == 'https' and url.hostname == 'github.com' and url.port in (None, 443)
                and not url.query and not url.fragment
                and url.path.strip('/').removesuffix('.git').lower() == repository.lower())
    except ValueError:
        return False


if __name__ == '__main__':
    try:
        prompt = sys.argv[-1]
        if not permitted(prompt, os.environ['HERDR_REPOSITORY']):
            raise ValueError('Untrusted credential prompt.')
        token = GitHub(os.environ['HERDR_GITHUB_CONFIG']).configuration()['token']
        sys.stdout.write('x-access-token\n' if prompt.lower().startswith('username') else token + '\n')
    except (ValueError, KeyError, OSError):
        sys.exit(1)
