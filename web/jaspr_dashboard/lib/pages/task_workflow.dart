import 'dart:async';
import 'dart:math' as math;
import 'dart:js_interop';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import '../ui.dart';
import '../navigation.dart' as navigation;
import 'task_activity.dart' show taskAgentName, taskNextStep, shortSha;
import 'task_state.dart';

part 'task_workflow_layout.dart';
part 'task_workflow_panels.dart';
part 'task_workflow_inspector.dart';
part 'task_workflow_canvas.dart';
part 'task_workflow_releases.dart';

/// Summary order: finished work first, then what is closest to finishing.
const _progressOrder = [
  'completed',
  'merged',
  'pr_open',
  'published',
  'review_ready',
  'awaiting_candidate',
  'implementing',
  'queued',
  'draft',
  'closed',
];

/// Task state as a Phase 2 status tone: done/merged success, published and
/// PR open accent, review ready warning, implementing info, the rest neutral.
String _stateTone(String state) => switch (state) {
  'completed' || 'merged' => 'success',
  'pr_open' || 'published' => 'accent',
  'review_ready' => 'warning',
  'awaiting_candidate' => 'warning',
  'implementing' => 'info',
  _ => 'neutral',
};

/// A task needs attention when automation, its queued assignment or the
/// agent's session reports a decision; it is shown in the danger tone.
bool taskNeedsAttention(Map task) =>
    task['automation_error'] != null ||
    task['assignment_error'] != null ||
    (task['availability'] as Map?)?['state'] == 'decision';

/// Tone of a task's recorded state alone (group dots, legends).
String taskStateTone(Map task) => _stateTone(taskStateKey(task));

/// Pill tone for a task: attention first, then its recorded state.
String taskTone(Map task) =>
    taskNeedsAttention(task) ? 'danger' : taskStateTone(task);

/// Phase 2's native workflow board, driven exclusively by recorded task data.
/// Relationships are provenance or execution waits, never inferred prerequisites.
class TaskWorkflow extends StatefulComponent {
  const TaskWorkflow({
    super.key,
    required this.tasks,
    required this.onOpenTask,
    this.listView,
    this.profiles = const [],
    this.groups = const [],
    this.activity,
    this.executor = 'unavailable',
    this.loading = false,
    this.initialView = 'workflow',
    this.releases = const [],
    this.onReleaseAction,
    this.releaseBusy = false,
    this.onLoadReleaseHistory,
  });
  final List<Map<String, dynamic>> tasks;
  final List<Map<String, dynamic>> profiles;
  final List<Map<String, dynamic>> groups;
  final List<Map<String, dynamic>> releases;
  final void Function(String, Map<String, dynamic>)? onReleaseAction;
  final bool releaseBusy;
  final Future<Map<String, dynamic>> Function(String, String?, int?)?
  onLoadReleaseHistory;

  /// Live agent activity by profile id (working, idle, attention, offline),
  /// from the dashboard snapshot. Null when the host has no live state.
  final Map<String, String>? activity;
  final void Function(String) onOpenTask;
  final Component? listView;
  final String executor, initialView;
  final bool loading;
  @override
  State<TaskWorkflow> createState() => _TaskWorkflowState();
}

class _TaskWorkflowState extends State<TaskWorkflow> {
  static int _next = 0;
  late final String instance = 'task-workflow-${_next++}';
  late String view = component.initialView == 'list' ? 'list' : 'workflow';
  String repository = '', status = '', validation = '', agent = '';
  String? selected, hovered;
  bool creatingRelease = false;
  String newReleaseName = '', newReleaseRepository = '', newReleaseCut = '';
  String repairProfile = '';
  String? assemblyMode, assemblyAgent, assemblyRuns, assemblyRepairs;
  String releaseGroup = '';
  String? releaseReviewLimit;
  String? releasePullTitle, releasePullBody, releasePullSha;
  String exclusionReason = '';
  String? historyRelease;
  List<Map<String, dynamic>> retainedRevisions = [];
  Map<String, dynamic>? retainedRelease;
  int? historyBefore;
  int historyRequest = 0;
  bool historyLoading = false;
  String? historyError;
  bool hideMerged = false, lanes = true, panel = false, compact = false;
  double zoom = 1;
  ({double x, double y, double left, double top, int id})? drag;
  List<_Box> boxes = [];
  List<_Lane> bands = [];
  List<_Link> links = [];
  double graphWidth = 0, graphHeight = 0;
  // Horizontal scroll, mirrored into CSS so lane headers stay pinned.
  double scrollX = 0;
  web.MediaQueryList? phone;
  late final JSFunction phoneChanged = ((web.Event _) {
    if (mounted) setState(() => compact = phone?.matches ?? false);
  }).toJS;

