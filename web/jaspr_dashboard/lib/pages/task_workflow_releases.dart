part of 'task_workflow.dart';

String releaseStateLabel(Object? state) => switch (state) {
  'collecting' => 'Collecting changes',
  'integrating' => 'Integrating',
  'validating' => 'Validating combined result',
  'ready' => 'Ready for publication',
  'blocked' => 'Needs attention',
  'validation_failed' => 'Combined checks failed',
  'repair_queued' => 'Conflict agent queued',
  'repairing' => 'Agent resolving conflict',
  'excluding' => 'Excluding blocked member',
  'publishing' => 'Publishing branch',
  'published' => 'Branch published',
  _ => 'State unavailable',
};

List<Map<String, dynamic>> releaseMembers(Map release) => [
  for (final item in release['members'] as List? ?? [])
    if (item is Map) Map<String, dynamic>.from(item),
];

extension _Releases on _TaskWorkflowState {
  List<String> get releaseRepositories => {
    for (final record in [...component.tasks, ...component.releases])
      if ('${record['repository'] ?? ''}'.isNotEmpty) '${record['repository']}',
  }.toList()..sort();
  Iterable<Map<String, dynamic>> get visibleReleases =>
      component.releases.where(
        (release) => repository.isEmpty || release['repository'] == repository,
      );

  Future<void> loadHistory(Map release, {String? revision, int? before}) async {
    final loader = component.onLoadReleaseHistory;
    if (loader == null) return;
    final request = ++historyRequest;
    update(() {
      historyRelease = '${release['id']}';
      historyLoading = true;
      historyError = null;
      if (revision == null && before == null) {
        retainedRelease = null;
        retainedRevisions = [];
        historyBefore = null;
      }
    });
    try {
      final data = await loader('${release['id']}', revision, before);
      if (!mounted ||
          request != historyRequest ||
          selected != 'release:${release['id']}')
        return;
      update(() {
        if (revision != null) {
          retainedRelease = Map<String, dynamic>.from(data['release'] as Map);
        } else {
          retainedRevisions = [
            if (before != null) ...retainedRevisions,
            for (final item in data['revisions'] as List? ?? [])
              Map<String, dynamic>.from(item as Map),
          ];
          historyBefore = data['next_before'] as int?;
        }
      });
    } catch (error) {
      if (mounted &&
          request == historyRequest &&
          selected == 'release:${release['id']}') {
        update(() => historyError = '$error');
      }
    } finally {
      if (mounted && request == historyRequest)
        update(() => historyLoading = false);
    }
  }

  Component releaseHistoryInspector(Map<String, dynamic> current) {
    final retained = retainedRelease;
    if (retained != null) return releaseInspector(retained, historical: true);
    return el('aside', [
      el('header', [
        el('h2', [
          text('${current['version']} · retained revisions'),
        ], classes: 'inspector__title'),
      ], classes: 'inspector__header'),
      el('div', [
        button(
          'Return to current release',
          () => update(() {
            historyRelease = null;
            historyRequest++;
            historyLoading = false;
          }),
        ),
        if (historyError != null) notice(historyError, error: true),
        if (historyLoading)
          el(
            'p',
            [text('Loading retained revisions…')],
            attributes: {'role': 'status'},
          ),
        if (!historyLoading && retainedRevisions.isEmpty)
          el('p', [text('No frozen revisions have been retained yet.')]),
        for (final revision in retainedRevisions)
          el('div', [
            button(
              'Revision ${revision['generation']} · ${releaseStateLabel(revision['state'])}',
              () => loadHistory(current, revision: '${revision['digest']}'),
              disabled: historyLoading,
            ),
            if (revision['current'] == true) badge('Current manifest'),
            el('p', [text(revision['digest'])], classes: 'mono'),
            if (revision['resulting_sha'] != null)
              el('p', [
                text('Combined commit ${revision['resulting_sha']}'),
              ], classes: 'mono'),
          ], classes: 'wf-release__member'),
        if (historyBefore != null)
          button(
            'Load older revisions',
            () => loadHistory(current, before: historyBefore),
            disabled: historyLoading,
          ),
      ], classes: 'inspector__body'),
    ], classes: 'inspector task-wf-inspector');
  }

