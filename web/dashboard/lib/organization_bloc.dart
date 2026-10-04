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
  OrganizationCommand(this.action, [Map<String, dynamic>? body])
    : body = body == null
          ? null
          : {
              ...body,
              'request_id':
                  '${DateTime.now().microsecondsSinceEpoch}_${Random.secure().nextInt(0x7fffffff)}',
            };
  final String action;
  final Map<String, dynamic>? body;
}

class OrganizationBloc extends JuiceBloc<OrganizationState> {
  OrganizationBloc(this.connection)
    : super(const OrganizationState(), [
        () => UseCaseBuilder(
          typeOfEvent: OrganizationCommand,
          useCaseGenerator: () => OrganizationUseCase(),
          concurrency: EventConcurrency.droppable,
        ),
      ]);
  final DashboardBloc connection;
  OrganizationCommand? pending;
}

class OrganizationUseCase
    extends BlocUseCase<OrganizationBloc, OrganizationCommand> {
  @override
  Future<void> execute(OrganizationCommand event) async {
    if (!bloc.connection.state.connected) return;
    final generation = bloc.connection.generation;
    final old = bloc.state;
    if (event.action != 'refresh' || !old.loaded)
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
      if (event.action != 'refresh') {
        bloc.pending = event;
        await bloc.connection.request(
          'organizations/${event.action}',
          event.body,
        );
        bloc.pending = null;
      }
      if (generation != bloc.connection.generation || bloc.isClosing) return;
      final data = await bloc.connection.request('organizations');
      if (generation != bloc.connection.generation || bloc.isClosing) return;
      if (event.action == 'refresh' &&
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
      if (generation != bloc.connection.generation || bloc.isClosing) return;
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
    }
  }
}
