part of 'task_workflow.dart';

/// The canvas: lanes, edges and task cards inside the zoomable stage.
extension _Canvas on _TaskWorkflowState {
  /// Compact card: mono id + PR / two-line title / tile (lanes off), status
  /// pill and one flag icon. The aria-label carries every fact.
  Component node(Map<String, dynamic> task, _Box box, Set<String> highlighted) {
    final id = _id(task),
        assignment = task['assignment'] as Map?,
        execution = task['execution'] as Map?,
        blocking = task['blocking_execution'] as Map?,
        pull = task['pull'];
    final message = assignment?['state'] == 'queued'
        ? 'Queue order ${assignment?['position'] ?? 'not recorded'}'
        : execution?['mode'] == 'template'
        ? 'Parallel instance of ${execution?['template_name'] ?? 'agent template'}'
        : taskNextStep(task, executor: component.executor);
    final flag = _attention(task)
        ? ('attention', 'triangle-alert')
        : assignment?['state'] == 'queued'
        ? ('queued', 'clock')
        : blocking != null && _open(task)
        ? ('waiting', 'hourglass')
        : null;
    final owner = _agent(task);
    return el(
      'button',
      [
        el('span', [
          el('span', [text(shortSha(id))], classes: 'wf-node__id'),
          if (pull is Map)
            el('span', [
              icon(
                pull['draft'] == true
                    ? 'git-pull-request-draft'
                    : 'git-pull-request',
              ),
              text('#${pull['number']}'),
            ], classes: 'wf-node__pr task-wf-pr'),
          if (execution?['mode'] == 'template')
            el(
              'span',
              [icon('layers')],
              classes: 'wf-node__type',
              attributes: {'title': 'Parallel instance'},
            ),
        ], classes: 'wf-node__head task-wf-node-head'),
        el('span', [
          text(task['title'] ?? 'Untitled task'),
        ], classes: 'wf-node__title task-wf-node-title'),
        el('span', [
          if (!lanes)
            roleTile(
              _name(task),
              role: _role(task),
              size: 'xs',
              live: _activity(owner) == 'working',
            ),
          badge(_state(task), tone: _tone(task)),
          if (flag != null)
            el(
              'span',
              [icon(flag.$2)],
              classes: 'wf-node__flag',
              attributes: {'data-flag': flag.$1, 'title': message},
            ),
        ], classes: 'wf-node__foot task-wf-node-foot'),
      ],
      classes: 'task-wf-node wf-node',
      attributes: {
        'type': 'button',
        'id': '$instance-node-$id',
        'data-node': id,
        'data-state': '${task['state']}',
        'data-tone': _tone(task),
        if (links.any((link) => link.to == id)) 'data-in': '',
        if (links.any((link) => link.from == id)) 'data-out': '',
        'aria-pressed': '${selected == id}',
        'aria-label':
            '${task['title']}. ${_state(task)}. ${_name(task)}. $message',
        'title':
            '$id · ${task['repository'] ?? 'Repository not recorded'}\n$message',
        if (highlighted.isNotEmpty && !highlighted.contains(id)) 'data-dim': '',
        if (highlighted.contains(id)) 'data-emphasis': '',
        'style': 'left:${box.x}px;top:${box.y}px;',
      },
      events: {
        'click': (_) => chooseTask(selected == id ? null : id),
        'dblclick': (_) => component.onOpenTask(id),
        'pointerenter': (_) => update(() => hovered = id),
        'pointerleave': (_) => update(() => hovered = null),
        'keydown': (event) => keyFor(id, event as web.KeyboardEvent),
      },
    );
  }

  /// Decorative lane band; the header is the agent tile (live ring and status
  /// dot) plus the name. The cards and the agent bar carry the facts.
  Component lane(_Lane lane, int index, List<Map<String, dynamic>> tasks) {
    final first = tasks.firstWhere((task) => _agent(task) == lane.agent);
    final activity = _activity(lane.agent);
    return el(
      'div',
      [
        el(
          'div',
          [
            el('span', [
              roleTile(
                _name(first),
                role: _role(first),
                size: 'xs',
                live: activity == 'working',
              ),
              if (activity != null) _dot(activity),
            ], classes: 'wf-lane__tile'),
            el('span', [text(_name(first))], classes: 'wf-lane__name'),
          ],
          classes: 'task-wf-lane-head wf-lane__head',
          attributes: {
            'title': [
              _name(first),
              ?_activityLabel(activity),
              '${tasks.where((task) => _agent(task) == lane.agent).length} recorded tasks',
            ].join(' · '),
          },
        ),
      ],
      classes: 'task-wf-lane',
      attributes: {
        'aria-hidden': 'true',
        if (index.isOdd) 'data-band': '',
        if (agent.isNotEmpty && agent == lane.agent) 'data-active': '',
        if (agent.isNotEmpty && agent != lane.agent) 'data-dim': '',
        'style': 'top:${lane.y}px;height:${lane.height}px;',
      },
    );
  }