  /// setState for the panel extension (setState is protected).
  void update(void Function() change) => setState(change);
  web.HTMLElement? get viewport =>
      web.document.getElementById('$instance-viewport') as web.HTMLElement?;

  @override
  void initState() {
    super.initState();
    // Below 640px the lane header shrinks to the agent tile (template rule).
    phone = web.window.matchMedia('(max-width: 639.98px)');
    compact = phone!.matches;
    phone!.addEventListener('change', phoneChanged);
  }

  @override
  void dispose() {
    phone?.removeEventListener('change', phoneChanged);
    super.dispose();
  }

  @override
  void didUpdateComponent(TaskWorkflow old) {
    super.didUpdateComponent(old);
    if (creatingRelease) {
      final created = component.releases
          .where(
            (release) =>
                release['version'] == newReleaseName &&
                release['repository'] == newReleaseRepository &&
                !old.releases.any(
                  (previous) => previous['id'] == release['id'],
                ),
          )
          .firstOrNull;
      if (created != null) {
        selected = 'release:${created['id']}';
        creatingRelease = false;
        newReleaseName = '';
      }
    }
    if (!component.tasks.any((task) => _id(task) == selected) &&
        !component.releases.any(
          (release) => 'release:${release['id']}' == selected,
        ))
      selected = null;
    if (!component.tasks.any(
          (task) => '${task['repository'] ?? ''}' == repository,
        ) &&
        !component.releases.any(
          (release) => release['repository'] == repository,
        ))
      repository = '';
    if (!component.tasks.any((task) => _agent(task) == agent)) agent = '';
  }

  String _id(Map task) => '${task['id'] ?? ''}';
  String _agent(Map task) =>
      '${task['profile_id'] ?? (task['assignment'] as Map?)?['profile_id'] ?? (task['assigned_agent'] as Map?)?['id'] ?? ''}';
  Map? _profile(String owner) => component.profiles
      .where((profile) => '${profile['id']}' == owner)
      .firstOrNull;
  String _name(Map task) {
    final owner = _profile(_agent(task));
    return '${owner?['name'] ?? taskAgentName(task, fallback: 'Assignment not recorded')}';
  }

  Object? _role(Map task) =>
      _profile(_agent(task))?['role'] ??
      (task['assigned_agent'] as Map?)?['role'] ??
      _name(task);

  /// The agent's live activity, or null when the host has no live state.
  String? _activity(String owner) => component.activity == null
      ? null
      : component.activity![owner] ?? 'offline';

  String _stateKey(Map task) => taskStateKey(task);
  String _stateLabel(String key) => taskStateLabel(key);
  String _state(Map task) => taskState(task);
  Map? _evidence(Map task) {
    final target = task['merge_sha'] ?? task['head_sha'];
    final record = (task['builds'] as Map?)?[target];
    return target != null && record is Map && record['target'] == target
        ? record
        : null;
  }

  String _validation(Map task) {
    final evidence = _evidence(task);
    if (evidence?['state'] == 'complete' &&
        evidence?['required_checks_verified'] == true)
      return 'verified';
    if (['queued', 'running'].contains(evidence?['state'])) return 'running';
    return 'unverified';
  }

  bool _attention(Map task) => taskNeedsAttention(task);
  String _tone(Map task) => taskTone(task);

  bool _open(Map task) =>
      !['merged', 'completed', 'closed'].contains(task['state']);

  List<Map<String, dynamic>> get shown => component.tasks
      .where(
        (task) =>
            (repository.isEmpty || task['repository'] == repository) &&
            (status.isEmpty || _stateKey(task) == status) &&
            (validation.isEmpty || _validation(task) == validation) &&
            !(hideMerged && task['state'] == 'merged'),
      )
      .toList();

