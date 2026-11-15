import 'package:herdr_dashboard_state/planning_blocs.dart';
import 'snapshot_terminal.dart';
import 'dart:async';

import '../services.dart';

import '../ui.dart';

import '../navigation.dart';

import 'org_forms.dart';

import 'org_ui.dart';

/// Delivery stages are evidence, not proof of a completed turn. The labels keep

/// an acknowledged CLI return distinct from a verified reply file.

const deliveryStages = <String, String>{
  'submitting': 'Submitting the prompt',

  'command_returned': 'CLI returned (acknowledgment only)',

  'session_idle_reply_pending': 'Waiting for the reply file',

  'reply_verified': 'Reply verified',

  'not_submitted': 'Not submitted',

  'unknown': 'Unknown',
};

String deliveryLabel(Object? stage) {
  final value = '${stage ?? ''}';

  if (value.isEmpty) return 'No delivery evidence';

  return deliveryStages[value] ?? value;
}

class CollaborationPanel extends StatefulComponent {
  const CollaborationPanel({
    super.key,

    required this.organization,

    required this.profiles,

    this.onOpenGroup,
  });

  final Map<String, dynamic> organization;

  final List<Map<String, dynamic>> profiles;

  final void Function(String)? onOpenGroup;

  @override
  State<CollaborationPanel> createState() => _CollaborationPanelState();
}

class _CollaborationPanelState extends State<CollaborationPanel> {
  DashboardBloc get connection => dashboardConnection;

  String prompt = '';

  late final CollaborationBloc bloc;
  StreamSubscription? featureSubscription;
  List<Map<String, dynamic>> get groups => bloc.groups;
  List<Map<String, dynamic>> get jobs => bloc.jobs;
  String? get profile => bloc.profile;
  set profile(String? value) => bloc.select(value);
  String? _error;
  String? get error => _error ?? bloc.state.error;
  set error(String? value) => _error = value;
  String get output => bloc.output;
  String get status => bloc.status;
  String get draft => bloc.draft;
  String? get draftJob => bloc.draftJob;
  bool get busy => bloc.state.busy;
  OrganizationCommand? get pending => bloc.pending;
  set pending(OrganizationCommand? value) {
    if (value == null) bloc.abandon();
  }

  @override
  void initState() {
    super.initState();
    bloc = CollaborationBloc(connection, '${component.organization['id']}');
    featureSubscription = bloc.stream.listen((_) {
      if (mounted) setState(() {});
    });
    refresh();
  }

  @override
  void dispose() {
    featureSubscription?.cancel();
    bloc.dispose();

    super.dispose();
  }

  Future<void> refresh() => bloc.refresh();

  Future<void> submit(
    String action,
    Map<String, dynamic> values, {
    bool retry = false,
  }) async {
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    _error = null;
    final saved = await bloc.submit(action, values, retry: retry);
    if (saved &&
        mounted &&
        epoch == connection.generation &&
        action == 'chat') {
      setState(() => prompt = '');
    }
  }

  Future<void> editGroup([Json? group]) async {
    final epoch = connection.generation;
    final v = await groupForm(component.profiles, group);
    if (v != null && mounted && epoch == connection.generation)
      await submit('group', v);
  }

  bool occupied(String id) => jobs.any(
    (j) =>
        ['queued', 'running'].contains(j['state']) &&
        (j['participants'] as List? ?? [j['profile_id']]).contains(id),
  );

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

  String memberName(Object? id) =>
      '${component.profiles.where((p) => p['id'] == id).firstOrNull?['name'] ?? id}';
  Object? memberRole(Object? id) =>
      component.profiles.where((p) => p['id'] == id).firstOrNull?['role'];

