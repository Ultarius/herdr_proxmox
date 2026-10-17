part of 'task_workflow.dart';

/// Status dot whose shape repeats the activity (template .dot variants).
Component _dot(String activity) => el(
  'span',
  [],
  classes: [
    'dot dot--md',
    switch (activity) {
      'working' => '',
      'attention' => 'dot--diamond',
      'offline' => 'dot--dash',
      _ => 'dot--hollow',
    },
  ].join(' ').trim(),
  attributes: {
    'data-tone': switch (activity) {
      'working' => 'success',
      'attention' => 'danger',
      _ => 'neutral',
    },
    'aria-hidden': 'true',
  },
);

String? _activityLabel(String? activity) => switch (activity) {
  null => null,
  'working' => 'Working',
  'idle' => 'Idle',
  'attention' => 'Needs attention',
  _ => 'Offline',
};

extension _Panels on _TaskWorkflowState {
  /// One wrapping row over the board: counts by state (stacked bar + legend),
  /// recorded links, attention chips and the details toggle.
  Component summary() {
    final all = component.tasks;
    final counts = <String, int>{};
    for (final task in all) {
      counts.update(_stateKey(task), (n) => n + 1, ifAbsent: () => 1);
    }
    final keys = [
      for (final key in _progressOrder)
        if (counts.containsKey(key)) key,
      for (final key in counts.keys)
        if (!_progressOrder.contains(key)) key,
    ];
    final segments = [
      for (final key in keys) (_stateLabel(key), counts[key]!, _stateTone(key)),
    ];
    final open = all.where(_open).length;
    final attention = all.where(_attention).toList();
    return el('section', [
      el('h2', [text('Task summary')], classes: 'visually-hidden'),
      el('div', [
        el(
          'span',
          [icon('list-checks')],
          classes: 'icon-tile icon-tile--sm',
          attributes: {'data-tone': 'accent'},
        ),
        el('p', [
          el('strong', [text(all.length)]),
          text(all.length == 1 ? ' task' : ' tasks'),
          el('span', [text('·')], attributes: {'aria-hidden': 'true'}),
          el('strong', [text(open)]),
          text(' open'),
        ], classes: 'wf-summary__figure'),
      ], classes: 'wf-summary__build'),
      if (segments.isNotEmpty)
        el('div', [
          stackbar(segments, legend: false),
        ], classes: 'wf-summary__progress'),
      if (segments.isNotEmpty)
        el(
          'ul',
          [
            for (final segment in segments)
              el('li', [
                el(
                  'span',
                  [],
                  classes: 'dot',
                  attributes: {'data-tone': segment.$3, 'aria-hidden': 'true'},
                ),
                text(segment.$1),
                el('span', [text(segment.$2)], classes: 'legend__count'),
              ], classes: 'legend__item'),
          ],
          classes: 'legend',
          attributes: {'role': 'list', 'aria-label': 'Tasks by state'},
        ),
      el('span', [
        icon('git-branch'),
        text(
          '${links.length} recorded ${links.length == 1 ? 'link' : 'links'}',
        ),
      ], classes: 'wf-summary__fact'),
      el('div', [
        for (final task in attention.take(3))
          el(
            'button',
            [
              el(
                'span',
                [],
                classes: 'dot dot--diamond',
                attributes: {'data-tone': 'danger', 'aria-hidden': 'true'},
              ),
              text('Needs attention:'),
              el('span', [text(shortSha(_id(task)))], classes: 'mono'),
            ],
            classes: 'wf-blocking',
            attributes: {
              'type': 'button',
              'title':
                  '${task['title']}: ${taskNextStep(task, executor: component.executor)}',
            },
            events: {'click': (_) => chooseTask(_id(task), reveal: true)},
          ),
        el(
          'button',
          [icon('panel-right'), text(panel ? 'Hide details' : 'Show details')],
          classes: 'btn btn--secondary btn--sm',
          attributes: {
            'type': 'button',
            'aria-expanded': '$panel',
            'aria-controls': '$instance-inspector',
          },
          events: {'click': (_) => update(() => panel = !panel)},
        ),
      ], classes: 'wf-summary__actions'),
    ], classes: 'wf-summary');
  }

  /// Compact agent chips: role tile (live ring while working), name, then a
  /// status dot, the activity and the number of recorded tasks.
  Component agentBar(List<Map<String, dynamic>> tasks) {
    final owners = tasks.map(_agent).toSet().toList();
    final known = component.activity != null;
    final working = owners.where((o) => _activity(o) == 'working').length;
    Component chip(
      Component lead,
      String name,
      List<Component> meta, {
      required bool pressed,
      required void Function() onClick,
      Map<String, String> attributes = const {},
    }) => el(
      'button',
      [
        lead,
        el('span', [
          el('span', [text(name)], classes: 'agent-chip__name'),
          el('span', meta, classes: 'agent-chip__meta'),
        ], classes: 'agent-chip__text'),
      ],
      classes: 'agent-chip task-wf-agent',
      attributes: {'type': 'button', 'aria-pressed': '$pressed', ...attributes},
      events: {'click': (_) => onClick()},
    );
    return el('div', [
      el(
        'div',
        [
          chip(
            el('span', [icon('users')], classes: 'icon-tile icon-tile--sm'),
            'All agents',
            [
              text(
                known
                    ? '$working of ${owners.length} working'
                    : '${owners.length} ${owners.length == 1 ? 'agent' : 'agents'}',
              ),
            ],
            pressed: agent.isEmpty,
            onClick: () => update(() => agent = ''),
          ),
          for (final owner in owners)
            () {
              final first = tasks.firstWhere((task) => _agent(task) == owner);
              final count = tasks.where((task) => _agent(task) == owner).length;
              final activity = _activity(owner);
              return chip(
                roleTile(
                  _name(first),
                  role: _role(first),
                  size: 'xs',
                  live: activity == 'working',
                ),
                _name(first),
                [
                  if (activity != null) ...[
                    _dot(activity),
                    el('span', [text(_activityLabel(activity))]),
                    el(
                      'span',
                      [text('·')],
                      attributes: {'aria-hidden': 'true'},
                    ),
                  ],
                  el('span', [
                    text('$count ${count == 1 ? 'task' : 'tasks'}'),
                  ], classes: 'agent-chip__task'),
                ],
                pressed: agent == owner,
                attributes: {'data-agent': owner},
                onClick: () {
                  update(() {
                    agent = agent == owner ? '' : owner;
                    selected = null;
                  });
                  final box = boxes
                      .where((box) => box.agent == owner)
                      .firstOrNull;
                  if (box != null) scheduleMicrotask(() => _reveal(box.id));
                },
              );
            }(),
        ],
        classes: 'agent-bar task-wf-agents',
        attributes: {
          'role': 'group',
          'aria-label': 'Highlight tasks by assigned agent',
        },
      ),
    ], classes: 'wf-agents');
  }

