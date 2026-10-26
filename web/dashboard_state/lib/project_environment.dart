import 'package:juice/juice.dart';
import 'feature_bloc.dart';
import 'project_analysis.dart' show ProjectAnalysisRequest;

const activePreparationStates = ['queued', 'running'];

Map<String, dynamic> _object(Object? value, String label) {
  if (value is! Map) throw FormatException('Invalid $label.');
  return Map<String, dynamic>.from(value);
}

List<Map<String, dynamic>> _objects(Object? value, String label) {
  if (value == null) return [];
  if (value is! List) throw FormatException('Invalid $label.');
  return [for (final item in value) _object(item, label)];
}

Map<String, dynamic> _job(Object? value) {
  final job = _object(value, 'preparation job');
  if (job['id'] is! String || job['state'] is! String) {
    throw const FormatException('Invalid preparation job identity.');
  }
  return job;
}

void _requirements(Object? value) {
  for (final item in _objects(value, 'SDK requirements')) {
    if (item['components'] != null &&
        (item['components'] is! List ||
            !(item['components'] as List).every((value) => value is String))) {
      throw const FormatException('Invalid SDK component scopes.');
    }
    _objects(item['restore_recipes'], 'restore recipes');
  }
}

Map<String, dynamic> _plan(Object? value) {
  final plan = _object(value, 'environment plan');
  if (plan['digest'] is! String)
    throw const FormatException('Invalid environment plan identity.');
  _requirements(plan['requirements']);
  _objects(plan['selected_checks'], 'approved checks');
  if (plan['restore_permission'] != null)
    _object(plan['restore_permission'], 'restore permission');
  for (final check in _objects(plan['check_candidates'], 'check proposals')) {
    if (check['arguments'] is! List ||
        !(check['arguments'] as List).every((value) => value is String)) {
      throw const FormatException('Invalid check command.');
    }
  }
  return plan;
}

/// Shared Juice owner for saved status, administrator actions and active runs.
/// The callback constructor keeps this workflow usable by either renderer.
class ProjectEnvironmentController extends JuiceBloc<FeatureState> {
  ProjectEnvironmentController({
    required this.request,
    required this.onUpdate,
    required this.mounted,
    required this.connectionGeneration,
  }) : super(FeatureState(values: _empty), [
         () => UseCaseBuilder.typed(
           () => _LoadEnvironmentUseCase(),
           concurrency: EventConcurrency.concurrent,
         ),
         () => UseCaseBuilder.typed(
           () => _EnvironmentActionUseCase(),
           concurrency: EventConcurrency.droppable,
         ),
         () => UseCaseBuilder.typed(
           () => _PollEnvironmentUseCase(),
           concurrency: EventConcurrency.droppable,
         ),
         () => UseCaseBuilder.typed(
           () => _ResetEnvironmentUseCase(),
           concurrency: EventConcurrency.concurrent,
         ),
       ]) {
    _updates = stream.listen((_) {
      if (!isClosing && mounted()) onUpdate();
    });
  }
  static const _empty = <String, dynamic>{
    'status': null,
    'plan': null,
    'job': null,
    'activeJobs': <Map<String, dynamic>>[],
  };
  final ProjectAnalysisRequest request;
  final void Function() onUpdate;
  final bool Function() mounted;
  final int Function() connectionGeneration;
  late final StreamSubscription _updates;
  Map<String, dynamic>? get status => state.value('status');
  Map<String, dynamic>? get plan => state.value('plan');
  Map<String, dynamic>? get job => state.value('job');
  List<Map<String, dynamic>> get activeJobs =>
      List<Map<String, dynamic>>.from(state.values['activeJobs']);
  String? get lookupError => state.errors['lookup'];
  String? get actionError => state.errors['action'];
  bool get loading => state.reading.contains('load');
  bool get busy => state.busy;
  String? _path;
  int? _epoch;
  int _version = 0, _request = 0, _watch = 0;
  Timer? _timer;

  bool _current(int version, int epoch) =>
      !isClosing &&
      mounted() &&
      version == _version &&
      epoch == connectionGeneration();
  void _stop() {
    _watch++;
    _timer?.cancel();
    _timer = null;
  }

  void reset(String path) {
    _version++;
    _request++;
    _stop();
    _path = path;
    _epoch = connectionGeneration();
    unawaited(send(_ResetEnvironment()));
  }

