"""A printable brief of one venture candidate, for presenting an idea on paper.

The brief is a self-contained HTML page sized for A4. Everything the agents
wrote is escaped, and the page's own policy allows no network requests and
only the one inline script that opens the print dialog, so a hostile scout
answer can neither run code nor load anything when the brief is opened.

Narrative and evidence are the agents' opinion; the numbers come from the
gateway's ``candidate_economics``. The brief says both on every page.
"""

import base64
import hashlib
import html
from urllib.parse import urlsplit

import venture_notebook

# Opens the print dialog when the dashboard shows the brief in a new tab
# (a blob: page); a downloaded copy opened later only prints on request.
_SCRIPT = ("var b=document.getElementById('print');"
           "if(b)b.addEventListener('click',function(){window.print()});"
           "if(location.protocol==='blob:')window.addEventListener('load',function(){"
           "setTimeout(function(){window.print()},250)});")
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
POLICY = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
          "script-src 'sha256-" + _SCRIPT_HASH + "'; base-uri 'none'; form-action 'none'")

_STYLE = """
@page { size: A4; margin: 15mm 14mm 16mm; }
* { box-sizing: border-box; }
html { background: #e9e9ec; }
body { margin: 0; color: #16181d; font: 10pt/1.45 "Segoe UI", system-ui, -apple-system, Helvetica, Arial, sans-serif;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.sheet { background: #fff; max-width: 210mm; margin: 12mm auto; padding: 14mm; box-shadow: 0 1px 6px rgba(0,0,0,.15); }
.toolbar { max-width: 210mm; margin: 8mm auto -6mm; display: flex; gap: 8px; justify-content: flex-end;
           font-size: 9pt; color: #4a4f5a; align-items: center; }
.toolbar button { font: inherit; padding: 6px 14px; border: 1px solid #2b4acb; background: #2b4acb; color: #fff;
                  border-radius: 6px; cursor: pointer; }
header { border-bottom: 2px solid #16181d; padding-bottom: 8px; margin-bottom: 12px; }
.eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: 8pt; color: #5b6170; margin: 0 0 2px; }
h1 { font-size: 20pt; line-height: 1.15; margin: 0 0 4px; }
.meta { color: #4a4f5a; font-size: 9pt; margin: 0; }
.verdict { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 8.5pt; font-weight: 600;
           border: 1px solid currentColor; margin-left: 6px; }
.verdict.advance { color: #1d6b3a; } .verdict.hold { color: #8a5a00; } .verdict.reject { color: #a1262b; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 10px 0 4px; }
.stat { border: 1px solid #d4d7de; border-radius: 6px; padding: 6px 8px; background: #f6f7f9; break-inside: avoid; }
.stat b { display: block; font-size: 13pt; line-height: 1.2; }
.stat span { font-size: 8pt; color: #5b6170; }
h2 { font-size: 11.5pt; margin: 14px 0 5px; padding-bottom: 2px; border-bottom: 1px solid #d4d7de;
     break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 6px; }
.lead { font-size: 11pt; }
dl { display: grid; grid-template-columns: 34mm 1fr; gap: 3px 10px; margin: 0 0 6px; }
dt { color: #5b6170; font-size: 9pt; }
dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 2px 0 8px; font-size: 9.2pt; }
th, td { text-align: left; padding: 3px 6px; border-bottom: 1px solid #e3e5ea; vertical-align: top; }
th { font-size: 8.2pt; color: #5b6170; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
th.num, td.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
tr, li { break-inside: avoid; page-break-inside: avoid; }
ul { margin: 0 0 8px; padding-left: 16px; }
li { margin-bottom: 3px; }
.cite { color: #5b6170; font-size: 8pt; white-space: nowrap; }
.warn { border-left: 3px solid #a1262b; background: #fbeeee; padding: 5px 8px; margin: 4px 0; break-inside: avoid; }
.note { border-left: 3px solid #8a5a00; background: #fbf5e8; padding: 5px 8px; margin: 4px 0; break-inside: avoid; }
.muted { color: #5b6170; }
.columns { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.sources li { font-size: 8.6pt; }
.url { word-break: break-all; color: #2b4acb; }
footer { margin-top: 14px; padding-top: 6px; border-top: 1px solid #d4d7de; font-size: 8pt; color: #5b6170; }
@media print {
  html { background: #fff; }
  .sheet { margin: 0; padding: 0; max-width: none; box-shadow: none; }
  .toolbar { display: none; }
  a { color: inherit; text-decoration: none; }
}
@media (max-width: 640px) {
  .sheet { margin: 0; padding: 16px; }
  .toolbar { margin: 8px 16px 0; }
  .stats, .columns { grid-template-columns: 1fr; }
  dl { grid-template-columns: 1fr; }
}
"""