  Set<String> get emphasis {
    final seed = selected ?? hovered;
    if (seed == null)
      return boxes
          .where((box) => box.agent == agent)
          .map((box) => box.id)
          .toSet();
    Set<String> walk(bool upstream) {
      final found = <String>{seed};
      var changed = true;
      while (changed) {
        changed = false;
        for (final link in links) {
          if (found.contains(upstream ? link.to : link.from) &&
              found.add(upstream ? link.from : link.to))
            changed = true;
        }
      }
      return found;
    }

    return {...walk(true), ...walk(false)};
  }

  void chooseTask(String? id, {bool reveal = false}) {
    setState(() {
      selected = id;
      assemblyMode = assemblyAgent = assemblyRuns = assemblyRepairs = null;
      releaseGroup = '';
      releaseReviewLimit = null;
      releasePullTitle = releasePullBody = releasePullSha = null;
      creatingRelease = false;
      historyRelease = null;
      retainedRelease = null;
      historyRequest++;
      if (id != null) panel = true;
    });
    if (reveal && id != null) scheduleMicrotask(() => _reveal(id));
  }

  void _reveal(String id) {
    final box = boxes.where((box) => box.id == id).firstOrNull;
    final area = viewport;
    if (box == null || area == null) return;
    area.scrollLeft = math.max(
      0,
      (box.x + _nodeW / 2) * zoom - area.clientWidth / 2,
    );
    area.scrollTop = math.max(
      0,
      (box.y + boxHeight(box) / 2) * zoom - area.clientHeight / 2,
    );
  }

  void setZoom(double value) {
    final area = viewport;
    final centerX =
        ((area?.scrollLeft ?? 0) + (area?.clientWidth ?? 0) / 2) / zoom;
    final centerY =
        ((area?.scrollTop ?? 0) + (area?.clientHeight ?? 0) / 2) / zoom;
    setState(() => zoom = value.clamp(.3, 1.75));
    scheduleMicrotask(() {
      if (!mounted || area == null) return;
      area.scrollLeft = math.max(0, centerX * zoom - area.clientWidth / 2);
      area.scrollTop = math.max(0, centerY * zoom - area.clientHeight / 2);
    });
  }

  void fit() {
    final area = viewport;
    if (area == null || graphWidth == 0 || graphHeight == 0) return;
    setState(
      () => zoom = math
          .min(
            (area.clientWidth - 24) / graphWidth,
            (area.clientHeight - 24) / graphHeight,
          )
          .clamp(.3, 1.25),
    );
    scheduleMicrotask(() {
      if (mounted) {
        area.scrollLeft = 0;
        area.scrollTop = 0;
      }
    });
  }

  void keyFor(String id, web.KeyboardEvent event) {
    String? next;
    if (event.key == 'Escape') {
      event.preventDefault();
      chooseTask(null);
      return;
    }
    if (event.key == 'ArrowRight')
      next = links.where((link) => link.from == id).firstOrNull?.to;
    if (event.key == 'ArrowLeft')
      next = links.where((link) => link.to == id).firstOrNull?.from;
    if (event.key == 'ArrowDown' || event.key == 'ArrowUp') {
      final order = boxes.toList()
        ..sort(
          (a, b) => a.column == b.column
              ? a.y.compareTo(b.y)
              : a.column.compareTo(b.column),
        );
      final index = order.indexWhere((box) => box.id == id);
      final offset = event.key == 'ArrowDown' ? 1 : -1;
      if (index + offset >= 0 && index + offset < order.length)
        next = order[index + offset].id;
    }
    if (next == null) return;
    event.preventDefault();
    chooseTask(next, reveal: true);
    scheduleMicrotask(
      () =>
          (web.document.getElementById('$instance-node-$next')
                  as web.HTMLElement?)
              ?.focus(),
    );
  }

