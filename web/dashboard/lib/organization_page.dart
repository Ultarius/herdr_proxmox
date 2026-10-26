import 'package:herdr_dashboard_state/organization_tools_bloc.dart';
import 'organization_automation.dart';
import 'package:herdr_dashboard_state/team_template_draft.dart';
import 'package:herdr_dashboard_state/template_library_state.dart';
import 'package:herdr_dashboard_state/project_directory_choices.dart';
import 'dart:convert';
import 'artifact_download.dart';
import 'permission_options.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'request_id.dart';
import 'routes.dart';
import 'org_chart.dart';
import 'collaboration_panel.dart';
import 'model_catalog.dart';
import 'remove_entry_dialog.dart';
import 'configuration_transfer.dart';

class OrganizationPage extends StatefulWidget {
  const OrganizationPage({
    super.key,
    required this.coordinator,
    this.initialView = 'chart',
  });
  final AppCoordinator coordinator;
  final String initialView;
  @override
  State<OrganizationPage> createState() => _OrganizationPageState();
}

class _OrganizationPageState extends State<OrganizationPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final OrganizationBloc bloc = OrganizationBloc(connection);
  late String view = widget.initialView;
  bool cleaningSessions = false;
  // Keep the exact request across lost responses so a retried restart or close
  // reconciles instead of repeating the pane operation.
  final Map<String, String> sessionRequests = {};
  late final OrganizationToolsBloc tools;
  StreamSubscription? toolsSubscription;
  ModelCatalog get catalog => ModelCatalog.fromJson(tools.catalog);
  String? get selected => connection.selectedOrganization.value;
  set selected(String? value) => connection.selectedOrganization.value = value;
  void selectionChanged() {
    if (mounted) setState(() {});
  }

  /// The model dropdowns need a catalog before a hire form opens. A gateway
  /// without one leaves the manual entry fields in place instead.
  Future<void> loadCatalog() async {
    try {
      await tools.loadCatalog();
    } catch (_) {
      /* Manual model entry remains available. */
    }
  }

  @override
  void initState() {
    super.initState();
    tools = OrganizationToolsBloc(connection);
    toolsSubscription = tools.stream.listen((_) {
      if (mounted) setState(() {});
    });
    connection.selectedOrganization.addListener(selectionChanged);
    bloc.send(OrganizationCommand('refresh'));
    bloc.startPolling();
    loadCatalog();
  }

  @override
  void dispose() {
    toolsSubscription?.cancel();
    tools.dispose();
    connection.selectedOrganization.removeListener(selectionChanged);
    bloc.dispose();
    super.dispose();
  }

  Future<void> submit(String action, Map<String, dynamic> body) =>
      bloc.send(OrganizationCommand(action, body));

  Future<void> editOrganization([Map<String, dynamic>? organization]) async {
    final epoch = connection.generation;
    Map<String, dynamic>? templates;
    var projectFolders = <String, String>{};
    String? projectFolderError;
    if (organization == null) {
      try {
        templates = Map<String, dynamic>.from(
          await tools.request('organizations/templates'),
        );
      } catch (e) {
        templates = {'error': 'Role presets could not load: $e'};
      }
      try {
        projectFolders = projectDirectoryChoices(
          Map<String, dynamic>.from(
            await tools.request('projects/browse', {'path': ''}),
          ),
        );
      } catch (e) {
        projectFolderError = 'Project folders could not load: $e';
      }
    }
    if (!mounted || epoch != connection.generation) return;
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => OrganizationForm(
        organization: organization,
        templates: templates,
        projectFolders: projectFolders,
        projectFolderError: projectFolderError,
        preview: (body) async {
          final result = Map<String, dynamic>.from(
            await tools.request('organizations/templates', body),
          );
          if (epoch != connection.generation)
            throw StateError(
              'Connection changed. Reopen the organization form.',
            );
          return result;
        },
        library: (body) async {
          if (epoch != connection.generation || !connection.state.connected)
            throw StateError(
              'Connection changed. Reopen the organization form.',
            );
          final result = Map<String, dynamic>.from(
            await tools.request('organizations/templates/library', body),
          );
          if (epoch != connection.generation)
            throw StateError(
              'Connection changed. Reopen the organization form.',
            );
          return result;
        },
        canManageTemplates: connection.operator.value['role'] == 'admin',
      ),
    );
    if (values != null && mounted && epoch == connection.generation) {
      final previous = bloc.state.organizations.map((org) => org['id']).toSet();
      await submit('save', values);
      if (mounted && organization == null && bloc.state.error == null) {
        final created = bloc.state.organizations
            .where((org) => !previous.contains(org['id']))
            .firstOrNull;
        if (created != null) selected = created['id'];
      }
    }
  }

  Future<void> hire(
    Map<String, dynamic> org, [
    Map<String, dynamic>? profile,
  ]) async {
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => HireForm(
        organization: org,
        profile: profile,
        catalog: catalog,
        profiles: bloc.state.profiles
            .where(
              (p) => p['organization_id'] == org['id'] && p['group_id'] == null,
            )
            .toList(),
      ),
    );
    if (values != null && mounted) {
      await submit('hire', values);
      if (mounted) setState(() => view = 'team');
    }
  }

  Future<void> delegate(Map<String, dynamic> org) async {
    final profiles = bloc.state.profiles
        .where(
          (p) =>
              p['organization_id'] == org['id'] &&
              p['group_id'] == null &&
              bloc.state.jobs.any(
                (j) =>
                    j['kind'] == 'launch' &&
                    j['profile_id'] == p['id'] &&
                    j['state'] == 'persona_sent',
              ),
        )
        .toList();
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => DelegationForm(organization: org, profiles: profiles),
    );
    if (values != null && mounted) await submit('delegate', values);
  }

  Future<void> cleanupSessions(Iterable<Map<String, dynamic>> jobs) async {
    final obsolete = jobs
        .where(
          (j) =>
              j['kind'] == 'launch' &&
              j['state'] == 'released' &&
              j['session_closed_at'] == null,
        )
        .toList();
    final chosen = <String>{};
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Review obsolete sessions'),
          content: SizedBox(
            width: 600,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Only selected released sessions are considered. Busy, blocked, changed or unidentified sessions are skipped. Repository files remain intact.',
                  ),
                  for (final job in obsolete)
                    CheckboxListTile(
                      title: Text(
                        '${job['profile']?['name'] ?? bloc.state.profiles.where((p) => p['id'] == job['profile_id']).map((p) => p['name']).firstOrNull ?? job['profile_id']}',
                      ),
                      subtitle: Text(
                        '${job['alias']} · ${job['pane_id'] ?? 'Pane identity missing'}\nStarted ${job['created_at'] ?? 'at an unknown time'}',
                      ),
                      value: chosen.contains(job['id']),
                      onChanged: (value) => update(() {
                        if (value == true) {
                          chosen.add(job['id']);
                        } else {
                          chosen.remove(job['id']);
                        }
                      }),
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: chosen.isEmpty
                  ? null
                  : () => Navigator.pop(context, true),
              child: const Text('Archive and close selected sessions'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => cleaningSessions = true);
    try {
      final result = await tools.execute(
        OrganizationCommand('session', {
          'organization_id': selected,
          'mode': 'cleanup',
          'job_ids': chosen.toList(),
          'inspected': true,
        }),
      );
      if (!mounted) return;
      final names = {
        for (final job in obsolete)
          job['id']:
              '${job['profile']?['name'] ?? bloc.state.profiles.where((p) => p['id'] == job['profile_id']).map((p) => p['name']).firstOrNull ?? job['profile_id']}',
      };
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Session cleanup results'),
          content: SingleChildScrollView(
            child: Text(
              (result['results'] as List)
                  .map(
                    (r) =>
                        '${names[r['id']] ?? r['id']}: ${r['state']}${r['reason'] == null ? '' : ' · ${r['reason']}'}',
                  )
                  .join('\n'),
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
      bloc.send(OrganizationCommand('refresh'));
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$error')));
    } finally {
      if (mounted) setState(() => cleaningSessions = false);
    }
  }

  Future<void> showArchives() async {
    final epoch = connection.generation;
    final organizationId = selected;
    bool current() =>
        mounted && epoch == connection.generation && selected == organizationId;
    try {
      final response = await tools.request('organizations/session', {
        'mode': 'archives',
        'organization_id': organizationId,
      });
      if (!current()) return;
      final archives = [
        for (final entry in response['archives'] as List? ?? [])
          if (entry is Map) entry,
      ];
      final chosen = await showDialog<Map>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Archived sessions'),
          content: SizedBox(
            width: 720,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (archives.isEmpty)
                    const Text(
                      'No session archives yet. Archive and close a verified idle session first.',
                    ),
                  for (final archive in archives)
                    ListTile(
                      title: Text(
                        '${archive['identity']?['name'] ?? 'Historical agent'}',
                      ),
                      subtitle: Text(
                        '${archive['alias']}${archive['native_resume_available'] == true ? ' \u00b7 native resume available' : ''}\nArchived ${archive['archived_at']}${archive['terminal_unavailable'] != null ? '\nTerminal history unavailable; saved records only.' : ''}',
                      ),
                      trailing: TextButton(
                        onPressed: () => Navigator.pop(context, archive),
                        child: const Text('View history'),
                      ),
                    ),
                ],
              ),
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
      if (chosen == null || !current()) return;
      final data = await tools.request('organizations/session', {
        'mode': 'view_archive',
        'organization_id': organizationId,
        'archive_id': chosen['id'],
      });
      if (!current()) return;
      final nativeResume = data['native_resume_available'] == true;
      final action = await showDialog<String>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(
            'Archived conversation: ${data['identity']?['name'] ?? 'agent'}',
          ),
          content: SizedBox(
            width: 800,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    nativeResume
                        ? 'Bounded historical evidence, not a complete provider transcript. This archive records a verified native resume reference; resuming restores that conversation binding and is not completed work.'
                        : 'Bounded historical evidence, not a complete provider transcript. Continuing creates a fresh conversation and waits for instructions.',
                  ),
                  SelectableText(
                    'Checkout: ${data['worktree_path'] ?? data['source_project']}\nBranch: ${data['worktree_branch']}\nTask: ${data['task_id']}',
                  ),
                  if (data['terminal_capture']?['forced_stop'] == true)
                    const Text(
                      'Forced stop: visible screen only (up to 160 lines). Earlier history is omitted; the reply may be incomplete.',
                    ),
                  if (data['terminal_capture']?['character_truncated'] == true)
                    const Text(
                      'Terminal output also exceeded the archive character limit.',
                    ),
                  SelectableText(
                    '${data['terminal'] ?? data['terminal_unavailable'] ?? 'Terminal unavailable.'}',
                  ),
                  for (final message in data['messages'] as List? ?? [])
                    SelectableText('$message'),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => downloadArtifact(
                jsonEncode(data),
                'session-${data['id']}.json',
              ),
              child: const Text('Download archive'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close'),
            ),
            if (nativeResume)
              OutlinedButton(
                onPressed: () => Navigator.pop(context, 'resume'),
                child: const Text('Resume original conversation'),
              ),
            FilledButton(
              onPressed: () => Navigator.pop(context, 'continue'),
              child: const Text('Continue with saved context'),
            ),
          ],
        ),
      );
      if (action != null && current())
        await manageSession({
          'id': data['run_id'],
          'organization_id': organizationId,
          'archive_id': data['id'],
          'alias': data['alias'],
          'pane_id': data['pane_id'],
          'profile': data['identity'],
        }, action);
    } catch (error) {
      if (current())
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> manageSession(Map<String, dynamic> job, String mode) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          mode == 'restart'
              ? 'Restart agent session?'
              : mode == 'continue'
              ? 'Continue archived context?'
              : mode == 'resume'
              ? 'Resume provider conversation?'
              : 'Archive and close session?',
        ),
        content: Text(
          '${job['profile']?['name'] ?? job['profile_id']}\nAlias: ${job['alias']}\nPane: ${job['pane_id']}\n'
          '${mode == 'restart'
              ? 'Restarts a fresh session in the retained checkout with the current agent profile. It waits for instructions and does not replay the task.'
              : mode == 'continue'
              ? 'Starts a fresh session in the retained checkout with the archived evidence and the current agent profile. It does not resume the provider conversation or replay the task.'
              : mode == 'resume'
              ? 'Restores the archived provider conversation binding in the retained checkout. Restoration is not completed work; the session waits for instructions.'
              : 'Preserves available evidence before closing a verified idle session. If capture fails, the pane stays open.'}',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('I inspected the idle session'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final selection = 'session:${job['id']}:$mode';
    final request = sessionRequests.putIfAbsent(selection, newRequestId);
    try {
      await tools.request('organizations/session', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'mode': mode,
        'inspected': true,
        'request_id': request,
        if (job['archive_id'] != null) 'archive_id': job['archive_id'],
      });
      sessionRequests.remove(selection);
      if (!mounted) return;
      bloc.send(OrganizationCommand('refresh'));
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> release(Map<String, dynamic> job) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Release run binding?'),
        content: const Text(
          'This allows another launch. It leaves the terminal and process running. Use Archive and close session to stop an idle session and avoid duplicate work.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Release binding'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted)
      await submit('release', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
      });
  }

  Future<void> report(Map<String, dynamic> job) async {
    final value = await showDialog<String>(
      context: context,
      builder: (_) => const ReportForm(),
    );
    if (value != null && mounted)
      await submit('report', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'result': value,
      });
  }

  Future<void> resolveDelegation(
    Map<String, dynamic> job,
    String decision,
  ) async {
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => ReportForm(
        title: decision == 'complete'
            ? 'Accept inspected delegation completion'
            : 'Cancel inspected delegation',
        label: 'Inspection evidence and reason (releases the work reservation)',
        button: 'Confirm resolution',
        limit: 2000,
      ),
    );
    if (reason != null && mounted)
      await submit('resolve_delegation', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'decision': decision,
        'inspected': true,
        'reason': reason,
      });
  }

  @override
  Widget build(BuildContext context) => JuiceBuilder<DashboardBloc>(
    builder: (context, connection, status) {
      if (!connection.state.connected)
        return Scaffold(
          body: Center(
            child: FilledButton(
              onPressed: () => widget.coordinator.navigate(DashboardRoute()),
              child: const Text('Connect on the dashboard'),
            ),
          ),
        );
      return StreamBuilder(
        stream: bloc.stream,
        builder: (context, _) {
          final state = bloc.state;
          final orgs = state.organizations;
          final org = orgs.isEmpty
              ? null
              : orgs.firstWhere(
                  (o) => o['id'] == selected,
                  orElse: () => orgs.first,
                );
          final profiles = state.profiles
              .where(
                (p) =>
                    p['organization_id'] == org?['id'] && p['group_id'] == null,
              )
              .toList();
          final jobs = state.jobs
              .where((j) => j['organization_id'] == org?['id'])
              .toList()
              .reversed;
          return Scaffold(
            body: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 1200),
                child: ListView(
                  padding: const EdgeInsets.all(28),
                  children: [
                    if (MediaQuery.sizeOf(context).width < 1000) ...[
                      Text(
                        'Your organization',
                        style: Theme.of(context).textTheme.headlineLarge,
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'Hire a persistent team, give each agent a persona, and delegate work. Configure accounts on the CLI configuration page; use SSH for agent work.',
                      ),
                      const SizedBox(height: 16),
                      Wrap(
                        spacing: 12,
                        runSpacing: 8,
                        children: [
                          TextButton(
                            onPressed: () =>
                                widget.coordinator.navigate(DashboardRoute()),
                            child: const Text('Workspaces'),
                          ),
                          TextButton(
                            onPressed: () =>
                                widget.coordinator.navigate(AgentsRoute()),
                            child: const Text('Live agents'),
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
                          OutlinedButton(
                            onPressed: state.busy
                                ? null
                                : () =>
                                      bloc.send(OrganizationCommand('refresh')),
                            child: const Text('Refresh organization'),
                          ),
                          TextButton(
                            onPressed: () => connection.disconnect(),
                            child: const Text('Disconnect'),
                          ),
                        ],
                      ),
                    ],
                    if (state.busy) const LinearProgressIndicator(),
                    if (state.error != null) ...[
                      Text(
                        state.error!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                      if (bloc.pending != null)
                        TextButton(
                          onPressed: state.busy
                              ? null
                              : () => bloc.send(bloc.pending!),
                          child: const Text('Retry last request'),
                        ),
                    ],
                    const SizedBox(height: 16),
                    ConfigurationTransfer(
                      onImported: () async {
                        await bloc.send(OrganizationCommand('refresh'));
                      },
                    ),
                    if (org == null || MediaQuery.sizeOf(context).width < 1000)
                      Wrap(
                        spacing: 12,
                        runSpacing: 8,
                        children: [
                          if (MediaQuery.sizeOf(context).width < 1000)
                            for (final item in orgs)
                              ChoiceChip(
                                label: Text(item['name']),
                                selected: org?['id'] == item['id'],
                                onSelected: (_) =>
                                    setState(() => selected = item['id']),
                              ),
                          FilledButton.icon(
                            onPressed: state.busy
                                ? null
                                : () => editOrganization(),
                            icon: const Icon(Icons.add),
                            label: const Text('Create organization'),
                          ),
                        ],
                      ),
                    if (org == null && state.loaded)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 24),
                        child: Text(
                          'Create your first organization to start hiring.',
                        ),
                      ),
                    if (org != null) ...[
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const CircleAvatar(
                            radius: 26,
                            child: Icon(Icons.business_outlined),
                          ),
                          const SizedBox(width: 16),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  org['name'],
                                  key: const ValueKey('organization-name'),
                                  style: Theme.of(
                                    context,
                                  ).textTheme.headlineMedium,
                                ),
                                const SizedBox(height: 6),
                                Text(
                                  org['purpose'],
                                  style: Theme.of(context).textTheme.bodyMedium,
                                ),
                                const SizedBox(height: 10),
                                Text(
                                  '${profiles.length} agents · ${state.groups.where((g) => g['organization_id'] == org['id']).length} groups',
                                  style: Theme.of(context).textTheme.bodySmall,
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      if (view != 'chart' &&
                          (org['instructions'] as String).isNotEmpty)
                        Card(
                          child: ExpansionTile(
                            key: PageStorageKey(
                              'organization-instructions-${org['id']}',
                            ),
                            title: const Text('Shared instructions'),
                            leading: const Icon(Icons.description_outlined),
                            childrenPadding: const EdgeInsets.all(16),
                            expandedCrossAxisAlignment:
                                CrossAxisAlignment.start,
                            children: [Text(org['instructions'])],
                          ),
                        ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 12,
                        runSpacing: 8,
                        children: [
                          OutlinedButton(
                            onPressed: state.busy
                                ? null
                                : () => editOrganization(org),
                            child: const Text('Edit organization'),
                          ),
                          FilledButton.icon(
                            onPressed: state.busy ? null : () => hire(org),
                            icon: const Icon(Icons.person_add_alt),
                            label: const Text('Hire agent'),
                          ),
                          OutlinedButton(
                            onPressed: state.busy || profiles.length < 2
                                ? null
                                : () => delegate(org),
                            child: const Text('Delegate work'),
                          ),
                          if (MediaQuery.sizeOf(context).width >= 1000)
                            TextButton.icon(
                              onPressed: state.busy
                                  ? null
                                  : () => editOrganization(),
                              icon: const Icon(Icons.add, size: 16),
                              label: const Text('Create organization'),
                            ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      SingleChildScrollView(
                        scrollDirection: Axis.horizontal,
                        child: SegmentedButton<String>(
                          segments: const [
                            ButtonSegment(
                              value: 'chart',
                              label: Text('Chart'),
                              icon: Icon(Icons.account_tree_outlined),
                            ),
                            ButtonSegment(value: 'team', label: Text('Team')),
                            ButtonSegment(value: 'runs', label: Text('Runs')),
                            ButtonSegment(
                              value: 'automation',
                              label: Text('Automation & limits'),
                            ),
                            ButtonSegment(
                              value: 'collaborate',
                              label: Text('Chat'),
                            ),
                          ],
                          selected: {view},
                          onSelectionChanged: (values) =>
                              setState(() => view = values.single),
                        ),
                      ),
                      const SizedBox(height: 18),
                      if (view == 'automation')
                        OrganizationAutomation(
                          key: ValueKey('automation-${org['id']}'),
                          organizationId: org['id'] as String,
                        ),
                      if (view == 'collaborate')
                        CollaborationPanel(
                          key: ValueKey(org['id']),
                          organization: org,
                          profiles: profiles,
                          onOpenGroup: (id) =>
                              widget.coordinator.navigate(GroupRoute(id)),
                        ),
                      if (view == 'chart')
                        OrgChart(
                          profiles: profiles,
                          groups: state.groups
                              .where((g) => g['organization_id'] == org['id'])
                              .toList(),
                          onSelectGroup: (group) => widget.coordinator.navigate(
                            GroupRoute(group['id'] as String),
                          ),
                          agents: connection.state.agents,
                          jobs: jobs.toList(),
                          onSelect: (profile) => widget.coordinator.navigate(
                            AgentConsoleRoute(profile['id'] as String),
                          ),
                          onConfigure: (profile) => hire(org, profile),
                        ),
                      if (view == 'team') ...[
                        Text(
                          'Team roster',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const Text(
                          'Hiring saves a profile. Launch delivers its current persona as the first conversation prompt. Changes apply to the next launch.',
                        ),
                        if (profiles.isEmpty)
                          const Padding(
                            padding: EdgeInsets.all(16),
                            child: Text(
                              'No hires yet. Add your lead, implementer, or reviewer.',
                            ),
                          ),
                        for (final profile in profiles)
                          Card(
                            child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '${profile['name']} · ${profile['role']}',
                                    style: Theme.of(
                                      context,
                                    ).textTheme.titleMedium,
                                  ),
                                  if (profile['coordination_binding'] != null)
                                    Text(
                                      profile['coordination_binding'] == 'used'
                                          ? 'Used by coordination: ${(profile['coordination_repositories'] as List).join(', ')}'
                                          : 'Inactive coordinator profile · no repository binding',
                                    ),
                                  const SizedBox(height: 8),
                                  Text(
                                    'Model: ${savedModelLabel(profile)}',
                                    style: Theme.of(
                                      context,
                                    ).textTheme.bodyMedium,
                                  ),
                                  const SizedBox(height: 8),
                                  ExpansionTile(
                                    key: PageStorageKey(
                                      'agent-persona-${profile['id']}',
                                    ),
                                    tilePadding: EdgeInsets.zero,
                                    title: const Text(
                                      'Persona and responsibilities',
                                    ),
                                    childrenPadding: const EdgeInsets.only(
                                      bottom: 12,
                                    ),
                                    expandedCrossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        '${profile['runtime']} · Persona v${profile['version']}',
                                      ),
                                      const SizedBox(height: 8),
                                      SelectableText(
                                        'Project: ${profile['project']}',
                                        key: PageStorageKey(
                                          'agent-project-${profile['id']}',
                                        ),
                                      ),
                                      Text(
                                        'Reasoning: ${savedReasoningLabel(profile)}',
                                      ),
                                      Text(
                                        'Reports to: ${profiles.where((p) => p['id'] == profile['manager_id']).map((p) => p['name']).firstOrNull ?? 'Organization owner'}',
                                      ),
                                      const Divider(height: 24),
                                      Text(profile['persona']),
                                    ],
                                  ),
                                  Wrap(
                                    spacing: 12,
                                    children: [
                                      TextButton(
                                        onPressed: state.busy
                                            ? null
                                            : () => hire(org, profile),
                                        child: const Text('Edit persona'),
                                      ),
                                      TextButton.icon(
                                        onPressed: state.busy
                                            ? null
                                            : () async {
                                                if (await confirmRemoval(
                                                      context,
                                                      profile['name'] as String,
                                                    ) &&
                                                    mounted) {
                                                  await submit('remove_agent', {
                                                    'organization_id':
                                                        org['id'],
                                                    'profile_id': profile['id'],
                                                  });
                                                }
                                              },
                                        icon: const Icon(Icons.delete_outline),
                                        label: const Text('Remove agent'),
                                      ),
                                      OutlinedButton(
                                        onPressed:
                                            state.busy ||
                                                state.jobs.any(
                                                  (j) =>
                                                      j['kind'] == 'launch' &&
                                                      j['profile_id'] ==
                                                          profile['id'] &&
                                                      j['state'] != 'released',
                                                )
                                            ? null
                                            : () => submit('launch', {
                                                'organization_id': org['id'],
                                                'profile_id': profile['id'],
                                              }),
                                        child: Text(
                                          'Launch ${profile['name']}',
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ),
                        const SizedBox(height: 24),
                      ],
                      if (view == 'runs') ...[
                        if (connection.operator.value['role'] == 'admin')
                          TextButton(
                            onPressed: state.busy || cleaningSessions
                                ? null
                                : () => cleanupSessions(jobs),
                            child: const Text('Clean up obsolete panes'),
                          ),
                        if (connection.operator.value['role'] == 'admin')
                          TextButton(
                            onPressed: state.busy ? null : showArchives,
                            child: const Text('Archived sessions'),
                          ),
                        Text(
                          'Runs and delegation history',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const Text(
                          'Delivery confirms terminal input, not completed work. Launch states are historical; see Live agents for current activity.',
                        ),
                        for (final job in jobs.where(
                          (j) => ['launch', 'delegate'].contains(j['kind']),
                        ))
                          Card(
                            child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '${job['kind'] == 'environment_preparation'
                                        ? 'Environment preparation'
                                        : job['kind'] == 'launch'
                                        ? 'Launch'
                                        : 'Delegation'} · ${state.profiles.where((p) => p['id'] == job['profile_id']).map((p) => p['name']).firstOrNull ?? 'Agent'}',
                                  ),
                                  Text(
                                    'State: ${job['state']} · ${job['updated_at']}',
                                  ),
                                  if (job['kind'] == 'launch' &&
                                      job['profile'] is Map<String, dynamic>)
                                    Text(
                                      'Model at launch: ${savedModelLabel(job['profile'] as Map<String, dynamic>)} · Reasoning: ${savedReasoningLabel(job['profile'] as Map<String, dynamic>)}',
                                    ),
                                  if (job['launch_arguments'] is List)
                                    SelectableText(
                                      'Arguments sent to Herdr: ${(job['launch_arguments'] as List).join(' ')}',
                                    ),
                                  if (job['alias'] != null)
                                    SelectableText(
                                      'Herdr name: ${job['alias']} · Pane: ${job['pane_id'] ?? 'not allocated'}',
                                    ),
                                  if (job['progress'] != null)
                                    Text('${job['progress']}'),
                                  if (job['preparation_execution_id'] != null)
                                    SelectableText(
                                      'Preparation execution: ${job['preparation_execution_id']}',
                                    ),
                                  if (job['parent_execution_id'] != null)
                                    SelectableText(
                                      'Waiting launch: ${job['parent_execution_id']}',
                                    ),
                                  if (job['task'] != null)
                                    Text(
                                      'From ${job['sender_name']}: ${job['task']}',
                                    ),
                                  if (job['workspace_mode'] == 'worktree')
                                    SelectableText(
                                      'Worktree: ${job['worktree_path']}\nBranch: ${job['worktree_branch']}',
                                    ),
                                  if ((job['workspace_note'] as String? ?? '')
                                      .isNotEmpty)
                                    Text(job['workspace_note'] as String),
                                  if ((job['error'] ?? '') != '')
                                    Text(
                                      job['error'],
                                      style: TextStyle(
                                        color: Theme.of(
                                          context,
                                        ).colorScheme.error,
                                      ),
                                    ),
                                  if (job['resolution'] is Map)
                                    Text(
                                      'Resolved: ${job['resolution']['decision']} · ${job['resolution']['reason']}',
                                    ),
                                  if (job['kind'] == 'delegate' &&
                                      connection.operator.value['role'] ==
                                          'admin' &&
                                      [
                                        'delivered',
                                        'reported_complete',
                                        'needs_attention',
                                        'uncertain',
                                      ].contains(job['state']))
                                    Wrap(
                                      children: [
                                        if (job['state'] == 'reported_complete')
                                          TextButton(
                                            onPressed: () => resolveDelegation(
                                              job,
                                              'complete',
                                            ),
                                            child: const Text(
                                              'Accept completion and free agent',
                                            ),
                                          ),
                                        TextButton(
                                          onPressed: () =>
                                              resolveDelegation(job, 'cancel'),
                                          child: const Text(
                                            'Cancel after inspection',
                                          ),
                                        ),
                                      ],
                                    ),
                                  if ((job['result'] ?? '') != '')
                                    Text(
                                      'Operator completion report: ${job['result']}',
                                    ),
                                  if (job['kind'] == 'launch' &&
                                      [
                                        'persona_sent',
                                        'released',
                                      ].contains(job['state']) &&
                                      job['session_closed_at'] == null &&
                                      connection.operator.value['role'] ==
                                          'admin')
                                    Wrap(
                                      children: [
                                        TextButton(
                                          onPressed: state.busy
                                              ? null
                                              : () =>
                                                    manageSession(job, 'close'),
                                          child: const Text(
                                            'Archive and close session',
                                          ),
                                        ),
                                        if (job['state'] == 'persona_sent')
                                          TextButton(
                                            onPressed: state.busy
                                                ? null
                                                : () => manageSession(
                                                    job,
                                                    'restart',
                                                  ),
                                            child: const Text(
                                              'Restart in assigned checkout',
                                            ),
                                          ),
                                      ],
                                    ),
                                  if (![
                                        'queued',
                                        'running',
                                        'released',
                                        'finished',
                                      ].contains(job['state']) &&
                                      [
                                        'launch',
                                        'environment_preparation',
                                      ].contains(job['kind']))
                                    TextButton(
                                      onPressed: state.busy
                                          ? null
                                          : () => release(job),
                                      child: const Text('Release run binding'),
                                    ),
                                  if (job['state'] == 'delivered' &&
                                      job['kind'] == 'delegate' &&
                                      [
                                        'delivered',
                                        'reported_complete',
                                      ].contains(job['state']))
                                    TextButton(
                                      onPressed: state.busy
                                          ? null
                                          : () => report(job),
                                      child: const Text(
                                        'Record completion report',
                                      ),
                                    ),
                                ],
                              ),
                            ),
                          ),
                      ],
                    ],
                  ],
                ),
              ),
            ),
          );
        },
      );
    },
  );
}

