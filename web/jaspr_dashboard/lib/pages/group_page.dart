import 'snapshot_terminal.dart';
import 'folder_access_panel.dart';
import 'dart:async';

import 'dart:convert';

import '../services.dart';

import '../navigation.dart';

import '../ui.dart';

import 'org_forms.dart';

import 'organization_automation.dart';

import 'org_ui.dart';

/// Job states that keep a group conversation live. Shared so the poll interval

/// and the live-streaming guard cannot drift apart.

const _activeJobStates = [
  'queued',

  'running',

  'waiting_for_members',

  'needs_attention',

  'waiting_for_input',
];

class GroupPage extends StatefulComponent {
  const GroupPage({super.key, required this.id});

  final String id;

  @override
  State<GroupPage> createState() => _GroupPageState();
}

class _GroupPageState extends State<GroupPage> {
  DashboardBloc get connection => dashboardConnection;

  Map<String, dynamic>? group;

  List<Map<String, dynamic>> profiles = [];

  List<Map<String, dynamic>> jobs = [];

  Map<String, dynamic> memberStates = {};

  List<Map<String, dynamic>> allJobs = [];

  final live = <String, Map<String, dynamic>>{};

  String message = '';

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

  OrganizationCommand? pending;
  final proposalRequests = <String, String>{};
  StreamSubscription? connectionSubscription;
  int generation = -1;

  @override
  void initState() {
    super.initState();
    generation = connection.generation;
    connectionSubscription = connection.stream.listen((_) {
      if (!mounted) return;
      if (generation != connection.generation || !connection.state.connected) {
        generation = connection.generation;
        setState(() {
          group = null;
          profiles = [];
          jobs = [];
          memberStates = {};
          allJobs = [];
          live.clear();
          loaded = false;
          snapshotSignature = null;
          pending = null;
          proposalRequests.clear();
          busy = false;
          error = null;
          nextBefore = null;
          message = '';
        });
      }
      if (connection.state.connected) refresh();
    });

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

    connectionSubscription?.cancel();
    timer?.cancel();

    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || !connection.state.connected) return;

    loading = true;

    final epoch = connection.generation;