_MODELS = dict(saas='SaaS subscription', service='Service', productised_service='Productised service',
               hybrid='Hybrid: one-off plus subscription')
_KINDS = dict(one_off='One-off', recurring='Recurring', per_unit='Per unit', take_rate='Share of volume',
              credit='Credit')
_DRIVERS = dict(regulation='Regulation', technology='A technology shift', infrastructure='New infrastructure',
                incumbent='A weakening incumbent', behaviour='Changed behaviour', other='Other')
_ALTERNATIVES = dict(software='Software', service='Service', in_house='In-house', do_nothing='Do nothing',
                     other='Other')


def _e(value):
    return html.escape('' if value is None else str(value), quote=True)


def _money(amount, currency):
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return _e(amount) if amount not in (None, '') else '-'
    # Cents matter for a per-unit price (EUR 0.89 a box); whole amounts stay whole.
    text = format(round(amount), ',') if round(amount, 2) == round(amount) else format(amount, ',.2f')
    return _e(str(currency or '') + ' ' + text).strip()


def _price(price):
    return _money(price.get('amount'), price.get('currency')) if isinstance(price, dict) else 'Price not public'


def _cite(ids):
    ids = [str(item) for item in ids or [] if item]
    return ' <span class="cite">[' + _e(', '.join(ids)) + ']</span>' if ids else ''


def _list(values):
    values = [value for value in values or [] if str(value).strip()]
    return '<ul>' + ''.join('<li>' + _e(value) + '</li>' for value in values) + '</ul>' if values else ''


def _rows(rows):
    rows = [(label, value) for label, value in rows if value not in (None, '')]
    return ('<dl>' + ''.join('<dt>' + _e(label) + '</dt><dd>' + value + '</dd>' for label, value in rows)
            + '</dl>') if rows else ''


def cited_evidence_ids(candidate):
    """Every evidence id the candidate cites, in first-seen order."""
    seen = []
    def add(ids):
        for item in ids or []:
            if isinstance(item, str) and item not in seen:
                seen.append(item)
    add(candidate.get('evidence'))
    for key in ('prior_state', 'buyer', 'reachable_accounts', 'dissent', 'analogue', 'market_ceiling'):
        if isinstance(candidate.get(key), dict):
            add(candidate[key].get('evidence'))
    for key in ('alternatives', 'evidence_against'):
        for item in candidate.get(key) or []:
            if isinstance(item, dict):
                add(item.get('evidence'))
    business = candidate.get('business_model')
    for line in (business.get('revenue_lines') or []) if isinstance(business, dict) else []:
        if isinstance(line, dict):
            add(line.get('evidence'))
    incumbent = candidate.get('incumbent')
    for item in (incumbent.get('complaints') or []) if isinstance(incumbent, dict) else []:
        if isinstance(item, dict):
            add(item.get('evidence'))
    dimensions = candidate.get('confidence_dimensions')
    if isinstance(dimensions, dict):
        for item in dimensions.values():
            if isinstance(item, dict):
                add(item.get('evidence'))
    return seen


