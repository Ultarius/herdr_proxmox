import 'dart:convert';

import 'package:herdr_dashboard_state/ventures_bloc.dart';

import 'operations_support.dart';
import 'ops_ui.dart';

class VenturesPage extends StatefulComponent {
  const VenturesPage({super.key});

  @override
  State<VenturesPage> createState() => _VenturesPageState();
}

class _VenturesPageState extends FeatureOperationsState<VenturesPage> {
  late final VenturesBloc bloc;
  bool _asked = false;

  @override
  void initState() {
    super.initState();
    bindFeature(bloc = VenturesBloc(connection), onChanged: _changed);
    if (admin) {
      _asked = true;
      unawaited(bloc.refresh());
    }
  }

  void _changed() {
    if (!admin) {
      _asked = false;
    } else if (!_asked) {
      _asked = true;
      unawaited(bloc.refresh());
    }
  }

  @override
  Component build(BuildContext context) {
    if (!admin) {
      return opsPage([
        pageHeader('Ventures', subtitle: 'Fictional companies run against real daily news.'),
        callout([calloutText('Only an administrator can view or manage ventures.')],
            tone: 'warning', title: 'Administrator access required'),
      ]);
    }
    if (!bloc.enabled) {
      return opsPage([
        pageHeader('Ventures', subtitle: 'Fictional companies run against real daily news.'),
        feedback(),
        callout([
          calloutText('Venture simulation is switched off for this installation. No ticks, schedules or feed '
              'requests run. Existing ventures and their notebooks are kept.'),
        ], tone: 'warning', title: 'Module switched off'),
        actions([
          button('Switch on', busy ? null : () => unawaited(_module(true)), primary: true),
        ]),
      ]);
    }
    final selected = bloc.selected;
    return opsPage([
      pageHeader(
        'Ventures',
        subtitle: 'Simulated B2B companies, daily news ticks and reviewed outreach drafts.',
        actions: [
          button('Refresh', busy ? null : () => unawaited(bloc.refresh()), iconName: 'refresh-cw'),
          button('Create venture', busy ? null : _openCreate, primary: true, iconName: 'plus'),
        ],
      ),
      feedback(),
      if (bloc.state.error != null && bloc.ventures.isEmpty)
        notice(bloc.state.error, error: true),
      if (bloc.state.loading && bloc.ventures.isEmpty)
        paragraph('Loading ventures…'),
      _portfolio(),
      if (selected != null) _detail(selected),
      _feeds(),
      _limits(),
      _moduleCard(),
      callout([
        calloutText('The gateway never sends email and stores no recipient addresses. '
            'Headlines are untrusted third-party text; the group is read-only advisory, not an OS sandbox. '
            'Review and send outreach yourself.'),
      ], title: 'Safety boundary', iconName: 'shield-check'),
    ]);
  }

  Component _portfolio() {
    final ventures = bloc.ventures;
    if (ventures.isEmpty) {
      return card('Portfolio', [
        emptyState('No ventures yet. Create a notebook, assign a read-only group and run a manual tick.',
            iconName: 'flask-conical', actions: [button('Create venture', _openCreate, primary: true)]),
      ], subtitle: 'One row per simulated company.');
    }
    return card('Portfolio', [
      table(
        ['Venture', 'State', 'Capital', 'Score', 'Qualification', 'Runway', 'Last tick', 'Actions'],
        [
          for (final venture in ventures)
            [
              button('${venture['name']}', () => unawaited(bloc.select('${venture['id']}')), small: true, variant: 'ghost'),
              pill('${venture['state']}', tone: stateTone(venture['state']), shape: shapeFor(stateTone(venture['state']))),
              text(_money(venture['capital'], venture['currency'])),
              text(_score(venture)),
              text(_qualification(venture)),
              text(_runway(venture)),
              text(_lastTick(venture)),
              actions([
                button('Run tick now', busy ? null : () => _run(venture), small: true),
                button('Configure', busy ? null : () => _configure(venture), small: true),
              ]),
            ],
        ],
      ),
    ], subtitle: 'Enable daily ticks per venture, or run one now. The active venture limit is '
        '${bloc.limits['max_active_ventures'] ?? 3}.');
  }

  String _qualification(Map<String, dynamic> venture) {
    if (venture['state'] == 'qualified') return 'Qualified';
    final threshold = venture['survival_threshold'];
    if (threshold == null) return '—';
    return '${venture['qualifying_days'] ?? 0}/${venture['required_days'] ?? '?'} days at ≥ $threshold';
  }

  String _schedule(Map<String, dynamic> venture) {
    final configuration = venture['configuration'] as Map? ?? const {};
    final simulation = configuration['simulation'] as Map? ?? const {};
    if (simulation['daily_ticks'] != true) return 'Manual only';
    final hour = '${simulation['tick_hour_utc'] ?? 6}'.padLeft(2, '0');
    return 'Daily at $hour:00 UTC · next ${displayTime(venture['next_at'])}';
  }

  String _roles(Map<String, dynamic> venture) {
    final roles = venture['roles'] as Map?;
    if (roles == null || roles.isEmpty) return 'Assigned when a group is set';
    const labels = {'analyst': 'Analyst', 'founder': 'Founder', 'red_team': 'Red Team', 'outreach': 'Outreach'};
    return [
      for (final entry in labels.entries)
        '${entry.value}: ${(roles[entry.key] as List? ?? const []).join(', ')}',
    ].join(' · ');
  }

