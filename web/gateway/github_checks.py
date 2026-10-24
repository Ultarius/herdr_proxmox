"""Missing optional GitHub evidence is unavailable, never a passing check."""
from github_api import GitHubError


def optional_evidence(github, path, field):
    try:
        result = github.request(path)
    except GitHubError as error:
        if error.status not in (403, 404):
            raise
        return ({field: []} if field else []), {'state': 'unavailable', 'github_status': error.status}
    return result, None
