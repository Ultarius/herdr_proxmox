import 'feature_bloc.dart';

const cloneRemovalPreservationNotice =
    'The remote repository and saved GitHub credentials are not deleted or revoked.';
const legacyCloneRemovalNotice =
    'This older entry has no verified original folder identity. You can remove its history entry; inspect the folder before deleting it manually.';

bool cloneFolderIdentityAvailable(Map<String, dynamic> job) {
  final identity = job['destination_identity'];
  return identity is List &&
      identity.length == 2 &&
      identity[0] is int &&
      (identity[0] as int) >= 0 &&
      identity[1] is int &&
      (identity[1] as int) > 0;
}

class CloneJobsBloc extends FeatureBloc {
  CloneJobsBloc(super.connection)
    : super(
        initial: const {'jobs': <Map<String, dynamic>>[], 'loaded': false},
      ) {
    poll('jobs', const Duration(seconds: 3), refresh);
    connection.operator.addListener(_roleChanged);
  }
  void _roleChanged() {
    // Clone history is readable after an administrator becomes a viewer.
    // Re-read it after the feature's privilege reset; do not retain credentials.
    Future<void>.microtask(refresh);
  }

  @override
  Future<void> close() {
    if (!isClosing) connection.operator.removeListener(_roleChanged);
    return super.close();
  }

  List<Map<String, dynamic>> get jobs =>
      (state.values['jobs'] as List).cast<Map<String, dynamic>>();
  String? get removing =>
      state.busy ? state.values['removing'] as String? : null;
  String? get result =>
      state.errors['clone'] ??
      state.errors['remove'] ??
      (state.errors['jobs'] == null
          ? state.values['notice'] as String?
          : 'Clone status unavailable; the server job may still be running. ${state.errors['jobs']}');
  String? get clonedFolder => state.values['clonedFolder'] as String?;
  int get completionRevision => state.values['completionRevision'] as int? ?? 0;
  Future<void> refresh() => read<void>(
    'jobs',
    () async {
      final data = await connection.request('projects/jobs');
      final next = List<Map<String, dynamic>>.from(data['jobs'] ?? []);
      final latest = next.firstOrNull;
      final oldStates = {for (final job in jobs) job['id']: job['state']};
      final completed =
          latest?['state'] == 'completed' &&
          latest?['id'] != state.values['completed'] &&
          (state.values['loaded'] != true ||
              ['queued', 'running'].contains(oldStates[latest?['id']]));
      return FeatureResult(
        patch: {
          'jobs': next,
          'loaded': true,
          if (completed) ...{
            'completed': latest!['id'],
            'clonedFolder': latest['cwd'],
            'completionRevision': completionRevision + 1,
          },
          if ([
                'failed',
                'interrupted',
                'completed',
              ].contains(latest?['state']) &&
              (state.values['notice'] as String? ?? '').startsWith(
                'Clone queued.',
              ))
            'notice': null,
        },
      );
    },
    quiet: true,
    replace: false,
  );
  Future<void> clone(String url, String folder, String connectionId) async {
    await mutate<void>('clone', () async {
      final data = Map<String, dynamic>.from(
        await connection.request('projects/clone', {
          'url': url.trim(),
          'folder': folder.trim(),
          if (connectionId.isNotEmpty) 'connection_id': connectionId,
        }),
      );
      return FeatureResult(
        patch: {
          'jobs': [data, ...jobs.where((job) => job['id'] != data['id'])],
          'notice':
              'Clone queued. You can navigate away and return to see its status.',
        },
      );
    }, start: const {'notice': null});
    await refresh();
  }

  Future<Map<String, dynamic>?> remove(
    Map<String, dynamic> job, {
    required bool deleteFolder,
    required String confirmation,
  }) => mutate<Map<String, dynamic>>(
    'remove',
    () async {
      if (deleteFolder &&
          (!cloneFolderIdentityAvailable(job) ||
              confirmation != job['folder'])) {
        throw StateError(
          'Confirm the exact original folder name before deleting it.',
        );
      }
      final data = Map<String, dynamic>.from(
        await connection.request('projects/jobs/remove', {
          'id': job['id'],
          'delete_folder': deleteFolder,
          if (deleteFolder) 'confirm_folder': confirmation,
        }),
      );
      return FeatureResult(
        value: data,
        patch: {
          'jobs': jobs.where((item) => item['id'] != job['id']).toList(),
          'notice': '${data['message']}',
          'removing': null,
        },
      );
    },
    admin: true,
    start: {'removing': job['id'], 'notice': null},
  );
}