def _sources(candidate, run):
    """What each cited id points at: a researched page, a news item or a lesson."""
    known = {}
    for link in candidate.get('evidence_links') or []:
        if isinstance(link, dict) and link.get('id'):
            known[link['id']] = link
    if isinstance(run, dict):
        for link in run.get('research') or []:
            if isinstance(link, dict) and link.get('id'):
                known.setdefault(link['id'], link)
        evidence = run.get('evidence') or {}
        for item in evidence.get('items') or []:
            if isinstance(item, dict) and item.get('id'):
                known.setdefault(item['id'], dict(id=item['id'], title=item.get('title'), url=item.get('link'),
                                                  published=item.get('published'), claim=item.get('source')))
        for item in evidence.get('lessons') or []:
            if isinstance(item, dict) and item.get('id'):
                known.setdefault(item['id'], dict(id=item['id'],
                                                  title=item.get('title') or 'Lesson from an earlier venture',
                                                  claim='Lesson from this studio\'s own ventures'))
    lines = []
    for identity in cited_evidence_ids(candidate):
        link = known.get(identity)
        if not link:
            lines.append('<li><b>' + _e(identity) + '</b> <span class="muted">not in the saved research</span></li>')
            continue
        url = str(link.get('url') or '')
        try:
            host = (urlsplit(url).hostname or '') if url.startswith(('https://', 'http://')) else ''
        except ValueError:
            host = ''
        title = str(link.get('title') or '') or url or identity
        parts = ['<b>' + _e(identity) + '</b> ' + _e(title)]
        details = ' · '.join(_e(value) for value in (host, link.get('published')) if value)
        if details:
            parts.append(' <span class="muted">· ' + details + '</span>')
        if link.get('claim'):
            parts.append('<br>' + _e(link['claim']))
        if url.startswith('https://'):
            parts.append('<br><a class="url" href="' + _e(url)
                         + '" target="_blank" rel="noopener noreferrer">' + _e(url) + '</a>')
        elif url:
            parts.append('<br><span class="url">' + _e(url) + '</span>')
        lines.append('<li>' + ''.join(parts) + '</li>')
    return lines


