"""Read only a temporary push credential, for exactly the recorded destination."""
import os
import sys
from github_api import GitHub
from repository_askpass import permitted

if __name__ == '__main__':
    try:
        prompt = sys.argv[-1]
        if not permitted(prompt, os.environ['HERDR_REPOSITORY']):
            raise ValueError('Untrusted credential prompt.')
        config = GitHub(os.environ['HERDR_GITHUB_CONFIG']).configuration()
        if set(config) != {'token'}:
            raise ValueError('Push credential file must not contain upstream credentials.')
        sys.stdout.write('x-access-token\n' if prompt.lower().startswith('username') else config['token'] + '\n')
    except (KeyError, ValueError, OSError):
        sys.exit(1)
