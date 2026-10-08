import 'routes.dart';
import 'task_activity.dart';
import 'task_timeline.dart';
import 'artifact_download.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'clipboard_copy.dart';
import 'request_id.dart';

/// Task lifecycle labels. The gateway owns these states; an unknown value is
/// shown verbatim rather than guessed.
const taskStates = <String, String>{
  'completed': 'Done',
  'draft': 'Draft',
  'implementing': 'Implementing',
  'review_ready': 'Review ready',
  'published': 'Published',
  'pr_open': 'Pull request open',
  'merged': 'Merged',
  'closed': 'Closed',
};

String taskState(Map<String, dynamic> task) =>
    taskStates['${task['state']}'] ?? '${task['state']}';

/// A merged pull request is never presented as awaiting review, and a draft is
/// never presented as ready.
String pullStatus(Map<String, dynamic> task) {
  if ('${task['state']}' == 'merged') return 'Merged';
  final pull = task['pull'];
  if (pull is! Map || pull['state'] != 'open') return 'Closed';
  return pull['draft'] == true ? 'Draft' : 'Open for review';
}

/// Build states as sentences. A raw state name is not an explanation, and a
/// finished run is never described as verified unless it was.
const buildStates = <String, String>{
  'queued': 'Queued',
  'running': 'Running',
  'complete': 'Finished',
  'failed': 'Failed',
  'error': 'Did not finish',
  'interrupted': 'Interrupted',
  'cancelled': 'Cancelled',
};

/// A finished build is not a verified pass. Required checks must be recorded.
String buildSummary(String target, Map<dynamic, dynamic> build) {
  final state = '${build['state']}';
  final parts = <String>[
    'Build $target',
    buildStates[state] ?? state,
    if (build['exit_code'] != null) 'exit ${build['exit_code']}',
  ];
  final verified = build['required_checks_verified'] == true;
  return '${parts.join(' · ')}'
      '${state == 'complete' && !verified ? ' · required checks not verified' : ''}';
}

/// Task repositories come from managed agent assignments, relative to projects.
List<String> taskRepositories(
  List<Map<String, dynamic>> profiles,
  String root,
) {
  final prefix =
      '${root.replaceAll('\\', '/').replaceAll(RegExp(r'/+$'), '')}/';
  return (profiles
      .map((p) {
        final project = '${p['project'] ?? ''}'.replaceAll('\\', '/');
        return project.startsWith(prefix)
            ? project.substring(prefix.length)
            : project;
      })
      .where(
        (path) =>
            path.isNotEmpty &&
            !path.startsWith('/') &&
            !path.contains(':') &&
            !path.split('/').contains('..'),
      )
      .toSet()
      .toList()
    ..sort());
}

class TasksPage extends StatefulWidget {
  const TasksPage({super.key, this.taskId, this.coordinator});
  final String? taskId;
  final AppCoordinator? coordinator;
  @override
  State<TasksPage> createState() => _TasksPageState();
}