  String _signals(Map<String, dynamic> venture) {
    final signals = venture['signals'] as Map? ?? const {};
    return 'sent ${signals['sent'] ?? 0}, replied ${signals['replied'] ?? 0}, '
        'meetings ${signals['meeting'] ?? 0}, declined ${signals['declined'] ?? 0}';
  }

  Component _detail(Map<String, dynamic> venture) {
    final configuration = Map<String, dynamic>.from(venture['configuration'] as Map? ?? {});
    final latest = bloc.ticks.isEmpty ? null : bloc.ticks.first;
    final attention = latest != null && latest['state'] == 'needs_attention';
    return card('Venture detail', [
      facts([
        ('Organization', text(_organizationName('${venture['organization_id']}'))),
        ('Notebook', mono('${venture['notebook']}')),
        ('Group', text(venture['group_id'] == null || '${venture['group_id']}'.isEmpty ? 'Not assigned yet' : '${venture['group_id']}')),
        ('Capital', text(_money(venture['capital'], venture['currency']))),
        ('Survival score', text(_score(venture))),
        ('Qualification', text(_qualification(venture))),
        ('Runway', text(_runway(venture))),
        ('Pricing', text(venture['pricing'] ?? 'Not set')),
        ('Schedule', text(_schedule(venture))),
        ('Roles', text(_roles(venture))),
        ('Real outreach', text(_signals(venture))),
        ('Discovery', text(venture['promoted_at'] == null
            ? 'Not promoted'
            : 'Promoted ${displayTime(venture['promoted_at'])}')),
        ('Created', text(displayTime(venture['created_at']))),
      ]),
      if ('${venture['schedule_error'] ?? ''}'.isNotEmpty)
        notice('Scheduled tick: ${venture['schedule_error']}', error: true),
      actions([
        button('Run tick now', busy ? null : () => _run(venture), primary: true),
        button(venture['state'] == 'paused' ? 'Resume' : 'Pause',
            busy ? null : () => _state(venture, venture['state'] == 'paused' ? 'resume' : 'pause')),
        button('Configure', busy ? null : () => _configure(venture)),
        if ('${venture['group_id'] ?? ''}'.isEmpty)
          button('Create team', busy ? null : () => _team(venture), iconName: 'users'),
        if ((venture['state'] == 'qualified' || venture['qualified_at'] != null) &&
            venture['promoted_at'] == null)
          button('Promote to discovery', busy ? null : () => _promote(venture), iconName: 'rocket'),
        if (venture['promoted_at'] != null)
          button('Restore previous discovery', busy ? null : () => _restorePromotion(venture),
              iconName: 'rotate-ccw'),
        if (attention)
          button('Recover artifact', busy ? null : () => _recover(venture), variant: 'danger'),
        button('Archive', busy ? null : () => _archive(venture), variant: 'danger'),
      ]),
      _chart('Survival score', (tick) => (tick['ledger'] as Map?)?['score'] as num?, 'var(--color-success)'),
      _chart('Capital', (tick) => (tick['ledger'] as Map?)?['capital_after'] as num?, 'var(--color-accent)'),
      disclosure('Ticks', [
        if (bloc.ticks.isEmpty) paragraph('No ticks yet.'),
        for (final tick in bloc.ticks) _tick(tick),
      ]),
      disclosure('Outreach drafts', [
        if (bloc.outreach.isEmpty) paragraph('No drafts yet. Each news tick can add up to three.'),
        if (bloc.outreach.isNotEmpty && venture['state'] != 'qualified')
          paragraph('Drafts unlock for approval when the venture qualifies.'),
        for (final draft in bloc.outreach) _draft(venture, draft),
      ]),
      disclosure('Configuration', [
        codeText(const JsonEncoder.withIndent('  ').convert(configuration), label: 'Venture configuration'),
        actions([button('Edit configuration', busy ? null : () => _configure(venture))]),
      ]),
    ], subtitle: 'Agents judge each day’s news; the gateway computes capital, revenue and the survival score.');
  }

  Component _draft(Map<String, dynamic> venture, Map<String, dynamic> draft) {
    final state = '${draft['state']}';
    final qualified = venture['state'] == 'qualified' || venture['qualified_at'] != null;
    Future<void> decide(Map<String, dynamic> body) => _submit('outreach', {'outreach_id': '${draft['id']}', ...body},
        ventureId: '${venture['id']}', organization: '${venture['organization_id']}');
    return disclosure('${draft['date']} · ${draft['persona']} · ${draft['language']} · ${draft['subject']}', [
      pill(state, tone: stateTone(state)),
      paragraph('Evidence: ${(draft['evidence'] as List? ?? const []).join(', ')} · score when drafted '
          '${draft['score'] ?? '—'}'),
      codeText('${draft['body']}', label: 'Draft body'),
      if (draft['notebook_path'] != null) paragraph('Saved in the notebook at ${draft['notebook_path']}.'),
      actions([
        if (state == 'draft')
          button('Approve', busy || !qualified ? null : () => unawaited(decide({'decision': 'approve'})), small: true),
        if (state == 'draft' || state == 'ready')
          button('Reject', busy ? null : () => unawaited(decide({'decision': 'reject'})), small: true, variant: 'danger'),
        if (state == 'ready' || state == 'sent')
          for (final outcome in const ['sent', 'replied', 'meeting', 'declined'])
            button('Mark $outcome', busy ? null : () => unawaited(decide({'decision': 'outcome', 'outcome': outcome})),
                small: true),
      ]),
    ]);
  }