// Controllers belong to dialog states and are disposed when their route finishes.
class OrganizationForm extends StatefulWidget {
  const OrganizationForm({
    super.key,
    this.organization,
    this.templates,
    this.preview,
    this.library,
    this.canManageTemplates = true,
    this.projectFolders = const {},
    this.projectFolderError,
  });
  final Map<String, dynamic>? organization;
  final Map<String, dynamic>? templates;
  final Future<Map<String, dynamic>> Function(Map<String, dynamic>)? preview;
  final Future<Map<String, dynamic>> Function(Map<String, dynamic>)? library;
  final bool canManageTemplates;
  final Map<String, String> projectFolders;
  final String? projectFolderError;
  @override
  State<OrganizationForm> createState() => _OrganizationFormState();
}

class _OrganizationFormState extends State<OrganizationForm> {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.organization?['name']);
  late final purpose = TextEditingController(
    text: widget.organization?['purpose'],
  );
  late final instructions = TextEditingController(
    text: widget.organization?['instructions'],
  );
  final project = TextEditingController();
  final selectedRoles = <String>{};
  String policy = 'balanced';
  String preferredRuntime = '';
  String teamPreset = '';
  String savedChoice = '';
  List<String>? loadedPreferences;
  List<String> get preferredRuntimes =>
      loadedPreferences ?? [if (preferredRuntime.isNotEmpty) preferredRuntime];
  late final savedTemplates = [
    for (final value in widget.templates?['saved_templates'] as List? ?? [])
      Map<String, dynamic>.from(value),
  ];
  final templateName = TextEditingController(),
      templateDescription = TextEditingController();
  final libraryState = TemplateLibraryState();
  String? libraryError, libraryNotice;
  static const resourceLabels = {
    'max_active_sessions': 'Maximum concurrent sessions',
    'max_active_meetings': 'Maximum concurrent meetings',
    'daily_session_cap': 'Sessions per 24 hours',
  };
  late final resourceControllers = {
    for (final entry in {
      'max_active_sessions': 4,
      'max_active_meetings': 1,
      'daily_session_cap': 20,
    }.entries)
      entry.key: TextEditingController(
        text:
            '${(widget.templates?['resource_defaults'] as Map?)?[entry.key] ?? entry.value}',
      ),
  };
  Map<String, dynamic> get resources => {
    for (final entry in resourceControllers.entries)
      entry.key: int.tryParse(entry.value.text) ?? -1,
  };
  Map<String, dynamic>? team;
  Map<String, dynamic>? lastPreview;
  String? previewError;
  bool loading = false;
  int revision = 0;
  List<Map<String, dynamic>> get roles => widget.organization == null
      ? [
          for (final r in widget.templates?['roles'] as List? ?? [])
            Map<String, dynamic>.from(r),
        ]
      : [];
  late final draft = TeamTemplateDraft(
    roles,
    maxMembers: widget.templates?['max_members'] as int? ?? 20,
  );
  void invalidate() {
    revision++;
    team = null;
    previewError = null;
    loading = false;
  }

  Future<void> previewTeam() async {
    final epoch = ++revision;
    setState(() {
      loading = true;
      team = null;
      previewError = null;
    });
    try {
      final result = await widget.preview!({
        ...draft.payload,
        'project': project.text.trim(),
        'model_policy': policy,
        'runtime_preferences': preferredRuntimes,
        'team_preset': teamPreset,
        'resource_limits': resources,
        if (libraryState.source != null) ...{
          'template_id': libraryState.source!['id'],
          'template_version': libraryState.source!['version'],
        },
      });
      if (mounted && epoch == revision)
        setState(() {
          team = result;
          lastPreview = result;
          draft.hydrate(result['member_specs']);
        });
    } catch (e) {
      if (mounted && epoch == revision) setState(() => previewError = '$e');
    } finally {
      if (mounted && epoch == revision) setState(() => loading = false);
    }
  }

  Future<void> loadSavedTemplate(String choice) async {
    setState(() {
      savedChoice = choice;
      libraryState.source = null;
      invalidate();
    });
    if (choice.isEmpty || widget.library == null) return;
    final epoch = revision;
    final operation = libraryState.begin();
    setState(() {
      libraryError = null;
    });
    final parts = choice.split('@');
    try {
      final record = await widget.library!({
        'action': 'read',
        'template_id': parts[0],
        'version': int.parse(parts[1]),
      });
      if (!mounted || epoch != revision || !libraryState.current(operation))
        return;
      setState(() {
        libraryState.source = record;
        final definition = Map<String, dynamic>.from(record['definition']);
        draft.hydrate(definition['team_members'], replace: true);
        selectedRoles
          ..clear()
          ..addAll(draft.selectedRoles);
        policy = '${definition['model_policy']}';
        loadedPreferences = List<String>.from(
          definition['runtime_preferences'] as List? ?? [],
        );
        preferredRuntime =
            '${(definition['runtime_preferences'] as List? ?? []).firstOrNull ?? ''}';
        teamPreset = '${definition['team_preset'] ?? ''}';
        templateName.text = '${record['name']}';
        templateDescription.text = '${record['description']}';
        for (final entry in resourceControllers.entries)
          entry.value.text = '${definition['resource_limits'][entry.key]}';
        lastPreview = null;
        invalidate();
        libraryNotice =
            'Loaded ${record['name']} · v${record['version']}. Re-preview for this project and its CLI accounts.';
      });
    } catch (e) {
      if (mounted && libraryState.current(operation))
        setState(() => libraryError = '$e');
    } finally {
      if (mounted) setState(() => libraryState.finish(operation));
    }
  }

  Future<void> saveReusableTemplate(bool update) async {
    if (libraryState.busy || widget.library == null) return;
    final intent = <String, dynamic>{
      'action': 'save',
      'name': templateName.text.trim(),
      'description': templateDescription.text.trim(),
      'definition': {
        ...draft.payload,
        'model_policy': policy,
        'runtime_preferences': preferredRuntimes,
        'resource_limits': resources,
        'team_preset': teamPreset,
      },
      if (update) ...{
        'template_id': libraryState.source!['id'],
        'expected_version': libraryState.source!['version'],
      },
    };
    final request = libraryState.request(intent, newRequestId);
    final selection = libraryState.selection;
    final operation = libraryState.begin();
    final epoch = revision;
    setState(() {
      libraryError = null;
      libraryNotice = null;
    });
    try {
      final saved = await widget.library!(request);
      if (!mounted) return;
      setState(() {
        final header = savedTemplates
            .where((t) => t['id'] == saved['id'])
            .firstOrNull;
        final versions = (header?['versions'] as List? ?? [])
            .where((v) => v['version'] != saved['version'])
            .toList();
        savedTemplates.removeWhere((t) => t['id'] == saved['id']);
        savedTemplates.add({
          'id': saved['id'],
          'name': saved['name'],
          'version': saved['version'],
          'versions': [
            {'version': saved['version'], 'name': saved['name']},
            ...versions,
          ],
        });
        if (libraryState.acknowledge(saved, selection)) {
          if (epoch == revision)
            draft.hydrate(saved['definition']['team_members']);
          savedChoice = '${saved['id']}@${saved['version']}';
          invalidate();
        }
        libraryNotice =
            'Saved ${saved['name']} · v${saved['version']}. Existing organizations remain unchanged.';
      });
    } catch (e) {
      if (mounted) setState(() => libraryError = '$e');
    } finally {
      if (mounted) setState(() => libraryState.finish(operation));
    }
  }

  Widget memberEditor(Map<String, dynamic> member) {
    final key = '${member['key']}';
    final role = draft.role('${member['template_role']}');
    final resolved = (lastPreview?['members'] as List? ?? [])
        .where((m) => m['template_key'] == key)
        .firstOrNull;
    final runtime = '${member['runtime'] ?? resolved?['runtime'] ?? ''}';
    final entry =
        (lastPreview?['runtimes'] as Map? ?? {})[runtime] as Map? ?? {};
    final models = entry['models'] as List? ?? [];
    final chosen = models
        .where(
          (m) =>
              m['id'] == (member['model'] ?? resolved?['model']) &&
              m['provider'] == (member['provider'] ?? resolved?['provider']),
        )
        .firstOrNull;
    final reasoning = chosen?['reasoning'] as List? ?? [];
    void change(String field, dynamic value) => setState(() {
      member[field] = value;
      invalidate();
    });
    return ExpansionTile(
      key: ValueKey('member-$key'),
      title: Text('Configure ${draft.name(member)}'),
      children: [
        TextFormField(
          key: ValueKey('member-name-$key'),
          initialValue: draft.name(member),
          decoration: const InputDecoration(labelText: 'Member name'),
          maxLength: 120,
          onChanged: (v) => change('name', v),
        ),
        TextFormField(
          key: ValueKey('member-persona-$key'),
          initialValue:
              '${member['persona'] ?? role['persona_instruction'] ?? ''}',
          decoration: const InputDecoration(
            labelText: 'Persona responsibilities',
          ),
          maxLength: 6000,
          minLines: 3,
          maxLines: 8,
          onChanged: (v) => change('persona', v),
        ),
        DropdownButtonFormField<String>(
          key: ValueKey('member-runtime-$key-${member['runtime']}'),
          initialValue: '${member['runtime'] ?? ''}',
          decoration: const InputDecoration(labelText: 'Member CLI'),
          items: [
            const DropdownMenuItem(value: '', child: Text('Automatic')),
            for (final entry in Map<String, String>.from(
              widget.templates?['runtime_names'] ?? {},
            ).entries)
              DropdownMenuItem(value: entry.key, child: Text(entry.value)),
          ],
          onChanged: (v) => setState(() {
            if (v == null || v.isEmpty)
              member.remove('runtime');
            else
              member['runtime'] = v;
            for (final field in [
              'provider',
              'model',
              'reasoning',
              'permission_mode',
              'accessible_paths',
            ])
              member.remove(field);
            invalidate();
          }),
        ),
        if (models.isNotEmpty && runtime != 'agy') ...[
          DropdownButtonFormField<String>(
            key: ValueKey('member-model-$key-$runtime-${member['model']}'),
            initialValue: member['model'] == null
                ? ''
                : jsonEncode([member['provider'] ?? '', member['model']]),
            decoration: const InputDecoration(labelText: 'Member model'),
            items: [
              const DropdownMenuItem(
                value: '',
                child: Text('Automatic for role'),
              ),
              for (final model in models)
                DropdownMenuItem(
                  value: jsonEncode([model['provider'] ?? '', model['id']]),
                  child: Text(
                    '${model['provider'] == '' ? '' : '${model['provider']}/'}${model['name']}',
                  ),
                ),
            ],
            onChanged: (v) => setState(() {
              member.remove('reasoning');
              if (v == null || v.isEmpty) {
                member.remove('model');
                member.remove('provider');
              } else {
                final pair = jsonDecode(v) as List;
                member['runtime'] = runtime;
                member['provider'] = pair[0];
                member['model'] = pair[1];
              }
              invalidate();
            }),
          ),
          if (runtime != 'opencode' || entry['supports_variants'] == true)
            DropdownButtonFormField<String>(
              key: ValueKey(
                'member-effort-$key-$runtime-${member['reasoning']}',
              ),
              initialValue: '${member['reasoning'] ?? '__auto__'}',
              decoration: const InputDecoration(labelText: 'Member reasoning'),
              items: [
                const DropdownMenuItem(
                  value: '__auto__',
                  child: Text('Automatic for role'),
                ),
                const DropdownMenuItem(value: '', child: Text('Model default')),
                for (final value in reasoning)
                  DropdownMenuItem(value: '$value', child: Text('$value')),
              ],
              onChanged: (v) => setState(() {
                if (v == '__auto__')
                  member.remove('reasoning');
                else
                  member['reasoning'] = v ?? '';
                invalidate();
              }),
            ),
        ],
        DropdownButtonFormField<String>(
          key: ValueKey(
            'member-manager-$key-${member['manager_key']}-${draft.members.length}',
          ),
          initialValue: '${member['manager_key'] ?? '__auto__'}',
          decoration: const InputDecoration(labelText: 'Reports to'),
          items: [
            const DropdownMenuItem(
              value: '__auto__',
              child: Text('Role default'),
            ),
            const DropdownMenuItem(
              value: '',
              child: Text('Organization owner'),
            ),
            for (final manager in draft.members)
              if (manager['key'] != member['key'])
                DropdownMenuItem(
                  value: '${manager['key']}',
                  child: Text(draft.name(manager)),
                ),
          ],
          onChanged: (v) => setState(() {
            if (v == '__auto__')
              member.remove('manager_key');
            else
              member['manager_key'] = v ?? '';
            invalidate();
          }),
        ),
        Wrap(
          children: [
            TextButton(
              onPressed: draft.members.length >= draft.maxMembers
                  ? null
                  : () => setState(() {
                      draft.add('${member['template_role']}', member);
                      invalidate();
                    }),
              child: Text('Add another ${role['name']}'),
            ),
            TextButton(
              onPressed: () => setState(() {
                draft.remove(key);
                selectedRoles
                  ..clear()
                  ..addAll(draft.selectedRoles);
                invalidate();
              }),
              child: Text('Remove ${draft.name(member)}'),
            ),
          ],
        ),
      ],
    );
  }

  @override
  void dispose() {
    name.dispose();
    purpose.dispose();
    instructions.dispose();
    project.dispose();
    templateName.dispose();
    templateDescription.dispose();
    for (final controller in resourceControllers.values) controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(
      widget.organization == null ? 'Create organization' : 'Edit organization',
    ),
    content: SizedBox(
      width: 800,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              field(name, 'Organization name'),
              const SizedBox(height: 16),
              field(purpose, 'Purpose', limit: 2000, lines: 3),
              const SizedBox(height: 16),
              field(
                instructions,
                'Shared instructions',
                limit: 8000,
                lines: 8,
                required: false,
              ),
              if (widget.organization == null &&
                  widget.templates?['error'] != null)
                Text('${widget.templates!['error']}'),
              if (roles.isNotEmpty) ...[
                if (widget.library != null)
                  DropdownButtonFormField<String>(
                    key: ValueKey(
                      'saved-template-$savedChoice-${savedTemplates.length}',
                    ),
                    initialValue: savedChoice,
                    decoration: const InputDecoration(
                      labelText: 'Saved template version',
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('Custom draft'),
                      ),
                      for (final template in savedTemplates)
                        for (final version
                            in template['versions'] as List? ?? [])
                          DropdownMenuItem(
                            value: '${template['id']}@${version['version']}',
                            child: Text(
                              '${version['name'] ?? template['name']} · v${version['version']}',
                            ),
                          ),
                    ],
                    onChanged: libraryState.busy
                        ? null
                        : (v) => loadSavedTemplate(v ?? ''),
                  ),
                if (libraryState.busy)
                  const Text('Loading or saving template…'),
                if (libraryError != null) Text(libraryError!),
                if (libraryNotice != null) Text(libraryNotice!),
                if ((widget.templates?['bundles'] as List? ?? []).isNotEmpty)
                  DropdownButtonFormField<String>(
                    initialValue: teamPreset,
                    decoration: const InputDecoration(labelText: 'Team preset'),
                    items: [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('Custom team'),
                      ),
                      for (final bundle in widget.templates!['bundles'] as List)
                        DropdownMenuItem(
                          value: '${bundle['id']}',
                          child: Text('${bundle['name']}'),
                        ),
                    ],
                    onChanged: (v) => setState(() {
                      teamPreset = v ?? '';
                      libraryState.source = null;
                      savedChoice = '';
                      final bundle =
                          (widget.templates?['bundles'] as List? ?? [])
                              .where((b) => b['id'] == teamPreset)
                              .firstOrNull;
                      if (bundle != null) {
                        draft.applyBundle(Map<String, dynamic>.from(bundle));
                        selectedRoles
                          ..clear()
                          ..addAll(draft.selectedRoles);
                        policy = '${bundle['model_policy']}';
                        for (final entry in resourceControllers.entries) {
                          entry.value.text =
                              '${(bundle['resource_limits'] as Map?)?[entry.key] ?? {'max_active_sessions': 4, 'max_active_meetings': 1, 'daily_session_cap': 20}[entry.key]}';
                        }
                      }
                      invalidate();
                    }),
                  ),
                for (final bundle
                    in (widget.templates?['bundles'] as List? ?? []).where(
                      (b) => b['id'] == teamPreset,
                    ))
                  Text('${bundle['description']}'),
                const Text(
                  'Select roles to create with this organization. Profiles are ready for tasks; sessions start when launched.',
                ),
                for (final role in roles)
                  CheckboxListTile(
                    title: Text('${role['name']}'),
                    value: selectedRoles.contains(role['id']),
                    onChanged: (value) => setState(() {
                      if (value == true) {
                        selectedRoles.add('${role['id']}');
                      } else {
                        selectedRoles.remove(role['id']);
                      }
                      final withinLimit = draft.syncRoles(selectedRoles);
                      selectedRoles
                        ..clear()
                        ..addAll(draft.selectedRoles);
                      invalidate();
                      if (!withinLimit)
                        previewError =
                            'A team supports up to ${draft.maxMembers} members.';
                    }),
                  ),
                if (selectedRoles.isNotEmpty) ...[
                  DropdownButtonFormField<String>(
                    initialValue: project.text.isEmpty ? null : project.text,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Team project directory',
                    ),
                    hint: const Text('Select a project folder'),
                    items: [
                      for (final entry in widget.projectFolders.entries)
                        DropdownMenuItem(
                          value: entry.key,
                          child: Text(
                            entry.value,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: widget.projectFolders.isEmpty
                        ? null
                        : (v) => setState(() {
                            project.text = v ?? '';
                            invalidate();
                          }),
                    validator: (v) => !widget.projectFolders.containsKey(v)
                        ? 'Select the team project directory.'
                        : null,
                  ),
                  if (widget.projectFolderError != null)
                    Text(widget.projectFolderError!)
                  else if (widget.projectFolders.isEmpty)
                    const Text(
                      'No project folders are available. Add or clone a project in the project explorer, then reopen this form.',
                    ),
                  DropdownButtonFormField<String>(
                    key: ValueKey('model-policy-$policy'),
                    initialValue: policy,
                    decoration: const InputDecoration(
                      labelText: 'Model policy',
                    ),
                    items: [
                      for (final entry in Map<String, String>.from(
                        widget.templates?['policies'] ?? {},
                      ).entries)
                        DropdownMenuItem(
                          value: entry.key,
                          child: Text(entry.value),
                        ),
                    ],
                    onChanged: (v) => setState(() {
                      policy = v ?? 'balanced';
                      invalidate();
                    }),
                  ),
                  TextButton(
                    onPressed:
                        loading ||
                            libraryState.busy ||
                            widget.preview == null ||
                            !widget.projectFolders.containsKey(project.text)
                        ? null
                        : previewTeam,
                    child: Text(
                      loading ? 'Checking CLI accounts…' : 'Preview team',
                    ),
                  ),
                  const Text(
                    'Resource limits cover every launched session, including idle sessions and review helpers. Use 0 for unlimited.',
                  ),
                  for (final entry in resourceControllers.entries)
                    TextFormField(
                      controller: entry.value,
                      decoration: InputDecoration(
                        labelText: resourceLabels[entry.key],
                      ),
                      keyboardType: TextInputType.number,
                      maxLength: 3,
                      onChanged: (_) => setState(invalidate),
                      validator: (v) {
                        final value = int.tryParse(v ?? '');
                        final maximum = {
                          'max_active_sessions': 50,
                          'max_active_meetings': 10,
                          'daily_session_cap': 500,
                        }[entry.key]!;
                        return value == null || value < 0 || value > maximum
                            ? 'Use an integer from 0 to $maximum.'
                            : null;
                      },
                    ),
                  DropdownButtonFormField<String>(
                    key: ValueKey('preferred-runtime-$preferredRuntime'),
                    initialValue: preferredRuntime,
                    decoration: const InputDecoration(
                      labelText: 'Preferred CLI',
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('Automatic'),
                      ),
                      for (final entry in Map<String, String>.from(
                        widget.templates?['runtime_names'] ?? {},
                      ).entries)
                        DropdownMenuItem(
                          value: entry.key,
                          child: Text(entry.value),
                        ),
                    ],
                    onChanged: (v) => setState(() {
                      preferredRuntime = v ?? '';
                      loadedPreferences = null;
                      invalidate();
                    }),
                  ),
                  for (final member in draft.members) memberEditor(member),
                  if (widget.library != null && widget.canManageTemplates)
                    ExpansionTile(
                      title: const Text('Save reusable template'),
                      children: [
                        const Text(
                          'Save the team configuration without its project path or CLI account data. New versions do not update existing organizations.',
                        ),
                        TextField(
                          controller: templateName,
                          decoration: const InputDecoration(
                            labelText: 'Template name',
                          ),
                        ),
                        TextField(
                          controller: templateDescription,
                          decoration: const InputDecoration(
                            labelText: 'Template description',
                          ),
                          maxLines: 3,
                        ),
                        Wrap(
                          children: [
                            TextButton(
                              onPressed: libraryState.busy
                                  ? null
                                  : () => saveReusableTemplate(false),
                              child: const Text('Save as new template'),
                            ),
                            if (libraryState.source != null)
                              TextButton(
                                onPressed: libraryState.busy
                                    ? null
                                    : () => saveReusableTemplate(true),
                                child: const Text('Save new version'),
                              ),
                          ],
                        ),
                      ],
                    ),
                  if (previewError != null)
                    Text(
                      previewError!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  if (lastPreview != null)
                    ExpansionTile(
                      title: const Text('CLI account check from last preview'),
                      children: [
                        for (final account
                            in lastPreview?['accounts'] as List? ?? [])
                          Text(
                            '${account['name']}: ${account['available'] == true ? 'Available' : 'Excluded'} · ${account['reason']}',
                          ),
                      ],
                    ),
                  for (final warning in team?['warnings'] as List? ?? [])
                    Text('$warning'),
                  for (final member in team?['members'] as List? ?? [])
                    ExpansionTile(
                      title: Text(
                        '${member['name']} · ${member['runtime']} · ${member['model_name']}',
                      ),
                      children: [
                        Text(
                          'Reports to: ${member['manager_name'] ?? (member['manager_role'] == '' ? 'Organization owner' : 'Lead Developer')} · Reasoning: ${member['reasoning'] == '' ? 'Model default' : member['reasoning']}',
                        ),
                        Text('${member['selection_note']}'),
                        Text(
                          memberExecutionSummary(
                            Map<String, dynamic>.from(member),
                          ),
                        ),
                        SelectableText('${member['persona']}'),
                      ],
                    ),
                ],
              ],
            ],
          ),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () {
          if (form.currentState!.validate()) {
            if (libraryState.busy) {
              setState(
                () =>
                    previewError = 'Wait for the template operation to finish.',
              );
              return;
            }
            if (selectedRoles.isNotEmpty && (team == null || loading)) {
              setState(
                () => previewError = 'Preview the selected team before saving.',
              );
              return;
            }
            Navigator.pop(context, {
              'id': widget.organization?['id'] ?? '',
              'name': name.text.trim(),
              'purpose': purpose.text.trim(),
              'instructions': instructions.text.trim(),
              if (widget.organization == null && selectedRoles.isNotEmpty) ...{
                ...draft.payload,
                'project': project.text.trim(),
                'model_policy': policy,
                'runtime_preferences': preferredRuntimes,
                'template_plan': team!['template_plan'],
                'team_preset': teamPreset,
                'resource_limits': resources,
                if (libraryState.source != null) ...{
                  'template_id': libraryState.source!['id'],
                  'template_version': libraryState.source!['version'],
                },
              },
            });
          }
        },
        child: const Text('Save organization'),
      ),
    ],
  );
}

