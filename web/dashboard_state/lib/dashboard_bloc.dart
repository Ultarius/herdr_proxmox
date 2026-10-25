import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:juice/juice.dart';

class DashboardRequestException implements Exception {
  const DashboardRequestException(
    this.statusCode,
    this.message, {
    this.fieldErrors = const {},
  });
  final int statusCode;
  final String message;
  final Map<String, String> fieldErrors;
  @override
  String toString() => 'Exception: $message';
}

class DashboardState extends BlocState {
  const DashboardState({
    this.connected = false,
    this.busy = false,
    this.error,
    this.workspaces = const [],
    this.agents = const [],
    this.refreshed,
    this.serverStatus = 'unknown',
  });
  final bool connected;
  final bool busy;
  final String? error;
  final List<Map<String, dynamic>> workspaces;
  final List<Map<String, dynamic>> agents;
  final DateTime? refreshed;
  final String serverStatus;
}

class DashboardCommand extends EventBase {
  factory DashboardCommand(
    String action, [
    Map<String, dynamic>? body,
    bool background = false,
  ]) => action == 'refresh'
      ? _DashboardRefresh(body, background)
      : DashboardCommand._(action, body, background);

  DashboardCommand._(this.action, this.body, this.background);
  final bool background;
  final String action;
  final Map<String, dynamic>? body;
}

// Separate runtime event types give reads and mutations independent admission.
class _DashboardRefresh extends DashboardCommand {
  _DashboardRefresh(Map<String, dynamic>? body, bool background)
    : super._('refresh', body, background);
}

class DisconnectDashboard extends EventBase {
  DisconnectDashboard([this.error]);
  final String? error;
}

class DisconnectUseCase
    extends BlocUseCase<DashboardBloc, DisconnectDashboard> {
  @override
  Future<void> execute(DisconnectDashboard event) async {
    emitUpdate(newState: DashboardState(error: event.error));
  }
}

typedef DisconnectCleanupRequest =
    Future<dynamic> Function(String path, [Map<String, dynamic>? body]);

class _DisconnectCleanupPermit {
  const _DisconnectCleanupPermit(this.connection, this.generation);
  final DashboardBloc connection;
  final int generation;
}

