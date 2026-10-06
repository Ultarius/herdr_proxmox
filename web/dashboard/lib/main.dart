import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';
import 'clone_project_card.dart';
import 'resource_panel.dart';
import 'overview_panels.dart';
import 'app_theme.dart';

void main() {
  BlocScope.register<DashboardBloc>(
    () => DashboardBloc()..restoreSession(),
    lifecycle: BlocLifecycle.permanent,
  );
  runApp(const HerdrApp());
}

class HerdrApp extends StatefulWidget {
  const HerdrApp({super.key});
  @override
  State<HerdrApp> createState() => _HerdrAppState();
}

class _HerdrAppState extends State<HerdrApp> {
  final coordinator = AppCoordinator();
  @override
  void dispose() {
    coordinator.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<ThemeMode>(
    valueListenable: themeMode,
    builder: (context, mode, _) => MaterialApp.router(
      debugShowCheckedModeBanner: false,
      theme: dashboardTheme(Brightness.light),
      darkTheme: dashboardTheme(Brightness.dark),
      themeMode: mode,
      routerConfig: coordinator,
    ),
  );
}

class Dashboard extends StatefulWidget {
  const Dashboard({
    super.key,
    required this.coordinator,
    this.agentsOnly = false,
  });
  final AppCoordinator coordinator;
  final bool agentsOnly;
  @override
  State<Dashboard> createState() => _DashboardState();
}

class _DashboardState extends State<Dashboard> {
  final setupKey = GlobalKey();
  final setupController = ExpansibleController();
  final token = TextEditingController();
  final label = TextEditingController();
  final cwd = TextEditingController(text: '/home/herdr/projects');
  DashboardBloc get bloc => BlocScope.get<DashboardBloc>();
  Future<void> refresh() => bloc.send(DashboardCommand('refresh'));
  Future<void> action(String name, Map<String, dynamic> body) =>
      bloc.send(DashboardCommand(name, body));

  Future<void> rename(Map<String, dynamic> workspace) async {
    final controller = TextEditingController(
      text: '${workspace['label'] ?? ''}',
    );
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Rename workspace'),
        content: TextField(controller: controller, autofocus: true),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    // The dialog route may animate after its result arrives.
    if (value != null && value.trim().isNotEmpty) {
      await action('rename', {
        'id': workspace['workspace_id'],
        'label': value.trim(),
      });
    }
  }

