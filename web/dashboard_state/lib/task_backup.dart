import 'dart:typed_data';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

const taskBackupLabel = 'Database backup';
const taskJsonLabel = 'Export JSON';
const taskJsonNotice =
    'Readable JSON downloaded. Includes all tasks, full history, and related metadata. Repository files, credentials, and external logs/artifacts need separate backups.';
const taskBackupNotice =
    'Task database backup downloaded. Includes all tasks, full history, and related metadata. '
    'Repository files, credentials, and external logs/artifacts need separate backups.';
const taskBackupBrowserLimit = 64 * 1024 * 1024;
const taskBackupLimitNotice =
    'This backup exceeds the 64 MiB buffered-download limit. Sign in with a browser session for a streamed download, or use a streaming HTTP client with the authenticated task export API.';
const taskBackupStartedNotice =
    'Task backup download started. Your browser streams this large backup to disk. Check Downloads for completion or errors.';
typedef TaskBackupSaver = Future<void> Function(TaskBackup backup);

class TaskBackup {
  TaskBackup(
    this.bytes,
    this.filename, {
    this.contentType = 'application/vnd.sqlite3',
  }) : downloadUri = null;
  TaskBackup.streamed(Uri uri)
    : downloadUri = uri,
      bytes = Uint8List(0),
      filename = '',
      contentType = '';
  final Uint8List bytes;
  final String filename;
  final String contentType;
  final Uri? downloadUri;
}

Future<TaskBackup> fetchTaskBackup(
  DashboardBloc connection, {
  bool asJson = false,
}) async {
  bool allowed() =>
      connection.state.connected &&
      connection.operator.value['role'] == 'admin';
  if (!allowed())
    throw StateError('Only an administrator can export all tasks.');
  final generation = connection.generation;
  final path = asJson ? 'tasks/export/json' : 'tasks/export';
  final info = await connection.request('$path/info');
  if (!allowed() || generation != connection.generation)
    throw StateError('Connection changed. Export the backup again.');
  if (info is! Map || info['estimated_bytes'] is! num)
    throw StateError(
      'Task backup size could not be verified. Refresh the dashboard and retry.',
    );
  if ((info['estimated_bytes'] as num) > taskBackupBrowserLimit) {
    if (connection.browserSession)
      return TaskBackup.streamed(Uri.base.resolve('/api/$path'));
    throw StateError(taskBackupLimitNotice);
  }
  final response = await connection.download(
    path,
    maximumBytes: taskBackupBrowserLimit,
  );
  if (!allowed() || generation != connection.generation) {
    throw StateError('Connection changed. Export the backup again.');
  }
  final disposition = response.headers['content-disposition'] ?? '';
  final filename = RegExp(
    asJson
        ? r'filename="(herdr-tasks-[0-9]{8}T[0-9]{6}Z\.json)"'
        : r'filename="(herdr-tasks-[0-9]{8}T[0-9]{6}Z\.sqlite3)"',
  ).firstMatch(disposition)?[1];
  if (filename == null)
    throw StateError(
      'Task backup filename was not supplied by the gateway. Refresh the dashboard and retry.',
    );
  return TaskBackup(
    response.bodyBytes,
    filename,
    contentType: asJson ? 'application/json' : 'application/vnd.sqlite3',
  );
}

/// Both dashboards share the complete operation lifecycle and download guard.
class TaskBackupController extends ValueNotifier<int> {
  TaskBackupController(this.connection, this.save)
    : _generation = connection.generation,
      super(0) {
    connection.operator.addListener(_sessionChanged);
    _subscription = connection.stream.listen((_) => _sessionChanged());
  }
  final DashboardBloc connection;
  final TaskBackupSaver save;
  StreamSubscription? _subscription;
  int _generation, _operation = 0;
  bool _disposed = false;
  bool busy = false, failed = false, jsonExport = false;
  String? message;
  bool get _allowed =>
      connection.state.connected &&
      connection.operator.value['role'] == 'admin';

  void _sessionChanged() {
    if (_generation != connection.generation || !_allowed) {
      _generation = connection.generation;
      _operation++;
      if (busy || message != null || failed) {
        busy = false;
        message = null;
        failed = false;
        if (!_disposed) notifyListeners();
      }
    }
  }

  void dismiss() {
    message = null;
    failed = false;
    notifyListeners();
  }

  Future<void> export({bool blocked = false, bool asJson = false}) async {
    if (_disposed || busy || blocked || !_allowed) return;
    final epoch = connection.generation, operation = ++_operation;
    bool current() =>
        !_disposed &&
        operation == _operation &&
        epoch == connection.generation &&
        _allowed;
    jsonExport = asJson;
    busy = true;
    message = null;
    failed = false;
    notifyListeners();
    try {
      final backup = await fetchTaskBackup(connection, asJson: asJson);
      if (!current()) return;
      try {
        await save(backup);
      } catch (_) {
        throw StateError(
          'Could not save the task backup. Check browser download permissions and retry the export.',
        );
      }
      if (current())
        message = backup.downloadUri == null
            ? (asJson ? taskJsonNotice : taskBackupNotice)
            : taskBackupStartedNotice;
    } catch (error) {
      if (current()) {
        failed = true;
        message = error is StateError
            ? '${error.message}'
            : error is DashboardRequestException
            ? error.message
            : error is TimeoutException
            ? 'Task export timed out. Retry on a faster connection or use the streaming task export API.'
            : 'Could not export the task backup. Check gateway connectivity and retry.';
      }
    } finally {
      if (current()) {
        busy = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _operation++;
    connection.operator.removeListener(_sessionChanged);
    _subscription?.cancel();
    super.dispose();
  }
}