  /// Board footer: a status key (no counts), the link samples and the hint.
  Component legend(List<Map<String, dynamic>> tasks) {
    final keys = [
      for (final key in _progressOrder)
        if (tasks.any((task) => _stateKey(task) == key)) key,
    ];
    Component sample(String kind, String label) => el('li', [
      el(
        'svg',
        [
          el(
            'g',
            [
              el(
                'path',
                [],
                classes: 'wf-edge__line',
                attributes: {'d': 'M1 5H18'},
              ),
              el(
                'path',
                [],
                classes: 'wf-edge__head',
                attributes: {'d': 'M24 5L17 1V9Z'},
              ),
            ],
            classes: 'wf-edge',
            attributes: {'data-sample': kind},
          ),
        ],
        classes: 'wf-edge-sample',
        attributes: {
          'viewBox': '0 0 24 10',
          'aria-hidden': 'true',
          'focusable': 'false',
        },
      ),
      el('span', [text(label)], classes: 'legend__label'),
    ], classes: 'legend__item');
    return el('footer', [
      if (keys.isNotEmpty)
        el('div', [
          el('p', [text('Status')], classes: 'overline'),
          el(
            'ul',
            [
              for (final key in keys)
                el('li', [
                  el(
                    'span',
                    [],
                    classes: 'dot',
                    attributes: {
                      'data-tone': _stateTone(key),
                      'aria-hidden': 'true',
                    },
                  ),
                  text(_stateLabel(key)),
                ], classes: 'legend__item'),
            ],
            classes: 'legend',
            attributes: {'role': 'list'},
          ),
        ], classes: 'wf-legend__group'),
      el('div', [
        el('p', [text('Links')], classes: 'overline'),
        el(
          'ul',
          [
            sample('follow-up', 'Recorded follow-up'),
            sample('finished', 'Follow-up of done or merged work'),
            sample('execution-wait', 'Waiting on another task execution'),
            if (component.releases.isNotEmpty) ...[
              sample(
                'release-pending',
                'Selected for release; awaiting incorporation',
              ),
              sample(
                'release-incorporated',
                'Incorporated into release; separate from main',
              ),
            ],
          ],
          classes: 'legend',
          attributes: {'role': 'list'},
        ),
      ], classes: 'wf-legend__group'),
      el('p', [
        text('Only recorded relationships in this task snapshot are shown.'),
        el('span', [
          text(' Drag to pan · arrow keys follow links · Esc clears'),
        ], classes: 'wf-legend__keys'),
      ], classes: 'wf-legend__hint'),
    ], classes: 'task-wf-legend wf-legend');
  }

  /// Template switch: <label class="switch"><input role="switch"><span>.
  Component switchControl(
    String label,
    bool value,
    void Function(bool) change,
  ) => el('label', [
    dom.input<bool>(
      type: dom.InputType.checkbox,
      checked: value,
      onChange: change,
      classes: 'switch__input',
      attributes: {'role': 'switch'},
    ),
    el('span', [text(label)], classes: 'switch__label'),
  ], classes: 'switch task-wf-switch');

  Component zoomGroup() => el('div', [
    el('span', [text('Zoom')], classes: 'toolbar__label'),
    el(
      'div',
      [
        _groupButton(
          'Zoom out',
          'zoom-out',
          () => setZoom(zoom - .15),
          zoom <= .3,
        ),
        el(
          'output',
          [text('${(zoom * 100).round()}%')],
          classes: 'btn-group__readout',
          attributes: {'aria-label': 'Workflow zoom', 'aria-live': 'polite'},
        ),
        _groupButton(
          'Zoom in',
          'zoom-in',
          () => setZoom(zoom + .15),
          zoom >= 1.75,
        ),
        _groupButton('Fit', 'scan', fit, false),
      ],
      classes: 'btn-group task-wf-zoom',
      attributes: {'role': 'group', 'aria-label': 'Workflow zoom'},
    ),
  ], classes: 'toolbar__group');

  Component _groupButton(
    String label,
    String iconName,
    void Function() onClick,
    bool disabled,
  ) => dom.button(
    [icon(iconName), text(label)],
    type: dom.ButtonType.button,
    disabled: disabled,
    classes: 'btn-group__btn',
    onClick: onClick,
  );
}
