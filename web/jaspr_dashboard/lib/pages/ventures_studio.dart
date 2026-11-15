import 'operations_support.dart';
import 'ops_ui.dart';

/// Portfolio review and venture scouting, separate from the operating loop.
Component venturesStudio({
  required Map<String, dynamic>? studio,
  required List<Map<String, dynamic>> candidates,
  required String organizationName,
  required bool busy,
  required void Function() onSetup,
  required void Function() onConfigure,
  required void Function() onRunPortfolio,
  required void Function() onRunScout,
  required void Function(bool paused) onPause,
  required void Function(Map<String, dynamic> run) onRecover,
  required void Function(Map<String, dynamic> run) onDismiss,
  required void Function(Map<String, dynamic> recommendation) onRecommendation,
  Map<String, String> ventureNames = const {},
}) {
  final record = Map<String, dynamic>.from(
    studio?['studio'] as Map? ?? const {},
  );
  final policy = Map<String, dynamic>.from(
    studio?['policy'] as Map? ?? const {},
  );
  final runs = List<Map<String, dynamic>>.from(
    studio?['runs'] as List? ?? const [],
  );
  final budget = Map<String, dynamic>.from(
    studio?['budget'] as Map? ?? const {},
  );
  final groupId = '${record['group_id'] ?? ''}';
  if (groupId.isEmpty || '${record['notebook'] ?? ''}'.isEmpty) {
    return card(
      'Venture Studio',
      [
        emptyState(
          'Set up one managed notebook and one read-only group for portfolio reviews and venture scouting.',
          iconName: 'search',
          actions: [
            button('Set up studio', busy ? null : onSetup, primary: true),
          ],
        ),
      ],
      subtitle:
          'The studio proposes; it never creates a venture or performs a recommendation.',
    );
  }
  final portfolio = runs.where((item) => item['kind'] == 'portfolio').toList();
  final scout = runs.where((item) => item['kind'] == 'scout').toList();
  final latestPortfolio = portfolio.isEmpty ? null : portfolio.first;
  final latestScout = scout.isEmpty ? null : scout.first;
  final schedule = Map<String, dynamic>.from(
    studio?['schedule'] as Map? ?? const {},
  );
  final invalid = (studio?['invalid_policy'] as List? ?? const [])
      .map((value) => '$value')
      .toList();
  final lessons = List<Map<String, dynamic>>.from(
    studio?['lessons'] as List? ?? const [],
  );
  final attention = runs
      .where((item) => item['state'] == 'needs_attention')
      .toList();
  Component recoverActions(Map<String, dynamic> run) => actions([
    button(
      run['dispatch_failed'] == true ? 'Re-send prompt' : 'Recover result',
      busy ? null : () => onRecover(run),
      small: true,
    ),
    button(
      'Dismiss run',
      busy ? null : () => onDismiss(run),
      small: true,
      variant: 'danger',
    ),
  ]);
  return card(
    'Venture Studio',
    [
      facts([
        ('Group', text(groupId)),
        ('Notebook', mono('${record['notebook'] ?? ''}')),
        (
          'Portfolio cadence',
          text('${policy['studio_portfolio_cadence'] ?? 'off'}'),
        ),
        ('Scout cadence', text('${policy['studio_scout_cadence'] ?? 'off'}')),
        (
          'Candidate cap',
          text(
            '${policy['studio_max_candidates'] ?? 2} per run, '
            '${policy['studio_candidate_backlog'] ?? 6} backlog',
          ),
        ),
        (
          'Scout sources',
          text(
            '${(policy['studio_sources'] as List? ?? const []).length} allowed',
          ),
        ),
        ('Paused', text(policy['studio_paused'] == true ? 'Yes' : 'No')),
      ]),
      actions([
        button(
          'Run portfolio now',
          busy ? null : onRunPortfolio,
          small: true,
          primary: true,
        ),
        button('Run scout now', busy ? null : onRunScout, small: true),
        button('Configure studio', busy ? null : onConfigure, small: true),
        button(
          policy['studio_paused'] == true ? 'Resume studio' : 'Pause studio',
          busy ? null : () => onPause(policy['studio_paused'] != true),
          small: true,
        ),
      ]),
      if (invalid.isNotEmpty)
        notice(
          'Stored studio settings out of range, using defaults: ${invalid.join(', ')}. '
          'Save the studio settings to repair them.',
          error: true,
        ),
      for (final kind in const ['portfolio', 'scout'])
        if (schedule[kind] is Map) _scheduleLine(kind, schedule[kind] as Map),
      notice(
        'Projected per day: ${budget['daily_meetings'] ?? 0} guaranteed meeting(s), up to '
        '${budget['max_daily_meetings'] ?? budget['daily_meetings'] ?? 0} with news-only ventures; '
        '${budget['projected_agent_sessions'] ?? 0} to ${budget['max_agent_sessions'] ?? 0} agent sessions. '
        'Studio runs per week: ${budget['studio_runs_per_week'] ?? 0}. '
        'Limits: ${budget['max_active_meetings'] ?? 0} active meetings, '
        '${budget['daily_session_cap'] ?? 0} sessions per day (0 = off).',
        error: budget['over_cap'] == true,
      ),
      if (budget['over_cap'] == true)
        paragraph(
          'Over the daily session cap: changes that add meetings are refused until it is raised.',
        ),
      if (attention.isNotEmpty)
        disclosure('Needs attention (${attention.length})', [
          paragraph(
            'These runs do not block the studio. Recover re-processes the saved result or re-sends a prompt '
            'that never reached the group; dismiss closes a run whose result cannot be used.',
          ),
          for (final run in attention)
            el('article', [
              paragraph('${run['kind']} · ${displayTime(run['created_at'])}'),
              notice('${run['error'] ?? ''}', error: true),
              recoverActions(run),
            ], classes: 'stack'),
        ]),
      if (latestPortfolio == null)
        paragraph('No portfolio review yet.')
      else
        disclosure('Portfolio review · ${latestPortfolio['state']}', [
          if ('${latestPortfolio['error'] ?? ''}'.isNotEmpty)
            notice('${latestPortfolio['error']}', error: true)
          else if (latestPortfolio['state'] == 'skipped')
            paragraph('${latestPortfolio['reason'] ?? 'Skipped.'}')
          else ...[
            paragraph('${latestPortfolio['summary'] ?? 'No summary.'}'),
            for (final item in List<Map<String, dynamic>>.from(
              latestPortfolio['recommendations'] as List? ?? const [],
            ))
              _recommendation(item, ventureNames, busy, onRecommendation),
          ],
          if (latestPortfolio['state'] == 'needs_attention')
            recoverActions(latestPortfolio),
        ]),
      if (latestScout == null)
        paragraph('No scout review yet.')
      else
        disclosure('Scout review · ${latestScout['state']}', [
          if ('${latestScout['error'] ?? ''}'.isNotEmpty)
            notice('${latestScout['error']}', error: true)
          else
            paragraph(
              '${(latestScout['candidates'] as List? ?? const []).length} candidate(s) produced. '
              'Open Ideas to review them.',
            ),
          for (final refused in List<Map<String, dynamic>>.from(
            latestScout['refused_candidates'] as List? ?? const [],
          ))
            paragraph(
              'Refused by the gateway: ${refused['name'] ?? 'unnamed'}: ${refused['error'] ?? ''}',
            ),
          if (latestScout['state'] == 'needs_attention')
            recoverActions(latestScout),
        ]),
      if (lessons.isNotEmpty)
        disclosure('Lessons the studio sees (${lessons.length})', [
          paragraph(
            'Built from recorded outcomes. They shape recommendations, never the score formula.',
          ),
          for (final lesson in lessons)
            paragraph('${lesson['kind'] ?? ''}: ${lesson['title'] ?? ''}'),
        ]),
      if (runs.isNotEmpty)
        disclosure('Studio runs', [
          table(
            ['Kind', 'State', 'Created', 'Error'],
            [
              for (final run in runs.take(20))
                [
                  text('${run['kind'] ?? ''}'),
                  pill('${run['state'] ?? ''}', tone: stateTone(run['state'])),
                  text(displayTime(run['created_at'])),
                  text(
                    '${run['error'] ?? ''}'.isNotEmpty
                        ? '${run['error']}'
                        : run['dismissed'] is Map
                        ? 'Dismissed: ${(run['dismissed'] as Map)['reason'] ?? ''}'
                        : '${run['reason'] ?? ''}',
                  ),
                ],
            ],
          ),
        ]),
      if (candidates.isNotEmpty)
        paragraph('${candidates.length} candidate(s) are waiting in Ideas.'),
    ],
    subtitle:
        'Recommended actions are inert until you click them. The studio never spends, publishes or sends mail.',
  );
}