def _economics(candidate):
    economics = candidate.get('economics')
    business = candidate.get('business_model')
    delivery = candidate.get('delivery_effort')
    out = []
    if isinstance(business, dict):
        rows = []
        for line in business.get('revenue_lines') or []:
            if not isinstance(line, dict):
                continue
            rows.append('<tr><td>' + _e(_KINDS.get(line.get('kind'), line.get('kind'))) + '</td><td>'
                        + _e(line.get('label')) + '</td><td class="num">' + _line_price(line) + '</td></tr>')
        out.append('<p><b>' + _e(_MODELS.get(business.get('type'), business.get('type'))) + '</b></p>')
        if rows:
            out.append('<table><thead><tr><th>Revenue line</th><th>What</th><th class="num">Price</th></tr>'
                       '</thead><tbody>' + ''.join(rows) + '</tbody></table>')
    if not isinstance(economics, dict):
        if isinstance(delivery, dict):
            out.append(_rows([('Delivery', _e(delivery.get('expert_days')) + ' expert days at '
                               + _price(delivery.get('day_rate')) + ' a day')]))
        return ''.join(out)
    currency = economics.get('currency')
    project = economics.get('basis') == 'project'
    unit = economics.get('break_even_unit') or ('projects a month' if project else 'paying customers')
    rows = []
    if isinstance(delivery, dict):
        rows.append(('Delivery per ' + ('project' if project else 'new customer'),
                     _e(delivery.get('expert_days')) + ' expert days at ' + _price(delivery.get('day_rate'))
                     + ' = ' + _money(economics.get('delivery_cost'), currency)))
    if project:
        rows.append(('Contribution', _money(economics.get('contribution'), currency) + ' per project after delivery'))
    else:
        rows.append(('Contribution', _money(economics.get('contribution'), currency)
                     + ' per customer a month'))
        if economics.get('first_year_revenue') is not None:
            rows.append(('First year per customer', _money(economics.get('first_year_revenue'), currency)
                         + ' revenue, ' + _money(economics.get('first_year_contribution'), currency)
                         + ' after delivery and credits'))
    if economics.get('project_contribution') is not None:
        days = economics.get('pilot_break_even_days')
        rows.append(('First offer margin', _money(economics.get('project_contribution'), currency) + ' per pilot'
                     + (' · loses money above ' + _e(days) + ' expert days' if days is not None else '')))
    mix = economics.get('revenue_mix') if isinstance(economics.get('revenue_mix'), dict) else {}
    if mix.get('per_unit') or mix.get('take_rate'):
        # Revenue that grows with the customer is what makes the idea scale.
        parts = [(_money(mix.get(kind), currency) + ' ' + label) for kind, label in
                 (('recurring', 'subscription'), ('per_unit', 'per unit'), ('take_rate', 'share of volume'))
                 if mix.get(kind)]
        rows.append(('Repeating revenue', _money(economics.get('recurring_monthly'), currency)
                     + ' per customer a month: ' + ', '.join(parts)))
    burn = economics.get('monthly_burn')
    configuration = candidate.get('configuration') if isinstance(candidate.get('configuration'), dict) else {}
    simulation = configuration.get('simulation') if isinstance(configuration.get('simulation'), dict) else {}
    lines = [line for line in simulation.get('burn') or [] if isinstance(line, dict)]
    # The simulation charges a whole amount a day, so the total can differ
    # slightly from the sum of the lines; say so rather than leave a puzzle.
    rows.append(('Monthly costs', _money(burn, currency) + ' a month'
                 + (' (' + _money(burn / 30, currency) + ' a day in the simulation)'
                    if isinstance(burn, (int, float)) and burn > 0 else '')
                 + ('<br><span class="muted">' + ', '.join(_e(line.get('label')) + ' '
                                                           + _money(line.get('amount'), line.get('currency'))
                                                           for line in lines) + '</span>' if lines else '')))
    rows.append(('Starting capital', _money(economics.get('capital'), currency)
                 + (' · runway ' + _e(economics.get('runway_months')) + ' months'
                    if economics.get('runway_months') is not None else '')))
    if economics.get('break_even_customers') is not None:
        share = economics.get('break_even_share')
        text = _e(economics.get('break_even_customers')) + ' ' + _e(unit)
        if project and economics.get('accounts_needed') is not None:
            text += ' (about ' + _e(economics['accounts_needed']) + ' a year)'
        if share is not None:
            text += ' · ' + _e(round(share * 100)) + '% of the ' + _e(economics.get('reachable_accounts')) \
                + ' cited reachable accounts'
        if economics.get('customers_per_month') is not None:
            text += ' · about ' + _e(economics['customers_per_month']) + ' new a month before the money runs out'
        rows.append(('Break-even', text))
    else:
        rows.append(('Break-even', 'Not reached: nothing is left after delivery to cover the monthly costs'))
    if economics.get('time_to_first_payment_days') is not None:
        rows.append(('First payment', _e(economics['time_to_first_payment_days']) + ' days after starting'))
    out.append(_rows(rows))
    out.extend('<div class="warn">' + _e(warning) + '</div>' for warning in economics.get('warnings') or [])
    # The checkable review questions: advisory, so styled as notes.
    out.extend('<div class="note"><b>Review:</b> ' + _e(note) + '</div>'
               for note in candidate.get('review_notes') or [] if note)
    out.append('<p class="muted">Break-even is a floor: costs beyond the stated delivery effort are not counted.</p>')
    return ''.join(out)


