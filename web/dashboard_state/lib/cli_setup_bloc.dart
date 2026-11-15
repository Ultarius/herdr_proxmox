import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'cli_setup_session.dart';

class CliSetupState extends BlocState {
  const CliSetupState({
    this.clis = const [],
    this.integrations,
    this.session,
    this.selected,
    this.error,
    this.message,
    this.running = false,
    this.busy = false,
    this.refreshing = false,
    this.loaded = false,
    this.output = '',
    this.outputRevision = 0,
    this.clearRevision = 0,
  });
  final List<Map<String, dynamic>> clis;
  final Map<String, dynamic>? integrations;
  final String? session, selected, error, message;
  final bool running, busy, refreshing, loaded;
  final String output;
  final int outputRevision, clearRevision;
}

class CliSetupRefresh extends EventBase {}

class CliSetupAction extends EventBase {
  CliSetupAction(this.action, this.cli);
  final String action, cli;
}

class CliSetupStop extends EventBase {}

class _CliPoll extends EventBase {}

class _CliSessionChanged extends EventBase {}

class _CliWrite extends EventBase {
  _CliWrite(this.action, this.body, this.revision);
  final String action;
  final Map<String, dynamic> body;
  final int revision;
}

/// Owns provider metadata, remote setup session, output cursor and serialized
/// terminal input. Emulator rendering and clipboard feedback remain in the UI.
class CliSetupBloc extends JuiceBloc<CliSetupState> {
  CliSetupBloc(this.connection)
    : super(const CliSetupState(), [
        () => UseCaseBuilder.typed(
          () => _RefreshCli(),
          // The loading guard drops ordinary duplicate reads; concurrent
          // dispatch still admits a fresh session while an old read settles.
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _ActCli(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _PollCli(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _WriteCli(),
          concurrency: EventConcurrency.sequential,
        ),
        () => UseCaseBuilder.typed(
          () => _StopCli(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _ResetCli(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]) {
    _owner = CliSetupSession(connection);
    _sessionStamp = session;
    bindStream(connection.stream, (_) {
      if (_sessionStamp != session) {
        _sessionChanged();
      }
    }, key: 'connection');
    connection.operator.addListener(_sessionChanged);
    bindStream(
      Stream.periodic(const Duration(milliseconds: 300)),
      (_) => send(_CliPoll()),
      key: 'poll',
    );
    refresh();
  }
  final DashboardBloc connection;
  late final CliSetupSession _owner;
  int _sessionRevision = 0, _revision = 0, _cursor = 0;
  int _outputRevision = 0, _clearRevision = 0;
  List<Map<String, dynamic>> _clis = [];
  Map<String, dynamic>? _integrations;
  String? _session, _selected, _error, _message;
  String _output = '';
  bool _running = false, _busy = false, _refreshing = false, _loaded = false;
  bool _polling = false;
  String _sessionStamp = '';
  String get session =>
      '${connection.generation}:${connection.state.connected}:${connection.operator.value['role']}';
  void _sessionChanged() {
    if (isClosing || _sessionStamp == session) return;
    _sessionStamp = session;
    _sessionRevision++;
    send(_CliSessionChanged());
  }

  bool _current(int epoch) =>
      !isClosing &&
      connection.state.connected &&
      !connection.disconnecting &&
      epoch == _sessionRevision &&
      _sessionStamp == session;
  void _publish(void Function(CliSetupState) emit) => emit(
    CliSetupState(
      clis: List.unmodifiable(_clis),
      integrations: _integrations,
      session: _session,
      selected: _selected,
      error: _error,
      message: _message,
      running: _running,
      busy: _busy,
      refreshing: _refreshing,
      loaded: _loaded,
      output: _output,
      outputRevision: _outputRevision,
      clearRevision: _clearRevision,
    ),
  );
  Future<void> refresh() => send(CliSetupRefresh());
  Future<void> openSession(String cli) => send(CliSetupAction('start', cli));
  Future<void> action(String action, String cli) =>
      send(CliSetupAction(action, cli));
  Future<void> stop() => send(CliSetupStop());
  Future<void> input(String value) =>
      _session == null || !_running || value.isEmpty
      ? Future.value()
      : send(_CliWrite('input', {'id': _session, 'data': value}, _revision));
  Future<void> resize(int cols, int rows) => _session == null || !_running
      ? Future.value()
      : send(
          _CliWrite('resize', {
            'id': _session,
            'cols': cols.clamp(10, 400),
            'rows': rows.clamp(5, 150),
          }, _revision),
        );

  Future<void> _refresh(void Function(CliSetupState) emit) async {
    if (_refreshing || !_current(_sessionRevision)) return;
    final epoch = _sessionRevision;
    _refreshing = true;
    _error = null;
    _publish(emit);
    try {
      final data = await connection.request('cli-setup');
      if (!_current(epoch)) return;
      _clis = List<Map<String, dynamic>>.from(data['clis']);
      _integrations = data['integrations'] is Map
          ? Map<String, dynamic>.from(data['integrations'])
          : null;
      _loaded = true;
    } catch (error) {
      if (_current(epoch)) _error = '$error';
    } finally {
      if (_current(epoch)) {
        _refreshing = false;
        _publish(emit);
      }
    }
  }

  Future<void> _action(
    CliSetupAction event,
    void Function(CliSetupState) emit,
  ) async {
    if (_busy ||
        _session != null ||
        _owner.closing ||
        !_current(_sessionRevision))
      return;
    final revision = ++_revision, epoch = _sessionRevision;
    _busy = true;
    _error = _message = null;
    _publish(emit);
    try {
      if (event.action == 'start') {
        final data = await _owner.start(event.cli);
        if (data == null) return;
        if (!_current(epoch) || revision != _revision) {
          await _owner.close();
          return;
        }
        _session = data['id'] as String;
        _selected = event.cli;
        _running = true;
        _cursor = 0;
        _output = '';
        _clearRevision++;
        _publish(emit);
        await _poll(emit);
      } else {
        final data = await connection.request('cli-setup/${event.action}', {
          'cli': event.cli,
        });
        if (!_current(epoch) || revision != _revision) return;
        _message =
            '${data['detail'] ?? (event.action == 'verify' ? 'Account verified.' : 'Herdr integration installed.')}';
        await _refresh(emit);
      }
    } catch (error) {
      if (_current(epoch) && revision == _revision) _error = '$error';
    } finally {
      if (_current(epoch) && revision == _revision) {
        _busy = false;
        _publish(emit);
      }
    }
  }

  Future<void> _poll(void Function(CliSetupState) emit) async {
    final id = _session, epoch = _sessionRevision, revision = _revision;
    if (id == null ||
        _polling ||
        !_running ||
        !_current(epoch) ||
        _owner.closing)
      return;
    _polling = true;
    try {
      final data = await connection.request('cli-setup/poll', {
        'id': id,
        'cursor': _cursor,
      });
      if (!_current(epoch) || id != _session || revision != _revision) return;
      _output =
          '${data['truncated'] == true ? '\x1b[2J\x1b[H[Earlier output expired]\r\n' : ''}${data['output']}';
      _cursor = data['cursor'] as int;
      if (data['running'] == false) {
        _output += '\r\n[Setup command exited: ${data['exit_code']}]\r\n';
        _running = false;
      }
      _outputRevision++;
      _publish(emit);
      if (!_running) await _refresh(emit);
    } catch (error) {
      if (_current(epoch) && id == _session && revision == _revision) {
        _error = '$error';
        _running = false;
        _publish(emit);
      }
    } finally {
      if (revision == _revision) _polling = false;
    }
  }

  Future<void> _write(
    _CliWrite event,
    void Function(CliSetupState) emit,
  ) async {
    final epoch = _sessionRevision;
    if (event.revision != _revision ||
        !_current(epoch) ||
        _owner.closing ||
        event.body['id'] != _session)
      return;
    try {
      await connection.request('cli-setup/${event.action}', event.body);
    } catch (error) {
      if (_current(epoch) && event.revision == _revision) {
        _error = '$error';
        _publish(emit);
      }
    }
  }

  Future<void> _stop(void Function(CliSetupState) emit) async {
    final epoch = _sessionRevision;
    _revision++;
    _session = _selected = null;
    _running = _busy = false;
    _polling = false;
    _output = '';
    _clearRevision++;
    _publish(emit);
    await _owner.close();
    if (_current(epoch)) await _refresh(emit);
  }

  void _reset(void Function(CliSetupState) emit) {
    unawaited(_owner.close());
    _revision++;
    _clis = [];
    _integrations = null;
    _session = _selected = _error = _message = null;
    _running = _busy = _refreshing = _loaded = false;
    _polling = false;
    _output = '';
    _clearRevision++;
    _publish(emit);
    if (connection.state.connected) refresh();
  }

  @override
  Future<void> close() {
    connection.operator.removeListener(_sessionChanged);
    _sessionRevision++;
    _revision++;
    _owner.dispose();
    return super.close();
  }
}

class _RefreshCli extends BlocUseCase<CliSetupBloc, CliSetupRefresh> {
  @override
  Future<void> execute(CliSetupRefresh event) =>
      bloc._refresh((s) => emitUpdate(newState: s));
}

class _ActCli extends BlocUseCase<CliSetupBloc, CliSetupAction> {
  @override
  Future<void> execute(CliSetupAction event) =>
      bloc._action(event, (s) => emitUpdate(newState: s));
}

class _PollCli extends BlocUseCase<CliSetupBloc, _CliPoll> {
  @override
  Future<void> execute(_CliPoll event) =>
      bloc._poll((s) => emitUpdate(newState: s));
}

class _WriteCli extends BlocUseCase<CliSetupBloc, _CliWrite> {
  @override
  Future<void> execute(_CliWrite event) =>
      bloc._write(event, (s) => emitUpdate(newState: s));
}

class _StopCli extends BlocUseCase<CliSetupBloc, CliSetupStop> {
  @override
  Future<void> execute(CliSetupStop event) =>
      bloc._stop((s) => emitUpdate(newState: s));
}

class _ResetCli extends BlocUseCase<CliSetupBloc, _CliSessionChanged> {
  @override
  Future<void> execute(_CliSessionChanged event) async =>
      bloc._reset((s) => emitUpdate(newState: s));
}