Widget field(
  TextEditingController controller,
  String label, {
  int limit = 120,
  int lines = 1,
  bool required = true,
}) => Padding(
  padding: const EdgeInsets.only(bottom: 12),
  child: TextFormField(
    controller: controller,
    minLines: lines,
    maxLines: lines + 2,
    maxLength: limit,
    decoration: InputDecoration(labelText: label),
    validator: (value) => required && (value?.trim().isEmpty ?? true)
        ? '$label is required'
        : null,
  ),
);

class HireForm extends StatefulWidget {
  const HireForm({
    super.key,
    required this.organization,
    required this.profiles,
    this.profile,
    this.catalog = const ModelCatalog({}),
  });
  final Map<String, dynamic> organization;
  final List<Map<String, dynamic>> profiles;
  final Map<String, dynamic>? profile;
  final ModelCatalog catalog;
  @override
  State<HireForm> createState() => _HireFormState();
}

class _HireFormState extends State<HireForm> {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.profile?['name']);
  late final role = TextEditingController(text: widget.profile?['role']);
  late final persona = TextEditingController(text: widget.profile?['persona']);
  late final project = TextEditingController(
    text: widget.profile?['project'] ?? '/home/herdr/projects',
  );
  late final provider = TextEditingController(
    text: widget.profile?['provider'],
  );
  late final model = TextEditingController(text: widget.profile?['model']);
  late final reasoning = TextEditingController(
    text: widget.profile?['reasoning'],
  );
  late String? runtime = widget.profile?['runtime'];
  late String permissions = widget.profile?['permission_mode'] ?? 'default';
  late bool useWorktree = widget.profile?['use_worktree'] ?? true;
  late final accessiblePaths = TextEditingController(
    text: (widget.profile?['accessible_paths'] as List? ?? []).join('\n'),
  );
  late String manager = widget.profile?['manager_id'] ?? '';
  @override
  void initState() {
    super.initState();
    project.addListener(projectChanged);
  }

  void projectChanged() => setState(() {});

  @override
  void dispose() {
    name.dispose();
    role.dispose();
    persona.dispose();
    project.removeListener(projectChanged);
    project.dispose();
    provider.dispose();
    model.dispose();
    reasoning.dispose();
    accessiblePaths.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.profile == null ? 'Hire agent' : 'Edit hired agent'),
    content: SizedBox(
      width: 580,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              field(name, 'Agent name'),
              field(role, 'Role'),
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: runtime,
                decoration: const InputDecoration(
                  labelText: 'Agent runtime',
                  helperText:
                      'Choose the CLI you connected on the CLI accounts page.',
                ),
                hint: const Text('Choose a runtime'),
                validator: (value) =>
                    value == null ? 'Choose an agent runtime.' : null,
                items: [
                  for (final entry in {
                    'codex': 'Codex',
                    'claude': 'Claude Code',
                    'opencode': 'OpenCode',
                    'agy': 'Antigravity',
                  }.entries)
                    DropdownMenuItem(
                      value: entry.key,
                      child: Text(entry.value),
                    ),
                ],
                onChanged: (value) => setState(() {
                  if (runtime != value) {
                    provider.clear();
                    model.clear();
                    reasoning.clear();
                  }
                  runtime = value!;
                  if (widget.profile == null && runtime == 'opencode') {
                    permissions = 'dashboard_outputs';
                  }
                  if (runtime != 'opencode') {
                    permissions = 'default';
                    accessiblePaths.clear();
                  }
                }),
              ),
              const SizedBox(height: 16),
              if (runtime != null)
                ModelFields(
                  runtime: runtime,
                  catalog: widget.catalog[runtime],
                  provider: provider,
                  model: model,
                  reasoning: reasoning,
                  project: project.text.trim(),
                ),
              if (runtime == 'agy')
                const Text(
                  'Choose provider, model and reasoning inside Antigravity.',
                ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: manager,
                decoration: const InputDecoration(labelText: 'Reports to'),
                items: [
                  const DropdownMenuItem(
                    value: '',
                    child: Text('Organization owner'),
                  ),
                  for (final p in widget.profiles.where(
                    (p) => p['id'] != widget.profile?['id'],
                  ))
                    DropdownMenuItem(
                      value: p['id'] as String,
                      child: Text(p['name']),
                    ),
                ],
                onChanged: (value) => manager = value!,
              ),
              const SizedBox(height: 12),
              if (runtime == 'opencode')
                PermissionOptions(
                  key: ValueKey('permissions-$runtime'),
                  value: permissions,
                  paths: accessiblePaths,
                  onChanged: (v) => setState(() => permissions = v),
                ),
              field(project, 'Existing project directory', limit: 2000),
              SwitchListTile(
                title: const Text('Use a Git worktree'),
                subtitle: const Text(
                  'Default: a separate branch and checkout from committed HEAD. Non-Git folders use the selected directory. Applies on next launch.',
                ),
                value: useWorktree,
                onChanged: (v) => setState(() => useWorktree = v),
              ),
              field(persona, 'Persona instructions', limit: 8000, lines: 5),
              const Text(
                'Blank model settings use the CLI defaults. Saved settings apply on the next launch. Connect that same CLI on the CLI accounts page before launching. Changing runtime requires releasing and relaunching the existing run.',
              ),
            ],
          ),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () {
          if (form.currentState!.validate())
            Navigator.pop(context, {
              'id': widget.profile?['id'] ?? '',
              'organization_id': widget.organization['id'],
              'name': name.text.trim(),
              'role': role.text.trim(),
              'runtime': runtime,
              'provider': provider.text.trim(),
              'model': model.text.trim(),
              'reasoning': reasoning.text.trim(),
              'permission_mode': permissions,
              'use_worktree': useWorktree,
              'accessible_paths': PermissionOptions.parsePaths(
                accessiblePaths.text,
              ),
              'manager_id': manager,
              'project': project.text.trim(),
              'persona': persona.text.trim(),
            });
        },
        child: Text(widget.profile == null ? 'Save hire' : 'Save persona'),
      ),
    ],
  );
}