  Component record(Json job) {
    final chat = job['kind'] == 'chat';
    final delivery = job['delivery'] is Map ? job['delivery'] as Map : null;
    return el('li', [
      el(
        'span',
        [icon(chat ? 'message-square' : 'users')],
        classes: 'icon-tile icon-tile--sm',
        attributes: {'aria-hidden': 'true'},
      ),
      el('div', [
        el('div', [
          el('h3', [
            text(chat ? 'Message' : '${job['group']?['name'] ?? 'Discussion'}'),
          ], classes: 'collab-record__title'),
          statePill(job['state']),
          if (job['created_at'] != null)
            el('span', [
              timeStamp(job['created_at']),
            ], classes: 'org-run__time'),
        ], classes: 'collab-record__head'),
        if (job['prompt'] != null)
          el('div', [
            if (chat) el('span', [text('You')], classes: 'collab-bubble__who'),
            textBlock(job['prompt']),
          ], classes: 'collab-bubble'),
        if (job['progress'] != null) note(job['progress']),
        if (job['error'] != null) errorLine(job['error']),

        if (delivery != null)
          el('section', [
            factList([
              ('Delivery', text(deliveryLabel(delivery['stage']))),
              if ('${delivery['reply_name'] ?? ''}'.isNotEmpty)
                ('Reply file', mono(delivery['reply_name'])),
              for (final field in [
                'started_at',
                'returned_at',
                'checked_at',
                'verified_at',
              ])
                if ('${delivery[field] ?? ''}'.isNotEmpty)
                  (humanize(field), mono(delivery[field])),
              ('Job', mono(job['id'])),
            ]),
            if (delivery['recovered'] == true)
              note('Recovered from a saved reply; no terminal input was sent.'),
          ], classes: 'delivery-evidence'),

        if (chat && ['needs_attention', 'uncertain'].contains(job['state']))
          row([
            button(
              'Recover saved reply',
              () async {
                final epoch = connection.generation;
                if (await confirm(
                      'Recover saved reply',
                      'Confirm you inspected the original conversation and the agent is idle. Recovery reads the saved reply without resending the prompt.',
                    ) &&
                    mounted &&
                    epoch == connection.generation)
                  await submit('recover', {
                    'job_id': job['id'],
                    'chat': true,
                    'inspected': true,
                  });
              },
              disabled: busy || pending != null,
              small: true,
              iconName: 'history',
            ),
          ]),

        if ('${job['result'] ?? ''}'.isNotEmpty) ...[
          disclosure(
            chat
                ? (job['result_format'] == 'markdown'
                      ? 'View agent reply'
                      : 'View earlier terminal snapshot')
                : 'View artifact',
            [pre(job['result'])],
            iconName: 'file-text',
          ),
          row([
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
          ]),
        ],

        if ((job['contributions'] as List? ?? []).isNotEmpty)
          disclosure('Discussion contributions', [
            el(
              'ol',
              [
                for (final part in records(job['contributions']))
                  el('li', [
                    agentTile(part['name'], size: 'xs'),
                    el('div', [
                      el('p', [
                        el('strong', [text(part['name'])]),
                        text(' · Round ${part['round']}'),
                      ], classes: 'timeline__text'),
                      textBlock(
                        part['content'],
                        classes: 'collab-contribution',
                      ),
                    ]),
                  ], classes: 'timeline__item'),
              ],
              classes: 'timeline timeline--trail',
              attributes: {'role': 'list'},
            ),
          ], iconName: 'users'),
      ], classes: 'collab-record__body'),
    ], classes: 'collab-record');
  }

  Component groupRow(Json group) {
    final members = group['members'] as List? ?? [];
    return el('li', [
      el(
        'span',
        [icon('message-square')],
        classes: 'icon-tile icon-tile--sm',
        attributes: {'aria-hidden': 'true'},
      ),
      el('div', [
        el('h3', [text('${group['name']}')], classes: 'collab-group__name'),
        if ('${group['description'] ?? ''}'.isNotEmpty)
          note(group['description'], classes: 'collab-group__description'),
        el('div', [
          el('span', [text('Members')], classes: 'visually-hidden'),
          for (final id in members)
            memberChip(memberName(id), role: memberRole(id)),
        ], classes: 'org-chips'),
        row([
          button(
            'Start discussion',
            () => submit('discuss', {'group_id': group['id']}),
            disabled:
                busy || pending != null || members.any((id) => occupied('$id')),
            primary: true,
            small: true,
            iconName: 'play',
          ),
          button(
            'Open group',
            () {
              if (component.onOpenGroup != null) {
                component.onOpenGroup!('${group['id']}');
              } else {
                navigate('/groups/${group['id']}');
              }
            },
            small: true,
            iconName: 'arrow-right',
          ),
          button(
            'Edit group',
            () => editGroup(group),
            disabled: busy || pending != null,
            small: true,
            variant: 'ghost',
            iconName: 'pencil',
          ),
          button(
            'Remove group',
            () async {
              final epoch = connection.generation;
              if (await confirm(
                    'Remove ${group['name']}?',
                    'Removes this group and closes its facilitator terminal. Members remain; history, artifacts and files are preserved.',
                  ) &&
                  mounted &&
                  epoch == connection.generation)
                await submit('remove_group', {'group_id': group['id']});
            },
            disabled: busy || pending != null,
            small: true,
            variant: 'ghost',
            classes: 'btn--danger',
            iconName: 'trash-2',
          ),
        ]),
      ], classes: 'collab-group__body'),
    ], classes: 'collab-group');
  }