Component _scheduleLine(String kind, Map schedule) {
  final cadence = '${schedule['cadence'] ?? 'off'}';
  final error = '${schedule['error'] ?? ''}';
  final failures = schedule['failures'] is num
      ? schedule['failures'] as num
      : 0;
  final label = kind == 'portfolio' ? 'Portfolio review' : 'Scout';
  if (cadence == 'off') return paragraph('$label: off.');
  final line =
      '$label: $cadence · last ${schedule['last_at'] == null ? 'never' : displayTime(schedule['last_at'])}'
      ' · next ${displayTime(schedule['next_at'])}';
  return error.isEmpty
      ? paragraph(line)
      : notice(
          '$line · last scheduled attempt failed'
          '${failures > 1 ? ' ($failures times)' : ''}: $error',
          error: true,
        );
}

Component _recommendation(
  Map<String, dynamic> item,
  Map<String, String> ventureNames,
  bool busy,
  void Function(Map<String, dynamic>) onRecommendation,
) {
  final action = '${item['recommendation'] ?? 'investigate'}';
  final evidence = (item['evidence'] as List? ?? const [])
      .map((value) => '$value')
      .join(', ');
  final id = '${item['venture_id'] ?? ''}';
  final named = '${item['venture_name'] ?? ''}';
  final name = named.isNotEmpty ? named : (ventureNames[id] ?? id);
  return el('article', [
    paragraph('#${item['rank'] ?? '—'} $name · $action'),
    paragraph('${item['reason'] ?? ''}'),
    paragraph('Evidence: ${evidence.isEmpty ? 'none' : evidence}'),
    actions([
      if (action == 'pause' || action == 'kill')
        button(
          action == 'kill' ? 'Archive venture' : 'Pause venture',
          busy ? null : () => onRecommendation(item),
          small: true,
          variant: 'danger',
        )
      else
        button(
          'Open venture',
          busy ? null : () => onRecommendation(item),
          small: true,
        ),
    ]),
  ], classes: 'stack');
}