class DelegationForm extends StatefulWidget {
  const DelegationForm({
    super.key,
    required this.organization,
    required this.profiles,
  });
  final Map<String, dynamic> organization;
  final List<Map<String, dynamic>> profiles;
  @override
  State<DelegationForm> createState() => _DelegationFormState();
}

class _DelegationFormState extends State<DelegationForm> {
  final form = GlobalKey<FormState>();
  final task = TextEditingController();
  String? sender;
  String? recipient;
  @override
  void dispose() {
    task.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Delegate work'),
    content: SizedBox(
      width: 580,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Choose two launched hires. The operator sends this task on behalf of the selected sender.',
              ),
              for (final isSender in [true, false])
                DropdownButtonFormField<String>(
                  isExpanded: true,
                  decoration: InputDecoration(
                    labelText: isSender ? 'From agent' : 'To agent',
                  ),
                  items: [
                    for (final p in widget.profiles)
                      DropdownMenuItem(
                        value: p['id'] as String,
                        child: Text(p['name']),
                      ),
                  ],
                  onChanged: (value) {
                    if (isSender) {
                      sender = value;
                    } else {
                      recipient = value;
                    }
                  },
                  validator: (value) => value == null
                      ? 'Launch an agent and select it'
                      : !isSender && value == sender
                      ? 'Choose a different agent'
                      : null,
                ),
              const SizedBox(height: 12),
              field(task, 'Task instructions', limit: 8000, lines: 5),
            ],
          ),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () {
          if (form.currentState!.validate())
            Navigator.pop(context, {
              'organization_id': widget.organization['id'],
              'sender_id': sender,
              'profile_id': recipient,
              'task': task.text.trim(),
            });
        },
        child: const Text('Send delegation'),
      ),
    ],
  );
}

class ReportForm extends StatefulWidget {
  const ReportForm({
    super.key,
    this.title = 'Record completion report',
    this.label = 'Verified result or reply from SSH',
    this.button = 'Save report',
    this.limit = 8000,
  });
  final String title, label, button;
  final int limit;
  @override
  State<ReportForm> createState() => _ReportFormState();
}

class _ReportFormState extends State<ReportForm> {
  final form = GlobalKey<FormState>();
  final result = TextEditingController();
  @override
  void dispose() {
    result.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: SizedBox(
      width: 580,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: field(result, widget.label, limit: widget.limit, lines: 5),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () {
          if (form.currentState!.validate())
            Navigator.pop(context, result.text.trim());
        },
        child: Text(widget.button),
      ),
    ],
  );
}
