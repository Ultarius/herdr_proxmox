import 'package:juice/juice.dart';
import 'dart:typed_data';
import 'dashboard_bloc.dart';
import 'request_id.dart';

const _keepTaskValue = Object();

class TasksState extends BlocState {
  const TasksState({
    this.tasks = const [],
    this.releases = const [],
    this.github = const {},
    this.storage = const {},
    this.executor = 'unavailable',
    this.loading = false,
    this.busy = false,
    this.readError,
    this.error,
    this.releaseFailure,
  });
  final List<Map<String, dynamic>> tasks, releases;
  final Map<String, dynamic> github, storage;
  final String executor;
  final bool loading, busy;
  final String? readError, error;
  final Map<String, String>? releaseFailure;
  TasksState copyWith({
    List<Map<String, dynamic>>? tasks,
    List<Map<String, dynamic>>? releases,
    Map<String, dynamic>? github,
    Map<String, dynamic>? storage,
    String? executor,
    bool? loading,
    bool? busy,
    Object? readError = _keepTaskValue,
    Object? error = _keepTaskValue,
    Object? releaseFailure = _keepTaskValue,
  }) => TasksState(
    tasks: tasks ?? this.tasks,
    releases: releases ?? this.releases,
    github: github ?? this.github,
    storage: storage ?? this.storage,
    executor: executor ?? this.executor,
    loading: loading ?? this.loading,
    busy: busy ?? this.busy,
    readError: identical(readError, _keepTaskValue)
        ? this.readError
        : readError as String?,
    error: identical(error, _keepTaskValue) ? this.error : error as String?,
    releaseFailure: identical(releaseFailure, _keepTaskValue)
        ? this.releaseFailure
        : releaseFailure as Map<String, String>?,
  );
}

class RefreshTasks extends EventBase {
  RefreshTasks(this.id, this.revision);
  final String? id;
  final int revision;
}

class MutateTask extends ResultEvent<dynamic> {
  MutateTask(this.action, this.body, {this.release = false});
  final String action;
  final Map<String, dynamic> body;
  final bool release;
}

class ReadTaskEvidence extends ResultEvent<dynamic> {
  ReadTaskEvidence(this.path);
  final String path;
}

class DownloadTaskEvidence extends ResultEvent<Uint8List> {
  DownloadTaskEvidence(this.path);
  final String path;
}

class RunTaskOperation extends ResultEvent<dynamic> {
  RunTaskOperation(this.path, this.body);
  final String path;
  final Map<String, dynamic> body;
}

class ResetTasks extends EventBase {
  ResetTasks({this.rowsOnly = false});
  final bool rowsOnly;
}

