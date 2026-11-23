"""Deterministic venture ledger, survival score and role-aware meeting brief.

Agents supply judgment (cited events, bounded deltas, objections, drafts);
this module does the arithmetic, so a venture can be replayed from its tick
records and figures cannot drift across days. Nothing here performs I/O.
"""
from datetime import datetime, timedelta, timezone
import math

# Version 2: quiet days no longer count towards qualification, a qualifying
# day needs a paying customer, and price is anchored to a reference price.
FORMULA_VERSION = 2
DELTA_STEP = 5
PRESSURES = ('competition', 'regulatory', 'delivery_risk')
DELTAS = ('demand',) + PRESSURES
ROLES = ('analyst', 'founder', 'red_team', 'outreach')
ROLE_LABELS = dict(analyst='Analyst', founder='Founder', red_team='Red Team', outreach='Outreach writer')
ROLE_HINTS = dict(analyst=('analyst', 'research', 'scout'),
                  founder=('founder', 'product', 'ceo', 'builder'),
                  red_team=('red team', 'red-team', 'redteam', 'critic', 'skeptic', 'risk'),
                  outreach=('outreach', 'sales', 'writer', 'copy'))
RUNWAY_HORIZON_DAYS = 180
MAX_PRICE_STEP = 0.5
MAX_CUSTOMERS = 5
CUSTOMER_DEMAND = 60
PRICE_CEILING = 4
PRICE_DRAG_PER_DOUBLING = 10
# The one place these lists live: validation, the meeting brief and both
# dashboards (through the snapshot's ``options``) read them from here.
ARCHITECTURES = dict(saas='SaaS app', api='Headless API', chat_bot='Chat bot')
PERSONA_LABELS = dict(cto='CTO / IT director', cfo='CFO', operations='Operations manager')
STAGE_LABELS = dict(survey='Discovery survey', waitlist='Fake-door waitlist', paid_pilot='Paid concierge pilot')


def initial_state():
    return dict(scores={key: 50 for key in DELTAS}, demand_pipeline=[], qualifying_days=0,
                monthly_price=None, revenue_total=0, formula_version=FORMULA_VERSION)


def _clamp(value, low=0, high=100):
    return max(low, min(high, value))


def convert(amount, currency, market):
    """Convert an amount into the base currency with the configured pair.

    ``fx_pair`` ``LEFT/RIGHT`` at ``fx_rate`` means one LEFT buys ``fx_rate``
    RIGHT, the usual quotation (``USD/JPY`` 150).
    """
    base = market['currency']
    if currency == base:
        return amount
    left, right = market['fx_pair'].split('/')
    rate = market['fx_rate']
    if currency == left and base == right:
        return amount * rate
    if currency == right and base == left:
        return amount / rate
    raise ValueError('Burn line currency must be the base currency or one side of the FX pair.')


def daily_burn(configuration):
    """Burn lines are monthly costs; a tick is one day.

    Any real cost burns at least one unit a day. Rounding a small monthly
    burn to zero would mean capital never falls, and the reference price
    derived from it would switch off the price ceiling."""
    monthly = sum(convert(line['amount'], line['currency'], configuration['market'])
                  for line in configuration['simulation']['burn'])
    return max(1, int(round(monthly / 30))) if monthly > 0 else 0


def change_limit(strictness):
    """A stricter Red Team admits fewer product changes per day (scope-creep guard)."""
    return 3 if strictness <= 3 else 2 if strictness <= 7 else 1


