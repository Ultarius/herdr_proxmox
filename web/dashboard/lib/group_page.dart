import 'organization_automation.dart';
import 'artifact_export.dart';
import 'dart:convert';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'collaboration_panel.dart';
import 'artifact_download.dart';
import 'routes.dart';
import 'remove_entry_dialog.dart';
import 'folder_access_panel.dart';

/// Job states that keep a group conversation live. Shared so the poll interval
/// and the live-streaming guard cannot drift apart.
const _activeJobStates = [
  'queued',
  'running',
  'waiting_for_members',
  'needs_attention',
  'waiting_for_input',
];

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
  Map<String, dynamic> memberStates = {};
  List<Map<String, dynamic>> allJobs = [];
  final live = <String, Map<String, dynamic>>{};
  final message = TextEditingController();
  Timer? timer;
  bool loading = false;
  bool busy = false;
  bool loaded = false;
  String? snapshotSignature;
  int pollTick = 0;
  int? nextBefore;
  int historyLimit = 20;
  String tab = 'posts';
  String? artifact;
  String? error;
  String? readError;
  OrganizationCommand? pending;

  @override
  void initState() {
    super.initState();
    connection.notificationRevision.addListener(notificationRefresh);
    refresh();
    timer = Timer.periodic(const Duration(seconds: 1), (_) {
      final active = jobs.any((j) => _activeJobStates.contains(j['state']));
      final interval = active && tab == 'posts' ? 1 : 10;
      if (++pollTick % interval == 0) refresh();
    });
  }

  void notificationRefresh() {
    if (mounted) refresh();
  }

  @override
  void dispose() {
    connection.notificationRevision.removeListener(notificationRefresh);
    timer?.cancel();
    message.dispose();
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || !connection.state.connected) return;
    loading = true;
    final epoch = connection.generation;
    try {
      final data = await connection.request('organizations/history', {
        'group_id': widget.id,
        'limit': historyLimit,
      });
      if (!mounted || epoch != connection.generation) return;
      final current =
          List<Map<String, dynamic>>.from(
            data['groups'] ?? [],
          ).where((g) => g['id'] == widget.id).firstOrNull ??
          data['group'] as Map<String, dynamic>?;
      final signature = jsonEncode(data);
      if (signature != snapshotSignature || readError != null)
        setState(() {
          snapshotSignature = signature;
          readError = null;
          group = current;
          if (jobs.length <= historyLimit)
            nextBefore = data['next_before'] as int?;
          memberStates = Map<String, dynamic>.from(data['member_states'] ?? {});
          allJobs = List<Map<String, dynamic>>.from(data['jobs']);
          profiles = List<Map<String, dynamic>>.from(data['profiles'])
              .where((p) => p['organization_id'] == current?['organization_id'])
              .toList();
          final older = jobs
              .where(
                (j) => !(data['jobs'] as List).any((n) => n['id'] == j['id']),
              )
              .toList();
          jobs = List<Map<String, dynamic>>.from(data['jobs'])
              .where(
                (j) => j['kind'] == 'discussion' && j['group_id'] == widget.id,
              )
              .toList()
              .reversed
              .toList();
          jobs.addAll(older);
          loaded = true;
        });
      if (current != null)
        connection.selectedOrganization.value =
            current['organization_id'] as String;
      final active = jobs
          .where((j) => _activeJobStates.contains(j['state']))
          .firstOrNull;
      if (tab == 'posts' && current != null && active != null) {
        try {
          final result = await connection.request('organizations/inspect', {
            'organization_id': current['organization_id'],
            'job_id': active['id'],
            'request_id':
                'group_stream_${DateTime.now().microsecondsSinceEpoch}',
          });
          if (mounted && epoch == connection.generation) {
            final previous = live[active['id']];
            // A file can briefly be incomplete while the facilitator rewrites it.
            // Keep the last valid draft/replies until the next valid document arrives.
            if ((result['contributions'] as List? ?? []).isEmpty &&
                previous != null) {
              result['contributions'] = previous['contributions'];
            }
            if ((result['artifact_draft'] as String? ?? '').isEmpty &&
                previous != null) {
              result['artifact_draft'] = previous['artifact_draft'];
            }
            if (jsonEncode(previous) != jsonEncode(result))
              setState(() => live[active['id'] as String] = result);
          }
        } catch (exception) {
          if (mounted && epoch == connection.generation) {
            setState(
              () => live[active['id'] as String] = {
                ...?live[active['id']],
                'inspection_error': exception.toString(),
              },
            );
          }
        }
      }
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => readError = exception.toString());
    } finally {
      loading = false;
    }
  }

  Future<void> loadOlder() async {
    if (loading || nextBefore == null) return;
    final epoch = connection.generation;
    loading = true;
    try {
      final data = await connection.request('organizations/history', {
        'group_id': widget.id,
        'before': nextBefore,
      });
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        final ids = jobs.map((j) => j['id']).toSet();
        jobs.addAll(
          List<Map<String, dynamic>>.from(data['jobs'])
              .where((j) => j['kind'] == 'discussion' && !ids.contains(j['id']))
              .toList()
              .reversed,
        );
        nextBefore = data['next_before'] as int?;
        readError = null;
      });
    } catch (exception) {
      if (mounted) setState(() => readError = exception.toString());
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
      await pending!.execute(connection);
      if (!mounted || epoch != connection.generation) return;
      final removed = pending!.action == 'remove_group';
      pending = null;
      if (action == 'group' || removed)
        await connection.request('organizations/directory');
      await refresh();
      if (removed && mounted) widget.coordinator.navigate(DashboardRoute());
      if (action == 'discuss') message.clear();
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> managePreparation(Map job, bool retry) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          retry
              ? 'Retry inspected session preparation?'
              : 'Cancel waiting meeting?',
        ),
        content: Text(
          retry
              ? 'Inspect and repair the member sessions first. This retries preparation only; submitted discussions must recover their artifacts instead.'
              : 'No discussion prompt has been sent. Cancel this waiting meeting and release its scheduling reservation.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Back'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted)
      await submit(retry ? 'retry_discussion' : 'cancel_discussion', {
        'job_id': job['id'],
        'inspected': true,
      });
  }

  Future<void> edit() async {
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => DiscussionGroupForm(profiles: profiles, group: group),
    );
    if (values != null && mounted) await submit('group', values);
  }

  Future<void> realignSession(Map job, Map mismatch, bool force) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          force
              ? 'Force realign ${mismatch['name']}?'
              : 'Realign ${mismatch['name']}?',
        ),
        content: Text(
          'Session project: ${mismatch['session_project']}\n'
          'Current project: ${mismatch['configured_project']}\n\n'
          '${force ? 'Stop this stale session even if it is working or blocked, after preserving its visible terminal output.' : 'Archive and close this stale session only when it is idle.'} '
          'Launch a fresh session from the current profile. Checkout files are preserved. '
          'Other jobs and ambiguous session identities still prevent closure. '
          'The gateway will resume this unsubmitted discussion automatically when all project mismatches are resolved and members are ready.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(
              force ? 'Force stop and realign' : 'Archive and relaunch',
            ),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await submit('realign_discussion_session', {
      'job_id': job['id'],
      'run_id': mismatch['run_id'],
      'expected_project': mismatch['configured_project'],
      'expected_alias': mismatch['alias'],
      'inspected': true,
      'force': force,
    });
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
            const SizedBox(height: 8),
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
                    FeedText(
                      key: ValueKey(
                        'reply-${job['id']}-$round-${part['profile_id'] ?? part['name']}',
                      ),
                      text: part['content'] as String,
                    ),
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

  Future<void> respondToBlock(Map stream) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Respond to ${stream['name']}'),
        content: SizedBox(
          width: 700,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'Review the terminal request and its selected option below. Confirm sends Enter to that option. For a dashboard artifact request, select Allow once using the terminal controls first. This does not update the saved permission policy.',
                ),
                const SizedBox(height: 12),
                SelectableText(stream['output'] as String? ?? ''),
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
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Confirm selected option'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await submit('input', {
      'profile_id': stream['profile_id'],
      'key': 'enter',
      'console_token': stream['console_token'],
    });
  }

  Widget liveDiscussion(Map<String, dynamic> job) {
    final data = live[job['id']] ?? const <String, dynamic>{};
    final streams = data['streams'] as List? ?? [];
    final parts = data['contributions'] as List? ?? [];
    final draft = data['artifact_draft'] as String? ?? '';
    final discussion = Card(
      child: ExpansionTile(
        key: PageStorageKey('discussion-live-${job['id']}'),
        leading: const Icon(Icons.forum_outlined),
        title: Text('Live discussion · ${parts.length} replies'),
        subtitle: Text(
          streams.isEmpty
              ? 'Waiting for live output…'
              : streams.map((s) => '${s['name']}: ${s['status']}').join(' · '),
        ),
        childrenPadding: const EdgeInsets.all(16),
        children: [
          if (data['inspection_error'] != null)
            Text('Live output unavailable: ${data['inspection_error']}'),
          if (parts.isNotEmpty)
            SizedBox(
              height: 240,
              child: ListView(
                key: PageStorageKey('discussion-replies-scroll-${job['id']}'),
                children: [
                  for (final part in parts)
                    ListTile(
                      title: Text('${part['name']} · Round ${part['round']}'),
                      subtitle: SelectableText(part['content'] as String),
                    ),
                ],
              ),
            ),
          if (draft.isNotEmpty)
            ExpansionTile(
              key: PageStorageKey('discussion-draft-${job['id']}'),
              title: Text(
                job['state'] == 'needs_attention'
                    ? 'Saved discussion artifact · Not finalized'
                    : 'Discussion artifact draft · In progress',
              ),
              children: [
                SizedBox(
                  height: 220,
                  child: SingleChildScrollView(
                    key: PageStorageKey('discussion-draft-scroll-${job['id']}'),
                    child: SelectableText(draft),
                  ),
                ),
              ],
            ),
          for (final stream in streams)
            ExpansionTile(
              key: PageStorageKey(
                'discussion-terminal-${job['id']}-${stream['profile_id']}',
              ),
              title: Text('${stream['name']} · ${stream['status']}'),
              subtitle: const Text(
                'Live terminal output · May include earlier messages and CLI controls',
              ),
              children: [
                if (stream['scratch_directory'] != null)
                  SelectableText(
                    'Session scratch: ${stream['scratch_directory']}',
                  ),
                SizedBox(
                  height: 220,
                  child: SingleChildScrollView(
                    key: PageStorageKey(
                      'discussion-terminal-scroll-${job['id']}-${stream['profile_id']}',
                    ),
                    child: SelectableText(
                      (stream['error'] as String? ?? '').isNotEmpty
                          ? stream['error'] as String
                          : stream['output'] as String? ?? '',
                    ),
                  ),
                ),
                // A blocked agent holds the discussion open until someone
                // answers it. Offering the keys here, where the block is
                // visible, avoids sending the operator to another page to
                // resolve it. The gateway re-verifies the original
                // conversation is still blocked before sending anything.
                if (stream['input_result'] is Map)
                  Text(
                    'Terminal response: ${stream['input_result']['state']}${(stream['input_result']['error'] as String? ?? '').isEmpty ? '' : ' · ${stream['input_result']['error']}'}',
                  ),
                if (stream['status'] == 'blocked')
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        if (stream['console_token'] != null)
                          FilledButton.icon(
                            onPressed: busy
                                ? null
                                : () => respondToBlock(stream),
                            icon: const Icon(Icons.lock_open_outlined),
                            label: const Text(
                              'Respond to permission or question',
                            ),
                          ),
                        for (final entry in const {
                          'up': 'Up',
                          'down': 'Down',
                          'tab': 'Tab',
                          'enter': 'Enter',
                          'esc': 'Escape',
                          'ctrl+c': 'Interrupt',
                        }.entries)
                          OutlinedButton(
                            onPressed: busy
                                ? null
                                : () => submit('input', {
                                    'profile_id': stream['profile_id'],
                                    'key': entry.key,
                                    if (stream['console_token'] != null)
                                      'console_token': stream['console_token'],
                                  }),
                            child: Text(entry.value),
                          ),
                      ],
                    ),
                  ),
              ],
            ),
        ],
      ),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Keep requests visible outside collapsed live terminal output.
        if (data['inspection_error'] == null)
          for (final stream in streams)
            FolderAccessPanel(
              inspection: stream as Map,
              name: '${stream['name']}',
            ),
        discussion,
      ],
    );
  }

  Future<void> downloadTranscript(Map<String, dynamic> job) async {
    try {
      final result = await connection.request('organizations/transcript', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'request_id': 'transcript_${DateTime.now().microsecondsSinceEpoch}',
      });
      await downloadArtifact(result['content'] as String, 'discussion.json');
    } catch (exception) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Transcript download failed: $exception')),
        );
      }
    }
  }

  Future<void> export(Map<String, dynamic> job, bool download) =>
      exportDiscussionArtifact(context, job, download: download);

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
            Text(
              error ??
                  readError ??
                  (loaded ? 'Group not found.' : 'Loading group…'),
            ),
            TextButton(onPressed: refresh, child: const Text('Refresh')),
          ],
        ),
      );
    final archived = group!['removed_at'] != null;
    final members = group!['members'] as List;
    final offline = members
        .where((id) => memberStates[id]?['active'] == false)
        .toList();
    final occupied = allJobs.any(
      (j) =>
          ['queued', 'running', 'waiting_for_input'].contains(j['state']) &&
          (j['participants'] as List? ?? [j['profile_id']]).any(
            members.contains,
          ),
    );
    return Align(
      alignment: Alignment.topCenter,
      child: SizedBox(
        width: 1120,
        child: ListView(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 24),
          children: [
            Card(
              color: Theme.of(context).colorScheme.surface,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: BorderSide(color: Theme.of(context).dividerColor),
              ),
              child: Padding(
                padding: const EdgeInsets.all(24),
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
                    TextButton.icon(
                      onPressed: () => widget.coordinator.push(
                        KnowledgeRoute(group!['organization_id'] as String),
                      ),
                      icon: const Icon(Icons.menu_book_outlined),
                      label: const Text(
                        'Project knowledge used by discussions',
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      (group!['description'] as String).split('\n').first,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyLarge,
                    ),
                    ExpansionTile(
                      key: PageStorageKey('group-instructions-${widget.id}'),
                      tilePadding: EdgeInsets.zero,
                      title: const Text('Purpose and instructions'),
                      childrenPadding: const EdgeInsets.only(bottom: 16),
                      expandedCrossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SelectableText(
                          group!['description'] as String,
                          key: PageStorageKey(
                            'group-header-purpose-${widget.id}',
                          ),
                        ),
                      ],
                    ),
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
                          onPressed: busy || pending != null || archived
                              ? null
                              : edit,
                          child: const Text('Edit group'),
                        ),
                        OutlinedButton.icon(
                          onPressed:
                              busy || pending != null || occupied || archived
                              ? null
                              : () async {
                                  if (await confirmRemoval(
                                        context,
                                        group!['name'] as String,
                                        group: true,
                                      ) &&
                                      mounted) {
                                    await submit('remove_group', {
                                      'group_id': widget.id,
                                    });
                                  }
                                },
                          icon: const Icon(Icons.delete_outline),
                          label: const Text('Remove group'),
                        ),
                        FilledButton.icon(
                          onPressed:
                              busy || pending != null || occupied || archived
                              ? null
                              : () =>
                                    submit('discuss', {'group_id': widget.id}),
                          icon: const Icon(Icons.play_arrow),
                          label: const Text('Start discussion'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            if (archived)
              const Padding(
                padding: EdgeInsets.all(12),
                child: Text(
                  'Archived group · Saved discussions and downloads remain available.',
                ),
              ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              children: [
                for (final item in const {
                  'posts': 'Posts',
                  'artifacts': 'Artifacts',
                  'proposals': 'Proposals',
                  'settings': 'Settings',
                  'members': 'Members',
                  'about': 'About',
                }.entries)
                  Semantics(
                    key: ValueKey('group-tab-${item.key}'),
                    selected: tab == item.key,
                    child: TextButton(
                      onPressed: () => setState(() {
                        tab = item.key;
                        artifact = null;
                      }),
                      style: TextButton.styleFrom(
                        backgroundColor: tab == item.key
                            ? Theme.of(context).colorScheme.secondaryContainer
                            : Colors.transparent,
                      ),
                      child: Text(item.value),
                    ),
                  ),
              ],
            ),
            const Divider(height: 24),
            if (tab == 'posts' && !archived) ...[
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
                  onPressed: busy || pending != null || occupied || archived
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
              const SizedBox(height: 20),
            ],
            if (error != null) Text(error!),
            if (readError != null) Text(readError!),
            if (pending != null && !busy)
              Wrap(
                children: [
                  TextButton(
                    onPressed: () =>
                        submit(pending!.action, const {}, retry: true),
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
              for (final job in jobs)
                Card(
                  margin: const EdgeInsets.only(bottom: 20),
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const CircleAvatar(
                              child: Icon(Icons.person_outline),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    'You',
                                    style: Theme.of(
                                      context,
                                    ).textTheme.titleMedium,
                                  ),
                                  Text(
                                    timestamp(job['created_at']),
                                    style: Theme.of(
                                      context,
                                    ).textTheme.bodySmall,
                                  ),
                                ],
                              ),
                            ),
                            Chip(
                              label: Text(switch (job['state']) {
                                'artifact_ready' => 'Completed',
                                'needs_attention' => 'Needs attention',
                                'uncertain' => 'Inspect after gateway restart',
                                'running' => 'In progress',
                                'queued' => 'Preparing sessions',
                                'waiting_for_members' => 'Waiting for members',
                                'waiting_for_input' =>
                                  'Waiting for your decision',
                                'cancelled' => 'Cancelled before submission',
                                _ => '${job['state']}',
                              }),
                            ),
                          ],
                        ),
                        const SizedBox(height: 16),
                        FeedText(
                          key: ValueKey('prompt-${job['id']}'),
                          text: (job['prompt'] as String? ?? '').isNotEmpty
                              ? job['prompt'] as String
                              : 'Started a discussion with the group.',
                        ),
                        const Divider(height: 28),
                        ExpansionTile(
                          key: PageStorageKey(
                            'discussion-context-${job['id']}',
                          ),
                          tilePadding: EdgeInsets.zero,
                          title: const Text('Discussion context'),
                          expandedCrossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SelectableText(
                              job['group']?['description'] as String? ?? '',
                              key: PageStorageKey(
                                'discussion-context-text-${job['id']}',
                              ),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              'Members: ${(job['runs'] as List? ?? []).map((r) => r['profile']?['name'] ?? r['profile_id']).join(', ')}',
                            ),
                            const SizedBox(height: 16),
                          ],
                        ),
                        if ((job['progress'] as String? ?? '').isNotEmpty &&
                            job['state'] != 'artifact_ready')
                          Text(job['progress'] as String),
                        if ((job['error'] as String? ?? '').isNotEmpty)
                          Text(job['error'] as String),
                        for (final mismatch
                            in job['project_mismatches'] as List? ?? [])
                          Card(
                            child: Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '${mismatch['name']}: session project mismatch',
                                  ),
                                  if (mismatch['recovery_pending'] == true)
                                    Text(
                                      'Archived session awaits relaunch: ${mismatch['recovery_error'] ?? ''}',
                                    ),
                                  SelectableText(
                                    'Session: ${mismatch['session_project']}\nConfigured: ${mismatch['configured_project']}\nGroup target: ${mismatch['expected_project']}',
                                  ),
                                  if (mismatch['repairable'] != true)
                                    const Text(
                                      'Update the agent/group project settings first. Task-owned or starting sessions require inspection.',
                                    ),
                                  if (connection.operator.value['role'] ==
                                          'admin' &&
                                      mismatch['repairable'] == true &&
                                      [
                                        'needs_attention',
                                        'uncertain',
                                      ].contains(job['state']))
                                    Wrap(
                                      children: [
                                        TextButton(
                                          onPressed: busy
                                              ? null
                                              : () => realignSession(
                                                  job,
                                                  mismatch,
                                                  false,
                                                ),
                                          child: const Text(
                                            'Archive and relaunch in current project',
                                          ),
                                        ),
                                        TextButton(
                                          onPressed: busy
                                              ? null
                                              : () => realignSession(
                                                  job,
                                                  mismatch,
                                                  true,
                                                ),
                                          child: const Text(
                                            'Force realign session',
                                          ),
                                        ),
                                      ],
                                    ),
                                ],
                              ),
                            ),
                          ),
                        if (connection.operator.value['role'] == 'admin' &&
                            job['preparation_version'] == 1 &&
                            job['discussion_submitted_at'] == null &&
                            [
                              'waiting_for_members',
                              'queued',
                              'needs_attention',
                              'uncertain',
                            ].contains(job['state']))
                          Wrap(
                            children: [
                              if ([
                                'needs_attention',
                                'uncertain',
                              ].contains(job['state']))
                                TextButton(
                                  onPressed: busy
                                      ? null
                                      : () => managePreparation(job, true),
                                  child: const Text(
                                    'Retry inspected preparation',
                                  ),
                                ),
                              TextButton(
                                onPressed: busy
                                    ? null
                                    : () => managePreparation(job, false),
                                child: const Text('Cancel waiting meeting'),
                              ),
                            ],
                          ),
                        if ([
                              'needs_attention',
                              'uncertain',
                            ].contains(job['state']) &&
                            (job['discussion_submitted_at'] != null ||
                                job['preparation_version'] == null)) ...[
                          const Text(
                            'The dashboard stopped waiting for this discussion. If the agents have finished, recover their saved artifact and transcript without sending another prompt.',
                          ),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: OutlinedButton.icon(
                              onPressed: busy || pending != null
                                  ? null
                                  : () => submit('recover', {
                                      'job_id': job['id'],
                                    }),
                              icon: const Icon(Icons.restore_page_outlined),
                              label: const Text('Recover saved artifact'),
                            ),
                          ),
                        ],
                        if (job == jobs.firstOrNull &&
                            [
                              'queued',
                              'running',
                              'needs_attention',
                              'waiting_for_input',
                            ].contains(job['state']))
                          liveDiscussion(job),
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
                    ),
                  ),
                ),
            ],
            if (tab == 'posts' && nextBefore != null)
              TextButton(
                onPressed: loading ? null : loadOlder,
                child: const Text('Load older discussions'),
              ),
            if (tab == 'artifacts') ...[
              if (artifact != null)
                TextButton(
                  onPressed: () => setState(() => artifact = null),
                  child: const Text('Show all artifacts'),
                ),
              if (!jobs.any((j) => (j['result'] as String? ?? '').isNotEmpty))
                const Text(
                  'Artifacts appear here after a discussion completes.',
                ),
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
                          'Discussion artifact · ${timestamp(job['created_at'])}',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 16),
                        SelectableText(job['result'] as String),
                        Wrap(
                          spacing: 12,
                          children: [
                            if (job['task_id'] != null)
                              TextButton.icon(
                                onPressed: () => widget.coordinator.push(
                                  TaskDetailRoute('${job['task_id']}'),
                                ),
                                icon: const Icon(Icons.task_alt),
                                label: const Text(
                                  'Review task and follow-up proposals',
                                ),
                              ),
                            TextButton(
                              onPressed: () => export(job, false),
                              child: const Text('Copy Markdown'),
                            ),
                            TextButton(
                              onPressed: () => export(job, true),
                              child: const Text('Download Markdown'),
                            ),
                            TextButton.icon(
                              onPressed: () => downloadTranscript(job),
                              icon: const Icon(Icons.download_outlined),
                              label: const Text('Download discussion.json'),
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
            if (offline.isNotEmpty && tab != 'members')
              ListTile(
                leading: const Icon(Icons.power_settings_new),
                title: Text(
                  'Members not active: ${offline.map((id) => profiles.where((p) => p['id'] == id).firstOrNull?['name'] ?? id).join(', ')}',
                ),
                trailing: TextButton(
                  onPressed: () => setState(() => tab = 'members'),
                  child: const Text('Open Members'),
                ),
              ),
            if (tab == 'members') ...[
              const Text(
                'Turn on an offline member to launch it with its saved persona. Active members are shown as on. Uncertain runs need inspection from the Org chart.',
              ),
              for (final id in members)
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.smart_toy_outlined),
                    trailing: Switch(
                      value: memberStates[id]?['active'] == true,
                      onChanged:
                          busy ||
                              archived ||
                              pending != null ||
                              memberStates[id]?['active'] == true ||
                              ![
                                'off',
                                'stopped',
                                'exited',
                              ].contains(memberStates[id]?['status'])
                          ? null
                          : (_) async {
                              final run = memberStates[id]?['run_id'];
                              if (run != null) {
                                await submit('release', {'job_id': run});
                                if (pending != null || !mounted) return;
                              }
                              await submit('launch', {'profile_id': id});
                            },
                    ),
                    title: Text(
                      '${profiles.where((p) => p['id'] == id).firstOrNull?['name'] ?? id}',
                    ),
                    subtitle: Text(
                      '${profiles.where((p) => p['id'] == id).firstOrNull?['role'] ?? ''} · ${memberStates[id]?['status'] ?? 'unknown'}',
                    ),
                  ),
                ),
            ],
            if (tab == 'settings') ...[
              Text(
                'Group settings',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              Text(
                'Automatic queueing for this group: ${group!['create_tasks'] == true ? 'on' : 'off'}. Outcome reviews: ${group!['notify_outcomes'] != false ? 'on' : 'off'}.',
              ),
              Text(
                'Read-only discussion: ${group!['read_only'] != false ? 'on' : 'off'}. Member permissions keep their individual settings.',
              ),
              TextButton(
                onPressed: busy || archived ? null : edit,
                child: const Text('Edit group settings'),
              ),
              Text(
                'Default discussion scope: ${group!['review_scope'] == 'repository' ? 'whole repository at configured HEAD' : 'group topic'}.',
              ),
              const Divider(),
              const Text(
                'Inherited organization limits apply even when group queueing is off.',
              ),
              OrganizationAutomation(
                key: ValueKey('group-policy-${group!['organization_id']}'),
                organizationId: group!['organization_id'] as String,
              ),
            ],
            if (tab == 'proposals') ...[
              Text(
                'Discussion proposals',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const Text(
                'Creation and queueing outcomes are recorded per discussion. Skipped proposals have not become tasks.',
              ),
              if (!jobs.any(
                (j) => (j['proposal_details'] as List? ?? []).isNotEmpty,
              ))
                const Text(
                  'No finalized proposals in this loaded history. See the artifact for discussion findings.',
                ),
              for (final job in jobs)
                for (final proposal in List<Map<String, dynamic>>.from(
                  job['proposal_details'] ?? [],
                ))
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${proposal['title']}',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          Text('Discussion: ${job['id']}'),
                          Text(
                            'Assignee: ${profiles.where((p) => p['id'] == proposal['profile_id']).firstOrNull?['name'] ?? proposal['profile_id']}',
                          ),
                          Text(
                            proposal['status'] == 'draft'
                                ? 'Draft created'
                                : proposal['status'] == 'queued'
                                ? 'Created and queued'
                                : proposal['status'] == 'skipped'
                                ? 'Skipped'
                                : 'Awaiting proposal evaluation',
                          ),
                          if ('${proposal['reason'] ?? ''}'.isNotEmpty)
                            SelectableText('${proposal['reason']}'),
                          if (['true', 'yes'].contains(
                            '${proposal['needs_review']}'.toLowerCase(),
                          ))
                            const Text(
                              'Operator review required before execution.',
                            ),
                          ExpansionTile(
                            title: const Text(
                              'Acceptance criteria and required checks',
                            ),
                            children: [
                              SelectableText('${proposal['description']}'),
                            ],
                          ),
                          if (proposal['task_id'] != null)
                            TextButton(
                              onPressed: () => widget.coordinator.navigate(
                                TaskDetailRoute(proposal['task_id'] as String),
                              ),
                              child: const Text('Open created task'),
                            ),
                        ],
                      ),
                    ),
                  ),
              if (nextBefore != null)
                TextButton(
                  onPressed: loading ? null : loadOlder,
                  child: const Text('Load older proposals'),
                ),
              TextButton(
                onPressed: () => setState(() => tab = 'settings'),
                child: const Text('View group and organization limits'),
              ),
            ],
            if (tab == 'about') ...[
              Text(
                'About this group',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 12),
              SelectableText(
                group!['description'] as String,
                key: PageStorageKey('group-purpose-text-${widget.id}'),
              ),
              const SizedBox(height: 24),
              const Text(
                'This group has its own Herdr agent conversation. Your messages go to that agent, which coordinates the selected members and synthesizes a discussion artifact. It chooses the discussion rounds and follow-ups. Posts and artifacts preserve the group description and members used for that discussion. Review recommendations before taking action.',
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Keeps long posts readable without hiding the rest of the discussion feed.
class FeedText extends StatefulWidget {
  const FeedText({super.key, required this.text});
  final String text;
  @override
  State<FeedText> createState() => _FeedTextState();
}

class _FeedTextState extends State<FeedText> {
  bool expanded = false;
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final style = Theme.of(
        context,
      ).textTheme.bodyMedium!.copyWith(height: 1.5);
      final preview = TextPainter(
        text: TextSpan(text: widget.text, style: style),
        textDirection: Directionality.of(context),
        textScaler: MediaQuery.textScalerOf(context),
        maxLines: 5,
      )..layout(maxWidth: constraints.maxWidth);
      final overflows = preview.didExceedMaxLines;
      preview.dispose();
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (expanded || !overflows)
            SelectableText(widget.text, style: style)
          else
            Text(
              widget.text,
              style: style,
              maxLines: 5,
              overflow: TextOverflow.ellipsis,
            ),
          if (overflows)
            TextButton(
              onPressed: () => setState(() => expanded = !expanded),
              child: Text(expanded ? 'Show less' : 'Show more'),
            ),
        ],
      );
    },
  );
}