  @override
  Component build(BuildContext context) {
    final tasks = shown;
    layoutGraph(tasks);
    final highlighted = emphasis;
    final repositories = releaseRepositories;
    return el('section', [
      el('div', [
        el(
          'div',
          [
            for (final value in ['workflow', 'list'])
              el(
                'button',
                [
                  icon(value == 'workflow' ? 'workflow' : 'list'),
                  text(value == 'workflow' ? 'Workflow' : 'List'),
                  if (value == 'list')
                    el('span', [
                      text(component.tasks.length),
                    ], classes: 'count'),
                ],
                classes: 'tabs__tab task-wf-tab',
                attributes: {
                  'type': 'button',
                  'role': 'tab',
                  'id': '$instance-tab-$value',
                  'aria-selected': '${view == value}',
                  'aria-controls': '$instance-panel-$value',
                },
                events: {'click': (_) => setState(() => view = value)},
              ),
          ],
          classes: 'tabs task-wf-tabs',
          attributes: {'role': 'tablist', 'aria-label': 'Task view'},
        ),
        if (view == 'workflow')
          el('div', [
            if (component.onReleaseAction != null)
              button(
                'New release candidate',
                () => setState(() {
                  creatingRelease = true;
                  selected = null;
                  panel = true;
                  newReleaseRepository = repository.isNotEmpty
                      ? repository
                      : (repositories.firstOrNull ?? '');
                }),
                small: true,
                iconName: 'rocket',
                disabled: component.releaseBusy,
              ),
            switchControl(
              'Lanes by agent',
              lanes,
              (value) => setState(() => lanes = value),
            ),
            el(
              'span',
              [],
              classes: 'toolbar__divider',
              attributes: {'aria-hidden': 'true'},
            ),
            zoomGroup(),
          ], classes: 'toolbar wf-view'),
      ], classes: 'wf-tabsrow'),
      if (view == 'workflow')
        el('div', [
          select('Repository', repository, {
            '': 'All repositories',
            for (final name in repositories) name: name,
          }, (value) => setState(() => repository = value)),
          select('Task state', status, {
            '': 'All states',
            for (final state in component.tasks.map(_stateKey).toSet())
              state: taskStateLabel(state),
          }, (value) => setState(() => status = value)),
          select('Validation', validation, {
            '': 'All evidence',
            'verified': 'Exact commit verified',
            'running': 'Queued or running',
            'unverified': 'Not verified',
          }, (value) => setState(() => validation = value)),
          switchControl(
            'Hide merged',
            hideMerged,
            (value) => setState(() => hideMerged = value),
          ),
          el(
            'span',
            [
              text(
                'Showing ${tasks.length} of ${component.tasks.length} tasks',
              ),
            ],
            classes: 'task-wf-result',
            attributes: {'aria-live': 'polite'},
          ),
        ], classes: 'toolbar task-wf-toolbar'),
      if (view == 'workflow')
        el(
          'div',
          [
            summary(),
            agentBar(tasks),
            el(
              'div',
              [
                el('div', [
                  canvas(tasks, highlighted),
                  legend(tasks),
                ], classes: 'task-wf-main'),
                if (panel)
                  el(
                    'div',
                    [inspector()],
                    classes: 'task-wf-panel',
                    attributes: {'id': '$instance-inspector'},
                  ),
              ],
              classes: 'task-wf-layout',
              attributes: {'data-panel': '$panel', 'data-view': view},
            ),
          ],
          classes: 'task-wf-board',
          attributes: {
            'role': 'tabpanel',
            'id': '$instance-panel-workflow',
            'aria-labelledby': '$instance-tab-workflow',
          },
        )
      else
        el(
          'div',
          [
            component.listView ??
                table(
                  ['Task', 'Agent', 'State', 'Repository'],
                  [
                    for (final task in component.tasks)
                      [
                        button(
                          '${task['title']}',
                          () => component.onOpenTask(_id(task)),
                        ),
                        text(_name(task)),
                        badge(_state(task), tone: _tone(task)),
                        text(task['repository']),
                      ],
                  ],
                ),
          ],
          classes: 'task-wf-list-panel',
          attributes: {
            'role': 'tabpanel',
            'id': '$instance-panel-list',
            'aria-labelledby': '$instance-tab-list',
          },
        ),
    ], classes: 'task-workflow');
  }
}
