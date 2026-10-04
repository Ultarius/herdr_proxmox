import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';

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

  @override
  void initState() {
    super.initState();
    subscription = connection.stream.listen((_) => load());
    load();
  }

  Future<void> load() async {
    if (loading ||
        !connection.state.connected ||
        connection.organizationDirectory.value['loaded'] == true)
      return;
    loading = true;
    try {
      await connection.request('organizations');
    } catch (_) {
      /* The active page reports connection errors. */
    } finally {
      loading = false;
    }
  }

  @override
  void dispose() {
    subscription?.cancel();
    super.dispose();
  }

  Widget navigation(
    List<Map<String, dynamic>> profiles,
    List<Map<String, dynamic>> groups,
  ) => Container(
    width: 224,
    decoration: const BoxDecoration(
      color: Color(0xff0b0b0b),
      border: Border(right: BorderSide(color: Color(0xff252525))),
    ),
    child: Material(
      color: const Color(0xff0b0b0b),
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
          nav('Terminal logs', Icons.subject, LogsRoute(), 'logs'),
          nav('CLI accounts', Icons.terminal, CliSetupRoute(), 'configuration'),
          heading('AGENTS'),
          if (profiles.isEmpty)
            const Padding(
              padding: EdgeInsets.all(10),
              child: Text(
                'Hire your first agent',
                style: TextStyle(color: Color(0xff777777), fontSize: 12),
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
              onTap: () => widget.coordinator.navigate(OrganizationRoute()),
            ),
          heading('GROUPS'),
          if (groups.isEmpty)
            const Padding(
              padding: EdgeInsets.all(10),
              child: Text(
                'No discussion groups yet',
                style: TextStyle(color: Color(0xff777777), fontSize: 12),
              ),
            ),
          for (final group in groups)
            nav(
              '${group['name']}',
              Icons.forum_outlined,
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
      style: const TextStyle(
        fontSize: 10,
        letterSpacing: 1.5,
        color: Color(0xff777777),
      ),
    ),
  );
  Widget nav(String label, IconData icon, AppRoute route, String section) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: ListTile(
          dense: true,
          selected: widget.section == section,
          selectedTileColor: const Color(0xff1b1b1b),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
          contentPadding: const EdgeInsets.symmetric(horizontal: 10),
          leading: Icon(icon, size: 17),
          title: Text(label, style: const TextStyle(fontSize: 12)),
          onTap: () => widget.coordinator.navigate(route),
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
        final profiles = List<Map<String, dynamic>>.from(
          directory['profiles'],
        ).where((item) => item['organization_id'] == org?['id']).toList();
        final groups = List<Map<String, dynamic>>.from(
          directory['groups'] ?? [],
        ).where((item) => item['organization_id'] == org?['id']).toList();
        return LayoutBuilder(
          builder: (context, constraints) {
            final desktop = constraints.maxWidth >= 1000;
            return Scaffold(
              drawer: desktop
                  ? null
                  : Drawer(child: navigation(profiles, groups)),
              body: Row(
                children: [
                  Container(
                    width: desktop ? 68 : 56,
                    decoration: const BoxDecoration(
                      color: Color(0xff090909),
                      border: Border(
                        right: BorderSide(color: Color(0xff252525)),
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
                                        borderRadius: BorderRadius.circular(13),
                                        onTap: () {
                                          connection
                                                  .selectedOrganization
                                                  .value =
                                              item['id'];
                                          widget.coordinator.navigate(
                                            OrganizationRoute(),
                                          );
                                        },
                                        child: AnimatedContainer(
                                          duration: const Duration(
                                            milliseconds: 150,
                                          ),
                                          height: desktop ? 46 : 38,
                                          decoration: BoxDecoration(
                                            color: org?['id'] == item['id']
                                                ? const Color(0xffff7917)
                                                : const Color(0xff202020),
                                            borderRadius: BorderRadius.circular(
                                              13,
                                            ),
                                            border: Border.all(
                                              color: org?['id'] == item['id']
                                                  ? const Color(0xffffa14d)
                                                  : const Color(0xff343434),
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
                                            style: const TextStyle(
                                              fontSize: 19,
                                              fontWeight: FontWeight.w700,
                                              color: Colors.white,
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
                            onPressed: () => widget.coordinator.navigate(
                              OrganizationRoute(),
                            ),
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
                          decoration: const BoxDecoration(
                            color: Color(0xff0b0b0b),
                            border: Border(
                              bottom: BorderSide(color: Color(0xff252525)),
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
                                      ? 'DISCUSSION GROUP'
                                      : widget.section == 'organization'
                                      ? 'ORG CHART'
                                      : widget.section.toUpperCase(),
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
                                  style: const TextStyle(
                                    fontSize: 12,
                                    color: Color(0xff999999),
                                  ),
                                ),
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
