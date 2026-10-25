import 'dart:convert';
import 'package:herdr_dashboard_state/task_execution.dart';
import 'package:herdr_dashboard_state/task_validation_environment.dart';
import 'package:herdr_dashboard_state/publishing_connections.dart';
import 'package:herdr_dashboard_state/task_backup.dart';
import 'native_task_ui.dart';
import '../navigation.dart';
import '../services.dart';
import 'discovery_panel.dart';
import 'task_activity.dart';
import 'task_timeline.dart';
import 'task_revisions.dart';
import 'task_workflow.dart';
import 'task_clipboard.dart';
import 'task_list.dart';
import 'task_state.dart';
export 'task_state.dart';
import '../ui.dart' as ui;
import '../overview.dart' show activityFor;

// Keep the legacy verdict for older gateways and persisted configurations during
// upgrades. Current gateways emit supported or not_exercised instead.
bool needsPrAuthentication(Map github, String? repository) =>
    (github['publication']
        as Map?)?[repository]?['verification']?['upstream_pr_creation'] ==
    'needs_different_authentication';

String publicationSummary(String repository, Map entry) {
  final verification = entry['verification'] as Map? ?? {};
  final account = verification['account_write_to_publishing'] == 'verified'
      ? 'Account write access verified'
      : 'Account write access not verified';
  final pr =
      verification['upstream_pr_creation'] == 'needs_different_authentication'
      ? 'Different GitHub authentication is required to create a PR in $repository. Configure a credential authorized for the upstream repository.'
      : verification['upstream_pr_creation'] == 'supported'
      ? 'PR authentication is compatible; creation has not been tested.'
      : 'Upstream PR write permission has not been verified. A branch push may succeed before PR creation is rejected; confirm the credential can write pull requests in $repository.';
  return '$repository → ${entry['publishing_repository']}\n$account. '
      'Token write access has not been tested. $pr';
}

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
  final base = root.replaceAll('\\', '/').replaceAll(RegExp(r'/+$'), '');
  // An empty root would make the prefix '/' and match every absolute path.
  if (base.isEmpty) return const [];
  final prefix = '$base/';
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

class TasksPage extends StatefulComponent {
  const TasksPage({
    super.key,
    this.taskId,
    this.initialView = 'workflow',
    this.coordinator = const AppCoordinator(),
    this.saveTaskBackup,
  });
  final String? taskId;
  final String initialView;
  final AppCoordinator? coordinator;
  final TaskBackupSaver? saveTaskBackup;
  @override
  State<TasksPage> createState() => _TasksPageState();
}

class _TasksPageState extends State<TasksPage> {
  DashboardBloc get connection => dashboardConnection;
  List<Map<String, dynamic>> tasks = [];
  List<Map<String, dynamic>> releases = [];
  Map<String, dynamic> github = {};
  Map<String, dynamic> storage = {};
  String executor = 'unavailable';
  String section = 'Overview';
  final Set<String> expandedMeetings = {};
  String? localTaskId;
  String? get selectedId => component.taskId ?? localTaskId;
  String? error;
  Map<String, String>? releaseFailure;
  // A failed list read must not erase an action failure, and vice versa.
  String? readError;
  bool busy = false;
  bool configuringPublishing = false;
  late final TaskBackupController backup;
  bool get exportingTasks => backup.busy;
  bool get backupFailed => backup.failed;
  String? get backupMessage => backup.message;
  void backupChanged() {
    if (mounted) setState(() {});
  }

  bool refreshing = false;
  bool connected = true;
  Future<void>? pending;
  // Forced notification reads can overlap; only the newest may publish.
  int readRevision = 0;
  Timer? timer;
  StreamSubscription? subscription;
  int generation = -1;
  String liveActivity = '';
  // Keep the exact request across lost responses. Changing the selection gets
  // a new identity; retrying the same action reconciles the durable request.
  final Map<String, String> requests = {};
  bool get admin => connection.operator.value['role'] == 'admin';

