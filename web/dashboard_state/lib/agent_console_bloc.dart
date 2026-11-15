import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';

Duration agentConsoleRefreshInterval(String? status) => switch (status) {
  'working' || 'running' || 'busy' || 'thinking' => const Duration(seconds: 1),
  'idle' || 'done' => const Duration(seconds: 5),
  _ => const Duration(seconds: 2),
};

class AgentConsoleState extends BlocState {
  const AgentConsoleState({
    this.profile,
    this.inspection,
    this.jobs = const [],
    this.error,
    this.responseError,
    this.inputJob,
    this.pendingInput,
    this.loading = false,
    this.sending = false,
  });
  final Map<String, dynamic>? profile, inspection;
  final List<Map<String, dynamic>> jobs;
  final String? error, responseError, inputJob;
  final OrganizationCommand? pendingInput;
  final bool loading, sending;
}

class AgentConsoleRefresh extends EventBase {}

class AgentConsoleResponse extends EventBase {
  AgentConsoleResponse([this.key]);
  final String? key;
}

class _ConsoleSessionChanged extends EventBase {}

/// Owns inspected output, response replay identity and adaptive polling for one
/// agent. Renderers only own their emulator, scroll position and disclosures.
class AgentConsoleBloc extends JuiceBloc<AgentConsoleState> {
  AgentConsoleBloc(this.connection, this.profileId)
    : super(const AgentConsoleState(), [
        () => UseCaseBuilder.typed(
          () => _RefreshConsole(),
          // The loading guard drops ordinary duplicate reads; concurrent
          // dispatch still admits a fresh session while an old read settles.
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _RespondConsole(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _ResetConsole(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]) {
    _session = session;
    bindStream(connection.stream, (_) {
      if (_session != session) {
        _sessionChanged();
      } else {
        refresh();
      }
    }, key: 'connection');
    connection.operator.addListener(_sessionChanged);
    refresh();
  }
  final DashboardBloc connection;
  final String profileId;
  Timer? _timer;
  int _sessionRevision = 0;
  int _readRevision = 0;
  DateTime? _metadataReadAt;
  bool _trackDelivery = false;
  Map<String, dynamic>? _profile, _inspection;
  List<Map<String, dynamic>> _jobs = [];
  String? _error, _responseError, _inputJob;
  OrganizationCommand? _pendingInput;
  bool _loading = false, _sending = false;

  String _session = '';
  String get session =>
      '${connection.generation}:${connection.state.connected}:${connection.operator.value['role']}';
  void _sessionChanged() {
    if (isClosing || _session == session) return;
    _session = session;
    _sessionRevision++;
    send(_ConsoleSessionChanged());
  }

  bool _current(int epoch) =>
      !isClosing &&
      connection.state.connected &&
      !connection.disconnecting &&
      epoch == _sessionRevision &&
      _session == session;
  void _publish(void Function(AgentConsoleState) emit) => emit(
    AgentConsoleState(
      profile: _profile,
      inspection: _inspection,
      jobs: List.unmodifiable(_jobs),
      error: _error,
      responseError: _responseError,
      inputJob: _inputJob,
      pendingInput: _pendingInput,
      loading: _loading,
      sending: _sending,
    ),
  );
  Future<void> refresh() => send(AgentConsoleRefresh());
  Future<void> sendKey(String key) => send(AgentConsoleResponse(key));
  Future<void> retryResponse() => send(AgentConsoleResponse());

  void _reset(void Function(AgentConsoleState) emit) {
    _timer?.cancel();
    _readRevision++;
    _profile = _inspection = null;
    _jobs = [];
    _error = _responseError = _inputJob = null;
    _pendingInput = null;
    _trackDelivery = _loading = _sending = false;
    _metadataReadAt = null;
    _publish(emit);
    if (connection.state.connected) refresh();
  }

  Future<void> _refresh(void Function(AgentConsoleState) emit) async {
    if (_loading || _sending || !_current(_sessionRevision)) return;
    _timer?.cancel();
    _loading = true;
    final epoch = _sessionRevision;
    final revision = ++_readRevision;
    bool current() => _current(epoch) && revision == _readRevision;
    try {
      if (_profile == null ||
          _trackDelivery ||
          _metadataReadAt == null ||
          DateTime.now().difference(_metadataReadAt!) >=
              const Duration(minutes: 1)) {
        final data = Map<String, dynamic>.from(
          await connection.requestRead(
            'organizations/state',
            isCurrent: current,
          ),
        );
        if (!current()) return;
        _profile = List<Map<String, dynamic>>.from(
          data['profiles'],
        ).where((p) => p['id'] == profileId).firstOrNull;
        _metadataReadAt = DateTime.now();
        _jobs = List<Map<String, dynamic>>.from(data['jobs'])
            .where(
              (j) =>
                  j['profile_id'] == profileId ||
                  (j['participants'] as List? ?? []).contains(profileId),
            )
            .toList();
        final delivery = _jobs.where((j) => j['id'] == _inputJob).firstOrNull;
        if (delivery != null &&
            !['queued', 'running'].contains(delivery['state']))
          _trackDelivery = false;
        _publish(emit);
        if (_profile == null) {
          _inspection = null;
          _error = 'Agent was not found.';
          return;
        }
        connection.selectedOrganization.value =
            _profile!['organization_id'] as String;
      }
      final result = await OrganizationCommand('inspect', {
        'organization_id': _profile!['organization_id'],
        'profile_id': profileId,
      }).execute(connection);
      if (!current() || _sending) return;
      _inspection = Map<String, dynamic>.from(result);
      _error = null;
    } catch (error) {
      if (current()) {
        _error = '$error';
        _inspection = null;
      }
    } finally {
      if (current()) {
        _loading = false;
        _publish(emit);
        _timer = Timer(
          agentConsoleRefreshInterval(_inspection?['status'] as String?),
          refresh,
        );
      }
    }
  }

  Future<void> _respond(
    String? key,
    void Function(AgentConsoleState) emit,
  ) async {
    if (_sending || !_current(_sessionRevision)) return;
    if (key != null) {
      final delivery = _jobs.where((job) => job['id'] == _inputJob).firstOrNull;
      if (_inputJob != null &&
          (delivery == null ||
              ['queued', 'running'].contains(delivery['state'])))
        return;
      if (_pendingInput != null ||
          _inspection?['status'] != 'blocked' ||
          _inspection?['console_token'] == null)
        return;
      _pendingInput = OrganizationCommand('input', {
        'organization_id': _profile!['organization_id'],
        'profile_id': profileId,
        'key': key,
        'console_token': _inspection!['console_token'],
      });
    }
    if (_pendingInput == null) return;
    final epoch = _sessionRevision;
    _readRevision++;
    _loading = false;
    _sending = true;
    _responseError = null;
    _publish(emit);
    try {
      final result = await _pendingInput!.execute(connection);
      if (!_current(epoch)) return;
      _inputJob = result['id'] as String;
      _trackDelivery = true;
      _inspection = null;
      _pendingInput = null;
    } catch (error) {
      if (_current(epoch)) {
        if (error is DashboardRequestException &&
            error.statusCode >= 400 &&
            error.statusCode < 500)
          _pendingInput = null;
        _responseError = '$error';
      }
    } finally {
      if (_current(epoch)) {
        _sending = false;
        _publish(emit);
      }
    }
    await _refresh(emit);
  }

  @override
  Future<void> close() {
    connection.operator.removeListener(_sessionChanged);
    _sessionRevision++;
    _timer?.cancel();
    return super.close();
  }
}

class _RefreshConsole
    extends BlocUseCase<AgentConsoleBloc, AgentConsoleRefresh> {
  @override
  Future<void> execute(AgentConsoleRefresh event) =>
      bloc._refresh((state) => emitUpdate(newState: state));
}

class _RespondConsole
    extends BlocUseCase<AgentConsoleBloc, AgentConsoleResponse> {
  @override
  Future<void> execute(AgentConsoleResponse event) =>
      bloc._respond(event.key, (state) => emitUpdate(newState: state));
}

class _ResetConsole
    extends BlocUseCase<AgentConsoleBloc, _ConsoleSessionChanged> {
  @override
  Future<void> execute(_ConsoleSessionChanged event) async =>
      bloc._reset((state) => emitUpdate(newState: state));
}
