import 'feature_bloc.dart';
import 'package:juice/juice.dart' show Future, StreamSubscription;

Map<String, dynamic> _object(dynamic value) =>
    Map<String, dynamic>.from(value as Map);

/// SSH status and key changes share one owner in both dashboard renderers.
abstract class SettingsFeatureBloc extends FeatureBloc {
  SettingsFeatureBloc(super.connection) {
    _generation = connection.generation;
    _role = '${connection.operator.value['role']}';
    _connected = connection.state.connected;
    connection.operator.addListener(_sessionChanged);
    _sessionSubscription = connection.stream.listen((_) => _sessionChanged());
  }
  late int _generation;
  late String _role;
  late bool _connected;
  late final StreamSubscription _sessionSubscription;
  Future<void> refresh();
  void _sessionChanged() {
    final role = '${connection.operator.value['role']}';
    if (_generation == connection.generation &&
        _role == role &&
        _connected == connection.state.connected)
      return;
    _generation = connection.generation;
    _role = role;
    _connected = connection.state.connected;
    if (available) Future<void>.microtask(refresh);
  }

  @override
  Future<void> close() {
    if (!isClosing) {
      connection.operator.removeListener(_sessionChanged);
      _sessionSubscription.cancel();
    }
    return super.close();
  }
}

class SshAccessBloc extends SettingsFeatureBloc {
  SshAccessBloc(super.connection);
  int? get count => state.values['count'] as int?;
  Future<void> refresh() => read<void>('status', () async {
    final data = _object(await connection.request('ssh-access'));
    return FeatureResult(patch: {'count': data['key_count']});
  });
  Future<bool> add(String publicKey) async =>
      await mutate<bool>('key', () async {
        final data = _object(
          await connection.request('ssh-access/add', {'public_key': publicKey}),
        );
        return FeatureResult(
          value: true,
          patch: {'count': data['key_count']},
          message: data['added'] == true
              ? 'Public key added. You can now connect as herdr over SSH.'
              : 'This public key is already configured.',
        );
      }, admin: true) ??
      false;
}

class DashboardAccessBloc extends SettingsFeatureBloc {
  DashboardAccessBloc(super.connection);
  String? get mode => state.values['mode'] as String?;
  String? get pending => state.values['pending'] as String?;
  Future<void> refresh() => read<void>('status', () async {
    final data = _object(await connection.request('dashboard-access'));
    return FeatureResult(
      patch: {'mode': data['mode'], 'pending': data['pending_mode']},
    );
  });
  Future<void> change(
    String target, {
    required bool tunnelReady,
  }) => mutate<void>('access', () async {
    final data = _object(
      await connection.request('dashboard-access', {
        'mode': target,
        'tunnel_ready': tunnelReady,
      }),
    );
    return FeatureResult(
      patch: {'pending': data['pending_mode']},
      message: target == 'ssh'
          ? 'SSH-only access applies in 5 seconds. Continue through your tunnel at http://127.0.0.1:8787.'
          : 'LAN access applies in 5 seconds. Reconnect using the LXC IP.',
    );
  }, admin: true);
}

class ToolchainBloc extends SettingsFeatureBloc {
  ToolchainBloc(super.connection) {
    poll('sdk', const Duration(seconds: 5), () async {
      if (queued || ++_tick % 6 == 0) await refresh();
    });
  }
  int _tick = 0;
  Map<String, dynamic> get sdk => state.errors['sdk'] != null
      ? {'state': 'unavailable', 'error': state.errors['sdk']}
      : state.values['sdk'] as Map<String, dynamic>? ?? const {};
  Map<String, dynamic> get toolchains =>
      (sdk['toolchains'] as Map?)?.cast<String, dynamic>() ?? const {};
  bool get queued => ['queued', 'running'].contains(sdk['state']);
  Future<void> refresh() => read<void>('sdk', () async {
    final data = _object(await connection.request('sdk'));
    return FeatureResult(patch: {'sdk': data});
  }, replace: false);
  Future<bool> install(String name) async =>
      await mutate<bool>('install', () async {
        if (queued || '${toolchains[name]?['required'] ?? ''}'.isEmpty) {
          return const FeatureResult(value: false);
        }
        await connection.request('sdk/install', {'toolchain': name});
        final data = _object(await connection.request('sdk'));
        return FeatureResult(
          value: true,
          patch: {'sdk': data},
          message:
              '$name installation queued. The root service installs the pinned toolchain.',
        );
      }, admin: true) ??
      false;
}

class DashboardUpdatesBloc extends SettingsFeatureBloc {
  DashboardUpdatesBloc(super.connection) {
    poll('updates', const Duration(seconds: 5), () async {
      if (queued) await check();
    });
  }
  Map<String, dynamic>? get info =>
      state.values['info'] as Map<String, dynamic>?;
  List<Map<String, dynamic>> get alphas =>
      (state.values['alphas'] as List?)?.cast<Map<String, dynamic>>() ??
      const [];
  bool get queued => ['queued', 'running'].contains(info?['state']);
  @override
  Future<void> refresh() => check();
  Future<void> check({bool force = false}) => read<void>('updates', () async {
    final data = _object(
      await connection.request(
        force ? 'updates/check' : 'updates',
        force ? <String, dynamic>{} : null,
      ),
    );
    return FeatureResult(patch: {'info': data});
  }, replace: false);
  Future<void> install(String version, {bool alpha = false}) =>
      mutate<void>('install', () async {
        if (queued || info?['supported'] != true) return const FeatureResult();
        await connection.request('updates/install', {
          'version': version,
          if (alpha) 'channel': 'alpha',
          if (alpha) 'confirm_alpha': true,
        });
        return FeatureResult(
          patch: {
            'info': {...?info, 'state': 'queued'},
          },
        );
      }, admin: true);
  Future<void> loadAlphas() => read<void>('alphas', () async {
    final data = _object(
      await connection.request('updates/alphas', <String, dynamic>{}),
    );
    return FeatureResult(
      patch: {
        'alphas': (data['releases'] as List).cast<Map<String, dynamic>>(),
        'alphasLoaded': true,
      },
    );
  });
}

class ResourcesBloc extends SettingsFeatureBloc {
  ResourcesBloc(super.connection) {
    poll('resources', const Duration(seconds: 10), () async {
      if (!state.errors.containsKey('resources')) await refresh();
    });
  }
  Map<String, dynamic>? get data =>
      state.values['resources'] as Map<String, dynamic>?;
  Future<void> refresh() => read<void>('resources', () async {
    final data = _object(await connection.request('resources'));
    return FeatureResult(patch: {'resources': data});
  }, replace: false);
}
