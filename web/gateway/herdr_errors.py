"""Structured Herdr CLI outcomes: error codes, delivery certainty and resume facts.

The CLI prints a JSON error envelope to stderr and exits non-zero. Codes such as
`agent_blocked` refuse before any input is sent, while `timeout` and
`agent_prompt_stalled` may follow submission. Callers must be able to tell those
apart without parsing prose.
"""
import json
import re

DELIVERY_NONE = 'none'
DELIVERY_UNKNOWN = 'unknown'
DELIVERY_SENT = 'sent'
DELIVERY_BLOCKED = 'blocked'
DELIVERY_STATES = (DELIVERY_NONE, DELIVERY_UNKNOWN, DELIVERY_SENT, DELIVERY_BLOCKED)

# Refusals that happen before any terminal input is sent.
_REFUSED = ('agent_blocked', 'agent_not_ready', 'agent_not_found', 'agent_not_running',
            'pane_not_found', 'pane_not_running', 'invalid_arguments', 'server_not_running')
# Failures where input may already have been delivered.
_AMBIGUOUS = ('timeout', 'agent_prompt_stalled', 'pane_input_failed', 'events_lost')
# Read-only operations that may be retried without side effects.
READ_ONLY = (('agent', 'list'), ('agent', 'get'), ('agent', 'read'), ('agent', 'explain'),
             ('workspace', 'list'), ('pane', 'list'), ('pane', 'read'), ('api', 'schema'), ('api', 'snapshot'),
             ('agent', 'wait'))


class HerdrError(ValueError):
    """A Herdr CLI failure carrying its code, operation and delivery certainty."""

    def __init__(self, message, code='herdr_error', operation='', delivery=DELIVERY_UNKNOWN):
        super().__init__(str(message)[:500] or 'Herdr command failed.')
        self.code = str(code or 'herdr_error')[:80]
        self.operation = str(operation or '')[:80]
        self.delivery = delivery if delivery in DELIVERY_STATES else DELIVERY_UNKNOWN

    def as_dict(self):
        return dict(code=self.code, operation=self.operation, delivery=self.delivery, message=str(self)[:500])


def parse_error(stderr, operation=''):
    """Parse the CLI JSON error envelope, falling back to bounded plain text."""
    text = (stderr or '').strip()
    code, message = 'herdr_error', text[:500]
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get('error')
        if isinstance(error, dict):
            code = error.get('code') or code
            message = error.get('message') or message
        elif isinstance(error, str) and error:
            message = error[:500]
        code = data.get('code') or code
        message = str(data.get('message') or message)[:500]
    if code in _REFUSED:
        delivery = DELIVERY_NONE
    elif code in _AMBIGUOUS:
        delivery = DELIVERY_UNKNOWN
    else:
        delivery = DELIVERY_UNKNOWN
    return HerdrError(message or 'Herdr command failed; start Herdr over SSH first.', code, operation, delivery)


def is_read_only(args):
    return tuple(args[:2]) in READ_ONLY


def server_not_running(error):
    """True when a Herdr failure means nothing is listening on the API socket.

    The structured code is authoritative; the message fragment only covers
    callers that carry raw CLI text instead of a parsed error.
    """
    return getattr(error, 'code', '') == 'server_not_running' or 'server_not_running' in str(error)


def parse_resume(agent):
    """Read resume facts without trusting free-form command text.

    Accepts a structured mapping with arguments and a reference. A plain string
    or a mapping without structured arguments is reported but never executed:
    the gateway only resumes with adapter arguments it can validate.
    """
    source = agent.get('resume') if isinstance(agent, dict) else None
    if source is None:
        return dict(state='unavailable', reason='Herdr does not report a resume reference for this agent.')
    if isinstance(source, dict):
        reference = source.get('reference') or source.get('session') or source.get('id')
        args = source.get('args')
        if (isinstance(reference, str) and reference
                and isinstance(args, list) and args and all(isinstance(a, str) and 0 < len(a) <= 200 for a in args)):
            return dict(state='verified', reference=reference[:200], args=[a for a in args][:20],
                        source=str(source.get('source') or 'herdr')[:80])
        if isinstance(reference, str) and reference:
            return dict(state='reported', reference=reference[:200],
                        reason='Herdr reported a resume reference without structured arguments; '
                               'the gateway will not execute free-form command text.')
        if isinstance(source.get('command'), str):
            return dict(state='reported', reference=source['command'][:200],
                        reason='Resume command text is not executed; use Start with saved context.')
    if isinstance(source, str) and source:
        return dict(state='reported', reference=source[:200],
                    reason='Resume command text is not executed; use Start with saved context.')
    return dict(state='unavailable', reason='Herdr resume data has an unsupported shape.')


def parse_completion(agent):
    """Baseline for completion detection, scoped to the server/session lifetime."""
    if not isinstance(agent, dict):
        return None
    seq = agent.get('completion_seq')
    if not isinstance(seq, int):
        return None
    scope = agent.get('server_session') or agent.get('session_id') or agent.get('session') or ''
    return dict(seq=seq, scope=str(scope)[:120])


def completion_after_baseline(baseline, agent):
    """True when the observed completion is newer than the recorded baseline.

    Returns None when the evidence is insufficient (missing baseline, different
    server/session scope or missing sequence). Never treats a pre-dispatch
    completion as this prompt's work.
    """
    current = parse_completion(agent)
    if not baseline or not current or not isinstance(baseline.get('seq'), int):
        return None
    if baseline.get('scope') and baseline['scope'] != current.get('scope'):
        return None
    return current['seq'] > baseline['seq']


def delivery_for(error):
    """Delivery certainty for a failure raised during a prompt-like operation."""
    if isinstance(error, HerdrError):
        if error.code == 'agent_blocked':
            return DELIVERY_BLOCKED
        return error.delivery
    return DELIVERY_UNKNOWN


def may_auto_retry(delivery):
    """Only failures known not to have sent input may be retried automatically."""
    return delivery == DELIVERY_NONE


def preview(text, limit=600):
    """Bounded visible-screen preview for a blocked agent; ANSI stripped."""
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(text or ''))
    lines = [line.rstrip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line.strip())[-limit:]


HANDOVER_FIELDS = ('state', 'decisions', 'questions', 'next_step')


def parse_handover(receipt):
    """Normalize an agent-authored handover note; claims only, bounded.

    Invalid or oversized fields are dropped rather than failing the receipt:
    the handover is an aid for the next agent, not evidence.
    """
    handover = receipt.get('handover') if isinstance(receipt, dict) else None
    if not isinstance(handover, dict):
        return None
    note = {}
    for field in HANDOVER_FIELDS:
        value = handover.get(field)
        if isinstance(value, str) and value.strip():
            note[field] = preview(value, 1500)
    return note or None


def format_handover(note):
    """Render a normalized handover note for prompts and knowledge records."""
    if not isinstance(note, dict):
        return ''
    labels = (('state', 'State'), ('decisions', 'Decisions'),
              ('questions', 'Open questions'), ('next_step', 'Next step'))
    parts = [label + ': ' + note[field] for field, label in labels if note.get(field)]
    return '\n'.join(parts)