  @override
  void dispose() {
    setupController.dispose();
    token.dispose();
    label.dispose();
    cwd.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return JuiceBuilder<DashboardBloc>(
      builder: (context, bloc, status) {
        final state = bloc.state;
        final busy = state.busy;
        final error = state.error;
        final workspaces = state.workspaces;
        final agents = state.agents;
        final refreshed = state.refreshed;
        return Scaffold(
          body: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1200),
              child: ListView(
                padding: EdgeInsets.all(
                  MediaQuery.sizeOf(context).width < 650 ? 16 : 28,
                ),
                children: [
                  Wrap(
                    alignment: WrapAlignment.spaceBetween,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    spacing: 20,
                    runSpacing: 16,
                    children: [
                      Text(
                        widget.agentsOnly ? 'Agents' : 'Overview',
                        style: Theme.of(context).textTheme.headlineLarge
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      if (state.connected && !widget.agentsOnly)
                        FilledButton.icon(
                          onPressed: () {
                            setupController.expand();
                            WidgetsBinding.instance.addPostFrameCallback((_) {
                              final target = setupKey.currentContext;
                              if (target != null)
                                Scrollable.ensureVisible(
                                  target,
                                  duration: const Duration(milliseconds: 250),
                                );
                            });
                          },
                          icon: const Icon(Icons.add),
                          label: const Text('Create workspace'),
                        ),
                    ],
                  ),
                  const SizedBox(height: 24),
                  if (!state.connected) ...[
                    const Text('Connect to this container'),
                    const SizedBox(height: 12),
                    TextField(
                      controller: token,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'Dashboard access token',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      'Stay signed in for 7 days. Sign out on shared devices.',
                    ),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: () {
                        if (token.text.trim().isEmpty) return;
                        bloc.signIn(token.text.trim());
                        token.clear();
                      },
                      child: const Text('Connect'),
                    ),
                  ] else ...[
                    if (!widget.agentsOnly) ...[
                      OverviewSummary(
                        agents: agents.length,
                        workspaces: workspaces.length,
                        connected: state.serverStatus == 'running',
                        onAgents: () =>
                            widget.coordinator.navigate(AgentsRoute()),
                        onWorkspaces: () =>
                            widget.coordinator.navigate(ExplorerRoute()),
                        onConnection: refresh,
                      ),
                      const SizedBox(height: 24),
                    ],
                    if (state.serverStatus == 'stopped')
                      FilledButton.icon(
                        onPressed: busy
                            ? null
                            : () => bloc.send(DashboardCommand('start-server')),
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Start Herdr'),
                      ),
                    AgentPanel(
                      agents: agents,
                      onManage: () =>
                          widget.coordinator.navigate(OrganizationRoute()),
                    ),
                    const SizedBox(height: 24),
                    if (!widget.agentsOnly) ...[
                      WorkspacePanel(
                        workspaces: workspaces,
                        agents: agents,
                        busy: busy,
                        onFocus: (workspace) =>
                            action('focus', {'id': workspace['workspace_id']}),
                        onRename: rename,
                      ),
                      const SizedBox(height: 16),
                      const ResourcePanel(),
                      const SizedBox(height: 24),
                      Card(
                        key: setupKey,
                        child: ExpansionTile(
                          controller: setupController,
                          key: const PageStorageKey('dashboard-project-setup'),
                          leading: const Icon(Icons.create_new_folder_outlined),
                          title: const Text('Add a project or workspace'),
                          subtitle: const Text(
                            'Clone a repository or use an existing directory',
                          ),
                          childrenPadding: const EdgeInsets.all(16),
                          expandedCrossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            TextField(
                              // TextField scroll offsets must not share the
                              // ExpansionTile's stored boolean expansion state.
                              key: const PageStorageKey('workspace-name-field'),
                              controller: label,
                              decoration: const InputDecoration(
                                labelText: 'New workspace name',
                              ),
                            ),
                            TextField(
                              key: const PageStorageKey('workspace-path-field'),
                              controller: cwd,
                              decoration: const InputDecoration(
                                labelText:
                                    'Existing project directory inside ~/projects',
                              ),
                            ),
                            const SizedBox(height: 12),
                            Align(
                              alignment: Alignment.centerLeft,
                              child: FilledButton.icon(
                                onPressed: busy
                                    ? null
                                    : () => action('create', {
                                        'label': label.text.trim(),
                                        'cwd': cwd.text.trim(),
                                      }),
                                icon: const Icon(Icons.add),
                                label: const Text('Create workspace'),
                              ),
                            ),
                            const SizedBox(height: 24),
                            CloneProjectCard(
                              onCloned: (path) => cwd.text = path,
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 32),
                    ],
                    const SizedBox(height: 16),
                    if (widget.agentsOnly) const ResourcePanel(),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            refreshed == null
                                ? 'Herdr: ${state.serverStatus} · Waiting for first update'
                                : 'Herdr: ${state.serverStatus} · Updated ${refreshed.toLocal().toString().substring(11, 19)}',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ),
                        TextButton.icon(
                          onPressed: busy ? null : refresh,
                          icon: const Icon(Icons.refresh, size: 18),
                          label: const Text('Refresh'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 24),
                    const Card(
                      child: ExpansionTile(
                        title: Text('Terminal access'),
                        leading: Icon(Icons.terminal),
                        childrenPadding: EdgeInsets.all(16),
                        children: [
                          SelectableText(
                            'ssh herdr@<container-ip>\nThen: cd ~/projects && herdr',
                            key: PageStorageKey('dashboard-terminal-help'),
                          ),
                        ],
                      ),
                    ),
                  ],
                  if (busy) const LinearProgressIndicator(),
                  if (error != null)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      child: Text(
                        error,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