def _line_price(line):
    """One revenue line's price as a person would say it."""
    kind = line.get('kind')
    period = {'month': ' a month', 'year': ' a year'}.get(line.get('period'), '')
    if kind == 'take_rate':
        percent, cost = line.get('percent'), line.get('cost_percent')
        text = (_e(format(percent, 'g') if isinstance(percent, (int, float)) else percent) + '% of '
                + _money(line.get('volume'), line.get('currency')) + ' a month')
        if isinstance(cost, (int, float)) and cost > 0:
            text += ' (' + _e(format(cost, 'g')) + '% passed on)'
        return text + _cite(line.get('evidence'))
    if kind == 'per_unit':
        return (_money(line.get('amount'), line.get('currency')) + ' per ' + _e(line.get('unit') or 'unit')
                + _e(period) + ' &times; ' + _e(line.get('units')))
    if kind == 'one_off' and line.get('units'):
        return (_money(line.get('amount'), line.get('currency')) + ' per ' + _e(line.get('unit') or 'unit')
                + ' &times; ' + _e(line.get('units')) + ' per project')
    return ('&minus; ' if kind == 'credit' else '') + _money(line.get('amount'), line.get('currency')) + _e(period)


def _origin(candidate):
    """Where the idea comes from: a proven model elsewhere, a weak incumbent."""
    analogue = candidate.get('analogue') if isinstance(candidate.get('analogue'), dict) else None
    incumbent = candidate.get('incumbent') if isinstance(candidate.get('incumbent'), dict) else None
    out = []
    if analogue:
        out.append(_rows([('Proven elsewhere', _e(analogue.get('company')) + ' in ' + _e(analogue.get('vertical'))
                           + _cite(analogue.get('evidence'))),
                          ('Why it transfers', _e(analogue.get('transfer')))]))
    if incumbent:
        complaints = [item for item in incumbent.get('complaints') or [] if isinstance(item, dict)]
        out.append('<p><b>What customers of ' + _e(incumbent.get('name')) + ' complain about</b></p>'
                   + ('<ul>' + ''.join('<li>' + _e(item.get('claim')) + _cite(item.get('evidence')) + '</li>'
                                       for item in complaints) + '</ul>' if complaints else ''))
    return ''.join(out)


def _upside(candidate):
    """The market ceiling and later modules: reported, never in break-even."""
    economics = candidate.get('economics') if isinstance(candidate.get('economics'), dict) else {}
    currency = economics.get('currency')
    ceiling = candidate.get('market_ceiling') if isinstance(candidate.get('market_ceiling'), dict) else None
    expansion = [item for item in candidate.get('expansion') or [] if isinstance(item, dict)]
    rows = []
    if ceiling:
        count = ceiling.get('count')
        rows.append(('Market ceiling', _e(format(count, ',') if isinstance(count, int) else count) + ' accounts · '
                     + _e(ceiling.get('segment')) + _cite(ceiling.get('evidence'))))
        if economics.get('ceiling_annual_revenue') is not None:
            rows.append(('At these prices', _money(economics['ceiling_annual_revenue'], currency)
                         + ' a year if every one of them bought'
                         + (' · ' + _money(economics['ceiling_annual_revenue_with_expansion'], currency)
                            + ' with expansion' if economics.get('ceiling_annual_revenue_with_expansion') else '')))
    for item in expansion:
        period = {'month': ' a month', 'year': ' a year'}.get(item.get('period'), '')
        rows.append(('Later module', _e(item.get('name')) + ' · ' + _money(item.get('amount'), item.get('currency'))
                     + _e(period) + ' · ' + _e(item.get('attach_percent')) + '% of customers expected'))
    if economics.get('expansion_monthly'):
        rows.append(('Expansion per customer', _money(economics['expansion_monthly'], currency)
                     + ' a month on average'))
    return _rows(rows)


