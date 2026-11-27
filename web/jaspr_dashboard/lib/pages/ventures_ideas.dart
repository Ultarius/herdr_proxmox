import 'operations_support.dart';
import 'ops_ui.dart';

/// Candidate states still waiting for an operator decision.
const openCandidateStates = {'proposed', 'approved', 'creating', 'failed'};

/// Number of candidates in [candidates] that still need a decision.
int openCandidateCount(Iterable<Map<String, dynamic>> candidates) => candidates
    .where((item) => openCandidateStates.contains('${item['state']}'))
    .length;

const _stakeholderLabels = {
  'investor': 'Investor',
  'customer': 'Target customer',
  'operator': 'Industry operator',
  'competitor': 'Market leader',
};

String _stakeholderLabel(Object? role) =>
    _stakeholderLabels['$role'] ?? '$role';

const _barLabels = {
  'survival_threshold': 'Survival threshold',
  'consecutive_days': 'Qualifying days',
  'red_team': 'Red Team strictness',
  'capital': 'Starting capital',
  'daily_ticks': 'Daily reviews',
  'tick_hour_utc': 'Review hour (UTC)',
  'meeting_cadence': 'Meeting cadence',
};

/// The rules a candidate would run under, so they are visible without opening Edit.
List<(String, Component)> candidateBar(Map<String, dynamic> item) {
  final configuration = item['configuration'] as Map? ?? const {};
  final simulation = configuration['simulation'] as Map? ?? const {};
  final market = configuration['market'] as Map? ?? const {};
  final adjusted = item['scout_adjusted'] as Map? ?? const {};
  String shown(String key) {
    final value = simulation[key];
    final label = key == 'daily_ticks'
        ? (value == true ? 'On' : 'Off until you start them')
        : key == 'capital'
        ? '$value ${market['currency'] ?? ''}'.trim()
        : '$value';
    final change = adjusted[key];
    return change is Map
        ? '$label (scout proposed ${change['proposed']})'
        : label;
  }

  final burn = (simulation['burn'] as List? ?? const [])
      .map(
        (line) => line is Map
            ? '${line['label']}: ${line['amount']} ${line['currency']}'
            : '$line',
      )
      .join(', ');
  return [
    for (final key in const [
      'survival_threshold',
      'consecutive_days',
      'red_team',
      'capital',
      'daily_ticks',
    ])
      (_barLabels[key]!, text(shown(key))),
    if (adjusted['reference_price'] is Map)
      (
        'Reference price',
        text(
          'The monthly costs (scout proposed '
          '${(adjusted['reference_price'] as Map)['proposed']})',
        ),
      ),
    ('Monthly costs', text(burn.isEmpty ? 'None' : burn)),
  ];
}

/// The newest scout run in a studio summary (runs arrive newest first).
Map<String, dynamic>? latestScoutRun(Map<String, dynamic>? studio) {
  for (final run in studio?['runs'] as List? ?? const []) {
    if (run is Map && run['kind'] == 'scout') {
      return Map<String, dynamic>.from(run);
    }
  }
  return null;
}

/// Ideas the latest scout proposed that the gateway refused, with why.
///
/// A refused idea never becomes a candidate, so without this the queue
/// would look as if the scout found nothing.
List<Component> _refusals(
  Map<String, dynamic>? scout, {
  bool busy = false,
  void Function(Map<String, dynamic> run)? onReread,
}) {
  final refused = List<Map<String, dynamic>>.from(
    scout?['refused_candidates'] as List? ?? const [],
  );
  if (refused.isEmpty) return const [];
  final one = refused.length == 1;
  // Reading again applies the gateway's current rules to the same saved
  // answer: without saying what it found, an unchanged result looks as if
  // the button did nothing.
  final reread = scout?['reread'] is Map
      ? Map<String, dynamic>.from(scout!['reread'] as Map)
      : null;
  final added = (reread?['added'] as List? ?? const []).map((v) => '$v');
  return [
    notice(
      'The last scout review proposed ${refused.length} ${one ? 'idea' : 'ideas'} '
      'the gateway refused, so ${one ? 'it is' : 'they are'} not in this queue. '
      'Reading the saved answer again only helps after a gateway update changed the rules; '
      'otherwise run the scout again, as its prompt states the rules that were broken.',
      error: true,
    ),
    for (final item in refused)
      paragraph(
        '${item['name'] ?? 'Unnamed'}: ${item['error'] ?? 'no reason recorded'}',
      ),
    if (reread != null)
      paragraph(
        'Read again ${displayTime(reread['at'])}: '
        '${added.isEmpty ? 'nothing new passed under the current rules' : 'added ${added.join(', ')}'}. '
        '${refused.isEmpty ? '' : 'The same answer will be refused again; run the scout for a new one.'}',
      ),
    if (onReread != null && scout != null)
      actions([
        button(
          'Read the answer again',
          busy ? null : () => onReread(scout),
          small: true,
        ),
      ]),
  ];
}

