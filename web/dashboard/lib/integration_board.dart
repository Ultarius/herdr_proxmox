import 'package:juice/juice.dart';
import 'clipboard_copy.dart';
import 'dashboard_bloc.dart';

/// Durable coordinator configuration and collected worker decisions.
class IntegrationBoard extends StatefulWidget {
  const IntegrationBoard({super.key, required this.repository});
  final String repository;
  @override
  State<IntegrationBoard> createState() => _IntegrationBoardState();
}

class _IntegrationBoardState extends State<IntegrationBoard> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  Map<String, dynamic> data = const {};
  String? error;
  bool busy = false;
  bool refreshing = false;
  String? checked;

  String displayTime(dynamic value) {
    final date = DateTime.tryParse('$value')?.toLocal();
    if (date == null) return value == null ? 'Not checked yet' : '$value';
    return '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')} ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}:${date.second.toString().padLeft(2, '0')}';
  }

  String profileName(dynamic id) =>
      (connection.organizationDirectory.value['profiles'] as List? ?? [])
          .where((p) => p['id'] == id)
          .firstOrNull?['name'] ??
      '$id';

  String eventStatus(Map event) {
    if (event['state'] == 'validation_ready') {
      return 'Merged · validation queued';
    }
    if (event['state'] == 'validating') {
      return 'Merged · validating';
    }
    if (event['state'] == 'validation_pending' ||
        (event['state'] == 'blocked' &&
            event['verification']?['target_incorporated'] == true &&
            event['tests']?['status'] == 'not_run')) {
      return 'Merged · validation pending';
    }
    if (event['state'] == 'validation_failed' ||
        (event['state'] == 'blocked' &&
            event['verification']?['target_incorporated'] == true &&
            event['tests']?['status'] == 'failed')) {
      return 'Merged · validation failed';
    }
    return '${event['state']}';
  }

  Future<void> repair(Map config, {required bool fresh}) async {
    final epoch = connection.generation;
    if (fresh) {
      final inspected = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Create a fresh coordinator summary?'),
          content: const Text(
            'Inspect the coordinator conversation first. The previous request must no longer be running. This retires the old report and queues a new summary of current evidence. It preserves audit history and does not replay merges. If the old run was released, the same profile will be relaunched.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Inspected · create summary'),
            ),
          ],
        ),
      );
      if (inspected != true || !mounted) return;
    }
    await action('integration/repair', {
      'repository': widget.repository,
      'job_id': config['report_job_id'],
      'mode': fresh ? 'fresh' : 'recover',
      if (fresh) 'inspected': true,
    });
    if (mounted && epoch == connection.generation && error == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            fresh
                ? 'Fresh summary requested. The gateway waits for the coordinator to be ready.'
                : 'Saved coordinator report recovered.',
          ),
        ),
      );
    }
  }

  Timer? timer;
  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 15), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    if (busy || refreshing) return;
    setState(() => refreshing = true);
    final epoch = connection.generation;
    try {
      final result = await connection.request('integration');
      if (mounted && epoch == connection.generation)
        setState(() {
          data = Map<String, dynamic>.from(result['coordination'] ?? {});
          checked = result['checked'];
          error = null;
        });
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => refreshing = false);
    }
  }

  Future<void> action(String endpoint, Map<String, dynamic> body) async {
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await connection.request(endpoint, body);
      if (!mounted || epoch != connection.generation) return;
      await connection.request('organizations/directory');
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) {
        setState(() => busy = false);
        await refresh();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final config = (data['configurations'] as List? ?? [])
        .where((c) => c['repository'] == widget.repository)
        .firstOrNull;
    final events = (data['events'] as List? ?? [])
        .where((e) => e['repository'] == widget.repository)
        .toList();
    final org =
        connection.selectedOrganization.value ??
        (connection.organizationDirectory.value['organizations'] as List? ?? [])
            .firstOrNull?['id'];
    return Card(
      child: ExpansionTile(
        key: PageStorageKey('integration-board-${widget.repository}'),
        leading: const Icon(Icons.hub_outlined),
        title: const Text('Integration coordinator'),
        subtitle: Text(
          config?['enabled'] == true
              ? '${events.length} tracked decisions · idle-only delivery'
              : 'Coordinate updates without interrupting active work',
        ),
        childrenPadding: const EdgeInsets.all(16),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 12,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Chip(
                label: Text(
                  config == null
                      ? 'Not configured'
                      : config['enabled'] == true
                      ? 'Enabled'
                      : 'Paused',
                ),
              ),
              Text('Last scan: ${displayTime(checked)}'),
              IconButton(
                tooltip: 'Refresh coordination',
                onPressed: busy || refreshing ? null : refresh,
                icon: const Icon(Icons.refresh),
              ),
              const Text('Launch details: Org chart → Runs'),
            ],
          ),
          if (config != null)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                config['coordinator_state'] == 'persona_sent'
                    ? Icons.check_circle_outline
                    : Icons.warning_amber_rounded,
              ),
              title: Text(
                'Coordinator: ${config['coordinator_state'] ?? 'Checking launch'}',
              ),
              subtitle: (config['coordinator_error'] ?? '').isEmpty
                  ? null
                  : Text('${config['coordinator_error']}'),
            ),
          const Text(
            'The gateway queues exact-commit updates. Enabling coordination lets workers merge after choosing integrate_now; deferred work waits for a checkpoint. Workers can also report a blocker. The coordinator summarizes supplied Git verification, worker-reported tests and recovery references; it does not need access to other worktrees. Inspect a pending permission request in Org chart → Chat. Existing worktrees and sessions stay in place.',
          ),
          if (config?['report_state'] == 'waiting')
            ListTile(
              leading: const Icon(Icons.schedule),
              title: const Text('Fresh summary waiting'),
              subtitle: Text(
                config?['coordinator_state'] == 'persona_sent'
                    ? 'Awaiting collected worker outcomes; the coordinator is ready.'
                    : 'Coordinator is not ready (${config?['coordinator_state'] ?? 'not launched'}). '
                          '${config?['coordinator_error'] ?? ''} '
                          'Inspect its run; the summary is delivered once it is ready.',
              ),
            ),
          if (config != null && config['enabled'] != true)
            const Text(
              'Coordination is paused. Resume it to request retries; delivered jobs still finish.',
            ),
          if (config?['report_state'] == 'uncertain' ||
              config?['report_state'] == 'needs_attention')
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Coordinator report needs attention. ${config?['report_error'] ?? ''} Inspect the conversation in Org chart → Chat. Worker integration continues independently.',
                ),
                Wrap(
                  spacing: 12,
                  children: [
                    TextButton(
                      onPressed: busy || config?['enabled'] != true
                          ? null
                          : () => repair(config, fresh: false),
                      child: const Text('Recover saved report'),
                    ),
                    TextButton(
                      onPressed: busy || config?['enabled'] != true
                          ? null
                          : () => repair(config, fresh: true),
                      child: const Text('Create fresh summary'),
                    ),
                  ],
                ),
              ],
            ),
          const SizedBox(height: 12),
          if (config?['enabled'] != true)
            FilledButton.icon(
              onPressed: busy || org == null
                  ? null
                  : () => action('integration/configure', {
                      'repository': widget.repository,
                      'organization_id': org,
                      'profile_id': config?['profile_id'] ?? 'new',
                      'enabled': true,
                    }),
              icon: const Icon(Icons.play_arrow),
              label: const Text('Enable coordinator'),
            )
          else
            OutlinedButton(
              onPressed: busy
                  ? null
                  : () => action('integration/configure', {
                      'repository': widget.repository,
                      'enabled': false,
                    }),
              child: const Text('Pause coordination'),
            ),
          if (busy) const LinearProgressIndicator(),
          if (error != null)
            Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          if (events.isEmpty && config?['enabled'] == true)
            const Padding(
              padding: EdgeInsets.only(top: 12),
              child: Text(
                'No worker events yet. Job completion and remote fetches wake the watcher; a 60-second background scan remains as a fallback.',
              ),
            ),
          const SizedBox(height: 12),
          const Text(
            'Recovery snapshots include tracked and non-ignored untracked files, plus staging state. Ignored files and submodule contents need separate backups. Recover in a separate checkout to preserve current work.',
          ),
          for (final event in (data['recoveries'] as List? ?? events).where(
            (e) =>
                e['repository'] == widget.repository && e['recovery'] != null,
          ))
            ExpansionTile(
              key: PageStorageKey('recovery-${event['recovery']['ref']}'),
              title: Text(
                'Recovery · ${event['name'] ?? profileName(event['profile_id'])}',
              ),
              subtitle: Text('${event['path']} · before integration'),
              children: [
                TextButton.icon(
                  onPressed: () async {
                    final message = await copyTextOrDownload(
                      'git worktree add --detach <new-recovery-folder> ${event['recovery']['ref']}',
                      'recovery-command.txt',
                      copied: 'Recovery command copied.',
                      downloaded:
                          'Clipboard unavailable over HTTP; recovery command downloaded instead.',
                    );
                    if (context.mounted)
                      ScaffoldMessenger.of(
                        context,
                      ).showSnackBar(SnackBar(content: Text(message)));
                  },
                  icon: const Icon(Icons.copy),
                  label: const Text('Copy recovery command'),
                ),
                SelectableText(
                  key: PageStorageKey(
                    'recovery-text-${event['recovery']['ref']}',
                  ),
                  'Snapshot: ${event['recovery']['commit']}\nReference: ${event['recovery']['ref']}\nIn this repository, create a separate recovery checkout:\ngit worktree add --detach <new-recovery-folder> ${event['recovery']['ref']}\nOriginal staging tree: ${event['recovery']['ref']}^2\nOriginal HEAD: ${event['recovery']['ref']}^1',
                ),
              ],
            ),
          ExpansionTile(
            key: PageStorageKey('audit-${widget.repository}'),
            title: const Text('Audit history'),
            children: [
              const Padding(
                padding: EdgeInsets.all(8),
                child: Text(
                  'Recent 200 audit records. Recovery snapshots remain available below their repository until explicitly removed.',
                ),
              ),
              for (final entry in (data['audit'] as List? ?? []).where(
                (a) => a['repository'] == widget.repository,
              ))
                ListTile(
                  title: Text(
                    '${entry['action']} · ${entry['state'] ?? (entry['enabled'] == true ? 'enabled' : 'paused')}',
                  ),
                  subtitle: SelectableText(
                    key: PageStorageKey(
                      'audit-text-${entry['sequence'] ?? entry['at']}',
                    ),
                    '${displayTime(entry['at'])} · ${entry['name'] ?? profileName(entry['profile_id'] ?? 'Gateway')}\n${entry['reason'] ?? ''}',
                  ),
                ),
            ],
          ),
          for (final event in events.take(20))
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                event['state'] == 'completed'
                    ? Icons.check_circle_outline
                    : Icons.pending_actions,
              ),
              title: Text('${event['name']} · ${eventStatus(event)}'),
              subtitle: Text(
                '${event['path']} → ${event['target']}\n${event['reason'] ?? ''}${(event['checkpoint'] ?? '').isEmpty ? '' : '\nCheckpoint: ${event['checkpoint']}'}',
              ),
              onTap: () => showDialog<void>(
                context: context,
                builder: (context) => AlertDialog(
                  title: Text('${event['name']} · ${eventStatus(event)}'),
                  content: SingleChildScrollView(
                    child: SelectableText(
                      'Checkout: ${event['path']}\nTarget: ${event['target']}\nUpdated: ${displayTime(event['updated_at'])}\nReason: ${event['reason'] ?? ''}\nCheckpoint: ${event['checkpoint'] ?? ''}\nJob: ${event['job_id'] ?? 'Not submitted'}\nMerge mode: ${event['merge_mode'] ?? 'Not recorded'}\nGit verification: ${event['verification'] ?? 'Not recorded'}\nTests (worker-reported): ${event['tests']?['status'] ?? 'Not reported'}\n${event['tests']?['summary'] ?? ''}\nRecovery: ${event['recovery']?['ref'] ?? 'Not created yet; required before merge delivery'}',
                    ),
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('Close'),
                    ),
                  ],
                ),
              ),
              trailing:
                  [
                    'deferred',
                    'blocked',
                    'validation_pending',
                    'validation_failed',
                  ].contains(event['state'])
                  ? TextButton(
                      onPressed: busy || config?['enabled'] != true
                          ? null
                          : () => action('integration/retry', {
                              'id': event['id'],
                            }),
                      child: Text(
                        eventStatus(event).startsWith('Merged')
                            ? 'Retry validation'
                            : 'Ask again',
                      ),
                    )
                  : null,
            ),
        ],
      ),
    );
  }
}