  @override
  void initState() {
    super.initState();
    backup = TaskBackupController(
      connection,
      component.saveTaskBackup ??
          (file) => file.downloadUri != null
              ? downloadTaskBackupLink(file.downloadUri!)
              : downloadBytes(
                  file.bytes,
                  file.filename,
                  contentType: file.contentType,
                ),
    );
    backup.addListener(backupChanged);
    generation = connection.generation;
    subscription = connection.stream.listen((_) {
      if (!mounted) return;
      if (connection.state.connected != connected)
        setState(() => connected = connection.state.connected);
      // A session can change task/run while remaining Working. Rebuild for
      // both the session signal and its verified task binding.
      final live = jsonEncode([activity, taskExecutions]);
      if (live != liveActivity) setState(() => liveActivity = live);
      if (generation != connection.generation) {
        generation = connection.generation;
        setState(() {
          tasks = [];
          busy = false;
          configuringPublishing = false;
          releases = [];
          github = {};
          storage = {};
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
    connection.notificationRevision.addListener(notificationRefresh);
    refresh();
    timer = Timer.periodic(const Duration(seconds: 15), (_) => refresh());
  }

  @override
  void didUpdateComponent(covariant TasksPage oldWidget) {
    super.didUpdateComponent(oldWidget);
    if (oldWidget.taskId != component.taskId) {
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

  void notificationRefresh() {
    if (mounted) refresh(force: true);
  }

  @override
  void dispose() {
    readRevision++;
    backup.removeListener(backupChanged);
    backup.dispose();
    connection.notificationRevision.removeListener(notificationRefresh);
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
    final read = _read(epoch, ++readRevision);
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

  Future<void> _read(int epoch, int revision) async {
    final id = selectedId;
    bool current() =>
        mounted &&
        revision == readRevision &&
        epoch == connection.generation &&
        id == selectedId;
    try {
      final data = await connection.request('tasks');
      if (!current()) return;
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
      if (current()) {
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
          storage = Map<String, dynamic>.from(data['storage'] ?? {});
          releases = List<Map<String, dynamic>>.from(data['releases'] ?? []);
          executor = '${data['executor'] ?? 'unavailable'}';
        });
      }
    } catch (e) {
      if (current()) setState(() => readError = '$e');
    }
  }

  Future<void> reviewTaskEnvironment(Map<String, dynamic> task) async {
    if (busy || !admin) return;
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final review = await reviewValidationEnvironment(
        connection.request,
        '${task['id']}',
        'task-${requestId()}',
      );
      if (!mounted || epoch != connection.generation) return;
      setState(() => busy = false);
      final approved = await showDialog<bool>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: const Text('Review validation environment'),
          content: SingleChildScrollView(
            child: SelectableText(validationEnvironmentReviewText(review)),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: const Text('Approve for this candidate'),
            ),
          ],
        ),
      );
      if (approved == true && mounted && epoch == connection.generation) {
        await act(
          'validation_environment',
          validationEnvironmentApproval(review),
        );
      }
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> act(String action, Map<String, dynamic> body) async {
    if (busy || !admin) return;
    final epoch = connection.generation;
    final selection = '$action:$body';
    final request = requests.putIfAbsent(
      selection,
      () => 'task-${requestId()}',
    );
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await connection.request('tasks/$action', {
        ...body,
        'request_id': request,
      });
      if (!mounted || epoch != connection.generation) return;
      if (result is Map && result['action_message'] is String) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(result['action_message'] as String)),
        );
      }
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

  Future<void> releaseAct(String action, Map<String, dynamic> body) async {
    if (busy || !admin) return;
    final epoch = connection.generation;
    final selection = 'release:$action:$body';
    final request = requests.putIfAbsent(
      selection,
      () => 'release-${requestId()}',
    );
    setState(() {
      busy = true;
      error = null;
      releaseFailure = null;
    });
    try {
      final result = await connection.request('releases/$action', {
        ...body,
        'request_id': request,
      });
      if (!mounted || epoch != connection.generation) return;
      requests.remove(selection);
      if (pending != null) await pending;
      if (mounted && epoch == connection.generation) await refresh();
      if (mounted && epoch == connection.generation && action == 'single') {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              'Release candidate ${result['version']} is collecting this task. Freeze and publish it from the workflow board.',
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted && epoch == connection.generation) {
        final message = '$e'.replaceFirst(RegExp(r'^Exception: '), '');
        setState(() {
          error = message;
          releaseFailure = {
            'id': '${body['id']}',
            'action': action,
            'message': message,
          };
        });
        await refresh();
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<Map<String, dynamic>> loadReleaseHistory(
    String id,
    String? revision,
    int? before,
  ) async {
    final epoch = connection.generation;
    final query = Uri(
      queryParameters: {
        'id': id,
        if (revision != null) 'revision': revision,
        if (before != null) 'before': '$before',
      },
    ).query;
    final result = await connection.request('releases/revisions?$query');
    if (epoch != connection.generation)
      throw StateError('Connection changed; reopen release history.');
    return Map<String, dynamic>.from(result);
  }

  Future<void> exportTasks() =>
      backup.export(blocked: busy || configuringPublishing);

  Future<void> exportJson() =>
      backup.export(blocked: busy || configuringPublishing, asJson: true);

  Future<void> configure() async {
    if (!admin) {
      setState(
        () => error = 'Only an administrator can configure GitHub publishing.',
      );
      return;
    }
    if (busy ||
        configuringPublishing ||
        exportingTasks ||
        !connection.state.connected)
      return;
    final token = TextEditingController();
    final prToken = TextEditingController();
    var clearPrCredential = false;
    final repos = TextEditingController(
      text: (github['repositories'] as List? ?? []).join('\n'),
    );
    final configured = (github['publication'] as Map?) ?? const {};
    final modes = <String, String>{};
    final forks = <String, TextEditingController>{};
    for (final entry in configured.entries) {
      final value = entry.value is Map ? entry.value as Map : const {};
      modes['${entry.key}'] = '${value['mode'] ?? 'direct'}';
      if (value['mode'] == 'fork') {
        forks['${entry.key}'] = TextEditingController(
          text: '${value['publishing_repository'] ?? ''}',
        );
      }
    }
    List<String> listed() => repos.text
        .split('\n')
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty)
        .toList();
    TextEditingController forkController(String name) {
      final controller = forks.putIfAbsent(name, () => TextEditingController());
      final requiredFork = protectedPublishingPolicy(github, name)?['fork'];
      if (requiredFork is String && controller.text != requiredFork)
        controller.text = requiredFork;
      return controller;
    }

    final epoch = connection.generation;
    try {
      setState(() {
        busy = true;
        configuringPublishing = true;
      });
      var sources = <Map<String, String>>[];
      String? sourceError;
      try {
        sources = await publishingTokenConnections(connection);
      } catch (_) {
        sourceError = publishingSourcesUnavailable;
      }
      if (!mounted ||
          epoch != connection.generation ||
          !admin ||
          !connection.state.connected)
        return;
      // Waiting for operator input is not an in-flight request. Keep the
      // separate guard active to prevent opening duplicate setup dialogs.
      setState(() => busy = false);
      final selection = PublishingTokenSelection(sources);
      final setupRoute = DialogRoute<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, setDialogState) => AlertDialog(
            title: const Text('GitHub publishing'),
            content: SizedBox(
              width: 540,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(publishingPushTokenNotice),
                    DropdownButtonFormField<String>(
                      key: const ValueKey('publishing-token-source'),
                      initialValue: selection.selectedId,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: publishingTokenSourceLabel,
                      ),
                      items: [
                        for (final option in selection.options.entries)
                          DropdownMenuItem(
                            value: option.key,
                            child: Text(option.value),
                          ),
                      ],
                      onChanged: (value) => setDialogState(() {
                        repos.text = selection.select(value, repos.text);
                        token.clear();
                      }),
                    ),
                    if (sourceError != null) Text(sourceError),
                    if (!selection.manual)
                      const Text(savedPublishingTokenNotice),
                    if (selection.manual)
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
                    const Text(publishingPrTokenNotice),
                    if (github['pr_configured'] == true)
                      Text(
                        'Separate PR credential configured for ${github['pr_login'] ?? 'a verified account'}.',
                      ),
                    TextField(
                      controller: prToken,
                      obscureText: true,
                      enableSuggestions: false,
                      autocorrect: false,
                      decoration: InputDecoration(
                        labelText:
                            listed().any(
                              (name) =>
                                  protectedPublishingPolicy(github, name) !=
                                  null,
                            )
                            ? 'Upstream fine-grained PAT (required)'
                            : 'Pull request token (optional)',
                      ),
                      maxLength: 255,
                    ),
                    if (github['pr_configured'] == true)
                      CheckboxListTile(
                        value: clearPrCredential,
                        onChanged:
                            listed().any(
                              (name) =>
                                  protectedPublishingPolicy(github, name) !=
                                  null,
                            )
                            ? null
                            : (value) => setDialogState(
                                () => clearPrCredential = value == true,
                              ),
                        title: const Text(
                          'Remove separate PR credential and use the push token',
                        ),
                      ),
                    TextField(
                      controller: repos,
                      minLines: 2,
                      maxLines: 5,
                      onChanged: (_) => setDialogState(() {}),
                      decoration: const InputDecoration(
                        labelText: 'Allowed owner/repository (one per line)',
                      ),
                    ),
                    const SizedBox(height: 12),
                    const Text(publishingModeNotice),
                    for (final name in listed()) ...[
                      Row(
                        children: [
                          Expanded(child: Text(name)),
                          DropdownButton<String>(
                            value:
                                protectedPublishingPolicy(github, name) != null
                                ? 'fork'
                                : modes[name] ?? 'direct',
                            items: [
                              if (protectedPublishingPolicy(github, name) ==
                                  null)
                                const DropdownMenuItem(
                                  value: 'direct',
                                  child: Text('Direct'),
                                ),
                              DropdownMenuItem(
                                value: 'fork',
                                child: Text('Through a fork'),
                              ),
                            ],
                            onChanged: (value) => setDialogState(() {
                              modes[name] = value ?? 'direct';
                              if (modes[name] == 'fork') {
                                forks.putIfAbsent(
                                  name,
                                  () => TextEditingController(),
                                );
                              }
                            }),
                          ),
                        ],
                      ),
                      if (modes[name] == 'fork' ||
                          protectedPublishingPolicy(github, name) != null)
                        TextField(
                          readOnly:
                              protectedPublishingPolicy(github, name) != null,
                          controller: forkController(name),
                          decoration: InputDecoration(
                            labelText: 'Fork for $name (owner/repository)',
                          ),
                        ),
                    ],
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
        ),
      );
      final confirmed = await Navigator.of(context).push(setupRoute);
      // Keep controllers alive until the dialog has left the overlay.
      await setupRoute.completed;
      if (confirmed != true ||
          !mounted ||
          epoch != connection.generation ||
          !admin ||
          !connection.state.connected)
        return;
      setState(() {
        busy = true;
        error = null;
      });
      final publication = <String, dynamic>{};
      for (final name in listed()) {
        publication[name] =
            modes[name] == 'fork' ||
                protectedPublishingPolicy(github, name) != null
            ? {
                'mode': 'fork',
                'publishing_repository':
                    '${protectedPublishingPolicy(github, name)?['fork'] ?? forks[name]?.text.trim() ?? ''}',
              }
            : {'mode': 'direct'};
      }
      await connection.request('github/configure', {
        if (selection.manual) 'token': token.text.trim(),
        if (!selection.manual) 'connection_id': selection.selectedId,
        'repositories': listed(),
        'publication': publication,
        if (prToken.text.trim().isNotEmpty) 'pr_token': prToken.text.trim(),
        if (clearPrCredential) 'clear_pr_credential': true,
      });
      await refresh();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      token.clear();
      token.dispose();
      prToken.clear();
      prToken.dispose();
      repos.dispose();
      for (final controller in forks.values) {
        controller.dispose();
      }
      if (mounted && epoch == connection.generation)
        setState(() {
          busy = false;
          configuringPublishing = false;
        });
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
      final base = TextEditingController();
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
                          labelText: 'Base branch override (optional)',
                          helperText:
                              'Leave empty to use the repository base or origin default.',
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

  Future<void> previewPull(Map<String, dynamic> task) async {
    if (needsPrAuthentication(github, task['github_repository'] as String?)) {
      await configure();
      return;
    }
    final epoch = connection.generation;
    // Created up front so the dialog builder captures non-nullable controllers.
    final title = TextEditingController();
    final description = TextEditingController();
    try {
      final fresh = Map<String, dynamic>.from(
        await connection.request('tasks/detail?id=${task['id']}'),
      );
      if (!mounted || epoch != connection.generation) return;
      final preview = fresh['pull_preview'] as Map?;
      if (preview == null ||
          preview['head_sha'] != (fresh['publish'] as Map?)?['head_sha']) {
        throw StateError(
          'Publish the current candidate before previewing its PR. Update the gateway if PR previews are unavailable.',
        );
      }
      title.text = '${preview['title']}';
      description.text = '${preview['body']}';
      final excerpt = '${preview['excerpt'] ?? ''}';
      var includeExcerpt = false;
      final form = FormKey();
      final route = DialogRoute<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, setDialogState) => AlertDialog(
            title: const Text('Preview draft pull request'),
            content: SizedBox(
              width: 760,
              child: SingleChildScrollView(
                child: Form(
                  key: form,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      SelectableText(
                        'Publish branch to: ${preview['publishing_repository']}\n\n'
                        'Open draft PR into: ${preview['repository']}\n\n'
                        'Source branch: ${preview['head']}\n\n'
                        'Target branch: ${preview['base']}\n\n'
                        'Exact candidate: ${preview['head_sha']}',
                      ),
                      Text(
                        manualPrRequired(github, '${task['github_repository']}')
                            ? 'Copy this title and description into your draft PR on GitHub. Checking for adoption preserves the PR’s existing text. The branch is already published; merging and deployment remain separate.'
                            : 'This title and Markdown description will be public on the selected repository. The branch is already published. Creating a draft PR does not merge or deploy it.',
                      ),
                      TextFormField(
                        controller: title,
                        maxLength: 256,
                        decoration: const InputDecoration(
                          labelText: 'PR title',
                        ),
                        validator: (value) =>
                            value == null || value.trim().isEmpty
                            ? 'Enter a title.'
                            : null,
                      ),
                      TextFormField(
                        controller: description,
                        minLines: 10,
                        maxLines: 20,
                        maxLength: 30000,
                        buildCounter:
                            (
                              context, {
                              required currentLength,
                              required isFocused,
                              maxLength,
                            }) => null,
                        decoration: const InputDecoration(
                          labelText: 'PR description (Markdown)',
                        ),
                        validator: (value) =>
                            value == null || value.trim().isEmpty
                            ? 'Enter a description.'
                            : null,
                      ),
                      if (excerpt.isNotEmpty)
                        CheckboxListTile(
                          value: includeExcerpt,
                          onChanged: (value) => setDialogState(() {
                            includeExcerpt = value == true;
                            final block = '\n\n$excerpt';
                            if (includeExcerpt) {
                              if (!description.text.contains(excerpt)) {
                                description.text =
                                    '${description.text.trimRight()}$block';
                              }
                            } else {
                              description.text = description.text
                                  .replaceFirst(block, '')
                                  .trimRight();
                            }
                          }),
                          title: const Text(
                            'Include the finalized discussion excerpt',
                          ),
                          subtitle: const Text(
                            'Off by default: the excerpt publishes the group discussion text verbatim.',
                          ),
                          controlAffinity: ListTileControlAffinity.leading,
                          dense: true,
                        ),
                    ],
                  ),
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () {
                  if (form.currentState!.validate())
                    Navigator.pop(context, true);
                },
                child: Text(
                  manualPrRequired(github, '${task['github_repository']}')
                      ? 'Check for operator-created PR'
                      : 'Create draft PR',
                ),
              ),
            ],
          ),
        ),
      );
      final confirmed = await Navigator.of(context).push(route);
      await route.completed;
      if (confirmed == true && mounted && epoch == connection.generation) {
        await act('pull', {
          'task_id': fresh['id'],
          'head_sha': preview['head_sha'],
          'reviewed': true,
          'title': title.text.trim(),
          'body': description.text,
        });
      }
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$exception');
    } finally {
      title.dispose();
      description.dispose();
    }
  }

