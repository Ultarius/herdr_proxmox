import 'dart:async';
import 'dart:convert';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import 'ui.dart';
import 'services.dart';
import 'navigation.dart';
import 'repository_connections.dart';
import 'storage_panel.dart';
import 'package:herdr_dashboard_state/repository_connections.dart';
import 'package:herdr_dashboard_state/clone_jobs.dart';

String agentName(Map<String, dynamic> agent) =>
    '${agent['display_name'] ?? agent['name'] ?? agent['agent'] ?? agent['pane_id'] ?? 'Agent'}';
String agentStatus(Map<String, dynamic> agent) =>
    '${agent['agent_status'] ?? agent['state'] ?? agent['status'] ?? 'unknown'}';
String workspaceFor(Map<String, dynamic> agent) =>
    '${agent['workspace_id'] ?? '${agent['pane_id'] ?? ''}'.split(':').first}';
String activityFor(Map<String, dynamic> agent) =>
    switch (agent['agent_status'] ?? agent['state']) {
      'idle' || 'done' => 'idle',
      'working' || 'running' || 'busy' || 'thinking' => 'working',
      'blocked' => 'attention',
      _ => 'offline',
    };
String toneFor(String status) => switch (status) {
  'working' ||
  'running' ||
  'connected' ||
  'completed' ||
  'idle' ||
  'done' => 'success',
  'blocked' || 'attention' || 'failed' || 'error' => 'danger',
  'waiting' || 'queued' || 'pending' => 'warning',
  _ => 'neutral',
};

/// "3 agents", "1 workspace".
String _plural(int count, String noun) =>
    '$count $noun${count == 1 ? '' : 's'}';

/// hh:mm:ss of a snapshot or sample time.
String _clock(DateTime time) => time.toLocal().toString().substring(11, 19);

/// 16384 -> "16,384".
String _grouped(num value) => '${value.round()}'.replaceAllMapped(
  RegExp(r'\B(?=(\d{3})+(?!\d))'),
  (_) => ',',
);

/// The agent's organization role (for the role tile tint), when it has a profile.
String? _roleOf(Map<String, dynamic> agent) {
  final id = agent['profile_id'];
  if (id == null) return null;
  final profiles = List<Map<String, dynamic>>.from(
    dashboardConnection.organizationDirectory.value['profiles'] ?? const [],
  );
  final role = profiles.where((p) => p['id'] == id).firstOrNull?['role'];
  return role == null ? null : '$role';
}

/// Agent status pill: tone and dot shape follow the activity (WCAG 1.4.1).
Component _agentPill(Map<String, dynamic> agent) {
  final status = agentStatus(agent);
  final activity = activityFor(agent);
  final label = status.isEmpty
      ? status
      : '${status[0].toUpperCase()}${status.substring(1)}';
  return el(
    'span',
    [text(label)],
    classes: 'status',
    attributes: {
      'data-tone': switch (activity) {
        'working' => 'info',
        'idle' => 'success',
        'attention' => 'warning',
        _ => toneFor(status) == 'warning' ? 'warning' : 'neutral',
      },
      'data-shape': switch (activity) {
        'working' || 'idle' => 'filled',
        'attention' => 'diamond',
        'offline' => 'dash',
        _ => 'hollow',
      },
    },
  );
}

/// Template `.dot` (6px tone mark), decorative.
Component _dot(String tone, {String? shape}) => el(
  'span',
  [],
  classes: ['dot', if (shape != null) 'dot--$shape'].join(' '),
  attributes: {'data-tone': tone, 'aria-hidden': 'true'},
);

/// A text element with classes: `_text('ws-tile__name', label, 'h3')`.
Component _text(String classes, Object? value, [String tag = 'span']) =>
    el(tag, [text(value)], classes: classes);

/// Agent console route for an agent bound to an organization profile.
String _consoleHref(Map<String, dynamic> agent) =>
    '#/agents/${Uri.encodeComponent('${agent['profile_id']}')}';

/// Native disclosure in the template card look: tile, title, meta, chevron.
Component _disclosureSummary(
  String iconName,
  String title,
  String meta, {
  void Function()? onToggle,
}) => el(
  'summary',
  [
    el(
      'span',
      [icon(iconName)],
      classes: 'icon-tile icon-tile--sm',
      attributes: {'aria-hidden': 'true'},
    ),
    el('span', [
      _text('overview-disclosure__title', title),
      _text('overview-disclosure__meta', meta),
    ], classes: 'overview-disclosure__text'),
    icon('chevron-down', classes: 'disclosure-chevron'),
  ],
  classes: 'overview-disclosure__summary disclosure-summary',
  events: {
    if (onToggle != null)
      'click': (event) {
        event.preventDefault();
        onToggle();
      },
  },
);

