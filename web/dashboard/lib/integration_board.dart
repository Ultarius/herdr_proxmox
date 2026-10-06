import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'package:flutter/services.dart';

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
            'The gateway queues exact-commit updates. Enabling coordination lets workers merge after choosing integrate_now; deferred work waits for a checkpoint. Workers can also report a blocker. A dedicated coordinator collects their responses. Existing worktrees and sessions stay in place.',
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
                'No worker events yet. The background scan runs every 60 seconds. Fetch remote updates to check main.',
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
                  onPressed: () => Clipboard.setData(
                    ClipboardData(
                      text:
                          'git worktree add --detach <new-recovery-folder> ${event['recovery']['ref']}',
                    ),
                  ),
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
              title: Text('${event['name']} · ${event['state']}'),
              subtitle: Text(
                '${event['path']} → ${event['target']}\n${event['reason'] ?? ''}${(event['checkpoint'] ?? '').isEmpty ? '' : '\nCheckpoint: ${event['checkpoint']}'}',
              ),
              onTap: () => showDialog<void>(
                context: context,
                builder: (context) => AlertDialog(
                  title: Text('${event['name']} · ${event['state']}'),
                  content: SingleChildScrollView(
                    child: SelectableText(
                      'Checkout: ${event['path']}\nTarget: ${event['target']}\nUpdated: ${displayTime(event['updated_at'])}\nReason: ${event['reason'] ?? ''}\nCheckpoint: ${event['checkpoint'] ?? ''}\nJob: ${event['job_id'] ?? 'Not submitted'}\nTests: ${event['tests']?['status'] ?? 'Not reported'}\n${event['tests']?['summary'] ?? ''}\nRecovery: ${event['recovery']?['ref'] ?? 'Not created yet; required before merge delivery'}',
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
              trailing: ['deferred', 'blocked'].contains(event['state'])
                  ? TextButton(
                      onPressed: busy
                          ? null
                          : () => action('integration/retry', {
                              'id': event['id'],
                            }),
                      child: const Text('Ask again'),
                    )
                  : null,
            ),
        ],
      ),
    );
  }
}