/// What the latest scout is doing, when it has not produced reviewable ideas.
///
/// Without this the queue looks empty while a scout is still meeting, has
/// stopped, or found nothing, and the operator cannot tell which.
List<Component> _scoutStatus(Map<String, dynamic>? scout) {
  if (scout == null) return const [];
  final state = '${scout['state'] ?? ''}';
  final started = displayTime(scout['created_at']);
  final note = '${scout['research_note'] ?? ''}';
  final lead = '${(scout['research_lead'] as Map?)?['question'] ?? ''}';
  if (state == 'meeting' || state == 'pending') {
    final waiting = scout['waiting'] is Map
        ? ' ${(scout['waiting'] as Map)['title'] ?? ''}: ${(scout['waiting'] as Map)['message'] ?? ''}'
        : '';
    return [
      notice(
        'The latest scout (started $started) is still in its meeting. Its ideas appear here once the '
        'gateway has read the finished answer.$waiting',
      ),
    ];
  }
  if (state == 'needs_attention') {
    final failure = scout['failure'] as Map? ?? const {};
    return [
      notice(
        'The latest scout (started $started) stopped before its ideas could be read: '
        '${failure['title'] ?? 'it needs attention'}. ${scout['error'] ?? ''} '
        'Recover or dismiss it on the Studio tab.',
        error: true,
      ),
    ];
  }
  final submitted = (scout['candidates'] as List? ?? const []).whereType<Map>();
  final refused = scout['refused_candidates'] as List? ?? const [];
  if (state == 'ready' &&
      submitted.every((idea) => idea['state'] == 'excluded') &&
      (refused.isEmpty || lead.isNotEmpty)) {
    final proposedNone = submitted.isEmpty && refused.isEmpty;
    return [
      notice(
        'The latest scout (started $started) '
        '${proposedNone ? 'proposed no ideas' : 'added no ideas to the queue'}.'
        '${note.isEmpty ? '' : ' Its note: $note'}'
        '${lead.isEmpty ? '' : ' It left the next scout a research lead: $lead See the Studio tab.'}',
      ),
    ];
  }
  return const [];
}

/// One page a web-researching scout relied on: a link to open, where it is
/// hosted, and the claim the scout takes from it.
Component _researchPage(Map<String, dynamic> link) {
  final url = '${link['url'] ?? ''}';
  final host = Uri.tryParse(url)?.host ?? '';
  final published = '${link['published'] ?? ''}';
  final title = '${link['title'] ?? ''}'.isEmpty ? url : '${link['title']}';
  return el('article', [
    el('p', [
      text('${link['id'] ?? ''} · '),
      // Only an https page becomes a link; anything else stays text.
      if (url.startsWith('https://'))
        el(
          'a',
          [text(title)],
          attributes: {
            'href': url,
            'target': '_blank',
            'rel': 'noopener noreferrer',
          },
        )
      else
        text(title),
      text(
        '${host.isEmpty ? '' : ' · $host'}${published.isEmpty ? '' : ' · $published'}',
      ),
    ]),
    if ('${link['claim'] ?? ''}'.isNotEmpty) paragraph('${link['claim']}'),
  ], classes: 'stack');
}