  Component _chart(String title, num? Function(Map<String, dynamic>) value, String color) {
    final points = <num>[];
    for (final tick in bloc.ticks.reversed) {
      final measured = value(tick);
      if (measured != null) points.add(measured);
    }
    if (points.length < 2) return paragraph('$title chart needs at least two ticks.');
    final low = points.reduce((a, b) => a < b ? a : b);
    final high = points.reduce((a, b) => a > b ? a : b);
    final range = high == low ? 1 : high - low;
    String coordinate(int index, num measured) {
      final x = points.length == 1 ? 160.0 : 16 + index * 288 / (points.length - 1);
      final y = 68 - (measured - low) / range * 52;
      return '${x.toStringAsFixed(1)},${y.toStringAsFixed(1)}';
    }
    final coords = [
      for (var index = 0; index < points.length; index++) coordinate(index, points[index]),
    ].join(' ');
    return el('figure', [
      Component.element(
        tag: 'svg',
        attributes: {'viewBox': '0 0 320 80', 'role': 'img', 'aria-label': '$title history'},
        children: [
          Component.element(
            tag: 'polyline',
            // CSS variables only resolve in style, not in SVG presentation
            // attributes; using theme tokens keeps both themes legible.
            attributes: {'points': coords, 'fill': 'none', 'style': 'stroke: $color', 'stroke-width': '2',
                         'stroke-linejoin': 'round', 'stroke-linecap': 'round'},
          ),
        ],
      ),
      el('figcaption', [text('$title: ${points.last} latest, ${points.reduce((a, b) => a > b ? a : b)} high')]),
    ]);
  }

  Component _tick(Map<String, dynamic> tick) {
    final events = List<Map<String, dynamic>>.from(tick['events'] as List? ?? const []);
    final changes = List<Map<String, dynamic>>.from(tick['product_changes'] as List? ?? const []);
    final red = List<Map<String, dynamic>>.from(tick['red_team'] as List? ?? const []);
    final ledger = Map<String, dynamic>.from(tick['ledger'] as Map? ?? const {});
    return el('article', [
      el('h3', [text('${tick['simulated_date']} · ${tick['state']}')]),
      facts([
        ('Capital before', text(_money(ledger['capital_before'], ''))),
        ('Burn', text(_money(ledger['burn'], ''))),
        ('Revenue', text(_money(ledger['revenue'] ?? 0, ''))),
        ('Capital after', text(_money(ledger['capital_after'], ''))),
        ('Score', text(ledger['score'] == null ? '—' : '${ledger['score']}')),
        ('Runway', text(ledger['runway_days'] == null ? '—' : '${ledger['runway_days']} days')),
        if (ledger['landed_demand'] != null && ledger['landed_demand'] != 0)
          ('Demand landing today', text('${ledger['landed_demand']}')),
        if (ledger['fx'] is Map) ('FX', text(_fx(ledger['fx'] as Map))),
        if (ledger['score_breakdown'] is Map)
          ('Score parts', text(_breakdown(ledger['score_breakdown'] as Map))),
      ]),
      if (tick['error'] != null && '${tick['error']}'.isNotEmpty) notice(tick['error'], error: true),
      for (final event in events)
        paragraph('News: ${event['summary']} (${event['direction']})'),
      for (final change in changes)
        paragraph('Product: ${change['kind']} ${change['title']} — ${change['reason']}'),
      for (final item in red)
        paragraph('Red Team: ${item['objection']} (severity ${item['severity']})'),
      if (tick['quiet'] == true || (changes.isEmpty && events.isEmpty && tick['state'] == 'ready'))
        paragraph('Quiet day: ledger updated, no meeting started.'),
    ], classes: 'stack');
  }

  String _fx(Map fx) {
    final source = fx['source'] == 'ecb' ? 'ECB ${fx['date'] ?? ''}'.trim() : 'manual';
    final note = fx['fallback'] == true
        ? ' (live rate unavailable, manual fallback)'
        : fx['stale'] == true
            ? ' (cached; latest refresh failed)'
            : '';
    return '${fx['pair']} ${fx['rate']} · $source$note';
  }

  String _breakdown(Map parts) {
    const labels = {
      'runway': 'runway',
      'demand': 'demand',
      'competition': 'competition',
      'regulatory': 'regulation',
      'delivery_risk': 'delivery',
      'red_team_penalty': 'Red Team',
    };
    return [
      for (final entry in labels.entries)
        if (parts[entry.key] is num) '${entry.value} ${(parts[entry.key] as num).toStringAsFixed(1)}',
    ].join(' · ');
  }

