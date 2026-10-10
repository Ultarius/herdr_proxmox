"""Which jobs still reserve their participant sessions.

A job owns its sessions while it is non-terminal. `waiting_for_input` is the
state that is easy to miss: a discussion blocked on an operator decision can
resume later, so its participants must stay reserved and invisible to other
work. Guards that checked only `queued` and `running` silently released them,
and the generic release action could then detach a binding a discussion still
owned, leaving that discussion permanently unfinalizable.
"""

# States in which a job still reserves its participants' sessions. Terminal
# states are absent by omission: answered, artifact_ready, cancelled, completed,
# delivered, finished, input_sent, released, reported_complete.
#
# `waiting_for_members` is deliberately absent: a meeting in that state is
# waiting to acquire its members, not holding them, so it must not block the
# work that currently owns them.
ACTIVE_JOB_STATES = ('queued', 'running', 'waiting_for_input')


def active(job):
    """True while this job still owns the sessions it reserved."""
    return isinstance(job, dict) and job.get('state') in ACTIVE_JOB_STATES


def reserves(job, profile_id=None):
    """True while `job` reserves its participants.

    With a profile id, only when that profile is one of them. This mirrors the
    `participants` fallback used across the gateway: a job with no explicit
    participant list reserves the profile it was created for.
    """
    if not active(job):
        return False
    if profile_id is None:
        return True
    return profile_id in job.get('participants', [job.get('profile_id')])
