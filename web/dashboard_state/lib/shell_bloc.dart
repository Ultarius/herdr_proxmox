import 'feature_bloc.dart';
import 'integration.dart';

/// Shell server state stays alive across page navigation. Browser navigation,
/// themes, opened dialogs and which alerts have been read remain presentation.
class ShellBloc extends FeatureBloc {
  ShellBloc(super.connection)
    : super(
        initial: {
          'notices': <Map<String, dynamic>>[],
          'checked': null,
          'noticeRevision': 0,
          'announce': false,
        },
      ) {
    bindStream(connection.stream, (_) => synchronize());
    poll('notices', const Duration(seconds: 15), refreshNotices);
    synchronize();
  }

  int _noticeGeneration = -1;
  List<IntegrationNotice> get notices => [
    for (final item in state.values['notices'] as List? ?? [])
      if (item is Map<String, dynamic>) IntegrationNotice.fromJson(item),
  ];
  String? get checked => state.values['checked'] as String?;
  int get noticeRevision => state.values['noticeRevision'] as int? ?? 0;
  bool get announce => state.values['announce'] == true;
  Set<String> get asking => {
    for (final key in state.writing)
      if (key.startsWith('ask:')) key.substring(4),
  };

  void synchronize() {
    if (!available) return;
    hydrateDirectory();
    if (_noticeGeneration != connection.generation) {
      _noticeGeneration = connection.generation;
      refreshNotices(announce: false);
    }
  }

  Future<void> hydrateDirectory() async {
    if (connection.organizationDirectory.value['loaded'] == true) return;
    await read(
      'directory',
      () async {
        await connection.request('organizations/directory');
        return const FeatureResult();
      },
      quiet: true,
      replace: false,
    );
  }

  Future<void> refreshNotices({bool announce = true}) async {
    await read(
      'integration',
      () async {
        final result = await connection.request('integration');
        return FeatureResult(
          patch: {
            'notices': [
              for (final item in result['alerts'] as List? ?? [])
                if (item is Map<String, dynamic>) item,
            ],
            'checked': result['checked'] as String?,
            'noticeRevision': noticeRevision + 1,
            'announce': announce,
          },
        );
      },
      quiet: true,
      replace: false,
    );
  }

  Future<bool> ask(IntegrationNotice notice) async {
    if (!available ||
        !admin ||
        !agentCanIntegrate(connection, notice.profileId))
      throw StateError('The agent is unavailable or already working.');
    final done = await mutate<bool>(
      'ask:${notice.profileId}',
      () async {
        await requestIntegration(connection, notice);
        return const FeatureResult(value: true);
      },
      admin: true,
      rethrowErrors: true,
    );
    return done == true;
  }
}