  Component _limits() {
    final organizations = Map<String, dynamic>.from(bloc.limits['organizations'] as Map? ?? const {});
    if (organizations.isEmpty) return el('div', const []);
    final ceiling = bloc.limits['ceiling'] ?? 20;
    final rows = <List<Component>>[];
    for (final organization in bloc.organizations) {
      final entry = organizations['${organization['id']}'];
      if (entry is! Map) continue;
      final limit = '${entry['max_active_ventures'] ?? 3}';
      rows.add([
        text('${organization['name']}'),
        text('${entry['active'] ?? 0}'),
        text(limit),
        actions([
          button('Change limit', busy ? null : () => unawaited(_editLimit('${organization['id']}',
              '${organization['name']}', limit, ceiling)), small: true),
        ]),
      ]);
    }
    return card('Limits', [
      table(['Organization', 'Active ventures', 'Limit', 'Actions'], rows),
      if (bloc.data['fx'] is Map && '${(bloc.data['fx'] as Map)['error'] ?? ''}'.isNotEmpty)
        notice('Live FX: ${(bloc.data['fx'] as Map)['error']}', error: true),
    ], subtitle: 'Running, paused and qualified ventures count. Lowering a limit never stops a running venture.');
  }

  Component _moduleCard() {
    final fault = bloc.module['fault'];
    return card('Module', [
      if (fault is Map) notice('Last poll step failed: ${fault['error']}', error: true),
      actions([
        button('Switch off venture simulation', busy ? null : () => unawaited(_module(false)), variant: 'danger'),
      ]),
    ], subtitle: 'Installation-wide. Switching off stops ticks, schedules and feed requests; '
        'ventures and notebooks are kept.');
  }

  Future<void> _module(bool enabled) async {
    if (!enabled &&
        !await confirm('Switch off venture simulation?',
            'No ticks, schedules or feed requests run until it is switched on again. '
            'Ventures and notebooks are kept.')) {
      return;
    }
    await _submit('module', {'enabled': enabled});
  }

  Future<void> _editLimit(String organization, String name, String current, Object ceiling) async {
    final value = await showDialog<int>('Active venture limit', (close) => _LimitForm(
      name: name,
      current: current,
      ceiling: int.tryParse('$ceiling') ?? 20,
      onCancel: () => close(null),
      onSave: (value) => close(value),
    ));
    if (value == null || !mounted) return;
    await _submit('limits', {'max_active_ventures': value}, organization: organization);
  }

  Component _feeds() {
    final feeds = bloc.feeds;
    if (feeds.isEmpty) return card('Feeds', [paragraph('No feeds configured for this venture.')]);
    return card('Feeds', [
      table(['Source', 'Last fetch', 'Items', 'Status'], [
        for (final feed in feeds)
          [text('${feed['title']}' == '' ? '${feed['url']}' : '${feed['title']}'),
           text(displayTime(feed['last_fetch'])),
           text('${feed['item_count'] ?? 0}'),
           text('${feed['last_error']}' == '' ? 'OK' : '${feed['last_error']}'),
          ],
      ]),
    ], subtitle: 'Fetch health is shared per organization and capped at one request per source per hour.');
  }

  String _organizationName(String identity) {
    for (final organization in bloc.organizations) {
      if (organization['id'] == identity) return '${organization['name']}';
    }
    return identity;
  }

  String _money(Object? value, Object? currency) =>
      '${value ?? '—'}${'${currency ?? ''}'.isEmpty ? '' : ' ${currency}'}';

  Object? _ledger(Map<String, dynamic> venture, String key) {
    if (venture[key] != null) return venture[key];
    final tick = venture['last_tick'] as Map?;
    return (tick?['ledger'] as Map?)?[key];
  }

  String _score(Map<String, dynamic> venture) {
    final score = _ledger(venture, 'score');
    return score == null ? '—' : '$score';
  }

  String _runway(Map<String, dynamic> venture) {
    final runway = _ledger(venture, 'runway_days');
    if (runway != null) return '$runway days';
    return _ledger(venture, 'score') == null ? '—' : 'Self-funding';
  }

  String _lastTick(Map<String, dynamic> venture) {
    final tick = venture['last_tick'] as Map?;
    return tick == null ? 'Never' : '${tick['simulated_date']} (${tick['state']})';
  }

  Future<void> _submit(String mode, Map<String, dynamic> body, {String? ventureId, String? organization}) async {
    final epoch = connection.generation;
    try {
      await bloc.submit(mode, {
        if (organization != null) 'organization_id': organization,
        ...body,
      }, ventureId: ventureId);
    } catch (_) {
      if (mounted && valid(epoch)) setState(() {});
    }
  }

  Future<void> _run(Map<String, dynamic> venture) =>
      _submit('run', {}, ventureId: '${venture['id']}', organization: '${venture['organization_id']}');

  Future<void> _state(Map<String, dynamic> venture, String mode) =>
      _submit(mode, {}, ventureId: '${venture['id']}', organization: '${venture['organization_id']}');

  Future<void> _recover(Map<String, dynamic> venture) async {
    Map<String, dynamic>? tick;
    for (final item in bloc.ticks) {
      if (item['state'] == 'needs_attention') {
        tick = item;
        break;
      }
    }
    if (tick == null) return;
    await _submit('recover', {'inspected': true, 'simulated_date': '${tick['simulated_date']}'},
        ventureId: '${venture['id']}', organization: '${venture['organization_id']}');
  }

  Future<void> _archive(Map<String, dynamic> venture) async {
    if (!await confirm('Archive venture?', 'The venture history and notebook remain, but no further ticks can run.')) return;
    await _state(venture, 'archive');
  }

