import 'package:herdr_dashboard_state/ventures_bloc.dart';
import 'package:juice/juice.dart';

import 'dashboard_bloc.dart';
import 'feature_view.dart';

/// Flutter reference view of venture simulation, backed by the shared
/// [VenturesBloc]. Creating and configuring ventures stays on the Jaspr
/// release dashboard; this page covers the daily operating loop: reading
/// the portfolio, running a tick, pausing, and reviewing outreach drafts.
class VenturesPage extends StatefulWidget {
  const VenturesPage({super.key});
  @override
  State<VenturesPage> createState() => _VenturesPageState();
}

class _VenturesPageState extends State<VenturesPage>
    with FeatureView<VenturesPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final VenturesBloc bloc;
  bool get admin => connection.operator.value['role'] == 'admin';
  bool get busy => featureBusy;

  @override
  void initState() {
    super.initState();
    bindFeature(bloc = VenturesBloc(connection));
    if (admin) bloc.refresh();
  }

  Future<void> submit(
    String mode,
    Map<String, dynamic> venture, [
    Map<String, dynamic> body = const {},
  ]) async {
    try {
      await bloc.submit(mode, {
        'organization_id': '${venture['organization_id']}',
        ...body,
      }, ventureId: '${venture['id']}');
    } catch (_) {
      // The bloc records the error; the page shows it from feature state.
    }
  }

  static const openStates = {'proposed', 'approved', 'creating', 'failed'};

  List<Map<String, dynamic>> _open(Object? organization) => bloc.candidates
      .where(
        (item) =>
            item['organization_id'] == organization &&
            openStates.contains('${item['state']}'),
      )
      .toList();

  int _decided(Object? organization) => bloc.candidates
      .where(
        (item) =>
            item['organization_id'] == organization &&
            !openStates.contains('${item['state']}'),
      )
      .length;

  String _organizationName(String identity) {
    for (final organization in bloc.organizations) {
      if (organization['id'] == identity) return '${organization['name']}';
    }
    return identity;
  }

  String score(Map<String, dynamic> venture) {
    final value =
        venture['score'] ??
        ((venture['last_tick'] as Map?)?['ledger'] as Map?)?['score'];
    return value == null ? '—' : '$value';
  }

  String qualification(Map<String, dynamic> venture) {
    if (venture['state'] == 'qualified') return 'Qualified';
    if (venture['survival_threshold'] == null) return '—';
    return '${venture['qualifying_days'] ?? 0}/${venture['required_days'] ?? '?'} days at ≥ ${venture['survival_threshold']}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (!admin) {
      return ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Ventures', style: theme.textTheme.headlineSmall),
          const SizedBox(height: 12),
          const Text('Only an administrator can view or manage ventures.'),
        ],
      );
    }
    if (!bloc.enabled) {
      return ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Ventures', style: theme.textTheme.headlineSmall),
          const SizedBox(height: 12),
          const Text(
            'Venture simulation is switched off for this installation. '
            'An administrator can switch it on from the web dashboard.',
          ),
        ],
      );
    }
    final selected = bloc.selected;
    final error = featureError;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Expanded(
              child: Text('Ventures', style: theme.textTheme.headlineSmall),
            ),
            IconButton(
              tooltip: 'Refresh',
              onPressed: busy ? null : () => bloc.refresh(),
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const Text(
          'Simulated B2B companies run against daily news. Create and configure ventures on the release dashboard.',
        ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              error,
              style: TextStyle(color: theme.colorScheme.error),
            ),
          ),
        const SizedBox(height: 12),
        if (bloc.ventures.isEmpty && !bloc.state.loading)
          const Card(
            child: Padding(
              padding: EdgeInsets.all(16),
              child: Text('No ventures yet.'),
            ),
          ),
        for (final venture in bloc.ventures)
          Card(
            child: ListTile(
              selected: selected?['id'] == venture['id'],
              title: Text('${venture['name']}'),
              subtitle: Text(
                '${venture['state']} · score ${score(venture)} · ${qualification(venture)}',
              ),
              trailing: TextButton(
                onPressed: busy ? null : () => submit('run', venture),
                child: const Text('Run tick now'),
              ),
              onTap: () => bloc.select('${venture['id']}'),
            ),
          ),
        if (selected != null) ...[
          const SizedBox(height: 16),
          detail(context, selected),
        ],
        if (bloc.candidates.isNotEmpty) ...[
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Venture ideas', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 8),
                  // Grouped by organization; only candidates awaiting a
                  // decision are listed, decided ones are counted.
                  for (final organization in bloc.organizations)
                    if (bloc.candidates.any(
                      (item) => item['organization_id'] == organization['id'],
                    )) ...[
                      Text(
                        '${organization['name']} · '
                        '${_open(organization['id']).length} waiting, '
                        '${_decided(organization['id'])} decided',
                        style: theme.textTheme.labelLarge,
                      ),
                      for (final item in _open(organization['id']))
                        ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          title: Text('${item['name']} - ${item['state']}'),
                          subtitle: Text('${item['thesis'] ?? ''}'),
                        ),
                    ],
                ],
              ),
            ),
          ),
        ],
        if (bloc.studioFor(
              bloc.ventures.isEmpty
                  ? ''
                  : '${bloc.ventures.first['organization_id']}',
            ) !=
            null) ...[
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Venture Studio', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 8),
                  for (final studio in bloc.studios) ...[
                    Text(_organizationName('${studio['organization_id']}')),
                    for (final kind in const ['portfolio', 'scout'])
                      if ('${((studio['schedule'] as Map?)?[kind] as Map?)?['error'] ?? ''}'
                          .isNotEmpty)
                        Text(
                          '${kind == 'portfolio' ? 'Portfolio review' : 'Scout'} scheduling failed: '
                          '${((studio['schedule'] as Map)[kind] as Map)['error']}',
                          style: TextStyle(color: theme.colorScheme.error),
                        ),
                    if ((studio['invalid_policy'] as List? ?? const [])
                        .isNotEmpty)
                      Text(
                        'Out-of-range studio settings use defaults: '
                        '${(studio['invalid_policy'] as List).join(', ')}',
                        style: TextStyle(color: theme.colorScheme.error),
                      ),
                    Text(
                      'Portfolio cadence: ${(studio['policy'] as Map?)?['studio_portfolio_cadence'] ?? 'off'}',
                    ),
                    Text(
                      'Scout cadence: ${(studio['policy'] as Map?)?['studio_scout_cadence'] ?? 'off'}',
                    ),
                    Text(
                      'Budget: ${(studio['budget'] as Map?)?['daily_meetings'] ?? 0} meetings / '
                      '${(studio['budget'] as Map?)?['projected_agent_sessions'] ?? 0} sessions per day',
                    ),
                    for (final run
                        in (studio['runs'] as List? ?? const []).take(4))
                      if (run is Map)
                        ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          title: Text('${run['kind']} - ${run['state']}'),
                          subtitle: Text(
                            '${run['summary'] ?? run['error'] ?? ''}',
                          ),
                        ),
                  ],
                ],
              ),
            ),
          ),
        ],
        const SizedBox(height: 16),
        const Text(
          'The gateway never sends email and stores no recipient addresses. Review and send outreach yourself.',
        ),
      ],
    );
  }

  Widget detail(BuildContext context, Map<String, dynamic> venture) {
    final theme = Theme.of(context);
    final qualified =
        venture['state'] == 'qualified' || venture['qualified_at'] != null;
    final paused = venture['state'] == 'paused';
    final roles = venture['roles'] as Map? ?? const {};
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${venture['name']}', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            Wrap(
              spacing: 16,
              runSpacing: 4,
              children: [
                Text('Capital ${venture['capital']} ${venture['currency']}'),
                Text('Score ${score(venture)}'),
                Text(qualification(venture)),
                Text(
                  venture['runway_days'] == null
                      ? 'Runway —'
                      : 'Runway ${venture['runway_days']} days',
                ),
                Text('Pricing ${venture['pricing'] ?? 'not set'}'),
              ],
            ),
            if (roles.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                [
                  for (final entry in roles.entries)
                    '${entry.key}: ${(entry.value as List? ?? const []).join(', ')}',
                ].join(' · '),
              ),
            ],
            if ('${venture['schedule_error'] ?? ''}'.isNotEmpty)
              Text(
                'Scheduled tick: ${venture['schedule_error']}',
                style: TextStyle(color: theme.colorScheme.error),
              ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                FilledButton(
                  onPressed: busy ? null : () => submit('run', venture),
                  child: const Text('Run tick now'),
                ),
                OutlinedButton(
                  onPressed: busy
                      ? null
                      : () => submit(paused ? 'resume' : 'pause', venture),
                  child: Text(paused ? 'Resume' : 'Pause'),
                ),
              ],
            ),
            const Divider(height: 24),
            Text('Reviews', style: theme.textTheme.titleMedium),
            if (bloc.reviews.isEmpty) const Text('No review records yet.'),
            for (final review in bloc.reviews)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(
                  '${review['simulated_date']} - ${review['kind'] ?? review['review_kind']} - ${review['state']}',
                ),
                subtitle: Text('${review['reason'] ?? ''}'),
              ),
            const Divider(height: 24),
            Text('Ticks', style: theme.textTheme.titleMedium),
            if (bloc.ticks.isEmpty) const Text('No ticks yet.'),
            for (final tick in bloc.ticks)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text('${tick['simulated_date']} · ${tick['state']}'),
                subtitle: Text(
                  'score ${(tick['ledger'] as Map?)?['score'] ?? '—'} · capital ${(tick['ledger'] as Map?)?['capital_after'] ?? '—'}'
                  '${tick['quiet'] == true ? ' · quiet day' : ''}',
                ),
              ),
            const Divider(height: 24),
            Text('Outreach drafts', style: theme.textTheme.titleMedium),
            if (bloc.outreach.isEmpty) const Text('No drafts yet.'),
            if (bloc.outreach.isNotEmpty && !qualified)
              const Text(
                'Drafts unlock for approval when the venture qualifies.',
              ),
            for (final draft in bloc.outreach)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: Text('${draft['subject']}'),
                subtitle: Text(
                  '${draft['date']} · ${draft['persona']} · ${draft['language']} · ${draft['state']}',
                ),
                children: [
                  SelectableText('${draft['body']}'),
                  Wrap(
                    spacing: 8,
                    children: [
                      if (draft['state'] == 'draft')
                        TextButton(
                          onPressed: busy || !qualified
                              ? null
                              : () => submit('outreach', venture, {
                                  'outreach_id': '${draft['id']}',
                                  'decision': 'approve',
                                }),
                          child: const Text('Approve'),
                        ),
                      if (draft['state'] == 'draft' ||
                          draft['state'] == 'ready')
                        TextButton(
                          onPressed: busy
                              ? null
                              : () => submit('outreach', venture, {
                                  'outreach_id': '${draft['id']}',
                                  'decision': 'reject',
                                }),
                          child: const Text('Reject'),
                        ),
                      if (draft['state'] == 'ready' || draft['state'] == 'sent')
                        for (final outcome in const [
                          'sent',
                          'replied',
                          'meeting',
                          'declined',
                        ])
                          TextButton(
                            onPressed: busy
                                ? null
                                : () => submit('outreach', venture, {
                                    'outreach_id': '${draft['id']}',
                                    'decision': 'outcome',
                                    'outcome': outcome,
                                  }),
                            child: Text('Mark $outcome'),
                          ),
                    ],
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