/// Reviewed venture candidates produced by the Venture Studio.
Component venturesIdeas({
  required List<Map<String, dynamic>> candidates,
  required String organizationName,
  required bool busy,
  required void Function(Map<String, dynamic>) onEdit,
  required void Function(Map<String, dynamic>) onApprove,
  required void Function(Map<String, dynamic>) onReject,
  Map<String, dynamic>? latestScout,
  void Function(Map<String, dynamic> run)? onReread,
  void Function(Map<String, dynamic>)? onExport,
}) {
  final refusals = [
    ..._scoutStatus(latestScout),
    ..._refusals(latestScout, busy: busy, onReread: onReread),
  ];
  if (candidates.isEmpty) {
    return card('Ideas', [
      ...refusals,
      emptyState(
        refusals.isEmpty && latestScout == null
            ? 'No venture candidates yet for $organizationName. Set up the Venture Studio and run a scout review.'
            : 'No venture candidates waiting for $organizationName.',
        iconName: 'lightbulb',
      ),
    ], subtitle: 'Candidates stay proposals until an administrator creates one.');
  }
  // Strongest first, so the most promising idea is the one read first.
  final open =
      candidates
          .where((item) => openCandidateStates.contains('${item['state']}'))
          .toList()
        ..sort(
          (a, b) => ((b['confidence'] as num?) ?? -1).compareTo(
            (a['confidence'] as num?) ?? -1,
          ),
        );
  final closed = candidates
      .where((item) => !openCandidateStates.contains('${item['state']}'))
      .toList();
  return card(
    'Ideas',
    [
      ...refusals,
      if (open.isEmpty) paragraph('No candidates waiting for a decision.'),
      for (final item in open)
        _candidate(
          item,
          organizationName,
          busy,
          onEdit,
          onApprove,
          onReject,
          scout: latestScout,
          onExport: onExport,
        ),
      if (closed.isNotEmpty)
        disclosure(
          'Decided (${closed.length}): created, rejected and excluded',
          [
            for (final item in closed)
              _candidate(
                item,
                organizationName,
                busy,
                onEdit,
                onApprove,
                onReject,
                onExport: onExport,
              ),
          ],
        ),
    ],
    subtitle:
        '$organizationName · ${open.length} waiting. Evidence, assumptions and kill criteria are agent opinion until reviewed.',
  );
}

/// How an idea means to win where a seller already has paying customers,
/// with the gateway's own figures and anything to check.
String _competitionText(Map competition) {
  final figures = competition['figures'] as Map? ?? const {};
  final leader = competition['leader'] as Map? ?? const {};
  final currency = figures['currency'];
  final notes = (competition['notes'] as List? ?? const []).join(' ');
  final claim = switch ('${competition['strategy'] ?? ''}') {
    'price' =>
      'Price: ${figures['discount_percent']}% below ${leader['name']} '
          '(${_moneyText(figures['leader_monthly'], currency)} a month, '
          '${leader['customers']} paying customers). Repricing all cited accounts would cut monthly revenue by '
          '${_moneyText(figures['matching_cost_monthly'], currency)}. '
          'Our lower cost: ${competition['cost_reason'] ?? ''}',
    'bundle' =>
      'Bundle replacing ${(competition['stack'] as List? ?? const []).map((seller) => seller is Map ? seller['name'] : seller).join(', ')}: '
          'the buyer pays ${_moneyText(figures['stack_monthly'], currency)} a month for them today, '
          'the bundle saves ${figures['saving_percent']}%'
          '${'${competition['shared_record'] ?? ''}'.isEmpty ? '' : '. One record: ${competition['shared_record']}'}',
    'segment' =>
      'Buyers ${leader['name']} does not serve: ${competition['unserved'] ?? ''}',
    _ => '${competition['strategy'] ?? ''}',
  };
  final error = '${competition['comparison_error'] ?? ''}';
  final comparison = error.isEmpty ? claim : 'Recheck this comparison. $error';
  final usage = <String>[];
  for (final seller in [
    leader,
    ...(competition['stack'] as List? ?? const []).whereType<Map>(),
  ]) {
    if (seller['price'] case final Map price
        when price['period'] == 'per_unit') {
      usage.add(
        '${seller['name']} at ${price['units_per_month']} ${price['unit']} per month',
      );
    }
  }
  return [
    comparison,
    if (usage.isNotEmpty) 'Usage assumed: ${usage.join(', ')}.',
    if (notes.isNotEmpty) 'To check: $notes',
  ].join(' ');
}

String _moneyText(Object? value, Object? currency) {
  // Cents matter for a per-unit price (EUR 0.89 a box); whole amounts stay whole.
  final amount = value is num
      ? (value == value.roundToDouble()
            ? value.toStringAsFixed(0)
            : value.toStringAsFixed(2))
      : '${value ?? 0}';
  final code = '${currency ?? ''}';
  return '$amount${code.isEmpty ? '' : ' $code'}';
}

String _priceText(Map? price) {
  if (price == null) return 'price not public';
  return _moneyText(price['amount'], price['currency']);
}

