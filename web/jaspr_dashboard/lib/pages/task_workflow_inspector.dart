part of 'task_workflow.dart';

/// Template inspector: definition lists, overline sections, relationships.
extension _Inspector on _TaskWorkflowState {
  Component term(String label, Object? value, {bool mono = false}) =>
      el('div', [
        el('dt', [text(label)]),
        el('dd', [
          mono ? el('span', [text(value)], classes: 'mono') : text(value),
        ]),
      ]);

  Component section(String title, List<Component> children) => el(
    'section',
    [
      el('h3', [text(title)], classes: 'overline inspector__section-title'),
      ...children,
    ],
    classes: 'inspector__section',
    attributes: {'data-section': title},
  );

  /// Template inspector: the workflow overview, or one task's recorded facts.
  Component inspector() {
    if (creatingRelease) return releaseCreateInspector();
    final release = component.releases
        .where((item) => 'release:${item['id']}' == selected)
        .firstOrNull;
    if (release != null) return releaseInspector(release);
    final task = component.tasks
        .where((task) => _id(task) == selected)
        .firstOrNull;
    if (task == null)
      return el('aside', [
        el('header', [
          el('div', [
            el('p', [
              icon('workflow', classes: 'icon--sm'),
              text('Overview'),
            ], classes: 'inspector__eyebrow'),
            el('h2', [text('Workflow overview')], classes: 'inspector__title'),
          ], classes: 'inspector__heading'),
        ], classes: 'inspector__header'),
        el('div', [
          el('section', [
            el('p', [
              text(
                'Select a task to inspect its recorded assignment, execution, relationships and exact-commit validation.',
              ),
            ], classes: 'wf-insp__muted'),
            el('dl', [
              term('Recorded tasks', '${component.tasks.length}'),
              term('Open', '${component.tasks.where(_open).length}'),
              term(
                'Queued assignments',
                '${component.tasks.where((task) => (task['assignment'] as Map?)?['state'] == 'queued').length}',
              ),
              term('Visible relationships', '${links.length}'),
            ], classes: 'dl'),
            el('p', [
              icon('info'),
              text(
                'Follow-up links show provenance. Execution-wait links show recorded occupancy; they do not define a code dependency.',
              ),
            ], classes: 'wf-row__note'),
          ], classes: 'inspector__section'),
        ], classes: 'inspector__body'),
      ], classes: 'inspector task-wf-inspector');
    final id = _id(task),
        execution = task['execution'] as Map?,
        assignment = task['assignment'] as Map?,
        target = task['merge_sha'] ?? task['head_sha'],
        build = _evidence(task),
        pull = task['pull'];
    final reviewer = (task['acceptance'] as Map?)?['reviewer_profile_id'];
    final reviewerName =
        component.profiles
            .where((profile) => profile['id'] == reviewer)
            .firstOrNull?['name'] ??
        reviewer;
    final related = links
        .where(
          (link) =>
              link.kind != 'release-membership' &&
              (link.from == selected || link.to == selected),
        )
        .toList();
    final owner = _agent(task), activity = _activity(owner);
    String relation(_Link link) => link.kind == 'follow-up'
        ? (link.to == selected ? 'Follow-up of' : 'Proposed follow-up')
        : (link.to == selected
              ? 'Waiting on execution of'
              : 'Execution blocking');
    final groups = <String, List<_Link>>{};
    for (final link in related) {
      groups.putIfAbsent(relation(link), () => []).add(link);
    }
    return el(
      'aside',
      [
        el('header', [
          el('div', [
            el('p', [
              icon('list-checks', classes: 'icon--sm'),
              el('span', [text(id)], classes: 'mono'),
            ], classes: 'inspector__eyebrow'),
            el('h2', [
              text(task['title'] ?? 'Untitled task'),
            ], classes: 'inspector__title'),
            el('div', [
              badge(_state(task), tone: _tone(task)),
              if (pull is Map)
                el('span', [
                  icon('git-pull-request'),
                  text('#${pull['number']}'),
                ], classes: 'type-tag'),
            ], classes: 'inspector__meta'),
          ], classes: 'inspector__heading'),
          button(
            'Clear selection',
            () => chooseTask(null),
            variant: 'ghost',
            small: true,
            iconName: 'x',
          ),
        ], classes: 'inspector__header'),
        el('div', [
          el('section', [
            el(
              'p',
              [
                icon(_attention(task) ? 'triangle-alert' : 'arrow-right'),
                text(taskNextStep(task, executor: component.executor)),
              ],
              classes: 'wf-row__note',
              attributes: {if (_attention(task)) 'data-tone': 'danger'},
            ),
          ], classes: 'inspector__section'),
          section('Assignment & execution', [
            el('div', [
              roleTile(
                _name(task),
                role: _role(task),
                live: activity == 'working',
              ),
              el('div', [
                el('p', [text(_name(task))], classes: 'wf-assignee__name'),
                if (activity != null)
                  el('p', [
                    _dot(activity),
                    text(_activityLabel(activity)),
                  ], classes: 'wf-assignee__status'),
              ]),
            ], classes: 'wf-assignee'),
            el('dl', [
              term('Assigned agent', _name(task)),
              term(
                'Profile',
                owner.isEmpty ? 'Not recorded' : owner,
                mono: owner.isNotEmpty,
              ),
              term('Repository', task['repository'] ?? 'Not recorded'),
              if (assignment != null)
                term(
                  'Assignment',
                  '${assignment['state']} · order ${assignment['position'] ?? 'not recorded'}',
                ),
              if (execution != null)
                term(
                  'Execution',
                  execution['mode'] == 'template'
                      ? 'Parallel instance of ${execution['template_name'] ?? execution['template_id']}'
                      : execution['mode'] ?? 'Recorded',
                ),
              if (task['run_id'] != null)
                term('Run', task['run_id'], mono: true),
              if (reviewer != null) term('Acceptance reviewer', reviewerName),
              if (task['updated_at'] != null)
                term('Updated', task['updated_at']),
            ], classes: 'dl'),
          ]),
          releaseMembership(task),
          section('Recorded relationships', [
            if (related.isEmpty)
              el('p', [
                text('No relationships to other visible tasks are recorded.'),
              ], classes: 'wf-insp__muted'),
            for (final entry in groups.entries)
              el('div', [
                el('p', [text(entry.key)], classes: 'wf-links__title'),
                for (final link in entry.value)
                  () {
                    final otherId = link.to == selected ? link.from : link.to;
                    final other = component.tasks.firstWhere(
                      (candidate) => _id(candidate) == otherId,
                    );
                    return el('div', [
                      el('span', [
                        text(shortSha(otherId)),
                      ], classes: 'task-chip__id'),
                      el(
                        'button',
                        [text('${other['title']}')],
                        classes: 'task-wf-relationship__link',
                        attributes: {'type': 'button'},
                        events: {
                          'click': (_) => chooseTask(otherId, reveal: true),
                        },
                      ),
                      badge(_state(other), tone: _tone(other)),
                    ], classes: 'inspector__row task-wf-relationship');
                  }(),
              ], classes: 'wf-links'),
            if ((task['source'] as Map?)?['task_id'] != null &&
                !component.tasks.any(
                  (candidate) =>
                      _id(candidate) == '${task['source']['task_id']}',
                ))
              button(
                'Open originating task outside this snapshot',
                () => component.onOpenTask('${task['source']['task_id']}'),
                small: true,
                iconName: 'external-link',
              ),
          ]),
          section('Validation & publication', [
            el('dl', [
              term('Candidate', target ?? 'Not captured', mono: target != null),
              term(
                'Validation',
                _validation(task) == 'verified'
                    ? 'Required checks verified for this exact commit'
                    : build != null
                    ? '${build['state']} · required checks not verified'
                    : 'No matching current-commit build evidence',
              ),
              term(
                'Branch',
                task['branch'] ?? 'Not recorded',
                mono: task['branch'] != null,
              ),
              if (pull is Map)
                term(
                  'Pull request',
                  '#${pull['number']} · ${pull['draft'] == true ? 'Draft' : pull['state']}',
                ),
            ], classes: 'dl'),
            el('p', [
              text(
                'Validation, publication, merge and deployment are separate recorded operations.',
              ),
            ], classes: 'wf-insp__muted'),
          ]),
        ], classes: 'inspector__body'),
        el('footer', [
          button(
            'Open task',
            () => component.onOpenTask(id),
            primary: true,
            small: true,
            iconName: 'arrow-right',
          ),
          if (owner.isNotEmpty)
            button(
              'Open assigned agent',
              () =>
                  navigation.navigate('/agents/${Uri.encodeComponent(owner)}'),
              small: true,
              iconName: 'bot',
            ),
          button(
            'Builds & deployments',
            () => navigation.navigate('/builds'),
            small: true,
            iconName: 'rocket',
          ),
        ], classes: 'inspector__footer actions'),
      ],
      classes: 'inspector task-wf-inspector',
      attributes: {'aria-label': 'Selected task details'},
    );
  }
}
