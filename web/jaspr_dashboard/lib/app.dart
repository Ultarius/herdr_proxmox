import 'dart:async';
import 'dart:js_interop';
import 'package:web/web.dart' as web;
import 'ui.dart';
import 'services.dart';
import 'navigation.dart';
import 'overview.dart';
import 'platform/theme_storage_web.dart' as themeStorage;
import 'pages/organization_page.dart';
import 'pages/group_page.dart';
import 'pages/agent_console_page.dart';
import 'pages/tasks_page.dart';
import 'pages/knowledge_page.dart';
import 'pages/builds_page.dart';
import 'pages/logs_page.dart';
import 'pages/cli_setup_page.dart';
import 'pages/project_explorer_page.dart';

class DashboardApp extends StatefulComponent {
  const DashboardApp({super.key});
  @override
  State<DashboardApp> createState() => _DashboardAppState();
}

class _DashboardAppState extends State<DashboardApp> {
  DashboardBloc get connection => dashboardConnection;
  final subscriptions = <StreamSubscription>[];
  late final JSFunction hashListener;
  bool restoring = true;
  bool drawer = false;
  bool agentsOpen = true;
  bool sidebarCollapsed = themeStorage.readSidebarCollapsed();
  bool loadingDirectory = false;
  bool polling = false;
  int noticeGeneration = -1;
  int sessionGeneration = -1;
  Timer? noticeTimer;
  String token = '';
  String theme = 'system';
  String navSearch = '';
  String? noticesChecked;
  List<IntegrationNotice> notices = [];
  final Set<String> seen = {};
  final Set<String> asking = {};

  @override
  void initState() {
    super.initState();
    final queryTheme = Uri.base.queryParameters['theme'];
    theme = ['light', 'dark'].contains(queryTheme)
        ? queryTheme!
        : themeStorage.readTheme() ?? 'system';
    applyTheme(theme, persist: false);
    hashListener = ((web.Event _) {
      routeChanged();
    }).toJS;
    web.window.addEventListener('hashchange', hashListener);
    subscriptions.add(
      connection.stream.listen((_) {
        if (!mounted) return;
        if (sessionGeneration != connection.generation) {
          sessionGeneration = connection.generation;
          closeDialogs();
          messages.value = null;
          asking.clear();
          notices = [];
          seen.clear();
          noticesChecked = null;
        }
        setState(() {});
        loadDirectory();
        if (noticeGeneration != connection.generation)
          pollNotices(announce: false);
      }),
    );
    connection.organizationDirectory.addListener(rebuild);
    connection.selectedOrganization.addListener(rebuild);
    connection.operator.addListener(rebuild);
    routeChanges.addListener(routeChanged);
    dialogChanges.addListener(rebuild);
    messages.addListener(rebuild);
    connection.notificationRevision.addListener(rebuild);
    connection.restoreSession().whenComplete(() {
      if (mounted) setState(() => restoring = false);
    });
    noticeTimer = Timer.periodic(
      const Duration(seconds: 15),
      (_) => pollNotices(),
    );
  }

  void rebuild() {
    if (mounted) setState(() {});
  }

  void routeChanged() {
    if (!mounted) return;
    setState(() => drawer = false);
    scheduleMicrotask(() {
      (web.document.getElementById('content') as web.HTMLElement?)?.scrollTop =
          0;
    });
  }

  void applyTheme(String value, {bool persist = true}) {
    theme = value;
    if (['light', 'dark'].contains(value))
      web.document.documentElement?.setAttribute('data-theme', value);
    else
      web.document.documentElement?.removeAttribute('data-theme');
    if (persist) themeStorage.writeTheme(value);
    rebuild();
  }

  Future<void> loadDirectory() async {
    if (loadingDirectory ||
        !connection.state.connected ||
        connection.organizationDirectory.value['loaded'] == true)
      return;
    loadingDirectory = true;
    try {
      await connection.request('organizations/directory');
    } catch (_) {
    } finally {
      loadingDirectory = false;
    }
  }