String _numberText(Object? value) =>
    value is num && value == value.roundToDouble()
    ? '${value.round()}'
    : '$value';

const _periodText = {'month': ' a month', 'year': ' a year'};

const _driverText = {
  'regulation': 'regulation',
  'technology': 'a technology shift',
  'infrastructure': 'new infrastructure',
  'incumbent': 'a weakening incumbent',
  'behaviour': 'changed behaviour',
  'other': 'other timing',
};

/// One revenue line as a person would say it.
String revenueLineText(Map line) {
  final kind = '${line['kind'] ?? ''}';
  final period = _periodText['${line['period'] ?? ''}'] ?? '';
  final currency = line['currency'];
  switch (kind) {
    case 'take_rate':
      final cost = line['cost_percent'];
      return '${line['label'] ?? 'share'}: ${_numberText(line['percent'])}% of '
          '${_moneyText(line['volume'], currency)} a month'
          '${cost is num && cost > 0 ? ' (${_numberText(cost)}% passed on)' : ''}';
    case 'per_unit':
      return '${line['label'] ?? 'per unit'}: ${_moneyText(line['amount'], currency)} '
          'per ${line['unit'] ?? 'unit'}$period × ${line['units'] ?? 0}';
    case 'credit':
      return '${line['label'] ?? 'credit'}: − ${_moneyText(line['amount'], currency)}';
    case 'one_off' when line['units'] != null:
      return '${line['label'] ?? 'one-off'}: ${_moneyText(line['amount'], currency)} '
          'per ${line['unit'] ?? 'unit'} × ${line['units']} per project';
    default:
      return '${line['label'] ?? kind}: ${_moneyText(line['amount'], currency)}$period';
  }
}

/// Monthly repeating revenue per customer by kind, e.g. "720 USD per unit".
String _mixText(Map mix, Object? currency) {
  const labels = {
    'recurring': 'subscription',
    'per_unit': 'per unit',
    'take_rate': 'share of volume',
  };
  return [
    for (final entry in labels.entries)
      if (((mix[entry.key] as num?) ?? 0) > 0)
        '${_moneyText(mix[entry.key], currency)} ${entry.value}',
  ].join(', ');
}

String _breakEvenText(Map economics) {
  final count = economics['break_even_customers'];
  if ('${economics['basis'] ?? ''}' == 'project') {
    final accounts = economics['accounts_needed'];
    return '$count projects a month'
        '${accounts == null ? '' : ' · $accounts accounts a year'}';
  }
  final share = economics['break_even_share'];
  // Without a share (older or partial economics) say nothing rather than "null".
  return '$count paying customers'
      '${share is num ? ' · ${(share * 100).round()}% of cited reach' : ''}';
}