  void releaseAction(
    String action,
    Map release, [
    Map<String, dynamic> extra = const {},
  ]) {
    component.onReleaseAction?.call(action, {
      'id': release['id'],
      'edit_version': release['edit_version'],
      if (release['revision'] is Map) 'revision': release['revision']['digest'],
      ...extra,
    });
  }

  Component releaseNode(
    Map<String, dynamic> release,
    _Box box,
    Set<String> highlighted,
  ) {
    final id = box.id;
    final counts = release['counts'] as Map? ?? {};
    final total = counts['selected'] ?? releaseMembers(release).length;
    final incorporated = counts['incorporated'] ?? 0;
    final state = releaseStateLabel(release['state']);
    return el(
      'button',
      [
        el('span', [
          icon('rocket'),
          text(release['version']),
        ], classes: 'wf-node__title'),
        badge(
          state,
          tone: ['blocked', 'validation_failed'].contains(release['state'])
              ? 'danger'
              : 'accent',
        ),
        el(
          'span',
          [
            for (final member in releaseMembers(release))
              el(
                'span',
                [],
                attributes: {
                  'data-member-state': '${member['state'] ?? 'pending'}',
                },
              ),
          ],
          classes: 'wf-release__progress',
          attributes: {'aria-hidden': 'true'},
        ),
        el('span', [
          text('$incorporated of $total incorporated'),
        ], classes: 'wf-release__count'),
        if (release['planned_cut_at'] != null)
          el('span', [
            icon('calendar'),
            text('Planned cut ${release['planned_cut_at']}'),
          ], classes: 'wf-release__cut'),
      ],
      classes: 'task-wf-node wf-node wf-release',
      attributes: {
        'type': 'button',
        'id': '$instance-node-$id',
        'data-node': id,
        'data-release': '${release['id']}',
        'data-state': '${release['state']}',
        'data-in': '',
        'aria-pressed': '${selected == id}',
        'aria-label':
            '${release['version']}. $state. $incorporated of $total selected changes incorporated.',
        if (highlighted.isNotEmpty && !highlighted.contains(id)) 'data-dim': '',
        if (highlighted.contains(id)) 'data-emphasis': '',
        'style': 'left:${box.x}px;top:${box.y}px;height:${boxHeight(box)}px;',
      },
      events: {
        'click': (_) => chooseTask(selected == id ? null : id),
        'keydown': (event) => keyFor(id, event as web.KeyboardEvent),
        'pointerenter': (_) => update(() => hovered = id),
        'pointerleave': (_) => update(() => hovered = null),
      },
    );
  }

  Component releaseCreateInspector() => el('aside', [
    el('header', [
      el('h2', [text('New release candidate')], classes: 'inspector__title'),
    ], classes: 'inspector__header'),
    el('div', [
      select(
        'Repository',
        newReleaseRepository,
        {for (final repository in releaseRepositories) repository: repository},
        (value) => update(() => newReleaseRepository = value),
      ),
      field(
        'Release name',
        newReleaseName,
        (value) => update(() => newReleaseName = value),
      ),
      field(
        'Planned cut date (optional, YYYY-MM-DD)',
        newReleaseCut,
        (value) => update(() => newReleaseCut = value),
      ),
      el('p', [
        text(
          'Add task candidates, then freeze the exact selection before integration. Publication is a separate action.',
        ),
      ]),
      button(
        'Create release candidate',
        () => component.onReleaseAction?.call('create', {
          'repository': newReleaseRepository,
          'version': newReleaseName,
          if (newReleaseCut.isNotEmpty) 'planned_cut_at': newReleaseCut,
        }),
        primary: true,
        disabled:
            component.releaseBusy ||
            newReleaseName.trim().isEmpty ||
            newReleaseRepository.isEmpty,
      ),
    ], classes: 'inspector__body'),
  ], classes: 'inspector task-wf-inspector');