  Future<void> load(String path) {
    if (isClosing) return Future.value();
    if (_path != path || _epoch != connectionGeneration()) reset(path);
    return send(
      _LoadEnvironment(path, _version, connectionGeneration(), ++_request),
    );
  }

  Future<void> action(String operation, {List<String>? checks, String? jobId}) {
    if (isClosing || busy || loading || _path == null) return Future.value();
    _request++;
    _stop();
    return send(
      _EnvironmentAction(
        operation,
        checks,
        jobId,
        _version,
        connectionGeneration(),
      ),
    );
  }

  Future<Map<String, dynamic>> _observe(String path) async {
    final value = _object(
      await request(
        'projects/environment?${Uri(queryParameters: {'path': path}).query}',
      ),
      'saved environment status',
    );
    if (value['revision'] is! String)
      throw const FormatException('Invalid environment revision.');
    final selected = value['plan'] == null ? null : _plan(value['plan']);
    final latest = value['job'] == null ? null : _job(value['job']);
    final active = _objects(
      value['active_jobs'],
      'active preparations',
    ).map(_job).toList();
    if (!value.containsKey('active_jobs') &&
        latest != null &&
        activePreparationStates.contains(latest['state']))
      active.add(latest);
    if (value['prepared'] != null) {
      final prepared = _object(value['prepared'], 'prepared environment');
      if (prepared['current'] is! bool ||
          prepared['revision'] is! String ||
          (prepared['selected_check_count'] != null &&
              prepared['selected_check_count'] is! int))
        throw const FormatException('Invalid prepared environment identity.');
      _requirements(prepared['requirements']);
      _objects(prepared['selected_checks'], 'prepared checks');
    }
    return {
      'status': value,
      'plan': value['plan_current'] == false ? null : selected,
      'job': latest,
      'activeJobs': active,
    };
  }

  void _poll() {
    _stop();
    if (isClosing ||
        busy ||
        loading ||
        lookupError != null ||
        activeJobs.isEmpty)
      return;
    final version = _version, epoch = connectionGeneration(), watch = _watch;
    _timer = Timer.periodic(const Duration(seconds: 2), (timer) {
      if (!_current(version, epoch) || watch != _watch) {
        timer.cancel();
        return;
      }
      unawaited(send(_PollEnvironment(version, epoch, watch)));
    });
  }

  @override
  Future<void> close() {
    if (!isClosing) {
      _version++;
      _stop();
      _updates.cancel();
    }
    return super.close();
  }
}

class _ResetEnvironment extends EventBase {}

class _LoadEnvironment extends EventBase {
  _LoadEnvironment(this.path, this.version, this.epoch, this.id);
  final String path;
  final int version, epoch, id;
}

class _EnvironmentAction extends EventBase {
  _EnvironmentAction(
    this.operation,
    this.checks,
    this.jobId,
    this.version,
    this.epoch,
  );
  final String operation;
  final List<String>? checks;
  final String? jobId;
  final int version, epoch;
}

class _PollEnvironment extends EventBase {
  _PollEnvironment(this.version, this.epoch, this.watch);
  final int version, epoch, watch;
}

class _ResetEnvironmentUseCase
    extends BlocUseCase<ProjectEnvironmentController, _ResetEnvironment> {
  @override
  Future<void> execute(_ResetEnvironment event) async => emitUpdate(
    newState: FeatureState(values: ProjectEnvironmentController._empty),
  );
}

class _LoadEnvironmentUseCase
    extends BlocUseCase<ProjectEnvironmentController, _LoadEnvironment> {
  @override
  Future<void> execute(_LoadEnvironment event) async {
    bool current() =>
        bloc._current(event.version, event.epoch) && event.id == bloc._request;
    if (!current()) return;
    bloc._stop();
    emitWaiting(newState: bloc.state.changed(reading: {'load'}));
    try {
      final patch = await bloc._observe(event.path);
      if (!current()) return;
      emitUpdate(
        newState: bloc.state.changed(
          patch: patch,
          reading: {},
          errors: {...bloc.state.errors}..remove('lookup'),
        ),
      );
    } catch (error, stack) {
      if (!current()) return;
      bloc._stop();
      emitFailure(
        newState: bloc.state.changed(
          patch: ProjectEnvironmentController._empty,
          reading: {},
          errors: {...bloc.state.errors, 'lookup': '$error'},
        ),
        error: error,
        errorStackTrace: stack,
      );
    } finally {
      if (current()) bloc._poll();
    }
  }
}

