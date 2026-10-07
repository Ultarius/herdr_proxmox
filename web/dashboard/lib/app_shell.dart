import 'package:juice/juice.dart';
import 'clipboard_copy.dart';
import 'dashboard_bloc.dart';
import 'integration.dart';
import 'routes.dart';
import 'app_theme.dart';

class AppShell extends StatefulWidget {
  const AppShell({
    super.key,
    required this.coordinator,
    required this.section,
    required this.child,
  });
  final AppCoordinator coordinator;
  final String section;
  final Widget child;
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  StreamSubscription? subscription;
  bool loading = false;
  bool polling = false;
  int noticeGeneration = -1;
  List<IntegrationNotice> notices = const [];
  final Set<String> seen = {};
  final Set<String> asking = {};
  String? noticesChecked;
  Timer? noticeTimer;

  @override
  void initState() {
    super.initState();
    subscription = connection.stream.listen((_) {
      load();
      if (noticeGeneration != connection.generation)
        pollNotices(announce: false);
    });
    load();
    pollNotices(announce: false);
    noticeTimer = Timer.periodic(
      const Duration(seconds: 15),
      (_) => pollNotices(),
    );
  }

  Future<void> load() async {
    if (loading ||
        !connection.state.connected ||
        connection.organizationDirectory.value['loaded'] == true)
      return;
    loading = true;
    try {
      await connection.request('organizations/directory');
    } catch (_) {
      /* The active page reports connection errors. */
    } finally {
      loading = false;
    }
  }

  @override
  void dispose() {
    noticeTimer?.cancel();
    subscription?.cancel();
    super.dispose();
  }

  final shellKey = GlobalKey<ScaffoldState>();

  void navigate(AppRoute route) {
    shellKey.currentState?.closeDrawer();
    widget.coordinator.navigate(route);
  }