  Future<void> _team(Map<String, dynamic> venture) async {
    final value = await showDialog<Map<String, dynamic>>('Create the venture team', (close) => _TeamForm(
      agents: bloc.agents,
      onCancel: () => close(null),
      onSave: (body) => close(body),
    ));
    if (value == null || !mounted) return;
    await _submit('team', value, ventureId: '${venture['id']}',
        organization: '${venture['organization_id']}');
  }

  Future<void> _restorePromotion(Map<String, dynamic> venture) async {
    if (!await confirm('Restore previous discovery?',
        'Product Discovery will be returned to the repository and brief saved before this venture was promoted. '
        'The current discovery settings are replaced.')) return;
    await _submit('restore_promotion', {'confirmed': true}, ventureId: '${venture['id']}',
        organization: '${venture['organization_id']}');
  }

  Future<void> _promote(Map<String, dynamic> venture) async {
    if (!await confirm('Promote to discovery?',
        'Product Discovery will use this notebook repository and its venture brief. This replaces the '
        'organization\'s current discovery repository and brief (the previous values are kept on the venture). '
        'Configure discovery focus and a read-only group first; an individual worktree agent must be assigned to '
        'the notebook.')) return;
    await _submit('promote', {'replace_discovery': true}, ventureId: '${venture['id']}',
        organization: '${venture['organization_id']}');
  }

  Future<void> _openCreate() async {
    final value = await showDialog<Map<String, dynamic>>('Create venture', (close) => _VentureForm(
      organizations: bloc.organizations,
      groups: bloc.groups,
      choices: bloc.choices,
      onCancel: () => close(null),
      onSave: (body) => close(body),
    ));
    if (value == null || !mounted) return;
    await _submit('create', value);
  }

  Future<void> _configure(Map<String, dynamic> venture) async {
    final value = await showDialog<Map<String, dynamic>>('Configure venture', (close) => _VentureForm(
      organizations: bloc.organizations,
      groups: bloc.groups,
      choices: bloc.choices,
      initial: venture,
      fixedOrganization: '${venture['organization_id']}',
      onCancel: () => close(null),
      onSave: (body) => close(body),
    ));
    if (value == null || !mounted) return;
    await _submit('configure', value, ventureId: '${venture['id']}');
  }
}

/// The form's reference price: empty means 0 ("use the monthly costs"),
/// anything else must be a whole number of 0 or more. Returns null for
/// invalid input, so a typo is reported instead of silently becoming 0.
int? parseReferencePrice(String value) {
  final text = value.trim();
  if (text.isEmpty) return 0;
  final parsed = int.tryParse(text);
  return parsed == null || parsed < 0 ? null : parsed;
}

class _LimitForm extends StatefulComponent {
  const _LimitForm({
    required this.name,
    required this.current,
    required this.ceiling,
    required this.onSave,
    required this.onCancel,
  });

  final String name;
  final String current;
  final int ceiling;
  final void Function(int) onSave;
  final void Function() onCancel;

  @override
  State<_LimitForm> createState() => _LimitFormState();
}

class _LimitFormState extends State<_LimitForm> {
  late String _value = component.current;
  String? _error;

  void _save() {
    final value = int.tryParse(_value.trim());
    if (value == null || value < 1 || value > component.ceiling) {
      setState(() => _error = 'Enter a whole number from 1 to ${component.ceiling}.');
      return;
    }
    component.onSave(value);
  }

  @override
  Component build(BuildContext context) => stack([
        if (_error != null) notice(_error, error: true),
        paragraph('Maximum running, paused and qualified ventures for ${component.name}.'),
        field('Active venture limit', _value, (value) => setState(() => _value = value), required: true),
        actions([
          button('Cancel', component.onCancel),
          button('Save limit', _save, primary: true),
        ]),
      ]);
}

class _VentureForm extends StatefulComponent {
  const _VentureForm({
    required this.organizations,
    required this.groups,
    required this.choices,
    required this.onSave,
    required this.onCancel,
    this.initial,
    this.fixedOrganization,
  });

  final List<Map<String, dynamic>> organizations;
  final List<Map<String, dynamic>> groups;
  final Map<String, String> Function(String) choices;
  final Map<String, dynamic>? initial;
  final String? fixedOrganization;
  final void Function(Map<String, dynamic>) onSave;
  final void Function() onCancel;

  @override
  State<_VentureForm> createState() => _VentureFormState();
}

class _VentureFormState extends State<_VentureForm> {
  late String _organization;
  late String _name;
  late String _thesis;
  late String _product;
  late String _pricing;
  late String _group;
  late String _feeds;
  late String _tags;
  late String _keywords;
  late String _keepTerms;
  late bool _translation;
  late String _geography;
  late String _salesCycle;
  late String _legacyBias;
  late String _currency;
  late String _fxPair;
  late String _fxRate;
  late String _fxSource;
  late bool _dailyTicks;
  late String _tickHour;
  late Map<String, String> _roleOverrides;
  late String _regulatory;
  late String _capital;
  late List<Map<String, String>> _burnLines;
  late String _architecture;
  late String _icpSize;
  late String _icpSector;
  late String _redTeam;
  late String _survival;
  late String _consecutive;
  late String _referencePrice;
  late String _stage;
  late String _tone;
  late String _language;
  late String _persona;
  String? _error;