class _EnvironmentActionUseCase
    extends BlocUseCase<ProjectEnvironmentController, _EnvironmentAction> {
  @override
  Future<void> execute(_EnvironmentAction event) async {
    bool current() => bloc._current(event.version, event.epoch);
    if (!current()) return;
    var failed = false;
    emitWaiting(
      newState: bloc.state.changed(
        writing: {'action'},
        errors: {...bloc.state.errors}..remove('action'),
      ),
    );
    try {
      final body = switch (event.operation) {
        'plan' => {'path': bloc._path},
        'cancel' => {'id': event.jobId ?? bloc.job?['id']},
        'select' => {'digest': bloc.plan?['digest'], 'checks': event.checks},
        'authorize-restore' => {
          'digest': bloc.plan?['digest'],
          'allowed': bloc.plan?['restore_permission']?['allowed'] != true,
        },
        _ => {'digest': bloc.plan?['digest'], 'retry': true},
      };
      final result = await bloc.request(
        'projects/environment/${event.operation}',
        body,
      );
      if (!current()) return;
      if (['plan', 'select', 'authorize-restore'].contains(event.operation)) {
        final plan = _plan(result);
        emitUpdate(
          newState: bloc.state.changed(
            patch: {
              'plan': plan,
              if (bloc.job?['digest'] != plan['digest']) 'job': null,
            },
          ),
        );
      } else {
        final job = _job(result);
        final active = bloc.activeJobs
            .where((value) => value['id'] != job['id'])
            .toList();
        if (activePreparationStates.contains(job['state'])) active.add(job);
        emitUpdate(
          newState: bloc.state.changed(
            patch: {'job': job, 'activeJobs': active},
          ),
        );
      }
      try {
        final patch = await bloc._observe(bloc._path!);
        if (current())
          emitUpdate(
            newState: bloc.state.changed(
              patch: patch,
              errors: {...bloc.state.errors}..remove('lookup'),
            ),
          );
      } catch (error, stack) {
        failed = true;
        if (current())
          emitFailure(
            newState: bloc.state.changed(
              patch: ProjectEnvironmentController._empty,
              writing: {},
              errors: {...bloc.state.errors, 'lookup': '$error'},
            ),
            error: error,
            errorStackTrace: stack,
          );
      }
    } catch (error, stack) {
      failed = true;
      if (current())
        emitFailure(
          newState: bloc.state.changed(
            writing: {},
            errors: {
              ...bloc.state.errors,
              'action':
                  'Environment action (${event.operation}) failed: $error',
            },
          ),
          error: error,
          errorStackTrace: stack,
        );
    } finally {
      if (current()) {
        if (!failed) emitUpdate(newState: bloc.state.changed(writing: {}));
        bloc._poll();
      }
    }
  }
}

class _PollEnvironmentUseCase
    extends BlocUseCase<ProjectEnvironmentController, _PollEnvironment> {
  @override
  Future<void> execute(_PollEnvironment event) async {
    bool current() =>
        bloc._current(event.version, event.epoch) && event.watch == bloc._watch;
    if (!current()) return;
    try {
      final values = await Future.wait([
        for (final active in bloc.activeJobs)
          () async {
            final value = _job(
              await bloc.request('projects/environment?job=${active['id']}'),
            );
            if (value['id'] != active['id'])
              throw const FormatException('Preparation job identity changed.');
            return value;
          }(),
      ]);
      if (!current()) return;
      Map<String, dynamic>? job = bloc.job;
      for (final value in values) {
        if (job?['id'] == value['id']) job = value;
      }
      emitUpdate(
        newState: bloc.state.changed(
          patch: {
            'job': job,
            'activeJobs': values
                .where(
                  (value) => activePreparationStates.contains(value['state']),
                )
                .toList(),
          },
        ),
      );
      if (values.any(
        (value) => !activePreparationStates.contains(value['state']),
      )) {
        final patch = await bloc._observe(bloc._path!);
        if (current())
          emitUpdate(
            newState: bloc.state.changed(
              patch: patch,
              errors: {...bloc.state.errors}..remove('lookup'),
            ),
          );
      }
      if (current()) bloc._poll();
    } catch (error, stack) {
      if (current()) {
        bloc._stop();
        emitFailure(
          newState: bloc.state.changed(
            patch: ProjectEnvironmentController._empty,
            errors: {...bloc.state.errors, 'lookup': '$error'},
          ),
          error: error,
          errorStackTrace: stack,
        );
      }
    }
  }
}