class DashboardBloc extends JuiceBloc<DashboardState> {
  DashboardBloc({http.Client? client})
    : client = client ?? http.Client(),
      super(const DashboardState(), [
        () => UseCaseBuilder.typed(
          () => DashboardUseCase(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _DashboardRefreshUseCase(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => DisconnectUseCase(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]);
  final http.Client client;
  String? _credential;
  int generation = 0;
  bool get browserSession => _credential == '' && state.connected;
  Timer? _timer;
  Timer? _notificationTimer;
  int _mutationRevision = 0;
  int? _mutationGeneration;
  final _disconnectCleanups = <Future<void> Function()>{};
  static final _disconnectCleanupZone = Object();
  Future<void>? _disconnectFuture;
  bool get disconnecting => _disconnectFuture != null;

  /// Register owned-resource teardown and capture its session-scoped transport.
  /// The returned capability also works when teardown began before sign-out.
  DisconnectCleanupRequest addDisconnectCleanup(
    Future<void> Function() cleanup,
  ) {
    _disconnectCleanups.add(cleanup);
    final permit = _DisconnectCleanupPermit(this, generation);
    return (String path, [Map<String, dynamic>? body]) {
      if (!_disconnectCleanups.contains(cleanup) ||
          permit.generation != generation) {
        return Future<dynamic>.error(
          StateError('Dashboard cleanup registration is no longer current.'),
        );
      }
      return runZoned(
        () => request(path, body),
        zoneValues: {_disconnectCleanupZone: permit},
      );
    };
  }

  void removeDisconnectCleanup(Future<void> Function() cleanup) =>
      _disconnectCleanups.remove(cleanup);

  void _checkRequestAdmission() {
    if (isClosing) throw StateError('Dashboard connection is closed.');
    final permit = Zone.current[_disconnectCleanupZone];
    final cleanup =
        permit is _DisconnectCleanupPermit &&
        identical(permit.connection, this);
    if (cleanup && permit.generation != generation)
      throw StateError('Dashboard cleanup session is no longer current.');
    if (disconnecting && !cleanup)
      throw StateError('Dashboard is signing out.');
  }

  final notificationRevision = ValueNotifier<int>(0);
  final runtimeEvents = ValueNotifier<Map<String, dynamic>>({});
  String _notificationEpoch = '';
  int _notificationCursor = 0;
  final selectedOrganization = ValueNotifier<String?>(null);
  final operator = ValueNotifier<Map<String, dynamic>>({
    'name': 'dashboard',
    'role': 'admin',
  });
  final organizationDirectory = ValueNotifier<Map<String, dynamic>>({
    'organizations': [],
    'profiles': [],
  });

  void _rememberOperator(dynamic session) {
    if (session is Map<String, dynamic>) {
      operator.value = {
        'name': session['operator'] ?? 'dashboard',
        'role': session['role'] ?? 'admin',
      };
    }
  }

  Future<void> signIn(String token) async {
    if (isClosing || disconnecting) return;
    final attempt = ++generation;
    try {
      final response = await client
          .post(
            Uri.base.resolve('/api/session'),
            headers: {'Authorization': 'Bearer $token'},
          )
          .timeout(const Duration(seconds: 15));
      if (attempt != generation || isClosing) return;
      final session = jsonDecode(response.body);
      if (response.statusCode != 200 || session['authenticated'] != true) {
        throw Exception(session['error'] ?? 'Sign in failed.');
      }
      _rememberOperator(session);
      connect('');
    } catch (error) {
      if (attempt == generation && !isClosing)
        await send(DisconnectDashboard(error.toString()));
    }
  }

  Future<void> restoreSession() async {
    if (isClosing || disconnecting) return;
    final attempt = generation;
    try {
      final response = await client
          .get(Uri.base.resolve('/api/session'))
          .timeout(const Duration(seconds: 15));
      final session = jsonDecode(response.body);
      if (attempt == generation &&
          !isClosing &&
          response.statusCode == 200 &&
          session['authenticated'] == true) {
        _rememberOperator(session);
        connect('');
      }
    } catch (_) {
      // Leave the token form available when the gateway is unreachable.
    }
  }

  void connect(String token) {
    if (isClosing || disconnecting) return;
    _credential = token;
    generation++;
    send(DashboardCommand('refresh'));
    _notificationTimer?.cancel();
    _notificationEpoch = '';
    _notificationCursor = 0;
    final attempt = generation;
    _notificationTimer = Timer(
      const Duration(milliseconds: 100),
      () => _notifications(attempt),
    );
    _timer?.cancel();
    _timer = Timer.periodic(
      const Duration(seconds: 5),
      (_) => send(DashboardCommand('refresh', null, true)),
    );
  }

  Future<void> _notifications(int attempt) async {
    if (attempt != generation ||
        _credential == null ||
        isClosing ||
        disconnecting)
      return;
    var delay = const Duration(milliseconds: 100);
    try {
      final response = await client
          .get(
            Uri.base.resolve(
              '/api/notifications?after=$_notificationCursor&epoch=${Uri.encodeQueryComponent(_notificationEpoch)}',
            ),
            headers: {
              if (_credential!.isNotEmpty)
                'Authorization': 'Bearer $_credential',
            },
          )
          .timeout(const Duration(seconds: 25));
      if (attempt != generation ||
          _credential == null ||
          isClosing ||
          disconnecting)
        return;
      if (response.statusCode != 200)
        throw StateError('Notification feed unavailable');
      final data = jsonDecode(response.body);
      // Older gateways keep the periodic refresh path. No busy retry loop.
      if (data is! Map || data['epoch'] is! String || data['revision'] is! int)
        return;
      final changed =
          data['reset'] == true || data['revision'] != _notificationCursor;
      _notificationEpoch = data['epoch'];
      _notificationCursor = data['revision'];
      if (data['runtime'] is Map)
        runtimeEvents.value = Map<String, dynamic>.from(data['runtime']);
      if (changed) {
        notificationRevision.value++;
        send(DashboardCommand('refresh', null, true));
      }
    } catch (_) {
      if (attempt != generation ||
          _credential == null ||
          isClosing ||
          disconnecting)
        return;
      delay = const Duration(seconds: 5);
    }
    _notificationTimer = Timer(delay, () => _notifications(attempt));
  }

  Future<void> disconnect() {
    if (_disconnectFuture case final pending?) return pending;
    if (isClosing) return Future<void>.value();
    final completion = Completer<void>();
    final pending = completion.future;
    _disconnectFuture = pending;
    _disconnect().then(
      (_) {
        _disconnectFuture = null;
        completion.complete();
      },
      onError: (Object error, StackTrace stack) {
        _disconnectFuture = null;
        completion.completeError(error, stack);
      },
    );
    return pending;
  }

  Future<void> _disconnect() async {
    // Cleanup still needs the current credential. Bound the combined wait so
    // a failed remote session cannot indefinitely prevent local sign-out.
    _notificationTimer?.cancel();
    _timer?.cancel();
    final cleanups = List<Future<void> Function()>.of(_disconnectCleanups);
    try {
      await Future.wait([
        for (final cleanup in cleanups)
          runZoned(
            () => Future<void>.sync(cleanup),
            zoneValues: {
              _disconnectCleanupZone: _DisconnectCleanupPermit(
                this,
                generation,
              ),
            },
          ).catchError((Object _) {}),
      ]).timeout(const Duration(seconds: 20));
    } catch (_) {}
    if (isClosing) return;
    final browserSession = _credential == '';
    _notificationTimer?.cancel();
    _timer?.cancel();
    runtimeEvents.value = {};
    _credential = null;
    selectedOrganization.value = null;
    operator.value = {'name': 'dashboard', 'role': 'admin'};
    organizationDirectory.value = {'organizations': [], 'profiles': []};
    generation++;
    // Reset immediately, including when an old request is still in flight.
    await send(DisconnectDashboard());
    if (browserSession) {
      final attempt = generation;
      try {
        final response = await client
            .post(Uri.base.resolve('/api/session/logout'))
            .timeout(const Duration(seconds: 15));
        if (response.statusCode != 200) throw StateError('Sign out failed');
      } catch (_) {
        if (attempt == generation && !isClosing) {
          await send(
            DisconnectDashboard(
              'Could not revoke the browser session. Reconnect and sign out when the gateway is available.',
            ),
          );
        }
      }
    }
  }

  Future<dynamic> request(String path, [Map<String, dynamic>? body]) =>
      _request(path, body);

  /// Fence an ordered read before it can update shared directory observations.
  Future<dynamic> requestRead(
    String path, {
    required bool Function() isCurrent,
  }) => _request(path, null, isCurrent: isCurrent);

  Future<dynamic> _request(
    String path,
    Map<String, dynamic>? body, {
    bool Function()? isCurrent,
  }) async {
    _checkRequestAdmission();
    final requestGeneration = generation;
    final uri = Uri.base.resolve('/api/$path');
    final headers = {
      if (_credential != null && _credential!.isNotEmpty)
        'Authorization': 'Bearer $_credential',
      'Content-Type': 'application/json',
    };
    final response =
        await (body == null
                ? client.get(uri, headers: headers)
                : client.post(uri, headers: headers, body: jsonEncode(body)))
            .timeout(
              Duration(
                seconds:
                    path.startsWith('tasks/') ||
                        path == 'github/configure' ||
                        path == 'cli-setup/verify' ||
                        path == 'organizations/templates' ||
                        (path == 'organizations/save' &&
                            (body?['template_roles'] as List? ?? [])
                                .isNotEmpty) ||
                        path == 'projects/clone' ||
                        path == 'projects/jobs/remove' ||
                        path == 'projects/connections' ||
                        (path == 'projects/git' &&
                            ['fetch', 'update_base'].contains(body?['action']))
                    ? 135
                    : path == 'models'
                    ? 35
                    : 15,
              ),
            );
    if (requestGeneration != generation || _credential == null)
      throw StateError('Dashboard connection changed.');
    if (isCurrent != null && !isCurrent())
      throw StateError('Dashboard read was superseded.');
    final data = jsonDecode(response.body);
    if (response.statusCode == 401 && _credential == '') {
      _timer?.cancel();
      _credential = null;
      generation++;
      selectedOrganization.value = null;
      organizationDirectory.value = {'organizations': [], 'profiles': []};
      await send(
        DisconnectDashboard(
          'Your session expired. Enter the dashboard token to reconnect.',
        ),
      );
      throw StateError('Dashboard session expired.');
    }
    if (response.statusCode != 200)
      throw DashboardRequestException(
        response.statusCode,
        '${data['error'] ?? 'Request failed'}',
        fieldErrors: {
          if (data['field_errors'] case final Map fields)
            for (final entry in fields.entries)
              if (entry.key is String && entry.value is String)
                entry.key as String: entry.value as String,
        },
      );
    if ([
          'organizations',
          'organizations/directory',
          'organizations/state',
        ].contains(path) &&
        body == null &&
        data is Map<String, dynamic> &&
        data['organizations'] is List &&
        data['profiles'] is List &&
        _credential != null &&
        requestGeneration == generation) {
      final directory = {
        'organizations': data['organizations'],
        'profiles': data['profiles'],
        'groups': data['groups'] ?? [],
        'archived_groups': data['archived_groups'] ?? [],
        'loaded': true,
      };
      if (jsonEncode(directory) != jsonEncode(organizationDirectory.value)) {
        organizationDirectory.value = directory;
      }
    }
    return data;
  }

  Future<Uint8List> bytes(String path) async {
    _checkRequestAdmission();
    final requestGeneration = generation;
    final response = await client
        .get(
          Uri.base.resolve('/api/$path'),
          headers: {
            if (_credential != null && _credential!.isNotEmpty)
              'Authorization': 'Bearer $_credential',
          },
        )
        .timeout(const Duration(seconds: 30));
    if (requestGeneration != generation || _credential == null)
      throw StateError('Dashboard connection changed.');
    if (response.statusCode != 200) {
      var message = 'Request failed';
      try {
        message = jsonDecode(response.body)['error'] ?? message;
      } catch (_) {}
      throw Exception(message);
    }
    return response.bodyBytes;
  }

  /// Bounded downloads keep headers (including the authoritative filename)
  /// and reject oversized bodies before collecting them in browser memory.
  Future<http.Response> download(
    String path, {
    required int maximumBytes,
    Duration timeout = const Duration(minutes: 10),
  }) async {
    _checkRequestAdmission();
    final epoch = generation;
    final request = http.Request('GET', Uri.base.resolve('/api/$path'));
    if (_credential?.isNotEmpty == true)
      request.headers['Authorization'] = 'Bearer $_credential';
    final response = await client.send(request).timeout(timeout);
    final limit = response.statusCode == 200 ? maximumBytes : 64 * 1024;
    if ((response.contentLength ?? 0) > limit) {
      await response.stream.listen(null).cancel();
      throw StateError(
        'This backup exceeds the browser download limit. Use a streaming HTTP client with the task export API.',
      );
    }
    final content = BytesBuilder(copy: false);
    final completed = Completer<void>();
    StreamSubscription<List<int>>? subscription;
    Timer? idle;
    void fail(Object error, [StackTrace? stack]) {
      if (completed.isCompleted) return;
      idle?.cancel();
      subscription?.cancel().ignore();
      completed.completeError(error, stack);
    }

    void resetTimeout() {
      idle?.cancel();
      idle = Timer(
        timeout,
        () => fail(TimeoutException('Task backup transfer stalled', timeout)),
      );
    }

    resetTimeout();
    subscription = response.stream.listen(
      (chunk) {
        if (completed.isCompleted) return;
        if (epoch != generation || _credential == null) {
          fail(StateError('Dashboard connection changed.'));
        } else if (content.length + chunk.length > limit) {
          fail(
            StateError(
              'This backup exceeds the browser download limit. Use a streaming HTTP client with the task export API.',
            ),
          );
        } else {
          content.add(chunk);
          resetTimeout();
        }
      },
      onError: fail,
      onDone: () {
        idle?.cancel();
        if (!completed.isCompleted) completed.complete();
      },
      cancelOnError: true,
    );
    await completed.future;
    if (epoch != generation || _credential == null)
      throw StateError('Dashboard connection changed.');
    final result = http.Response.bytes(
      content.takeBytes(),
      response.statusCode,
      headers: response.headers,
    );
    if (result.statusCode != 200) {
      var message = 'Task export failed. Retry the export.';
      try {
        message = jsonDecode(result.body)['error'] ?? message;
      } catch (_) {}
      throw DashboardRequestException(result.statusCode, message);
    }
    return result;
  }

  Future<String> text(String path) async => utf8.decode(await bytes(path));

  @override
  Future<void> close() {
    // Scope teardown and dispose() both enter this awaitable ownership boundary.
    if (!isClosing) {
      _notificationTimer?.cancel();
      _timer?.cancel();
      _credential = null;
      generation++;
      _disconnectCleanups.clear();
      client.close();
    }
    return super.close();
  }
}

class DashboardUseCase extends _DashboardOperation<DashboardCommand> {}

class _DashboardRefreshUseCase extends _DashboardOperation<_DashboardRefresh> {}

abstract class _DashboardOperation<T extends DashboardCommand>
    extends BlocUseCase<DashboardBloc, T> {
  @override
  Future<void> execute(T event) async {
    if (bloc._credential == null || bloc.disconnecting) return;
    final generation = bloc.generation;
    final refresh = event.action == 'refresh';
    if (refresh && bloc._mutationGeneration == generation) return;
    final revision = refresh
        ? bloc._mutationRevision
        : ++bloc._mutationRevision;
    if (!refresh) bloc._mutationGeneration = generation;
    bool current() =>
        generation == bloc.generation &&
        revision == bloc._mutationRevision &&
        !bloc.isClosing &&
        !bloc.disconnecting;
    final previous = bloc.state;
    if (!event.background)
      emitUpdate(
        newState: DashboardState(
          connected: true,
          busy: true,
          workspaces: previous.workspaces,
          agents: previous.agents,
          refreshed: previous.refreshed,
          serverStatus: previous.serverStatus,
        ),
      );
    try {
      if (event.action == 'start-server') {
        await bloc.request('herdr-server/start', {});
      } else if (!refresh) {
        await bloc.request('workspaces/${event.action}', event.body);
      }
      if (!current()) return;
      final data = await bloc.requestRead('snapshot', isCurrent: current);
      if (!current()) return;
      if (event.background &&
          previous.error == null &&
          jsonEncode(data['workspaces']) == jsonEncode(previous.workspaces) &&
          jsonEncode(data['agents']) == jsonEncode(previous.agents) &&
          (data['herdr_server'] ?? 'running') == previous.serverStatus)
        return;
      emitUpdate(
        newState: DashboardState(
          connected: true,
          workspaces: List<Map<String, dynamic>>.from(data['workspaces']),
          agents: List<Map<String, dynamic>>.from(data['agents']),
          refreshed: DateTime.now(),
          serverStatus: data['herdr_server'] as String? ?? 'running',
        ),
      );
    } catch (e) {
      if (!current()) return;
      emitUpdate(
        newState: DashboardState(
          connected: true,
          error: e.toString(),
          workspaces: previous.workspaces,
          agents: previous.agents,
          refreshed: previous.refreshed,
          serverStatus: previous.serverStatus,
        ),
      );
    } finally {
      if (!refresh && revision == bloc._mutationRevision)
        bloc._mutationGeneration = null;
    }
  }
}
