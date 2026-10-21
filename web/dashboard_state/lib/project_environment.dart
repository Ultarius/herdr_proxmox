import 'dart:async';
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

/// Shared lifecycle for saved status, administrator actions and all active runs.
/// Discovery loads independently; stale replies cannot change a new selection.
class ProjectEnvironmentController {
  ProjectEnvironmentController({
    required this.request,
    required this.onUpdate,
    required this.mounted,
    required this.connectionGeneration,
  });
  final ProjectAnalysisRequest request;
  final void Function() onUpdate;
  final bool Function() mounted;
  final int Function() connectionGeneration;
  Map<String, dynamic>? status, plan, job;
  List<Map<String, dynamic>> activeJobs = [];
  String? lookupError, actionError;
  bool loading = false, busy = false;
  String? _path;
  int? _epoch;
  int _version = 0, _request = 0, _watch = 0;
  bool _disposed = false;
  Timer? _timer;

  bool _current(int version, int epoch) =>
      !_disposed &&
      mounted() &&
      version == _version &&
      epoch == connectionGeneration();
  void _notify() {
    if (!_disposed && mounted()) onUpdate();
  }

  void _stop() {
    _watch++;
    _timer?.cancel();
    _timer = null;
  }

  void _clear() {
    status = plan = job = null;
    activeJobs = [];
  }

  void reset(String path) {
    _version++;
    _request++;
    _stop();
    _path = path;
    _epoch = connectionGeneration();
    _clear();
    loading = busy = false;
    lookupError = actionError = null;
  }

  void dispose() {
    _disposed = true;
    _version++;
    _stop();
  }

  Future<void> load(String path) async {
    if (_disposed) return;
    if (_path != path || _epoch != connectionGeneration()) reset(path);
    final version = _version, epoch = connectionGeneration(), id = ++_request;
    bool current() => _current(version, epoch) && id == _request;
    _stop();
    loading = true;
    _notify();
    try {
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
          activePreparationStates.contains(latest['state'])) {
        active.add(latest);
      }
      if (value['prepared'] != null) {
        final prepared = _object(value['prepared'], 'prepared environment');
        if (prepared['current'] is! bool ||
            prepared['revision'] is! String ||
            (prepared['selected_check_count'] != null &&
                prepared['selected_check_count'] is! int)) {
          throw const FormatException('Invalid prepared environment identity.');
        }
        _requirements(prepared['requirements']);
        _objects(prepared['selected_checks'], 'prepared checks');
      }
      if (!current()) return;
      status = value;
      plan = value['plan_current'] == false ? null : selected;
      job = latest;
      activeJobs = active;
      lookupError = null;
    } catch (error) {
      if (!current()) return;
      _clear();
      _stop();
      lookupError = '$error';
    } finally {
      if (current()) {
        loading = false;
        _notify();
        _poll();
      }
    }
  }

  Future<void> action(
    String operation, {
    List<String>? checks,
    String? jobId,
  }) async {
    if (_disposed || busy || loading || _path == null) return;
    final version = _version, epoch = connectionGeneration();
    bool current() => _current(version, epoch);
    _request++;
    _stop();
    busy = true;
    actionError = null;
    _notify();
    try {
      final body = switch (operation) {
        'plan' => {'path': _path},
        'cancel' => {'id': jobId ?? job?['id']},
        'select' => {'digest': plan?['digest'], 'checks': checks},
        'authorize-restore' => {
          'digest': plan?['digest'],
          'allowed': plan?['restore_permission']?['allowed'] != true,
        },
        _ => {'digest': plan?['digest'], 'retry': true},
      };
      final result = await request('projects/environment/$operation', body);
      if (!current()) return;
      if (['plan', 'select', 'authorize-restore'].contains(operation)) {
        plan = _plan(result);
        if (job?['digest'] != plan!['digest']) job = null;
        // Active preparations belong to their own plans. Editing a proposal
        // never discards those jobs or authorizes another preparation.
      } else {
        job = _job(result);
        activeJobs.removeWhere((value) => value['id'] == job!['id']);
        if (activePreparationStates.contains(job!['state']))
          activeJobs.add(job!);
      }
      _notify();
      await load(_path!);
    } catch (error) {
      if (current())
        actionError = 'Environment action ($operation) failed: $error';
    } finally {
      if (current()) {
        busy = false;
        _notify();
        _poll();
      }
    }
  }

  void _poll() {
    _stop();
    if (busy || loading || lookupError != null || activeJobs.isEmpty) return;
    final version = _version, epoch = connectionGeneration(), watch = _watch;
    bool current() => _current(version, epoch) && watch == _watch;
    var polling = false;
    _timer = Timer.periodic(const Duration(seconds: 2), (timer) async {
      if (!current()) {
        timer.cancel();
        return;
      }
      if (polling) return;
      polling = true;
      try {
        final values = await Future.wait([
          for (final active in activeJobs)
            () async {
              final value = _job(
                await request('projects/environment?job=${active['id']}'),
              );
              if (value['id'] != active['id'])
                throw const FormatException(
                  'Preparation job identity changed.',
                );
              return value;
            }(),
        ]);
        if (!current()) return;
        for (final value in values) {
          if (job?['id'] == value['id']) job = value;
        }
        activeJobs = values
            .where((value) => activePreparationStates.contains(value['state']))
            .toList();
        _notify();
        if (values.any(
          (value) => !activePreparationStates.contains(value['state']),
        ))
          await load(_path!);
      } catch (error) {
        if (current()) {
          _clear();
          _stop();
          lookupError = '$error';
          _notify();
        }
      } finally {
        polling = false;
      }
    });
  }
}
