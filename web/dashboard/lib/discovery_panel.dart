import 'dart:convert';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';
import 'artifact_download.dart';

/// Product-level planning is independent of task review and starts with drafts.
class DiscoveryPanel extends StatefulWidget {
  const DiscoveryPanel({super.key, this.coordinator});
  final AppCoordinator? coordinator;
  @override
  State<DiscoveryPanel> createState() => _DiscoveryPanelState();
}

class _DiscoveryPanelState extends State<DiscoveryPanel> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  Map<String, dynamic> data = {};
  String? organization;
  String? error;
  bool busy = false;
  bool expanded = false;
  Timer? timer;
  StreamSubscription? subscription;
  int epoch = -1;
  bool get admin => connection.operator.value['role'] == 'admin';

  @override
  void initState() {
    super.initState();
    epoch = connection.generation;
    subscription = connection.stream.listen((_) {
      if (mounted && epoch != connection.generation) {
        epoch = connection.generation;
        setState(() {
          data = {};
          organization = null;
          error = null;
        });
      }
    });
    timer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (expanded && !busy && admin && connection.state.connected) refresh();
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    subscription?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    if (busy || !admin || !connection.state.connected) return;
    final generation = connection.generation;
    setState(() => busy = true);
    try {
      final snapshot = await connection.request('discovery');
      if (!mounted || generation != connection.generation) return;
      setState(() {
        data = snapshot;
        final organizations = data['organizations'] as List? ?? [];
        if (!organizations.any((o) => o['id'] == organization)) {
          organization = organizations.isEmpty
              ? null
              : '${organizations.first['id']}';
        }
        error = null;
      });
    } catch (e) {
      if (mounted && generation == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> send(Map<String, dynamic> body) async {
    if (busy || !admin) return;
    final generation = connection.generation;
    setState(() => busy = true);
    try {
      await connection.request('tasks/discovery', {
        'organization_id': organization,
        ...body,
      });
      if (!mounted || generation != connection.generation) return;
      setState(() {
        busy = false;
        error = null;
      });
      await refresh();
    } catch (e) {
      if (mounted && generation == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> configure(Map policy) async {
    final generation = connection.generation;
    final selectedOrganization = organization;
    final brief = TextEditingController(
      text: '${policy['product_brief'] ?? ''}',
    );
    final focus = TextEditingController(
      text: (policy['discovery_focus'] as List? ?? []).join(', '),
    );
    final excluded = TextEditingController(
      text: '${policy['do_not_propose'] ?? ''}',
    );
    final groups = [
      for (final g in data['groups'] as List? ?? [])
        if (g['organization_id'] == organization) g,
    ];
    final root = '${data['projects_root']}'
        .replaceAll('\\', '/')
        .replaceAll(RegExp(r'/+$'), '');
    final repositories = <String>{};
    for (final p in data['profiles'] as List? ?? []) {
      if (p['organization_id'] != organization) continue;
      final path = '${p['project']}'.replaceAll('\\', '/');
      if (path.startsWith('$root/'))
        repositories.add(path.substring(root.length + 1));
    }
    final choices = repositories.toList()..sort();
    var groupId = groups.any((g) => g['id'] == policy['discovery_group_id'])
        ? '${policy['discovery_group_id']}'
        : groups.isEmpty
        ? null
        : '${groups.first['id']}';
    var repository = choices.contains(policy['discovery_repository'])
        ? '${policy['discovery_repository']}'
        : choices.isEmpty
        ? null
        : choices.first;
    var enabled = policy['discovery_enabled'] == true;
    var interval = policy['discovery_interval_hours'] as int? ?? 168;
    var cap = policy['discovery_max_proposals'] as int? ?? 3;
    DialogRoute<Map<String, dynamic>>? route;
    try {
      route = DialogRoute<Map<String, dynamic>>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
            title: const Text('Product discovery direction'),
            content: SizedBox(
              width: 600,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'Choose a read-only Product group. Start its members on the group page before running discovery. Proposals become drafts after approval; this does not enable automatic task launch.',
                    ),
                    TextField(
                      controller: brief,
                      maxLength: 1500,
                      minLines: 3,
                      maxLines: 6,
                      decoration: const InputDecoration(
                        labelText: 'Product brief',
                        hintText:
                            'Who it serves, goals, quality bar and non-goals',
                      ),
                    ),
                    TextField(
                      controller: focus,
                      decoration: const InputDecoration(
                        labelText: 'Focus areas, separated by commas',
                      ),
                    ),
                    TextField(
                      controller: excluded,
                      maxLength: 800,
                      minLines: 2,
                      maxLines: 4,
                      decoration: const InputDecoration(
                        labelText: 'Do not propose',
                      ),
                    ),
                    DropdownButtonFormField<String>(
                      initialValue: repository,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Repository',
                      ),
                      items: [
                        for (final path in choices)
                          DropdownMenuItem(value: path, child: Text(path)),
                      ],
                      onChanged: choices.isEmpty
                          ? null
                          : (v) => update(() => repository = v),
                    ),
                    DropdownButtonFormField<String>(
                      initialValue: groupId,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Product group',
                      ),
                      items: [
                        for (final g in groups)
                          DropdownMenuItem(
                            value: '${g['id']}',
                            child: Text('${g['name']}'),
                          ),
                      ],
                      onChanged: groups.isEmpty
                          ? null
                          : (v) => update(() => groupId = v),
                    ),
                    if (groups.isEmpty || choices.isEmpty)
                      const Text(
                        'Create a group and assign a worktree agent to a repository first.',
                      ),
                    SwitchListTile(
                      value: enabled,
                      onChanged: (v) => update(() => enabled = v),
                      title: const Text('Schedule discovery'),
                      subtitle: const Text(
                        'Off by default. One meeting per window; manual runs share that budget.',
                      ),
                    ),
                    Row(
                      children: [
                        Expanded(
                          child: DropdownButtonFormField<int>(
                            initialValue: interval,
                            decoration: const InputDecoration(
                              labelText: 'Interval',
                            ),
                            items: [
                              for (final hours in {
                                24,
                                72,
                                168,
                                336,
                                720,
                                interval,
                              }.toList()..sort())
                                DropdownMenuItem(
                                  value: hours,
                                  child: Text('$hours hours'),
                                ),
                            ],
                            onChanged: (v) => update(() => interval = v!),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: DropdownButtonFormField<int>(
                            initialValue: cap,
                            decoration: const InputDecoration(
                              labelText: 'Proposal cap',
                            ),
                            items: [
                              for (var i = 1; i <= 5; i++)
                                DropdownMenuItem(value: i, child: Text('$i')),
                            ],
                            onChanged: (v) => update(() => cap = v!),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: groupId == null || repository == null
                    ? null
                    : () => Navigator.pop(context, {
                        'mode': 'configure',
                        'product_brief': brief.text,
                        'discovery_focus': focus.text
                            .split(',')
                            .map((f) => f.trim())
                            .where((f) => f.isNotEmpty)
                            .toList(),
                        'do_not_propose': excluded.text,
                        'discovery_group_id': groupId,
                        'discovery_repository': repository,
                        'discovery_enabled': enabled,
                        'discovery_interval_hours': interval,
                        'discovery_max_proposals': cap,
                      }),
                child: const Text('Save discovery direction'),
              ),
            ],
          ),
        ),
      );
      final result = await Navigator.of(
        context,
        rootNavigator: true,
      ).push(route);
      if (result != null &&
          mounted &&
          generation == connection.generation &&
          selectedOrganization == organization)
        await send(result);
    } finally {
      if (route != null) await route.completed;
      brief.dispose();
      focus.dispose();
      excluded.dispose();
    }
  }

  void evidence(Map meeting, [List? ids]) {
    final pack = meeting['evidence'] as Map? ?? {};
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Discovery evidence'),
        content: SizedBox(
          width: 700,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                SelectableText(
                  'Source: ${pack['source_sha']}\n${pack['coverage']}',
                ),
                for (final item in pack['items'] as List? ?? [])
                  if (ids == null || ids.contains(item['id']))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: SelectableText(
                        '${item['id']} · ${item['locator']}\n${item['text']}',
                      ),
                    ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => downloadArtifact(
              jsonEncode(pack),
              'discovery-${meeting['id']}.json',
            ),
            child: const Text('Download evidence'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Future<void> recover(Map record) async {
    final generation = connection.generation;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Reprocess recovered discovery result?'),
        content: const Text(
          'Inspect and recover the existing group artifact first. This only parses that saved result again; it does not send another prompt.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('I inspected the recovered artifact'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted && generation == connection.generation)
      await send({
        'mode': 'recover',
        'discovery_id': record['id'],
        'inspected': true,
      });
  }

  String _meetingState(Object? state) => switch ('${state ?? ''}') {
    'pending' => 'preparing',
    'meeting' => 'meeting in progress',
    'ready' => 'ready',
    'needs_attention' => 'needs attention',
    _ => '${state ?? ''}',
  };

  String _proposalState(Map proposal) {
    switch ('${proposal['state']}') {
      case 'proposed':
        return proposal['type'] == 'initiative'
            ? 'awaiting planning approval'
            : 'awaiting your decision';
      case 'draft':
        return 'draft task created';
      case 'planning':
        return 'planning meeting started';
      case 'duplicate':
        return 'possible duplicate';
      case 'rejected':
        return 'rejected';
      default:
        return '${proposal['state']}';
    }
  }

  Widget meeting(Map record) => ExpansionTile(
    title: Text(
      '${record['parent_id'] != null ? 'Initiative planning' : 'Discovery'} \u00b7 ${_meetingState(record['state'])}',
    ),
    subtitle: Text(
      '${record['created_at']} · ${(record['proposals'] as List? ?? []).length} proposals',
    ),
    childrenPadding: const EdgeInsets.all(12),
    children: [
      if (record['state'] == 'needs_attention')
        OutlinedButton(
          onPressed: busy ? null : () => recover(record),
          child: const Text('Reprocess recovered result'),
        ),
      if (record['error'] != null && '${record['error']}'.isNotEmpty)
        Text('${record['error']}'),
      Wrap(
        spacing: 8,
        children: [
          TextButton(
            onPressed: () => evidence(record),
            child: const Text('View evidence pack'),
          ),
          if (widget.coordinator != null)
            TextButton(
              onPressed: () =>
                  widget.coordinator!.push(GroupRoute('${record['group_id']}')),
              child: const Text('Open group discussion'),
            ),
        ],
      ),
      for (final row in record['rubric'] as List? ?? [])
        ListTile(
          title: Text('${row['focus']}: ${row['status']}'),
          trailing: TextButton(
            onPressed: () => evidence(record, row['evidence'] as List?),
            child: const Text('Evidence'),
          ),
        ),
      for (final proposal in record['proposals'] as List? ?? [])
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${proposal['title']}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                Text(
                  '${proposal['type']} \u00b7 ${_proposalState(proposal)}',
                ),
                Text('${proposal['problem']}\n${proposal['impact']}'),
                ExpansionTile(
                  title: const Text('Scope and acceptance criteria'),
                  children: [
                    SelectableText('${proposal['description']}'),
                    for (final criterion
                        in proposal['acceptance_criteria'] as List? ?? [])
                      Text('• $criterion'),
                    for (final check
                        in proposal['required_checks'] as List? ?? [])
                      Text('Check: $check'),
                  ],
                ),
                TextButton(
                  onPressed: () =>
                      evidence(record, proposal['evidence'] as List?),
                  child: const Text('View cited evidence'),
                ),
                if (proposal['state'] == 'proposed')
                  Wrap(
                    spacing: 8,
                    children: [
                      FilledButton(
                        onPressed: busy
                            ? null
                            : () => send({
                                'mode': 'proposal',
                                'discovery_id': record['id'],
                                'proposal_key': proposal['key'],
                                'decision': 'accept',
                              }),
                        child: Text(
                          proposal['type'] == 'initiative'
                              ? 'Approve planning'
                              : 'Create draft',
                        ),
                      ),
                      TextButton(
                        onPressed: busy
                            ? null
                            : () => send({
                                'mode': 'proposal',
                                'discovery_id': record['id'],
                                'proposal_key': proposal['key'],
                                'decision': 'reject',
                              }),
                        child: const Text('Reject'),
                      ),
                    ],
                  ),
                if (proposal['duplicate_task_id'] != null)
                  Wrap(
                    spacing: 8,
                    children: [
                      TextButton(
                        onPressed: widget.coordinator == null
                            ? null
                            : () => widget.coordinator!.push(
                                TaskDetailRoute(
                                  '${proposal['duplicate_task_id']}',
                                ),
                              ),
                        child: Text(
                          'Duplicate of task ${proposal['duplicate_task_id']}',
                        ),
                      ),
                      TextButton(
                        onPressed: busy
                            ? null
                            : () => send({
                                'mode': 'proposal',
                                'discovery_id': record['id'],
                                'proposal_key': proposal['key'],
                                'decision': 'accept',
                                'allow_duplicate': true,
                              }),
                        child: const Text('Create draft anyway'),
                      ),
                    ],
                  ),
                if (proposal['task_id'] != null && widget.coordinator != null)
                  TextButton(
                    onPressed: () => widget.coordinator!.push(
                      TaskDetailRoute('${proposal['task_id']}'),
                    ),
                    child: const Text('Open draft task'),
                  ),
              ],
            ),
          ),
        ),
    ],
  );

  @override
  Widget build(BuildContext context) {
    if (!admin) return const SizedBox.shrink();
    final policy = (data['policies'] as Map?)?[organization] as Map? ?? {};
    final organizations = data['organizations'] as List? ?? [];
    final records = data['meetings'] as List? ?? [];
    final windowUsed = records.any(
      (r) =>
          r['organization_id'] == organization &&
          r['parent_id'] == null &&
          r['state'] != 'pending' &&
          (DateTime.tryParse('${r['next_at']}')?.isAfter(DateTime.now()) ??
              false),
    );
    return Card(
      child: ExpansionTile(
        title: const Text('Product discovery'),
        subtitle: const Text(
          'Product goals → grounded proposals → approved drafts',
        ),
        onExpansionChanged: (value) {
          expanded = value;
          if (value) refresh();
        },
        childrenPadding: const EdgeInsets.all(16),
        children: [
          if (busy) const LinearProgressIndicator(),
          if (error != null)
            Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          if (organizations.isEmpty && !busy)
            const Text(
              'Create an organization, a read-only Product group and repository agents first.',
            ),
          if (organizations.isNotEmpty) ...[
            DropdownButtonFormField<String>(
              key: ValueKey(organization),
              initialValue: organization,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Discovery organization',
              ),
              items: [
                for (final o in organizations)
                  DropdownMenuItem(
                    value: '${o['id']}',
                    child: Text('${o['name']}'),
                  ),
              ],
              onChanged: busy ? null : (v) => setState(() => organization = v),
            ),
            Text(
              'Scheduled: ${policy['discovery_enabled'] == true ? 'on' : 'off'} · ${policy['discovery_paused'] == true || policy['paused'] == true ? 'paused' : 'available'}',
            ),
            Text(
              'Next window: ${policy['discovery_next_at'] ?? 'Not configured'}',
            ),
            if (policy['discovery_error'] != null)
              Text('Discovery waiting: ${policy['discovery_error']}'),
            Text(
              'One meeting per ${policy['discovery_interval_hours'] ?? 168} hours, up to ${policy['discovery_max_proposals'] ?? 3} proposals. Approved initiatives allow one planning meeting with up to five tasks. Drafts only.',
            ),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton(
                  onPressed: busy ? null : () => configure(policy),
                  child: const Text('Configure discovery'),
                ),
                FilledButton(
                  onPressed:
                      busy ||
                          windowUsed ||
                          '${policy['product_brief'] ?? ''}'.isEmpty ||
                          policy['discovery_paused'] == true ||
                          policy['paused'] == true
                      ? null
                      : () => send({'mode': 'run'}),
                  child: const Text('Run discovery now'),
                ),
                OutlinedButton(
                  onPressed: busy
                      ? null
                      : () => send({
                          'mode': 'configure',
                          'discovery_paused':
                              policy['discovery_paused'] != true,
                        }),
                  child: Text(
                    policy['discovery_paused'] == true
                        ? 'Resume discovery'
                        : 'Pause discovery',
                  ),
                ),
                IconButton(
                  tooltip: 'Refresh discovery',
                  onPressed: busy ? null : refresh,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            for (final record in data['meetings'] as List? ?? [])
              if (record['organization_id'] == organization) meeting(record),
          ],
        ],
      ),
    );
  }
}