/// The scout's diligence case and the gateway's unit economics.
///
/// The numbers are computed by the gateway and cannot be overridden by the
/// agents. This is deliberately shown before approval, not only after a
/// venture exists.
Component candidateDiligence(Map<String, dynamic> item) {
  final prior = item['prior_state'] as Map?;
  final alternatives = List<Map<String, dynamic>>.from(
    (item['alternatives'] as List? ?? const []).whereType<Map>(),
  );
  final buyer = item['buyer'] as Map?;
  final reachable = item['reachable_accounts'] as Map?;
  final business = item['business_model'] as Map?;
  final delivery = item['delivery_effort'] as Map?;
  final against = List<Map<String, dynamic>>.from(
    (item['evidence_against'] as List? ?? const []).whereType<Map>(),
  );
  final dissent = item['dissent'] as Map?;
  final dimensions = item['confidence_dimensions'] as Map?;
  final economics = item['economics'] as Map?;
  final firstOffer = '${item['first_offer'] ?? ''}';
  final firstPayment = item['time_to_first_payment_days'];
  final analogue = item['analogue'] as Map?;
  final incumbent = item['incumbent'] as Map?;
  final complaints = List<Map<String, dynamic>>.from(
    (incumbent?['complaints'] as List? ?? const []).whereType<Map>(),
  );
  final ceiling = item['market_ceiling'] as Map?;
  final expansion = List<Map<String, dynamic>>.from(
    (item['expansion'] as List? ?? const []).whereType<Map>(),
  );
  final mix = economics?['revenue_mix'] as Map?;
  final currency = economics?['currency'];
  final driver = _driverText['${prior?['driver'] ?? ''}'];
  final warnings = (economics?['warnings'] as List? ?? const [])
      .map((value) => '$value')
      .where((value) => value.isNotEmpty)
      .toList();
  // Wording a reviewer would question; advisory, so shown but not blocking.
  final notes = (item['kill_criteria_notes'] as List? ?? const [])
      .map((value) => '$value')
      .where((value) => value.isNotEmpty)
      .toList();
  // The stakeholder panel's latest round on this idea.
  final panel = item['panel'] as Map?;
  final pitchedWithoutReviews = panel?['reviewers'] == 0;
  final stakeholderReviews = [
    for (final review
        in (item['stakeholder_reviews'] as List? ?? const []).whereType<Map>())
      if (panel != null && review['run_id'] == panel['run_id']) review,
  ];
  final revisionNote = '${item['revision_note'] ?? ''}';
  // What the stakeholders said about the rough shortlist during the scout.
  final screen = (item['stakeholder_screen'] as List? ?? const [])
      .whereType<Map>()
      .toList();
  // The review questions the gateway can check; advisory like the notes.
  final review = (item['review_notes'] as List? ?? const [])
      .map((value) => '$value')
      .where((value) => value.isNotEmpty)
      .toList();
  final rows = <(String, Component)>[
    if (firstOffer.isNotEmpty) ('First offer', text(firstOffer)),
    if (prior != null)
      (
        'Prior state',
        text(
          '${prior['previous'] ?? ''} → ${prior['change'] ?? ''}'
          '${driver == null ? '' : ' (driven by $driver)'}',
        ),
      ),
    if (analogue != null)
      (
        'Proven elsewhere',
        text(
          '${analogue['company'] ?? ''} in ${analogue['vertical'] ?? ''}: '
          '${analogue['transfer'] ?? ''}',
        ),
      ),
    if (buyer != null)
      ('Buyer', text('${buyer['role'] ?? ''} · ${buyer['budget'] ?? ''}')),
    if (reachable != null)
      (
        'Reachable market',
        text('${reachable['count'] ?? 0} · ${reachable['segment'] ?? ''}'),
      ),
    if (business != null)
      (
        'Business model',
        text(
          '${business['type'] ?? ''}: '
          '${(business['revenue_lines'] as List? ?? const []).map((line) => line is Map ? revenueLineText(line) : '$line').join('; ')}',
        ),
      ),
    // Revenue that grows with the customer is what lets an idea scale.
    if (mix != null &&
        ((mix['per_unit'] as num? ?? 0) > 0 ||
            (mix['take_rate'] as num? ?? 0) > 0))
      (
        'Repeating revenue',
        text(
          '${_moneyText(economics?['recurring_monthly'], currency)} per customer a month: '
          '${_mixText(mix, currency)}',
        ),
      ),
    if (delivery != null)
      (
        'Delivery',
        text(
          '${delivery['expert_days'] ?? 0} expert days at '
          '${_priceText(delivery['day_rate'] as Map?)}',
        ),
      ),
    if (economics != null)
      (
        'Runway',
        text(
          '${economics['runway_months'] ?? '-'} months · burn '
          '${_moneyText(economics['monthly_burn'], economics['currency'])}/month',
        ),
      ),
    if (economics != null && economics['break_even_customers'] != null)
      ('Break-even', text(_breakEvenText(economics))),
    if (economics != null)
      (
        'Contribution',
        text(
          economics['basis'] == 'project'
              ? '${_moneyText(economics['contribution'], economics['currency'])} per project after delivery'
              : '${_moneyText(economics['contribution'], economics['currency'])} per customer a month',
        ),
      ),
    if (economics != null &&
        economics['first_year_revenue'] != null &&
        economics['basis'] != 'project')
      (
        'First year per customer',
        text(
          '${_moneyText(economics['first_year_revenue'], economics['currency'])} revenue, '
          '${_moneyText(economics['first_year_contribution'], economics['currency'])} after delivery and credits',
        ),
      ),
    if (economics != null && economics['project_contribution'] != null)
      (
        'First offer margin',
        text(
          '${_moneyText(economics['project_contribution'], economics['currency'])} per pilot'
          '${economics['pilot_break_even_days'] == null ? '' : ' · loses money above ${economics['pilot_break_even_days']} expert days'}',
        ),
      ),
    if (firstPayment != null) ('First payment', text('$firstPayment days')),
    if (item['competition'] is Map)
      ('How it competes', text(_competitionText(item['competition'] as Map))),
    // Numbers the meeting changed after a draft check failed: a fix, or a
    // number nudged until the check passed.
    if (item['draft_changes'] is Map)
      (
        'Changed after failed check ${(item['draft_changes'] as Map)['after_check'] ?? ''}',
        text(
          ((item['draft_changes'] as Map)['changes'] as List? ?? const [])
              .join('; '),
        ),
      ),
  ];
  // Upside: shown, never counted toward break-even or the warnings.
  final upside = <(String, Component)>[
    if (ceiling != null)
      (
        'Market ceiling',
        text(
          '${ceiling['count'] ?? 0} accounts · ${ceiling['segment'] ?? ''}'
          '${economics?['ceiling_annual_revenue'] == null ? '' : ' · ${_moneyText(economics?['ceiling_annual_revenue'], currency)} a year if all bought'}',
        ),
      ),
    for (final module in expansion)
      (
        'Later module',
        text(
          '${module['name'] ?? ''} · ${_moneyText(module['amount'], module['currency'])}'
          '${_periodText['${module['period'] ?? ''}'] ?? ''} · '
          '${module['attach_percent'] ?? 0}% of customers expected',
        ),
      ),
    if (((economics?['expansion_monthly'] as num?) ?? 0) > 0)
      (
        'Expansion per customer',
        text(
          '${_moneyText(economics?['expansion_monthly'], currency)} a month on average',
        ),
      ),
  ];
  if (rows.isEmpty &&
      upside.isEmpty &&
      complaints.isEmpty &&
      alternatives.isEmpty &&
      against.isEmpty &&
      dissent == null &&
      dimensions == null &&
      warnings.isEmpty &&
      review.isEmpty &&
      stakeholderReviews.isEmpty &&
      screen.isEmpty &&
      !pitchedWithoutReviews &&
      revisionNote.isEmpty &&
      notes.isEmpty) {
    return el('span', []);
  }
  return el('section', [
    if (rows.isNotEmpty) facts(rows),
    for (final warning in warnings) notice(warning, error: true),
    if ('${item['revises_unmatched'] ?? ''}'.isNotEmpty)
      paragraph(
        'Meant to revise "${item['revises_unmatched']}", which is not an open idea (refused earlier or '
        'already decided), so it was added as a new idea'
        '${revisionNote.isEmpty ? '.' : '. What changed: $revisionNote'}',
      )
    else if (revisionNote.isNotEmpty)
      paragraph(
        'Revised for round ${item['round'] ?? 2} after the stakeholders\' feedback: $revisionNote',
      ),
    if (pitchedWithoutReviews)
      paragraph('Pitched, no reviews.')
    else if (panel != null && stakeholderReviews.isNotEmpty)
      disclosure(
        'Stakeholders, round ${panel['round'] ?? 1}: ${panel['back'] ?? 0} back · '
        '${panel['hold'] ?? 0} hold · ${panel['pass'] ?? 0} pass',
        [
          if (panel['outdated'] == true)
            notice(
              'This idea was edited after these reviews, so they are about an earlier version. '
              'It goes back to the next pitch.',
              error: true,
            ),
          if (panel['independent'] == false)
            notice(
              'Every stakeholder ran on the studio\'s own model; this critique may echo the studio.',
              error: true,
            ),
          for (final entry in stakeholderReviews)
            el('article', [
              paragraph(
                '${entry['label'] ?? entry['stakeholder']} (${entry['model'] ?? ''}): '
                '${entry['verdict'] ?? ''}'
                '${entry['round'] == 2 ? ' after hearing the others' : ''}',
              ),
              for (final objection
                  in (entry['objections'] as List? ?? const []))
                paragraph('• $objection'),
              if ('${entry['would_change_mind'] ?? ''}'.isNotEmpty)
                paragraph(
                  'Would change its mind: ${entry['would_change_mind']}',
                ),
            ], classes: 'stack'),
        ],
      ),
    if (screen.isNotEmpty)
      disclosure('Screened during the scout (${screen.length})', [
        for (final entry in screen)
          el('article', [
            paragraph(
              '${_stakeholderLabel(entry['stakeholder'])}: '
              '${entry['objection'] ?? 'no objection given'}',
            ),
            if ('${entry['would_change_view'] ?? ''}'.isNotEmpty)
              paragraph('Would change its view: ${entry['would_change_view']}'),
            if ('${entry['response'] ?? ''}'.isNotEmpty)
              paragraph('What the studio did: ${entry['response']}'),
            if ('${entry['follow_up'] ?? ''}'.isNotEmpty)
              paragraph('After the redesign: ${entry['follow_up']}'),
          ], classes: 'stack'),
      ]),
    if (review.isNotEmpty)
      disclosure('Review notes (${review.length})', [
        for (final note in review) paragraph(note),
      ]),
    if (notes.isNotEmpty)
      disclosure('Kill criteria to tighten (${notes.length})', [
        for (final note in notes) paragraph(note),
      ]),
    if (upside.isNotEmpty)
      disclosure('Upside, not counted in break-even', [facts(upside)]),
    if (complaints.isNotEmpty)
      disclosure(
        'What customers of ${incumbent?['name'] ?? 'the incumbent'} complain about (${complaints.length})',
        [for (final entry in complaints) paragraph('${entry['claim'] ?? ''}')],
      ),
    if (alternatives.isNotEmpty)
      disclosure('Alternatives (${alternatives.length})', [
        for (final alternative in alternatives)
          paragraph(
            '${alternative['name'] ?? 'Unnamed'} · '
            '${alternative['kind'] ?? ''} · '
            '${_priceText(alternative['price'] as Map?)}',
          ),
      ]),
    if (against.isNotEmpty)
      disclosure('Case against (${against.length})', [
        for (final entry in against)
          paragraph(
            '${entry['claim'] ?? ''}'
            '${'${entry['reason'] ?? ''}'.isEmpty ? '' : ' — ${entry['reason']}'}',
          ),
      ]),
    if (dissent != null)
      paragraph(
        'Skeptic: ${dissent['verdict'] ?? ''} — ${dissent['summary'] ?? ''}',
      ),
    if (item['skeptic_silent'] == true)
      notice(
        'The Skeptic did not speak in this scout meeting, so this verdict was written for it. '
        'The studio never creates this idea by itself.',
        error: true,
      ),
    if (item['pitch_blocked'] is Map)
      notice(
        'Not pitched to the stakeholders: '
        '${(item['pitch_blocked'] as Map)['reason'] ?? 'its pitch is too large.'}',
        error: true,
      ),
    if (dimensions != null)
      disclosure('Confidence dimensions', [
        for (final entry in dimensions.entries)
          paragraph(
            '${entry.key.toString().replaceAll('_', ' ')}: '
            '${entry.value is Map ? (entry.value as Map)['rating'] ?? '' : ''}'
            '${entry.value is Map && (entry.value as Map)['unrated'] == true ? ' (not rated)' : ''}',
          ),
      ]),
  ], classes: 'stack');
}