  Component edge(_Link link, _Route route, Set<String> highlighted) {
    final active =
        highlighted.contains(link.from) && highlighted.contains(link.to);
    final source = component.tasks
        .where((task) => _id(task) == link.from)
        .firstOrNull;
    final fromFinished = ['merged', 'completed'].contains(source?['state']);
    return el(
      'g',
      [
        el('title', [
          text(
            '${link.from} → ${link.to}: ${link.kind == 'release-membership'
                ? '${link.state} ${link.reason}'
                : link.kind == 'follow-up'
                ? 'recorded follow-up'
                : 'waiting on execution'}',
          ),
        ]),
        el(
          'path',
          [],
          classes: 'task-wf-edge-line',
          attributes: {'d': route.line},
        ),
        el(
          'path',
          [],
          classes: 'task-wf-edge-head',
          attributes: {'d': route.head},
        ),
      ],
      classes: 'task-wf-edge',
      attributes: {
        'data-from': link.from,
        'data-to': link.to,
        'data-kind': link.kind,
        if (link.kind == 'release-membership') 'data-edge': link.state,
        if (link.kind == 'follow-up' && fromFinished) 'data-edge': 'finished',
        if (active) 'data-emphasis': '',
        if (highlighted.isNotEmpty && !active) 'data-dim': '',
      },
    );
  }

  Component canvas(List<Map<String, dynamic>> tasks, Set<String> highlighted) {
    final routes = routeLinks();
    // Highlighted connectors paint last, over the others.
    final ordered = links.toList()
      ..sort(
        (a, b) =>
            (highlighted.contains(a.from) && highlighted.contains(a.to) ? 1 : 0)
                .compareTo(
                  highlighted.contains(b.from) && highlighted.contains(b.to)
                      ? 1
                      : 0,
                ),
      );
    final contentHeight = graphHeight * zoom;
    return el(
      'div',
      [
        if (boxes.isEmpty)
          el('div', [
            el('span', [icon('workflow')], classes: 'empty__icon'),
            el('p', [
              text(
                component.loading
                    ? 'Loading task workflows…'
                    : component.tasks.isEmpty
                    ? 'No development tasks yet.'
                    : 'No tasks match these workflow filters.',
              ),
            ], classes: 'empty__title'),
          ], classes: 'task-wf-empty')
        else
          el(
            'div',
            [
              el(
                'div',
                [
                  for (var i = 0; i < bands.length; i++)
                    lane(bands[i], i, tasks),
                  el(
                    'svg',
                    [
                      for (final link in ordered)
                        edge(link, routes[link]!, highlighted),
                    ],
                    classes: 'task-wf-edges',
                    attributes: {
                      'width': '$graphWidth',
                      'height': '$graphHeight',
                      'aria-hidden': 'true',
                      'focusable': 'false',
                    },
                  ),
                  // DOM order = tab order: column first, then top to bottom.
                  for (final box
                      in boxes.toList()..sort(
                        (a, b) => a.column == b.column
                            ? a.y.compareTo(b.y)
                            : a.column.compareTo(b.column),
                      ))
                    if (box.kind == 'release')
                      releaseNode(
                        component.releases.firstWhere(
                          (release) => 'release:${release['id']}' == box.id,
                        ),
                        box,
                        highlighted,
                      )
                    else
                      node(
                        tasks.firstWhere((task) => _id(task) == box.id),
                        box,
                        highlighted,
                      ),
                ],
                classes: 'task-wf-stage',
                attributes: {
                  'style':
                      'width:${graphWidth}px;height:${graphHeight}px;transform:scale($zoom);',
                  'role': 'group',
                  'aria-label': 'Recorded task workflow graph',
                  if (lanes) 'data-lanes': '',
                },
              ),
            ],
            classes: 'task-wf-sizer',
            attributes: {
              'style':
                  'width:${graphWidth * zoom}px;height:${graphHeight * zoom}px;',
            },
          ),
      ],
      classes: 'task-wf-viewport',
      attributes: {
        'id': '$instance-viewport',
        'tabindex': '0',
        'aria-label': 'Workflow graph. Drag the empty area to pan.',
        'style':
            '--wf-content-h:${contentHeight}px;--wf-zoom:$zoom;--wf-scroll-x:${scrollX}px;',
        if (scrollX <= 0) 'data-scroll-start': '',
        if (drag != null) 'data-panning': '',
        if (boxes.isEmpty) 'data-empty': '',
      },
      events: {
        'scroll': (event) {
          final area = event.currentTarget as web.HTMLElement?;
          if (area == null) return;
          scrollX = area.scrollLeft;
          area.style.setProperty('--wf-scroll-x', '${scrollX}px');
          if (scrollX <= 0) {
            area.setAttribute('data-scroll-start', '');
          } else {
            area.removeAttribute('data-scroll-start');
          }
        },
        'pointerdown': (event) {
          final pointer = event as web.PointerEvent;
          final target = pointer.target as web.Element?;
          if (pointer.button != 0 ||
              target?.closest('button,a') != null ||
              viewport == null)
            return;
          drag = (
            x: pointer.clientX.toDouble(),
            y: pointer.clientY.toDouble(),
            left: viewport!.scrollLeft,
            top: viewport!.scrollTop,
            id: pointer.pointerId,
          );
          viewport!.setAttribute('data-panning', '');
          try {
            viewport!.setPointerCapture(pointer.pointerId);
          } catch (_) {
            /* Synthetic or detached pointer. */
          }
        },
        'pointermove': (event) {
          final pointer = event as web.PointerEvent, pan = drag;
          if (pan == null) return;
          viewport?.scrollLeft = pan.left - (pointer.clientX - pan.x);
          viewport?.scrollTop = pan.top - (pointer.clientY - pan.y);
        },
        'pointerup': (_) {
          drag = null;
          viewport?.removeAttribute('data-panning');
        },
        'pointercancel': (_) {
          drag = null;
          viewport?.removeAttribute('data-panning');
        },
        'keydown': (event) {
          if ((event as web.KeyboardEvent).key == 'Escape') {
            update(() {
              selected = null;
              agent = '';
            });
          }
        },
      },
    );
  }
}
