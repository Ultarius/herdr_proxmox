import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';
import 'clone_project_card.dart';
import 'resource_panel.dart';

void main() {
  BlocScope.register<DashboardBloc>(
    () => DashboardBloc()..restoreSession(),
    lifecycle: BlocLifecycle.permanent,
  );
  runApp(
    MaterialApp.router(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        colorScheme:
            ColorScheme.fromSeed(
              seedColor: const Color(0xffff7917),
              brightness: Brightness.dark,
            ).copyWith(
              primary: const Color(0xffff7917),
              onPrimary: Colors.white,
              secondaryContainer: const Color(0xff252525),
              onSecondaryContainer: Colors.white,
            ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            foregroundColor: Colors.black,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(7),
            ),
          ),
        ),
        outlinedButtonTheme: OutlinedButtonThemeData(
          style: OutlinedButton.styleFrom(
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(7),
            ),
          ),
        ),
        scaffoldBackgroundColor: const Color(0xff0b0b0b),
        cardTheme: const CardThemeData(
          color: Color(0xff161616),
          elevation: 0,
          margin: EdgeInsets.symmetric(vertical: 6),
        ),
        dividerColor: const Color(0xff282828),
        useMaterial3: true,
      ),
      routerConfig: AppCoordinator(),
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
                padding: const EdgeInsets.all(28),
                children: [
                  Text(
                    'Your agents, at a glance.',
                    style: Theme.of(context).textTheme.headlineLarge,
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Manage workspaces here. Open a terminal over SSH to work with your herd.',
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
                    const ResourcePanel(),
                    Wrap(
                      spacing: 12,
                      runSpacing: 12,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Chip(
                          label: Text(
                            refreshed == null
                                ? 'Connecting'
                                : 'Last read: ${refreshed.toLocal().toString().substring(11, 19)}',
                          ),
                        ),
                        Chip(label: Text('Herdr: ${state.serverStatus}')),
                        if (state.serverStatus == 'stopped')
                          FilledButton.icon(
                            onPressed: busy
                                ? null
                                : () => bloc.send(
                                    DashboardCommand('start-server'),
                                  ),
                            icon: const Icon(Icons.play_arrow),
                            label: Text(
                              busy ? 'Starting Herdr…' : 'Start Herdr',
                            ),
                          ),
                        OutlinedButton(
                          onPressed: busy ? null : refresh,
                          child: const Text('Refresh'),
                        ),
                        TextButton(
                          onPressed: () {
                            bloc.disconnect();
                          },
                          child: const Text('Disconnect'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    if (state.serverStatus == 'stopped')
                      const Text(
                        'Herdr is stopped. Select Start Herdr to launch it in this container; no SSH connection is needed.',
                      ),
                    Wrap(
                      spacing: 12,
                      children: [
                        TextButton(
                          onPressed: () =>
                              widget.coordinator.navigate(DashboardRoute()),
                          child: const Text('Workspaces'),
                        ),
                        TextButton(
                          onPressed: () =>
                              widget.coordinator.navigate(AgentsRoute()),
                          child: const Text('Agents'),
                        ),
                        TextButton(
                          onPressed: () =>
                              widget.coordinator.navigate(OrganizationRoute()),
                          child: const Text('Organization'),
                        ),
                        TextButton(
                          onPressed: () =>
                              widget.coordinator.navigate(CliSetupRoute()),
                          child: const Text('CLI configuration'),
                        ),
                        TextButton(
                          onPressed: () =>
                              widget.coordinator.navigate(LogsRoute()),
                          child: const Text('Saved logs'),
                        ),
                      ],
                    ),
                    if (!widget.agentsOnly) ...[
                      const SizedBox(height: 24),
                      Text(
                        'Workspaces',
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                      const SizedBox(height: 12),
                      if (workspaces.isEmpty &&
                          error == null &&
                          refreshed != null)
                        const Text(
                          'No workspaces yet. Create your first below.',
                        ),
                      for (final workspace in workspaces)
                        Card(
                          child: ListTile(
                            title: Text(
                              '${workspace['label'] ?? workspace['workspace_id']}',
                            ),
                            subtitle: Text(
                              '${workspace['cwd'] ?? workspace['workspace_id']}',
                            ),
                            trailing: Wrap(
                              children: [
                                IconButton(
                                  tooltip: 'Focus in Herdr',
                                  onPressed: busy
                                      ? null
                                      : () => action('focus', {
                                          'id': workspace['workspace_id'],
                                        }),
                                  icon: const Icon(Icons.center_focus_strong),
                                ),
                                IconButton(
                                  tooltip: 'Rename',
                                  onPressed: busy
                                      ? null
                                      : () => rename(workspace),
                                  icon: const Icon(Icons.edit_outlined),
                                ),
                              ],
                            ),
                          ),
                        ),
                      const SizedBox(height: 16),
                      Card(
                        child: ExpansionTile(
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
                              controller: label,
                              decoration: const InputDecoration(
                                labelText: 'New workspace name',
                              ),
                            ),
                            TextField(
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
                    Text(
                      'Agents',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 12),
                    if (agents.isEmpty && error == null && refreshed != null)
                      const Text(
                        'No agents detected. Launch one in a Herdr pane over SSH.',
                      ),
                    for (final agent in agents)
                      Card(
                        child: ListTile(
                          leading: Icon(
                            agent['entity_type'] == 'group'
                                ? Icons.forum_outlined
                                : Icons.smart_toy_outlined,
                          ),
                          title: Text(
                            '${agent['display_name'] ?? agent['name'] ?? agent['agent'] ?? agent['pane_id'] ?? 'Agent'}${agent['entity_type'] == 'group' ? ' · Group' : ''}',
                          ),
                          subtitle: SelectableText(
                            '${agent['name'] == null ? '' : 'Herdr ID: ${agent['name']} · '}${agent['pane_id'] ?? ''}',
                          ),
                          trailing: Chip(
                            label: Text(
                              '${agent['agent_status'] ?? agent['state'] ?? agent['status'] ?? 'unknown'}',
                            ),
                          ),
                        ),
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