def _stats(candidate):
    economics = candidate.get('economics') if isinstance(candidate.get('economics'), dict) else {}
    currency = economics.get('currency')
    stats = []
    confidence = candidate.get('confidence')
    if confidence is not None:
        stats.append((_e(confidence) + ' / 100', 'Confidence (agent opinion)'))
    if economics.get('break_even_customers') is not None:
        unit = economics.get('break_even_unit') or (
            'projects a month' if economics.get('basis') == 'project' else 'paying customers')
        stats.append((_e(economics['break_even_customers']), 'Break-even, ' + _e(unit)))
    elif economics:
        stats.append(('None', 'Break-even within these prices'))
    if economics.get('runway_months') is not None:
        stats.append((_e(economics['runway_months']) + ' months', 'Runway on ' + _money(economics.get('capital'), currency)))
    reachable = candidate.get('reachable_accounts')
    if isinstance(reachable, dict) and reachable.get('count') is not None:
        stats.append((_e(format(reachable['count'], ',') if isinstance(reachable['count'], int)
                         else reachable['count']), 'Reachable accounts (cited)'))
    if economics.get('project_contribution') is not None:
        stats.append((_money(economics['project_contribution'], currency), 'Margin on the first offer'))
    elif economics.get('first_year_contribution') is not None:
        stats.append((_money(economics['first_year_contribution'], currency), 'First-year contribution a customer'))
    if economics.get('time_to_first_payment_days') is not None:
        stats.append((_e(economics['time_to_first_payment_days']) + ' days', 'To the first payment'))
    if not stats:
        return ''
    return '<div class="stats">' + ''.join('<div class="stat"><b>' + value + '</b><span>' + label
                                            + '</span></div>' for value, label in stats[:6]) + '</div>'


