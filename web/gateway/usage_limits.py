"""Recognize provider usage status in a visible terminal."""
from datetime import timedelta
import re

from herdr_errors import preview

# Match recognizable provider status text at the start of a line. A generic
# mention of rate limits, backoff, or upgrading is common in agent replies.
_STATUS = re.compile(
    r"^(?:(?:go|opencode|codex|claude(?: code)?)\s+(?:error:\s*)?)?"
    r"(?:(?:\d+\s*(?:hour|day)\s+)?(?:usage |rate )?limit (?:reached|exceeded)|"
    r"(?:you(?:['’]ve| have) )?(?:hit|reached) your (?:usage )?limit|"
    r"quota exceeded|you exceeded your (?:current )?quota|out of credits|insufficient credits)"
    r"(?=\s*(?:$|[.!:,…·\[]))", re.I)
_RESET_LINE = re.compile(r'^(?:it|your (?:usage )?limit) will reset\b', re.I)
_RESET = re.compile(r'\b(?:reset|resets|try again|available again)\s+(?:at\b|in\b)', re.I)
_DURATION = r'(?:\d{1,9}\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,]*(?:and\s+)?)+'
_RESET_IN = re.compile(r'\b(?:reset|resets|try again|available again)\s+in\s+(' + _DURATION + ')', re.I)
_RETRY = re.compile(r'\[retrying in\s+(' + _DURATION + r')(?:attempt #(\d+))?\s*\]', re.I)
_DURATION_LINE = re.compile('^' + _DURATION, re.I)
_PART = re.compile(r'(\d+)\s*([dhms])', re.I)
_UNITS = dict(d=86400, h=3600, m=60, s=1)
_MAX_SECONDS = 31 * 86400


def _duration(match):
    if not match:
        return None
    seconds = sum(int(amount) * _UNITS[unit.lower()] for amount, unit in _PART.findall(match[1]))
    return seconds if 0 <= seconds <= _MAX_SECONDS else None


def detect(output):
    """Return status plus relative reset and CLI retry times when available."""
    if not isinstance(output, str):
        return None
    lines = [line.strip() for line in preview(output, limit=40000).splitlines()][-40:]
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].lstrip('│┃ ')
        status = _STATUS.match(line)
        if not status:
            continue
        # Allow the provider's continuation or retry badge to wrap. Arbitrary
        # lines elsewhere in the screen must not provide the timing.
        for following in lines[index + 1:index + 3]:
            following = following.lstrip('│┃ ')
            if not (_RESET_LINE.match(following) or _RETRY.match(following)
                    or (_RESET_IN.search(line) and _DURATION_LINE.match(following))):
                break
            line += ' ' + following
        retry = _RETRY.search(line)
        if not (_RESET.search(line) or retry):
            continue
        return dict(status=' '.join(status[0].lower().split()),
                    message=' '.join(_RETRY.sub('', line).split())[:240],
                    reset_after=_duration(_RESET_IN.search(line)), retry_after=_duration(retry),
                    attempt=retry[2] if retry else None)
    return None


def _deadline(seconds, previous_seconds, previous_at, now):
    if seconds is None:
        return previous_at
    observed_at = now + timedelta(seconds=seconds)
    # A static countdown may remain visible while the CLI is idle. A longer
    # countdown reported after expiry can be evidence of a new window.
    if previous_at is None or (previous_at <= now and previous_seconds is not None and seconds > previous_seconds):
        return observed_at
    return min(previous_at, observed_at)


def observe(hit, previous, now, unknown_limit):
    """Turn relative durations into deadlines for this session's status."""
    if not hit:
        return None
    previous = previous if previous and previous['status'] == hit['status'] else {}
    first_seen = previous.get('first_seen', now)
    reset_at = _deadline(hit['reset_after'], previous.get('reset_after'), previous.get('reset_at'), now)
    same_attempt = hit['attempt'] == previous.get('attempt')
    retry_at = _deadline(hit['retry_after'], previous.get('retry_after') if same_attempt else None,
                         previous.get('retry_at') if same_attempt else None, now)
    return dict(hit, first_seen=first_seen, reset_at=reset_at, retry_at=retry_at,
                until=reset_at or retry_at or first_seen + unknown_limit)
