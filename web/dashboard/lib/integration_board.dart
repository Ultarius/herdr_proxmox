import 'dart:math';
import 'package:juice/juice.dart';
import 'artifact_download.dart';
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
  Map<String, dynamic> sdk = const {};
  Map<String, dynamic> validation = const {};
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

  String auditTitle(Map entry) {
    final state =
        entry['state'] ??
        (entry['enabled'] == true
            ? 'enabled'
            : entry['enabled'] == false
            ? 'paused'
            : null);
    return state == null ? '${entry['action']}' : '${entry['action']} · $state';
  }

  String recoverySubtitle(Map event) {
    final commit = '${event['recovery']?['commit'] ?? ''}';
    final short = commit.length > 12 ? commit.substring(0, 12) : commit;
    return '${event['path']}${short.isEmpty ? '' : ' · $short'} · before integration';
  }

  String blockerLabel(Map event) => '${event['blocker_label'] ?? ''}';

  String? validationRun(Map event) {
    final recorded = event['validation_run'];
    if (recorded is Map) return '${recorded['state'] ?? ''}';
    final runs = validation['runs'] as List? ?? [];
    final match = runs
        .where((run) => run['event_id'] == event['id'])
        .firstOrNull;
    return match == null ? null : '${match['state'] ?? ''}';
  }

  Map? validationRunRecord(Map event) {
    final recorded = event['validation_run'];
    if (recorded is Map && recorded['run_id'] != null) return recorded;
    final runs = validation['runs'] as List? ?? [];
    return runs.where((run) => run['event_id'] == event['id']).firstOrNull
        as Map?;
  }

  Future<void> downloadValidationLog(Map run) async {
    final epoch = connection.generation;
    setState(() => busy = true);
    try {
      final content = await connection.text(
        'validation/log?id=${run['run_id']}',
      );
      if (!mounted || epoch != connection.generation) return;
      await downloadArtifact(content, 'validation-${run['run_id']}.log');
      if (mounted && epoch == connection.generation)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Validation log download started.')),
        );
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  String eventStatus(Map event) {
    if (event['state'] == 'validation_waived')
      return 'Merged · validation waived';
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

  Future<void> reviewBlockers(List events, bool enabled) async {
    final candidates = events
        .where(
          (e) => [
            'blocked',
            'deferred',
            'validation_pending',
            'validation_failed',
            'validation_waived',
          ].contains(e['state']),
        )
        .take(20)
        .toList();
    final selected = <String, String>{};
    var explanation = '';
    final epoch = connection.generation;
    final requestId = List.generate(
      16,
      (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
    ).join();
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) {
          bool merged(Map e) =>
              e['verification']?['target_incorporated'] == true &&
              e['verification']?['conflicts'] == 0 &&
              e['verification']?['merging'] != true;
          String defaultAction(Map e) => merged(e)
              ? (enabled ? 'retry_validation' : 'waive_validation')
              : 'reconsider';
          final eligible = candidates
              .where((e) => merged(e) || enabled)
              .toList();
          return AlertDialog(
            title: const Text('Review integration blockers'),
            content: SizedBox(
              width: 560,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'Approve actions for these commits and worktrees only. Missing tools remain missing; a waiver records skipped validation, never a test pass. No CLI permissions are changed.',
                    ),
                    CheckboxListTile(
                      title: const Text('Select all eligible actions'),
                      value:
                          eligible.isNotEmpty &&
                          eligible.every((e) => selected.containsKey(e['id'])),
                      onChanged: (value) => update(() {
                        selected.clear();
                        if (value == true) {
                          for (final e in eligible) {
                            selected[e['id']] = defaultAction(e);
                          }
                        }
                      }),
                    ),
                    for (final e in candidates) ...[
                      CheckboxListTile(
                        title: Text('${e['name']} · ${eventStatus(e)}'),
                        subtitle: Text(
                          '${e['path']} → ${e['target']}\n${e['reason'] ?? ''}\n${e['tests']?['summary'] ?? ''}'
                          '${blockerLabel(e).isEmpty ? '' : '\nBlocker: ${blockerLabel(e)}'}',
                        ),
                        value: selected.containsKey(e['id']),
                        onChanged: merged(e) || enabled
                            ? (value) => update(() {
                                if (value == true) {
                                  selected[e['id']] = defaultAction(e);
                                } else {
                                  selected.remove(e['id']);
                                }
                              })
                            : null,
                      ),
                      if (selected.containsKey(e['id']))
                        DropdownButton<String>(
                          isExpanded: true,
                          value: selected[e['id']],
                          items: [
                            if (merged(e) && enabled)
                              const DropdownMenuItem(
                                value: 'retry_validation',
                                child: Text('Run validation again'),
                              ),
                            if (merged(e))
                              const DropdownMenuItem(
                                value: 'waive_validation',
                                child: Text('Accept validation gap (waiver)'),
                              ),
                            if (!merged(e) && enabled)
                              const DropdownMenuItem(
                                value: 'reconsider',
                                child: Text('Ask worker to reassess blocker'),
                              ),
                          ],
                          onChanged: (value) => update(() {
                            if (value != null) selected[e['id']] = value;
                          }),
                        ),
                    ],
                    const Text(
                      'SDK installation is requested from the root service on the Integration card; the dashboard itself never gains root access. Conflicts are reassessed by the worker; uncertain delivery and missing snapshots require separate inspection.',
                    ),
                    TextField(
                      maxLength: 1000,
                      decoration: const InputDecoration(
                        labelText: 'Reason for approval',
                      ),
                      onChanged: (value) => update(() {
                        explanation = value.trim();
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
                onPressed: selected.isEmpty || explanation.isEmpty
                    ? null
                    : () => Navigator.pop(context, true),
                child: const Text('Approve selected actions'),
              ),
            ],
          );
        },
      ),
    );
    if (approved != true || !mounted || connection.generation != epoch) return;
    await action('integration/blockers', {
      'request_id': requestId,
      'reason': explanation,
      'selections': [
        for (final e in candidates)
          if (selected.containsKey(e['id']))
            {
              'id': e['id'],
              'repository': e['repository'],
              'target': e['target'],
              'updated_at': e['updated_at'],
              'action': selected[e['id']],
            },
      ],
    });
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
      var sdkData = sdk, validationData = validation;
      try {
        sdkData = Map<String, dynamic>.from(await connection.request('sdk'));
      } catch (_) {
        // Older gateways may not expose the SDK service yet.
      }
      try {
        validationData = Map<String, dynamic>.from(
          await connection.request('validation'),
        );
      } catch (_) {
        // Runner history is optional for the rest of the board.
      }
      if (mounted && epoch == connection.generation)
        setState(() {
          data = Map<String, dynamic>.from(result['coordination'] ?? {});
          sdk = sdkData;
          validation = validationData;
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

  Future<void> installSdk() async {
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await connection.request('sdk/install', const {});
      if (mounted && epoch == connection.generation)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'SDK installation queued. The root service installs the pinned toolchain.',
            ),
          ),
        );
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
              Text(
                'Signed in: ${connection.operator.value['name']} · ${connection.operator.value['role']}',
              ),
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
          if (config?['enabled'] == true)
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Install the development SDK automatically'),
              subtitle: const Text(
                'Queue the pinned toolchain once when a worker reports missing tools. Only the root service installs it; the dashboard never gains root access.',
              ),
              value: config?['auto_sdk'] == true,
              onChanged: busy || connection.operator.value['role'] != 'admin'
                  ? null
                  : (value) => action('integration/configure', {
                      'repository': widget.repository,
                      'organization_id': config?['organization_id'] ?? org,
                      'profile_id': config?['profile_id'],
                      'enabled': true,
                      'auto_sdk': value,
                    }),
            ),
          if (sdk['installed'] == true)
            const ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.check_circle_outline),
              title: Text('Development SDK installed'),
            )
          else if (sdk['supported'] == true) ...[
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.build_outlined),
              title: const Text('Development SDK missing'),
              subtitle: Text(
                'State: ${sdk['state'] ?? 'idle'}. The pinned toolchain is installed by a root service on request.',
              ),
            ),
            if ('${sdk['error'] ?? ''}'.isNotEmpty)
              Text(
                '${sdk['error']}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            FilledButton.icon(
              onPressed:
                  busy ||
                      (sdk['state'] == 'queued' || sdk['state'] == 'running') ||
                      connection.operator.value['role'] != 'admin'
                  ? null
                  : installSdk,
              icon: const Icon(Icons.download),
              label: Text(
                sdk['state'] == 'queued' || sdk['state'] == 'running'
                    ? 'Installation running'
                    : 'Install pinned SDK',
              ),
            ),
            if (connection.operator.value['role'] != 'admin')
              const Text(
                'Only an administrator can request the SDK installation.',
              ),
          ],
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
              subtitle: Text(recoverySubtitle(event)),
              children: [
                TextButton.icon(
                  onPressed: () async {
                    final message = await copyTextOrDownload(
                      'git worktree add --detach <new-recovery-folder> ${event['recovery']['ref']}^1\n'
                          'git -C <new-recovery-folder> restore --source=${event['recovery']['ref']} --worktree -- .\n'
                          'git -C <new-recovery-folder> read-tree ${event['recovery']['ref']}^2',
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
                  'Snapshot: ${event['recovery']['commit']}\nReference: ${event['recovery']['ref']}\nIn this repository, use a new empty folder. Restore files and staging without touching the original checkout:\ngit worktree add --detach <new-recovery-folder> ${event['recovery']['ref']}^1\ngit -C <new-recovery-folder> restore --source=${event['recovery']['ref']} --worktree -- .\ngit -C <new-recovery-folder> read-tree ${event['recovery']['ref']}^2\nOriginal staging tree: ${event['recovery']['ref']}^2\nOriginal HEAD: ${event['recovery']['ref']}^1',
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
                  title: Text(auditTitle(entry)),
                  subtitle: SelectableText(
                    key: PageStorageKey(
                      'audit-text-${entry['sequence'] ?? entry['at']}',
                    ),
                    '${displayTime(entry['at'])} · ${entry['name'] ?? profileName(entry['profile_id'] ?? 'Gateway')}\n${entry['reason'] ?? ''}',
                  ),
                ),
            ],
          ),
          if (events.any(
            (e) => [
              'blocked',
              'deferred',
              'validation_pending',
              'validation_failed',
              'validation_waived',
            ].contains(e['state']),
          ))
            OutlinedButton.icon(
              onPressed: busy
                  ? null
                  : () => reviewBlockers(events, config?['enabled'] == true),
              icon: const Icon(Icons.rule),
              label: const Text('Review blockers'),
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
                '${event['path']} → ${event['target']}\n${event['reason'] ?? ''}'
                '${blockerLabel(event).isEmpty ? '' : '\nBlocker: ${blockerLabel(event)}'}'
                '${validationRun(event) == null ? '' : '\nExact-commit validation: ${validationRun(event)}'}'
                '${(event['checkpoint'] ?? '').isEmpty ? '' : '\nCheckpoint: ${event['checkpoint']}'}',
              ),
              onTap: () => showDialog<void>(
                context: context,
                builder: (context) => AlertDialog(
                  title: Text('${event['name']} · ${eventStatus(event)}'),
                  content: SingleChildScrollView(
                    child: SelectableText(
                      'Checkout: ${event['path']}\nTarget: ${event['target']}\nUpdated: ${displayTime(event['updated_at'])}\nReason: ${event['reason'] ?? ''}\nBlocker category: ${blockerLabel(event).isEmpty ? 'None recorded' : blockerLabel(event)}\nValidation runner: ${validationRun(event) ?? 'Not run'}\nCheckpoint: ${event['checkpoint'] ?? ''}\nJob: ${event['job_id'] ?? 'Not submitted'}\nMerge mode: ${event['merge_mode'] ?? 'Not recorded'}\nGit verification: ${event['verification'] ?? 'Not recorded'}\nOperator approval: ${event['operator_approval'] ?? 'None'}\nTests (worker-reported): ${event['tests']?['status'] ?? 'Not reported'}\n${event['tests']?['summary'] ?? ''}\nRecovery: ${event['recovery']?['ref'] ?? 'Not created yet; required before merge delivery'}',
                    ),
                  ),
                  actions: [
                    if (validationRun(event) != 'running')
                      TextButton.icon(
                        onPressed: busy || '${event['target'] ?? ''}'.isEmpty
                            ? null
                            : () {
                                Navigator.pop(context);
                                action('validation/run', {'id': event['id']});
                              },
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Validate exact commit'),
                      ),
                    if (validationRunRecord(event) != null)
                      TextButton.icon(
                        onPressed: busy
                            ? null
                            : () => downloadValidationLog(
                                validationRunRecord(event)!,
                              ),
                        icon: const Icon(Icons.article_outlined),
                        label: const Text('Download validation log'),
                      ),
                    TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('Close'),
                    ),
                  ],
                ),
              ),
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (validationRunRecord(event) != null &&
                      validationRun(event) != 'running')
                    IconButton(
                      tooltip: 'Download validation log',
                      onPressed: busy
                          ? null
                          : () => downloadValidationLog(
                              validationRunRecord(event)!,
                            ),
                      icon: const Icon(Icons.article_outlined),
                    ),
                  if ([
                    'deferred',
                    'blocked',
                    'validation_pending',
                    'validation_failed',
                  ].contains(event['state']))
                    TextButton(
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
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
