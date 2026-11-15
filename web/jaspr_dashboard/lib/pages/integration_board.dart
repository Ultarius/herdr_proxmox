import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'dart:math';
import 'operations_support.dart';
import 'ops_ui.dart';

class IntegrationBoard extends StatefulComponent {
  const IntegrationBoard({super.key, required this.repository});
  final String repository;
  @override
  State<IntegrationBoard> createState() => _BoardState();
}

class _BoardState extends OperationsState<IntegrationBoard> {
  late final IntegrationBloc feature;
  StreamSubscription? featureSubscription;
  Map<String, dynamic> get data =>
      feature.state.value<Map<String, dynamic>>('data') ?? const {};
  Map<String, dynamic> get validation =>
      feature.state.value<Map<String, dynamic>>('validation') ?? const {};
  String? get error =>
      feature.state.errors['refresh'] ?? feature.state.errors['log'];
  String? get actionFailure => feature.state.errors['action'];
  bool get busy => feature.state.busy || feature.state.reading.contains('log');
  bool get refreshing => feature.state.reading.contains('refresh');
  String? get checked => feature.state.value<String>('checked');
  String? get message => feature.state.message;
  @override
  void initState() {
    super.initState();
    feature = IntegrationBloc(connection);
    featureSubscription = feature.stream.listen((_) {
      if (mounted) setState(() {});
    });
    refresh();
  }

  @override
  void dispose() {
    featureSubscription?.cancel();
    feature.dispose();
    super.dispose();
  }

  Future<void> refresh() => feature.refresh();
  Future<void> action(String endpoint, Map<String, dynamic> body) async =>
      feature.action(endpoint, body);

  String profileName(dynamic id) =>
      '${(connection.organizationDirectory.value['profiles'] as List? ?? []).where((p) => p['id'] == id).firstOrNull?['name'] ?? id}';
  String eventStatus(Map event) {
    if (event['state'] == 'validation_waived')
      return 'Merged · validation waived';
    if (event['state'] == 'validation_ready')
      return 'Merged · validation queued';
    if (event['state'] == 'validating') return 'Merged · validating';
    if (event['state'] == 'validation_pending' ||
        (event['state'] == 'blocked' &&
            event['verification']?['target_incorporated'] == true &&
            event['tests']?['status'] == 'not_run'))
      return 'Merged · validation pending';
    if (event['state'] == 'validation_failed' ||
        (event['state'] == 'blocked' &&
            event['verification']?['target_incorporated'] == true &&
            event['tests']?['status'] == 'failed'))
      return 'Merged · validation failed';
    return '${event['state']}';
  }

  String? validationRun(Map event) {
    final recorded = event['validation_run'];
    if (recorded is Map) return '${recorded['state'] ?? ''}';
    final match = (validation['runs'] as List? ?? [])
        .where((r) => r['event_id'] == event['id'])
        .firstOrNull;
    return match == null ? null : '${match['state'] ?? ''}';
  }

  Map? validationRecord(Map event) {
    final recorded = event['validation_run'];
    if (recorded is Map && recorded['run_id'] != null) return recorded;
    return (validation['runs'] as List? ?? [])
            .where((r) => r['event_id'] == event['id'])
            .firstOrNull
        as Map?;
  }

  Future<void> downloadLog(Map run) async {
    final epoch = connection.generation;
    final content = await feature.log(run);
    if (content == null || !(valid(epoch))) return;
    try {
      await downloadText(content, 'validation-${run['run_id']}.log');
      if (valid(epoch))
        await feature.patch({}, message: 'Validation log download started.');
    } catch (error) {
      if (valid(epoch)) await feature.reportError('log', error);
    }
  }

  Future<void> recoverWorker(Map event, String mode) async {
    final epoch = connection.generation;
    if (!await confirm(
          mode == 'recover'
              ? 'Recover saved worker result'
              : 'Continue with validation only',
          'Inspect the original worker conversation first. Recovery requires the same session to be idle. Validation only also verifies the target is already incorporated; it never repeats the merge.',
          confirmLabel: 'I inspected the conversation',
        ) ||
        !valid(epoch))
      return;
    await action('integration/recover', {
      'id': event['id'],
      'job_id': event['job_id'],
      'mode': mode,
      'inspected': true,
    });
  }