class OverviewPage extends StatefulComponent {
  const OverviewPage({this.agentsOnly = false, super.key});
  final bool agentsOnly;
  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage> {
  String search = '';
  String filter = 'All';
  String label = '';
  String cwd = '/home/herdr/projects';
  bool creating = false;
  StreamSubscription? subscription;
  @override
  void initState() {
    super.initState();
    subscription = dashboardConnection.stream.listen((_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    subscription?.cancel();
    super.dispose();
  }

  Future<void> action(String name, Map<String, dynamic> body) =>
      dashboardConnection.send(DashboardCommand(name, body));
  Future<void> rename(Map<String, dynamic> workspace) async {
    final epoch = dashboardConnection.generation;
    var value = '${workspace['label'] ?? ''}';
    final selected = await showDialog<String>(
      'Rename workspace',
      (close) => el(
        'form',
        [
          field(
            'Workspace label',
            value,
            (next) => value = next,
            required: true,
          ),
          el('div', [
            button('Cancel', () => close(null)),
            dom.button(
              [text('Save')],
              type: dom.ButtonType.submit,
              classes: 'btn btn--primary',
            ),
          ], classes: 'actions'),
        ],
        events: {
          'submit': (event) {
            event.preventDefault();
            if (value.trim().isNotEmpty) close(value.trim());
          },
        },
      ),
    );
    if (mounted &&
        epoch == dashboardConnection.generation &&
        selected != null &&
        selected.trim().isNotEmpty)
      await action('rename', {
        'id': workspace['workspace_id'],
        'label': selected.trim(),
      });
  }

  void openProject() {
    setState(() => creating = true);
    scheduleMicrotask(
      () => web.document
          .getElementById('overview-project-form')
          ?.scrollIntoView(),
    );
  }

  /// Workspace label for an id, falling back to the id itself.
  String _workspaceLabel(String id) =>
      '${dashboardConnection.state.workspaces.where((w) => '${w['workspace_id']}' == id).firstOrNull?['label'] ?? id}';

  Component _runtime(DashboardState state, bool admin) {
    final running = state.serverStatus == 'running';
    final working = state.agents
        .where((a) => activityFor(a) == 'working')
        .length;
    return el(
      'section',
      [
        el(
          'span',
          [icon(running ? 'activity' : 'power')],
          classes: 'icon-tile runtime__tile',
          attributes: {
            'data-tone': running ? 'success' : 'neutral',
            'aria-hidden': 'true',
          },
        ),
        el('div', [
          el(
            'h2',
            [
              text(
                running
                    ? 'Herdr is running'
                    : state.serverStatus == 'stopped'
                    ? 'Herdr is stopped'
                    : 'Herdr: ${state.serverStatus}',
              ),
            ],
            classes: 'runtime__title',
            attributes: {'id': 'runtime-title', 'aria-live': 'polite'},
          ),
          _text(
            'runtime__meta',
            '$working of ${state.agents.length} agents working',
            'p',
          ),
        ], classes: 'runtime__text'),
        if (state.serverStatus == 'stopped' && admin)
          button(
            'Start Herdr',
            () => dashboardConnection.send(DashboardCommand('start-server')),
            disabled: state.busy,
            primary: true,
            iconName: 'play',
          ),
      ],
      classes: 'runtime',
      attributes: {
        'id': 'runtime',
        'data-state': running ? 'running' : 'stopped',
        'aria-labelledby': 'runtime-title',
      },
    );
  }

  /// One stat strip segment; a link when [href] is set (with a chevron).
  Component _stat({
    String? href,
    required String iconName,
    required String value,
    required String label,
    required String meta,
    String? tone,
    String? shape,
  }) {
    final children = [
      icon(iconName, classes: 'stat-strip__icon'),
      el('span', [
        _text('stat-strip__value', value),
        text(' '),
        _text('stat-strip__label', label),
        _text('visually-hidden', ', '),
        el(
          'span',
          [if (tone != null) _dot(tone, shape: shape), text(meta)],
          classes: 'stat-strip__meta',
          attributes: {'data-tone': ?tone},
        ),
      ], classes: 'stat-strip__text'),
      if (href != null)
        icon('chevron-right', classes: 'icon--sm stat-strip__chevron'),
    ];
    return el('li', [
      href == null
          ? el('div', children, classes: 'stat-strip__item')
          : el(
              'a',
              children,
              classes: 'stat-strip__item',
              attributes: {'href': href},
            ),
    ]);
  }

  Component _stats(DashboardState state, int working, int attention) {
    final total = state.agents.length;
    final idle = state.agents.where((a) => activityFor(a) == 'idle').length;
    final ids = {for (final w in state.workspaces) '${w['workspace_id']}'};
    final assigned = state.agents
        .where((a) => ids.contains(workspaceFor(a)))
        .length;
    final workspaces = state.workspaces.length;
    return el('div', [
      el(
        'ul',
        [
          _stat(
            href: '#/agents',
            iconName: 'bot',
            value: total == 0 ? '0' : '$working/$total',
            label: total == 0 ? 'agents' : 'agents working',
            meta: total == 0 ? 'No agents yet' : '$idle idle',
          ),
          _stat(
            href: '#/agents',
            iconName: 'triangle-alert',
            value: '$attention',
            label: attention == 1 ? 'needs attention' : 'need attention',
            meta: attention == 0
                ? 'No blocked agents'
                : '${_plural(attention, 'agent')} blocked',
            tone: attention > 0 ? 'danger' : null,
            shape: 'diamond',
          ),
          _stat(
            href: '#/explorer',
            iconName: 'folder',
            value: '$workspaces',
            label: workspaces == 1 ? 'workspace' : 'workspaces',
            meta: workspaces == 0
                ? 'Create one to get started'
                : '${_plural(assigned, 'agent')} assigned',
          ),
        ],
        classes: 'stat-strip',
        attributes: {'role': 'list', 'aria-label': 'Summary'},
      ),
    ], classes: 'stat-strip-wrap overview-stats');
  }

  /// "Needs attention": blocked agents, each linked to its console.
  /// Always in the tree (hidden when empty) so the stateful cards below keep
  /// their place.
  Component _attention(List<Map<String, dynamic>> blocked) => el(
    'section',
    [
      el('div', [
        el(
          'h2',
          [
            icon('bell'),
            text('Needs attention'),
            _text('count', '${blocked.length}'),
          ],
          classes: 'card__title',
          attributes: {'id': 'attention-title'},
        ),
      ], classes: 'card__header'),
      el(
        'ul',
        [
          for (final agent in blocked)
            el(
              'li',
              [
                el('span', [
                  icon('triangle-alert'),
                ], classes: 'attention__icon'),
                el('p', [
                  el('strong', [text(agentName(agent))]),
                  text(' is blocked'),
                  if (workspaceFor(agent).isNotEmpty) ...[
                    text(' in '),
                    el('code', [text(_workspaceLabel(workspaceFor(agent)))]),
                  ],
                ], classes: 'attention__text'),
                if (agent['profile_id'] != null)
                  el(
                    'a',
                    [
                      text('Open console'),
                      _text('visually-hidden', ' for ${agentName(agent)}'),
                      icon('arrow-right', classes: 'icon--sm'),
                    ],
                    classes: 'attention__action link',
                    attributes: {'href': _consoleHref(agent)},
                  ),
              ],
              classes: 'attention__item',
              attributes: {'data-tone': 'danger'},
            ),
        ],
        classes: 'attention',
        attributes: {'role': 'list'},
      ),
    ],
    classes: 'card overview-attention',
    attributes: {
      'id': 'attention',
      'aria-labelledby': 'attention-title',
      if (blocked.isEmpty) 'hidden': '',
    },
  );

  Component _agentRow(Map<String, dynamic> agent) {
    final name = agentName(agent);
    final workspace = workspaceFor(agent);
    final runtime = '${agent['agent'] ?? agent['runtime'] ?? '—'}';
    return el('tr', [
      el(
        'th',
        [
          el('div', [
            roleTile(
              name,
              role: _roleOf(agent),
              live: activityFor(agent) == 'working',
            ),
            el('div', [
              if (agent['profile_id'] != null)
                el(
                  'a',
                  [text(name)],
                  classes: 'agent__name',
                  attributes: {'href': _consoleHref(agent)},
                )
              else
                _text('agent__name', name),
              el('span', [
                text(runtime),
                if (workspace.isNotEmpty) _text('agent__ws', ' · $workspace'),
              ], classes: 'agent__meta'),
            ], classes: 'agent__text'),
          ], classes: 'agent'),
        ],
        classes: 'agents-table__agent',
        attributes: {'scope': 'row'},
      ),
      el('td', [
        if (workspace.isEmpty)
          el('span', [
            el('span', [text('—')], attributes: {'aria-hidden': 'true'}),
            _text('visually-hidden', 'None'),
          ], classes: 'none')
        else
          el(
            'span',
            [text(workspace)],
            classes: 'mono-chip',
            attributes: {'title': _workspaceLabel(workspace)},
          ),
      ], classes: 'agents-table__workspace'),
      el('td', [_agentPill(agent)], classes: 'agents-table__status'),
    ]);
  }

  Component _agents() {
    final state = dashboardConnection.state;
    final agents = state.agents.where((a) {
      final status = agentStatus(a);
      final matches =
          filter == 'All' ||
          (filter == 'Working'
              ? ['working', 'running', 'busy'].contains(status)
              : filter == 'Needs attention'
              ? status == 'blocked'
              : ['idle', 'done', 'waiting'].contains(status));
      final needle = search.trim().toLowerCase();
      return matches &&
          (agentName(a).toLowerCase().contains(needle) ||
              workspaceFor(a).toLowerCase().contains(needle));
    }).toList();
    final filtered = search.trim().isNotEmpty || filter != 'All';
    return card(
      'Agents',
      [
        el('div', [
          el('div', [
            icon('search', classes: 'icon--sm search-field__icon'),
            field(
              'Search agents',
              search,
              (value) => setState(() => search = value),
              placeholder: 'Name or workspace',
            ),
          ], classes: 'search-field agents-search'),
          el(
            'div',
            [
              for (final value in ['All', 'Working', 'Idle', 'Needs attention'])
                el(
                  'button',
                  [icon('check', classes: 'chip__check'), text(value)],
                  classes: 'chip',
                  attributes: {
                    'type': 'button',
                    'aria-pressed': '${filter == value}',
                  },
                  events: {'click': (_) => setState(() => filter = value)},
                ),
            ],
            classes: 'chips',
            attributes: {
              'role': 'group',
              'aria-label': 'Filter agents by status',
            },
          ),
        ], classes: 'agents-toolbar'),
        el(
          'p',
          [text('Showing ${agents.length} of ${state.agents.length}')],
          classes: 'agents-result',
          attributes: {'aria-live': 'polite'},
        ),
        if (agents.isEmpty)
          emptyState(
            !filtered && state.agents.isEmpty
                ? (state.serverStatus == 'stopped'
                      ? 'No agents yet. Start Herdr to bring your team online.'
                      : 'No agents online yet.')
                : search.trim().isEmpty
                ? 'No agents match this view.'
                : 'No agents match "$search".',
            iconName: 'bot',
            actions: [
              if (search.isNotEmpty)
                button(
                  'Clear search',
                  () => setState(() => search = ''),
                  variant: 'ghost',
                  small: true,
                ),
            ],
          )
        else
          el(
            'div',
            [
              el('table', [
                _text(
                  'visually-hidden',
                  'Agents with their workspace and status',
                  'caption',
                ),
                el('thead', [
                  el('tr', [
                    for (final (heading, iconName, column) in [
                      ('Agent', 'bot', 'agent'),
                      ('Workspace', 'folder', 'workspace'),
                      ('Status', 'activity', 'status'),
                    ])
                      el(
                        'th',
                        [
                          el('span', [
                            icon(iconName),
                            text(heading),
                          ], classes: 'th'),
                        ],
                        classes: 'agents-table__$column',
                        attributes: {'scope': 'col'},
                      ),
                  ]),
                ]),
                el('tbody', [for (final agent in agents) _agentRow(agent)]),
              ], classes: 'table agents-table'),
            ],
            classes: 'table-wrap table-scroll agents-wrap',
            attributes: {'role': 'region', 'aria-label': 'Agents table'},
          ),
      ],
      iconName: 'bot',
      count: state.agents.length,
      classes: 'overview-grid__wide overview-agents',
      id: 'agents',
      actions: [
        button(
          'Manage',
          () => navigate('/organization'),
          variant: 'ghost',
          small: true,
        ),
      ],
    );
  }

  Component _workspaceTile(
    DashboardState state,
    Map<String, dynamic> workspace,
    bool admin,
  ) {
    final id = '${workspace['workspace_id']}';
    final members = state.agents.where((a) => workspaceFor(a) == id).toList();
    final working = members.where((a) => activityFor(a) == 'working').length;
    return el('article', [
      el('div', [
        _text(
          'ws-tile__name',
          workspace['label'] ?? workspace['workspace_id'],
          'h3',
        ),
        el(
          'span',
          [text(id)],
          classes: 'mono-chip',
          attributes: {'title': 'Workspace ID'},
        ),
      ], classes: 'ws-tile__head'),
      if (workspace['cwd'] != null)
        _text('ws-tile__path', workspace['cwd'], 'p'),
      el('div', [
        el('span', [
          if (members.isNotEmpty)
            el(
              'span',
              [
                for (final agent in members.take(5))
                  roleTile(agentName(agent), role: _roleOf(agent), size: 'xs'),
              ],
              classes: 'avatar-stack',
              attributes: {
                'role': 'img',
                'aria-label': 'Agents: ${members.map(agentName).join(', ')}',
              },
            ),
          _text('ws-tile__count', _plural(members.length, 'agent')),
          if (working > 0)
            el('span', [
              _text('visually-hidden', ', '),
              _dot('success'),
              text('$working working'),
            ], classes: 'ws-tile__working'),
        ], classes: 'ws-tile__agents'),
        if (admin)
          el('div', [
            button(
              'Focus',
              () => action('focus', {'id': workspace['workspace_id']}),
              disabled: state.busy,
              small: true,
              classes: 'btn--pill',
            ),
            button(
              'Rename',
              () => rename(workspace),
              disabled: state.busy,
              small: true,
              variant: 'ghost',
            ),
          ], classes: 'actions ws-tile__actions'),
      ], classes: 'ws-tile__foot'),
    ], classes: 'ws-tile workspace-tile');
  }

  Component _workspaces(DashboardState state, bool admin) => card(
    'Workspaces',
    [
      if (state.workspaces.isEmpty)
        emptyState(
          'No workspaces yet. Create a workspace to get started.',
          iconName: 'folder',
          actions: [
            if (admin)
              button(
                'Create workspace',
                openProject,
                primary: true,
                small: true,
                iconName: 'plus',
              ),
          ],
        )
      else
        el('div', [
          el(
            'ul',
            [
              for (final workspace in state.workspaces)
                el('li', [_workspaceTile(state, workspace, admin)]),
            ],
            classes: 'ws-grid',
            attributes: {'role': 'list'},
          ),
          if (admin)
            el(
              'button',
              [
                el(
                  'span',
                  [icon('plus')],
                  classes: 'icon-tile',
                  attributes: {'aria-hidden': 'true'},
                ),
                _text('ws-create__title', 'Create workspace'),
                _text(
                  'ws-create__meta',
                  'Use a project directory or clone a repository',
                ),
              ],
              classes: 'ws-create',
              attributes: {'type': 'button'},
              events: {'click': (_) => openProject()},
            ),
        ], classes: 'ws-section'),
    ],
    iconName: 'folder',
    count: state.workspaces.length,
    classes: 'overview-workspaces',
    id: 'workspaces',
    actions: [
      button(
        'View projects',
        () => navigate('/explorer'),
        variant: 'ghost',
        small: true,
      ),
    ],
  );

  Component _projectForm(DashboardState state) => disclosureElement(
    [
      _disclosureSummary(
        'folder-plus',
        'Add a project or workspace',
        'Create a workspace for a project directory, or clone a repository first',
        onToggle: () {
          if (mounted) setState(() => creating = !creating);
        },
      ),
      el(
        'div',
        [
          el('section', [
            _text('overview-disclosure__heading', 'Create a workspace', 'h3'),
            _text(
              'overview-disclosure__hint',
              'Name the workspace and point it at a project directory.',
              'p',
            ),
            el(
              'form',
              [
                field(
                  'Workspace label',
                  label,
                  (value) => label = value,
                  required: true,
                ),
                field(
                  'Project directory',
                  cwd,
                  (value) => cwd = value,
                  required: true,
                ),
                dom.button(
                  [icon('plus'), text('Create workspace')],
                  type: dom.ButtonType.submit,
                  disabled: state.busy,
                  classes: 'btn btn--primary',
                ),
              ],
              classes: 'overview-form',
              events: {
                'submit': (event) {
                  event.preventDefault();
                  if (!state.busy)
                    action('create', {
                      'label': label.trim(),
                      'cwd': cwd.trim(),
                    });
                },
              },
            ),
          ], classes: 'overview-disclosure__section'),
          CloneProjectCard(
            onCloned: (path) => setState(() => cwd = path),
            onDeleted: (path) {
              if (cwd == path) setState(() => cwd = '');
            },
          ),
        ],
        classes: 'overview-disclosure__body overview-disclosure__body--split',
      ),
    ],
    classes: 'card overview-disclosure',
    attributes: {'id': 'overview-project-form'},
    open: creating,
    events: {
      'toggle': (event) {
        if (!mounted || event.target != event.currentTarget) return;
        final value = (event.currentTarget as web.HTMLDetailsElement).open;
        if (value != creating) setState(() => creating = value);
      },
    },
  );

  /// "Tuesday, 13 October" without pulling in a date-formatting package.
  String today() {
    const weekdays = [
      'Monday',
      'Tuesday',
      'Wednesday',
      'Thursday',
      'Friday',
      'Saturday',
      'Sunday',
    ];
    const months = [
      'January',
      'February',
      'March',
      'April',
      'May',
      'June',
      'July',
      'August',
      'September',
      'October',
      'November',
      'December',
    ];
    final now = DateTime.now();
    return '${weekdays[now.weekday - 1]}, ${now.day} ${months[now.month - 1]}';
  }

  @override
  Component build(BuildContext context) {
    final state = dashboardConnection.state;
    final admin = dashboardConnection.operator.value['role'] == 'admin';
    final working = state.agents
        .where((a) => activityFor(a) == 'working')
        .length;
    final blocked = state.agents
        .where((a) => agentStatus(a) == 'blocked')
        .toList();
    final overview = !component.agentsOnly;
    return el('div', [
      pageHeader(
        overview ? 'Overview' : 'Agent activity',
        overline: today(),
        subtitle:
            '${_plural(state.agents.length, 'agent')} across '
            '${_plural(state.workspaces.length, 'workspace')}',
        actions: [
          if (overview && admin) ...[
            button('Add project', openProject, iconName: 'folder-plus'),
            button(
              'Create workspace',
              openProject,
              primary: true,
              iconName: 'plus',
            ),
          ],
        ],
      ),
      if (overview) ...[
        _runtime(state, admin),
        _stats(state, working, blocked.length),
        _attention(blocked),
      ],
      el('div', [
        el('div', [
          _agents(),
          if (overview) _workspaces(state, admin),
        ], classes: 'overview-lane overview-lane--primary'),
        el('div', [
          const SystemHealth(),
          if (overview) const RecentActivity(),
          el('details', [
            _disclosureSummary(
              'terminal',
              'Terminal access',
              'Connect to the container over SSH to run Herdr directly',
            ),
            el('div', [
              _text(
                'code-output',
                'ssh herdr@<container-ip>\nThen: cd ~/projects && herdr',
                'pre',
              ),
            ], classes: 'overview-disclosure__body'),
          ], classes: 'card overview-disclosure'),
        ], classes: 'overview-lane overview-lane--secondary'),
      ], classes: 'overview-grid'),
      if (overview && admin) _projectForm(state),
    ], classes: 'overview-page');
  }
}

class RecentActivity extends StatefulComponent {
  const RecentActivity({super.key});
  @override
  State<RecentActivity> createState() => _RecentActivityState();
}

class _RecentActivityState extends State<RecentActivity> {
  StreamSubscription? subscription;
  Map<String, String>? previous;

  /// [status] is null when the agent is no longer detected.
  final List<({String name, String? role, String? status, DateTime time})>
  events = [];
  @override
  void initState() {
    super.initState();
    observe(dashboardConnection.state);
    subscription = dashboardConnection.stream.listen((_) {
      observe(dashboardConnection.state);
    });
  }

  void observe(DashboardState state) {
    if (state.refreshed == null) return;
    final agents = {
      for (final a in state.agents) '${a['name'] ?? a['pane_id']}': a,
    };
    final current = {
      for (final entry in agents.entries)
        entry.key: '${agentName(entry.value)} · ${agentStatus(entry.value)}',
    };
    if (previous != null) {
      for (final entry in current.entries) {
        if (previous![entry.key] != entry.value) {
          final agent = agents[entry.key]!;
          events.insert(0, (
            name: agentName(agent),
            role: _roleOf(agent),
            status: agentStatus(agent),
            time: state.refreshed!,
          ));
        }
      }
      for (final key in previous!.keys.where(
        (key) => !current.containsKey(key),
      )) {
        events.insert(0, (
          name: previous![key]!.split(' · ').first,
          role: null,
          status: null,
          time: state.refreshed!,
        ));
      }
      if (events.length > 8) events.removeRange(8, events.length);
    }
    previous = current;
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    subscription?.cancel();
    super.dispose();
  }

  @override
  Component build(BuildContext context) => card(
    'Recent activity',
    [
      if (events.isEmpty)
        emptyState('No activity changes observed yet.', iconName: 'history')
      else
        el(
          'ol',
          [
            for (final event in events.take(6))
              el('li', [
                roleTile(event.name, role: event.role, size: 'sm'),
                el('div', [
                  el('p', [
                    el('strong', [text(event.name)]),
                    text(
                      event.status == null
                          ? ' is no longer detected'
                          : ' is now ${event.status}',
                    ),
                  ], classes: 'timeline__text'),
                  el('p', [
                    el(
                      'time',
                      [text(_clock(event.time))],
                      attributes: {
                        'datetime': event.time.toLocal().toIso8601String(),
                      },
                    ),
                    if (event.status == 'blocked') ...[
                      _dot('danger', shape: 'diamond'),
                      _text('timeline__flag', 'Needs attention'),
                    ],
                  ], classes: 'timeline__time'),
                ]),
              ], classes: 'timeline__item'),
          ],
          classes: 'timeline',
          attributes: {'role': 'list'},
        ),
    ],
    iconName: 'history',
    subtitle: 'Changes observed on this page',
    classes: 'overview-activity',
    id: 'activity',
  );
}

class SystemHealth extends StatefulComponent {
  const SystemHealth({super.key});
  @override
  State<SystemHealth> createState() => _SystemHealthState();
}

class _SystemHealthState extends State<SystemHealth> {
  Timer? timer;
  Map<String, dynamic>? data;
  bool loading = false;
  String? error;
  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 10), (_) => refresh());
  }