  Future<void> pollNotices({bool announce = true}) async {
    if (!connection.state.connected) {
      if (notices.isNotEmpty || seen.isNotEmpty)
        setState(() {
          notices = [];
          seen.clear();
          noticesChecked = null;
        });
      return;
    }
    if (noticeGeneration != connection.generation) {
      noticeGeneration = connection.generation;
      setState(() {
        notices = [];
        seen.clear();
        noticesChecked = null;
      });
    }
    if (polling) return;
    final epoch = connection.generation;
    polling = true;
    try {
      final result = await connection.request('integration');
      if (!mounted ||
          epoch != connection.generation ||
          !connection.state.connected)
        return;
      final next = [
        for (final notice in (result['alerts'] as List? ?? []))
          if (notice is Map<String, dynamic>)
            IntegrationNotice.fromJson(notice),
      ];
      seen.removeWhere((key) => !next.any((notice) => notice.key == key));
      final fresh = next.where((notice) => seen.add(notice.key)).toList();
      setState(() {
        notices = next;
        noticesChecked = result['checked'] as String?;
      });
      refreshDialogs();
      if (announce && fresh.isNotEmpty)
        toast(
          fresh.length == 1
              ? 'Integration notice: ${fresh.first.summary}'
              : '${fresh.length} integration notices need review',
        );
    } catch (_) {
    } finally {
      polling = false;
    }
  }

  Future<void> askIntegration(IntegrationNotice notice) async {
    if (asking.contains(notice.profileId)) return;
    final epoch = connection.generation;
    setState(() => asking.add(notice.profileId));
    refreshDialogs();
    try {
      await requestIntegration(connection, notice);
      if (!mounted || epoch != connection.generation) return;
      toast(
        'Asked ${notice.name.isEmpty ? 'the agent' : notice.name} to integrate.',
      );
    } catch (error) {
      if (mounted && epoch == connection.generation)
        toast('Integration request failed: $error');
    } finally {
      if (mounted && epoch == connection.generation) {
        setState(() => asking.remove(notice.profileId));
        refreshDialogs();
      }
    }
  }

  Future<void> showNotices() => showDialog<void>(
    'Integration notices',
    (close) => el('div', [
      el('p', [text('Agents keep working. Nothing is merged automatically.')]),
      if (noticesChecked != null)
        el('p', [
          text(
            'Checked ${DateTime.tryParse(noticesChecked!)?.toLocal() ?? noticesChecked}',
          ),
        ], classes: 'muted'),
      if (notices.isEmpty)
        text('No integration notices were found in the last check.'),
      for (final notice in notices)
        card(notice.name.isEmpty ? notice.path : notice.name, [
          el('code', [text('${notice.path} · ${notice.branch}')]),
          el('p', [text(notice.summary)]),
          el('p', [
            text(
              notice.lastFetch == null
                  ? 'Remote comparison has no recorded fetch time.'
                  : 'Remote last fetched: ${DateTime.tryParse(notice.lastFetch!)?.toLocal() ?? notice.lastFetch}',
            ),
          ], classes: 'muted'),
          el('div', [
            if (connection.operator.value['role'] == 'admin')
              button(
                agentCanIntegrate(connection, notice.profileId)
                    ? 'Ask to integrate'
                    : 'Busy; ask later to integrate',
                () => askIntegration(notice),
                disabled:
                    !agentCanIntegrate(connection, notice.profileId) ||
                    asking.contains(notice.profileId),
              ),
            button(
              'Copy instructions',
              () async => toast(
                await copyText(
                  notice.instructions,
                  filename: 'integration-instructions.md',
                ),
              ),
            ),
            button('Open Git changes', () {
              close(null);
              navigate(
                Uri(
                  path: '/explorer',
                  queryParameters: {'path': notice.path},
                ).toString(),
              );
            }),
          ], classes: 'actions'),
        ]),
      button('Close', () => close(null)),
    ]),
  ).then((_) {});

  @override
  void dispose() {
    noticeTimer?.cancel();
    for (final sub in subscriptions) unawaited(sub.cancel());
    web.window.removeEventListener('hashchange', hashListener);
    connection.organizationDirectory.removeListener(rebuild);
    connection.selectedOrganization.removeListener(rebuild);
    connection.operator.removeListener(rebuild);
    connection.notificationRevision.removeListener(rebuild);
    routeChanges.removeListener(routeChanged);
    dialogChanges.removeListener(rebuild);
    messages.removeListener(rebuild);
    super.dispose();
  }

