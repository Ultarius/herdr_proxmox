import '../ui.dart';
import 'package:herdr_dashboard_state/task_execution.dart';
import 'task_activity.dart' show taskAgentName, taskNextStep, shortSha;
import 'task_workflow.dart' show taskTone, taskStateTone, taskNeedsAttention;

/// Attention first (review and publication), then work in flight, queued and
/// draft work, and finished work last. Unknown states follow in data order.
const _groupOrder = [
  'Review ready',
  'Pull request open',
  'Published',
  'Need candidate',
  'Implementing',
  'Queued',
  'Draft',
  'Done',
  'Merged',
  'Closed',
];

/// Local "2026-10-13 14:00" for an ISO timestamp; other values verbatim.
String _when(String value) {
  final at = DateTime.tryParse(value)?.toLocal();
  if (at == null) return value;
  String two(int n) => n.toString().padLeft(2, '0');
  return '${at.year}-${two(at.month)}-${two(at.day)} ${two(at.hour)}:${two(at.minute)}';
}

/// Phase 2 List tab: one table with a tbody per task state. Each row keeps
/// the task's title (h2), next step, agent, state, repository, last activity
/// and the "Open task" action.
Component taskList({
  required List<Map<String, dynamic>> tasks,
  required String Function(Map<String, dynamic>) stateLabel,
  required void Function(String) onOpen,
  String executor = 'service',
  Map<String, Map<String, dynamic>> taskExecutions = const {},
}) {
  final groups = <String, List<Map<String, dynamic>>>{};
  for (final task in tasks) {
    groups.putIfAbsent(stateLabel(task), () => []).add(task);
  }
  final keys = [
    for (final key in _groupOrder)
      if (groups.containsKey(key)) key,
    for (final key in groups.keys)
      if (!_groupOrder.contains(key)) key,
  ];
  final open = tasks
      .where(
        (task) => !['merged', 'completed', 'closed'].contains(task['state']),
      )
      .length;
  Component row(Map<String, dynamic> task) {
    final id = '${task['id']}';
    final note = taskNextStep(task, executor: executor);
    final assigned = task['assigned_agent'] as Map?;
    final name = taskAgentName(task, fallback: 'Identity unavailable');
    final execution = taskExecutions[id];
    final work = taskWork(task, execution);
    return el(
      'tr',
      [
        el(
          'td',
          [
            if (work != null)
              el(
                'span',
                [text(work.label)],
                classes: 'wf-list__activity',
                attributes: {'data-work': work.activity},
              ),
            el('div', [
              el(
                'span',
                [text(shortSha(id))],
                classes: 'task-chip__id',
                attributes: {'title': id},
              ),
              el('h2', [text('${task['title']}')], classes: 'wf-list__name'),
            ], classes: 'wf-list__task'),
            el(
              'span',
              [text(note)],
              classes: 'wf-list__meta',
              attributes: {
                if (taskNeedsAttention(task)) 'data-flag': 'attention',
              },
            ),
          ],
          attributes: {'data-col': 'task'},
        ),
        el(
          'td',
          [
            el('span', [
              roleTile(
                name,
                role: assigned?['role'] ?? name,
                size: 'xs',
                live: work?.activity == 'working',
              ),
              el('span', [text(name)], classes: 'agent-label__name'),
            ], classes: 'agent-label'),
          ],
          attributes: {'data-col': 'agent'},
        ),
        el(
          'td',
          [badge(stateLabel(task), tone: taskTone(task))],
          attributes: {'data-col': 'status'},
        ),
        el(
          'td',
          [
            el('span', [text('${task['repository']}')], classes: 'mono'),
          ],
          attributes: {'data-col': 'repository'},
        ),
        el(
          'td',
          [
            if (task['updated_at'] != null)
              el(
                'time',
                [text(_when('${task['updated_at']}'))],
                attributes: {
                  'datetime': '${task['updated_at']}',
                  'title': 'Last activity ${task['updated_at']}',
                },
              )
            else
              el('span', [text('No activity recorded')], classes: 'none'),
          ],
          attributes: {'data-col': 'updated'},
        ),
        el(
          'td',
          [
            button(
              'Open task',
              () => onOpen(id),
              variant: 'ghost',
              small: true,
              iconName: 'arrow-right',
            ),
          ],
          attributes: {'data-col': 'action'},
        ),
      ],
      attributes: {'data-row': id},
    );
  }

  return el('section', [
    el('header', [
      el('p', [
        icon('list-checks'),
        text('Tasks by state'),
      ], classes: 'wf-list__title'),
      el('p', [
        text(
          '${tasks.length} ${tasks.length == 1 ? 'task' : 'tasks'} · $open open',
        ),
      ], classes: 'wf-list__summary'),
    ], classes: 'wf-list__header'),
    if (tasks.isEmpty)
      emptyState('No tasks to list yet.', iconName: 'list-checks')
    else
      el('div', [
        el('table', [
          el('caption', [
            text(
              'Tasks grouped by state, with agent, state, repository and last activity',
            ),
          ], classes: 'visually-hidden'),
          el('thead', [
            el('tr', [
              for (final (label, column) in [
                ('Task', 'task'),
                ('Agent', 'agent'),
                ('State', 'status'),
                ('Repository', 'repository'),
                ('Last activity', 'updated'),
                ('Action', 'action'),
              ])
                el(
                  'th',
                  [
                    column == 'action'
                        ? el('span', [text(label)], classes: 'visually-hidden')
                        : text(label),
                  ],
                  attributes: {'scope': 'col', 'data-col': column},
                ),
            ]),
          ]),
          for (final key in keys)
            el('tbody', [
              el('tr', [
                el(
                  'th',
                  [
                    el('span', [
                      el(
                        'span',
                        [],
                        classes: 'dot',
                        attributes: {
                          'data-tone': taskStateTone(groups[key]!.first),
                          'aria-hidden': 'true',
                        },
                      ),
                      text(key),
                      el('span', [
                        text(groups[key]!.length),
                      ], classes: 'count count--sm'),
                    ], classes: 'wf-list__group-label'),
                    // Drafts are proposals waiting on an operator, not work in
                    // flight; grouping them keeps the active work readable.
                    if (key == 'Draft')
                      el('span', [
                        text(
                          'Work that has not started. Open a task for its next step.',
                        ),
                      ], classes: 'wf-list__group-note'),
                  ],
                  attributes: {'colspan': '6', 'scope': 'rowgroup'},
                ),
              ], classes: 'wf-list__group'),
              for (final task in groups[key]!) row(task),
            ]),
        ], classes: 'table wf-list__table'),
      ], classes: 'table-wrap'),
  ], classes: 'card wf-list');
}
