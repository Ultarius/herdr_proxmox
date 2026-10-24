part of 'task_workflow.dart';

String releaseStateLabel(Object? state) => switch (state) {
  'collecting' => 'Collecting changes',
  'integrating' => 'Integrating',
  'validating' => 'Validating combined result',
  'ready' => 'Ready for publication',
  'assembled' => 'Assembly checked · awaiting freeze',
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
  Object? currentCandidate(Map member) {
    if (member.containsKey('current_candidate_sha'))
      return member['current_candidate_sha'];
    final task = component.tasks
        .where((task) => task['id'] == member['task_id'])
        .firstOrNull;
    return task?['head_sha'] ?? member['work']?['head_sha'];
  }

  bool candidateChanged(Map member) => member['candidate_changed'] is bool
      ? member['candidate_changed'] == true
      : member['candidate_sha'] != null &&
            currentCandidate(member) != null &&
            member['candidate_sha'] != currentCandidate(member);

  String memberElementId(Map release, Map member) =>
      '$instance-release-${release['id']}-member-${member['task_id']}';

  void revealMember(Map release, Map member) {
    final element =
        web.document.getElementById(memberElementId(release, member))
            as web.HTMLElement?;
    element?.scrollIntoView(
      web.ScrollIntoViewOptions(block: 'center', inline: 'nearest'),
    );
    element?.focus(web.FocusOptions(preventScroll: true));
  }

  List<Component> selectionUpdate(Map release, Map member) {
    final current = currentCandidate(member);
    final preview = selectionPreview;
    final reviewing =
        preview?['release_id'] == release['id'] &&
        preview?['task_id'] == member['task_id'];
    final stale =
        reviewing &&
        (preview?['candidate_sha'] != current ||
            preview?['previous_candidate_sha'] != member['candidate_sha'] ||
            preview?['edit_version'] != release['edit_version']);
    return [
      button(
        'Review updated selection',
        () => update(
          () => selectionPreview = {
            'release_id': release['id'],
            'task_id': member['task_id'],
            'previous_candidate_sha': member['candidate_sha'],
            'candidate_sha': current,
            'edit_version': release['edit_version'],
          },
        ),
        small: true,
        disabled: component.releaseBusy || current == null,
      ),
      if (reviewing)
        el(
          'div',
          [
            el('p', [
              text(
                'Use this reviewed commit in the release? Required checks must pass before freezing.',
              ),
            ]),
            el('p', [
              text('Replace ${preview?['previous_candidate_sha']}'),
            ], classes: 'mono'),
            el('p', [
              text('With ${preview?['candidate_sha']}'),
            ], classes: 'mono'),
            if (stale)
              notice(
                'The candidate or release selection changed again. Review the updated selection before continuing.',
                error: true,
              ),
            button(
              'Use reviewed candidate',
              () => releaseAction('membership', release, {
                'task_id': member['task_id'],
                'included': true,
                'candidate_sha': preview?['candidate_sha'],
                'previous_candidate_sha': preview?['previous_candidate_sha'],
                'edit_version': preview?['edit_version'],
              }),
              primary: true,
              disabled:
                  component.releaseBusy ||
                  stale ||
                  preview?['candidate_sha'] == null,
            ),
            button(
              'Cancel selection update',
              () => update(() => selectionPreview = null),
              small: true,
            ),
          ],
          classes: 'wf-release__selection-review',
          attributes: {
            'role': 'group',
            'aria-label': 'Review candidate replacement',
          },
        ),
    ];
  }

  Component memberWork(Map member) {
    final live = component.tasks
        .where((t) => t['id'] == member['task_id'])
        .firstOrNull;
    final work = member['work'] as Map? ?? {};
    final profileId = live?['profile_id'] ?? work['profile_id'];
    final profile = component.profiles
        .where((p) => p['id'] == profileId)
        .firstOrNull;
    final name =
        profile?['name'] ??
        (live == null ? 'Agent identity unavailable' : taskAgentName(live));
    final state = live?['state'] ?? work['state'] ?? 'unavailable';
    final activity = component.activity?[profileId];
    return el(
      'p',
      [
        text(
          '$name · task $state${activity == null ? '' : ' · session $activity'}',
        ),
      ],
      classes: 'wf-insp__muted',
      attributes: {'data-release-work': '${member['task_id']}'},
    );
  }

  Component releaseReview(Map release, bool canAct) {
    final review = release['review'] as Map?;
    final policy = release['review_policy'] as Map? ?? {};
    final group = releaseGroup.isNotEmpty
        ? releaseGroup
        : '${policy['group_id'] ?? ''}';
    final limit = releaseReviewLimit ?? '${policy['max_reviews'] ?? 3}';
    final active = ['queued', 'running'].contains(review?['state']);
    final beforePublication = ![
      'publishing',
      'published',
    ].contains(release['state']);
    return section('Group evidence review', [
      notice(
        'Groups assess missing capture, checks and integration risks. Their advice does not approve a release or authorize changes and publication.',
      ),
      if (review != null) ...[
        term('Review group', review['group_name']),
        term('Discussion state', review['state']),
        if (review['current'] == false)
          notice(
            'Outdated review: task or release evidence changed. Request a fresh review before relying on this advice.',
            error: true,
          ),
        term('Evidence packet', review['digest'], mono: true),
        if (review['job_id'] != null) ...[
          term('Discussion job', review['job_id'], mono: true),
          button(
            'Open group discussions',
            () => navigation.navigate('/groups/${review['group_id']}'),
            small: true,
          ),
        ],
        if (review['error'] != null) notice(review['error'], error: true),
        if (canAct && review['state'] == 'needs_attention')
          button(
            'Re-check interrupted group review',
            () => releaseAction('review', release, {'retry': true}),
            disabled: component.releaseBusy,
          ),
        if (review['report'] != null)
          el('pre', [text(review['report'])], classes: 'wf-insp__muted'),
      ],
      term(
        'Automatic review',
        !beforePublication
            ? 'Not scheduled after publication'
            : policy['enabled'] == true
            ? 'Enabled after the full selection passes combined checks'
            : 'Disabled',
      ),
      term(
        'Automatic reviews reserved',
        '${policy['reviews_used'] ?? 0} / ${policy['max_reviews'] ?? 3}',
      ),
      if (policy['notice'] != null) notice(policy['notice']),
      if (canAct) ...[
        select('Release review group', group, {
          '': 'Choose a repository group',
          for (final group in component.groups.where(
            (g) =>
                g['archived'] != true &&
                (release['organization_id'] == null ||
                    g['organization_id'] == release['organization_id']),
          ))
            '${group['id']}': '${group['name']}',
        }, (value) => update(() => releaseGroup = value)),
        if (!active && review?['state'] != 'needs_attention')
          button(
            'Ask group to review release evidence',
            () => releaseAction('review', release, {'group_id': group}),
            disabled:
                component.releaseBusy ||
                group.isEmpty ||
                releaseMembers(release).isEmpty,
          ),
        if (beforePublication)
          field(
            'Automatic review limit (1–20)',
            limit,
            (value) => update(() => releaseReviewLimit = value),
          ),
        if (beforePublication)
          button(
            policy['enabled'] == true
                ? 'Update automatic review authorization'
                : 'Enable automatic release review',
            () => releaseAction('review_policy', release, {
              'enabled': true,
              'group_id': group,
              'max_reviews': int.tryParse(limit),
            }),
            disabled:
                component.releaseBusy ||
                group.isEmpty ||
                int.tryParse(limit) == null,
          ),
        if (policy['enabled'] == true)
          button(
            'Disable automatic release review',
            () => releaseAction('review_policy', release, {'enabled': false}),
            disabled: component.releaseBusy,
          ),
      ],
    ]);
  }

  Component releasePull(Map release, bool canAct) {
    final pull = release['pull_request'] as Map?;
    final preview = release['pull_preview'] as Map? ?? {};
    final stale =
        releasePullSha != null && releasePullSha != release['resulting_sha'];
    canAct = canAct && release['publication_error'] == null;
    return section('Release pull request', [
      if (release['publication_error'] != null)
        notice(release['publication_error'], error: true),
      if (pull != null) ...[
        term('PR request', pull['state']),
        if (pull['message'] != null) notice(pull['message']),
        if (githubCompareUri(pull['compare_url']) != null)
          el(
            'a',
            [text('Open GitHub comparison and create draft PR')],
            attributes: {
              'href': '${pull['compare_url']}',
              'target': '_blank',
              'rel': 'noopener noreferrer',
            },
          ),
        if (pull['origin'] == 'adopted')
          notice(
            'Operator-created PR adopted after exact head, base and repository verification.',
          ),
        if (pull['remote_state'] != null)
          term('Observed GitHub status', pull['remote_state']),
        if (pull['merge_sha'] != null)
          term('Release merge commit', pull['merge_sha']),
        if (pull['remote_state'] == 'merged')
          notice(
            'Delivered through a merged release. Individual task SHAs need not be ancestors after squash or rebase; deployment acceptance remains separate.',
          ),
        if (release['pull_refresh_error'] != null)
          notice(release['pull_refresh_error'], error: true),
        if (canAct &&
            release['state'] == 'published' &&
            pull['state'] == 'complete')
          button(
            'Refresh release PR status',
            () => releaseAction('refresh', release),
            disabled: component.releaseBusy,
          ),
        if (pull['reused'] == true)
          notice(
            'An existing PR was reconciled. Its title and description were preserved.',
          ),
        if (pull['error'] != null) notice(pull['error'], error: true),
        if (pull['number'] != null)
          el(
            'a',
            [text('Open release PR #${pull['number']}')],
            attributes: {
              'href': '${pull['url']}',
              'target': '_blank',
              'rel': 'noopener noreferrer',
            },
          ),
      ],
      if (canAct &&
          release['state'] == 'published' &&
          !['queued', 'complete'].contains(pull?['state'])) ...[
        notice(
          'Review this exact combined commit. For manual adoption, copy the title and description into your draft PR on GitHub. Herdr preserves the existing PR’s text when adopting it. Merging and deployment are separate.',
        ),
        field(
          'Release PR title',
          releasePullTitle ?? '${preview['title'] ?? ''}',
          (value) => update(() {
            releasePullTitle = value;
            releasePullSha ??= '${release['resulting_sha']}';
          }),
        ),
        textarea(
          'Release PR description',
          releasePullBody ?? '${preview['body'] ?? ''}',
          (value) => update(() {
            releasePullBody = value;
            releasePullSha ??= '${release['resulting_sha']}';
          }),
        ),
        if (stale) ...[
          notice(
            'The combined commit changed. Reset and review the current PR preview.',
            error: true,
          ),
          button(
            'Reset release PR preview',
            () => update(
              () => releasePullTitle = releasePullBody = releasePullSha = null,
            ),
          ),
        ],
        button(
          release['manual_pr_required'] == true ||
                  pull?['state'] == 'awaiting_manual_pr'
              ? 'Check for operator-created release PR'
              : pull?['state'] == 'needs_attention'
              ? 'Reconcile or create draft release PR'
              : 'Create reviewed draft release PR',
          () => releaseAction('pull', release, {
            'head_sha': release['resulting_sha'],
            'reviewed': true,
            'title': releasePullTitle ?? preview['title'],
            'body': releasePullBody ?? preview['body'],
          }),
          disabled:
              component.releaseBusy ||
              stale ||
              (releasePullTitle ?? '${preview['title'] ?? ''}')
                  .trim()
                  .isEmpty ||
              (releasePullBody ?? '${preview['body'] ?? ''}').trim().isEmpty,
        ),
      ],
    ]);
  }

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
    final tasksById = {for (final task in component.tasks) task['id']: task};
    final implementing = releaseMembers(release)
        .where(
          (member) =>
              (tasksById[member['task_id']]?['state'] ??
                  member['work']?['state']) ==
              'implementing',
        )
        .length;
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
          text(
            '$incorporated of $total incorporated${implementing == 0 ? '' : ' · $implementing implementing'}',
          ),
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
        'click': (_) => chooseTask(selected == id && panel ? null : id),
        'keydown': (event) => keyFor(id, event as web.KeyboardEvent),
        'pointerenter': (_) => update(() => hovered = id),
        'pointerleave': (_) => update(() => hovered = null),
      },
    );
  }

  Component releaseCreateInspector() => el('aside', [
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
    if (component.onReleaseAction != null &&
        task['head_sha'] != null &&
        !['merged', 'closed', 'completed'].contains(task['state']))
      button(
        'Release this task only',
        () => component.onReleaseAction?.call('single', {
          'task_id': task['id'],
          'head_sha': task['head_sha'],
        }),
        disabled: component.releaseBusy,
      ),
    for (final receipt
        in (task['release_deliveries'] as List? ?? []).whereType<Map>()) ...[
      term('Release delivery', '${receipt['version']} · ${receipt['stage']}'),
      term('Included candidate', receipt['candidate_sha']),
      notice(
        receipt['current_candidate_matches'] == true
            ? 'Receipt covers the current candidate.'
            : 'Receipt covers an earlier candidate; it does not deliver the current head.',
      ),
    ],
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
              (release['state'] == 'collecting' ||
                  release['continuous']?['enabled'] == true))
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

  Component assemblyControls(Map release) {
    final grant = release['continuous'] as Map? ?? {};
    final mode =
        assemblyMode ?? (grant['auto_repair'] == true ? 'agent' : 'stop');
    final agent = assemblyAgent ?? '${grant['profile_id'] ?? ''}';
    final runs = assemblyRuns ?? '${grant['max_runs'] ?? 20}';
    final repairs = assemblyRepairs ?? '${grant['max_repairs'] ?? 2}';
    return section('Continuous private assembly', [
      notice(
        'Selected task heads are assembled and checked automatically. A checked assembly still needs explicit freeze; publication and deployment remain separate.',
      ),
      term(
        'Assembly runs used',
        '${grant['runs_used'] ?? 0} / ${grant['max_runs'] ?? 20}',
      ),
      term(
        'Automatic repairs used',
        '${grant['repairs_used'] ?? 0} / ${grant['max_repairs'] ?? 2}',
      ),
      select(
        'Assembly conflict handling',
        mode,
        const {
          'stop': 'Stop for operator inspection',
          'agent': 'Authorize bounded agent repair',
        },
        (value) => update(() => assemblyMode = value),
      ),
      if (mode == 'agent')
        select(
          'Assembly conflict agent',
          agent,
          {
            '': 'Choose a launched repository agent',
            for (final profile in component.profiles.where(
              (p) =>
                  p['read_only'] != true &&
                  p['archived'] != true &&
                  p['group_id'] == null,
            ))
              '${profile['id']}': '${profile['name']}',
          },
          (value) => update(() => assemblyAgent = value),
        ),
      field(
        'Assembly run limit (1–100)',
        runs,
        (value) => update(() => assemblyRuns = value),
      ),
      if (mode == 'agent')
        field(
          'Automatic repair limit (1–10)',
          repairs,
          (value) => update(() => assemblyRepairs = value),
        ),
      button(
        grant['enabled'] == true
            ? 'Update assembly authorization'
            : 'Enable continuous assembly',
        () => releaseAction('assembly', release, {
          'enabled': true,
          'auto_repair': mode == 'agent',
          'profile_id': agent,
          'max_runs': int.tryParse(runs),
          'max_repairs': int.tryParse(repairs),
        }),
        disabled:
            component.releaseBusy ||
            int.tryParse(runs) == null ||
            int.tryParse(repairs) == null ||
            (mode == 'agent' && agent.isEmpty),
      ),
      if (grant['enabled'] == true)
        button(
          'Stop assembly automation',
          () => releaseAction('assembly', release, {'enabled': false}),
          disabled: component.releaseBusy,
        ),
    ]);
  }

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
    final showChanges =
        !historical &&
        (release['state'] == 'collecting' ||
            release['continuous']?['enabled'] == true);
    final changed = !showChanges
        ? <Map<String, dynamic>>[]
        : members.where(candidateChanged).toList();
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
        if (!historical && component.releaseFailure?['id'] == release['id'])
          notice(
            'Last release action could not complete: ${component.releaseFailure?['message']}',
            error: true,
          ),
        if (changed.isNotEmpty)
          section('Selection needs review', [
            notice(
              '${changed.length} selected ${changed.length == 1 ? 'task has' : 'tasks have'} changed candidates. Review the selected and current commits in Selected changes before freezing.',
              error: true,
            ),
            for (final member in changed)
              button(
                'Review change: ${member['title'] ?? member['task_id']}',
                () => revealMember(release, member),
                small: true,
              ),
          ]),
        if ('${release['error'] ?? ''}'.isNotEmpty)
          notice(release['error'], error: true),
        if ('${release['assembly_notice'] ?? ''}'.isNotEmpty)
          notice(release['assembly_notice']),
        if (revision?['purpose'] == 'assembly')
          notice(
            'This is an immutable assembly snapshot. New task heads wait for another assembly; only an explicit freeze approves a checked checkpoint as a release candidate.',
          ),
        if (release['source']?['kind'] == 'discussion')
          notice(
            'Planned from group discussion ${release['source']['job_id']}. While collecting, automatic members follow their latest captured candidate after required checks pass. Freeze pins the selection.',
          ),
        section(
          revision == null
              ? 'Release plan'
              : revision['purpose'] == 'assembly'
              ? 'Assembly selection'
              : 'Frozen selection',
          [
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
          ],
        ),
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
                badge(
                  '${member['state'] ?? (member['selection'] == 'automatic' && member['candidate_sha'] == null ? 'Planned · awaiting checks' : 'Selected')}',
                ),
                if (showChanges && candidateChanged(member))
                  badge('Candidate changed', tone: 'warning'),
                if (!historical) memberWork(member),
                el('p', [
                  text(
                    '${showChanges && candidateChanged(member) ? 'Selected candidate' : 'Candidate'} ${member['candidate_sha'] ?? 'not captured'}',
                  ),
                ], classes: 'mono'),
                if (showChanges && candidateChanged(member)) ...[
                  el('p', [
                    text(
                      'Current candidate ${currentCandidate(member) ?? 'not captured'}',
                    ),
                  ], classes: 'mono'),
                  if (canAct &&
                      (release['state'] == 'collecting' ||
                          release['continuous']?['enabled'] == true))
                    ...selectionUpdate(release, member),
                ],
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
                if (!historical &&
                    (release['state'] == 'collecting' ||
                        release['continuous']?['enabled'] == true))
                  button(
                    'Open task and validation',
                    () => component.onOpenTask('${member['task_id']}'),
                    small: true,
                  ),
              ],
              classes: 'wf-release__member',
              attributes: {
                'data-release-member': '${member['task_id']}',
                'role': 'group',
                'aria-label':
                    'Selected change: ${member['title'] ?? member['task_id']}',
                'id': memberElementId(release, member),
                'tabindex': '-1',
                if (showChanges && candidateChanged(member))
                  'data-candidate-changed': 'true',
              },
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
        if (canAct &&
            !['ready', 'published', 'publishing'].contains(release['state']) &&
            (revision == null || revision['purpose'] == 'assembly'))
          assemblyControls(release),
        if (!historical) releaseReview(release, canAct),
        if (!historical &&
            (release['state'] == 'published' ||
                release['pull_request'] != null))
          releasePull(release, canAct),
        if (canAct)
          section('Actions', [
            if (release['state'] == 'collecting' &&
                release['continuous']?['enabled'] != true)
              button(
                'Freeze and integrate ${members.length} tasks',
                () => releaseAction('freeze', release),
                primary: true,
                disabled:
                    component.releaseBusy ||
                    members.isEmpty ||
                    changed.isNotEmpty ||
                    members.any((member) => member['candidate_sha'] == null),
              ),
            if (release['state'] == 'assembled')
              button(
                'Freeze verified assembly as release candidate',
                () => releaseAction('freeze', release, {
                  'head_sha': release['resulting_sha'],
                }),
                primary: true,
                disabled:
                    component.releaseBusy ||
                    members.isEmpty ||
                    members.any((member) => member['candidate_sha'] == null),
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
              'assembled',
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
