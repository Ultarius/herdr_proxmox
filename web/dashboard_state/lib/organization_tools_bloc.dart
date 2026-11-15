import 'feature_bloc.dart';
import 'organization_bloc.dart';

/// Auxiliary organization forms share Juice read/write admission and evidence.
/// Draft values, native dialogs and file pickers remain renderer-owned.
class OrganizationToolsBloc extends FeatureBloc {
  OrganizationToolsBloc(super.connection);
  Map<String, dynamic>? get catalog =>
      state.values['models'] as Map<String, dynamic>?;
  Future<dynamic> request(String path, [Map<String, dynamic>? body]) async {
    Future<FeatureResult<dynamic>> work() async {
      final data = await connection.request(path, body);
      return FeatureResult(value: data, patch: {path: data});
    }

    final result = body == null
        ? await read<dynamic>(path, work, rethrowErrors: true)
        : await mutate<dynamic>(path, work, admin: true, rethrowErrors: true);
    if (result == null)
      throw StateError(
        'The organization operation is busy or its connection changed.',
      );
    return result;
  }

  Future<void> loadCatalog() async {
    await request('models');
  }

  Future<dynamic> execute(OrganizationCommand command) => mutate<dynamic>(
    command.action,
    () async => FeatureResult(value: await command.execute(connection)),
    admin: true,
    rethrowErrors: true,
  );
}

class ModelDiscoveryBloc extends FeatureBloc {
  ModelDiscoveryBloc(super.connection);
  Map<String, dynamic>? get catalog =>
      state.values['catalog'] as Map<String, dynamic>?;
  String? get warning => state.values['warning'] as String?;
  Future<void> reset() async {
    await read<void>(
      'models',
      () => const FeatureResult(patch: {'catalog': null, 'warning': null}),
    );
    await clearError('models');
  }

  Future<void> discover({
    required String runtime,
    required String project,
    required String provider,
    required String model,
    bool force = false,
  }) async {
    if (runtime != 'opencode') return;
    await read<void>('models', () async {
      final data = Map<String, dynamic>.from(
        await connection.request('models', {
          'runtime': runtime,
          'project': project,
          'force': force,
          'provider': provider,
          'model': model,
        }),
      );
      if ((data['models'] as List? ?? []).isEmpty)
        throw StateError('The CLI reported no models.');
      return FeatureResult(
        patch: {
          'catalog': data,
          'warning': data['stale'] == true
              ? 'Using cached models because refresh failed: ${data['refresh_error']}'
              : null,
        },
      );
    }, replace: true);
  }
}