    try {
      final data = await connection.request('organizations/history', {
        'group_id': component.id,

        'limit': historyLimit,
      });

      if (!mounted || epoch != connection.generation) return;

      final current =
          List<Map<String, dynamic>>.from(
            data['groups'] ?? [],
          ).where((g) => g['id'] == component.id).firstOrNull ??
          data['group'] as Map<String, dynamic>?;

      final signature = jsonEncode(data);

      if (signature != snapshotSignature)
        setState(() {
          snapshotSignature = signature;

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
                (j) =>
                    j['kind'] == 'discussion' && j['group_id'] == component.id,
              )
              .toList()
              .reversed
              .toList();

          jobs.addAll(older);

          loaded = true;

          if (pending == null) error = null;
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
        setState(() => error = exception.toString());
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
        'group_id': component.id,

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
      });
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      loading = false;
    }
  }

  Future<void> submit(
    String action,

    Map<String, dynamic> body, {

    bool retry = false,
  }) async {
    if (busy ||
        group == null ||
        !connection.state.connected ||
        (!retry && pending != null) ||
        (retry && pending == null))
      return;

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

      if (removed && mounted) navigate('/organization');

      if (action == 'discuss') setState(() => message = '');
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> edit() async {
    final epoch = connection.generation;
    final v = await groupForm(profiles, group);
    if (v != null && mounted && epoch == connection.generation)
      await submit('group', v);
  }

  /// Create and queue a task from a recorded proposal.
  ///
  /// The automatic path decides for a whole discussion at once and records its
  /// verdict once, so a proposal it left as a draft, skipped, or marked
  /// unavailable could never be started afterwards. This is the operator's path
  /// for those — including every proposal from a discussion finalized before
  /// automatic follow-up was enabled. The gateway derives the task id from the
  /// discussion and the proposal, so pressing twice cannot duplicate the task.
  Future<void> startProposal(Json job, Json proposal) async {
    if (busy || pending != null || connection.operator.value['role'] != 'admin')
      return;
    final epoch = connection.generation;
    final assignee =
        profileOf(proposal['profile_id'])?['name'] ?? proposal['profile_id'];
    if (!await confirm(
          'Start this proposal as a task?',
          'Creates and queues a task assigned to $assignee. Automatic limits do '
              'not apply to an explicit start, and the assignee must still exist.',
        ) ||
        !mounted ||
        epoch != connection.generation ||
        busy ||
        pending != null ||
        connection.operator.value['role'] != 'admin')
      return;
    final key = '${job['id']}:${proposal['key']}';
    final idempotencyKey = proposalRequests.putIfAbsent(key, requestId);
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await connection.request('tasks/start_proposal', {
        'job_id': job['id'],
        'proposal_key': proposal['key'],
        'request_id': idempotencyKey,
      });
      if (mounted && epoch == connection.generation) {
        final id = result['id'];
        if (id is String && id.isNotEmpty) {
          proposalRequests.remove(key);
          navigate('/tasks/$id');
        } else {
          throw StateError(
            'The gateway did not return the created task. Retry to recover it.',
          );
        }
      }
    } catch (error) {
      if (mounted && epoch == connection.generation)
        setState(() => this.error = '$error');
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() => busy = false);
    }
  }

  Future<void> managePreparation(Json job, bool retry) async {
    final epoch = connection.generation;
    if (await confirm(
          retry
              ? 'Retry inspected session preparation?'
              : 'Cancel waiting meeting?',
          retry
              ? 'Inspect and repair member sessions first. This retries preparation only. Submitted discussions must recover artifacts instead.'
              : 'No discussion prompt has been sent. Cancel this waiting meeting and release its scheduling reservation.',
        ) &&
        mounted &&
        epoch == connection.generation)
      await submit(retry ? 'retry_discussion' : 'cancel_discussion', {
        'job_id': job['id'],
        'inspected': true,
      });
  }

  Future<void> realignSession(Json job, Json mismatch, bool force) async {
    final epoch = connection.generation;
    if (!await confirm(
      force ? 'Force realign session?' : 'Realign session?',
      'Session project: ${mismatch['session_project']}\nCurrent project: ${mismatch['configured_project']}\n${force ? 'Stops even a working or blocked stale session after preserving visible output.' : 'Archives and closes only a verified idle session.'} Launches a fresh session from the current profile, preserving checkout files. Other jobs and ambiguous identities prevent closure. Unsubmitted discussion resumes automatically when members are ready.',
    ))
      return;

    if (mounted && epoch == connection.generation)
      await submit('realign_discussion_session', {
        'job_id': job['id'],
        'run_id': mismatch['run_id'],
        'expected_project': mismatch['configured_project'],
        'expected_alias': mismatch['alias'],
        'inspected': true,
        'force': force,
      });
  }

  Future<void> respondToBlock(Json stream) async {
    final epoch = connection.generation;
    if (await confirm(
          'Respond to ${stream['name']}',
          'Review the terminal request and selected option:\n${stream['output'] ?? ''}\nConfirm sends Enter. For artifact requests, select Allow once first. Saved permission policy is unchanged.',
        ) &&
        mounted &&
        epoch == connection.generation)
      await submit('input', {
        'profile_id': stream['profile_id'],
        'key': 'enter',
        'console_token': stream['console_token'],
      });
  }

  Future<void> export(Json job, bool download) async {
    try {
      if (download) {
        await downloadText(
          '${job['result']}',
          'discussion-artifact-${job['id']}.md',
        );
      } else {
        await copyText(
          '${job['result']}',
          filename: 'discussion-artifact-${job['id']}.md',
        );
      }
    } catch (e) {
      if (mounted) setState(() => error = 'Export failed: $e');
    }
  }

  Future<void> downloadTranscript(Json job) async {
    final epoch = connection.generation;

    try {
      final result = await connection.request('organizations/transcript', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'request_id': 'transcript_${DateTime.now().microsecondsSinceEpoch}',
      });
      if (mounted && epoch == connection.generation)
        await downloadText('${result['content']}', 'discussion.json');
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    }
  }

  Json? profileOf(Object? id) =>
      profiles.where((p) => p['id'] == id).firstOrNull;

  /// One agent message in a discussion: role tile lead, name and round in
  /// the summary, the message itself behind the disclosure.
  Component contribution(Json part) => el('li', [
    agentTile(
      part['name'],
      role: profileOf(part['profile_id'])?['role'],
      size: 'xs',
    ),
    disclosure('${part['name']} · Round ${part['round']}', [
      textBlock(part['content'], classes: 'collab-contribution'),
    ], classes: 'group-message'),
  ], classes: 'timeline__item');

  Component liveDiscussion(Json job) {
    final data = live[job['id']] ?? <String, dynamic>{};

    final discussion = disclosure(
      'Live discussion',
      [
        if (data['inspection_error'] != null)
          errorLine('Live output unavailable: ${data['inspection_error']}'),
        if (records(data['streams']).isEmpty &&
            data['inspection_error'] == null)
          el(
            'p',
            [
              text(
                live.containsKey(job['id'])
                    ? 'No terminal streams have been returned for this discussion yet.'
                    : 'Loading live terminal output…',
              ),
            ],
            attributes: {'role': 'status'},
          ),

        if (records(data['contributions']).isNotEmpty)
          el(
            'ol',
            [
              for (final part in records(data['contributions']))
                contribution(part),
            ],
            classes: 'timeline timeline--trail',
            attributes: {'role': 'list'},
          ),

        if ('${data['artifact_draft'] ?? ''}'.isNotEmpty)
          disclosure('Artifact draft · Not finalized', [
            pre(data['artifact_draft']),
          ], iconName: 'file-text'),

        for (final stream in records(data['streams']))
          Component.element(
            tag: 'div',
            key: ValueKey('${job['id']}-${stream['profile_id']}'),
            classes: 'group-live-stream',
            attributes: {'data-profile-id': '${stream['profile_id']}'},
            children: [
              disclosure('${stream['name']} · ${stream['status']}', [
                if (stream['scratch_directory'] != null)
                  note('Session scratch: ${stream['scratch_directory']}'),
                if ('${stream['error'] ?? ''}'.isNotEmpty)
                  errorLine('Terminal output unavailable: ${stream['error']}'),
                if ('${stream['output'] ?? ''}'.isEmpty &&
                    '${stream['error'] ?? ''}'.isEmpty)
                  el(
                    'p',
                    [
                      text(
                        stream['status'] == 'starting'
                            ? 'Session is starting; waiting for terminal output.'
                            : 'No retained terminal output has been returned yet.',
                      ),
                    ],
                    attributes: {'role': 'status'},
                  ),
                SnapshotTerminal(
                  '${stream['output'] ?? ''}',
                  key: ValueKey('${job['id']}-${stream['profile_id']}'),
                ),

                if (stream['input_result'] is Map)
                  note(
                    'Terminal response: ${stream['input_result']['state']} ${stream['input_result']['error'] ?? ''}',
                  ),

                if (stream['status'] == 'blocked')
                  el('div', [
                    if (stream['console_token'] != null)
                      button(
                        'Respond to permission or question',
                        () => respondToBlock(stream),
                        disabled: busy || pending != null,
                        small: true,
                        primary: true,
                      ),
                    el(
                      'div',
                      [
                        for (final key in [
                          'up',
                          'down',
                          'tab',
                          'enter',
                          'esc',
                          'ctrl+c',
                        ])
                          el(
                            'button',
                            [text(key)],
                            classes: 'btn-group__btn collab-keys__key',
                            attributes: {
                              'type': 'button',
                              if (busy || pending != null) 'disabled': '',
                            },
                            events: {
                              'click': (_) => submit('input', {
                                'profile_id': stream['profile_id'],
                                'key': key,
                                if (stream['console_token'] != null)
                                  'console_token': stream['console_token'],
                              }),
                            },
                          ),
                      ],
                      classes: 'btn-group',
                      attributes: {
                        'role': 'group',
                        'aria-label': 'Terminal keys',
                      },
                    ),
                  ], classes: 'collab-keys'),
              ], iconName: 'terminal'),
            ],
          ),
      ],
      iconName: 'activity',
      classes: 'group-live__details',
    );
    return el('div', [
      // Keep requests visible outside collapsed live terminal output.
      if (data['inspection_error'] == null)
        for (final stream in records(data['streams']))
          folderAccessPanel(stream, name: '${stream['name']}'),
      discussion,
    ], classes: 'group-live');
  }

  Component discussion(Json job) {
    final admin = connection.operator.value['role'] == 'admin';
    return el('li', [
      el(
        'span',
        [icon('users')],
        classes: 'icon-tile icon-tile--sm',
        attributes: {'aria-hidden': 'true'},
      ),
      el('div', [
        el('div', [
          el('h3', [text('Discussion')], classes: 'collab-record__title'),
          statePill(job['state']),
          el('span', [timeStamp(job['created_at'])], classes: 'org-run__time'),
        ], classes: 'collab-record__head'),
        if (job['prompt'] != null)
          el('div', [textBlock(job['prompt'])], classes: 'collab-bubble')
        else
          note('Started a discussion with the group.'),

        disclosure('Discussion context', [
          textBlock(job['group']?['description'] ?? ''),
          el('div', [
            for (final r in (job['runs'] as List? ?? []))
              memberChip(
                r['profile']?['name'] ?? r['profile_id'],
                role: r['profile']?['role'],
              ),
          ], classes: 'org-chips'),
        ], iconName: 'info'),

        if (job['progress'] != null) note(job['progress']),
        if (job['error'] != null) errorLine(job['error']),

        for (final mismatch in records(job['project_mismatches']))
          callout(
            'warning',
            'triangle-alert',
            [
              factList([
                ('Session', mono(mismatch['session_project'])),
                ('Configured', mono(mismatch['configured_project'])),
                ('Group target', mono(mismatch['expected_project'])),
              ]),

              if (mismatch['recovery_pending'] == true)
                note(
                  'Archived session awaits relaunch: ${mismatch['recovery_error'] ?? ''}',
                ),

              if (mismatch['repairable'] != true)
                note(
                  'Update agent/group settings first. Task-owned or starting sessions require inspection.',
                ),

              if (admin &&
                  mismatch['repairable'] == true &&
                  ['needs_attention', 'uncertain'].contains(job['state']))
                row([
                  button(
                    'Archive and relaunch in current project',
                    () => realignSession(job, mismatch, false),
                    disabled: busy || pending != null,
                    small: true,
                  ),
                  button(
                    'Force realign session',
                    () => realignSession(job, mismatch, true),
                    disabled: busy || pending != null,
                    small: true,
                    variant: 'ghost',
                    classes: 'btn--danger',
                  ),
                ]),
            ],
            title: '${mismatch['name']}: project mismatch',
          ),

        if (admin &&
            job['preparation_version'] == 1 &&
            job['discussion_submitted_at'] == null &&
            [
              'waiting_for_members',
              'queued',
              'needs_attention',
              'uncertain',
            ].contains(job['state']))
          row([
            if (['needs_attention', 'uncertain'].contains(job['state']))
              button(
                'Retry inspected preparation',
                () => managePreparation(job, true),
                disabled: busy || pending != null,
                small: true,
                iconName: 'rotate-ccw',
              ),
            button(
              'Cancel waiting meeting',
              () => managePreparation(job, false),
              disabled: busy || pending != null,
              small: true,
              iconName: 'x',
            ),
          ]),

        if ([
              'needs_attention',
              'uncertain',
              'waiting_for_input',
            ].contains(job['state']) &&
            (job['discussion_submitted_at'] != null ||
                job['preparation_version'] == null))
          row([
            button(
              'Recover saved artifact',
              () => submit('recover', {'job_id': job['id']}),
              disabled: busy || pending != null,
              small: true,
              iconName: 'history',
            ),
          ]),

        if (job == jobs.firstOrNull && _activeJobStates.contains(job['state']))
          liveDiscussion(job),

        if (records(job['contributions']).isNotEmpty)
          el(
            'ol',
            [
              for (final part in records(job['contributions']))
                contribution(part),
            ],
            classes: 'timeline timeline--trail',
            attributes: {'role': 'list'},
          ),

        if ('${job['result'] ?? ''}'.isNotEmpty)
          row([
            button(
              'View artifact',
              () => setState(() {
                tab = 'artifacts';
                artifact = '${job['id']}';
              }),
              small: true,
              iconName: 'file-text',
            ),
          ]),
      ], classes: 'collab-record__body'),
    ], classes: 'collab-record');
  }

  Component onOff(bool on) =>
      statePill(on ? 'on' : 'off', label: on ? 'On' : 'Off');

  List<Component> postsPanel(bool archived, bool disabled) => [
    if (!archived)
      card(
        'Message the group',
        [
          textarea(
            'Ask the group agent to coordinate its members',
            message,
            (v) => message = v,
          ),
          el('div', [
            button(
              'Send to group',
              () {
                final value = message.trim();
                if (value.isNotEmpty && value.length <= 8000)
                  submit('discuss', {
                    'group_id': component.id,
                    'prompt': value,
                  });
              },
              disabled: disabled,
              primary: true,
              iconName: 'send',
            ),
          ], classes: 'collab-send'),
        ],
        iconName: 'send',
        classes: 'group-composer',
      ),
    card(
      'Discussions',
      [
        if (jobs.isEmpty)
          emptyState('No discussions yet.', iconName: 'message-square')
        else
          el(
            'ol',
            [for (final job in jobs) discussion(job)],
            classes: 'collab-records',
            attributes: {'role': 'list'},
          ),
        if (nextBefore != null)
          el('div', [
            button(
              'Load older discussions',
              loadOlder,
              disabled: loading,
              small: true,
              iconName: 'history',
            ),
          ], classes: 'org-more'),
      ],
      iconName: 'history',
      count: jobs.length,
    ),
  ];

  List<Component> artifactsPanel() {
    final shown = jobs
        .where(
          (j) =>
              '${j['result'] ?? ''}'.isNotEmpty &&
              (artifact == null || j['id'] == artifact),
        )
        .toList();
    return [
      if (artifact != null)
        el('div', [
          button(
            'Show all artifacts',
            () => setState(() => artifact = null),
            small: true,
            iconName: 'arrow-left',
          ),
        ], classes: 'org-more org-more--start'),

      if (!jobs.any((j) => '${j['result'] ?? ''}'.isNotEmpty))
        card('Artifacts', [
          emptyState(
            'Artifacts appear here after a discussion completes.',
            iconName: 'file-text',
          ),
        ], iconName: 'file-text'),

      for (final job in shown)
        card(
          'Discussion artifact',
          [
            if (job['release_plan_outcome'] is Map) ...[
              note(
                job['release_plan_outcome']['state'] == 'created'
                    ? 'Release plan ${job['release_plan_outcome']['version']} created. Planned tasks appear on the workflow board.'
                    : 'Release plan not created: ${job['release_plan_outcome']['reason']}',
              ),
              button(
                'View workflow board',
                () => navigate('/tasks'),
                small: true,
                iconName: 'rocket',
              ),
            ],
            pre(job['result']),
            row([
              if (job['task_id'] != null)
                button(
                  'Review task and follow-up proposals',
                  () => navigate('/tasks/${job['task_id']}'),
                  small: true,
                  primary: true,
                  iconName: 'list-checks',
                ),
              button(
                'Copy Markdown',
                () => export(job, false),
                small: true,
                iconName: 'copy',
              ),
              button(
                'Download Markdown',
                () => export(job, true),
                small: true,
                iconName: 'download',
              ),
              button(
                'Download discussion.json',
                () => downloadTranscript(job),
                small: true,
                iconName: 'download',
              ),
              button(
                'Back to posts',
                () => setState(() => tab = 'posts'),
                small: true,
                variant: 'ghost',
                iconName: 'arrow-left',
              ),
            ]),
          ],
          iconName: 'file-text',
          subtitle: stamp(job['created_at']),
          classes: 'group-artifact',
        ),
    ];
  }

  Component membersPanel(List<String> members, bool archived) => card(
    'Members',
    [
      el(
        'div',
        [
          el('table', [
            el('caption', [text('Group members')], classes: 'visually-hidden'),
            el('thead', [
              el('tr', [
                for (final (label, name) in [
                  ('Member', 'user'),
                  ('Status', 'activity'),
                ])
                  el(
                    'th',
                    [
                      el('span', [
                        icon(name, classes: 'icon--sm'),
                        text(label),
                      ], classes: 'th'),
                    ],
                    attributes: {'scope': 'col'},
                  ),
                el(
                  'th',
                  [
                    el('span', [text('Action')], classes: 'visually-hidden'),
                  ],
                  classes: 'org-team__actions',
                  attributes: {'scope': 'col'},
                ),
              ]),
            ]),
            el('tbody', [
              for (final id in members)
                el('tr', [
                  el(
                    'th',
                    [
                      agentIdentity(
                        profileOf(id)?['name'] ?? id,
                        role: profileOf(id)?['role'],
                        meta: '${profileOf(id)?['role'] ?? ''}',
                        live: memberStates[id]?['status'] == 'working',
                      ),
                    ],
                    attributes: {'scope': 'row'},
                  ),
                  el('td', [
                    statePill(memberStates[id]?['status'] ?? 'unknown'),
                  ]),
                  el('td', [
                    button(
                      memberStates[id]?['active'] == true ? 'On' : 'Turn on',
                      () async {
                        final run = memberStates[id]?['run_id'];
                        if (run != null) {
                          await submit('release', {'job_id': run});
                          if (pending != null || !mounted) return;
                        }
                        await submit('launch', {'profile_id': id});
                      },
                      disabled:
                          busy ||
                          archived ||
                          pending != null ||
                          memberStates[id]?['active'] == true ||
                          ![
                            'off',
                            'stopped',
                            'exited',
                          ].contains(memberStates[id]?['status']),
                      small: true,
                      iconName: 'power',
                    ),
                  ], classes: 'org-team__actions'),
                ]),
            ]),
          ], classes: 'table org-members'),
        ],
        classes: 'table-scroll',
        attributes: {'role': 'region', 'aria-label': 'Group members'},
      ),
    ],
    iconName: 'users',
    count: members.length,
    subtitle:
        'Turn on an offline member to launch its saved persona. Active members are on. Uncertain runs need inspection from the organization chart.',
  );

  List<Component> settingsPanel(bool archived) => [
    card(
      'Group settings',
      [
        factList([
          ('Automatic queueing', onOff(group!['create_tasks'] == true)),
          ('Outcome reviews', onOff(group!['notify_outcomes'] != false)),
          ('Read-only discussion', onOff(group!['read_only'] != false)),
          ('Scope', text(humanize(group!['review_scope'] ?? 'topic'))),
        ], classes: 'org-policy__list'),
        note('Members retain individual permissions.'),
      ],
      iconName: 'settings',
      actions: [
        button(
          'Edit group settings',
          edit,
          disabled: busy || archived || pending != null,
          small: true,
          iconName: 'pencil',
        ),
      ],
    ),
    OrganizationAutomation(organizationId: '${group!['organization_id']}'),
  ];

  Component proposalsPanel() {
    final proposals = [
      for (final job in jobs)
        for (final p in records(job['proposal_details'])) (job, p),
    ];
    return card(
      'Proposals',
      [
        if (proposals.isEmpty)
          emptyState('No proposals yet.', iconName: 'list-checks')
        else
          el(
            'ul',
            [
              for (final (job, p) in proposals)
                el('li', [
                  el('div', [
                    el('h3', [
                      text('${p['title']}'),
                    ], classes: 'collab-record__title'),
                    statePill(p['status'] ?? 'Awaiting evaluation'),
                  ], classes: 'collab-record__head'),
                  el('p', [
                    text('Discussion '),
                    mono(job['id']),
                    text(
                      ' · Assignee: ${profileOf(p['profile_id'])?['name'] ?? p['profile_id']}',
                    ),
                  ], classes: 'org-note'),
                  if ('${p['reason'] ?? ''}'.isNotEmpty) note(p['reason']),
                  if ([
                    'true',
                    'yes',
                  ].contains('${p['needs_review']}'.toLowerCase()))
                    callout('warning', 'triangle-alert', [
                      note('Operator review required before execution.'),
                    ]),
                  disclosure(
                    'Acceptance criteria and required checks',
                    [textBlock(p['description'])],
                    iconName: 'list-checks',
                  ),
                  if (p['task_id'] != null)
                    row([
                      button(
                        'Open created task',
                        () => navigate('/tasks/${p['task_id']}'),
                        small: true,
                        iconName: 'arrow-right',
                      ),
                    ])
                  else if (connection.operator.value['role'] == 'admin')
                    row([
                      button(
                        'Start as task',
                        () => startProposal(job, p),
                        disabled: busy || pending != null,
                        small: true,
                        iconName: 'play',
                      ),
                    ]),
                ], classes: 'group-proposal'),
            ],
            classes: 'group-proposals',
            attributes: {'role': 'list'},
          ),
        if (nextBefore != null)
          el('div', [
            button(
              'Load older proposals',
              loadOlder,
              disabled: loading,
              small: true,
              iconName: 'history',
            ),
          ], classes: 'org-more'),
      ],
      iconName: 'list-checks',
      count: proposals.length,
      actions: [
        button(
          'View limits',
          () => setState(() => tab = 'settings'),
          small: true,
          variant: 'ghost',
          iconName: 'settings',
        ),
      ],
      subtitle:
          'Creation and queueing outcomes are recorded per discussion. Skipped proposals have not become tasks.',
    );
  }

  @override
  Component build(BuildContext context) {
    if (!connection.state.connected)
      return el('div', [
        card('Group', [
          emptyState(
            'The dashboard is not connected.',
            iconName: 'wifi-off',
            actions: [button('Connect on the dashboard', () => navigate('/'))],
          ),
        ], iconName: 'message-square'),
      ], classes: 'group-page');

    if (group == null)
      return el('div', [
        card('Group', [
          emptyState(
            error ?? (loaded ? 'Group not found.' : 'Loading group…'),
            iconName: 'message-square',
            actions: [button('Refresh', refresh, iconName: 'refresh-cw')],
          ),
        ], iconName: 'message-square'),
      ], classes: 'group-page');

    final archived = group!['removed_at'] != null;

    final members = List<String>.from(group!['members'] ?? []);

    final occupied = allJobs.any(
      (j) =>
          ['queued', 'running', 'waiting_for_input'].contains(j['state']) &&
          (j['participants'] as List? ?? [j['profile_id']]).any(
            members.contains,
          ),
    );

    final disabled = busy || pending != null || occupied || archived;

    return el('div', [
      pageHeader(
        '${group!['name']}',
        overline: 'Discussion group',
        actions: [
          button(
            'Project knowledge',
            () => navigate('/knowledge/${group!['organization_id']}'),
            iconName: 'book-open',
          ),
          button(
            'Edit group',
            edit,
            disabled: busy || pending != null || archived,
            iconName: 'pencil',
          ),
          button(
            'Remove group',
            () async {
              final epoch = connection.generation;
              if (await confirm(
                    'Remove ${group!['name']}?',
                    'Removes this group and closes its facilitator terminal. Member agents remain. History, artifacts and worktree files are preserved.',
                  ) &&
                  mounted &&
                  epoch == connection.generation)
                await submit('remove_group', {'group_id': component.id});
            },
            disabled: disabled,
            variant: 'danger',
            iconName: 'trash-2',
          ),
          button(
            'Start discussion',
            () => submit('discuss', {'group_id': component.id}),
            disabled: disabled,
            primary: true,
            iconName: 'play',
          ),
        ],
      ),

      el('section', [
        el('div', [
          el('span', [
            text('${members.length} members'),
          ], classes: 'group-summary__count'),
          el('div', [
            for (final id in members)
              memberChip(
                profileOf(id)?['name'] ?? id,
                role: profileOf(id)?['role'],
                status: '${memberStates[id]?['status'] ?? 'unknown'}',
              ),
          ], classes: 'org-chips'),
        ], classes: 'group-summary__members'),
        disclosure('Purpose and instructions', [
          textBlock(group!['description']),
        ], iconName: 'file-text'),
        if (archived)
          callout('neutral', 'archive', [
            note(
              'Archived group · Saved discussions and downloads remain available.',
            ),
          ]),
      ], classes: 'card group-summary'),

      el('div', [
        viewTabs(
          'group-view',
          'Group sections',
          {
            'posts': ('Posts', 'message-square'),
            'artifacts': ('Artifacts', 'file-text'),
            'proposals': ('Proposals', 'list-checks'),
            'settings': ('Settings', 'settings'),
            'members': ('Members', 'users'),
            'about': ('About', 'info'),
          },
          tab,
          (value) => setState(() {
            tab = value;
            artifact = null;
          }),
        ),
      ], classes: 'org-tabsrow'),

      if (error != null) errorLine(error),
      if (pending != null && !busy)
        el('div', [
          icon('history', classes: 'icon--sm'),
          row([
            button(
              'Retry same request',
              () => submit(pending!.action, const {}, retry: true),
              small: true,
            ),
            button(
              'Dismiss request',
              () => setState(() {
                pending = null;
                error = null;
              }),
              small: true,
              variant: 'ghost',
            ),
          ]),
        ], classes: 'org-retry'),

      tabPanel('group-view', tab, [
        if (tab == 'posts') ...postsPanel(archived, disabled),
        if (tab == 'artifacts') ...artifactsPanel(),
        if (tab == 'members') membersPanel(members, archived),
        if (tab == 'settings') ...settingsPanel(archived),
        if (tab == 'proposals') proposalsPanel(),
        if (tab == 'about')
          card('About this group', [
            textBlock(group!['description']),
            note(
              'This group has its own Herdr conversation. Its agent coordinates members and synthesizes discussion artifacts, choosing rounds and follow-ups. Posts and artifacts preserve the description and members used for that discussion. Review recommendations before action.',
            ),
          ], iconName: 'info'),
      ]),
    ], classes: 'group-page');
  }
}