class _TasksPageState extends State<TasksPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  List<Map<String, dynamic>> tasks = [];
  Map<String, dynamic> github = {};
  String executor = 'unavailable';
  String section = 'Overview';
  final Set<String> expandedMeetings = {};
  String? localTaskId;
  String? get selectedId => widget.taskId ?? localTaskId;
  String? error;
  // A failed list read must not erase an action failure, and vice versa.
  String? readError;
  bool busy = false;
  bool refreshing = false;
  bool connected = true;
  Future<void>? pending;
  Timer? timer;
  StreamSubscription? subscription;
  int generation = -1;
  // Keep the exact request across lost responses. Changing the selection gets
  // a new identity; retrying the same action reconciles the durable request.
  final Map<String, String> requests = {};
  bool get admin => connection.operator.value['role'] == 'admin';

  @override
  void initState() {
    super.initState();
    generation = connection.generation;
    subscription = connection.stream.listen((_) {
      if (!mounted) return;
      if (connection.state.connected != connected)
        setState(() => connected = connection.state.connected);
      if (generation != connection.generation) {
        generation = connection.generation;
        setState(() {
          tasks = [];
          github = {};
          error = null;
          readError = null;
          requests.clear();
          expandedMeetings.clear();
        });
        // A read begun before reconnect is not valid for the new connection.
        refresh(force: true);
      }
    });
    connected = connection.state.connected;
    refresh();
    timer = Timer.periodic(const Duration(seconds: 15), (_) => refresh());
  }

  @override
  void didUpdateWidget(covariant TasksPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.taskId != widget.taskId) {
      tasks = [];
      localTaskId = null;
      section = 'Overview';
      error = null;
      readError = null;
      requests.clear();
      refreshSelection();
    }
  }

  Future<void> refreshSelection() async {
    await refresh(force: true);
  }

  @override
  void dispose() {
    timer?.cancel();
    subscription?.cancel();
    super.dispose();
  }

  /// Read the task list. A read already in flight is joined, so a caller that
  /// needs fresh data after an action never silently keeps stale state. A
  /// reconnect forces a new read: the old one belongs to the old connection.
  Future<void> refresh({bool force = false}) async {
    if (!connection.state.connected) {
      if (mounted) setState(() => connected = false);
      return;
    }
    if (!force && pending != null) return pending;
    final epoch = connection.generation;
    final read = _read(epoch);
    pending = read;
    refreshing = true;
    try {
      await read;
    } finally {
      if (identical(pending, read)) {
        pending = null;
        refreshing = false;
      }
    }
  }

  Future<void> _read(int epoch) async {
    final id = selectedId;
    try {
      final data = await connection.request('tasks');
      if (id != selectedId) return;
      Map<String, dynamic>? detail;
      String? detailError;
      if (id != null) {
        try {
          detail = Map<String, dynamic>.from(
            await connection.request('tasks/detail?id=$id'),
          );
        } catch (e) {
          detailError = '$e';
        }
      }
      if (id != selectedId) return;
      if (mounted && epoch == connection.generation) {
        setState(() {
          if (id == null) {
            tasks = List<Map<String, dynamic>>.from(data['tasks'] ?? []);
            readError = null;
          } else {
            // A failed detail read still shows the compact row it came from.
            final rows = List<Map<String, dynamic>>.from(data['tasks'] ?? []);
            final match = rows.where((row) => row['id'] == id).firstOrNull;
            tasks = detail != null
                ? [detail]
                : (match != null ? [match] : <Map<String, dynamic>>[]);
            readError = detailError;
          }
          github = Map<String, dynamic>.from(data['github'] ?? {});
          executor = '${data['executor'] ?? 'unavailable'}';
        });
      }
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => readError = '$e');
    }
  }

  Future<void> act(String action, Map<String, dynamic> body) async {
    if (busy || !admin) return;
    final epoch = connection.generation;
    final selection = '$action:$body';
    final request = requests.putIfAbsent(
      selection,
      () => 'task-${newRequestId()}',
    );
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await connection.request('tasks/$action', {
        ...body,
        'request_id': request,
      });
      if (!mounted || epoch != connection.generation) return;
      requests.remove(selection);
      // A read begun before the mutation is not post-action evidence.
      if (pending != null) await pending;
      if (mounted && epoch == connection.generation) await refresh();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> configure() async {
    final token = TextEditingController();
    final repos = TextEditingController(
      text: (github['repositories'] as List? ?? []).join('\n'),
    );
    final epoch = connection.generation;
    try {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('GitHub publishing'),
          content: SizedBox(
            width: 540,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Use a fine-grained token for selected repositories: Contents write, Pull requests write, Checks read, and Commit statuses read. Workflow changes may also require Workflows write. Keep branch protection enabled.\n\nAgents and gateway share a Linux user; this credential is not isolated from same-user agents. Use only trusted agents and repositories.',
                  ),
                  TextField(
                    controller: token,
                    obscureText: true,
                    enableSuggestions: false,
                    autocorrect: false,
                    decoration: const InputDecoration(
                      labelText: 'Personal access token',
                    ),
                    maxLength: 255,
                  ),
                  TextField(
                    controller: repos,
                    minLines: 2,
                    maxLines: 5,
                    decoration: const InputDecoration(
                      labelText: 'Allowed owner/repository (one per line)',
                    ),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Validate and save'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted || epoch != connection.generation)
        return;
      setState(() {
        busy = true;
        error = null;
      });
      await connection.request('github/configure', {
        'token': token.text.trim(),
        'repositories': repos.text
            .split('\n')
            .map((s) => s.trim())
            .where((s) => s.isNotEmpty)
            .toList(),
      });
      await refresh();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      token.clear();
      token.dispose();
      repos.dispose();
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> create() async {
    final epoch = connection.generation;
    try {
      final directory = await connection.request('organizations/directory');
      if (!mounted || epoch != connection.generation) return;
      final profiles =
          List<Map<String, dynamic>>.from(directory['profiles'] ?? [])
              .where((p) => p['group_id'] == null && p['use_worktree'] != false)
              .toList();
      if (profiles.isEmpty)
        throw StateError('Hire a worktree agent before creating a task.');
      final title = TextEditingController();
      final description = TextEditingController();
      final projects = await connection.request('projects/browse', {
        'path': '',
      });
      if (!mounted || epoch != connection.generation) return;
      final repositories = taskRepositories(
        profiles,
        '${projects['root'] ?? ''}',
      );
      if (repositories.isEmpty)
        throw StateError(
          'No managed repository is assigned to a worktree agent.',
        );
      final repository = TextEditingController(text: repositories.first);
      final base = TextEditingController(text: 'refs/remotes/origin/main');
      var profile = '${profiles.first['id']}';
      Map<String, dynamic>? body;
      try {
        body = await showDialog<Map<String, dynamic>>(
          context: context,
          builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
              title: const Text('Create development task'),
              content: SizedBox(
                width: 560,
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      TextField(
                        controller: title,
                        maxLength: 120,
                        decoration: const InputDecoration(
                          labelText: 'Task title',
                        ),
                      ),
                      TextField(
                        controller: description,
                        minLines: 3,
                        maxLines: 8,
                        maxLength: 8000,
                        decoration: const InputDecoration(
                          labelText:
                              'Change, acceptance criteria and required checks',
                        ),
                      ),
                      DropdownButtonFormField<String>(
                        initialValue: repository.text,
                        isExpanded: true,
                        decoration: const InputDecoration(
                          labelText: 'Repository',
                        ),
                        items: [
                          for (final path in repositories)
                            DropdownMenuItem(value: path, child: Text(path)),
                        ],
                        onChanged: (value) {
                          if (value != null)
                            update(() => repository.text = value);
                        },
                      ),
                      TextField(
                        controller: base,
                        decoration: const InputDecoration(
                          labelText: 'Explicit remote base ref',
                        ),
                      ),
                      ValueListenableBuilder<TextEditingValue>(
                        valueListenable: repository,
                        builder: (context, value, _) {
                          // Only an agent already assigned to the selected
                          // repository can own its task branch.
                          final wanted = value.text.trim().replaceAll(
                            '\\',
                            '/',
                          );
                          final matches = profiles.where((p) {
                            final project = '${p['project']}'.replaceAll(
                              '\\',
                              '/',
                            );
                            return wanted.isEmpty ||
                                project == wanted ||
                                project.endsWith('/$wanted');
                          }).toList();
                          final selected = matches.isEmpty
                              ? null
                              : matches.any((p) => '${p['id']}' == profile)
                              ? profile
                              : '${matches.first['id']}';
                          return DropdownButtonFormField<String>(
                            key: ValueKey(
                              matches.map((p) => p['id']).join(','),
                            ),
                            initialValue: selected,
                            isExpanded: true,
                            hint: const Text('No eligible agents'),
                            decoration: InputDecoration(
                              labelText: 'Assigned agent',
                              helperText: matches.isEmpty
                                  ? wanted.isEmpty
                                        ? 'Enter the repository inside projects first.'
                                        : 'Hire a worktree agent for this repository in Organization first.'
                                  : null,
                            ),
                            items: [
                              for (final p in matches)
                                DropdownMenuItem(
                                  value: '${p['id']}',
                                  child: Text('${p['name']}'),
                                ),
                            ],
                            onChanged: matches.isEmpty
                                ? null
                                : (value) {
                                    if (value != null)
                                      update(() => profile = value);
                                  },
                          );
                        },
                      ),
                      const Text(
                        'Creates a recorded task first. Launch starts work in a new task branch from the captured local remote-tracking SHA. Fetch beforehand if the base is stale. Existing agent runs must be inspected and released first.',
                      ),
                    ],
                  ),
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel'),
                ),
                ValueListenableBuilder<TextEditingValue>(
                  valueListenable: repository,
                  builder: (context, value, _) {
                    final wanted = value.text.trim().replaceAll('\\', '/');
                    final eligible = profiles.where((p) {
                      final project = '${p['project']}'.replaceAll('\\', '/');
                      return wanted.isNotEmpty &&
                          (project == wanted || project.endsWith('/$wanted'));
                    }).toList();
                    return FilledButton(
                      onPressed: eligible.isEmpty
                          ? null
                          : () {
                              final assigned =
                                  eligible.any((p) => '${p['id']}' == profile)
                                  ? profile
                                  : '${eligible.first['id']}';
                              Navigator.pop(context, {
                                'title': title.text,
                                'description': description.text,
                                'repository': repository.text.trim(),
                                'base_ref': base.text.trim(),
                                'profile_id': assigned,
                              });
                            },
                      child: const Text('Create task'),
                    );
                  },
                ),
              ],
            ),
          ),
        );
      } finally {
        title.dispose();
        description.dispose();
        repository.dispose();
        base.dispose();
      }
      if (body != null && mounted && epoch == connection.generation)
        await act('create', body);
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    }
  }

  Future<void> review(Map<String, dynamic> task) async {
    final epoch = connection.generation;
    try {
      task = Map<String, dynamic>.from(
        await connection.request('tasks/detail?id=${task['id']}'),
      );
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
      return;
    }
    if (!mounted || epoch != connection.generation) return;
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Review and publish candidate'),
        content: SizedBox(
          width: 760,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SelectableText(
                  'Repository: ${task['github_repository']}\nBranch: ${task['branch']}\nExact commit: ${task['head_sha']}',
                ),
                const SizedBox(height: 12),
                const Text(
                  'Publishing uploads this commit to GitHub. It does not merge, deploy, or prove validation passed. No force-push is used.',
                ),
                SelectableText(
                  '${task['diff_stat'] ?? ''}\n${task['diff'] ?? ''}',
                ),
                if (task['diff_truncated'] == true)
                  const Text(
                    'Diff truncated. Inspect the full change in Project explorer before approving.',
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Approve publication'),
          ),
        ],
      ),
    );
    if (approved == true && mounted && epoch == connection.generation) {
      await act('publish', {
        'task_id': task['id'],
        'head_sha': task['head_sha'],
      });
    }
  }

  Widget _buildEvidence(String target, Map<dynamic, dynamic> build) {
    final unverified =
        build['state'] == 'complete' &&
        build['required_checks_verified'] != true;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SelectableText(buildSummary(target, build)),
        if (unverified)
          Text(
            'This run finished without verified required checks. It is not evidence of a pass.',
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        if (build['note'] != null) Text('${build['note']}'),
        if (build['run_id'] != null || build['id'] != null)
          Wrap(
            spacing: 8,
            children: [
              TextButton.icon(
                icon: const Icon(Icons.download),
                label: const Text('Download build log'),
                onPressed: () async {
                  final epoch = connection.generation;
                  try {
                    final id = build['run_id'] ?? build['id'];
                    final bytes = await connection.bytes(
                      'validation/log?id=$id',
                    );
                    if (!mounted || epoch != connection.generation) return;
                    await downloadBinaryArtifact(
                      bytes,
                      'build-$id.log',
                      contentType: 'text/plain',
                    );
                  } catch (e) {
                    if (mounted && epoch == connection.generation)
                      setState(() => error = '$e');
                  }
                },
              ),
              if (widget.coordinator != null)
                TextButton(
                  onPressed: () => widget.coordinator!.push(BuildsRoute()),
                  child: const Text('Artifacts & deployments'),
                ),
            ],
          ),
        for (final check in (build['checks'] as List? ?? []))
          if (check is Map)
            Text('${check['id'] ?? check['name']}: ${check['status']}'),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => selectedId == null
      ? ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Text('Tasks', style: Theme.of(context).textTheme.headlineLarge),
            const SizedBox(height: 8),
            const Text(
              'Develop in a task branch, review the exact commit, publish and open a draft pull request. Merge on GitHub. Updating the base and deploying are separate operations.',
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: admin && !busy ? create : null,
                  icon: const Icon(Icons.add),
                  label: const Text('Create task'),
                ),
                OutlinedButton.icon(
                  onPressed: admin && !busy ? configure : null,
                  icon: const Icon(Icons.key),
                  label: const Text('GitHub publishing'),
                ),
                IconButton(
                  onPressed: busy || refreshing ? null : refresh,
                  tooltip: 'Refresh tasks',
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            Text(
              github['configured'] == true
                  ? admin
                        ? 'GitHub account: ${github['login']} · ${github['repositories']}'
                        : 'GitHub publishing is configured. Publishing requires an administrator.'
                  : github['error'] != null
                  ? 'GitHub publishing is unavailable: ${github['error']}'
                  : 'GitHub publishing is not configured. Local task work is available.',
            ),
            if (busy) const LinearProgressIndicator(),
            if ((error ?? readError) != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  error ?? readError!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            if (tasks.isEmpty)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  !connected
                      ? 'Not connected to the gateway.'
                      : error != null || readError != null
                      ? 'Task list unavailable.'
                      : refreshing
                      ? 'Loading tasks…'
                      : 'No development tasks yet.',
                ),
              ),
            for (final task in tasks) _summary(task),
          ],
        )
      : _detail();

  Future<void> openTask(String id) async {
    if (widget.coordinator != null) {
      widget.coordinator!.push(TaskDetailRoute(id));
    } else {
      setState(() {
        localTaskId = id;
        tasks = [];
        section = 'Overview';
      });
      if (pending != null) await pending;
      if (mounted) await refresh();
    }
  }

  Future<void> backToTasks() async {
    if (widget.coordinator != null) {
      widget.coordinator!.replace(TasksRoute());
    } else {
      setState(() {
        localTaskId = null;
        section = 'Overview';
        tasks = [];
      });
      if (pending != null) await pending;
      if (mounted) await refresh();
    }
  }

  Widget _summary(Map<String, dynamic> task) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${task['title']}',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          Text('${taskState(task)} \u00b7 ${task['repository']}'),
          Text('${taskAgentName(task)}'),
          Text(taskNextStep(task)),
          if (task['updated_at'] != null)
            Text('Last activity ${task['updated_at']}'),
          TextButton.icon(
            onPressed: () => openTask('${task['id']}'),
            icon: const Icon(Icons.arrow_forward),
            label: const Text('Open task'),
          ),
        ],
      ),
    ),
  );

  Future<void> showCommit(Map<String, dynamic> task, String sha) async {
    final epoch = connection.generation;
    try {
      final data = await connection.request(
        'tasks/commit?id=${task['id']}&sha=$sha',
      );
      if (!mounted || epoch != connection.generation) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Commit changes'),
          content: SizedBox(
            width: 760,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SelectableText('$sha\nCompared with ${data['comparison']}'),
                  if (data['build'] is Map) _buildEvidence(sha, data['build']),
                  SelectableText('${data['diff'] ?? ''}'),
                  if (data['diff_truncated'] == true)
                    const Text(
                      'Diff truncated. Download the candidate patch for the full candidate change.',
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close'),
            ),
          ],
        ),
      );
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    }
  }

  Future<void> completeTask(Map<String, dynamic> task) async {
    var reason = '';
    var outcome = 'incorporated_elsewhere';
    var inspected = false;
    Map<String, dynamic>? body;
    body = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Mark task done'),
          content: SizedBox(
            width: 560,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Record why no implementation remains. This does not claim validation passed or deploy anything.',
                  ),
                  SelectableText(
                    'Candidate: ${task['head_sha']}\nFetched base: ${task['current_upstream_sha']}',
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: outcome,
                    isExpanded: true,
                    items: const [
                      DropdownMenuItem(
                        value: 'incorporated_elsewhere',
                        child: Text('Changes incorporated elsewhere'),
                      ),
                      DropdownMenuItem(
                        value: 'superseded',
                        child: Text('Superseded by other work'),
                      ),
                    ],
                    onChanged: (value) {
                      if (value != null) update(() => outcome = value);
                    },
                  ),
                  TextField(
                    onChanged: (value) => update(() => reason = value),
                    maxLength: 2000,
                    minLines: 2,
                    maxLines: 5,
                    decoration: const InputDecoration(
                      labelText: 'Reviewed evidence and completion reason',
                    ),
                  ),
                  if (reason.trim().isNotEmpty && reason.trim().length < 10)
                    const Text(
                      'Record at least a short sentence explaining this decision.',
                    ),
                  if (task['current_upstream_sha'] == null)
                    const Text(
                      'Refresh the task to load the fetched base before confirming.',
                    ),
                  CheckboxListTile(
                    value: inspected,
                    onChanged: (value) =>
                        update(() => inspected = value == true),
                    title: const Text(
                      'I reviewed this candidate and the upstream work.',
                    ),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed:
                  inspected &&
                      reason.trim().length >= 10 &&
                      task['current_upstream_sha'] != null
                  ? () => Navigator.pop(context, {
                      'task_id': task['id'],
                      'head_sha': task['head_sha'],
                      'upstream_sha': task['current_upstream_sha'],
                      'outcome': outcome,
                      'reason': reason,
                      'inspected': true,
                    })
                  : null,
              child: const Text('Confirm completion'),
            ),
          ],
        ),
      ),
    );
    if (body != null && mounted) await act('complete', body);
  }

  Future<void> discussTask(Map<String, dynamic> task) async {
    try {
      final data = await connection.request('organizations/directory');
      if (!mounted) return;
      final groups = [
        for (final g in data['groups'] as List? ?? [])
          if (g is Map &&
              g['organization_id'] == task['organization_id'] &&
              g['removed_at'] == null)
            g,
      ];
      if (groups.isEmpty)
        throw StateError(
          'Create an active group in this task organization first.',
        );
      var group = '${groups.first['id']}';
      var automatic = task['auto_review'] == true;
      final selected = await showDialog<Map<String, dynamic>>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
            title: const Text('Review work with a group'),
            content: SizedBox(
              width: 520,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Discuss this task, blockers and other open work. Recommendations become draft proposals; agents do not start new work.',
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: group,
                    isExpanded: true,
                    items: [
                      for (final g in groups)
                        DropdownMenuItem(
                          value: '${g['id']}',
                          child: Text('${g['name']}'),
                        ),
                    ],
                    onChanged: (value) => update(() => group = value!),
                  ),
                  CheckboxListTile(
                    value: automatic,
                    onChanged: (value) =>
                        update(() => automatic = value == true),
                    title: const Text(
                      'Automatically request a review when task evidence changes',
                    ),
                    subtitle: const Text(
                      'One discussion per changed candidate, blocker or validation state. Draft proposals only.',
                    ),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, {
                  'group_id': group,
                  'auto_review': automatic,
                }),
                child: const Text('Start group review'),
              ),
            ],
          ),
        ),
      );
      if (selected != null && mounted) {
        await act('review_policy', {'task_id': task['id'], ...selected});
        await act('discuss', {
          'task_id': task['id'],
          'group_id': selected['group_id'],
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Widget _nextAction(Map<String, dynamic> task) {
    final target = task['merge_sha'] ?? task['head_sha'];
    final evidence = (task['builds'] as Map?)?[target] as Map?;
    if (task['state'] == 'draft')
      return FilledButton.icon(
        onPressed: admin && !busy
            ? () => act('launch', {'task_id': task['id']})
            : null,
        icon: const Icon(Icons.play_arrow),
        label: const Text('Start implementation'),
      );
    if (target == null || ['closed', 'completed'].contains(task['state']))
      return const SizedBox.shrink();
    if (evidence?['state'] == 'complete') {
      final verified = evidence?['required_checks_verified'] == true;
      if (verified &&
          !['merged', 'published', 'pr_open'].contains(task['state'])) {
        return FilledButton.icon(
          onPressed: admin && !busy && github['configured'] == true
              ? () => review(task)
              : null,
          icon: const Icon(Icons.rate_review),
          label: const Text('Review latest commit'),
        );
      }
      if (verified) return const SizedBox.shrink();
      // The durable queue will not re-run a completed build; show the evidence
      // instead of offering an action that cannot change anything.
      return OutlinedButton.icon(
        onPressed: () => setState(() => section = 'Checks & builds'),
        icon: const Icon(Icons.fact_check_outlined),
        label: const Text('Inspect validation evidence'),
      );
    }
    final running = ['queued', 'running'].contains(evidence?['state']);
    return FilledButton.icon(
      onPressed: admin && !busy && !running
          ? () => act('build', {'task_id': task['id'], 'target': target})
          : null,
      icon: const Icon(Icons.fact_check),
      label: Text(
        running ? 'Validation in progress' : 'Validate latest commit',
      ),
    );
  }

  Widget _detail() {
    final task = tasks.isEmpty ? null : tasks.first;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: backToTasks,
            icon: const Icon(Icons.arrow_back),
            label: const Text('All tasks'),
          ),
        ),
        if (busy || refreshing && task == null) const LinearProgressIndicator(),
        if ((error ?? readError) != null)
          Text(
            error ?? readError!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        if (task == null) const Text('Task unavailable. Refresh to try again.'),
        IconButton(
          onPressed: busy ? null : refresh,
          tooltip: 'Refresh task',
          icon: const Icon(Icons.refresh),
        ),
        if (task != null) ...[
          Text(
            '${task['title']}',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          Text('${taskState(task)} \u00b7 ${task['repository']}'),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Text(
              taskNextStep(task),
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
          Align(alignment: Alignment.centerLeft, child: _nextAction(task)),
          Wrap(
            spacing: 8,
            children: [
              if (task['head_sha'] != null &&
                  !['completed', 'closed', 'merged'].contains(task['state']))
                OutlinedButton(
                  onPressed: admin && !busy ? () => completeTask(task) : null,
                  child: const Text('Mark task done'),
                ),
              OutlinedButton.icon(
                onPressed: admin && !busy ? () => discussTask(task) : null,
                icon: const Icon(Icons.groups),
                label: const Text('Discuss with group'),
              ),
            ],
          ),
          if (task['completion'] is Map) ...[
            Text(
              'Done: ${completionOutcome(task)}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text('Reason: ${task['completion']['reason']}'),
            SelectableText(
              'Candidate: ${task['completion']['candidate']}\nUpstream at completion: ${task['completion']['upstream']}',
            ),
            Text(
              task['completion']['validation'] == 'verified'
                  ? 'Required checks passed for this candidate. Deployment remains separate.'
                  : 'No verified required checks for this candidate at completion. Done is not validation.',
            ),
            Text(
              'Recorded by ${task['completion']['actor']} at ${task['completion']['at']}',
            ),
          ],
          if (task['follow_up_tasks'] is Map)
            for (final id in (task['follow_up_tasks'] as Map).values)
              TextButton.icon(
                onPressed: () => openTask('$id'),
                icon: const Icon(Icons.task_alt),
                label: const Text('Open follow-up draft'),
              ),
          if (task['review_error'] != null)
            Text('Group review waiting: ${task['review_error']}'),

          if (github['configured'] != true)
            const Text(
              'GitHub publishing is not configured. Local implementation and validation remain available.',
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final label in [
                'Overview',
                'Activity & agents',
                'Changes',
                'Checks & builds',
              ])
                ChoiceChip(
                  label: Text(label),
                  selected: section == label,
                  onSelected: (_) => setState(() => section = label),
                ),
            ],
          ),
          const SizedBox(height: 20),
          if (section == 'Overview') ...[
            if (task['source'] is Map) ...[
              Text(
                'Follow-up proposed by group ${task['source']['group_id']} in meeting ${task['source']['meeting_id']}',
              ),
              TextButton(
                onPressed: () => openTask('${task['source']['task_id']}'),
                child: const Text('Open originating task'),
              ),
            ],
            const Text('Task description & acceptance criteria'),
            SelectableText(
              '${task['description'] ?? 'No description recorded.'}',
            ),
            const SizedBox(height: 12),
            Text(
              'Assigned agent: ${taskAgentName(task, fallback: 'Identity unavailable')}',
            ),
            if (task['error'] != null) Text('Task issue: ${task['error']}'),
            if (task['automation_error'] != null)
              Text('Automation waiting: ${task['automation_error']}'),
            if (task['completion_receipt'] is Map)
              SelectableText(
                'Completion receipt recorded for ${task['completion_receipt']['commit']}. Worker reports are separate from validation.',
              ),
            ExpansionTile(
              title: const Text('Automation settings'),
              children: [
                SwitchListTile(
                  title: const Text('Automatically capture and validate'),
                  subtitle: Text(
                    executor == 'service'
                        ? 'Requires a matching completion receipt. Publishing and deployment remain separate.'
                        : 'Install the durable build service to enable automation.',
                  ),
                  value: task['auto_validate'] == true,
                  onChanged:
                      admin &&
                          !busy &&
                          ![
                            'merged',
                            'closed',
                            'completed',
                          ].contains(task['state']) &&
                          (task['auto_validate'] == true ||
                              executor == 'service')
                      ? (value) => act('policy', {
                          'task_id': task['id'],
                          'auto_validate': value,
                        })
                      : null,
                ),
              ],
            ),
            const SizedBox(height: 12),
            _actions(task),
          ],
          if (section == 'Activity & agents')
            TaskActivity(
              task: task,
              onOpen: (value) => setState(() => section = value),
            ),
          if (section == 'Activity & agents')
            for (final meeting in task['meeting_results'] as List? ?? [])
              if (meeting is Map)
                Card(
                  key: ValueKey('meeting-${meeting['job_id']}'),
                  child: ExpansionTile(
                    initiallyExpanded: expandedMeetings.contains(
                      '${meeting['job_id']}',
                    ),
                    onExpansionChanged: (value) {
                      if (value)
                        expandedMeetings.add('${meeting['job_id']}');
                      else
                        expandedMeetings.remove('${meeting['job_id']}');
                    },
                    title: Text('${meeting['group_name']} review'),
                    subtitle: Text('${meeting['state']}'),
                    children: [
                      if (widget.coordinator != null)
                        TextButton(
                          onPressed: () => widget.coordinator!.push(
                            GroupRoute('${meeting['group_id']}'),
                          ),
                          child: const Text('Open group discussion'),
                        ),
                      if (meeting['error'] != null &&
                          '${meeting['error']}'.isNotEmpty)
                        Text('${meeting['error']}'),
                      SelectableText(
                        '${meeting['result'] ?? 'Waiting for discussion output.'}',
                      ),
                      for (final proposal
                          in meeting['proposals'] as List? ?? [])
                        if (proposal is Map)
                          ListTile(
                            title: Text('${proposal['title']}'),
                            subtitle: Text(
                              'Assigned: ${proposal['assignee'] ?? proposal['profile_id']}\n${proposal['description']}',
                            ),
                            trailing: TextButton(
                              onPressed:
                                  admin &&
                                      !busy &&
                                      (task['follow_up_tasks']
                                              as Map?)?[proposal['key']] ==
                                          null
                                  ? () => act('proposal', {
                                      'task_id': task['id'],
                                      'meeting_id': meeting['job_id'],
                                      'proposal_key': proposal['key'],
                                    })
                                  : null,
                              child: Text(
                                (task['follow_up_tasks']
                                            as Map?)?[proposal['key']] ==
                                        null
                                    ? 'Create draft'
                                    : 'Draft created',
                              ),
                            ),
                          ),
                    ],
                  ),
                ),
          if (section == 'Changes') ...[
            SelectableText(
              'Branch: ${task['branch']}\nBase: ${task['base_ref']} \u00b7 ${task['base_sha']}\nCandidate: ${task['head_sha'] ?? 'Not captured'}',
            ),
            if (task['pull'] is Map) ...[
              SelectableText(
                'PR #${task['pull']['number']} \u00b7 ${pullStatus(task)}\n${task['pull']['url']}',
              ),
              if (task['pull']['mergeable'] == false &&
                  !['merged', 'closed', 'completed'].contains(task['state']))
                const Text('GitHub reports merge conflicts.'),
            ],
            if (task['merge_sha'] != null)
              SelectableText('Merged result: ${task['merge_sha']}'),
            const SizedBox(height: 12),
            TaskTimeline(
              task: task,
              onSelect: (sha) => showCommit(task, sha),
              onRefresh: refresh,
            ),
            ExpansionTile(
              title: const Text('Candidate diff'),
              children: [
                SelectableText(
                  '${task['diff_stat'] ?? ''}\n${task['diff'] ?? 'Capture a candidate to view its changes.'}',
                ),
                if (task['diff_truncated'] == true)
                  const Text(
                    'Diff truncated; download the candidate patch for the full change.',
                  ),
              ],
            ),
            _actions(task),
          ],
          if (section == 'Checks & builds') ...[
            Builder(
              builder: (context) {
                final target = task['merge_sha'] ?? task['head_sha'];
                final build = (task['builds'] as Map?)?[target];
                final label = build is! Map
                    ? 'not run'
                    : build['state'] == 'complete'
                    ? (build['required_checks_verified'] == true
                          ? 'verified'
                          : 'finished without verified required checks')
                    : '${build['state']}';
                return Text('Current candidate validation: $label');
              },
            ),
            if (task['builds'] is Map)
              for (final entry in (task['builds'] as Map).entries)
                if (entry.value is Map)
                  if (entry.key == (task['merge_sha'] ?? task['head_sha']))
                    _buildEvidence('${entry.key}', entry.value)
                  else
                    ExpansionTile(
                      title: Text(
                        'Previous candidate build \u00b7 ${entry.key}',
                      ),
                      subtitle: const Text(
                        'Does not validate the current candidate.',
                      ),
                      children: [_buildEvidence('${entry.key}', entry.value)],
                    ),
            for (final check in task['checks'] as List? ?? [])
              if (check is Map)
                Text(
                  '${check['name']}: ${check['conclusion'] ?? check['status']}',
                ),
            for (final check in task['statuses'] as List? ?? [])
              if (check is Map) Text('${check['context']}: ${check['state']}'),
            for (final review in task['reviews'] as List? ?? [])
              if (review is Map)
                Text('${review['user']?['login']}: ${review['state']}'),
            _actions(task),
          ],
        ],
      ],
    );
  }

  Widget _actions(Map<String, dynamic> task) => Wrap(
    spacing: 8,
    runSpacing: 8,
    children: [
      if (task['run_id'] == null)
        FilledButton(
          onPressed: admin && !busy
              ? () => act('launch', {'task_id': task['id']})
              : null,
          child: const Text('Launch task agent'),
        ),
      if (task['run_id'] != null &&
          !['merged', 'closed', 'completed'].contains(task['state']))
        OutlinedButton(
          onPressed: admin && !busy
              ? () => act('candidate', {'task_id': task['id']})
              : null,
          child: const Text('Capture candidate'),
        ),
      if (task['head_sha'] != null)
        OutlinedButton(
          onPressed: busy
              ? null
              : () async {
                  try {
                    final bytes = await connection.bytes(
                      'tasks/patch?id=${task['id']}',
                    );
                    if (!mounted) return;
                    final head = '${task['head_sha']}';
                    final suffix = head.length >= 12
                        ? head.substring(0, 12)
                        : head;
                    await downloadBinaryArtifact(
                      bytes,
                      'herdr-${task['id']}-$suffix.patch',
                      contentType: 'text/x-patch',
                    );
                    if (!mounted) return;
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text(
                          'Candidate patch fetched (${bytes.length} bytes) and handed to the browser.',
                        ),
                      ),
                    );
                  } catch (exception) {
                    if (mounted) setState(() => error = exception.toString());
                  }
                },
          child: const Text('Download candidate patch'),
        ),
      if (task['head_sha'] != null &&
          !['merged', 'closed', 'completed'].contains(task['state']))
        OutlinedButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => review(task)
              : null,
          child: const Text('Review and publish'),
        ),
      if (task['publish'] != null && task['pull'] == null)
        FilledButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => act('pull', {'task_id': task['id']})
              : null,
          child: const Text('Open draft PR'),
        ),
      if (task['pull'] is Map)
        OutlinedButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => act('refresh', {'task_id': task['id']})
              : null,
          child: const Text('Refresh PR status'),
        ),
      if (task['head_sha'] != null)
        OutlinedButton(
          onPressed: admin && !busy
              ? () => act('build', {
                  'task_id': task['id'],
                  'target': task['state'] == 'merged'
                      ? task['merge_sha']
                      : task['head_sha'],
                })
              : null,
          child: Text(
            task['state'] == 'merged'
                ? 'Build merged result'
                : 'Validate candidate',
          ),
        ),
      if (task['pull'] is Map)
        TextButton(
          onPressed: () async {
            final message = await copyTextOrDownload(
              '${task['pull']['url']}',
              'task-pr-link.txt',
            );
            if (!mounted) return;
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(SnackBar(content: Text(message)));
          },
          child: const Text('Copy PR link'),
        ),
    ],
  );
}
