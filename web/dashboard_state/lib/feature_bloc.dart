import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

/// Authoritative observations and operation ownership for a mounted feature.
/// Presentation drafts, selection and browser resources stay in its renderer.
class FeatureState extends BlocState {
  FeatureState({
    Map<String, dynamic> values = const {},
    Set<String> reading = const {},
    Set<String> quietReading = const {},
    Set<String> writing = const {},
    Map<String, String> errors = const {},
    this.message,
  }) : values = Map.unmodifiable(values),
       reading = Set.unmodifiable(reading),
       quietReading = Set.unmodifiable(quietReading),
       writing = Set.unmodifiable(writing),
       errors = Map.unmodifiable(errors);

  final Map<String, dynamic> values;
  final Set<String> reading, quietReading, writing;
  final Map<String, String> errors;
  final String? message;
  bool get busy => writing.isNotEmpty;
  bool get loading => reading.any((key) => !quietReading.contains(key));
  String? get error => errors.values.lastOrNull;
  T? value<T>(String key) => values[key] as T?;

  FeatureState changed({
    Map<String, dynamic> patch = const {},
    Set<String>? reading,
    Set<String>? quietReading,
    Set<String>? writing,
    Map<String, String>? errors,
    String? message,
    bool clearMessage = false,
  }) => FeatureState(
    values: {...values, ...patch},
    reading: reading ?? this.reading,
    quietReading: quietReading ?? this.quietReading,
    writing: writing ?? this.writing,
    errors: errors ?? this.errors,
    message: clearMessage ? null : message ?? this.message,
  );
}

class FeatureResult<T> {
  const FeatureResult({this.patch = const {}, this.value, this.message});
  final Map<String, dynamic> patch;
  final T? value;
  final String? message;
}

class _ReadFeature extends EventBase {
  _ReadFeature(
    this.key,
    this.work,
    this.quiet,
    this.epoch,
    this.serial,
    this.version,
    this.start,
  );
  final String key;
  final FutureOr<FeatureResult<dynamic>> Function() work;
  final bool quiet;
  final int epoch, serial, version;
  final Map<String, dynamic> start;
  final completed = Completer<dynamic>();
}

class _MutateFeature extends EventBase {
  _MutateFeature(this.key, this.work, this.epoch, this.serial, this.start);
  final String key;
  final FutureOr<FeatureResult<dynamic>> Function() work;
  final int epoch, serial;
  final Map<String, dynamic> start;
  final completed = Completer<dynamic>();
}

class _PatchFeature extends EventBase {
  _PatchFeature(this.patch, this.message, this.errorKey, this.error);
  final Map<String, dynamic> patch;
  final String? message, errorKey, error;
}

class _ResetFeature extends EventBase {}