  @override
  void initState() {
    super.initState();
    final initial = component.initial;
    final config = initial?['configuration'] as Map? ?? {};
    final sources = Map<String, dynamic>.from(config['sources'] as Map? ?? {});
    final market = Map<String, dynamic>.from(config['market'] as Map? ?? {});
    final simulation = Map<String, dynamic>.from(config['simulation'] as Map? ?? {});
    final outreach = Map<String, dynamic>.from(config['outreach'] as Map? ?? {});
    _organization = component.fixedOrganization ??
        (component.organizations.isEmpty ? '' : '${component.organizations.first['id']}');
    _name = '${initial?['name'] ?? ''}';
    _thesis = '${initial?['thesis'] ?? ''}';
    _product = '${initial?['product'] ?? ''}';
    _pricing = '${initial?['pricing'] ?? ''}';
    _group = '${initial?['group_id'] ?? ''}';
    _feeds = List<String>.from(sources['feeds'] as List? ?? const []).join('\n');
    _tags = List<String>.from(sources['tags'] as List? ?? const []).join(', ');
    _keywords = List<String>.from(sources['keywords'] as List? ?? const []).join(', ');
    _keepTerms = List<String>.from(sources['keep_terms'] as List? ?? const []).join(', ');
    _translation = sources['translation'] == true;
    _geography = '${market['geography'] ?? ''}';
    _salesCycle = '${market['sales_cycle'] ?? 3}';
    _legacyBias = '${market['legacy_bias'] ?? 3}';
    _currency = '${market['currency'] ?? 'USD'}';
    _fxPair = '${market['fx_pair'] ?? 'USD/USD'}';
    _fxRate = '${market['fx_rate'] ?? 1}';
    _fxSource = '${market['fx_source'] ?? 'manual'}';
    _dailyTicks = simulation['daily_ticks'] == true;
    _tickHour = '${simulation['tick_hour_utc'] ?? 6}';
    _roleOverrides = {
      for (final entry in (initial?['role_overrides'] as Map? ?? const {}).entries) '${entry.key}': '${entry.value}',
    };
    _regulatory = '${market['regulatory'] ?? ''}';
    _capital = '${simulation['capital'] ?? 100000}';
    final burn = List<Map<String, dynamic>>.from(simulation['burn'] as List? ?? const []);
    _burnLines = burn.isEmpty
        ? [
            {'label': 'monthly burn', 'amount': '0', 'currency': _currency},
          ]
        : [
            for (final line in burn)
              {
                'label': '${line['label'] ?? ''}',
                'amount': '${line['amount'] ?? 0}',
                'currency': '${line['currency'] ?? _currency}',
              },
          ];
    _architecture = '${simulation['architecture'] ?? 'saas'}';
    _icpSize = '${simulation['icp_size'] ?? ''}';
    _icpSector = '${simulation['icp_sector'] ?? ''}';
    _redTeam = '${simulation['red_team'] ?? 5}';
    _survival = '${simulation['survival_threshold'] ?? 60}';
    _consecutive = '${simulation['consecutive_days'] ?? 3}';
    _referencePrice = '${simulation['reference_price'] ?? 0}';
    _stage = '${outreach['stage'] ?? 'survey'}';
    _tone = '${outreach['tone'] ?? 'direct'}';
    _language = '${outreach['language'] ?? 'en'}';
    _persona = '${outreach['persona'] ?? 'cto'}';
  }

  /// The gateway's own list when it sent one, so a new choice needs no page change.
  Map<String, String> _choices(String key, Map<String, String> fallback) {
    final served = component.choices(key);
    return served.isEmpty ? fallback : served;
  }

  Iterable<Map<String, dynamic>> get _organizationGroups =>
      component.groups.where((group) => group['organization_id'] == _organization);

  Map<String, String> get _members {
    for (final group in component.groups) {
      if (group['id'] == _group) {
        return {
          for (final entry in (group['member_names'] as Map? ?? const {}).entries) '${entry.key}': '${entry.value}',
        };
      }
    }
    return const {};
  }