  Component chatCard() {
    final history = jobs.reversed
        .where((j) => j['kind'] == 'chat' && j['profile_id'] == profile)
        .toList();
    return card(
      'Chat with an agent',
      [
        select(
          'Agent',
          profile ?? '',
          {
            '': 'Select agent',
            for (final p in component.profiles) '${p['id']}': '${p['name']}',
          },
          (value) {
            if (busy) return;
            setState(() {
              profile = value.isEmpty ? null : value;
              error = null;
            });
            refresh();
          },
        ),

        textarea(
          'Ask a question or give instructions',
          prompt,
          (v) => prompt = v,
        ),

        el('div', [
          if (status.isNotEmpty) statePill(status),
          button(
            'Send prompt',
            () {
              final value = prompt.trim();
              if (value.isNotEmpty && value.length <= 8000)
                submit('chat', {'profile_id': profile, 'prompt': value});
            },
            disabled:
                busy ||
                pending != null ||
                profile == null ||
                occupied(profile!),
            primary: true,
            iconName: 'send',
          ),
        ], classes: 'collab-send'),

        if (status == 'blocked')
          callout('warning', 'triangle-alert', [
            note(
              'Read the terminal request. Approval applies to the currently selected terminal option.',
            ),
          ]),

        if (draftJob != null)
          disclosure('Reply in progress', [
            pre(
              draft.isEmpty
                  ? 'Waiting for the agent to write its answer…'
                  : draft,
            ),
          ], iconName: 'message-square'),

        if (output.isNotEmpty)
          disclosure('Live terminal output', [
            SnapshotTerminal(output, key: ValueKey(profile)),
          ], iconName: 'terminal'),

        if (profile != null && output.isNotEmpty)
          el('div', [
            el('span', [text('Send key')], classes: 'toolbar__label'),
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
                      if (busy || pending != null || occupied(profile!))
                        'disabled': '',
                    },
                    events: {
                      'click': (_) =>
                          submit('input', {'profile_id': profile, 'key': key}),
                    },
                  ),
              ],
              classes: 'btn-group',
              attributes: {'role': 'group', 'aria-label': 'Terminal keys'},
            ),
          ], classes: 'collab-keys'),

        if (history.isNotEmpty) ...[
          el('h3', [text('Conversation')], classes: 'overline collab-history'),
          el(
            'ol',
            [for (final job in history) record(job)],
            classes: 'collab-records',
            attributes: {'role': 'list'},
          ),
        ],
      ],
      iconName: 'message-square',
      subtitle:
          'Launch the agent first. Replies contain the agent answer; terminal output is available separately.',
      classes: 'collab-chat',
    );
  }

  @override
  Component build(BuildContext context) {
    final discussions = jobs.reversed
        .where((j) => j['kind'] == 'discussion')
        .toList();
    return el('div', [
      el('div', [
        chatCard(),
        card(
          'Discussion groups',
          [
            note(
              'Choose 2–6 agents and a desired outcome. A dedicated facilitator coordinates members and saves the artifact. Launch members before starting.',
            ),
            if (groups.isEmpty)
              emptyState(
                'No discussion groups yet. Create a group to start a discussion.',
                iconName: 'users',
              )
            else
              el(
                'ul',
                [for (final group in groups) groupRow(group)],
                classes: 'collab-groups',
                attributes: {'role': 'list'},
              ),
          ],
          iconName: 'users',
          count: groups.length,
          actions: [
            button(
              'Create group',
              () => editGroup(),
              disabled: busy || pending != null,
              small: true,
              iconName: 'plus',
            ),
          ],
          classes: 'collab-groups-card',
        ),
      ], classes: 'collab-grid'),

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

      if (discussions.isNotEmpty)
        card(
          'Discussions',
          [
            el(
              'ol',
              [for (final job in discussions) record(job)],
              classes: 'collab-records',
              attributes: {'role': 'list'},
            ),
          ],
          iconName: 'history',
          count: discussions.length,
        ),
    ], classes: 'collab');
  }
}