  String get title {
    final path = currentRoute.path;
    if (path.startsWith('/knowledge/')) return 'Project knowledge';
    if (path.startsWith('/agents/')) return 'Agent console';
    if (path.startsWith('/groups/')) return 'Discussion group';
    if (path.startsWith('/tasks/')) return 'Task detail';
    return const {
          '/': 'Overview',
          '/agents': 'Agent activity',
          '/organization': 'Organization',
          '/tasks': 'Tasks',
          '/knowledge': 'Project knowledge',
          '/explorer': 'Project explorer',
          '/logs': 'Terminal logs',
          '/configuration': 'CLI accounts',
          '/builds': 'Builds & deployments',
        }[path] ??
        'Page not found';
  }

  Component _page() {
    final route = currentRoute;
    final key = Key('${route.toString()}:${connection.generation}');
    final segments = route.pathSegments;
    if (segments.length == 2 && segments.first == 'agents')
      return AgentConsolePage(id: segments[1], key: key);
    if (segments.length == 2 && segments.first == 'groups')
      return GroupPage(id: segments[1], key: key);
    if (segments.length == 2 && segments.first == 'tasks')
      return TasksPage(taskId: segments[1], key: key);
    if (segments.length == 2 && segments.first == 'knowledge')
      return KnowledgePage(organizationId: segments[1], key: key);
    return switch (route.path) {
      '/' || '' => OverviewPage(key: key),
      '/agents' => OverviewPage(agentsOnly: true, key: key),
      '/organization' => OrganizationPage(
        initialView:
            route.queryParameters['view'] ??
            route.queryParameters['section'] ??
            'chart',
        key: key,
      ),
      '/tasks' => TasksPage(
        key: key,
        initialView: route.queryParameters['view'] ?? 'workflow',
      ),
      '/knowledge' => KnowledgePage(
        organizationId: route.queryParameters['organization'] ?? '',
        key: key,
      ),
      '/builds' => BuildsPage(key: key),
      '/logs' => LogsPage(key: key),
      '/configuration' => CliSetupPage(key: key),
      '/explorer' => ProjectExplorerPage(
        initialPath: route.queryParameters['path'] ?? '',
        key: key,
      ),
      _ => card('Page not found', [
        button('Return to dashboard', () => navigate('/', replace: true)),
      ]),
    };
  }

  Component _nav(String label, String name, String route) => el(
    'a',
    [
      icon(name),
      el('span', [text(label)], classes: 'nav__label'),
    ],
    classes: 'nav__item nav-item',
    attributes: {
      'href': '#$route',
      'aria-current': currentRoute.path == Uri.parse(route).path
          ? 'page'
          : 'false',
    },
  );

  Component _section(String label) =>
      el('p', [text(label)], classes: 'nav__section');