def paying_customers(demand):
    return 0 if demand < CUSTOMER_DEMAND else min(MAX_CUSTOMERS, 1 + (demand - CUSTOMER_DEMAND) // 10)


def reference_price(configuration):
    """The monthly price the ideal customer accepts without resistance.

    ``simulation.reference_price`` when the operator set one; otherwise the
    monthly burn, the price at which a single customer covers the costs.
    Zero (no burn, no anchor) leaves the price unanchored.
    """
    explicit = configuration['simulation'].get('reference_price') or 0
    return int(explicit) if explicit > 0 else daily_burn(configuration) * 30


def bounded_price(proposed, previous, reference=0):
    """Agents move the price at most 50% a day and never past the ceiling.

    The ceiling (``PRICE_CEILING`` x the reference price) also bounds the
    first price, which has no previous value to be measured against.
    """
    if proposed is None:
        return previous
    price = proposed
    if previous:
        # Whole prices, rounded inward: a step never exceeds the limit either way.
        price = _clamp(price, math.ceil(previous * (1 - MAX_PRICE_STEP)),
                       math.floor(previous * (1 + MAX_PRICE_STEP)))
    if reference:
        price = min(price, reference * PRICE_CEILING)
    return price


def price_drag(price, reference):
    """Demand points lost to price: ten for every doubling above the reference."""
    if not price or not reference or price <= reference:
        return 0
    return int(round(PRICE_DRAG_PER_DOUBLING * math.log2(price / reference)))


def breakdown(scores, runway_days, red_team, configuration):
    """Each factor's points in the survival score, so weights can be tuned against history."""
    return {key: round(value, 2) for key, value in _parts(scores, runway_days, red_team, configuration).items()}


def _parts(scores, runway_days, red_team, configuration):
    market, simulation = configuration['market'], configuration['simulation']
    runway = 100 if runway_days is None else min(100, runway_days * 100 // RUNWAY_HORIZON_DAYS)
    # Legacy-workflow bias makes delivery risk matter more (fax/paper fallbacks).
    weights = dict(runway=0.25, demand=0.30, competition=0.15, regulatory=0.15,
                   delivery_risk=0.10 + 0.025 * (market['legacy_bias'] - 1))
    health = dict(runway=runway, demand=scores['demand'],
                  **{key: 100 - scores[key] for key in PRESSURES})
    total = sum(weights.values())
    parts = {key: weights[key] * health[key] / total for key in weights}
    parts['red_team_penalty'] = -min(30, sum(item['severity'] for item in red_team) * simulation['red_team'] / 10)
    return parts


def survival(scores, runway_days, red_team, configuration):
    # Score from unrounded parts; the rounded breakdown is for display only.
    return int(_clamp(round(sum(_parts(scores, runway_days, red_team, configuration).values()))))


def step(state, configuration, capital, deltas, red_team, proposed_price, judged=True):
    """Advance one simulated day. Returns ``(state, ledger)``; inputs are not mutated.

    ``judged`` is false on a quiet day (no news, no meeting). Burn, revenue and
    the demand pipeline still move, but the day neither counts towards
    qualification nor resets it: surviving silence proves nothing.
    """
    state = dict(initial_state(), **(state or {}))
    scores = dict(state['scores'])
    # Sales-cycle friction: today's demand judgment lands (friction - 1) days later.
    lag = configuration['market']['sales_cycle'] - 1
    pipeline = list(state['demand_pipeline']) + [int(deltas.get('demand', 0))]
    landed = 0
    while len(pipeline) > lag:
        landed += pipeline.pop(0)
    scores['demand'] = _clamp(scores['demand'] + landed * DELTA_STEP)
    for key in PRESSURES:
        scores[key] = _clamp(scores[key] + int(deltas.get(key, 0)) * DELTA_STEP)
    reference = reference_price(configuration)
    price = bounded_price(proposed_price, state['monthly_price'], reference)
    drag = price_drag(price, reference)
    # No price (or a free one) means nobody is paying, however keen demand is;
    # otherwise a venture could qualify without ever pricing its product.
    customers = paying_customers(scores['demand'] - drag) if price else 0
    revenue = int(round((price or 0) * customers / 30))
    burn = daily_burn(configuration)
    after = max(0, capital - burn + revenue)
    net = burn - revenue
    runway = None if net <= 0 else after // net
    score = survival(scores, runway, red_team, configuration)
    parts = breakdown(scores, runway, red_team, configuration)
    threshold = configuration['simulation']['survival_threshold']
    if not judged:
        qualifying = state['qualifying_days']
    else:
        # A qualifying day is a judged day at or above the threshold with at
        # least one paying customer at the current price.
        qualifying = state['qualifying_days'] + 1 if score >= threshold and customers else 0
    state.update(scores=scores, demand_pipeline=pipeline, qualifying_days=qualifying, monthly_price=price,
                 revenue_total=state['revenue_total'] + revenue, formula_version=FORMULA_VERSION)
    ledger = dict(capital_before=capital, burn=burn, revenue=revenue, customers=customers,
                  capital_after=after, runway_days=runway, score=score, score_breakdown=parts, scores=scores,
                  landed_demand=landed, monthly_price=price, reference_price=reference, price_drag=drag,
                  judged=bool(judged), qualifying_days=qualifying,
                  required_days=configuration['simulation']['consecutive_days'],
                  formula_version=FORMULA_VERSION)
    return state, ledger


def outcome(ledger, configuration):
    """``failed``, ``qualified`` or ``None`` after a tick."""
    if ledger['capital_after'] <= 0:
        return 'failed'
    if ledger['qualifying_days'] >= configuration['simulation']['consecutive_days']:
        return 'qualified'
    return None


def next_slot(hour, at=None):
    at = at or datetime.now(timezone.utc)
    slot = at.replace(hour=hour, minute=0, second=0, microsecond=0)
    return slot if slot > at else slot + timedelta(days=1)


def assign_roles(people, explicit=None):
    """Map the four roles onto group members.

    ``people`` is ``[(profile_id, name)]`` with the facilitator first. Explicit
    assignments win, then name hints, then the least-loaded member (so the
    facilitator takes the Analyst role in a small group).
    """
    explicit = explicit or {}
    ids = [identity for identity, _ in people]
    roles = {role: [] for role in ROLES}
    for identity, role in explicit.items():
        if identity in ids and role in roles:
            roles[role].append(identity)
    for role in ROLES:
        if roles[role]:
            continue
        for identity, name in people:
            taken = any(identity in owners for owners in roles.values())
            if not taken and any(hint in str(name).lower() for hint in ROLE_HINTS[role]):
                roles[role].append(identity)
                break
    for role in ROLES:
        if roles[role] or not ids:
            continue
        load = {identity: sum(identity in owners for owners in roles.values()) for identity in ids}
        roles[role].append(min(ids, key=lambda identity: (load[identity], ids.index(identity))))
    return roles


ETIQUETTE = {
    'ja': 'Formal business Japanese (keigo). Address the recipient as {{company}} {{name}}様, open with a short '
          'courtesy line, give the cited news as the reason for writing, ask permission rather than push, '
          'and close with よろしくお願いいたします. No exaggerated claims.',
    'en': 'Plain, direct business English. Lead with the cited news and the cost it creates; one clear ask; '
          'under 120 words.',
    'de': 'Formal German (Sie), factual and compliance-aware; one clear ask.',
    'nl': 'Direct, polite Dutch (u); short; one clear ask.',
    'fr': 'Formal French (vous) with a courteous close; one clear ask.',
    'ko': 'Formal Korean (합니다체) with an honorific address (님); one clear ask.',
    'zh': 'Formal Simplified Chinese business tone (您); one clear ask.',
}
PERSONA = dict(
    cto='Pitch to a CTO/IT director: integration effort, security, data residency and privacy law, API/SLA.',
    cfo='Pitch to a CFO: cost of the problem in the base currency, price, payback period and tax treatment.',
    operations='Pitch to an operations manager: workflow steps removed, hours saved, fallback for paper/fax '
               'or legacy systems.')
STAGE = dict(
    survey='CTA: ask for a 15-minute discovery conversation or three short questions. Make no product claims.',
    waitlist='CTA: invite the reader to a waitlist at {{waitlist_link}}; describe the product as upcoming.',
    paid_pilot='CTA: offer a concierge pilot for one workflow at the current monthly price.')


def etiquette(language):
    # The configuration's language pattern allows at most two languages.
    parts = [part for part in language.replace(',', '+').split('+') if part]
    lines = [ETIQUETTE.get(part, 'Formal business register appropriate to this language; one clear ask.')
             for part in parts]
    if len(parts) > 1:
        lines.append('Bilingual: write the ' + parts[0] + ' version first, then a short ' + parts[1] + ' summary.')
    return ' '.join(lines)


def brief(configuration, roles, names, standing):
    """The role-aware part of the meeting prompt."""
    simulation, outreach = configuration['simulation'], configuration['outreach']
    sources = configuration.get('sources') or {}
    strict = simulation['red_team']
    translate = ''
    if sources.get('translation'):
        keep = ', '.join(sources.get('keep_terms') or [])[:300]
        translate = (' Translate the selected items into the meeting language'
                     + ('; keep these terms in the original: ' + keep if keep else '')
                     + '; keep regulatory and tax terms in the original language.')
    duties = dict(
        analyst='Read the evidence file, select the items that force a buying decision for the ideal customer, '
                'and share cited excerpts with the others. Drop items with no plausible buyer.' + translate,
        founder='Respond to the selected events with at most ' + str(change_limit(strict)) + ' product change(s) '
                'and a monthly price. Prefer changing or removing a feature over adding one.',
        red_team='Strictness ' + str(strict) + '/10. Attack the plan using the market layer: sales-cycle '
                 'friction, legacy workflows, regulation, local-language support and scope creep. Rate each '
                 'objection 1-5.' + (' Challenge every product change.' if strict >= 7 else ''),
        outreach='Draft up to three messages for persona ' + outreach['persona'] + ', stage ' + outreach['stage']
                 + ', language ' + outreach['language'] + ', tone ' + str(outreach.get('tone') or 'direct')[:100]
                 + ', each anchored to one cited event.')
    lines = []
    for role in ROLES:
        owners = ', '.join(names.get(identity, identity) for identity in roles.get(role, [])) or 'the facilitator'
        lines.append('- ' + ROLE_LABELS[role] + ' (' + owners + '): ' + duties[role])
    return ('Roles:\n' + '\n'.join(lines) + '\n'
            'Order: Analyst, then Founder, then Red Team, then the Founder may answer objections once; the '
            'Outreach writer drafts last; the facilitator writes the final JSON.\n'
            'A price above the reference price in Standing costs customers (ten demand points per doubling) '
            'and cannot pass ' + str(PRICE_CEILING) + ' times it.\n'
            'Deltas (-3..3) are judgments of today only: demand (+ more buyers need this now), competition '
            '(+ more pressure), regulatory (+ more compliance burden), delivery_risk (+ harder to build or '
            'support locally). Objections the Founder did not answer must show in the deltas.\n'
            'Outreach etiquette: ' + etiquette(outreach['language']) + ' ' + PERSONA[outreach['persona']] + ' '
            + STAGE[outreach['stage']] + ' Use {{name}} and {{company}} placeholders; never real people, '
            'addresses or invented quotes.\n'
            'Standing: ' + standing + '\n')


def strategy_brief(configuration, roles, names, standing):
    """The role-aware part of a no-news strategy review.

    Strategy days have no external evidence. The group may review the venture's
    own standing, recent decisions, recorded outreach outcomes and open
    objections, but it must not invent news or market events.
    """
    simulation, outreach = configuration['simulation'], configuration['outreach']
    strict = simulation['red_team']
    duties = dict(
        analyst='Read the internal evidence only. Identify the most important unresolved question '
                'for this venture and cite S, T, O or R items. Do not invent news or outside events.',
        founder='Decide whether the current product and monthly price should change. A no-change '
                'answer is preferred unless internal evidence forces an adjustment.',
        red_team='Strictness ' + str(strict) + '/10. Attack the current plan using the open objections, '
                 'outreach outcomes and recent decisions. Rate each objection 1-5.',
        outreach='Draft no messages unless a recorded O outcome gives a concrete reason to follow up. '
                 'Use persona ' + outreach['persona'] + ', stage ' + outreach['stage']
                 + ', language ' + outreach['language'] + ', tone '
                 + str(outreach.get('tone') or 'direct')[:100] + '.')
    lines = []
    for role in ROLES:
        owners = ', '.join(names.get(identity, identity) for identity in roles.get(role, [])) or 'the facilitator'
        lines.append('- ' + ROLE_LABELS[role] + ' (' + owners + '): ' + duties[role])
    return ('Strategy roles:\n' + '\n'.join(lines) + '\n'
            'Order: Analyst, then Founder, then Red Team, then the Founder may answer objections once; the '
            'Outreach writer drafts last; the facilitator writes the final JSON.\n'
            'No news is available. Do not fabricate events, headlines, buyers or quotes.\n'
            'Deltas (-3..3): demand must be 0 on a strategy day. Competition, regulatory and delivery_risk '
            'may move only when the internal evidence supports it.\n'
            'A price above the reference price in Standing costs customers (ten demand points per doubling) '
            'and cannot pass ' + str(PRICE_CEILING) + ' times it.\n'
            'Outreach etiquette: ' + etiquette(outreach['language']) + ' ' + PERSONA[outreach['persona']] + ' '
            + STAGE[outreach['stage']] + ' Use {{name}} and {{company}} placeholders; never real people, '
            'addresses or invented quotes.\n'
            'Standing: ' + standing + '\n')
