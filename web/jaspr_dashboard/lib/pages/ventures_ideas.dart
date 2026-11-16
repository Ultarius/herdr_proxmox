import 'operations_support.dart';
import 'ops_ui.dart';

/// Candidate states still waiting for an operator decision.
const openCandidateStates = {'proposed', 'approved', 'creating', 'failed'};

/// Number of candidates in [candidates] that still need a decision.
int openCandidateCount(Iterable<Map<String, dynamic>> candidates) => candidates
    .where((item) => openCandidateStates.contains('${item['state']}'))
    .length;

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
List<Component> _refusals(Map<String, dynamic>? scout) {
  final refused = List<Map<String, dynamic>>.from(
    scout?['refused_candidates'] as List? ?? const [],
  );
  if (refused.isEmpty) return const [];
  final one = refused.length == 1;
  return [
    notice(
      'The last scout review proposed ${refused.length} ${one ? 'idea' : 'ideas'} '
      'the gateway refused, so ${one ? 'it is' : 'they are'} not in this queue. '
      'Run the scout again; its prompt states the rules that were broken.',
      error: true,
    ),
    for (final item in refused)
      paragraph(
        '${item['name'] ?? 'Unnamed'}: ${item['error'] ?? 'no reason recorded'}',
      ),
  ];
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
}) {
  final refusals = _refusals(latestScout);
  if (candidates.isEmpty) {
    return card('Ideas', [
      ...refusals,
      emptyState(
        refusals.isEmpty
            ? 'No venture candidates yet for $organizationName. Set up the Venture Studio and run a scout review.'
            : 'No venture candidates waiting for $organizationName.',
        iconName: 'lightbulb',
      ),
    ], subtitle: 'Candidates stay proposals until an administrator creates one.');
  }
  final open = candidates
      .where((item) => openCandidateStates.contains('${item['state']}'))
      .toList();
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
              ),
          ],
        ),
    ],
    subtitle:
        '$organizationName · ${open.length} waiting. Evidence, assumptions and kill criteria are agent opinion until reviewed.',
  );
}

Component _candidate(
  Map<String, dynamic> item,
  String organizationName,
  bool busy,
  void Function(Map<String, dynamic>) onEdit,
  void Function(Map<String, dynamic>) onApprove,
  void Function(Map<String, dynamic>) onReject, {
  Map<String, dynamic>? scout,
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
      if (canReview && !hasVenture)
        button(
          'Reject',
          busy ? null : () => onReject(item),
          small: true,
          variant: 'danger',
        ),
    ]),
  ]);
}