  void toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed;
    if (sidebarCollapsed)
      web.document.documentElement?.setAttribute('data-sidebar', 'collapsed');
    else
      web.document.documentElement?.removeAttribute('data-sidebar');
    themeStorage.writeSidebarCollapsed(sidebarCollapsed);
    rebuild();
  }

  /// Topbar runtime pill: connection + Herdr state in one place.
  Component _runtimePill(DashboardState state) {
    final running = state.connected && state.serverStatus == 'running';
    final label = !state.connected
        ? 'Disconnected'
        : 'Herdr ${state.serverStatus}';
    return el(
      'span',
      [
        icon(
          state.connected ? 'wifi' : 'wifi-off',
          classes: 'runtime-pill__icon',
        ),
        el('span', [text(label)], classes: 'runtime-pill__label'),
      ],
      classes: 'runtime-pill',
      attributes: {
        'data-tone': running
            ? 'success'
            : state.connected
            ? 'warning'
            : 'neutral',
        'role': 'status',
      },
    );
  }

  Component _themeSwitch() => el(
    'div',
    [
      for (final (mode, iconName, label) in [
        ('light', 'sun', 'Light theme'),
        ('system', 'monitor', 'System theme'),
        ('dark', 'moon', 'Dark theme'),
      ])
        el(
          'button',
          [icon(iconName)],
          classes: 'segmented__btn',
          attributes: {
            'type': 'button',
            'role': 'radio',
            'aria-checked': '${theme == mode}',
            'aria-label': label,
            'title': label,
          },
          events: {'click': (_) => applyTheme(mode)},
        ),
    ],
    classes: 'segmented theme-switch',
    attributes: {'role': 'radiogroup', 'aria-label': 'Color theme'},
  );
  Component _signIn() => el('div', [
    logo(),
    el('h1', [text('Connect to this container')]),
    if (restoring)
      notice('Checking your browser session…')
    else
      el(
        'form',
        [
          field(
            'Dashboard access token',
            token,
            (value) => token = value,
            password: true,
            required: true,
          ),
          el('p', [
            text('Stay signed in for 7 days. Sign out on shared devices.'),
          ], classes: 'muted'),
          el(
            'button',
            [text('Connect')],
            classes: 'btn btn--primary',
            attributes: {'type': 'submit'},
          ),
        ],
        events: {
          'submit': (event) {
            event.preventDefault();
            if (token.trim().isEmpty) return;
            final credential = token.trim();
            final form = event.target as web.HTMLFormElement;
            (form.querySelector('input') as web.HTMLInputElement?)?.value = '';
            setState(() => token = '');
            connection.signIn(credential);
          },
        },
      ),
  ], classes: 'login card');

  @override
  Component build(BuildContext context) {
    final state = connection.state;
    final fluidOverview =
        state.connected &&
        const ['', '/', '/agents'].contains(currentRoute.path);
    final directory = connection.organizationDirectory.value;
    final orgs = List<Map<String, dynamic>>.from(
      directory['organizations'] ?? [],
    );
    final org =
        orgs
            .where(
              (item) => item['id'] == connection.selectedOrganization.value,
            )
            .firstOrNull ??
        orgs.firstOrNull;
    final profiles =
        List<Map<String, dynamic>>.from(directory['profiles'] ?? [])
            .where(
              (item) =>
                  item['organization_id'] == org?['id'] &&
                  item['group_id'] == null,
            )
            .toList();
    final groups = List<Map<String, dynamic>>.from(
      directory['groups'] ?? [],
    ).where((item) => item['organization_id'] == org?['id']).toList();
    final archived = List<Map<String, dynamic>>.from(
      directory['archived_groups'] ?? [],
    ).where((item) => item['organization_id'] == org?['id']).toList();
    final needle = navSearch.trim().toLowerCase();
    String activityOf(Map<String, dynamic> profile) => activityFor(
      connection.state.agents
              .where((a) => a['profile_id'] == profile['id'])
              .firstOrNull ??
          {},
    );
    final working = profiles
        .where((profile) => activityOf(profile) == 'working')
        .length;
    final updated = state.refreshed == null
        ? 'Waiting for first update'
        : 'Updated ${state.refreshed!.toLocal().toString().substring(11, 19)}';
    return el('div', [
      el(
        'a',
        [text('Skip to content')],
        classes: 'skip-link',
        attributes: {'href': '#content'},
        events: {
          'click': (event) {
            event.preventDefault();
            (web.document.getElementById('content') as web.HTMLElement?)
                ?.focus();
          },
        },
      ),
      el(
        'aside',
        [
          el(
            'a',
            [logo()],
            attributes: {'href': '#/', 'aria-label': 'Herdr home'},
            classes: 'rail__brand',
          ),
          el(
            'ul',
            [
              for (final item in orgs)
                el('li', [
                  el(
                    'button',
                    [
                      text(
                        '${item['name']}'.trim().isEmpty
                            ? '?'
                            : '${item['name']}'
                                  .trim()
                                  .substring(0, 1)
                                  .toUpperCase(),
                      ),
                    ],
                    classes: 'rail__tile',
                    attributes: {
                      'type': 'button',
                      'title': '${item['name']}',
                      'aria-label': 'Select ${item['name']}',
                      'aria-current': '${item['id'] == org?['id']}',
                    },
                    events: {
                      'click': (_) {
                        connection.selectedOrganization.value =
                            item['id'] as String;
                        navigate('/organization');
                      },
                    },
                  ),
                ]),
              el('li', [
                el(
                  'a',
                  [icon('plus')],
                  classes: 'rail__add',
                  attributes: {
                    'href': '#/organization',
                    'aria-label': 'Manage organizations',
                    'title': 'Manage organizations',
                  },
                ),
              ]),
            ],
            classes: 'rail__list',
            attributes: {'role': 'list'},
          ),
          el('div', [
            el(
              'a',
              [icon('settings')],
              classes: 'rail__btn',
              attributes: {
                'href': '#/configuration',
                'aria-label': 'Settings',
                'title': 'Settings',
              },
            ),
          ], classes: 'rail__footer'),
        ],
        classes: 'rail',
        attributes: {'aria-label': 'Organizations'},
      ),
      el(
        'aside',
        [
          el('div', [
            el('span', [logo()], classes: 'sidebar__mark'),
            el('span', [text('Herdr')], classes: 'wordmark'),
            iconButton(
              'Close navigation',
              'x',
              () => setState(() => drawer = false),
              small: true,
              classes: 'sidebar__close drawer-close',
            ),
          ], classes: 'sidebar__brand'),
          el('div', [
            el('div', [
              icon('search', classes: 'icon--sm search-field__icon'),
              field(
                'Search navigation',
                navSearch,
                (value) => setState(() => navSearch = value),
                placeholder: 'Search agents or groups',
              ),
            ], classes: 'nav-search search-field'),
            el(
              'nav',
              [
                el('div', [
                  _nav('Overview', 'layout-dashboard', '/'),
                  _nav('Org chart', 'network', '/organization'),
                  _nav('Tasks', 'list-checks', '/tasks'),
                  _nav('Builds & deployments', 'rocket', '/builds'),
                  _nav('Project knowledge', 'book-open', '/knowledge'),
                  _nav('Agent activity', 'activity', '/agents'),
                ], classes: 'nav'),
                _section('Workspace'),
                el('div', [
                  _nav('Project explorer', 'folder', '/explorer'),
                  _nav('Terminal logs', 'terminal', '/logs'),
                  _nav('CLI accounts', 'key-round', '/configuration'),
                ], classes: 'nav'),
                el('div', [
                  el(
                    'button',
                    [icon('chevron-down'), text('Agents')],
                    classes: 'nav-group__toggle',
                    attributes: {
                      'type': 'button',
                      'aria-expanded': '$agentsOpen',
                      'aria-controls': 'nav-agents',
                    },
                    events: {
                      'click': (_) => setState(() => agentsOpen = !agentsOpen),
                    },
                  ),
                  if (profiles.isNotEmpty)
                    el('span', [
                      text('$working working'),
                    ], classes: 'nav-group__meta'),
                ], classes: 'nav__section'),
                el(
                  'div',
                  [
                    if (profiles.isEmpty)
                      el('p', [
                        text('Hire your first agent'),
                      ], classes: 'nav__empty'),
                    for (final profile in profiles.where(
                      (p) => '$p'.toLowerCase().contains(needle),
                    ))
                      el(
                        'a',
                        [
                          statusDot(activityOf(profile)),
                          el('span', [
                            text(profile['name']),
                          ], classes: 'nav__label'),
                          el('span', [
                            text(activityOf(profile)),
                          ], classes: 'visually-hidden'),
                        ],
                        classes: 'nav__item nav-item',
                        attributes: {
                          'href':
                              '#/agents/${Uri.encodeComponent('${profile['id']}')}',
                          'aria-current':
                              currentRoute.path == '/agents/${profile['id']}'
                              ? 'page'
                              : 'false',
                        },
                      ),
                  ],
                  classes: 'nav',
                  attributes: {
                    'id': 'nav-agents',
                    if (!agentsOpen) 'hidden': '',
                  },
                ),
                _section('Groups'),
                el('div', [
                  if (groups.isEmpty)
                    el('p', [
                      text('No discussion groups yet'),
                    ], classes: 'nav__empty'),
                  for (final group in groups.where(
                    (g) => '$g'.toLowerCase().contains(needle),
                  ))
                    _nav(
                      '${group['name']}',
                      'message-square',
                      '/groups/${Uri.encodeComponent('${group['id']}')}',
                    ),
                ], classes: 'nav'),
                if (archived.isNotEmpty) _section('Archived groups'),
                if (archived.isNotEmpty)
                  el('div', [
                    for (final group in archived.where(
                      (g) => '$g'.toLowerCase().contains(needle),
                    ))
                      _nav(
                        '${group['name']}',
                        'archive',
                        '/groups/${Uri.encodeComponent('${group['id']}')}',
                      ),
                  ], classes: 'nav'),
              ],
              attributes: {'aria-label': 'Main navigation'},
            ),
          ], classes: 'sidebar__scroll'),
          if (state.connected)
            el('div', [
              el('div', [
                el(
                  'span',
                  [text(initials(connection.operator.value['name']))],
                  classes: 'avatar',
                  attributes: {'aria-hidden': 'true'},
                ),
                el('span', [
                  el('span', [
                    text(connection.operator.value['name']),
                  ], classes: 'user-card__name'),
                  el('span', [
                    text(connection.operator.value['role']),
                  ], classes: 'user-card__meta'),
                ], classes: 'user-card__text'),
                iconButton(
                  'Sign out',
                  'log-out',
                  connection.disconnect,
                  small: true,
                ),
              ], classes: 'user-card'),
            ], classes: 'sidebar__footer'),
        ],
        classes: 'sidebar ${drawer ? 'is-open' : ''}',
        attributes: {'aria-label': 'Sidebar'},
      ),
      if (drawer)
        el(
          'button',
          [],
          classes: 'scrim drawer-scrim',
          attributes: {'aria-label': 'Close navigation', 'type': 'button'},
          events: {'click': (_) => setState(() => drawer = false)},
        ),
      el('div', [
        el('header', [
          iconButton(
            'Open navigation',
            'menu',
            () => setState(() => drawer = !drawer),
            classes: 'topbar__menu drawer-toggle',
            attributes: {'aria-expanded': '$drawer'},
          ),
          iconButton(
            sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar',
            'panel-left',
            toggleSidebar,
            classes: 'topbar__collapse',
            attributes: {'aria-expanded': '${!sidebarCollapsed}'},
          ),
          el(
            'nav',
            [
              el(
                'ol',
                [
                  if (org != null)
                    el('li', [
                      el(
                        'a',
                        [text('${org['name']}')],
                        classes: 'breadcrumb__link',
                        attributes: {'href': '#/organization'},
                      ),
                    ], classes: 'breadcrumb__item'),
                  el('li', [
                    el(
                      'span',
                      [text(title)],
                      attributes: {'aria-current': 'page'},
                    ),
                  ], classes: 'breadcrumb__item'),
                ],
                classes: 'breadcrumb__list',
                attributes: {'role': 'list'},
              ),
            ],
            classes: 'breadcrumb',
            attributes: {'aria-label': 'Breadcrumb'},
          ),
          el('div', [
            el('span', [text(updated)], classes: 'updated topbar__updated'),
            _runtimePill(state),
            if (state.connected)
              iconButton(
                'Refresh',
                'refresh-cw',
                () => connection.send(DashboardCommand('refresh')),
                disabled: state.busy,
                classes: state.busy ? 'is-spinning' : null,
              ),
            _themeSwitch(),
            if (state.connected)
              el(
                'button',
                [
                  icon('bell'),
                  el('span', [
                    text(
                      notices.isEmpty
                          ? 'Integration notices'
                          : '${notices.length} integration notices',
                    ),
                  ], classes: 'visually-hidden'),
                  if (notices.isNotEmpty) el('span', [], classes: 'notif__dot'),
                ],
                classes: 'btn btn--ghost btn--icon notif notice-button',
                attributes: {
                  'type': 'button',
                  'title': notices.isEmpty
                      ? 'Integration notices'
                      : '${notices.length} integration notices',
                },
                events: {'click': (_) => showNotices()},
              ),
          ], classes: 'topbar__actions'),
        ], classes: fluidOverview ? 'topbar topbar--fluid' : 'topbar'),
        el(
          'main',
          [
            el('div', [
              if (state.error != null) notice(state.error, error: true),
              if (messages.value != null)
                el('div', [
                  notice(messages.value),
                  button(
                    'Dismiss',
                    () => messages.value = null,
                    variant: 'ghost',
                  ),
                ], classes: 'toast'),
              if (state.connected) _page() else _signIn(),
            ], classes: 'page-frame'),
          ],
          classes: fluidOverview
              ? 'main-content main-content--fluid'
              : 'main-content',
          attributes: {
            'id': 'content',
            'tabindex': '-1',
            'aria-busy': '${state.busy}',
          },
        ),
      ], classes: 'workspace app__column'),
      buildDialogs(),
    ], classes: 'app');
  }
}
