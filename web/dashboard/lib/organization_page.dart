import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'routes.dart';
import 'org_chart.dart';

class OrganizationPage extends StatefulWidget {
  const OrganizationPage({super.key, required this.coordinator});
  final AppCoordinator coordinator;
  @override
  State<OrganizationPage> createState() => _OrganizationPageState();
}

class _OrganizationPageState extends State<OrganizationPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final OrganizationBloc bloc = OrganizationBloc(connection);
  Timer? timer;
  String view = 'chart';
  String? get selected => connection.selectedOrganization.value;
  set selected(String? value) => connection.selectedOrganization.value = value;
  void selectionChanged() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    connection.selectedOrganization.addListener(selectionChanged);
    bloc.send(OrganizationCommand('refresh'));
    timer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (connection.state.connected && bloc.pending == null)
        bloc.send(OrganizationCommand('refresh'));
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    connection.selectedOrganization.removeListener(selectionChanged);
    bloc.dispose();
    super.dispose();
  }

  Future<void> submit(String action, Map<String, dynamic> body) =>
      bloc.send(OrganizationCommand(action, body));

  Future<void> editOrganization([Map<String, dynamic>? organization]) async {
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => OrganizationForm(organization: organization),
    );
    if (values != null && mounted) {
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
        profiles: bloc.state.profiles
            .where((p) => p['organization_id'] == org['id'])
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

  Future<void> release(Map<String, dynamic> job) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Release run binding?'),
        content: const Text(
          'This allows another launch. It leaves the terminal and process running. Inspect and stop the old agent over SSH first to avoid duplicate work.',
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
              .where((p) => p['organization_id'] == org?['id'])
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
                      if (MediaQuery.sizeOf(context).width < 1000 ||
                          view != 'chart') ...[
                        const SizedBox(height: 24),
                        Text(
                          org['purpose'],
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ],
                      if (view != 'chart' &&
                          (org['instructions'] as String).isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text(org['instructions']),
                        ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 12,
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
                      SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(
                            value: 'chart',
                            label: Text('Chart'),
                            icon: Icon(Icons.account_tree_outlined),
                          ),
                          ButtonSegment(value: 'team', label: Text('Team')),
                          ButtonSegment(value: 'runs', label: Text('Runs')),
                        ],
                        selected: {view},
                        onSelectionChanged: (values) =>
                            setState(() => view = values.single),
                      ),
                      const SizedBox(height: 18),
                      if (view == 'chart')
                        OrgChart(
                          profiles: profiles,
                          agents: connection.state.agents,
                          jobs: jobs.toList(),
                          onSelect: (profile) => hire(org, profile),
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
                                  Text(
                                    '${profile['runtime']} · Persona v${profile['version']} · ${profile['project']}',
                                  ),
                                  Text(
                                    'Reports to: ${profiles.where((p) => p['id'] == profile['manager_id']).map((p) => p['name']).firstOrNull ?? 'Organization owner'}',
                                  ),
                                  const SizedBox(height: 8),
                                  Text(profile['persona']),
                                  Wrap(
                                    spacing: 12,
                                    children: [
                                      TextButton(
                                        onPressed: state.busy
                                            ? null
                                            : () => hire(org, profile),
                                        child: const Text('Edit persona'),
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
                        Text(
                          'Runs and delegation history',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const Text(
                          'Delivery confirms terminal input, not completed work. Launch states are historical; see Live agents for current activity.',
                        ),
                        for (final job in jobs)
                          Card(
                            child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '${job['kind'] == 'launch' ? 'Launch' : 'Delegation'} · ${profiles.where((p) => p['id'] == job['profile_id']).map((p) => p['name']).firstOrNull ?? 'Agent'}',
                                  ),
                                  Text(
                                    'State: ${job['state']} · ${job['updated_at']}',
                                  ),
                                  if (job['alias'] != null)
                                    SelectableText(
                                      'Herdr name: ${job['alias']} · Pane: ${job['pane_id'] ?? 'not allocated'}',
                                    ),
                                  if (job['task'] != null)
                                    Text(
                                      'From ${job['sender_name']}: ${job['task']}',
                                    ),
                                  if ((job['error'] ?? '') != '')
                                    Text(
                                      job['error'],
                                      style: TextStyle(
                                        color: Theme.of(
                                          context,
                                        ).colorScheme.error,
                                      ),
                                    ),
                                  if ((job['result'] ?? '') != '')
                                    Text(
                                      'Operator completion report: ${job['result']}',
                                    ),
                                  if (![
                                        'queued',
                                        'running',
                                        'released',
                                      ].contains(job['state']) &&
                                      job['kind'] == 'launch')
                                    TextButton(
                                      onPressed: state.busy
                                          ? null
                                          : () => release(job),
                                      child: const Text('Release run binding'),
                                    ),
                                  if (job['state'] == 'delivered' &&
                                      job['kind'] == 'delegate')
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
  const OrganizationForm({super.key, this.organization});
  final Map<String, dynamic>? organization;
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
  @override
  void dispose() {
    name.dispose();
    purpose.dispose();
    instructions.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(
      widget.organization == null ? 'Create organization' : 'Edit organization',
    ),
    content: SizedBox(
      width: 580,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              field(name, 'Organization name'),
              field(purpose, 'Purpose', limit: 2000, lines: 3),
              field(
                instructions,
                'Shared instructions',
                limit: 8000,
                lines: 4,
                required: false,
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
              'id': widget.organization?['id'] ?? '',
              'name': name.text.trim(),
              'purpose': purpose.text.trim(),
              'instructions': instructions.text.trim(),
            });
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
  });
  final Map<String, dynamic> organization;
  final List<Map<String, dynamic>> profiles;
  final Map<String, dynamic>? profile;
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
  late String runtime = widget.profile?['runtime'] ?? 'codex';
  late String manager = widget.profile?['manager_id'] ?? '';
  @override
  void dispose() {
    name.dispose();
    role.dispose();
    persona.dispose();
    project.dispose();
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
                decoration: const InputDecoration(labelText: 'Agent runtime'),
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
                onChanged: (value) => runtime = value!,
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
              field(project, 'Existing project directory', limit: 2000),
              field(persona, 'Persona instructions', limit: 8000, lines: 5),
              const Text(
                'Use the CLI default model. Authenticate the runtime over SSH before launching.',
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
  const ReportForm({super.key});
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
    title: const Text('Record completion report'),
    content: SizedBox(
      width: 580,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: field(
            result,
            'Verified result or reply from SSH',
            limit: 8000,
            lines: 5,
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
            Navigator.pop(context, result.text.trim());
        },
        child: const Text('Save report'),
      ),
    ],
  );
}