  Component releaseMembership(
    Map<String, dynamic> task,
  ) => section('Release membership', [
    for (final release in component.releases.where(
      (r) => r['repository'] == task['repository'],
    ))
      () {
        final member = releaseMembers(
          release,
        ).where((m) => m['task_id'] == task['id']).firstOrNull;
        if (release['state'] != 'collecting' && member == null)
          return const Component.fragment([]);
        return el('div', [
          button(
            '${release['version']}',
            () => chooseTask('release:${release['id']}', reveal: true),
            small: true,
          ),
          if (member != null)
            text(
              '${member['state'] ?? 'Selected'} · ${shortSha('${member['candidate_sha'] ?? ''}')}',
            ),
          if (component.onReleaseAction != null &&
              release['state'] == 'collecting')
            button(
              '${member == null ? 'Add to' : 'Remove from'} ${release['version']}',
              () => releaseAction('membership', release, {
                'task_id': task['id'],
                'included': member == null,
              }),
              small: true,
              disabled: component.releaseBusy,
            ),
        ], classes: 'wf-release__member');
      }(),
    if (!component.releases.any((r) => r['repository'] == task['repository']))
      el('p', [
        text('No release candidate for this repository.'),
      ], classes: 'wf-insp__muted'),
  ]);

  Component releaseInspector(
    Map<String, dynamic> release, {
    bool historical = false,
  }) {
    if (!historical && historyRelease == release['id'])
      return releaseHistoryInspector(release);
    final members = releaseMembers(release);
    final counts = release['counts'] as Map? ?? {};
    final revision = release['revision'] as Map?;
    final validation = release['validation'] as Map?;
    final canAct = component.onReleaseAction != null && !historical;
    return el('aside', [
      el('header', [
        el('h2', [
          icon('rocket'),
          text(release['version']),
        ], classes: 'inspector__title'),
        badge(releaseStateLabel(release['state']), tone: 'accent'),
      ], classes: 'inspector__header'),
      el('div', [
        if (historical) ...[
          notice(
            'Retained revision ${revision?['generation']} · read-only evidence. This is not the current release state.',
          ),
          button(
            'Back to retained revisions',
            () => update(() => retainedRelease = null),
          ),
        ] else if (component.onLoadReleaseHistory != null)
          button(
            'Inspect retained revisions',
            () => loadHistory(release),
            small: true,
          ),
        if ('${release['error'] ?? ''}'.isNotEmpty)
          notice(release['error'], error: true),
        section('Frozen selection', [
          el('dl', [
            term('Repository', release['repository']),
            term('Selected', members.length),
            term('Incorporated', counts['incorporated'] ?? 0),
            term('Blocked', counts['blocked'] ?? 0),
            term('Excluded with reason', counts['excluded'] ?? 0),
            if (release['planned_cut_at'] != null)
              term('Planned cut', release['planned_cut_at']),
            if (revision != null) ...[
              term('Manifest digest', revision['digest'], mono: true),
              term('Base', revision['base_sha'], mono: true),
              term('Policy digest', revision['policy_digest'], mono: true),
            ],
            if (release['resulting_sha'] != null)
              term('Combined commit', release['resulting_sha'], mono: true),
            if (release['branch'] != null)
              term('Release branch', release['branch'], mono: true),
            if (release['publication'] is Map)
              term(
                'Publication destination',
                release['publication']['destination'],
              ),
            if (historical && release['updated_at'] != null)
              term('Recorded at', release['updated_at']),
          ], classes: 'dl'),
          el('p', [
            text(
              'Membership is separate from task dependencies. Incorporation does not mean merged into main. Hidden tasks remain in the selected count.',
            ),
          ], classes: 'wf-insp__muted'),
        ]),
        section('Selected changes', [
          for (final member in members)
            el(
              'div',
              [
                button(
                  '${member['title'] ?? member['task_id']}',
                  () => component.onOpenTask('${member['task_id']}'),
                  small: true,
                ),
                badge('${member['state'] ?? 'Selected'}'),
                el('p', [
                  text(
                    'Candidate ${member['candidate_sha'] ?? 'not captured'}',
                  ),
                ], classes: 'mono'),
                if (member['integration_sha'] != null)
                  el('p', [
                    text('Incorporated at ${member['integration_sha']}'),
                  ], classes: 'mono'),
                if (member['reason'] != null)
                  notice(member['reason'], error: true),
                if (member['eligibility_reason'] != null)
                  el('p', [
                    text(member['eligibility_reason']),
                  ], classes: 'wf-insp__muted'),
              ],
              classes: 'wf-release__member',
              attributes: {'data-release-member': '${member['task_id']}'},
            ),
        ]),
        section('Combined validation', [
          text(
            validation == null
                ? 'Not yet verified. Individual task checks do not verify the combined result.'
                : '${validation['state']} · ${validation['required_checks_verified'] == true ? 'required checks verified' : 'required checks not verified'}',
          ),
          if (validation?['run_id'] != null)
            term('Validation run', validation!['run_id'], mono: true),
          for (final check in validation?['checks'] as List? ?? [])
            if (check is Map)
              el('div', [
                text(check['name'] ?? check['id'] ?? 'Check'),
                badge('${check['status'] ?? 'Result unavailable'}'),
                if (check['detail'] != null) el('p', [text(check['detail'])]),
              ], classes: 'wf-release__member'),
          if (validation?['run_id'] != null)
            button(
              'View combined build',
              () => navigation.navigate('/builds'),
              small: true,
            ),
        ]),
        if (canAct)
          section('Actions', [
            if (release['state'] == 'collecting')
              button(
                'Freeze and integrate ${members.length} tasks',
                () => releaseAction('freeze', release),
                primary: true,
                disabled: component.releaseBusy || members.isEmpty,
              ),
            if (release['state'] == 'validation_failed')
              button(
                'Re-run combined checks',
                () => releaseAction('retry', release),
                primary: true,
                disabled: component.releaseBusy,
              ),
            if (release['state'] == 'blocked') ...[
              button(
                'Resume inspected release',
                () => releaseAction('resume', release),
                disabled: component.releaseBusy,
              ),
              if (release['intent'] != null) ...[
                select(
                  'Conflict agent',
                  repairProfile,
                  {
                    '': 'Choose an agent',
                    for (final profile in component.profiles.where(
                      (p) =>
                          p['read_only'] != true &&
                          p['archived'] != true &&
                          p['group_id'] == null,
                    ))
                      '${profile['id']}': '${profile['name']}',
                  },
                  (value) => update(() => repairProfile = value),
                ),
                button(
                  'Ask agent to resolve conflict',
                  () => releaseAction('repair', release, {
                    'profile_id': repairProfile,
                  }),
                  disabled: component.releaseBusy || repairProfile.isEmpty,
                ),
                field(
                  'Reason to exclude blocked member',
                  exclusionReason,
                  (value) => update(() => exclusionReason = value),
                ),
                button(
                  'Exclude member and abort its unfinished merge',
                  () => releaseAction('exclude', release, {
                    'task_id': release['intent']['task_id'],
                    'reason': exclusionReason,
                  }),
                  disabled:
                      component.releaseBusy || exclusionReason.trim().isEmpty,
                ),
              ],
            ],
            if (release['repair_chat_id'] != null)
              term('Conflict agent run', release['repair_chat_id'], mono: true),
            if (release['state'] == 'ready')
              button(
                'Publish verified release branch',
                () => releaseAction('publish', release, {
                  'head_sha': release['resulting_sha'],
                }),
                primary: true,
                disabled:
                    component.releaseBusy || release['publication'] == null,
              ),
            if ([
              'blocked',
              'validation_failed',
              'ready',
              'published',
            ].contains(release['state']))
              button(
                'Revise selection',
                () => releaseAction('revise', release),
                disabled: component.releaseBusy,
              ),
            if (release['publication_unavailable'] != null)
              notice(release['publication_unavailable']),
            if (release['publication'] is Map) ...[
              term(
                'Publication destination',
                release['publication']['destination'],
              ),
              term('Publication branch', release['publication']['branch']),
            ],
          ]),
      ], classes: 'inspector__body'),
    ], classes: 'inspector task-wf-inspector');
  }
}