Component _candidate(
  Map<String, dynamic> item,
  String organizationName,
  bool busy,
  void Function(Map<String, dynamic>) onEdit,
  void Function(Map<String, dynamic>) onApprove,
  void Function(Map<String, dynamic>) onReject, {
  Map<String, dynamic>? scout,
  void Function(Map<String, dynamic>)? onExport,
}) {
  final state = '${item['state'] ?? 'proposed'}';
  final configuration = item['configuration'] as Map? ?? const {};
  final market = configuration['market'] as Map? ?? const {};
  final outreach = configuration['outreach'] as Map? ?? const {};
  final feeds =
      (configuration['sources'] as Map?)?['feeds'] as List? ?? const [];
  final research = List<Map<String, dynamic>>.from(
    item['evidence_links'] as List? ?? const [],
  );
  // The scout's own note and suggestions belong to the run that proposed this idea.
  final own = scout != null && '${scout['id']}' == '${item['run_id']}';
  final note = own ? '${scout['research_note'] ?? ''}' : '';
  final suggested = own
      ? (scout['suggested_sources'] as List? ?? const []).length
      : 0;
  final assumptions = (item['assumptions'] as List? ?? const [])
      .map((value) => '$value')
      .toList();
  final kill = (item['kill_criteria'] as List? ?? const [])
      .map((value) => '$value')
      .toList();
  final evidence = (item['evidence'] as List? ?? const [])
      .map((value) => '$value')
      .join(', ');
  final confidence = item['confidence'];
  final canReview =
      state == 'proposed' ||
      state == 'approved' ||
      state == 'creating' ||
      state == 'failed';
  final hasVenture = '${item['venture_id'] ?? ''}'.isNotEmpty;
  final canEdit = !hasVenture && state != 'creating';
  // The duplicate check compares words, so it can be wrong. An exclusion term
  // set by the operator cannot be overridden here.
  final likelyDuplicate =
      state == 'excluded' &&
      !hasVenture &&
      '${item['duplicate_of'] ?? ''}'.isNotEmpty &&
      '${item['excluded_by'] ?? ''}'.isEmpty;
  return disclosure('${item['name'] ?? 'Untitled candidate'} · $state', [
    facts([
      ('Organization', text(organizationName)),
      (
        'Confidence',
        text(confidence == null ? '—' : '$confidence / 100 (agent opinion)'),
      ),
      ('Thesis', text('${item['thesis'] ?? ''}')),
      ('Product', text('${item['product'] ?? ''}')),
      ('Pricing', text('${item['pricing'] ?? 'Not proposed'}')),
      ('Evidence', text(evidence.isEmpty ? 'None' : evidence)),
      // What you would check before approving, without opening Edit.
      if (market.isNotEmpty)
        (
          'Market',
          text(
            '${'${market['geography'] ?? ''}'.isEmpty ? 'No geography' : market['geography']} · '
            '${market['currency'] ?? ''}',
          ),
        ),
      if (outreach.isNotEmpty)
        (
          'Outreach',
          text(
            '${outreach['persona'] ?? ''} · language ${outreach['language'] ?? ''} · '
            '${outreach['stage'] ?? ''} · tone ${outreach['tone'] ?? ''}',
          ),
        ),
      (
        'News feeds',
        text(
          feeds.isEmpty
              ? 'None: without feeds the venture only has quiet days. Attach the studio sources when you create it.'
              : '${feeds.length}: ${feeds.join(', ')}',
        ),
      ),
      if ('${item['duplicate_of'] ?? ''}'.isNotEmpty)
        ('Duplicate of', text('${item['duplicate_of']}')),
      if ('${item['excluded_by'] ?? ''}'.isNotEmpty)
        ('Excluded by', text('"${item['excluded_by']}"')),
      ...candidateBar(item),
    ]),
    candidateDiligence(item),
    if (research.isNotEmpty)
      disclosure(
        'Research (${research.length} page${research.length == 1 ? '' : 's'}, agent-reported, not verified)',
        [for (final link in research) _researchPage(link)],
      ),
    if (note.isNotEmpty) paragraph('Scout\'s note: $note'),
    if (suggested > 0)
      paragraph(
        'The scout suggested $suggested source${suggested == 1 ? '' : 's'} for this focus. Add them on the Studio tab.',
      ),
    if ((item['scout_adjusted'] as Map? ?? const {}).isNotEmpty)
      paragraph(
        'The gateway reset the values the scout proposed for the bar, the schedule and capital above a '
        'year of costs. You can change them with Edit.',
      ),
    if (assumptions.isNotEmpty) ...[
      paragraph('Assumptions'),
      for (final value in assumptions) paragraph('• $value'),
    ],
    if (kill.isNotEmpty) ...[
      paragraph('Kill criteria'),
      for (final value in kill) paragraph('• $value'),
    ],
    if (item['error'] != null && '${item['error']}'.isNotEmpty)
      notice('${item['error']}', error: true),
    if (item['decision'] is Map)
      paragraph(
        'Decision: ${(item['decision'] as Map)['decision'] ?? 'recorded'} '
        '${(item['decision'] as Map)['reason'] ?? ''}',
      ),
    if (item['venture_id'] != null)
      paragraph('Created venture: ${item['venture_id']}'),
    if (likelyDuplicate)
      paragraph(
        'This looks like a duplicate. Edit it to make the difference clear, or create it anyway.',
      ),
    actions([
      if (canEdit)
        button('Edit', busy ? null : () => onEdit(item), small: true),
      if (canReview &&
          (state == 'proposed' ||
              state == 'approved' ||
              state == 'failed' ||
              state == 'creating'))
        button(
          'Create venture',
          busy ? null : () => onApprove(item),
          small: true,
          primary: true,
        ),
      if (likelyDuplicate)
        button(
          'Create anyway',
          busy ? null : () => onApprove(item),
          small: true,
        ),
      if (canReview && !hasVenture)
        button(
          'Reject',
          busy ? null : () => onReject(item),
          small: true,
          variant: 'danger',
        ),
      // Read-only, so it stays available while another action runs and
      // for decided ideas too.
      if (onExport != null)
        button(
          'Export brief',
          () => onExport(item),
          small: true,
          iconName: 'file-text',
        ),
    ]),
  ]);
}
