import 'dart:convert';
import 'dart:math';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

class OrganizationState extends BlocState {
  const OrganizationState({
    this.busy = false,
    this.loaded = false,
    this.error,
    this.organizations = const [],
    this.profiles = const [],
    this.jobs = const [],
    this.groups = const [],
  });
  final bool busy;
  final bool loaded;
  final String? error;
  final List<Map<String, dynamic>> organizations;
  final List<Map<String, dynamic>> profiles;
  final List<Map<String, dynamic>> jobs;
  final List<Map<String, dynamic>> groups;
}

class OrganizationCommand extends EventBase {
  factory OrganizationCommand(String action, [Map<String, dynamic>? body]) =>
      action == 'refresh'
      ? _OrganizationRefresh(body)
      : OrganizationCommand._(action, body);

  OrganizationCommand._(this.action, Map<String, dynamic>? body)
    : body = body == null
          ? null
          : {
              ...body,
              'request_id':
                  '${DateTime.now().microsecondsSinceEpoch}_${Random.secure().nextInt(0x7fffffff)}',
            };
  final String action;
  final Map<String, dynamic>? body;

  Future<dynamic> execute(DashboardBloc connection) =>
      connection.request('organizations/$action', body);
}

class _OrganizationRefresh extends OrganizationCommand {
  _OrganizationRefresh(Map<String, dynamic>? body) : super._('refresh', body);
}

class OrganizationBloc extends JuiceBloc<OrganizationState> {
  OrganizationBloc(this.connection)
    : super(const OrganizationState(), [
        () => UseCaseBuilder.typed(
          () => OrganizationUseCase(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _OrganizationRefreshUseCase(),
          concurrency: EventConcurrency.droppable,
        ),
      ]);
  final DashboardBloc connection;
  OrganizationCommand? pending;
  int _mutationRevision = 0;
  int? _mutationGeneration;
  Timer? _polling;

  /// Mounted organization views opt into live observations; the bloc owns
  /// their cadence and notification subscription until it closes.
  void startPolling() {
    if (isClosing || _polling != null) return;
    connection.notificationRevision.addListener(_refresh);
    _polling = Timer.periodic(const Duration(seconds: 5), (_) => _refresh());
  }

  void _refresh() {
    if (!isClosing && connection.state.connected && pending == null)
      unawaited(send(OrganizationCommand('refresh')));
  }

  @override
  Future<void> close() {
    _polling?.cancel();
    connection.notificationRevision.removeListener(_refresh);
    return super.close();
  }
}

class OrganizationUseCase extends _OrganizationOperation<OrganizationCommand> {}

class _OrganizationRefreshUseCase
    extends _OrganizationOperation<_OrganizationRefresh> {}

abstract class _OrganizationOperation<T extends OrganizationCommand>
    extends BlocUseCase<OrganizationBloc, T> {
  @override
  Future<void> execute(T event) async {
    if (!bloc.connection.state.connected || bloc.connection.disconnecting)
      return;
    final generation = bloc.connection.generation;
    final refresh = event.action == 'refresh';
    if (refresh && bloc._mutationGeneration == generation) return;
    final revision = refresh
        ? bloc._mutationRevision
        : ++bloc._mutationRevision;
    if (!refresh) bloc._mutationGeneration = generation;
    bool current() =>
        generation == bloc.connection.generation &&
        revision == bloc._mutationRevision &&
        !bloc.isClosing &&
        !bloc.connection.disconnecting;
    final old = bloc.state;
    if (!refresh || !old.loaded)
      emitUpdate(
        newState: OrganizationState(
          busy: true,
          loaded: old.loaded,
          organizations: old.organizations,
          profiles: old.profiles,
          jobs: old.jobs,
          groups: old.groups,
        ),
      );
    try {
      if (!refresh) {
        bloc.pending = event;
        await event.execute(bloc.connection);
        if (!current()) return;
        bloc.pending = null;
      }
      if (!current()) return;
      final data = await bloc.connection.requestRead(
        'organizations/state',
        isCurrent: current,
      );
      if (!current()) return;
      if (refresh &&
          old.loaded &&
          old.error == null &&
          jsonEncode(data['organizations']) == jsonEncode(old.organizations) &&
          jsonEncode(data['profiles']) == jsonEncode(old.profiles) &&
          jsonEncode(data['jobs']) == jsonEncode(old.jobs) &&
          jsonEncode(data['groups'] ?? []) == jsonEncode(old.groups))
        return;
      emitUpdate(
        newState: OrganizationState(
          loaded: true,
          organizations: List<Map<String, dynamic>>.from(data['organizations']),
          profiles: List<Map<String, dynamic>>.from(data['profiles']),
          jobs: List<Map<String, dynamic>>.from(data['jobs']),
          groups: List<Map<String, dynamic>>.from(data['groups'] ?? []),
        ),
      );
    } catch (error) {
      if (!current()) return;
      emitUpdate(
        newState: OrganizationState(
          loaded: old.loaded,
          error: error.toString(),
          organizations: old.organizations,
          profiles: old.profiles,
          jobs: old.jobs,
          groups: old.groups,
        ),
      );
    } finally {
      if (!refresh && revision == bloc._mutationRevision)
        bloc._mutationGeneration = null;
    }
  }
}
