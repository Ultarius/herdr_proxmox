import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:juice/juice.dart';

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
  DashboardCommand(this.action, [this.body, this.background = false]);
  final bool background;
  final String action;
  final Map<String, dynamic>? body;
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

class DashboardBloc extends JuiceBloc<DashboardState> {
  DashboardBloc({http.Client? client})
    : client = client ?? http.Client(),
      super(const DashboardState(), [
        () => UseCaseBuilder(
          typeOfEvent: DashboardCommand,
          useCaseGenerator: () => DashboardUseCase(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder(
          typeOfEvent: DisconnectDashboard,
          useCaseGenerator: () => DisconnectUseCase(),
        ),
      ]);
  final http.Client client;
  String? _credential;
  int generation = 0;
  Timer? _timer;
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
    _credential = token;
    generation++;
    send(DashboardCommand('refresh'));
    _timer?.cancel();
    _timer = Timer.periodic(
      const Duration(seconds: 5),
      (_) => send(DashboardCommand('refresh', null, true)),
    );
  }

  Future<void> disconnect() async {
    final browserSession = _credential == '';
    _timer?.cancel();
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

  Future<dynamic> request(String path, [Map<String, dynamic>? body]) async {
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
                        path == 'projects/clone' ||
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
      throw Exception(data['error'] ?? 'Request failed');
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

  Future<String> text(String path) async => utf8.decode(await bytes(path));

  @override
  void dispose() {
    _timer?.cancel();
    _credential = null;
    generation++;
    client.close();
    super.dispose();
  }
}

class DashboardUseCase extends BlocUseCase<DashboardBloc, DashboardCommand> {
  @override
  Future<void> execute(DashboardCommand event) async {
    if (bloc._credential == null) return;
    final generation = bloc.generation;
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
      } else if (event.action != 'refresh') {
        await bloc.request('workspaces/${event.action}', event.body);
      }
      if (generation != bloc.generation) return;
      final data = await bloc.request('snapshot');
      if (generation != bloc.generation || bloc.isClosing) return;
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
      if (generation != bloc.generation || bloc.isClosing) return;
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
    }
  }
}
