import 'operations_support.dart';
import 'ops_ui.dart';

/// What each recovery action is called on a button. The gateway names the
/// action; the dashboard never guesses which one fits a failure.
const studioActionLabels = {
  'start_herdr': 'Start Herdr',
  'repair': 'Repair studio',
  'setup': 'Set up Venture Studio',
  'configure': 'Configure studio',
  'limits': 'Raise limits for the studio',
  'cli_accounts': 'Open CLI accounts',
  'open_group': 'Open the studio group',
  'dismiss': 'Dismiss run',
};

const _phaseLabels = {
  'dispatch': 'before the prompt was sent',
  'preparation': 'while the agents were starting',
  'meeting': 'during the meeting',
  'validation': 'when the result was checked',
};

const _modeLabels = {
  'proposal': 'Proposal: the studio suggests, you approve',
  'autonomous': 'Autonomous: the studio creates ventures within the caps',
};

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
  void Function(String action, Map<String, dynamic>? run)? onAction,
  void Function()? onRunScoutAnyway,
  void Function()? onStartTeam,
  void Function(String url)? onAddSource,
  String herdr = '',
  List<Map<String, dynamic>> runtimes = const [],
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
  final readiness = Map<String, dynamic>.from(
    studio?['readiness'] as Map? ?? const {},
  );
  void act(String action, [Map<String, dynamic>? run]) {
    if (action == 'setup') return onSetup();
    if (action == 'configure') return onConfigure();
    if (action == 'dismiss' && run != null) return onDismiss(run);
    onAction?.call(action, run);
  }

  final groupId = '${record['group_id'] ?? ''}';
  if (groupId.isEmpty || '${record['notebook'] ?? ''}'.isEmpty) {
    // Before a studio exists there may be no organization either, so the
    // environment checks come from the page: Herdr and the agent CLIs.
    final ready = runtimes.where((item) => item['ready'] == true).toList();
    final checks = readiness['checks'] is List
        ? List<Map<String, dynamic>>.from(readiness['checks'] as List)
        : <Map<String, dynamic>>[
            {
              'id': 'herdr',
              'label': 'Herdr is running',
              'ok': herdr != 'stopped',
              'detail': herdr == 'stopped'
                  ? 'Stopped: no agent can start.'
                  : 'Running.',
              'action': herdr == 'stopped' ? 'start_herdr' : '',
            },
            {
              'id': 'provider',
              'label': 'An agent CLI is ready',
              'ok': ready.isNotEmpty || runtimes.isEmpty,
              'detail': ready.isNotEmpty
                  ? '${ready.map((item) => item['name']).join(', ')} ready.'
                  : runtimes
                        .map((item) => '${item['name']}: ${item['reason']}')
                        .join('; '),
              'action': ready.isNotEmpty || runtimes.isEmpty
                  ? ''
                  : 'cli_accounts',
            },
          ];
    return card(
      'Venture Studio',
      [
        emptyState(
          'No AI venture proposals yet. Create the studio and it scouts businesses for you: '
          'one click creates its notebook, four agents and a read-only group.',
          iconName: 'search',
          actions: [
            button(
              'Set up Venture Studio',
              busy ? null : onSetup,
              primary: true,
            ),
          ],
        ),
        _checklist(
          checks.where((item) => item['id'] != 'team').toList(),
          busy,
          act,
        ),
        paragraph(
          'You do not need to create an organization, an agent or a project folder first. '
          'An organization is only the container for the studio\'s agents, limits and history; '
          'one is created for you when none exists.',
        ),
      ],
      subtitle:
          'In proposal mode the studio suggests and you approve. Autonomous mode is an explicit setting.',
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
  final meeting = runs
      .where((item) => item['state'] == 'meeting' && item['waiting'] is Map)
      .toList();
  // A snapshot from an older gateway has no readiness: nothing is blocked.
  final canRun = readiness['can_run'] != false;
  final canScout = readiness['can_scout'] != false;
  final coverage = Map<String, dynamic>.from(
    readiness['coverage'] as Map? ?? const {},
  );
  final checks = List<Map<String, dynamic>>.from(
    readiness['checks'] as List? ?? const [],
  );
  final mode = '${studio?['mode'] ?? policy['studio_mode'] ?? 'proposal'}';
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
        ('Mode', text(_modeLabels[mode] ?? mode)),
        ('Group', text(groupId)),
        if (_agentSettings(record).isNotEmpty)
          ('Agents run on', text(_agentSettings(record))),
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
      if (checks.isNotEmpty) _checklist(checks, busy, act),
      actions([
        button(
          'Run portfolio now',
          busy || !canRun ? null : onRunPortfolio,
          small: true,
          primary: true,
        ),
        button(
          'Run scout now',
          busy || !canScout ? null : onRunScout,
          small: true,
        ),
        if (canRun && !canScout && onRunScoutAnyway != null)
          button(
            'Run scout without sources',
            busy ? null : onRunScoutAnyway,
            small: true,
          ),
        button('Configure studio', busy ? null : onConfigure, small: true),
        if (onStartTeam != null)
          button(
            'Start studio team',
            busy || !canRun ? null : onStartTeam,
            small: true,
          ),
        if (onAction != null)
          button(
            'Repair studio',
            busy ? null : () => act('repair'),
            small: true,
          ),
        button(
          policy['studio_paused'] == true ? 'Resume studio' : 'Pause studio',
          busy ? null : () => onPause(policy['studio_paused'] != true),
          small: true,
        ),
      ]),
      if (!canRun)
        notice(
          'The studio cannot start a meeting yet. Fix the items marked above; each has its own action.',
          error: true,
        )
      else if (!canScout)
        notice(
          'Evidence coverage: ${coverage['sources'] ?? 0} source(s), '
          '${(coverage['focus'] ?? 0) == 0 ? 'no focus' : '${coverage['focus']} focus area(s)'}. '
          'A scout without market evidence proposes weak ideas; add sources and a focus in the studio settings.',
          error: true,
        )
      else if (coverage['web_research'] == true)
        notice(
          'No readable source: the next scout researches the web itself, lists every page it relies on and '
          'suggests feeds you can add. Its pages are agent-reported, not verified by the gateway.',
        ),
      if (mode == 'autonomous')
        notice(
          'Autonomous mode: after a scout, the studio creates at most one venture per run, only above '
          '${policy['studio_auto_min_confidence'] ?? 70} confidence, only when the idea cites an outside source, '
          'and never above the active venture limit. It hires that venture\'s team and starts its daily reviews. '
          'It never sends mail, spends or publishes.',
        ),
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
      for (final run in meeting)
        _waiting(run, Map<String, dynamic>.from(run['waiting'] as Map), busy, act),
      if (attention.isNotEmpty)
        disclosure('Needs attention (${attention.length})', [
          paragraph(
            'These runs do not block the studio. Each one names what went wrong and the one action that '
            'fixes it; Repair studio does all of them in order.',
          ),
          for (final run in attention)
            el('article', [
              paragraph('${run['kind']} · ${displayTime(run['created_at'])}'),
              if (run['failure'] is Map)
                ..._failure(
                  run,
                  Map<String, dynamic>.from(run['failure'] as Map),
                  busy,
                  act,
                )
              else
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
          if (latestScout['web_research'] == true)
            paragraph(
              'This scout researched the web because no source gave items. '
              '${latestScout['research_note'] ?? ''}',
            ),
          for (final page in List<Map<String, dynamic>>.from(
            latestScout['research'] as List? ?? const [],
          ))
            paragraph(
              '${page['id']}: ${page['title'] ?? ''} · ${page['url'] ?? ''}'
              '${'${page['published'] ?? ''}'.isEmpty ? '' : ' · ${page['published']}'} '
              '(agent-reported, not verified)',
            ),
          for (final source in List<Map<String, dynamic>>.from(
            latestScout['suggested_sources'] as List? ?? const [],
          ))
            el('div', [
              text(
                'Suggested source: ${source['title'] ?? ''} · ${source['url'] ?? ''}'
                '${'${source['reason'] ?? ''}'.isEmpty ? '' : ' · ${source['reason']}'} ',
              ),
              if ((policy['studio_sources'] as List? ?? const []).contains(
                source['url'],
              ))
                pill('Added', tone: 'success')
              else if (onAddSource != null)
                button(
                  'Add source',
                  busy ? null : () => onAddSource('${source['url']}'),
                  small: true,
                ),
            ], classes: 'actions'),
          for (final item in List<Map<String, dynamic>>.from(
            latestScout['auto'] as List? ?? const [],
          ))
            paragraph(
              item['created'] == true
                  ? 'Created automatically: ${item['name'] ?? ''}.'
                  : 'Not created automatically: ${item['name'] ?? ''}: ${item['reason'] ?? ''}',
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
    subtitle: mode == 'autonomous'
        ? 'The studio creates ventures within its caps. It never spends, publishes or sends mail.'
        : 'Recommended actions are inert until you click them. The studio never spends, publishes or sends mail.',
  );
}

/// The CLI and model the studio agents were created with, and how it was chosen.
String _agentSettings(Map<String, dynamic> record) {
  final team = record['team'] as Map? ?? const {};
  final roles = team['settings_by_role'] as Map? ?? const {};
  final shown = <String>{
    for (final settings in roles.values)
      if (settings is Map)
        '${settings['runtime'] ?? ''} · '
            '${'${settings['model'] ?? ''}'.isEmpty ? 'CLI default model' : [if ('${settings['provider'] ?? ''}'.isNotEmpty) settings['provider'], settings['model']].join('/')}',
  };
  if (shown.isEmpty) return '';
  const sources = {
    'detected': 'detected from the account',
    'automatic': 'first ready CLI',
    'settings': 'chosen at setup',
    'agent': 'copied from an agent',
    'templates': 'copied from agents',
  };
  final source = sources['${team['settings_source'] ?? ''}'];
  return '${shown.join('; ')}${source == null ? '' : ' ($source)'}';
}

/// The setup checklist: one line per requirement, with the action that fixes it.
Component _checklist(
  List<Map<String, dynamic>> checks,
  bool busy,
  void Function(String action, [Map<String, dynamic>? run]) act,
) => el('div', [
  for (final check in checks)
    el('div', [
      pill(
        check['ok'] == true ? 'Ready' : 'Needs you',
        tone: check['ok'] == true ? 'success' : 'danger',
        shape: shapeFor(check['ok'] == true ? 'success' : 'danger'),
      ),
      text(' ${check['label'] ?? ''}: ${check['detail'] ?? ''} '),
      if ('${check['action'] ?? ''}'.isNotEmpty)
        button(
          studioActionLabels['${check['action']}'] ?? '${check['action']}',
          busy ? null : () => act('${check['action']}'),
          small: true,
          primary: true,
        ),
    ], classes: 'actions'),
], classes: 'stack');

/// A named failure: what happened, in which phase, to which agent, and the
/// one action that recovers it.
List<Component> _failure(
  Map<String, dynamic> run,
  Map<String, dynamic> failure,
  bool busy,
  void Function(String action, [Map<String, dynamic>? run]) act,
) {
  final action = '${failure['action'] ?? ''}';
  final agent = '${failure['agent'] ?? ''}';
  final phase = _phaseLabels['${failure['phase']}'] ?? '${failure['phase'] ?? ''}';
  return [
    paragraph(
      '${failure['title'] ?? 'The run stopped'} · $phase'
      '${agent.isEmpty ? '' : ' · $agent'}',
    ),
    notice('${failure['message'] ?? run['error'] ?? ''}', error: true),
    paragraph('${failure['help'] ?? ''}'),
    if (action.isNotEmpty && action != 'dismiss')
      actions([
        button(
          studioActionLabels[action] ?? action,
          busy ? null : () => act(action, run),
          small: true,
          primary: true,
        ),
      ]),
  ];
}

/// A meeting that is still running but waits on something the operator can see.
Component _waiting(
  Map<String, dynamic> run,
  Map<String, dynamic> waiting,
  bool busy,
  void Function(String action, [Map<String, dynamic>? run]) act,
) {
  final action = '${waiting['action'] ?? ''}';
  return el('article', [
    notice(
      'The ${run['kind']} meeting is waiting: ${waiting['title'] ?? ''}. ${waiting['message'] ?? ''}',
      error: waiting['category'] == 'permission',
    ),
    paragraph('${waiting['help'] ?? ''}'),
    if (action.isNotEmpty)
      actions([
        button(
          studioActionLabels[action] ?? action,
          busy ? null : () => act(action, run),
          small: true,
          primary: true,
        ),
      ]),
  ], classes: 'stack');
}

Component _scheduleLine(String kind, Map schedule) {
  final cadence = '${schedule['cadence'] ?? 'off'}';
  final error = '${schedule['error'] ?? ''}';
  final waiting = '${schedule['waiting'] ?? ''}';
  final failures = schedule['failures'] is num
      ? schedule['failures'] as num
      : 0;
  final label = kind == 'portfolio' ? 'Portfolio review' : 'Scout';
  if (cadence == 'off') return paragraph('$label: manual only.');
  final line =
      '$label: $cadence · last ${schedule['last_at'] == null ? 'never' : displayTime(schedule['last_at'])}'
      ' · next ${displayTime(schedule['next_at'])}';
  if (error.isNotEmpty) {
    return notice(
      '$line · last scheduled attempt failed'
      '${failures > 1 ? ' ($failures times)' : ''}: $error',
      error: true,
    );
  }
  // Waiting is not a failure: the run starts by itself once this clears.
  return waiting.isEmpty
      ? paragraph(line)
      : notice('$line · waiting, nothing was started: $waiting');
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