  void _save() {
    final capital = int.tryParse(_capital);
    final redTeam = int.tryParse(_redTeam);
    final survival = int.tryParse(_survival);
    final consecutive = int.tryParse(_consecutive);
    final fxRate = num.tryParse(_fxRate);
    final referencePrice = parseReferencePrice(_referencePrice);
    if (referencePrice == null) {
      setState(() => _error = 'Reference price must be a whole number of 0 or more (0 uses the monthly costs).');
      return;
    }
    final burnLines = <Map<String, dynamic>>[];
    for (final line in _burnLines) {
      final amount = int.tryParse(line['amount'] ?? '');
      if ((line['label'] ?? '').trim().isEmpty || amount == null ||
          (line['currency'] ?? '').trim().length != 3) {
        setState(() => _error = 'Each monthly cost needs a label, whole-number amount and three-letter currency.');
        return;
      }
      burnLines.add({
        'label': line['label']!.trim(),
        'amount': amount,
        'currency': line['currency']!.trim().toUpperCase(),
      });
    }
    if (_name.trim().isEmpty || capital == null || redTeam == null ||
        survival == null || consecutive == null || fxRate == null) {
      setState(() => _error = 'Enter a name, whole-number capital, an FX rate and valid simulation limits.');
      return;
    }
    final feeds = _feeds.split(RegExp(r'[\r\n]+')).map((item) => item.trim()).where((item) => item.isNotEmpty).toList();
    final tags = _tags.split(',').map((item) => item.trim()).where((item) => item.isNotEmpty).toList();
    final keywords = _keywords.split(',').map((item) => item.trim()).where((item) => item.isNotEmpty).toList();
    final keepTerms = _keepTerms.split(',').map((item) => item.trim()).where((item) => item.isNotEmpty).toList();
    component.onSave({
      'organization_id': _organization,
      'name': _name.trim(),
      'thesis': _thesis.trim(),
      'product': _product.trim(),
      'pricing': _pricing.trim(),
      if (_group.isNotEmpty) 'group_id': _group,
      'role_overrides': {
        for (final entry in _roleOverrides.entries)
          if (entry.value.isNotEmpty && entry.value != 'auto') entry.key: entry.value,
      },
      'configuration': {
        'sources': {
          'feeds': feeds,
          'tags': tags,
          'keywords': keywords,
          'translation': _translation,
          'keep_terms': keepTerms,
        },
        'market': {
          'geography': _geography.trim(),
          'currency': _currency.trim().toUpperCase(),
          'fx_pair': _fxPair.trim().toUpperCase(),
          'fx_rate': fxRate,
          'fx_source': _fxSource,
          'sales_cycle': int.tryParse(_salesCycle) ?? 3,
          'legacy_bias': int.tryParse(_legacyBias) ?? 3,
          'regulatory': _regulatory.trim(),
        },
        'simulation': {
          'capital': capital,
          'burn': burnLines,
          'daily_ticks': _dailyTicks,
          'tick_hour_utc': int.tryParse(_tickHour) ?? 6,
          'architecture': _architecture,
          'icp_size': _icpSize.trim(),
          'icp_sector': _icpSector.trim(),
          'red_team': redTeam,
          'survival_threshold': survival,
          'consecutive_days': consecutive,
          'reference_price': referencePrice,
        },
        'outreach': {
          'stage': _stage,
          'tone': _tone.trim(),
          'language': _language.trim().toLowerCase(),
          'persona': _persona,
        },
      },
    });
  }