def render(candidate, organization_name='', run=None, prepared=None, dimensions=()):
    """The brief as one HTML document. ``dimensions`` is ``SCOUT_CONFIDENCE``."""
    name = str(candidate.get('name') or 'Untitled idea')
    configuration = candidate.get('configuration') if isinstance(candidate.get('configuration'), dict) else {}
    market = configuration.get('market') if isinstance(configuration.get('market'), dict) else {}
    outreach = configuration.get('outreach') if isinstance(configuration.get('outreach'), dict) else {}
    simulation = configuration.get('simulation') if isinstance(configuration.get('simulation'), dict) else {}
    dissent = candidate.get('dissent') if isinstance(candidate.get('dissent'), dict) else None
    prior = candidate.get('prior_state') if isinstance(candidate.get('prior_state'), dict) else None
    buyer = candidate.get('buyer') if isinstance(candidate.get('buyer'), dict) else None
    reachable = candidate.get('reachable_accounts') if isinstance(candidate.get('reachable_accounts'), dict) else None
    date = prepared.strftime('%d %B %Y').lstrip('0') if prepared else ''
    verdict = str(dissent.get('verdict') or '') if dissent else ''

    body = ['<header><p class="eyebrow">Venture idea brief</p><h1>' + _e(name) + '</h1><p class="meta">'
            + ' · '.join(_e(value) for value in (organization_name, 'Prepared ' + date if date else '',
                                                  'Status: ' + str(candidate.get('state') or 'proposed'))
                         if value)
            + (('<span class="verdict ' + _e(verdict) + '">Skeptic: ' + _e(verdict) + '</span>') if verdict else '')
            + '</p></header>']
    body.append(_stats(candidate))

    body.append('<h2>The idea</h2>')
    if candidate.get('thesis'):
        body.append('<p class="lead">' + _e(candidate['thesis']) + '</p>')
    body.append(_rows([
        ('Product', _e(candidate.get('product'))),
        ('First offer', _e(candidate.get('first_offer'))),
        ('Pricing', _e(candidate.get('pricing'))),
        ('Revised', ('Round ' + _e(candidate.get('round') or 1) + ': ' + _e(candidate['revision_note']))
         if candidate.get('revision_note') else ''),
    ]))

    if prior:
        body.append('<h2>Why now</h2>' + _rows([
            ('Before', _e(prior.get('previous'))),
            ('What changed', _e(prior.get('change')) + _cite(prior.get('evidence'))),
            ('Driver', _e(_DRIVERS.get(prior.get('driver'), prior.get('driver')))),
        ]))
    origin = _origin(candidate)
    if origin:
        body.append('<h2>Where the idea comes from</h2>' + origin)

    geography = market.get('geography')
    body.append('<h2>Who buys</h2>' + _rows([
        ('Buyer', (_e(buyer.get('role')) + ' · ' + _e(buyer.get('budget')) + _cite(buyer.get('evidence')))
         if buyer else ''),
        ('Reachable market', (_e(format(reachable['count'], ',') if isinstance(reachable.get('count'), int)
                                 else reachable.get('count')) + ' accounts · ' + _e(reachable.get('segment'))
                              + _cite(reachable.get('evidence'))) if reachable else ''),
        ('Market', ' · '.join(_e(value) for value in (geography, market.get('currency')) if value)),
        ('First outreach', ' · '.join(_e(value) for value in (outreach.get('persona'), outreach.get('stage'))
                                      if value)),
    ]))

    economics = _economics(candidate)
    if economics:
        body.append('<h2>Business model and unit economics</h2>' + economics)
    upside = _upside(candidate)
    if upside:
        body.append('<h2>Upside, not counted in break-even</h2>' + upside)

    alternatives = [item for item in candidate.get('alternatives') or [] if isinstance(item, dict)]
    if alternatives:
        body.append('<h2>What buyers use today</h2><table><thead><tr><th>Alternative</th><th>Kind</th>'
                    '<th class="num">Price</th></tr></thead><tbody>'
                    + ''.join('<tr><td>' + _e(item.get('name')) + _cite(item.get('evidence')) + '</td><td>'
                              + _e(_ALTERNATIVES.get(item.get('kind'), item.get('kind'))) + '</td><td class="num">'
                              + (_price(item.get('price')) if item.get('price') else '<span class="muted">-</span>')
                              + '</td></tr>' for item in alternatives)
                    + '</tbody></table>')

    against = [item for item in candidate.get('evidence_against') or [] if isinstance(item, dict)]
    if against or dissent:
        body.append('<h2>The case against</h2>')
        if against:
            body.append('<ul>' + ''.join('<li>' + _e(item.get('claim'))
                                         + (' <span class="muted">' + _e(item['reason']) + '</span>'
                                            if item.get('reason') else '')
                                         + _cite(item.get('evidence')) + '</li>' for item in against) + '</ul>')
        if dissent:
            body.append('<p><b>Skeptic (' + _e(verdict) + '):</b> ' + _e(dissent.get('summary'))
                        + _cite(dissent.get('evidence')) + '</p>')

    panel = candidate.get('panel') if isinstance(candidate.get('panel'), dict) else None
    latest = [review for review in candidate.get('stakeholder_reviews') or []
              if isinstance(review, dict) and panel and review.get('run_id') == panel.get('run_id')]
    if latest:
        body.append('<h2>What the stakeholders said</h2><p class="muted">Round ' + _e(panel.get('round') or 1)
                    + ': ' + _e(panel.get('back', 0)) + ' back, ' + _e(panel.get('hold', 0)) + ' hold, '
                    + _e(panel.get('pass', 0)) + ' pass. Each reviewed on its own model before hearing the '
                    'others.</p>')
        if panel.get('outdated'):
            body.append('<div class="note">This idea was edited after these reviews, so they are about an '
                        'earlier version. It goes back to the next pitch.</div>')
        if panel.get('independent') is False:
            body.append('<div class="note">Every stakeholder ran on the studio\'s own model; this critique may '
                        'echo the studio.</div>')
        body.append('<ul>' + ''.join(
            '<li><b>' + _e(review.get('label') or review.get('stakeholder')) + '</b> <span class="muted">('
            + _e(review.get('model')) + ')</span>: ' + _e(review.get('verdict'))
            + ('; '.join([''] + [_e(item) for item in review.get('objections') or []]))
            + ('<br><span class="muted">Would change its mind: ' + _e(review['would_change_mind']) + '</span>'
               if review.get('would_change_mind') else '') + '</li>' for review in latest) + '</ul>')
    elif panel and panel.get('reviewers') == 0:
        body.append('<h2>Stakeholder panel</h2><p class="muted">Pitched, no reviews.</p>')

    ratings = candidate.get('confidence_dimensions')
    if isinstance(ratings, dict) and ratings:
        known = {key for key, _, _ in dimensions}
        # Older records may carry a dimension under a former name; list it too.
        labels = [(key, label, weight) for key, label, weight in dimensions if key in ratings] + [
            (key, str(key).replace('_', ' '), None) for key in ratings if key not in known]
        body.append('<h2>Confidence</h2><table><thead><tr><th>Dimension</th><th>Weight</th><th>Rating</th></tr>'
                    '</thead><tbody>'
                    + ''.join('<tr><td>' + _e(label[:1].upper() + label[1:]) + '</td><td>'
                              + (_e(round(weight * 100)) + '%' if weight is not None else '-') + '</td><td>'
                              + _e((ratings[key] or {}).get('rating') if isinstance(ratings[key], dict) else '')
                              + (' <span class="muted">(not rated)</span>'
                                 if isinstance(ratings[key], dict) and ratings[key].get('unrated') else '')
                              + _cite((ratings[key] or {}).get('evidence') if isinstance(ratings[key], dict) else [])
                              + '</td></tr>' for key, label, weight in labels)
                    + '</tbody></table>')

    assumptions = _list(candidate.get('assumptions'))
    kill = _list(candidate.get('kill_criteria'))
    if assumptions or kill:
        body.append('<div class="columns"><div><h2>Assumptions to test</h2>' + assumptions
                    + '</div><div><h2>Kill criteria</h2>' + kill + '</div></div>')
    for note in candidate.get('kill_criteria_notes') or []:
        body.append('<div class="note"><b>To tighten:</b> ' + _e(note) + '</div>')
    if not candidate.get('economics'):
        # Without economics the costs are not shown above; keep them on paper.
        burn = [line for line in simulation.get('burn') or [] if isinstance(line, dict)]
        if burn:
            body.append('<h2>Monthly costs assumed</h2><p>'
                        + ', '.join(_e(line.get('label')) + ' ' + _money(line.get('amount'), line.get('currency'))
                                    for line in burn) + '</p>')

    sources = _sources(candidate, run)
    if sources:
        body.append('<h2>Sources</h2><p class="muted">Researched pages are agent-reported and not fetched by '
                    'the gateway; news items come from the configured feeds and are not independently '
                    'verified.</p><ol class="sources">' + ''.join(sources) + '</ol>')

    body.append('<footer>Prepared with the Herdr Venture Studio. The narrative, evidence and ratings are the '
                'agents\' opinion and have not been verified. The unit economics are computed by the gateway '
                'from the prices, costs and reach the agents stated.</footer>')

    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="' + _e(POLICY) + '">'
            '<meta name="referrer" content="no-referrer">'
            '<title>' + _e(name) + ' - venture idea brief</title><style>' + _STYLE + '</style></head><body>'
            '<div class="toolbar"><span>Print, or choose "Save as PDF" as the printer.</span>'
            '<button id="print" type="button">Print</button></div>'
            '<main class="sheet">' + ''.join(part for part in body if part) + '</main>'
            '<script>' + _SCRIPT + '</script></body></html>')


def filename(candidate):
    return venture_notebook.slug(candidate.get('name') or 'idea') + '-brief.html'