  /// Integration notices are polled here rather than on one page so drift is
  /// visible wherever the operator is. Nothing is merged automatically and a
  /// busy agent is left alone; a notice only asks for attention.
  Future<void> pollNotices({bool announce = true}) async {
    if (!connection.state.connected) {
      if (notices.isNotEmpty || seen.isNotEmpty)
        setState(() {
          notices = const [];
          seen.clear();
          noticesChecked = null;
        });
      return;
    }
    if (noticeGeneration != connection.generation) {
      noticeGeneration = connection.generation;
      setState(() {
        notices = const [];
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
      final notices = [
        for (final notice in (result['alerts'] as List? ?? const []))
          if (notice is Map<String, dynamic>)
            IntegrationNotice.fromJson(notice),
      ];
      seen.removeWhere((key) => !notices.any((n) => n.key == key));
      final fresh = notices.where((n) => seen.add(n.key)).toList();
      if (mounted)
        setState(() {
          this.notices = notices;
          noticesChecked = result['checked'] as String?;
        });
      if (announce && fresh.isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              fresh.length == 1
                  ? 'Integration notice: ${fresh.first.summary}'
                  : '${fresh.length} integration notices need review',
            ),
            action: SnackBarAction(label: 'Review', onPressed: showNotices),
            duration: const Duration(seconds: 8),
          ),
        );
      }
    } catch (_) {
      // Notices are advisory; a failure must not disturb the current page.
    } finally {
      polling = false;
    }
  }

  Future<void> showNotices() async {
    final checked = noticesChecked;
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Integration notices'),
        content: SizedBox(
          width: 560,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Agents keep working. Nothing is merged automatically; ask an '
                'idle agent to integrate, or copy the instructions yourself.',
              ),
              if (checked != null) ...[
                const SizedBox(height: 4),
                Text(
                  'Checked ${DateTime.tryParse(checked)?.toLocal() ?? checked}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
              const SizedBox(height: 12),
              Flexible(
                child: notices.isEmpty
                    ? const Text(
                        'No integration notices were found in the last check.',
                      )
                    : ListView(
                        shrinkWrap: true,
                        children: [
                          for (final notice in notices) noticeCard(notice),
                        ],
                      ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Widget noticeCard(IntegrationNotice notice) {
    final canAsk = agentCanIntegrate(connection, notice.profileId);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              notice.name.isEmpty ? notice.path : notice.name,
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text(
              '${notice.path}${notice.branch.isEmpty ? '' : ' · ${notice.branch}'}',
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 6),
            Text(notice.summary),
            Text(
              notice.lastFetch == null
                  ? 'Remote comparison has no recorded fetch time.'
                  : 'Remote last fetched: ${DateTime.tryParse(notice.lastFetch!)?.toLocal() ?? notice.lastFetch}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  onPressed: canAsk && !asking.contains(notice.profileId)
                      ? () => askIntegration(notice)
                      : null,
                  icon: const Icon(Icons.notifications_outlined, size: 18),
                  label: Text(
                    canAsk
                        ? 'Ask to integrate'
                        : 'Busy; ask later to integrate',
                  ),
                ),
                TextButton.icon(
                  onPressed: () async {
                    final message = await copyTextOrDownload(
                      notice.instructions,
                      'integration-instructions.md',
                      copied: 'Instructions copied.',
                      downloaded:
                          'Clipboard unavailable over HTTP; instructions downloaded instead.',
                    );
                    if (context.mounted)
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text(message)),
                      );
                  },
                  icon: const Icon(Icons.copy_outlined, size: 18),
                  label: const Text('Copy instructions'),
                ),
                TextButton.icon(
                  onPressed: () {
                    Navigator.pop(context);
                    navigate(ExplorerRoute(notice.path));
                  },
                  icon: const Icon(Icons.difference_outlined, size: 18),
                  label: const Text('Open Git changes'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> askIntegration(IntegrationNotice notice) async {
    if (asking.contains(notice.profileId)) return;
    setState(() => asking.add(notice.profileId));
    try {
      await requestIntegration(connection, notice);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Asked ${notice.name.isEmpty ? 'the agent' : notice.name} to integrate.',
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Integration request failed: $e')));
    } finally {
      if (mounted) setState(() => asking.remove(notice.profileId));
    }
  }

  Widget noticeBadge() {
    if (notices.isEmpty) return const SizedBox.shrink();
    return Tooltip(
      message: notices.length == 1
          ? '1 integration notice'
          : '${notices.length} integration notices',
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: showNotices,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.warning_amber_rounded,
                size: 20,
                color: Color(0xfff1c75b),
              ),
              const SizedBox(width: 6),
              Text(
                '${notices.length}',
                style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  color: Color(0xfff1c75b),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget navigation(
    List<Map<String, dynamic>> profiles,
    List<Map<String, dynamic>> groups,
  ) => Container(
    width: 224,
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.surface,
      border: Border(right: BorderSide(color: Theme.of(context).dividerColor)),
    ),
    child: Material(
      color: Theme.of(context).colorScheme.surface,
      child: ListView(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 20),
        children: [
          const Padding(
            padding: EdgeInsets.all(10),
            child: Text(
              'HERDR',
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                letterSpacing: 2,
              ),
            ),
          ),
          nav(
            'Dashboard',
            Icons.space_dashboard_outlined,
            DashboardRoute(),
            'dashboard',
          ),
          nav(
            'Org chart',
            Icons.account_tree_outlined,
            OrganizationRoute(),
            'organization',
          ),
          nav('Agent activity', Icons.bolt_outlined, AgentsRoute(), 'agents'),
          heading('WORKSPACE'),
          nav(
            'Project explorer',
            Icons.folder_open_outlined,
            ExplorerRoute(),
            'explorer',
          ),
          nav('Terminal logs', Icons.subject, LogsRoute(), 'logs'),
          nav('CLI accounts', Icons.terminal, CliSetupRoute(), 'configuration'),
          nav('Builds & deployments', Icons.build_outlined, BuildsRoute(), 'builds'),
          heading('AGENTS'),
          if (profiles.isEmpty)
            Padding(
              padding: const EdgeInsets.all(10),
              child: Text(
                'Hire your first agent',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 12,
                ),
              ),
            ),
          for (final profile in profiles)
            ListTile(
              dense: true,
              contentPadding: const EdgeInsets.symmetric(horizontal: 10),
              leading: const Icon(Icons.smart_toy_outlined, size: 16),
              title: Text(
                '${profile['name']}',
                style: const TextStyle(fontSize: 12),
              ),
              onTap: () => navigate(OrganizationRoute()),
            ),
          heading('GROUPS'),
          if (groups.isEmpty)
            Padding(
              padding: const EdgeInsets.all(10),
              child: Text(
                'No discussion groups yet',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 12,
                ),
              ),
            ),
          for (final group in groups)
            nav(
              '${group['name']}',
              Icons.forum_outlined,
              GroupRoute(group['id'] as String),
              'group:${group['id']}',
            ),
          if ((connection.organizationDirectory.value['archived_groups']
                      as List? ??
                  [])
              .isNotEmpty)
            heading('ARCHIVED GROUPS'),
          for (final group
              in (connection.organizationDirectory.value['archived_groups']
                          as List? ??
                      [])
                  .where(
                    (g) =>
                        g['organization_id'] ==
                        (connection.selectedOrganization.value ??
                            (connection
                                            .organizationDirectory
                                            .value['organizations']
                                        as List? ??
                                    [])
                                .firstOrNull?['id']),
                  ))
            nav(
              '${group['name']}',
              Icons.archive_outlined,
              GroupRoute(group['id'] as String),
              'group:${group['id']}',
            ),
          heading('CONTAINER'),
          ListTile(
            dense: true,
            leading: const Icon(Icons.logout, size: 16),
            title: const Text('Sign out', style: TextStyle(fontSize: 12)),
            onTap: connection.disconnect,
          ),
        ],
      ),
    ),
  );

  Widget heading(String text) => Padding(
    padding: const EdgeInsets.fromLTRB(10, 28, 10, 10),
    child: Text(
      text,
      style: TextStyle(
        fontSize: 10,
        letterSpacing: 1.5,
        color: Theme.of(context).colorScheme.onSurfaceVariant,
      ),
    ),
  );
  Widget nav(String label, IconData icon, AppRoute route, String section) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: ListTile(
          dense: true,
          selected: widget.section == section,
          selectedTileColor: Theme.of(context).colorScheme.primaryContainer,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
          contentPadding: const EdgeInsets.symmetric(horizontal: 10),
          leading: Icon(icon, size: 17),
          title: Text(label, style: const TextStyle(fontSize: 12)),
          onTap: () => navigate(route),
        ),
      );

  @override
  Widget build(
    BuildContext context,
  ) => ValueListenableBuilder<Map<String, dynamic>>(
    valueListenable: connection.organizationDirectory,
    builder: (context, directory, _) => ValueListenableBuilder<String?>(
      valueListenable: connection.selectedOrganization,
      builder: (context, selected, _) {
        final orgs = List<Map<String, dynamic>>.from(
          directory['organizations'],
        );
        final org =
            orgs.where((item) => item['id'] == selected).firstOrNull ??
            orgs.firstOrNull;
        final profiles = List<Map<String, dynamic>>.from(directory['profiles'])
            .where(
              (item) =>
                  item['organization_id'] == org?['id'] &&
                  item['group_id'] == null,
            )
            .toList();
        final groups = List<Map<String, dynamic>>.from(
          directory['groups'] ?? [],
        ).where((item) => item['organization_id'] == org?['id']).toList();
        return LayoutBuilder(
          builder: (context, constraints) {
            final desktop = constraints.maxWidth >= 1000;
            return Scaffold(
              key: shellKey,
              drawer: desktop
                  ? null
                  : Drawer(child: navigation(profiles, groups)),
              bottomNavigationBar: desktop
                  ? null
                  : NavigationBar(
                      selectedIndex: switch (widget.section) {
                        'explorer' => 1,
                        'agents' => 2,
                        'organization' => 3,
                        'configuration' || 'builds' => 4,
                        _ => 0,
                      },
                      onDestinationSelected: (index) => navigate(
                        [
                          DashboardRoute(),
                          ExplorerRoute(),
                          AgentsRoute(),
                          OrganizationRoute(),
                          CliSetupRoute(),
                        ][index],
                      ),
                      destinations: const [
                        NavigationDestination(
                          icon: Icon(Icons.home_outlined),
                          selectedIcon: Icon(Icons.home),
                          label: 'Overview',
                        ),
                        NavigationDestination(
                          icon: Icon(Icons.folder_outlined),
                          label: 'Projects',
                        ),
                        NavigationDestination(
                          icon: Icon(Icons.smart_toy_outlined),
                          label: 'Agents',
                        ),
                        NavigationDestination(
                          icon: Icon(Icons.account_tree_outlined),
                          label: 'Team',
                        ),
                        NavigationDestination(
                          icon: Icon(Icons.settings_outlined),
                          label: 'Settings',
                        ),
                      ],
                    ),
              body: Row(
                children: [
                  if (desktop)
                    Container(
                      width: 68,
                      decoration: BoxDecoration(
                        color: Theme.of(
                          context,
                        ).colorScheme.surfaceContainerLow,
                        border: Border(
                          right: BorderSide(
                            color: Theme.of(context).dividerColor,
                          ),
                        ),
                      ),
                      child: Column(
                        children: [
                          const SizedBox(height: 18),
                          const Tooltip(
                            message: 'Herdr organizations',
                            child: Icon(Icons.hub_outlined, size: 25),
                          ),
                          const SizedBox(height: 24),
                          Expanded(
                            child: ListView(
                              children: [
                                for (final item in orgs)
                                  Padding(
                                    padding: const EdgeInsets.fromLTRB(
                                      9,
                                      0,
                                      9,
                                      12,
                                    ),
                                    child: Tooltip(
                                      message: '${item['name']}',
                                      child: Semantics(
                                        button: true,
                                        label: 'Switch to ${item['name']}',
                                        selected: org?['id'] == item['id'],
                                        child: InkWell(
                                          borderRadius: BorderRadius.circular(
                                            13,
                                          ),
                                          onTap: () {
                                            connection
                                                    .selectedOrganization
                                                    .value =
                                                item['id'];
                                            navigate(OrganizationRoute());
                                          },
                                          child: AnimatedContainer(
                                            duration: const Duration(
                                              milliseconds: 150,
                                            ),
                                            height: desktop ? 46 : 38,
                                            decoration: BoxDecoration(
                                              color: org?['id'] == item['id']
                                                  ? Theme.of(
                                                      context,
                                                    ).colorScheme.primary
                                                  : Theme.of(context)
                                                        .colorScheme
                                                        .surfaceContainerHighest,
                                              borderRadius:
                                                  BorderRadius.circular(13),
                                              border: Border.all(
                                                color: org?['id'] == item['id']
                                                    ? Theme.of(
                                                        context,
                                                      ).colorScheme.primary
                                                    : Theme.of(
                                                        context,
                                                      ).dividerColor,
                                              ),
                                            ),
                                            alignment: Alignment.center,
                                            child: Text(
                                              '${item['name']}'.trim().isEmpty
                                                  ? '?'
                                                  : '${item['name']}'
                                                        .trim()
                                                        .substring(0, 1)
                                                        .toUpperCase(),
                                              style: TextStyle(
                                                fontSize: 19,
                                                fontWeight: FontWeight.w700,
                                                color: org?['id'] == item['id']
                                                    ? Theme.of(
                                                        context,
                                                      ).colorScheme.onPrimary
                                                    : Theme.of(
                                                        context,
                                                      ).colorScheme.onSurface,
                                              ),
                                            ),
                                          ),
                                        ),
                                      ),
                                    ),
                                  ),
                              ],
                            ),
                          ),
                          Padding(
                            padding: const EdgeInsets.only(bottom: 18),
                            child: IconButton(
                              tooltip: 'Manage organizations',
                              onPressed: () => navigate(OrganizationRoute()),
                              icon: const Icon(Icons.add),
                            ),
                          ),
                        ],
                      ),
                    ),
                  if (desktop) navigation(profiles, groups),
                  Expanded(
                    child: Column(
                      children: [
                        Container(
                          height: 56,
                          decoration: BoxDecoration(
                            color: Theme.of(context).colorScheme.surface,
                            border: Border(
                              bottom: BorderSide(
                                color: Theme.of(context).dividerColor,
                              ),
                            ),
                          ),
                          child: Row(
                            children: [
                              if (!desktop)
                                Builder(
                                  builder: (context) => IconButton(
                                    tooltip: 'Open navigation',
                                    onPressed: () =>
                                        Scaffold.of(context).openDrawer(),
                                    icon: const Icon(Icons.menu, size: 20),
                                  ),
                                ),
                              const SizedBox(width: 22),
                              Expanded(
                                child: Text(
                                  widget.section.startsWith('group:')
                                      ? 'Discussion group'
                                      : widget.section == 'organization'
                                      ? 'Organization'
                                      : widget.section == 'dashboard'
                                      ? 'HERDR'
                                      : widget.section,
                                  style: const TextStyle(
                                    fontSize: 12,
                                    fontWeight: FontWeight.w700,
                                    letterSpacing: 1,
                                  ),
                                ),
                              ),
                              if (desktop && org != null)
                                Text(
                                  '${org['name']}',
                                  style: TextStyle(
                                    fontSize: 12,
                                    color: Theme.of(
                                      context,
                                    ).colorScheme.onSurfaceVariant,
                                  ),
                                ),
                              const ThemeMenu(),
                              noticeBadge(),
                              const SizedBox(width: 22),
                            ],
                          ),
                        ),
                        Expanded(child: widget.child),
                      ],
                    ),
                  ),
                ],
              ),
            );
          },
        );
      },
    ),
  );
}