  Future<void> refresh() async {
    if (loading) return;
    setState(() => loading = true);
    final epoch = dashboardConnection.generation;
    try {
      final result = await dashboardConnection.request('resources');
      if (mounted && epoch == dashboardConnection.generation)
        setState(() {
          data = Map<String, dynamic>.from(result);
          error = null;
        });
      if (mounted && !(timer?.isActive ?? false))
        timer = Timer.periodic(const Duration(seconds: 10), (_) => refresh());
    } catch (_) {
      timer?.cancel();
      if (mounted && epoch == dashboardConnection.generation)
        setState(
          () => error =
              'Resource monitor unavailable; displayed values may be stale.',
        );
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  String mib(dynamic value) => _grouped((value as num? ?? 0) / 1048576);
  String bytes(dynamic value) => '${mib(value)} MiB';
  String percent(dynamic value) =>
      value == null ? 'Sampling…' : '${(value as num).toStringAsFixed(1)}%';
  double ratio(dynamic used, dynamic total) => (total as num? ?? 0) > 0
      ? ((used as num? ?? 0) / (total as num)).toDouble()
      : 0;

  /// Template meter row: icon + label + value, then a 6px track whose fill
  /// width is --meter-value; warning from 75%, danger from 90%.
  Component meter(
    String id,
    String iconName,
    String label,
    String value,
    double fraction,
    String valueText,
  ) {
    final percentage = fraction.clamp(0, 1) * 100;
    return el('div', [
      el('div', [
        icon(iconName),
        el('span', [text(label)], attributes: {'id': id}),
        _text('meter-row__value', value),
      ], classes: 'meter-row__label'),
      el(
        'div',
        [el('span', [], classes: 'meter__fill')],
        classes: 'meter',
        attributes: {
          'role': 'meter',
          'aria-labelledby': id,
          'aria-valuemin': '0',
          'aria-valuemax': '100',
          'aria-valuenow': percentage.toStringAsFixed(0),
          'aria-valuetext': valueText,
          'style': '--meter-value: ${percentage.toStringAsFixed(1)}%',
          if (percentage >= 90)
            'data-level': 'danger'
          else if (percentage >= 75)
            'data-level': 'warning',
        },
      ),
    ], classes: 'meter-row');
  }

  /// Memory-style value: "6,348 / 16,384 MiB".
  Component usage(String id, String iconName, String label, String key) {
    final used = data!['${key}_used'];
    final total = data!['${key}_total'];
    final fraction = ratio(used, total);
    return meter(
      id,
      iconName,
      label,
      '${mib(used)} / ${mib(total)} MiB',
      fraction,
      '${mib(used)} of ${mib(total)} MiB (${(fraction * 100).round()}%)',
    );
  }

  Component processes(List processes) => el('details', [
    el('summary', [
      el('span', [
        _text('processes__title', 'Processes by memory usage'),
        _text(
          'processes__meta',
          'Top 20 · process CPU uses 100% per core · RSS may include shared memory',
        ),
      ], classes: 'processes__text'),
      icon('chevron-down', classes: 'disclosure-chevron'),
    ], classes: 'processes__summary disclosure-summary'),
    if (processes.isEmpty)
      _text('processes__empty', 'No processes in this sample.', 'p')
    else
      el('div', [
        el('table', [
          _text('visually-hidden', 'Top processes by memory usage', 'caption'),
          el('thead', [
            el('tr', [
              el('th', [text('Process')], attributes: {'scope': 'col'}),
              for (final heading in ['PID', 'Memory', 'CPU'])
                el(
                  'th',
                  [text(heading)],
                  classes: 'num',
                  attributes: {'scope': 'col'},
                ),
            ]),
          ]),
          el('tbody', [
            for (final p in processes)
              el('tr', [
                el(
                  'td',
                  [text(p['name'])],
                  attributes: {'title': '${p['name']}'},
                ),
                _text('num', p['pid'], 'td'),
                _text('num', bytes(p['memory_bytes']), 'td'),
                _text('num', percent(p['cpu_percent']), 'td'),
              ]),
          ]),
        ], classes: 'table table--compact'),
      ], classes: 'table-wrap'),
  ], classes: 'processes');

  @override
  Component build(BuildContext context) {
    final d = data;
    return card(
      'System health',
      [
        if (error != null) notice(error, error: true),
        if (d == null && error == null)
          _text('health__sampled', 'Loading resource usage…', 'p')
        else if (d != null && d['available'] != true)
          emptyState('Container metrics unavailable.', iconName: 'cpu')
        else if (d != null) ...[
          el('div', [
            el('p', [
              text('Sampled '),
              el('time', [
                text(
                  DateTime.fromMillisecondsSinceEpoch(
                    ((d['sampled_at'] as num) * 1000).round(),
                  ).toLocal().toString().substring(0, 19),
                ),
              ]),
            ], classes: 'health__sampled'),
            el('div', [
              meter(
                'health-cpu',
                'cpu',
                'CPU',
                '${percent(d['cpu_percent'])} · ${d['cpu_cores']} cores',
                (d['cpu_percent'] as num? ?? 0) / 100,
                d['cpu_percent'] == null
                    ? 'Sampling'
                    : '${percent(d['cpu_percent'])} across ${d['cpu_cores']} cores',
              ),
              usage('health-memory', 'memory-stick', 'Memory', 'memory'),
              usage('health-swap', 'hard-drive', 'Swap', 'swap'),
              if (d['storage'] case final Map disk)
                if (disk['available'] == true)
                  meter(
                    'health-disk',
                    'hard-drive',
                    'Disk',
                    '${mib(disk['free_bytes'])} MiB available',
                    ratio(disk['used_bytes'], disk['total_bytes']),
                    '${mib(disk['free_bytes'])} MiB available on the projects filesystem',
                  ),
            ], classes: 'meters'),
            if (d['storage'] case final Map disk)
              if (disk['blocked'] == true)
                notice(
                  'Storage needs attention: ${disk['reason']}',
                  error: true,
                ),
            if (d['build_capacity'] case final Map capacity) ...[
              el('p', [
                text(
                  'Heavy builds: ${capacity['active'] == null ? 'idle' : 'running'}'
                  ' · ${capacity['waiting'] ?? 0} waiting'
                  ' · up to ${capacity['compiler_jobs'] ?? 1} compiler jobs per build',
                ),
              ], classes: 'health__sampled'),
              if ('${capacity['waiting_reason'] ?? ''}'.isNotEmpty)
                notice('${capacity['waiting_reason']} New builds will wait.'),
            ],
            if ((d['memory_total'] as num) > 0 &&
                (d['memory_used'] as num) / (d['memory_total'] as num) >= .9)
              notice(
                'High memory usage — avoid launching more agents.',
                error: true,
              ),
          ], classes: 'health'),
          processes(d['processes'] as List? ?? []),
        ],
        const StoragePanel(key: ValueKey('managed-storage')),
      ],
      iconName: 'cpu',
      classes: 'overview-grid__wide overview-health',
      id: 'health',
      actions: [
        button(
          'Refresh resources',
          refresh,
          disabled: loading,
          variant: 'ghost',
          small: true,
          iconName: 'refresh-cw',
        ),
      ],
    );
  }
}

class CloneProjectCard extends StatefulComponent {
  const CloneProjectCard({required this.onCloned, this.onDeleted, super.key});
  final void Function(String) onCloned;
  final void Function(String)? onDeleted;
  @override
  State<CloneProjectCard> createState() => _CloneProjectCardState();
}

class _CloneProjectCardState extends State<CloneProjectCard> {
  String url = '';
  String folder = '';
  String connectionId = '';
  String? unavailableConnection;
  List<Map<String, dynamic>> connections = [];
  bool busy = false;
  Timer? timer;
  List<Map<String, dynamic>> jobs = [];
  String? completed;
  bool polling = false;
  String? result;
  String? pendingRemoval, removing;
  bool deleteCloneFolder = false;
  String removalConfirmation = '';
  int jobsRevision = 0;
  bool jobsLoaded = false;
  int? removalGeneration;
  late final DashboardBloc cloneConnection;
  StreamSubscription? cloneSubscription;
  late int cloneGeneration;
  late bool cloneCanRemove;
  @override
  void initState() {
    super.initState();
    cloneConnection = dashboardConnection;
    cloneGeneration = cloneConnection.generation;
    cloneCanRemove = canRemove;
    cloneConnection.operator.addListener(cloneSessionChanged);
    cloneSubscription = cloneConnection.stream.listen(
      (_) => cloneSessionChanged(),
    );
    refreshJobs();
    timer = Timer.periodic(const Duration(seconds: 3), (_) => refreshJobs());
  }

  void cloneSessionChanged() {
    if (!mounted) return;
    if (cloneGeneration == cloneConnection.generation &&
        cloneCanRemove == canRemove)
      return;
    setState(() {
      cloneCanRemove = canRemove;
      if (cloneGeneration != cloneConnection.generation) {
        cloneGeneration = cloneConnection.generation;
        jobsRevision++;
        jobs = [];
        jobsLoaded = false;
        completed = null;
        busy = false;
        removing = null;
        result = null;
      }
      if (!canRemove || removalGeneration != cloneGeneration)
        pendingRemoval = null;
    });
  }

  Future<void> refreshJobs() async {
    final connection = dashboardConnection;
    if (polling || removing != null || !connection.state.connected) return;
    polling = true;
    final epoch = connection.generation;
    final revision = jobsRevision;
    try {
      final data = await connection.request('projects/jobs');
      if (!mounted ||
          epoch != connection.generation ||
          revision != jobsRevision)
        return;
      final firstLoad = !jobsLoaded;
      final previousStates = {for (final job in jobs) job['id']: job['state']};
      jobsLoaded = true;
      final next = List<Map<String, dynamic>>.from(data['jobs'] ?? []);
      if (jsonEncode(next) != jsonEncode(jobs)) setState(() => jobs = next);
      final latest = jobs.firstOrNull;
      if (latest != null &&
          ['failed', 'interrupted', 'completed'].contains(latest['state']) &&
          result?.startsWith('Clone queued.') == true) {
        setState(() => result = null);
      }
      if (latest?['state'] == 'completed' &&
          latest?['id'] != completed &&
          (firstLoad ||
              ['queued', 'running'].contains(previousStates[latest?['id']]))) {
        completed = latest!['id'] as String;
        component.onCloned(latest['cwd'] as String);
      }
    } catch (exception) {
      if (mounted && epoch == connection.generation && revision == jobsRevision)
        setState(
          () => result =
              'Clone status unavailable; the server job may still be running. $exception',
        );
    } finally {
      polling = false;
    }
  }

  Future<void> clone() async {
    if (unavailableConnection != null ||
        !knownRepositoryConnection(connections, connectionId)) {
      setState(
        () => result =
            'The saved repository connection is unavailable. Refresh connections and select one before cloning.',
      );
      return;
    }
    final connection = dashboardConnection;
    final epoch = connection.generation;
    setState(() {
      busy = true;
      result = null;
    });
    try {
      final data = await connection.request('projects/clone', {
        'url': url.trim(),
        'folder': folder.trim(),
        if (connectionId.isNotEmpty) 'connection_id': connectionId,
      });
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        jobs.insert(0, Map<String, dynamic>.from(data));
        result =
            'Clone queued. You can navigate away and return to see its status.';
      });
      await refreshJobs();
    } catch (error) {
      if (mounted && epoch == connection.generation)
        setState(() => result = error.toString());
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() => busy = false);
    }
  }

  bool get canRemove =>
      dashboardConnection.state.connected &&
      dashboardConnection.operator.value['role'] == 'admin';

  void prepareRemoval(Map<String, dynamic> job) {
    if (busy || !canRemove) return;
    setState(() {
      pendingRemoval = '${job['id']}';
      removalGeneration = dashboardConnection.generation;
      deleteCloneFolder = false;
      removalConfirmation = '';
    });
    context.binding.addPostFrameCallback(() {
      if (!mounted) return;
      (web.document.querySelector(
                '#clone-removal-$pendingRemoval input:not(:disabled), #clone-removal-$pendingRemoval .clone-removal__confirm',
              )
              as web.HTMLElement?)
          ?.focus();
    });
  }

  Future<void> removeClone(Map<String, dynamic> job) async {
    if (busy ||
        !canRemove ||
        removalGeneration != dashboardConnection.generation ||
        pendingRemoval != job['id'] ||
        (deleteCloneFolder && !cloneFolderIdentityAvailable(job)) ||
        (deleteCloneFolder && removalConfirmation != job['folder']))
      return;
    final connection = dashboardConnection;
    final epoch = connection.generation;
    final id = '${job['id']}';
    jobsRevision++;
    setState(() {
      busy = true;
      removing = id;
      result = null;
    });
    try {
      final data = await connection.request('projects/jobs/remove', {
        'id': id,
        'delete_folder': deleteCloneFolder,
        if (deleteCloneFolder) 'confirm_folder': removalConfirmation,
      });
      if (!mounted || epoch != connection.generation || !canRemove) return;
      setState(() {
        jobs.removeWhere((item) => item['id'] == id);
        pendingRemoval = null;
        result = '${data['message']}';
      });
      if (data['folder_deleted'] == true)
        component.onDeleted?.call('${job['cwd']}');
      context.binding.addPostFrameCallback(() {
        if (!mounted) return;
        (web.document.querySelector(
                  '.clone-project .clone-remove:not(:disabled), .clone-project button[type=submit]',
                )
                as web.HTMLElement?)
            ?.focus();
      });
    } catch (error) {
      if (mounted && epoch == connection.generation)
        setState(() => result = '$error');
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() {
          busy = false;
          removing = null;
        });
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    cloneConnection.operator.removeListener(cloneSessionChanged);
    cloneSubscription?.cancel();
    super.dispose();
  }

  @override
  Component build(BuildContext context) => el('section', [
    _text('overview-disclosure__heading', 'Clone a project', 'h3'),
    _text(
      'overview-disclosure__hint',
      'Clone a repository into a new folder inside the container’s projects directory. Existing folders are preserved.',
      'p',
    ),
    if (dashboardConnection.operator.value['role'] == 'admin')
      _text(
        'overview-disclosure__hint',
        'For a private repository, add a connection below.',
        'p',
      ),
    el(
      'form',
      [
        select(
          'Repository connection',
          connectionId,
          {
            '': 'Container Git credentials / public repository',
            for (final item in connections)
              '${item['id']}':
                  "${item['label']} · ${item['kind']} · ${item['access_state']}",
          },
          (value) => setState(() {
            unavailableConnection = null;
            connectionId = value;
            final item = connections
                .where((item) => item['id'] == value)
                .firstOrNull;
            if (item != null) {
              url = item['kind'] == 'ssh'
                  ? "git@github.com:${item['repository']}.git"
                  : "https://github.com/${item['repository']}.git";
              if (folder.isEmpty)
                folder = '${item['repository']}'.split('/').last;
            }
          }),
        ),
        field('Repository URL', url, (value) => url = value, required: true),
        field(
          'New folder name',
          folder,
          (value) => folder = value,
          required: true,
        ),
        dom.button(
          [icon('git-branch'), text('Clone project')],
          type: dom.ButtonType.submit,
          disabled: busy,
          classes: 'btn btn--primary',
        ),
      ],
      classes: 'overview-form',
      events: {
        'submit': (event) {
          event.preventDefault();
          if (!busy) clone();
        },
      },
    ),
    if (dashboardConnection.operator.value['role'] == 'admin')
      RepositoryConnectionsPanel(
        onChanged: (values) {
          if (mounted)
            setState(() {
              connections = values;
              if (unavailableConnection != null &&
                  knownRepositoryConnection(values, unavailableConnection!)) {
                connectionId = unavailableConnection!;
                unavailableConnection = null;
              }
              if (!values.any((item) => item['id'] == connectionId))
                if (connectionId.isNotEmpty) {
                  unavailableConnection = connectionId;
                  connectionId = '';
                }
            });
        },
      ),
    if (jobs.isNotEmpty)
      el(
        'ul',
        [
          for (final job in jobs)
            el('li', [
              el('span', [
                el('strong', [text(job['folder'] ?? job['url'])]),
                _text(
                  'clone-job__detail',
                  (job['error'] as String? ?? '').isNotEmpty
                      ? job['error']
                      : job['detail'] ?? job['cwd'] ?? '',
                ),
              ], classes: 'clone-job__text'),
              badge('${job['state']}', tone: toneFor('${job['state']}')),
              if (['failed', 'interrupted'].contains(job['state']) &&
                  job['url'] != null)
                button(
                  'Retry in a new folder',
                  () => setState(() {
                    url = '${job['url']}';
                    connectionId = '${job['connection_id'] ?? ''}';
                    unavailableConnection =
                        knownRepositoryConnection(connections, connectionId)
                        ? null
                        : connectionId;
                    if (unavailableConnection != null) connectionId = '';
                    final previous = '${job['folder']}';
                    folder =
                        '${previous.substring(0, previous.length.clamp(0, 70))}-retry';
                    result =
                        'The previous folder was preserved. Review the new folder name, then clone again.';
                  }),
                  small: true,
                ),
              if (job['state'] == 'completed')
                button(
                  'Use project',
                  () => component.onCloned(job['cwd'] as String),
                  small: true,
                ),
              if (canRemove) ...[
                dom.button(
                  [icon('trash-2'), text('Remove clone')],
                  type: dom.ButtonType.button,
                  classes: 'btn btn--ghost btn--sm clone-remove',
                  disabled:
                      busy ||
                      ![
                        'completed',
                        'failed',
                        'interrupted',
                      ].contains(job['state']),
                  attributes: {
                    'id': 'clone-remove-${job['id']}',
                    'aria-label': 'Remove clone ${job['folder']}',
                    'aria-expanded':
                        '${pendingRemoval == job['id'] && removalGeneration == dashboardConnection.generation}',
                    if (pendingRemoval == job['id'] &&
                        removalGeneration == dashboardConnection.generation)
                      'aria-controls': 'clone-removal-${job['id']}',
                  },
                  onClick: () => prepareRemoval(job),
                ),
                if (pendingRemoval == job['id'] &&
                    removalGeneration == dashboardConnection.generation)
                  el(
                    'div',
                    [
                      el('p', [
                        text(
                          'Remove ${job['folder']} from clone history? Your files are kept unless you choose deletion below.',
                        ),
                      ]),
                      el('p', [text(cloneRemovalPreservationNotice)]),
                      if (!cloneFolderIdentityAvailable(job))
                        el('p', [text(legacyCloneRemovalNotice)]),
                      el('label', [
                        dom.input<bool>(
                          type: dom.InputType.checkbox,
                          checked: deleteCloneFolder,
                          disabled: busy || !cloneFolderIdentityAvailable(job),
                          onChange: (value) =>
                              setState(() => deleteCloneFolder = value),
                        ),
                        text('Also permanently delete the cloned folder'),
                      ], classes: 'clone-removal__option'),
                      if (deleteCloneFolder) ...[
                        el('p', [
                          text(
                            'This deletes ${job['cwd']} and all files inside, including local changes. This cannot be undone.',
                          ),
                        ]),
                        field(
                          'Type ${job['folder']} to confirm deletion',
                          removalConfirmation,
                          (value) =>
                              setState(() => removalConfirmation = value),
                          disabled: busy,
                          autocomplete: 'off',
                          spellcheck: false,
                        ),
                      ],
                      el('div', [
                        button(
                          'Cancel removal',
                          () {
                            setState(() => pendingRemoval = null);
                            context.binding.addPostFrameCallback(() {
                              if (mounted)
                                (web.document.getElementById(
                                          'clone-remove-${job['id']}',
                                        )
                                        as web.HTMLElement?)
                                    ?.focus();
                            });
                          },
                          disabled: busy,
                          small: true,
                        ),
                        button(
                          removing == job['id']
                              ? 'Removing…'
                              : deleteCloneFolder
                              ? 'Delete folder and history'
                              : 'Remove history entry',
                          () => removeClone(job),
                          disabled:
                              busy ||
                              (deleteCloneFolder &&
                                  removalConfirmation != job['folder']),
                          variant: deleteCloneFolder ? 'danger' : 'primary',
                          small: true,
                          classes: 'clone-removal__confirm',
                        ),
                      ], classes: 'actions'),
                    ],
                    classes: 'clone-removal',
                    attributes: {
                      'id': 'clone-removal-${job['id']}',
                      'role': 'group',
                      'aria-label': 'Remove clone ${job['folder']}',
                    },
                  ),
              ],
            ], classes: 'job-row clone-job'),
        ],
        classes: 'clone-jobs',
        attributes: {'role': 'list'},
      ),
    if (result != null) notice(result),
  ], classes: 'overview-disclosure__section clone-project');
}
