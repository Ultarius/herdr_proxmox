import 'artifact_download.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'clipboard_copy.dart';
import 'request_id.dart';

/// Task lifecycle labels. The gateway owns these states; an unknown value is
/// shown verbatim rather than guessed.
const taskStates = <String, String>{
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

class TasksPage extends StatefulWidget {
  const TasksPage({super.key});
  @override
  State<TasksPage> createState() => _TasksPageState();
}

class _TasksPageState extends State<TasksPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  List<Map<String, dynamic>> tasks = [];
  Map<String, dynamic> github = {};
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
        });
        refresh();
      }
    });
    connected = connection.state.connected;
    refresh();
    timer = Timer.periodic(const Duration(seconds: 15), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    subscription?.cancel();
    super.dispose();
  }

  /// Read the task list. A read already in flight is joined, so a caller that
  /// needs fresh data after an action never silently keeps stale state.
  Future<void> refresh() async {
    if (!connection.state.connected) {
      if (mounted) setState(() => connected = false);
      return;
    }
    if (refreshing) return pending;
    final epoch = connection.generation;
    final read = _read(epoch);
    pending = read;
    refreshing = true;
    try {
      await read;
    } finally {
      refreshing = false;
      if (identical(pending, read)) pending = null;
    }
  }

  Future<void> _read(int epoch) async {
    try {
      final data = await connection.request('tasks');
      if (mounted && epoch == connection.generation) {
        setState(() {
          tasks = List<Map<String, dynamic>>.from(data['tasks'] ?? []);
          github = Map<String, dynamic>.from(data['github'] ?? {});
          readError = null; // A successful read clears an earlier failure.
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
      requests.remove(selection);
      await refresh();
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
      final repository = TextEditingController(text: 'herdr_proxmox');
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
                      TextField(
                        controller: repository,
                        decoration: const InputDecoration(
                          labelText: 'Repository inside projects',
                        ),
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
                          // Only an agent already assigned to the typed
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
                          if (matches.isEmpty)
                            return const InputDecorator(
                              decoration: InputDecoration(
                                labelText: 'Assigned agent',
                                errorText:
                                    'No worktree agent is assigned to this repository.',
                              ),
                              child: Text(
                                'Hire a worktree agent for it in Organization first.',
                              ),
                            );
                          final selected =
                              matches.any((p) => '${p['id']}' == profile)
                              ? profile
                              : '${matches.first['id']}';
                          return DropdownButtonFormField<String>(
                            key: ValueKey(
                              matches.map((p) => p['id']).join(','),
                            ),
                            initialValue: selected,
                            isExpanded: true,
                            decoration: const InputDecoration(
                              labelText: 'Assigned agent',
                            ),
                            items: [
                              for (final p in matches)
                                DropdownMenuItem(
                                  value: '${p['id']}',
                                  child: Text('${p['name']}'),
                                ),
                            ],
                            onChanged: (value) {
                              if (value != null) update(() => profile = value);
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
                FilledButton(
                  onPressed: () => Navigator.pop(context, {
                    'title': title.text,
                    'description': description.text,
                    'repository': repository.text.trim(),
                    'base_ref': base.text.trim(),
                    'profile_id': profile,
                  }),
                  child: const Text('Create task'),
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
        for (final check in (build['checks'] as List? ?? []))
          if (check is Map)
            Text('${check['id'] ?? check['name']}: ${check['status']}'),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => ListView(
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
      for (final task in tasks)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${task['title']}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                Text('${taskState(task)} · ${task['repository']}'),
                SelectableText(
                  'Branch: ${task['branch']}\nBase: ${task['base_ref']} · ${task['base_sha']}',
                ),
                if (task['head_sha'] != null)
                  SelectableText('Candidate: ${task['head_sha']}'),
                if (task['error'] != null)
                  Text(
                    '${task['error']}',
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (task['candidate_changed'] == true)
                  const Text(
                    'PR head differs from the reviewed candidate. Review the new commit before publishing again.',
                  ),
                if (task['merge_sha'] != null)
                  SelectableText(
                    'Merged result: ${task['merge_sha']}\nRefresh the shared base and validate/build this resulting commit. The PR head is not the merge result.',
                  ),
                if (task['pull'] is Map) ...[
                  SelectableText(
                    'PR #${task['pull']['number']} · ${pullStatus(task)}\n${task['pull']['url']}',
                  ),
                  // GitHub, not this page, decides whether a pull request can
                  // merge. Only an unmerged pull request can conflict.
                  if (task['pull']['mergeable'] == false &&
                      !['merged', 'closed'].contains('${task['state']}'))
                    Text(
                      'GitHub reports that this pull request conflicts and cannot merge cleanly.',
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  if (task['last_sync'] != null)
                    Text('Pull request status last read ${task['last_sync']}.'),
                ],
                if (task['builds'] is Map) ...[
                  for (final entry in (task['builds'] as Map).entries)
                    if (entry.value is Map)
                      _buildEvidence('${entry.key}', entry.value as Map),
                ],
                if (task['checks'] is List) ...[
                  for (final check in task['checks'])
                    if (check is Map)
                      Text(
                        '${check['name']}: ${check['conclusion'] ?? check['status']}',
                      ),
                ],
                if (task['statuses'] is List) ...[
                  for (final check in task['statuses'])
                    if (check is Map)
                      Text('${check['context']}: ${check['state']}'),
                ],
                if (task['reviews'] is List) ...[
                  for (final item in task['reviews'])
                    if (item is Map)
                      Text('${item['user']?['login']}: ${item['state']}'),
                ],
                const SizedBox(height: 12),
                Wrap(
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
                        !['merged', 'closed'].contains(task['state']))
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
                                  await downloadBinaryArtifact(
                                    bytes,
                                    'herdr-${task['id']}.patch',
                                    contentType: 'text/x-patch',
                                  );
                                } catch (exception) {
                                  if (mounted)
                                    setState(
                                      () => error = exception.toString(),
                                    );
                                }
                              },
                        child: const Text('Download candidate patch'),
                      ),
                    if (task['head_sha'] != null &&
                        !['merged', 'closed'].contains(task['state']))
                      OutlinedButton(
                        onPressed:
                            admin && !busy && github['configured'] == true
                            ? () => review(task)
                            : null,
                        child: const Text('Review and publish'),
                      ),
                    if (task['publish'] != null && task['pull'] == null)
                      FilledButton(
                        onPressed:
                            admin && !busy && github['configured'] == true
                            ? () => act('pull', {'task_id': task['id']})
                            : null,
                        child: const Text('Open draft PR'),
                      ),
                    if (task['pull'] is Map)
                      OutlinedButton(
                        onPressed:
                            admin && !busy && github['configured'] == true
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
                ),
                ExpansionTile(
                  title: const Text('Task history'),
                  children: [
                    for (final entry in (task['audit'] as List? ?? []))
                      if (entry is Map)
                        ListTile(
                          title: Text('${entry['action']} · ${entry['actor']}'),
                          subtitle: Text(
                            '${entry['at']}'
                            '${entry['state'] != null ? ' · ${entry['state']}' : ''}'
                            '${entry['head_sha'] != null ? ' · ${'${entry['head_sha']}'.substring(0, 12)}' : ''}',
                          ),
                        ),
                  ],
                ),
              ],
            ),
          ),
        ),
    ],
  );
}
