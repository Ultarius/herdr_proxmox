import 'package:flutter/services.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'collaboration_panel.dart';
import 'artifact_download.dart';
import 'routes.dart';

class GroupPage extends StatefulWidget {
  const GroupPage({super.key, required this.id, required this.coordinator});
  final String id;
  final AppCoordinator coordinator;
  @override
  State<GroupPage> createState() => _GroupPageState();
}

class _GroupPageState extends State<GroupPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  Map<String, dynamic>? group;
  List<Map<String, dynamic>> profiles = [];
  List<Map<String, dynamic>> jobs = [];
  final message = TextEditingController();
  Timer? timer;
  bool loading = false;
  bool busy = false;
  bool loaded = false;
  String tab = 'posts';
  String? artifact;
  String? error;
  OrganizationCommand? pending;

  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 5), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    message.dispose();
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || !connection.state.connected) return;
    loading = true;
    final epoch = connection.generation;
    try {
      final data = await connection.request('organizations');
      if (!mounted || epoch != connection.generation) return;
      final current = List<Map<String, dynamic>>.from(
        data['groups'] ?? [],
      ).where((g) => g['id'] == widget.id).firstOrNull;
      setState(() {
        group = current;
        profiles = List<Map<String, dynamic>>.from(data['profiles'])
            .where((p) => p['organization_id'] == current?['organization_id'])
            .toList();
        jobs = List<Map<String, dynamic>>.from(data['jobs'])
            .where(
              (j) => j['kind'] == 'discussion' && j['group_id'] == widget.id,
            )
            .toList()
            .reversed
            .toList();
        loaded = true;
        if (pending == null) error = null;
      });
      if (current != null)
        connection.selectedOrganization.value =
            current['organization_id'] as String;
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = exception.toString());
    } finally {
      loading = false;
    }
  }

  Future<void> submit(
    String action,
    Map<String, dynamic> body, {
    bool retry = false,
  }) async {
    if (busy || group == null || !connection.state.connected) return;
    final epoch = connection.generation;
    pending = retry
        ? pending
        : OrganizationCommand(action, {
            ...body,
            'organization_id': group!['organization_id'],
          });
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await connection.request(
        'organizations/${pending!.action}',
        pending!.body,
      );
      if (!mounted || epoch != connection.generation) return;
      pending = null;
      await refresh();
      if (action == 'discuss') message.clear();
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> edit() async {
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => DiscussionGroupForm(profiles: profiles, group: group),
    );
    if (values != null && mounted) await submit('group', values);
  }

  void viewArtifact(Map<String, dynamic> job) => setState(() {
    tab = 'artifacts';
    artifact = job['id'] as String;
  });
  String timestamp(dynamic value) {
    final parsed = DateTime.tryParse('$value')?.toLocal();
    return parsed == null
        ? ''
        : '${parsed.year}-${parsed.month.toString().padLeft(2, '0')}-${parsed.day.toString().padLeft(2, '0')} ${parsed.hour.toString().padLeft(2, '0')}:${parsed.minute.toString().padLeft(2, '0')}';
  }

  Widget post(Map<String, dynamic> job, int round) {
    final parts = (job['contributions'] as List? ?? [])
        .where((p) => p['round'] == round)
        .toList();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const CircleAvatar(child: Icon(Icons.forum_outlined)),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Round $round · ${round == 1
                            ? 'Proposals'
                            : round == 2
                            ? 'Review and refinement'
                            : 'Follow-up'}',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      Text(
                        timestamp(job['created_at']),
                        style: const TextStyle(color: Color(0xff999999)),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Text('${job['group']?['description'] ?? group!['description']}'),
            const Divider(height: 28),
            for (final part in parts)
              Padding(
                padding: const EdgeInsets.only(bottom: 20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${part['name']}',
                      style: const TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 6),
                    SelectableText(part['content'] as String),
                  ],
                ),
              ),
            if ((job['result'] as String? ?? '').isNotEmpty)
              TextButton.icon(
                onPressed: () => viewArtifact(job),
                icon: const Icon(Icons.article_outlined),
                label: const Text('View resulting artifact'),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> export(Map<String, dynamic> job, bool download) async {
    try {
      if (download) {
        await downloadArtifact(
          job['result'] as String,
          'action-plan-${job['id']}.md',
        );
      } else {
        await Clipboard.setData(ClipboardData(text: job['result'] as String));
      }
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              download ? 'Markdown download started.' : 'Markdown copied.',
            ),
          ),
        );
    } catch (_) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Export failed. Select the artifact text to copy it manually.',
            ),
          ),
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!connection.state.connected)
      return Center(
        child: FilledButton(
          onPressed: () => widget.coordinator.navigate(DashboardRoute()),
          child: const Text('Connect on the dashboard'),
        ),
      );
    if (group == null)
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(error ?? (loaded ? 'Group not found.' : 'Loading group…')),
            TextButton(onPressed: refresh, child: const Text('Refresh')),
          ],
        ),
      );
    final members = group!['members'] as List;
    final occupied =
        List<Map<String, dynamic>>.from(
          connection.organizationDirectory.value['jobs'] ?? [],
        ).any(
          (j) =>
              ['queued', 'running'].contains(j['state']) &&
              (j['participants'] as List? ?? [j['profile_id']]).any(
                members.contains,
              ),
        );
    return ListView(
      padding: const EdgeInsets.all(28),
      children: [
        Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: const Color(0xff191512),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: const Color(0xff332820)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(
                    Icons.forum_outlined,
                    color: Color(0xffff7917),
                    size: 30,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      '# ${group!['name']}',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text(group!['description'] as String),
              const SizedBox(height: 16),
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  Chip(
                    avatar: const Icon(Icons.people_outline, size: 18),
                    label: Text('${members.length} members'),
                  ),
                  OutlinedButton(
                    onPressed: busy || pending != null ? null : edit,
                    child: const Text('Edit group'),
                  ),
                  FilledButton.icon(
                    onPressed: busy || pending != null || occupied
                        ? null
                        : () => submit('discuss', {'group_id': widget.id}),
                    icon: const Icon(Icons.play_arrow),
                    label: const Text('Start discussion'),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        Wrap(
          spacing: 8,
          children: [
            for (final item in const {
              'posts': 'Posts',
              'artifacts': 'Artifacts',
              'members': 'Members',
              'about': 'About',
            }.entries)
              TextButton(
                onPressed: () => setState(() {
                  tab = item.key;
                  artifact = null;
                }),
                style: TextButton.styleFrom(
                  backgroundColor: tab == item.key
                      ? const Color(0xff242424)
                      : Colors.transparent,
                ),
                child: Text(item.value),
              ),
          ],
        ),
        const Divider(height: 24),
        TextField(
          controller: message,
          minLines: 2,
          maxLines: 5,
          maxLength: 8000,
          decoration: const InputDecoration(
            labelText: 'Message the group',
            hintText: 'Ask the group agent to coordinate its members…',
            border: OutlineInputBorder(),
          ),
        ),
        Align(
          alignment: Alignment.centerRight,
          child: FilledButton.icon(
            onPressed: busy || pending != null || occupied
                ? null
                : () {
                    if (message.text.trim().isNotEmpty) {
                      submit('discuss', {
                        'group_id': widget.id,
                        'prompt': message.text.trim(),
                      });
                    }
                  },
            icon: const Icon(Icons.send_outlined),
            label: const Text('Send to group'),
          ),
        ),
        const SizedBox(height: 16),
        if (error != null) Text(error!),
        if (pending != null && !busy)
          Wrap(
            children: [
              TextButton(
                onPressed: () => submit(pending!.action, const {}, retry: true),
                child: const Text('Retry same request'),
              ),
              TextButton(
                onPressed: () => setState(() {
                  pending = null;
                  error = null;
                }),
                child: const Text('Dismiss request'),
              ),
            ],
          ),
        if (tab == 'posts') ...[
          if (jobs.isEmpty)
            const Padding(
              padding: EdgeInsets.all(24),
              child: Text(
                'No discussion posts yet. Start a discussion to hear from the group.',
              ),
            ),
          for (final job in jobs) ...[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(
                'Discussion · ${timestamp(job['created_at'])} · ${job['state']}',
              ),
            ),
            if ((job['prompt'] as String? ?? '').isNotEmpty)
              SelectableText('You: ${job['prompt']}'),
            if ((job['progress'] as String? ?? '').isNotEmpty &&
                job['state'] != 'artifact_ready')
              Text(job['progress'] as String),
            if ((job['error'] as String? ?? '').isNotEmpty)
              Text(job['error'] as String),
            for (final round
                in ((job['contributions'] as List? ?? [])
                    .map((p) => p['round'] as int)
                    .toSet()
                    .toList()
                  ..sort((a, b) => b.compareTo(a))))
              if ((job['contributions'] as List? ?? []).any(
                (part) => part['round'] == round,
              ))
                post(job, round),
          ],
        ],
        if (tab == 'artifacts') ...[
          if (artifact != null)
            TextButton(
              onPressed: () => setState(() => artifact = null),
              child: const Text('Show all artifacts'),
            ),
          if (!jobs.any((j) => (j['result'] as String? ?? '').isNotEmpty))
            const Text('Artifacts appear here after a discussion completes.'),
          for (final job in jobs.where(
            (j) =>
                (j['result'] as String? ?? '').isNotEmpty &&
                (artifact == null || j['id'] == artifact),
          ))
            Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Action brief · ${timestamp(job['created_at'])}',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 16),
                    SelectableText(job['result'] as String),
                    Wrap(
                      spacing: 12,
                      children: [
                        TextButton(
                          onPressed: () => export(job, false),
                          child: const Text('Copy Markdown'),
                        ),
                        TextButton(
                          onPressed: () => export(job, true),
                          child: const Text('Download Markdown'),
                        ),
                        TextButton(
                          onPressed: () => setState(() => tab = 'posts'),
                          child: const Text('Back to discussion posts'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
        ],
        if (tab == 'members') ...[
          for (final id in members)
            Card(
              child: ListTile(
                leading: const Icon(Icons.smart_toy_outlined),
                title: Text(
                  '${profiles.where((p) => p['id'] == id).firstOrNull?['name'] ?? id}',
                ),
                subtitle: Text(
                  '${profiles.where((p) => p['id'] == id).firstOrNull?['role'] ?? ''}',
                ),
              ),
            ),
        ],
        if (tab == 'about') ...[
          Text(
            'About this group',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 12),
          SelectableText(group!['description'] as String),
          const SizedBox(height: 24),
          const Text(
            'This group has its own Herdr agent conversation. Your messages go to that agent, which coordinates the selected members and synthesizes an action brief. It chooses the discussion rounds and follow-ups. Posts and artifacts preserve the group description and members used for that discussion. Review recommendations before taking action.',
          ),
        ],
      ],
    );
  }
}