  @override
  Component build(BuildContext context) => stack([
        if (_error != null) notice(_error, error: true),
        field('Name', _name, (value) => setState(() => _name = value), required: true),
        textarea('Thesis', _thesis, (value) => setState(() => _thesis = value), rows: 2),
        textarea('Product summary', _product, (value) => setState(() => _product = value), rows: 2),
        field('Pricing summary', _pricing, (value) => setState(() => _pricing = value)),
        if (component.fixedOrganization == null)
          select('Organization', _organization, {
            for (final item in component.organizations) '${item['id']}': '${item['name']}',
          }, (value) => setState(() {
            _organization = value;
            _group = '';
          }), disabled: false),
        select('Venture group (optional)', _group, {
          '': 'No group yet',
          for (final group in _organizationGroups) '${group['id']}': '${group['name']}',
        }, (value) => setState(() {
          _group = value;
          _roleOverrides = {};
        }), disabled: false),
        if (_members.isNotEmpty)
          disclosure('Agent roles', [
            paragraph('Auto assigns by agent name (analyst, founder, red team, outreach), then spreads the rest.'),
            for (final member in _members.entries)
              select(member.value, _roleOverrides[member.key] ?? 'auto', {
                'auto': 'Auto',
                ..._choices('roles', const {
                  'analyst': 'Analyst',
                  'founder': 'Founder',
                  'red_team': 'Red Team',
                  'outreach': 'Outreach writer',
                }),
              }, (value) => setState(() => _roleOverrides[member.key] = value)),
          ]),
        disclosure('Sources', [
          textarea('Feed URLs (one per line, https only)', _feeds, (value) => setState(() => _feeds = value)),
          paragraph('An item is kept when it has an allowed tag or contains a trigger keyword. '
              'Leave both empty to keep everything.'),
          field('Allowed tags (comma separated)', _tags, (value) => setState(() => _tags = value)),
          field('Trigger keywords (comma separated)', _keywords, (value) => setState(() => _keywords = value)),
          field('Keep terms in original language (comma separated)', _keepTerms, (value) => setState(() => _keepTerms = value)),
          select('Translate headlines', _translation ? 'yes' : 'no', const {'no': 'No', 'yes': 'Yes'},
              (value) => setState(() => _translation = value == 'yes')),
        ]),
        disclosure('Market', [
          field('Geography', _geography, (value) => setState(() => _geography = value)),
          field('Base currency', _currency, (value) => setState(() => _currency = value)),
          field('FX pair (for example USD/JPY)', _fxPair, (value) => setState(() => _fxPair = value)),
          select('FX source', _fxSource, _choices('fx_sources', const {
            'manual': 'Manual rate',
            'ecb': 'Live ECB reference rate (manual rate is the fallback)',
          }), (value) => setState(() => _fxSource = value)),
          field(_fxSource == 'ecb' ? 'Fallback FX rate (1 left = rate right)' : 'FX rate (1 left = rate right)',
              _fxRate, (value) => setState(() => _fxRate = value)),
          select('Sales-cycle friction', _salesCycle, const {'1': '1', '2': '2', '3': '3', '4': '4', '5': '5'},
              (value) => setState(() => _salesCycle = value)),
          select('Legacy-workflow bias', _legacyBias, const {'1': '1', '2': '2', '3': '3', '4': '4', '5': '5'},
              (value) => setState(() => _legacyBias = value)),
          textarea('Regulatory baseline', _regulatory, (value) => setState(() => _regulatory = value), rows: 3),
        ]),
        disclosure('Simulation', [
          field('Starting capital', _capital, (value) => setState(() => _capital = value)),
          paragraph('Monthly costs (the gateway divides each by 30 for the daily burn).'),
          for (var index = 0; index < _burnLines.length; index++)
            el('div', [
              field('Cost label', _burnLines[index]['label']!, (value) => setState(() {
                _burnLines[index] = {..._burnLines[index], 'label': value};
              })),
              field('Monthly amount', _burnLines[index]['amount']!, (value) => setState(() {
                _burnLines[index] = {..._burnLines[index], 'amount': value};
              })),
              field('Currency', _burnLines[index]['currency']!, (value) => setState(() {
                _burnLines[index] = {..._burnLines[index], 'currency': value};
              })),
              if (_burnLines.length > 1)
                button('Remove cost line', () => setState(() => _burnLines.removeAt(index)), small: true,
                    variant: 'danger'),
            ], classes: 'stack'),
          actions([
            button('Add monthly cost', () => setState(() => _burnLines.add(
                {'label': '', 'amount': '0', 'currency': _currency})), small: true),
          ]),
          select('Daily ticks', _dailyTicks ? 'yes' : 'no', const {'no': 'Manual only', 'yes': 'Run every day'},
              (value) => setState(() => _dailyTicks = value == 'yes')),
          select('Tick hour (UTC)', _tickHour, {for (var hour = 0; hour < 24; hour++) '$hour': '$hour:00'},
              (value) => setState(() => _tickHour = value)),
          select('Architecture', _architecture,
              _choices('architectures', const {'saas': 'SaaS', 'api': 'API', 'chat_bot': 'Chat bot'}),
              (value) => setState(() => _architecture = value)),
          field('Ideal customer size', _icpSize, (value) => setState(() => _icpSize = value)),
          field('Ideal customer sector', _icpSector, (value) => setState(() => _icpSector = value)),
          field('Red Team strictness (1-10)', _redTeam, (value) => setState(() => _redTeam = value)),
          field('Survival threshold (0-100)', _survival, (value) => setState(() => _survival = value)),
          field('Consecutive qualifying days', _consecutive, (value) => setState(() => _consecutive = value)),
          paragraph('Only days with a meeting count, and each needs a paying customer. Quiet days neither '
              'count nor reset the run.'),
          field('Reference price per month (0 = the monthly costs)', _referencePrice,
              (value) => setState(() => _referencePrice = value)),
          paragraph('A price above the reference loses customers and cannot pass four times it.'),
        ]),
        disclosure('Outreach', [
          select('Stage', _stage,
              _choices('stages', const {'survey': 'Survey', 'waitlist': 'Waitlist', 'paid_pilot': 'Paid pilot'}),
              (value) => setState(() => _stage = value)),
          field('Tone', _tone, (value) => setState(() => _tone = value)),
          field('Language', _language, (value) => setState(() => _language = value)),
          select('Persona', _persona,
              _choices('personas', const {'cto': 'CTO', 'cfo': 'CFO', 'operations': 'Operations'}),
              (value) => setState(() => _persona = value)),
        ]),
        actions([
          button('Cancel', component.onCancel),
          button(component.initial == null ? 'Create venture' : 'Save configuration', _save, primary: true),
        ]),
      ]);
}

class _TeamForm extends StatefulComponent {
  const _TeamForm({required this.agents, required this.onSave, required this.onCancel});

  final List<Map<String, dynamic>> agents;
  final void Function(Map<String, dynamic>) onSave;
  final void Function() onCancel;

  @override
  State<_TeamForm> createState() => _TeamFormState();
}

class _TeamFormState extends State<_TeamForm> {
  static const roles = {
    'analyst': 'Analyst',
    'founder': 'Founder',
    'red_team': 'Red Team',
    'outreach': 'Outreach writer',
  };
  late Map<String, String> _templates;

  @override
  void initState() {
    super.initState();
    _templates = {for (final role in roles.keys) role: ''};
  }

  @override
  Component build(BuildContext context) {
    if (component.agents.isEmpty) {
      return stack([
        notice('Add an individual worktree agent to this organization first; the panel has no runtime or model to '
            'copy for the venture team.', error: true),
        actions([button('Close', component.onCancel)]),
      ]);
    }
    return stack([
      paragraph('Each role copies the selected individual agent\'s runtime, provider and model. The group\'s '
          'permission mode and accessible paths come from the Analyst template. The facilitator session starts now; '
          'the four members start when a meeting needs them.'),
      for (final entry in roles.entries)
        select(entry.value, _templates[entry.key]!, {
          '': 'First available agent',
          for (final agent in component.agents)
            '${agent['id']}': '${agent['name']} (${agent['runtime']}'
                '${'${agent['model'] ?? ''}'.isEmpty ? '' : '/' + '${agent['model']}'})',
        }, (value) => setState(() => _templates[entry.key] = value)),
      actions([
        button('Cancel', component.onCancel),
        button('Create team', () => component.onSave({
          'confirmed': true,
          'templates': {
            for (final entry in _templates.entries)
              if (entry.value.isNotEmpty) entry.key: entry.value,
          },
        }), primary: true),
      ]),
    ]);
  }
}