/// Shared task/release feature model. The renderers own confirmation UI; this
/// bloc owns observed rows, polling, read ordering, writes and durable retry IDs.
class TasksBloc extends JuiceBloc<TasksState> {
  TasksBloc(this.connection)
    : super(const TasksState(), [
        () => UseCaseBuilder.typed(
          () => _RefreshTasks(),
          concurrency: EventConcurrency.restartable,
        ),
        () => UseCaseBuilder.typed(
          () => _MutateTask(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _ReadTaskEvidence(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _DownloadTaskEvidence(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _RunTaskOperation(),
          concurrency: EventConcurrency.sequential,
        ),
        () => UseCaseBuilder.typed(
          () => _ResetTasks(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]) {
    _session = session;
    bindStream(connection.stream, (_) => _sessionChanged());
    connection.operator.addListener(_sessionChanged);
    connection.notificationRevision.addListener(_notified);
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => refresh());
  }
  final DashboardBloc connection;
  final Map<String, String> _requests = {};
  int _revision = 0;
  String _session = '';
  String get session =>
      '${connection.generation}:${connection.state.connected}:${connection.operator.value["role"]}';
  void _sessionChanged() {
    if (isClosing || _session == session) return;
    _session = session;
    _revision++;
    _pending = null;
    _requests.clear();
    send(ResetTasks());
    if (available && _started) refresh(force: true);
  }

  String? _selected;
  Future<void>? _pending;
  Timer? _timer;
  bool _started = false;
  bool get available =>
      !isClosing && connection.state.connected && !connection.disconnecting;
  bool current(String epoch) => available && epoch == session;
  Future<void>? get pending => _pending;
  void _notified() => refresh(force: true);
  Future<void> clearRows() {
    _revision++;
    return send(ResetTasks(rowsOnly: true));
  }

  Future<void> refresh({
    String? taskId,
    bool force = false,
    bool selection = false,
  }) {
    if (!available) return Future<void>.value();
    _started = true;
    if (state.busy && !force) return Future<void>.value();
    if (selection && taskId != _selected) {
      _selected = taskId;
      _requests.clear();
      send(ResetTasks());
      force = true;
    }
    if (!force && _pending != null) return _pending!;
    final read = send(RefreshTasks(_selected, ++_revision));
    _pending = read;
    return read.whenComplete(() {
      if (identical(_pending, read)) _pending = null;
    });
  }

  Future<dynamic> mutate(
    String action,
    Map<String, dynamic> body, {
    bool release = false,
  }) {
    if (!available || connection.operator.value['role'] != 'admin')
      return Future<dynamic>.error(
        StateError('Only a connected administrator can change tasks.'),
      );
    if (state.busy) return Future<dynamic>.value(null);
    return sendForResult(
      MutateTask(action, body, release: release),
      timeout: const Duration(minutes: 4),
    );
  }

  /// Dialog evidence and administrative task operations still go through typed
  /// Juice events; no widget owns their HTTP lifetime or completion fencing.
  Future<dynamic> request(String path, [Map<String, dynamic>? body]) =>
      body == null
      ? sendForResult(
          ReadTaskEvidence(path),
          timeout: const Duration(minutes: 3),
        )
      : sendForResult(
          RunTaskOperation(path, body),
          timeout: const Duration(minutes: 3),
        );
  Future<Uint8List> bytes(String path) => sendForResult(
    DownloadTaskEvidence(path),
    timeout: const Duration(seconds: 35),
  );

  @override
  Future<void> close() {
    if (!isClosing) {
      _revision++;
      _timer?.cancel();
      connection.notificationRevision.removeListener(_notified);
      connection.operator.removeListener(_sessionChanged);
      _requests.clear();
    }
    return super.close();
  }
}

class _RefreshTasks extends BlocUseCase<TasksBloc, RefreshTasks> {
  @override
  Future<void> execute(RefreshTasks event) async {
    if (!bloc.available) return;
    final epoch = bloc.session;
    bool current() =>
        bloc.current(epoch) &&
        event.revision == bloc._revision &&
        !isSuperseded;
    emitUpdate(newState: bloc.state.copyWith(loading: true));
    try {
      final data = await bloc.connection.request('tasks');
      if (!current()) return;
      Map<String, dynamic>? detail;
      String? detailError;
      if (event.id != null) {
        try {
          detail = Map<String, dynamic>.from(
            await bloc.connection.request('tasks/detail?id=${event.id}'),
          );
        } catch (error) {
          detailError = '$error';
        }
      }
      if (!current()) return;
      final rows = List<Map<String, dynamic>>.from(data['tasks'] ?? []);
      final match = rows.where((row) => row['id'] == event.id).firstOrNull;
      emitUpdate(
        newState: bloc.state.copyWith(
          tasks: event.id == null
              ? rows
              : detail != null
              ? [detail]
              : match != null
              ? [match]
              : [],
          releases: List<Map<String, dynamic>>.from(data['releases'] ?? []),
          github: Map<String, dynamic>.from(data['github'] ?? {}),
          storage: Map<String, dynamic>.from(data['storage'] ?? {}),
          executor: '${data['executor'] ?? 'unavailable'}',
          loading: false,
          readError: detailError,
        ),
      );
    } catch (error) {
      if (current())
        emitUpdate(
          newState: bloc.state.copyWith(loading: false, readError: '$error'),
        );
    }
  }
}

class _MutateTask extends BlocUseCase<TasksBloc, MutateTask> {
  @override
  Future<void> execute(MutateTask event) async {
    if (!bloc.available || bloc.connection.operator.value['role'] != 'admin') {
      event.fail(StateError('Only an administrator can change tasks.'));
      emitCancel();
      return;
    }
    final epoch = bloc.session;
    final selection =
        '${event.release ? 'release:' : ''}${event.action}:${event.body}';
    final request = bloc._requests.putIfAbsent(
      selection,
      () => '${event.release ? 'release' : 'task'}-${newRequestId()}',
    );
    bloc._revision++;
    emitUpdate(
      newState: bloc.state.copyWith(
        busy: true,
        error: null,
        releaseFailure: null,
      ),
    );
    try {
      final result = await bloc.connection.request(
        '${event.release ? 'releases' : 'tasks'}/${event.action}',
        {...event.body, 'request_id': request},
      );
      if (!bloc.current(epoch))
        throw StateError('Dashboard connection changed.');
      bloc._requests.remove(selection);
      await bloc.refresh(force: true);
      if (!bloc.current(epoch))
        throw StateError('Dashboard connection changed.');
      emitUpdate(newState: bloc.state.copyWith(busy: false));
      event.succeed(result);
    } catch (error, stack) {
      // A refused freeze/publication may have updated retained member evidence.
      // Reload it without erasing the failed action or its stable retry ID.
      if (event.release && bloc.current(epoch)) await bloc.refresh(force: true);
      if (bloc.current(epoch)) {
        final message = '$error'.replaceFirst(RegExp(r'^Exception: '), '');
        emitFailure(
          newState: bloc.state.copyWith(
            busy: false,
            error: message,
            releaseFailure: event.release
                ? {
                    'id': '${event.body['id']}',
                    'action': event.action,
                    'message': message,
                  }
                : null,
          ),
          error: error,
          errorStackTrace: stack,
        );
      }
      event.fail(error, stack);
    }
  }
}

class _ReadTaskEvidence extends BlocUseCase<TasksBloc, ReadTaskEvidence> {
  @override
  Future<void> execute(ReadTaskEvidence event) async {
    final epoch = bloc.session;
    try {
      if (!bloc.available) throw StateError('Dashboard connection changed.');
      final result = await bloc.connection.request(event.path);
      if (!bloc.current(epoch))
        throw StateError('Dashboard connection changed.');
      emitUpdate();
      event.succeed(result);
    } catch (error, stack) {
      emitFailure(error: error, errorStackTrace: stack);
      event.fail(error, stack);
    }
  }
}

class _RunTaskOperation extends BlocUseCase<TasksBloc, RunTaskOperation> {
  @override
  Future<void> execute(RunTaskOperation event) async {
    final epoch = bloc.session;
    try {
      if (!bloc.available || bloc.connection.operator.value['role'] != 'admin')
        throw StateError('Only an administrator can change tasks.');
      bloc._revision++;
      emitUpdate(newState: bloc.state.copyWith(busy: true, error: null));
      final result = await bloc.connection.request(event.path, event.body);
      if (!bloc.current(epoch))
        throw StateError('Dashboard connection changed.');
      emitUpdate(newState: bloc.state.copyWith(busy: false));
      event.succeed(result);
    } catch (error, stack) {
      if (bloc.current(epoch))
        emitFailure(
          newState: bloc.state.copyWith(busy: false, error: '$error'),
          error: error,
          errorStackTrace: stack,
        );
      event.fail(error, stack);
    }
  }
}

class _DownloadTaskEvidence
    extends BlocUseCase<TasksBloc, DownloadTaskEvidence> {
  @override
  Future<void> execute(DownloadTaskEvidence event) async {
    final epoch = bloc.session;
    try {
      if (!bloc.available) throw StateError('Dashboard connection changed.');
      final bytes = await bloc.connection.bytes(event.path);
      if (!bloc.current(epoch))
        throw StateError('Dashboard connection changed.');
      emitUpdate();
      event.succeed(bytes);
    } catch (error, stack) {
      emitFailure(error: error, errorStackTrace: stack);
      event.fail(error, stack);
    }
  }
}

class _ResetTasks extends BlocUseCase<TasksBloc, ResetTasks> {
  @override
  Future<void> execute(ResetTasks event) async => emitUpdate(
    newState: event.rowsOnly
        ? bloc.state.copyWith(tasks: [], loading: false, readError: null)
        : const TasksState(),
  );
}