/// Shared Juice lifecycle engine. Feature subclasses own API payloads, data
/// patches and refresh policies; UI code invokes their domain methods.
class FeatureBloc extends JuiceBloc<FeatureState> {
  FeatureBloc(this.connection, {Map<String, dynamic> initial = const {}})
    : initial = Map.unmodifiable(initial),
      super(FeatureState(values: initial), [
        () => UseCaseBuilder.typed(
          () => _ReadFeatureUseCase(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _MutateFeatureUseCase(),
          // Admission is synchronous and per session in mutate(). A write from
          // a retired session must not consume the new session's handler slot.
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _PatchFeatureUseCase(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _ResetFeatureUseCase(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]) {
    _epoch = connection.generation;
    _connected = connection.state.connected;
    _role = '${connection.operator.value['role']}';
    _connectionSubscription = connection.stream.listen(
      (_) => _sessionChanged(),
    );
    connection.operator.addListener(_sessionChanged);
  }

  final DashboardBloc connection;
  final Map<String, dynamic> initial;
  late final StreamSubscription _connectionSubscription;
  final _timers = <String, Timer>{};
  final _reads = <String, Future<dynamic>>{};
  final _versions = <String, int>{};
  int _serial = 0, _writeVersion = 0;
  late int _epoch;
  late String _role;
  late bool _connected;
  bool _mutating = false;
  bool get available =>
      !isClosing && connection.state.connected && !connection.disconnecting;
  bool get admin => connection.operator.value['role'] == 'admin';
  bool current(int epoch) => available && epoch == connection.generation;

  void _sessionChanged() {
    if (isClosing) return;
    final role = '${connection.operator.value['role']}';
    if (_epoch == connection.generation &&
        _connected == connection.state.connected &&
        _role == role)
      return;
    _epoch = connection.generation;
    _connected = connection.state.connected;
    _role = role;
    _serial++;
    _reads.clear();
    _versions.clear();
    _mutating = false;
    unawaited(send(_ResetFeature()));
  }

  Future<T?> read<T>(
    String key,
    FutureOr<FeatureResult<T>> Function() work, {
    bool quiet = false,
    bool replace = true,
    bool rethrowErrors = false,
    Map<String, dynamic> start = const {},
  }) async {
    if (!available || _mutating) return null;
    if (!replace) {
      final pending = _reads[key];
      if (pending != null) {
        try {
          return await pending as T?;
        } catch (_) {
          if (rethrowErrors) rethrow;
          return null;
        }
      }
    }
    final version = (_versions[key] ?? 0) + 1;
    _versions[key] = version;
    final event = _ReadFeature(
      key,
      work,
      quiet,
      connection.generation,
      _serial,
      version,
      start,
    );
    final result = event.completed.future;
    result.ignore();
    _reads[key] = result;
    try {
      await send(event);
      if (!event.completed.isCompleted) event.completed.complete(null);
      return await result as T?;
    } catch (_) {
      if (rethrowErrors) rethrow;
      return null;
    } finally {
      if (identical(_reads[key], result)) _reads.remove(key);
    }
  }

  Future<T?> mutate<T>(
    String key,
    FutureOr<FeatureResult<T>> Function() work, {
    bool admin = false,
    bool rethrowErrors = false,
    Map<String, dynamic> start = const {},
  }) async {
    if (!available || _mutating || (admin && !this.admin)) return null;
    _mutating = true;
    _writeVersion++;
    _reads.clear();
    for (final key in _versions.keys.toList()) {
      _versions[key] = _versions[key]! + 1;
    }
    final event = _MutateFeature(
      key,
      work,
      connection.generation,
      _serial,
      start,
    );
    final result = event.completed.future;
    result.ignore();
    try {
      await send(event);
      if (!event.completed.isCompleted) event.completed.complete(null);
      return await result as T?;
    } catch (_) {
      if (rethrowErrors) rethrow;
      return null;
    } finally {
      if (event.serial == _serial) _mutating = false;
    }
  }

  Future<void> patch(Map<String, dynamic> patch, {String? message}) => isClosing
      ? Future.value()
      : send(_PatchFeature(patch, message, null, null));
  Future<void> reportError(String key, Object error) => isClosing
      ? Future.value()
      : send(_PatchFeature(const {}, null, key, '$error'));
  Future<void> clearError(String key) => isClosing
      ? Future.value()
      : send(_PatchFeature(const {}, null, key, null));

  /// Timers are owned by the feature and never call a disposed renderer.
  void poll(String key, Duration interval, Future<void> Function() refresh) {
    _timers.remove(key)?.cancel();
    if (isClosing) return;
    _timers[key] = Timer.periodic(interval, (_) {
      if (available && !_mutating) unawaited(refresh());
    });
  }

  void stopPolling(String key) => _timers.remove(key)?.cancel();

  @override
  Future<void> close() {
    if (!isClosing) {
      _serial++;
      for (final timer in _timers.values) timer.cancel();
      _timers.clear();
      _connectionSubscription.cancel();
      connection.operator.removeListener(_sessionChanged);
    }
    return super.close();
  }
}

class _ReadFeatureUseCase extends BlocUseCase<FeatureBloc, _ReadFeature> {
  @override
  Future<void> execute(_ReadFeature event) async {
    bool current() =>
        bloc.current(event.epoch) &&
        event.serial == bloc._serial &&
        bloc._versions[event.key] == event.version;
    if (!current()) {
      event.completed.complete(null);
      return;
    }
    final writeVersion = bloc._writeVersion;
    emitWaiting(
      newState: bloc.state.changed(
        patch: event.start,
        reading: {...bloc.state.reading, event.key},
        quietReading: event.quiet
            ? {...bloc.state.quietReading, event.key}
            : ({...bloc.state.quietReading}..remove(event.key)),
        errors: event.quiet
            ? bloc.state.errors
            : ({...bloc.state.errors}..remove(event.key)),
      ),
    );
    try {
      final result = await event.work();
      if (!current() || writeVersion != bloc._writeVersion) {
        event.completed.complete(null);
        return;
      }
      emitUpdate(
        newState: bloc.state.changed(
          patch: result.patch,
          reading: {...bloc.state.reading}..remove(event.key),
          quietReading: {...bloc.state.quietReading}..remove(event.key),
          errors: {...bloc.state.errors}..remove(event.key),
          message: result.message,
        ),
      );
      event.completed.complete(result.value);
    } catch (error, stack) {
      if (!current()) {
        event.completed.complete(null);
        return;
      }
      emitFailure(
        newState: bloc.state.changed(
          reading: {...bloc.state.reading}..remove(event.key),
          quietReading: {...bloc.state.quietReading}..remove(event.key),
          errors: {...bloc.state.errors, event.key: '$error'},
        ),
        error: error,
        errorStackTrace: stack,
      );
      event.completed.completeError(error, stack);
    }
  }
}

class _MutateFeatureUseCase extends BlocUseCase<FeatureBloc, _MutateFeature> {
  @override
  Future<void> execute(_MutateFeature event) async {
    bool current() => bloc.current(event.epoch) && event.serial == bloc._serial;
    if (!current()) {
      event.completed.complete(null);
      return;
    }
    emitWaiting(
      newState: bloc.state.changed(
        reading: {},
        quietReading: {},
        patch: event.start,
        writing: {event.key},
        clearMessage: true,
        errors: {...bloc.state.errors}..remove(event.key),
      ),
    );
    try {
      final result = await event.work();
      if (!current()) {
        event.completed.complete(null);
        return;
      }
      emitUpdate(
        newState: bloc.state.changed(
          patch: result.patch,
          writing: {},
          message: result.message,
        ),
      );
      event.completed.complete(result.value);
    } catch (error, stack) {
      if (!current()) {
        event.completed.complete(null);
        return;
      }
      emitFailure(
        newState: bloc.state.changed(
          writing: {},
          errors: {...bloc.state.errors, event.key: '$error'},
        ),
        error: error,
        errorStackTrace: stack,
      );
      event.completed.completeError(error, stack);
    }
  }
}

class _PatchFeatureUseCase extends BlocUseCase<FeatureBloc, _PatchFeature> {
  @override
  Future<void> execute(_PatchFeature event) async {
    emitUpdate(
      newState: bloc.state.changed(
        patch: event.patch,
        message: event.message,
        errors: event.errorKey == null
            ? null
            : (event.error == null
                  ? ({...bloc.state.errors}..remove(event.errorKey))
                  : {...bloc.state.errors, event.errorKey!: event.error!}),
      ),
    );
  }
}

class _ResetFeatureUseCase extends BlocUseCase<FeatureBloc, _ResetFeature> {
  @override
  Future<void> execute(_ResetFeature event) async {
    emitUpdate(newState: FeatureState(values: bloc.initial));
  }
}