  Future<void> repair(Map config, {required bool fresh}) async {
    final epoch = connection.generation;
    if (fresh &&
        !await confirm(
          'Create a fresh coordinator summary?',
          'Inspect the coordinator conversation first. The previous request must no longer be running. This retires the old report and queues a new summary of current evidence. It preserves audit history and does not replay merges. If the old run was released, the same profile will be relaunched.',
          confirmLabel: 'Inspected · create summary',
        ))
      return;
    if (!valid(epoch)) return;
    await action('integration/repair', {
      'repository': component.repository,
      'job_id': config['report_job_id'],
      'mode': fresh ? 'fresh' : 'recover',
      if (fresh) 'inspected': true,
    });
    if (valid(epoch) && actionFailure == null)
      await feature.patch(
        {},
        message: fresh
            ? 'Fresh summary requested. The gateway waits for the coordinator to be ready.'
            : 'Saved coordinator report recovered.',
      );
  }

  Future<void> guidance(Map event) async {
    final epoch = connection.generation;
    if (!admin || busy) return;
    if (!await confirm(
          'Repair worker guidance',
          'Inspect the worker first and confirm it is idle. Modified guidance is quarantined and rebuilt. This does not resend the job. Administrator access is required.',
          confirmLabel: 'I inspected the idle worker',
        ) ||
        !valid(epoch))
      return;
    await action('integration/guidance', {
      'id': event['id'],
      'inspected': true,
    });
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
    final id = List.generate(
      16,
      (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
    ).join();
    bool merged(Map e) =>
        e['verification']?['target_incorporated'] == true &&
        e['verification']?['conflicts'] == 0 &&
        e['verification']?['merging'] != true;
    String defaultAction(Map e) => merged(e)
        ? (enabled ? 'retry_validation' : 'waive_validation')
        : 'reconsider';
    final eligible = candidates.where((e) => merged(e) || enabled).toList();
    final approved = await showDialog<bool>(
      'Review integration blockers',
      (close) => stack([
        paragraph(
          'Approve actions for these commits and worktrees only. Missing tools remain missing; a waiver records skipped validation, never a test pass. No CLI permissions change.',
        ),
        toggle(
          'Select all eligible actions',
          eligible.isNotEmpty &&
              eligible.every((e) => selected.containsKey(e['id'])),
          (value) {
            selected.clear();
            if (value)
              for (final e in eligible) {
                selected[e['id']] = defaultAction(e);
              }
            refreshDialogs();
          },
        ),
        for (final e in candidates)
          card('${e['name']} · ${eventStatus(e)}', [
            codeText(
              '${e['path']} → ${e['target']}\n${e['reason'] ?? ''}\n${e['tests']?['summary'] ?? ''}${'${e['blocker_label'] ?? ''}'.isEmpty ? '' : '\nBlocker: ${e['blocker_label']}'}',
            ),
            toggle(
              'Approve this action',
              selected.containsKey(e['id']),
              (value) {
                if (value) {
                  selected[e['id']] = defaultAction(e);
                } else {
                  selected.remove(e['id']);
                }
                refreshDialogs();
              },
              disabled: !merged(e) && !enabled,
            ),
            if (selected.containsKey(e['id']))
              select(
                'Approved action',
                selected[e['id']]!,
                {
                  if (merged(e) && enabled)
                    'retry_validation': 'Run validation again',
                  if (merged(e))
                    'waive_validation': 'Accept validation gap (waiver)',
                  if (!merged(e) && enabled)
                    'reconsider': 'Ask worker to reassess blocker',
                },
                (value) {
                  selected[e['id']] = value;
                  refreshDialogs();
                },
              ),
          ]),
        paragraph(
          'Pinned toolchains are installed by the root service from Settings. Conflicts are reassessed by the worker; uncertain delivery and missing snapshots require separate inspection.',
        ),
        textarea(
          'Reason for approval (maximum 1,000 characters)',
          explanation,
          (value) {
            explanation = value.substring(0, min(value.length, 1000)).trim();
            refreshDialogs();
          },
        ),
        actions([
          button('Cancel', () => close(false)),
          button(
            'Approve selected actions',
            () => close(true),
            primary: true,
            disabled: selected.isEmpty || explanation.isEmpty,
          ),
        ]),
      ]),
    );
    if (approved != true || !valid(epoch)) return;
    await action('integration/blockers', {
      'request_id': id,
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

  Future<void> eventDetails(Map event) => showDialog<void>(
    '${event['name']} · ${eventStatus(event)}',
    (close) => stack([
      codeText(
        'Checkout: ${event['path']}\nTarget: ${event['target']}\nUpdated: ${displayTime(event['updated_at'])}\nReason: ${event['reason'] ?? ''}\nBlocker category: ${'${event['blocker_label'] ?? ''}'.isEmpty ? 'None recorded' : event['blocker_label']}\nValidation runner: ${validationRun(event) ?? 'Not run'}\nCheckpoint: ${event['checkpoint'] ?? ''}\nJob: ${event['job_id'] ?? 'Not submitted'}\nMerge mode: ${event['merge_mode'] ?? 'Not recorded'}\nGit verification: ${event['verification'] ?? 'Not recorded'}\nOperator approval: ${event['operator_approval'] ?? 'None'}\nTests (worker-reported): ${event['tests']?['status'] ?? 'Not reported'}\n${event['tests']?['summary'] ?? ''}\nRecovery: ${event['recovery']?['ref'] ?? 'Not created yet; required before merge delivery'}',
      ),
      actions([
        if (event['state'] == 'needs_attention' && event['job_id'] != null) ...[
          button('Recover saved result', () {
            close(null);
            recoverWorker(event, 'recover');
          }, disabled: busy),
          button('Continue validation only', () {
            close(null);
            recoverWorker(event, 'validate');
          }, disabled: busy),
        ],
        button('Repair worker guidance', () {
          close(null);
          guidance(event);
        }, disabled: busy || !admin),
        if (validationRun(event) != 'running')
          button(
            'Validate exact commit',
            () {
              close(null);
              action('validation/run', {'id': event['id']});
            },
            disabled: busy || '${event['target'] ?? ''}'.isEmpty,
          ),
        if (validationRecord(event) != null)
          button(
            'Download validation log',
            () => downloadLog(validationRecord(event)!),
            disabled: busy,
          ),
        button('Close', () => close(null)),
      ]),
    ]),
  );
  String recoveryCommand(Map event) =>
      'git worktree add --detach <new-recovery-folder> ${event['recovery']['ref']}^1\ngit -C <new-recovery-folder> restore --source=${event['recovery']['ref']} --worktree -- .\ngit -C <new-recovery-folder> read-tree ${event['recovery']['ref']}^2';
  Component eventRow(Map event, bool enabled) {
    final status = eventStatus(event);
    final tone = stateTone(status);
    return el('li', [
      iconTile(
        switch (tone) {
          'danger' => 'triangle-alert',
          'warning' => 'hourglass',
          'success' => 'git-merge',
          _ => 'git-commit-horizontal',
        },
        tone: tone,
        size: 'sm',
      ),
      el('div', [
        el('p', [
          el('strong', [text(event['name'])]),
          pill(status, tone: tone, shape: shapeFor(tone)),
        ], classes: 'integration-event__title'),
        el('p', [
          text('${event['path']} → ${event['target']}'),
        ], classes: 'integration-event__change mono'),
        if ('${event['reason'] ?? ''}'.isNotEmpty)
          el('p', [text(event['reason'])], classes: 'integration-event__text'),
        facts([
          if ('${event['blocker_label'] ?? ''}'.isNotEmpty)
            ('Blocker', mono(event['blocker_label'])),
          if (validationRun(event) != null)
            ('Exact-commit validation', text(validationRun(event))),
          if ('${event['checkpoint'] ?? ''}'.isNotEmpty)
            ('Checkpoint', mono(event['checkpoint'])),
        ]),
        actions([
          button(
            'Review decision',
            () => eventDetails(event),
            small: true,
            iconName: 'scan',
          ),
          if (validationRecord(event) != null &&
              validationRun(event) != 'running')
            button(
              'Download validation log',
              () => downloadLog(validationRecord(event)!),
              disabled: busy,
              small: true,
              iconName: 'download',
            ),
          if ([
            'deferred',
            'blocked',
            'validation_pending',
            'validation_failed',
          ].contains(event['state']))
            button(
              status.startsWith('Merged') ? 'Retry validation' : 'Ask again',
              () => action('integration/retry', {'id': event['id']}),
              disabled: busy || !enabled,
              small: true,
              iconName: 'rotate-ccw',
            ),
        ]),
      ], classes: 'integration-event__body'),
    ], classes: 'integration-event');
  }

  @override
  Component build(BuildContext context) {
    final config = (data['configurations'] as List? ?? [])
        .where((c) => c['repository'] == component.repository)
        .firstOrNull;
    final events = (data['events'] as List? ?? [])
        .where((e) => e['repository'] == component.repository)
        .toList();
    final org =
        connection.selectedOrganization.value ??
        (connection.organizationDirectory.value['organizations'] as List? ?? [])
            .firstOrNull?['id'];
    final enabled = config?['enabled'] == true;
    final blockers = events.any(
      (e) => [
        'blocked',
        'deferred',
        'validation_pending',
        'validation_failed',
        'validation_waived',
      ].contains(e['state']),
    );
    final audit = (data['audit'] as List? ?? [])
        .where((a) => a['repository'] == component.repository)
        .toList();
    return card(
      'Integration coordinator',
      [
        el('details', [
          el('summary', [
            text(
              enabled
                  ? '${events.length} tracked decisions · idle-only delivery'
                  : 'Coordinate updates without interrupting active work',
            ),
          ]),
          facts([
            ('Last scan', mono(displayTime(checked))),
            (
              'Signed in',
              text(
                '${connection.operator.value['name']} · ${connection.operator.value['role']}',
              ),
            ),
            ('Launch details', text('Org chart → Runs')),
            if (config != null)
              (
                'Coordinator',
                text(
                  '${config['coordinator_state'] ?? 'Checking launch'} ${config['coordinator_error'] ?? ''}',
                ),
              ),
          ]),
          el('p', [
            text(
              'The gateway queues exact-commit updates. Enabling coordination lets workers merge after choosing integrate_now; deferred work waits for a checkpoint. Workers can report blockers. The coordinator summarizes supplied Git verification, worker-reported tests and recovery references. Inspect pending permissions in Org chart → Chat. Existing worktrees and sessions stay in place.',
            ),
          ], classes: 'integration__about'),
          if (config?['report_state'] == 'waiting')
            callout([
              calloutText(
                config?['coordinator_state'] == 'persona_sent'
                    ? 'Fresh summary waiting for collected worker outcomes; the coordinator is ready.'
                    : 'Fresh summary waiting: coordinator is not ready (${config?['coordinator_state'] ?? 'not launched'}). ${config?['coordinator_error'] ?? ''} Inspect its run; delivered once ready.',
              ),
            ], iconName: 'hourglass'),
          if (config != null && !enabled)
            callout(
              [
                calloutText(
                  'Coordination is paused. Resume it to request retries; delivered jobs still finish.',
                ),
              ],
              tone: 'neutral',
              iconName: 'circle-dashed',
            ),
          if ([
            'uncertain',
            'needs_attention',
          ].contains(config?['report_state']))
            callout(
              [
                calloutText(
                  'Coordinator report needs attention. ${config?['report_error'] ?? ''} Inspect the conversation in Org chart → Chat. Worker integration continues independently.',
                ),
              ],
              tone: 'danger',
              actions: [
                button(
                  'Recover saved report',
                  () => repair(config, fresh: false),
                  disabled: busy || !enabled,
                  small: true,
                ),
                button(
                  'Create fresh summary',
                  () => repair(config, fresh: true),
                  disabled: busy || !enabled,
                  small: true,
                ),
              ],
            ),
          el('div', [
            if (!enabled)
              button(
                'Enable coordinator',
                () => action('integration/configure', {
                  'repository': component.repository,
                  'organization_id': org,
                  'profile_id': config?['profile_id'] ?? 'new',
                  'enabled': true,
                }),
                primary: true,
                disabled: busy || org == null,
                iconName: 'play',
              )
            else
              button(
                'Pause coordination',
                () => action('integration/configure', {
                  'repository': component.repository,
                  'enabled': false,
                }),
                disabled: busy,
                iconName: 'square',
              ),
            if (enabled) ...[
              switchField(
                'Install a missing toolchain automatically',
                config?['auto_sdk'] == true,
                (value) => action('integration/configure', {
                  'repository': component.repository,
                  'organization_id': config?['organization_id'] ?? org,
                  'profile_id': config?['profile_id'],
                  'enabled': true,
                  'auto_sdk': value,
                }),
                disabled: busy || !admin,
              ),
              meta(
                'Queues the pinned toolchain once only when the release declares exactly one. Toolchains are listed in Settings. Only the root service installs it.',
              ),
              switchField(
                'Build incorporated commits automatically',
                config?['auto_build'] == true,
                (value) => action('integration/configure', {
                  'repository': component.repository,
                  'profile_id': config?['profile_id'],
                  'enabled': true,
                  'auto_build': value,
                }),
                disabled:
                    busy ||
                    !admin ||
                    (validation['executor'] != 'service' &&
                        config?['auto_build'] != true),
              ),
              meta(
                validation['executor'] == 'service'
                    ? 'Queues the exact commit after Git verification; failed builds need explicit retry. Deployment remains an administrator action.'
                    : 'Requires the durable build service. Install as root: bash /opt/herdr-web/install/build-install.sh',
              ),
            ],
          ], classes: 'integration__controls'),
          for (final event in events)
            if ('${event['automatic_build_error'] ?? ''}'.isNotEmpty)
              callout([
                calloutText(
                  'Automatic build waiting · ${event['name']}: ${event['automatic_build_error']}',
                ),
              ], tone: 'warning'),
          if (validation['error'] != null)
            callout([
              calloutText(
                'Validation runner unavailable: ${validation['error']}',
              ),
            ], tone: 'danger'),
          feedback(),
          if (actionFailure != null) notice(actionFailure, error: true),
          el('div', [
            el('h3', [text('Worker decisions')], classes: 'overline'),
            if (blockers)
              button(
                'Review blockers',
                () => reviewBlockers(events, enabled),
                disabled: busy,
                small: true,
                iconName: 'list-checks',
              ),
          ], classes: 'integration__section-head'),
          if (events.isEmpty && enabled)
            emptyState(
              'No worker events yet. Job completion and remote fetches wake the watcher; a 60-second background scan remains as fallback.',
              iconName: 'git-merge',
            ),
          if (events.isNotEmpty)
            el(
              'ul',
              [for (final event in events.take(20)) eventRow(event, enabled)],
              classes: 'integration-events',
              attributes: {'role': 'list'},
            ),
          el('div', [
            el('h3', [text('Recovery')], classes: 'overline'),
          ], classes: 'integration__section-head'),
          meta(
            'Recovery snapshots include tracked and non-ignored untracked files, plus staging. Ignored files and submodule contents need separate backups. Recover in a separate checkout to preserve current work.',
            iconName: 'shield-check',
          ),
          for (final event in (data['recoveries'] as List? ?? events).where(
            (e) =>
                e['repository'] == component.repository &&
                e['recovery'] != null,
          ))
            disclosure(
              'Recovery · ${event['name'] ?? profileName(event['profile_id'])} · ${event['path']} · before integration',
              [
                codeText(
                  'Snapshot: ${event['recovery']['commit']}\nReference: ${event['recovery']['ref']}\nIn this repository use a new empty folder:\n${recoveryCommand(event)}\nOriginal staging tree: ${event['recovery']['ref']}^2\nOriginal HEAD: ${event['recovery']['ref']}^1',
                ),
                button(
                  'Copy recovery command',
                  () async {
                    final result = await copyText(
                      recoveryCommand(event),
                      filename: 'recovery-command.txt',
                    );
                    if (mounted) await feature.patch({}, message: result);
                  },
                  small: true,
                  iconName: 'copy',
                ),
              ],
            ),
          disclosure('Audit history', [
            meta(
              'Recent 200 audit records. Recovery snapshots remain until explicitly removed.',
            ),
            if (audit.isNotEmpty)
              table(
                ['Action', 'Time', 'Actor', 'Reason'],
                [
                  for (final entry in audit)
                    [
                      text(
                        '${entry['action']}${entry['state'] != null
                            ? ' · ${entry['state']}'
                            : entry['enabled'] == true
                            ? ' · enabled'
                            : entry['enabled'] == false
                            ? ' · paused'
                            : ''}',
                      ),
                      mono(displayTime(entry['at'])),
                      text(
                        entry['name'] ??
                            profileName(entry['profile_id'] ?? 'Gateway'),
                      ),
                      text(entry['reason'] ?? ''),
                    ],
                ],
              ),
          ]),
        ], classes: 'integration'),
      ],
      iconName: 'git-merge',
      actions: [
        pill(
          config == null
              ? 'Not configured'
              : enabled
              ? 'Enabled'
              : 'Paused',
          tone: enabled ? 'success' : 'neutral',
          shape: config == null
              ? 'dash'
              : enabled
              ? 'filled'
              : 'hollow',
        ),
        button(
          'Refresh coordination',
          refresh,
          disabled: busy || refreshing,
          small: true,
          variant: 'ghost',
          iconName: 'refresh-cw',
        ),
      ],
      classes: 'integration-board',
    );
  }
}