  Future<void> choosePublication(Map<String, dynamic> task) async {
    final epoch = connection.generation;
    final upstream = '${task['github_repository'] ?? ''}';
    final configured = (github['publication'] as Map?)?[upstream] as Map?;
    final fork = configured?['mode'] == 'fork'
        ? '${configured?['publishing_repository'] ?? ''}'
        : '';
    if (upstream.isEmpty || fork.isEmpty) {
      setState(
        () => error =
            'Configure fork publication for $upstream before choosing a destination.',
      );
      return;
    }
    final current = '${task['publishing_repository'] ?? upstream}';
    var mode = protectedPublishingPolicy(github, upstream) != null
        ? 'fork'
        : current == upstream
        ? 'direct'
        : 'fork';
    try {
      final route = DialogRoute<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, setDialogState) => AlertDialog(
            title: const Text('Publication destination'),
            content: SizedBox(
              width: 480,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Publish branch to: ${mode == 'fork' ? fork : upstream}',
                  ),
                  Text('Open draft PR into: $upstream'),
                  const Text(
                    'The destination is recorded with this task and cannot change once the branch is published. The upstream repository is never force-pushed.',
                  ),
                  DropdownButton<String>(
                    value: mode,
                    items: [
                      if (protectedPublishingPolicy(github, upstream) == null)
                        DropdownMenuItem(
                          value: 'direct',
                          child: Text('Direct \u00b7 $upstream'),
                        ),
                      DropdownMenuItem(
                        value: 'fork',
                        child: Text('Through a fork \u00b7 $fork'),
                      ),
                    ],
                    onChanged: (value) =>
                        setDialogState(() => mode = value ?? 'direct'),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Record destination'),
              ),
            ],
          ),
        ),
      );
      final confirmed = await Navigator.of(context).push(route);
      await route.completed;
      if (confirmed == true && mounted && epoch == connection.generation) {
        await act('publication', {
          'task_id': task['id'],
          'mode': mode,
          'publishing_repository': fork,
        });
      }
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$exception');
    }
  }

  Component _buildEvidence(String target, Map<dynamic, dynamic> build) {
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
                icon: const Icon('download'),
                label: const Text('Download build log'),
                onPressed: () async {
                  final epoch = connection.generation;
                  try {
                    final id = build['run_id'] ?? build['id'];
                    final bytes = await connection.bytes(
                      'validation/log?id=$id',
                    );
                    if (!mounted || epoch != connection.generation) return;
                    await downloadBytes(
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
              if (component.coordinator != null)
                TextButton(
                  onPressed: () => component.coordinator!.push(BuildsRoute()),
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
  Component build(BuildContext context) {
    if (selectedId != null) return ui.el('div', [_storageNotice(), _detail()]);
    final open = tasks
        .where((t) => !['merged', 'completed', 'closed'].contains(t['state']))
        .length;
    final queued = tasks
        .where((t) => (t['assignment'] as Map?)?['state'] == 'queued')
        .length;
    final githubLine = github['configured'] == true
        ? admin
              ? 'GitHub account: ${github['login']} · ${github['repositories']}'
              : 'GitHub publishing is configured. Publishing requires an administrator.'
        : github['error'] != null
        ? 'GitHub publishing is unavailable: ${github['error']}'
        : 'GitHub publishing is not configured. Local task work is available.';
    return ui.el('div', [
      ui.pageHeader(
        'Tasks',
        subtitle: tasks.isEmpty
            ? null
            : [
                '${tasks.length} ${tasks.length == 1 ? 'task' : 'tasks'}',
                '$open open',
                if (queued > 0) '$queued queued',
              ].join(' · '),
        actions: [
          ui.button(
            'GitHub publishing',
            admin && !busy && !exportingTasks && !configuringPublishing
                ? configure
                : null,
            iconName: 'key-round',
          ),
          ui.button(
            exportingTasks && !backup.jsonExport
                ? 'Exporting tasks…'
                : taskBackupLabel,
            admin &&
                    connection.state.connected &&
                    !busy &&
                    !configuringPublishing &&
                    !exportingTasks
                ? exportTasks
                : null,
            iconName: 'download',
          ),
          ui.button(
            exportingTasks && backup.jsonExport
                ? 'Exporting JSON…'
                : taskJsonLabel,
            admin &&
                    connection.state.connected &&
                    !busy &&
                    !configuringPublishing &&
                    !exportingTasks
                ? exportJson
                : null,
            iconName: 'file-text',
          ),
          ui.button(
            'Refresh tasks',
            busy || refreshing ? null : refresh,
            iconName: 'refresh-cw',
          ),
          ui.button(
            'Create task',
            admin && !busy ? create : null,
            primary: true,
            iconName: 'plus',
          ),
        ],
      ),
      _storageNotice(),
      ui.el('div', [
        ui.el('p', [
          ui.text(
            'Develop in a task branch, review the exact commit, publish and open a draft pull request. Merge on GitHub. Updating the base and deploying are separate operations.',
          ),
        ], classes: 'task-page__intro'),
        ui.el(
          'p',
          [
            ui.icon(
              github['configured'] == true
                  ? 'circle-check'
                  : github['error'] != null
                  ? 'triangle-alert'
                  : 'info',
            ),
            ui.text(githubLine),
          ],
          classes: 'task-page__status',
          attributes: {
            'data-tone': github['configured'] == true
                ? 'success'
                : github['error'] != null
                ? 'warning'
                : 'info',
          },
        ),
        if (admin && publishingPolicyNotice(github) != null)
          ui.notice(publishingPolicyNotice(github)!),
        if (admin && github['publication'] is Map)
          for (final entry in (github['publication'] as Map).entries)
            if (entry.value is Map && entry.value['mode'] == 'fork')
              ui.el('p', [
                ui.icon('git-fork'),
                ui.text(publicationSummary('${entry.key}', entry.value as Map)),
              ], classes: 'task-page__status task-page__status--pre'),
      ], classes: 'task-page__meta'),
      if (backupMessage != null)
        ui.el('div', [
          ui.notice(backupMessage, error: backupFailed),
          ui.button(
            'Dismiss backup message',
            backup.dismiss,
            small: true,
            variant: 'ghost',
          ),
        ]),
      if (busy) const LinearProgressIndicator(),
      if ((error ?? readError) != null)
        ui.notice(error ?? readError, error: true),
      if (tasks.isEmpty)
        ui.el('p', [
          ui.text(
            !connected
                ? 'Not connected to the gateway.'
                : readError != null
                ? 'Task list unavailable.'
                : refreshing
                ? 'Loading tasks…'
                : 'No development tasks yet.',
          ),
        ], classes: 'task-page__empty'),
      TaskWorkflow(
        tasks: tasks,
        releases: releases,
        onReleaseAction: admin ? releaseAct : null,
        releaseBusy: busy,
        releaseFailure: releaseFailure,
        onLoadReleaseHistory: loadReleaseHistory,
        initialView: component.initialView,
        groups: [
          for (final group
              in connection.organizationDirectory.value['groups'] as List? ??
                  [])
            if (group is Map<String, dynamic>) group,
        ],
        profiles: [
          for (final profile
              in connection.organizationDirectory.value['profiles'] as List? ??
                  [])
            if (profile is Map<String, dynamic>) profile,
        ],
        activity: activity,
        taskExecutions: taskExecutions,
        onOpenTask: openTask,
        executor: executor,
        loading: refreshing,
        listView: taskList(
          tasks: tasks,
          stateLabel: taskState,
          onOpen: openTask,
          executor: executor,
          taskExecutions: taskExecutions,
        ),
      ),
      DiscoveryPanel(coordinator: component.coordinator),
    ], classes: 'task-page task-index');
  }

  Component _storageNotice() => storage['blocked'] == true
      ? ui.notice('Storage needs attention: ${storage['reason']}', error: true)
      : Component.fragment([]);

  /// Live agent activity by profile id, from the dashboard snapshot.
  Map<String, String> get activity => {
    for (final agent in connection.state.agents)
      if (agent['profile_id'] != null)
        '${agent['profile_id']}': activityFor(agent),
  };

  Map<String, Map<String, dynamic>> get taskExecutions => taskExecutionBindings(
    connection.state.agents,
    available:
        connection.state.connected &&
        connection.state.error == null &&
        connection.state.serverStatus != 'stopped',
  );

  Future<void> openTask(String id) async {
    if (component.coordinator != null) {
      component.coordinator!.push(TaskDetailRoute(id));
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
    if (component.coordinator != null) {
      component.coordinator!.replace(TasksRoute());
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

  Future<void> moveAssignment(Map<String, dynamic> task) async {
    final epoch = connection.generation;
    var position = (task['assignment'] as Map?)?['position'] as int? ?? 1;
    final chosen = await showDialog<int>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Change queue order'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Lower order numbers run first. This does not interrupt active work.',
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  IconButton(
                    tooltip: 'Earlier',
                    onPressed: position > 0
                        ? () => update(() => position--)
                        : null,
                    icon: const Icon('remove'),
                  ),
                  Text('$position'),
                  IconButton(
                    tooltip: 'Later',
                    onPressed: position < 10000
                        ? () => update(() => position++)
                        : null,
                    icon: const Icon('add'),
                  ),
                ],
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, position),
              child: const Text('Save queue order'),
            ),
          ],
        ),
      ),
    );
    if (chosen != null && mounted && epoch == connection.generation)
      await act('assignment', {
        'task_id': task['id'],
        'mode': 'move',
        'position': chosen,
      });
  }

  Component _outcomeNotice(Map notice) {
    final state = '${notice['state'] ?? ''}';
    if (state == 'scheduled')
      return Text(
        'Outcome review scheduled for: ${notice['group_name'] ?? 'group'} \u00b7 '
        'outcome ${notice['outcome'] ?? ''} \u00b7 meeting ${notice['job_id'] ?? ''}.',
      );
    if (state == 'off')
      return const Text(
        'Outcome reviews are disabled for the proposing group.',
      );
    if (state == 'unavailable' || state == 'error')
      return Text('Outcome notice: ${notice['reason'] ?? state}');
    return const SizedBox.shrink();
  }

  String _acceptanceText(Map acceptance) {
    final state = '${acceptance['state'] ?? ''}';
    final reviewer = '${acceptance['reviewer_name'] ?? 'reviewer'}';
    final reason = '${acceptance['reason'] ?? ''}';
    return switch (state) {
      'satisfied' => 'Acceptance: satisfied by $reviewer. $reason',
      'not_satisfied' => 'Acceptance: not satisfied by $reviewer. $reason',
      'uncertain' ||
      'inconclusive' ||
      'attention' => 'Acceptance: $state \u00b7 $reason',
      'requested' => 'Acceptance review requested from $reviewer.',
      'waiting' =>
        'Acceptance review waiting: ${reason.isEmpty ? 'no reason recorded' : reason}',
      'off' => 'Acceptance review is off.',
      _ => 'Acceptance: $state',
    };
  }

  String _automationOutcome(Map result) {
    final unavailable = '${result['unavailable'] ?? ''}';
    if (unavailable.isNotEmpty) return unavailable;
    final queued = (result['queued'] as List? ?? []).length;
    final drafts = (result['drafts'] as List? ?? []).length;
    // Report every distinct reason, not a sample: a meeting that skipped five
    // proposals for three causes must not look like one that skipped two.
    final reasons = ((result['skipped'] as Map? ?? const {}).values)
        .map((value) => '$value')
        .toSet()
        .join('; ');
    if (queued == 0 && drafts == 0 && reasons.isEmpty) {
      return 'Automation evaluated this review: no proposals.';
    }
    final made = [
      if (queued > 0) '$queued created and queued',
      if (drafts > 0) '$drafts created as drafts',
    ].join(', ');
    // Every proposal was rejected before creation; without a prefix this
    // would render as a dangling separator followed by the reasons.
    if (made.isEmpty) return 'Automation: nothing created · $reasons';
    final deferred = ((result['deferred'] as Map? ?? const {}).values)
        .map((value) => '$value')
        .toSet()
        .join('; ');
    return 'Automation: $made${deferred.isEmpty ? '' : ' · kept as drafts: $deferred'}${reasons.isEmpty ? '' : ' · not created: $reasons'}';
  }

  Future<void> sendKey(Map<String, dynamic> task, String key) async {
    if (busy || !admin) return;
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await connection.request('organizations/input', {
        'organization_id': task['organization_id'],
        'profile_id': (task['availability'] as Map?)?['profile_id'],
        'key': key,
      });
      if (!mounted || epoch != connection.generation) return;
      if (pending != null) await pending;
      if (mounted && epoch == connection.generation) await refresh();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Component _decision(Map<String, dynamic> task) {
    final availability = task['availability'] as Map?;
    final preview = '${availability?['preview'] ?? ''}';
    return Card(
      color: Theme.of(context).colorScheme.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${taskAgentName(task, fallback: 'The agent')} needs your decision',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (preview.isNotEmpty)
              SelectableText(preview)
            else
              const Text('The agent is waiting at a prompt.'),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton(
                  onPressed: admin && !busy
                      ? () => sendKey(task, 'enter')
                      : null,
                  child: const Text('Send Enter'),
                ),
                OutlinedButton(
                  onPressed: admin && !busy ? () => sendKey(task, 'esc') : null,
                  child: const Text('Send Escape'),
                ),
                OutlinedButton(
                  onPressed: admin && !busy
                      ? () => sendKey(task, 'shift+tab')
                      : null,
                  child: const Text('Cycle permission mode'),
                ),
                if (component.coordinator != null)
                  TextButton(
                    onPressed: () => component.coordinator!.navigate(
                      OrganizationRoute('runs'),
                    ),
                    child: const Text('Inspect session'),
                  ),
              ],
            ),
            const Text(
              'Keys are sent only to the inspected session and recorded with your operator name. Enter does not always mean approval.',
            ),
          ],
        ),
      ),
    );
  }

  Future<List<Map<String, dynamic>>> _agents(Map task) async {
    final directory = await connection.request('organizations/directory');
    final profiles = List<Map<String, dynamic>>.from(
      directory['profiles'] ?? [],
    );
    final repository = '${task['repository']}'.replaceAll('\\', '/');
    return profiles
        .where(
          (p) =>
              p['group_id'] == null &&
              '${p['project']}'.replaceAll('\\', '/').endsWith('/$repository'),
        )
        .toList();
  }

  Map? _archivedRun(Map task) {
    for (final session in task['sessions'] as List? ?? []) {
      if (session is Map &&
          session['id'] == task['run_id'] &&
          ['finished', 'released'].contains(session['state']) &&
          session['session_closed_at'] != null)
        return session;
    }
    return null;
  }

  Future<void> handoverTask(Map<String, dynamic> task) async {
    try {
      final agents = await _agents(task);
      final current = '${task['profile_id']}';
      final choices = agents.where((p) => '${p['id']}' != current).toList();
      if (!mounted) return;
      if (choices.isEmpty) {
        setState(() => error = 'No other agent works in this repository.');
        return;
      }
      String? target = '${choices.first['id']}';
      final chosen = await showDialog<String>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
            title: const Text('Hand over this task'),
            content: SizedBox(
              width: 480,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'The current session is archived. The next agent receives the task, the recorded evidence and any handover note, and continues in the same checkout.',
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: target,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Next agent'),
                    items: [
                      for (final p in choices)
                        DropdownMenuItem(
                          value: '${p['id']}',
                          child: Text('${p['name']}'),
                        ),
                    ],
                    onChanged: (v) => update(() => target = v),
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
                onPressed: () => Navigator.pop(context, target),
                child: const Text('Hand over and start'),
              ),
            ],
          ),
        ),
      );
      if (chosen != null && mounted)
        await act('handover', {
          'task_id': task['id'],
          'target_profile_id': chosen,
        });
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> consultTask(Map<String, dynamic> task) async {
    final epoch = connection.generation;
    try {
      final agents = await _agents(task);
      if (!mounted || epoch != connection.generation) return;
      if (agents.isEmpty) {
        setState(() => error = 'No agent works in this repository.');
        return;
      }
      String? consultant = '${agents.first['id']}';
      final question = TextEditingController();
      Future<dynamic>? dialogClosed;
      final chosen = await showDialog<({String profileId, String question})>(
        context: context,
        builder: (context) {
          dialogClosed = ModalRoute.of(context)?.completed;
          return StatefulBuilder(
            builder: (context, update) => AlertDialog(
              title: const Text('Ask a consultant'),
              content: SizedBox(
                width: 480,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'The answer is recorded as reported knowledge on this task. It is not validation evidence.',
                    ),
                    DropdownButtonFormField<String>(
                      initialValue: consultant,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Consultant',
                      ),
                      items: [
                        for (final p in agents)
                          DropdownMenuItem(
                            value: '${p['id']}',
                            child: Text('${p['name']}'),
                          ),
                      ],
                      onChanged: (v) => update(() => consultant = v),
                    ),
                    TextField(
                      controller: question,
                      maxLength: 2000,
                      minLines: 3,
                      maxLines: 6,
                      decoration: const InputDecoration(labelText: 'Question'),
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
                  onPressed: () => Navigator.pop(
                    context,
                    question.text.trim().isEmpty || consultant == null
                        ? null
                        : (
                            profileId: consultant!,
                            question: question.text.trim(),
                          ),
                  ),
                  child: const Text('Ask'),
                ),
              ],
            ),
          );
        },
      );
      await dialogClosed;
      question.dispose();
      if (chosen != null && mounted && epoch == connection.generation) {
        await act('consult', {
          'task_id': task['id'],
          'consultant_profile_id': chosen.profileId,
          'question': chosen.question,
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> acceptanceTask(Map<String, dynamic> task) async {
    try {
      final agents = await _agents(task);
      final current = '${task['profile_id']}';
      final template = '${(task['execution'] as Map?)?['template_id'] ?? ''}';
      final choices = agents
          .where((p) => '${p['id']}' != current && '${p['id']}' != template)
          .toList();
      if (!mounted) return;
      if (choices.isEmpty) {
        setState(() => error = 'No other agent works in this repository.');
        return;
      }
      final existing = task['acceptance'] as Map?;
      var reviewer =
          '${existing?['reviewer_profile_id'] ?? choices.first['id']}';
      if (!choices.any((p) => '${p['id']}' == reviewer))
        reviewer = '${choices.first['id']}';
      var automatic = existing?['auto'] == true;
      final chosen = await showDialog<List<String>>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
            title: const Text('Acceptance review'),
            content: SizedBox(
              width: 480,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'An independent agent checks the acceptance criteria against the recorded candidate and verified checks, then saves a JSON verdict. The verdict is reported knowledge, not validation evidence.',
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: reviewer,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Reviewer'),
                    items: [
                      for (final p in choices)
                        DropdownMenuItem(
                          value: '${p['id']}',
                          child: Text('${p['name']}'),
                        ),
                    ],
                    onChanged: (v) => update(() => reviewer = v ?? reviewer),
                  ),
                  SwitchListTile(
                    value: automatic,
                    onChanged: (v) => update(() => automatic = v),
                    title: const Text(
                      'Review every verified candidate automatically',
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
                onPressed: () => Navigator.pop(context, [
                  reviewer,
                  automatic ? 'auto' : 'manual',
                ]),
                child: const Text('Save'),
              ),
            ],
          ),
        ),
      );
      if (chosen != null && mounted) {
        await act('acceptance', {
          'task_id': task['id'],
          'reviewer_profile_id': chosen.first,
          'auto': chosen.last == 'auto',
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  String _blockingSessionLine(Map blocking, [Map? availability]) {
    final run = '${blocking['run_id'] ?? ''}';
    final short = run.isEmpty
        ? 'unknown run'
        : run.substring(0, run.length.clamp(0, 8));
    final state = '${blocking['state'] ?? 'unknown'}';
    final created = '${blocking['created_at'] ?? ''}';
    final taskless = blocking['task_id'] == null;
    final availabilityState = '${availability?['state'] ?? ''}';
    final tail = !taskless
        ? ''
        : availabilityState == 'ready'
        ? ' \u00b7 it will be archived automatically to start this task.'
        : availabilityState == 'occupied' || availabilityState == 'starting'
        ? ' \u00b7 the task starts when it finishes.'
        : ' \u00b7 inspect it in Org chart \u2192 Runs for session details.';
    return 'Session $short \u00b7 $state'
        '${created.isEmpty ? '' : ' \u00b7 started $created'}'
        '$tail';
  }

  Future<void> archiveGeneralSession(Map task, String runId) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Archive inspected general session?'),
        content: const Text(
          'Inspect the session in Organization → Runs first. This preserves bounded terminal history and closes this exact session. Its files remain intact. The task starts in a separate Git worktree.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('I inspected it; archive and start'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted)
      await act('launch', {
        'task_id': task['id'],
        'inspected_general_session': runId,
      });
  }

  Component _groupReviewStatus(Map<String, dynamic> task) {
    // Meetings carry no outcome flag, so the latest review is the one to show.
    // Whether its proposals already became tasks is reported below instead of
    // being used to hide the review and its acceptance state.
    final meetings = (task['meeting_results'] as List? ?? [])
        .whereType<Map>()
        .toList();
    if (meetings.isEmpty) return const SizedBox.shrink();
    final meeting = meetings.last;
    final ready = meeting['state'] == 'artifact_ready';
    final candidate = meeting['candidate'];
    final current = candidate != null && candidate == task['head_sha'];
    final proposals = (meeting['proposals'] as List? ?? [])
        .whereType<Map>()
        .toList();
    final created = (task['follow_up_tasks'] as Map?) ?? const {};
    final converted = proposals
        .where((proposal) => created.containsKey(proposal['key']))
        .length;
    final result = '${meeting['result'] ?? ''}'.trim();
    final acceptance = task['acceptance'] as Map?;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Group review · ${meeting['group_name'] ?? 'Group'}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text(
              ready
                  ? 'Discussion finalized · ${proposals.length} follow-up proposals'
                        '${converted > 0 ? ' ($converted already created as tasks)' : ''}. '
                        'This is not an approval verdict.'
                  : 'Discussion state: ${meeting['state'] ?? 'unavailable'}',
            ),
            if (candidate == null)
              const Text(
                'This older review has no recorded candidate. Compare its findings with the latest changes.',
              )
            else if (!current)
              const Text(
                'This review concerns an earlier candidate. Review the latest changes before completing the task.',
              ),
            if (acceptance != null)
              Text(
                'Acceptance review: ${acceptance['state'] ?? 'not recorded'} · ${acceptance['reason'] ?? ''}',
              ),
            if (result.isNotEmpty)
              SelectableText(
                result.length > 1200 ? '${result.substring(0, 1200)}…' : result,
              ),
            if (ready)
              const Text(
                'Before marking done, check whether each follow-up fixes an unmet acceptance criterion or is an independent improvement. A draft alone does not record that decision.',
              ),
            const Text(
              'Follow-up drafts do not reserve agents. The original execution is handed off only after its completion evidence and session are verified.',
            ),
            TextButton(
              onPressed: () => setState(() {
                section = 'Activity & agents';
                expandedMeetings.add('${meeting['job_id']}');
              }),
              child: const Text('Read full group review'),
            ),
          ],
        ),
      ),
    );
  }

  Component _nextAction(Map<String, dynamic> task) {
    final repair = task['pr_repair'] as Map?;
    if (!['merged', 'closed', 'completed'].contains(task['state']) &&
        (taskHasPrConflicts(task) ||
            repair != null &&
                ![
                  'cancelled',
                  'obsolete',
                  'published',
                ].contains(repair['state']))) {
      return OutlinedButton.icon(
        onPressed: () => setState(() => section = 'Activity & agents'),
        icon: const Icon('build_outlined'),
        label: Text(
          repair?['kind'] == 'validation_update'
              ? 'View branch update'
              : 'View conflict repair',
        ),
      );
    }
    final target = taskValidationTarget(task);
    final evidence = (task['builds'] as Map?)?[target] as Map?;
    if (task['launch_retryable'] == true) {
      return FilledButton.icon(
        onPressed: admin && !busy
            ? () => act('launch', {'task_id': task['id']})
            : null,
        icon: const Icon('refresh'),
        label: const Text('Retry agent launch'),
      );
    }
    if (task['state'] == 'draft') {
      final assignment = task['assignment'] as Map?;
      final blocking = task['blocking_execution'] as Map?;
      final availability = task['availability'] as Map?;
      if (assignment?['state'] == 'queued') {
        final waitingTitle =
            blocking == null || availability?['state'] == 'ready'
            ? null
            : '${blocking['task_title'] ?? ''}'.isNotEmpty
            ? '${blocking['task_title']}'
            : "${taskAgentName(task, fallback: 'The assigned agent')}'s active session";
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            Chip(
              avatar: const Icon('schedule', size: 18),
              label: Text(
                'Queued for this agent \u00b7 order ${assignment?['position']}',
              ),
            ),
            if (waitingTitle != null)
              Chip(
                avatar: const Icon('hourglass_empty', size: 18),
                label: Text('Waiting on: $waitingTitle'),
              ),
            if ('${availability?['detail'] ?? ''}'.isNotEmpty)
              Text('${availability?['detail']}'),
            if (blocking != null)
              Text(_blockingSessionLine(blocking, availability)),
            if (availability?['action'] == 'archive_general')
              OutlinedButton.icon(
                onPressed: admin && !busy
                    ? () => archiveGeneralSession(
                        task,
                        '${availability?['run_id']}',
                      )
                    : null,
                icon: const Icon('archive_outlined'),
                label: const Text('Archive inspected session and start'),
              ),
            OutlinedButton.icon(
              onPressed: admin && !busy ? () => moveAssignment(task) : null,
              icon: const Icon('reorder'),
              label: const Text('Change queue order'),
            ),
            OutlinedButton.icon(
              onPressed: admin && !busy
                  ? () => act('assignment', {
                      'task_id': task['id'],
                      'mode': 'cancel',
                    })
                  : null,
              icon: const Icon('cancel_outlined'),
              label: const Text('Cancel queued assignment'),
            ),
          ],
        );
      }
      if (blocking == null) {
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: admin && !busy
                  ? () => act('launch', {'task_id': task['id']})
                  : null,
              icon: const Icon('play_arrow'),
              label: const Text('Start implementation'),
            ),
            OutlinedButton.icon(
              onPressed: admin && !busy
                  ? () => act('assignment', {
                      'task_id': task['id'],
                      'mode': 'queue',
                    })
                  : null,
              icon: const Icon('schedule'),
              label: const Text('Queue for this agent'),
            ),
            OutlinedButton.icon(
              onPressed: admin && !busy
                  ? () => act('launch', {
                      'task_id': task['id'],
                      'as_instance': true,
                    })
                  : null,
              icon: const Icon('copy_all_outlined'),
              label: Text('Instance of ${taskAgentName(task)}'),
            ),
          ],
        );
      }
      if (availability?['action'] == 'archive_general') {
        return OutlinedButton.icon(
          onPressed: admin && !busy
              ? () => archiveGeneralSession(task, '${availability?['run_id']}')
              : null,
          icon: const Icon('archive_outlined'),
          label: const Text('Archive inspected session and start'),
        );
      }
      if (availability?['state'] == 'ready') {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: admin && !busy
                      ? () => act('launch', {'task_id': task['id']})
                      : null,
                  icon: const Icon('play_arrow'),
                  label: const Text('Start implementation'),
                ),
                OutlinedButton.icon(
                  onPressed: admin && !busy
                      ? () => act('assignment', {
                          'task_id': task['id'],
                          'mode': 'queue',
                        })
                      : null,
                  icon: const Icon('schedule'),
                  label: const Text('Queue for this agent'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              '${taskAgentName(task, fallback: 'The agent')} is ready. Starting this task archives the startup conversation and opens a dedicated task execution.',
            ),
          ],
        );
      }
      final blockingTaskId = blocking['task_id'];
      final title = '${blocking['task_title'] ?? ''}'.isNotEmpty
          ? '${blocking['task_title']}'
          : "${taskAgentName(task, fallback: 'The assigned agent')}'s active session";
      if (blocking['handoff_ready'] == true) {
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: admin && !busy
                  ? () => act('launch', {'task_id': task['id']})
                  : null,
              icon: const Icon('swap_horiz'),
              label: const Text('Verify handoff and start'),
            ),
            if (blockingTaskId != null)
              OutlinedButton.icon(
                onPressed: () => openTask('$blockingTaskId'),
                icon: const Icon('history'),
                label: const Text('Inspect previous task'),
              ),
            OutlinedButton.icon(
              onPressed: admin && !busy
                  ? () => act('assignment', {
                      'task_id': task['id'],
                      'mode': 'queue',
                    })
                  : null,
              icon: const Icon('schedule'),
              label: const Text('Queue instead'),
            ),
          ],
        );
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                onPressed: blockingTaskId != null
                    ? () => openTask('$blockingTaskId')
                    : component.coordinator == null
                    ? null
                    : () => component.coordinator!.navigate(
                        OrganizationRoute('runs'),
                      ),
                icon: const Icon('block'),
                label: Text('Blocked by: $title'),
              ),
              OutlinedButton.icon(
                onPressed: admin && !busy
                    ? () => act('assignment', {
                        'task_id': task['id'],
                        'mode': 'queue',
                      })
                    : null,
                icon: const Icon('schedule'),
                label: const Text('Queue for this agent'),
              ),
            ],
          ),
          if ('${availability?['detail'] ?? ''}'.isNotEmpty)
            Text('${availability?['detail']}'),
          const SizedBox(height: 8),
          Text(_blockingSessionLine(blocking, availability)),
        ],
      );
    }
    if (target == null || ['closed', 'completed'].contains(task['state']))
      return const SizedBox.shrink();
    if (taskValidationNeedsRecheck(task, target)) return _validateAction(task);
    if (task['task_publication_allowed'] != false &&
        task['state'] == 'published' &&
        task['pull'] == null &&
        (task['publish'] as Map?)?['head_sha'] == target) {
      return FilledButton.icon(
        onPressed: admin && !busy && github['configured'] == true
            ? () => previewPull(task)
            : null,
        icon: const Icon('rate_review'),
        label: Text(
          needsPrAuthentication(github, task['github_repository'] as String?)
              ? 'Configure PR authentication'
              : 'Review draft PR',
        ),
      );
    }
    if (evidence?['state'] == 'complete') {
      final verified =
          evidence?['target'] == target &&
          evidence?['required_checks_verified'] == true;
      if (verified && task['task_publication_allowed'] == false) {
        return OutlinedButton(
          onPressed: admin && !busy
              ? () => releaseAct('single', {
                  'task_id': task['id'],
                  'head_sha': task['head_sha'],
                })
              : null,
          child: const Text('Release this task only'),
        );
      }
      if (verified &&
          !['merged', 'published', 'pr_open'].contains(task['state'])) {
        return FilledButton.icon(
          onPressed: admin && !busy && github['configured'] == true
              ? () => review(task)
              : null,
          icon: const Icon('rate_review'),
          label: const Text('Review and publish on GitHub'),
        );
      }
      if (verified) return const SizedBox.shrink();
      // The durable queue will not re-run a completed build; show the evidence
      // instead of offering an action that cannot change anything.
      return OutlinedButton.icon(
        onPressed: () => setState(() => section = 'Checks & builds'),
        icon: const Icon('fact_check_outlined'),
        label: const Text('Inspect validation evidence'),
      );
    }
    if (['queued', 'running'].contains(evidence?['state']))
      return OutlinedButton.icon(
        onPressed: component.coordinator == null
            ? null
            : () => component.coordinator!.push(BuildsRoute()),
        icon: const Icon('hourglass_top'),
        label: const Text('Validation in progress \u00b7 view build'),
      );
    return FilledButton.icon(
      onPressed: admin && !busy
          ? () => act('build', {
              'task_id': task['id'],
              'target': target,
              'update_branch': true,
            })
          : null,
      icon: const Icon('fact_check'),
      label: const Text('Validate latest commit'),
    );
  }

  Component _detail() {
    final task = tasks.isEmpty ? null : tasks.first;
    final pull = task?['pull'];
    return ui.el('div', [
      ui.button(
        'All tasks',
        backToTasks,
        variant: 'ghost',
        small: true,
        iconName: 'arrow-left',
        classes: 'task-detail__back',
      ),
      ui.el('header', [
        ui.el('div', [
          ui.el('p', [
            ui.icon('list-checks', classes: 'icon--sm'),
            ui.text('Task'),
            if (task != null)
              ui.el(
                'span',
                [ui.text(shortSha(task['id']))],
                classes: 'mono',
                attributes: {'title': '${task['id']}'},
              ),
          ], classes: 'page-header__overline task-detail__overline'),
          ui.el('h1', [
            ui.text(task == null ? 'Task detail' : '${task['title']}'),
          ], classes: 'page-header__title'),
          if (task != null)
            ui.el('div', [
              ui.badge(taskState(task), tone: taskTone(task)),
              ui.el('span', [
                ui.icon('folder'),
                ui.text('${task['repository']}'),
              ], classes: 'type-tag'),
              ui.el('span', [
                ui.roleTile(
                  taskAgentName(task, fallback: 'Identity unavailable'),
                  role:
                      (task['assigned_agent'] as Map?)?['role'] ??
                      taskAgentName(task),
                  size: 'xs',
                  live: activity['${task['profile_id']}'] == 'working',
                ),
                ui.el('span', [
                  ui.text(
                    taskAgentName(task, fallback: 'Identity unavailable'),
                  ),
                ], classes: 'agent-label__name'),
              ], classes: 'agent-label'),
              if (pull is Map)
                ui.el('span', [
                  ui.icon('git-pull-request'),
                  ui.text('#${pull['number']} · ${pullStatus(task)}'),
                ], classes: 'type-tag'),
            ], classes: 'task-detail__meta'),
        ], classes: 'page-header__text'),
        ui.el('div', [
          ui.button(
            'Refresh task',
            busy ? null : refresh,
            iconName: 'refresh-cw',
          ),
        ], classes: 'page-header__actions'),
      ], classes: 'page-header'),
      if (busy || refreshing && task == null) const LinearProgressIndicator(),
      if ((error ?? readError) != null)
        ui.notice(error ?? readError, error: true),
      if (task == null)
        ui.emptyState(
          'Task unavailable. Refresh to try again.',
          iconName: 'list-checks',
        ),
      if (task != null) ...[
        ui.el(
          'section',
          [
            ui.icon(
              taskNeedsAttention(task) ? 'triangle-alert' : 'arrow-right',
              classes: 'callout__icon',
            ),
            ui.el('p', [
              ui.text('Next step'),
            ], classes: 'overline task-detail__eyebrow'),
            ui.el('h2', [
              ui.text(taskNextStep(task, executor: executor)),
            ], classes: 'callout__title'),
            if ((task['availability'] as Map?)?['checked_at'] != null)
              ui.el('p', [
                ui.text(
                  'Availability checked ${task['availability']['checked_at']} · display checks may be cached for ${task['availability']['max_age_seconds']} seconds; starting work verifies it again.',
                ),
              ], classes: 'callout__text'),
            ui.el('div', [
              _nextAction(task),
              if (task['head_sha'] != null &&
                  !['completed', 'closed', 'merged'].contains(task['state']))
                OutlinedButton(
                  onPressed: admin && !busy ? () => completeTask(task) : null,
                  child: const Text('Mark task done'),
                ),
              OutlinedButton.icon(
                onPressed: admin && !busy ? () => discussTask(task) : null,
                icon: const Icon('groups'),
                label: const Text('Discuss with group'),
              ),
            ], classes: 'task-detail__actions'),
          ],
          classes: 'callout task-detail__next',
          attributes: {
            'data-tone': taskNeedsAttention(task)
                ? 'danger'
                : ['merged', 'completed'].contains(task['state'])
                ? 'success'
                : 'accent',
          },
        ),
        if (task['completion'] is Map)
          ui.el(
            'section',
            [
              ui.icon('circle-check', classes: 'callout__icon'),
              ui.el('h2', [
                ui.text('Done: ${completionOutcome(task)}'),
              ], classes: 'callout__title'),
              ui.el('p', [
                ui.text('Reason: ${task['completion']['reason']}'),
              ], classes: 'callout__text'),
              SelectableText(
                'Candidate: ${task['completion']['candidate']}\nUpstream at completion: ${task['completion']['upstream']}',
              ),
              ui.el('p', [
                ui.text(
                  task['completion']['validation'] == 'not_applicable'
                      ? 'No changes were made; candidate validation is not applicable.'
                      : task['completion']['validation'] == 'verified'
                      ? 'Required checks passed for this candidate. Deployment remains separate.'
                      : 'No verified required checks for this candidate at completion. Done is not validation.',
                ),
              ], classes: 'callout__text'),
              ui.el('p', [
                ui.text(
                  'Recorded by ${task['completion']['actor']} at ${task['completion']['at']}',
                ),
              ], classes: 'callout__text'),
            ],
            classes: 'callout task-detail__done',
            attributes: {'data-tone': 'success'},
          ),
        if (task['follow_up_tasks'] is Map &&
            (task['follow_up_tasks'] as Map).isNotEmpty)
          ui.el('div', [
            for (final id in (task['follow_up_tasks'] as Map).values)
              TextButton.icon(
                onPressed: () => openTask('$id'),
                icon: const Icon('task_alt'),
                label: const Text('Open follow-up draft'),
              ),
          ], classes: 'actions'),
        if (task['review_error'] != null)
          ui.el(
            'p',
            [
              ui.icon('clock'),
              ui.text('Group review waiting: ${task['review_error']}'),
            ],
            classes: 'task-page__status',
            attributes: {'data-tone': 'warning'},
          ),
        if (task['manual_pr'] is Map) ...[
          ui.notice(task['manual_pr']['message']),
          if (githubCompareUri(task['manual_pr']['compare_url']) != null)
            ui.el(
              'a',
              [ui.text('Open GitHub comparison and create draft PR')],
              attributes: {
                'href': '${task['manual_pr']['compare_url']}',
                'target': '_blank',
                'rel': 'noopener noreferrer',
              },
            ),
        ],
        if (task['checks_unavailable'] is Map &&
            (task['checks_unavailable'] as Map).isNotEmpty)
          ui.notice(
            'Some GitHub check evidence is unavailable. Delivery remains pending; unavailable checks are not passing checks.',
          ),
        if (task['pull_notice'] != null)
          ui.el(
            'p',
            [ui.icon('info'), ui.text('${task['pull_notice']}')],
            classes: 'task-page__status',
            attributes: {'data-tone': 'info'},
          ),
        if (github['configured'] != true)
          ui.el('div', [
            ui.el(
              'p',
              [
                ui.icon('info'),
                ui.text(
                  'GitHub publishing is not configured. Local group review and validation remain available. Publishing requires GitHub setup.',
                ),
              ],
              classes: 'task-page__status',
              attributes: {'data-tone': 'info'},
            ),
            ui.button(
              'Set up GitHub publishing',
              admin && !busy ? configure : null,
              small: true,
              iconName: 'key-round',
            ),
          ], classes: 'task-detail__github'),
        ui.el(
          'div',
          [
            for (final (label, name) in [
              ('Overview', 'layout-dashboard'),
              ('Activity & agents', 'activity'),
              ('Changes', 'git-compare'),
              ('Checks & builds', 'circle-check'),
            ])
              ui.el(
                'button',
                [ui.icon(name), ui.text(label)],
                classes: 'tabs__tab task-detail__tab',
                attributes: {
                  'type': 'button',
                  'role': 'tab',
                  'id':
                      'task-detail-tab-${label.replaceAll(' ', '-').replaceAll('&', 'and')}',
                  'aria-selected': '${section == label}',
                  'aria-controls': 'task-detail-panel',
                  'tabindex': section == label ? '0' : '-1',
                },
                events: {'click': (_) => setState(() => section = label)},
              ),
          ],
          classes: 'tabs task-detail__tabs',
          attributes: {'role': 'tablist', 'aria-label': 'Task sections'},
          events: {'keydown': ui.tabListKeyDown},
        ),
        ui.el(
          'div',
          [
            if (section == 'Overview') ...[
              _groupReviewStatus(task),
              if (task['availability']?['state'] == 'decision') _decision(task),
              if (task['source'] is Map &&
                  '${task['source']['group_id'] ?? ''}'.isNotEmpty)
                ui.el('div', [
                  ui.el('p', [
                    ui.icon('git-branch'),
                    ui.text(
                      'Follow-up proposed by group ${task['source']['group_id']} in meeting ${task['source']['meeting_id']}',
                    ),
                  ], classes: 'task-page__status'),
                  // Only when there is an originating task to open: a group
                  // proposal has none, and linking to it produced /tasks/null.
                  if ('${task['source']['task_id'] ?? ''}'.isNotEmpty)
                    TextButton(
                      onPressed: () => openTask('${task['source']['task_id']}'),
                      child: const Text('Open originating task'),
                    ),
                ], classes: 'task-detail__source'),
              if (task['outcome_notice'] is Map)
                _outcomeNotice(task['outcome_notice'] as Map),
              if (task['acceptance'] is Map)
                Text(_acceptanceText(task['acceptance'] as Map)),
              if (task['outcome_error'] != null)
                Text('Outcome review waiting: ${task['outcome_error']}'),
              ui.card(
                'Task description & acceptance criteria',
                [
                  ui.el('p', [
                    ui.text(
                      '${task['description'] ?? 'No description recorded.'}',
                    ),
                  ], classes: 'task-detail__description'),
                ],
                iconName: 'file-text',
                classes: 'task-detail__card',
              ),
              ui.card(
                'Assignment',
                [
                  ui.el('div', [
                    ui.roleTile(
                      taskAgentName(task, fallback: 'Identity unavailable'),
                      role:
                          (task['assigned_agent'] as Map?)?['role'] ??
                          taskAgentName(task),
                      size: 'sm',
                      live: activity['${task['profile_id']}'] == 'working',
                    ),
                    ui.el('p', [
                      ui.text(
                        'Assigned agent: ${taskAgentName(task, fallback: 'Identity unavailable')}',
                      ),
                    ]),
                  ], classes: 'task-detail__assignee'),
                  if (task['execution'] is Map)
                    Text(
                      'Execution: parallel instance of ${(task['execution'] as Map)['template_name'] ?? 'a template'}',
                    ),
                  for (final consult in task['consultations'] as List? ?? [])
                    if (consult is Map)
                      ExpansionTile(
                        title: Text(
                          'Consultation · ${consult['name'] ?? ''} · ${consult['state']}',
                        ),
                        subtitle: Text(
                          '${consult['question'] ?? ''}',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        children: [
                          SelectableText(
                            '${consult['answer'] ?? ''}${(consult['error'] as String? ?? '').isNotEmpty ? '\n${consult['error']}' : ''}',
                          ),
                        ],
                      ),
                  if (task['error'] != null)
                    ui.notice('Task issue: ${task['error']}', error: true),
                  // The gateway marks why delivery is blocked, so the action
                  // can be offered without matching on the message text. A
                  // missing toolchain is an operator task; the install lives
                  // on the Settings page rather than being duplicated here.
                  if ((task['delivery_blocker'] as Map?)?['kind'] ==
                      'missing_toolchain')
                    Row(
                      children: [
                        TextButton(
                          onPressed: () => navigate('/configuration'),
                          child: const Text('Install the required SDK'),
                        ),
                      ],
                    ),
                  if (task['automation_error'] != null)
                    ui.notice(
                      'Automation waiting: ${task['automation_error']}',
                      error: true,
                    ),
                  if (task['completion_receipt'] is Map)
                    SelectableText(
                      'Completion receipt recorded for ${task['completion_receipt']['commit']}. Worker reports are separate from validation.',
                    ),
                  if ((task['source'] as Map?)?['task_id'] != null)
                    Text(
                      (task['source'] as Map)['auto_queued'] is Map
                          ? 'Auto-queued from group review of task ${(task['source'] as Map)['task_id']}.'
                          : 'Follow-up of task ${(task['source'] as Map)['task_id']}.',
                    ),
                ],
                iconName: 'bot',
                classes: 'task-detail__card',
              ),
              ExpansionTile(
                title: const Text('Automation settings'),
                children: [
                  SwitchListTile(
                    title: const Text(
                      'Automatically capture completed candidates',
                    ),
                    subtitle: const Text(
                      'Check the completion receipt and capture the committed candidate. No build service is required; validation is a separate setting.',
                    ),
                    value: taskAutoCapture(task),
                    onChanged:
                        admin &&
                            !busy &&
                            ![
                              'merged',
                              'closed',
                              'completed',
                            ].contains(task['state'])
                        ? (value) => act('policy', {
                            'task_id': task['id'],
                            'auto_capture': value,
                            'auto_validate': task['auto_validate'] == true,
                            'auto_publish': task['auto_publish'] == true,
                          })
                        : null,
                  ),
                  SwitchListTile(
                    title: const Text(
                      'Automatically validate captured candidates',
                    ),
                    subtitle: Text(
                      executor == 'service'
                          ? 'Verify the exact receipt commit, then send results back to the agent before releasing its session.'
                          : 'Uses the gateway build executor. Builds stop if the gateway restarts; install the durable build service for restart resilience.',
                    ),
                    value: task['auto_validate'] == true,
                    onChanged:
                        admin &&
                            !busy &&
                            ![
                              'merged',
                              'closed',
                              'completed',
                            ].contains(task['state'])
                        ? (value) => act('policy', {
                            'task_id': task['id'],
                            'auto_capture': taskAutoCapture(task),
                            'auto_validate': value,
                            if (!value) 'auto_publish': false,
                          })
                        : null,
                  ),
                  SwitchListTile(
                    title: const Text(
                      'Publish verified branch and follow the configured PR/adoption workflow',
                    ),
                    subtitle: const Text(
                      'Checks current GitHub account access and the direct/fork destination before enabling. Authorizes pushing verified commits and publishing generated task/check details. Token write access is only proven by successful writes. The agent stays reserved until it reviews the results. Merge and deployment remain manual.',
                    ),
                    value: task['auto_publish'] == true,
                    onChanged:
                        admin &&
                            !busy &&
                            executor == 'service' &&
                            task['task_publication_allowed'] != false &&
                            (task['auto_publish'] == true ||
                                github['configured'] == true) &&
                            ![
                              'merged',
                              'closed',
                              'completed',
                            ].contains(task['state'])
                        ? (value) => act('policy', {
                            'task_id': task['id'],
                            'auto_capture': taskAutoCapture(task),
                            'auto_validate':
                                value || task['auto_validate'] == true,
                            'auto_publish': value,
                          })
                        : null,
                  ),
                  if (task['delivery_workflow'] is Map) ...[
                    ListTile(
                      title: Text(
                        'Delivery: ${task['delivery_workflow']['state']}',
                      ),
                      subtitle: Text(
                        '${task['delivery_workflow']['error'] ?? ''}\n${task['delivery_workflow']['notification_error'] ?? ''}\n${task['delivery_workflow']['agent_report']?['summary'] ?? ''}',
                      ),
                    ),
                    if (task['await_verification'] == true ||
                        task['auto_publish'] == true)
                      TextButton(
                        onPressed: admin && !busy
                            ? () => act('policy', {
                                'task_id': task['id'],
                                'auto_capture': taskAutoCapture(task),
                                'auto_validate': false,
                                'auto_publish': false,
                              })
                            : null,
                        child: const Text('Stop delivery automation'),
                      ),
                    if (task['delivery_workflow']['state'] == 'attention')
                      TextButton(
                        onPressed:
                            admin &&
                                !busy &&
                                task['auto_validate'] == true &&
                                task['delivery_workflow']['revalidation_wait']?['inspection_pending'] !=
                                    true
                            ? () => act('policy', {
                                'task_id': task['id'],
                                'auto_capture': taskAutoCapture(task),
                                'auto_validate': true,
                                'auto_publish': task['auto_publish'] == true,
                                'retry_delivery': true,
                              })
                            : null,
                        child: const Text(
                          'Retry verification and delivery inspection',
                        ),
                      ),
                  ],
                  if (task['organization_id'] != null)
                    ListTile(
                      title: const Text('Organization follow-up automation'),
                      subtitle: const Text(
                        'Shared limits and queueing are managed by the organization.',
                      ),
                      trailing: TextButton(
                        onPressed:
                            admin && !busy && component.coordinator != null
                            ? () {
                                connection.selectedOrganization.value =
                                    task['organization_id'] as String;
                                component.coordinator?.navigate(
                                  OrganizationRoute('automation'),
                                );
                              }
                            : null,
                        child: const Text('Organization settings'),
                      ),
                    ),
                  if (admin)
                    Wrap(
                      spacing: 8,
                      children: [
                        TextButton.icon(
                          onPressed: busy ? null : () => consultTask(task),
                          icon: const Icon('support_agent'),
                          label: const Text('Ask a consultant'),
                        ),
                        TextButton.icon(
                          onPressed: busy ? null : () => acceptanceTask(task),
                          icon: const Icon('verified_outlined'),
                          label: const Text('Acceptance review'),
                        ),
                        if (_archivedRun(task) != null)
                          TextButton.icon(
                            onPressed: busy ? null : () => handoverTask(task),
                            icon: const Icon('swap_horiz'),
                            label: const Text('Hand over to another agent'),
                          ),
                      ],
                    ),
                ],
              ),
              const SizedBox(height: 12),
              _actions(task),
            ],
            if (section == 'Activity & agents') _conflictRepair(task),
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
                      title: Text(
                        '${meeting['group_name']} ${meeting['outcome'] == true ? 'outcome review' : 'review'}',
                      ),
                      subtitle: Text('${meeting['state']}'),
                      children: [
                        if (component.coordinator != null)
                          TextButton(
                            onPressed: () => component.coordinator!.push(
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
                        if ((task['auto_queue'] as Map?)?[meeting['job_id']]
                            is Map)
                          Text(
                            _automationOutcome(
                              (task['auto_queue'] as Map)[meeting['job_id']]
                                  as Map,
                            ),
                          ),
                        for (final proposal
                            in meeting['proposals'] as List? ?? [])
                          if (proposal is Map)
                            ListTile(
                              title: Text('${proposal['title']}'),
                              subtitle: Text(
                                'Assigned: ${proposal['assignee'] ?? proposal['profile_id']}\n${proposal['description']}',
                              ),
                              trailing: Wrap(
                                spacing: 4,
                                children: [
                                  TextButton(
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
                                  if ((task['follow_up_tasks']
                                          as Map?)?[proposal['key']] ==
                                      null)
                                    TextButton(
                                      onPressed: admin && !busy
                                          ? () => act('proposal', {
                                              'task_id': task['id'],
                                              'meeting_id': meeting['job_id'],
                                              'proposal_key': proposal['key'],
                                              'queue': true,
                                            })
                                          : null,
                                      child: const Text('Create and queue'),
                                    )
                                  else
                                    TextButton(
                                      onPressed: admin && !busy
                                          ? () => act('assignment', {
                                              'task_id':
                                                  '${(task['follow_up_tasks'] as Map?)?[proposal['key']]}',
                                              'mode': 'queue',
                                            })
                                          : null,
                                      child: const Text('Queue'),
                                    ),
                                ],
                              ),
                            ),
                      ],
                    ),
                  ),
            if (section == 'Changes') ...[
              ui.card(
                'Branch & publication',
                [
                  SelectableText(
                    'Branch: ${task['branch']}\nBase: ${task['base_ref']} \u00b7 ${task['base_sha']}\nCandidate: ${task['head_sha'] ?? 'Not captured'}',
                  ),
                  if (task['github_repository'] != null)
                    SelectableText(
                      'Publish branch to: ${task['publishing_repository'] ?? task['github_repository']}\n'
                      'Open draft PR into: ${task['github_repository']}',
                    ),
                  if (task['pull'] is Map) ...[
                    SelectableText(
                      'PR #${task['pull']['number']} \u00b7 ${pullStatus(task)}\n${task['pull']['url']}',
                    ),
                    if (task['pull']['mergeable'] == false &&
                        ![
                          'merged',
                          'closed',
                          'completed',
                        ].contains(task['state']))
                      const Text('GitHub reports merge conflicts.'),
                  ],
                  if (task['merge_sha'] != null)
                    SelectableText('Merged result: ${task['merge_sha']}'),
                ],
                iconName: 'git-branch',
                classes: 'task-detail__card',
              ),
              ui.card(
                'Commit history',
                [
                  TaskRevisions(task: task),
                  TaskTimeline(
                    task: task,
                    onSelect: (sha) => showCommit(task, sha),
                    onRefresh: refresh,
                  ),
                ],
                iconName: 'git-commit-horizontal',
                classes: 'task-detail__card',
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
              if (task['validation_environment_status'] case {
                'state': 'unavailable',
                'reason': final String reason,
              })
                Text(reason),
              if (hasApprovedValidationEnvironment(task))
                const Text(
                  'Validation environment approved for this candidate.',
                ),
              if (needsValidationEnvironmentReview(task))
                OutlinedButton.icon(
                  onPressed: admin && !busy
                      ? () => reviewTaskEnvironment(task)
                      : null,
                  icon: const Icon('fact_check'),
                  label: const Text('Review validation environment'),
                ),
              ui.card(
                'Validation evidence',
                iconName: 'circle-check',
                classes: 'task-detail__card',
                [
                  Builder(
                    builder: (context) {
                      final target = taskValidationTarget(task);
                      final build = (task['builds'] as Map?)?[target];
                      final label = build is! Map
                          ? 'not run'
                          : build['state'] == 'complete'
                          ? (build['target'] == target &&
                                    build['required_checks_verified'] == true
                                ? (taskValidationNeedsRecheck(task, target)
                                      ? 'recorded checks passed; revalidation required'
                                      : 'verified')
                                : 'finished without verified required checks')
                          : '${build['state']}';
                      return Text('Current candidate validation: $label');
                    },
                  ),
                  if (task['builds'] is Map)
                    for (final entry in (task['builds'] as Map).entries)
                      if (entry.value is Map)
                        if (entry.key == taskValidationTarget(task))
                          _buildEvidence('${entry.key}', entry.value)
                        else
                          ExpansionTile(
                            title: Text(
                              'Previous candidate build \u00b7 ${entry.key}',
                            ),
                            subtitle: const Text(
                              'Does not validate the current candidate.',
                            ),
                            children: [
                              _buildEvidence('${entry.key}', entry.value),
                            ],
                          ),
                  for (final check in task['checks'] as List? ?? [])
                    if (check is Map)
                      Text(
                        '${check['name']}: ${check['conclusion'] ?? check['status']}',
                      ),
                  for (final check in task['statuses'] as List? ?? [])
                    if (check is Map)
                      Text('${check['context']}: ${check['state']}'),
                  for (final review in task['reviews'] as List? ?? [])
                    if (review is Map)
                      Text('${review['user']?['login']}: ${review['state']}'),
                ],
              ),
              _actions(task),
            ],
          ],
          classes: 'task-detail__panel',
          attributes: {
            'id': 'task-detail-panel',
            'role': 'tabpanel',
            'aria-labelledby':
                'task-detail-tab-${section.replaceAll(' ', '-').replaceAll('&', 'and')}',
            'tabindex': '0',
          },
        ),
      ],
    ], classes: 'task-page task-detail');
  }

  Component _conflictRepair(Map<String, dynamic> task) {
    final repair = task['pr_repair'] as Map?;
    if (!taskHasPrConflicts(task) && repair == null)
      return const SizedBox.shrink();
    final state = '${repair?['state'] ?? ''}';
    final updating = repair?['kind'] == 'validation_update';
    final agent = taskAgentName(task, fallback: 'the assigned agent');
    final terminal = ['merged', 'closed', 'completed'].contains(task['state']);
    final canStart =
        !terminal &&
        !updating &&
        taskHasPrConflicts(task) &&
        (repair == null ||
            [
              'cancelled',
              'obsolete',
              'published',
              // Finished attempts must not trap the task: a failed validation
              // or a repaired candidate that conflicts again can be retried.
              'validation_failed',
              'ready',
            ].contains(state));
    return Card(
      key: const ValueKey('pr-conflict-repair'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              taskHasPrConflicts(task) &&
                      (repair == null ||
                          ['cancelled', 'obsolete'].contains(state))
                  ? 'PR #${task['pull']?['number']} has merge conflicts'
                  : '${updating ? 'Branch update' : 'Conflict repair'} · ${state.replaceAll('_', ' ')}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            Text(
              '${repair?['message'] ?? 'This PR cannot merge cleanly into its upstream base. Ask the assigned agent to repair its branch.'}',
            ),
            Text('Assigned agent: $agent'),
            SelectableText(
              'Task branch: ${task['branch']}\nPublish to: ${task['publishing_repository'] ?? task['github_repository']}\nPR target: ${task['github_repository']}',
            ),
            const Text(
              'The agent updates the existing task branch and commits locally. Publication and merging remain separate actions.',
            ),
            if (repair?['candidate'] != null)
              SelectableText(
                '${updating ? 'Updated candidate' : 'Repaired candidate'}: ${repair?['candidate']}',
              ),
            if ((repair?['policy_files'] as List? ?? []).isNotEmpty)
              SelectableText(
                'Upstream validation files: ${(repair?['policy_files'] as List).join(', ')}',
              ),
            if (repair?['upstream_sha'] != null)
              SelectableText(
                '${updating ? 'Upstream commit for this update' : 'Upstream commit for this repair'}: ${repair?['upstream_sha']}',
              ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (canStart)
                  FilledButton.icon(
                    onPressed: admin && !busy
                        ? () => act('repair', {
                            'task_id': task['id'],
                            'head_sha': task['pull']['head_sha'],
                          })
                        : null,
                    icon: const Icon('build'),
                    label: Text('Resolve conflicts with $agent'),
                  ),
                if (state == 'queued')
                  TextButton(
                    onPressed: admin && !busy
                        ? () => act('repair', {
                            'task_id': task['id'],
                            'mode': 'cancel',
                          })
                        : null,
                    child: Text(
                      updating
                          ? 'Cancel queued update'
                          : 'Cancel queued repair',
                    ),
                  ),
                if (state == 'queued' ||
                    state == 'monitoring_unavailable' ||
                    state == 'waiting_for_input' ||
                    state == 'needs_attention')
                  OutlinedButton(
                    onPressed: component.coordinator == null
                        ? null
                        : () => component.coordinator!.navigate(
                            OrganizationRoute('runs'),
                          ),
                    child: const Text('Inspect agent session'),
                  ),
                if (state == 'needs_attention')
                  OutlinedButton(
                    onPressed: admin && !busy
                        ? () => act('repair', {
                            'task_id': task['id'],
                            'mode': 'retry',
                            'inspected': true,
                          })
                        : null,
                    child: Text(
                      updating
                          ? 'Recheck inspected update'
                          : 'Recheck inspected repair',
                    ),
                  ),
                if (state == 'validating' || state == 'validation_failed')
                  OutlinedButton(
                    onPressed: () =>
                        setState(() => section = 'Checks & builds'),
                    child: Text(
                      updating
                          ? 'Inspect update validation'
                          : 'Inspect repair validation',
                    ),
                  ),
                if (state == 'ready' &&
                    task['task_publication_allowed'] != false)
                  FilledButton(
                    onPressed: admin && !busy ? () => review(task) : null,
                    child: Text(
                      updating
                          ? 'Review and publish updated candidate'
                          : 'Review and publish repaired candidate',
                    ),
                  ),
                OutlinedButton(
                  onPressed: admin && !busy
                      ? () => act('refresh', {'task_id': task['id']})
                      : null,
                  child: Text(updating ? 'Refresh task' : 'Refresh PR status'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Component _actions(Map<String, dynamic> task) => Wrap(
    spacing: 8,
    runSpacing: 8,
    children: [
      if (task['head_sha'] != null &&
          !['merged', 'closed', 'completed'].contains(task['state']))
        OutlinedButton(
          onPressed: admin && !busy
              ? () => releaseAct('single', {
                  'task_id': task['id'],
                  'head_sha': task['head_sha'],
                })
              : null,
          child: const Text('Release this task only'),
        ),
      if (task['task_publication_allowed'] == false)
        const Text(
          'Delivery route: release PR. Capture and validation remain available.',
        ),
      for (final receipt
          in (task['release_deliveries'] as List? ?? []).whereType<Map>())
        Text(
          'Release ${receipt['version']}: ${receipt['stage']} · candidate ${receipt['candidate_sha']} · ${receipt['current_candidate_matches'] == true ? 'current candidate' : 'earlier candidate'}${receipt['pull'] is Map ? ' · ${receipt['pull']['url']}' : ''}',
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
                    await downloadBytes(
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
      if (task['task_publication_allowed'] != false &&
          task['head_sha'] != null &&
          !['merged', 'closed', 'completed'].contains(task['state']))
        OutlinedButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => review(task)
              : null,
          child: const Text('Review and publish'),
        ),
      if (task['task_publication_allowed'] != false &&
          task['publish'] == null &&
          task['github_repository'] != null &&
          (github['publication']
                  as Map?)?[task['github_repository']]?['mode'] ==
              'fork')
        OutlinedButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => choosePublication(task)
              : null,
          child: const Text('Publication destination'),
        ),
      if (task['task_publication_allowed'] != false &&
          task['publish'] != null &&
          task['pull'] == null)
        FilledButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => previewPull(task)
              : null,
          child: Text(
            needsPrAuthentication(github, task['github_repository'] as String?)
                ? 'Configure authentication for upstream PR'
                : manualPrRequired(github, '${task['github_repository']}')
                ? 'Check for operator-created PR'
                : 'Open draft PR',
          ),
        ),
      if (task['pull'] is Map)
        OutlinedButton(
          onPressed: admin && !busy && github['configured'] == true
              ? () => act('refresh', {'task_id': task['id']})
              : null,
          child: const Text('Refresh PR status'),
        ),
      _validateAction(task),
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

  /// A historical pass hides validation only while its approved policy matches.
  /// The durable queue separates policy identities. Next step links to active builds.
  Component _validateAction(Map<String, dynamic> task) {
    final target = taskValidationTarget(task);
    if (target == null) return const SizedBox.shrink();
    final build = (task['builds'] as Map?)?[target];
    if (build is Map &&
        build['target'] == target &&
        build['state'] == 'complete' &&
        build['required_checks_verified'] == true &&
        !taskValidationNeedsRecheck(task, target))
      return const SizedBox.shrink();
    final running =
        build is Map &&
        build['target'] == target &&
        ['queued', 'running'].contains(build['state']);
    if (running) return const SizedBox.shrink();
    return OutlinedButton(
      onPressed: admin && !busy
          ? () => act('build', {
              'task_id': task['id'],
              'target': target,
              'update_branch': true,
            })
          : null,
      child: Text(
        task['state'] == 'merged'
            ? 'Build merged result'
            : 'Validate latest commit',
      ),
    );
  }
}
