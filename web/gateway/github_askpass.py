"""Git invokes this helper; the token is read from disk, never put in argv."""
import os
import sys
from github_api import GitHub

if __name__ == '__main__':
    config = GitHub(os.environ['HERDR_GITHUB_CONFIG']).configuration()
    sys.stdout.write('x-access-token\n' if 'username' in sys.argv[-1].lower() else config['token'] + '\n')
