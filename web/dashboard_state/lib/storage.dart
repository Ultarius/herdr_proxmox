import 'feature_bloc.dart';
import 'dashboard_bloc.dart';

Map<String, dynamic> storageMap(dynamic value) => value is Map
    ? {for (final key in value.keys.whereType<String>()) key: value[key]}
    : {};

List<Map<String, dynamic>> storageEntries(dynamic value) =>
    value is List ? value.whereType<Map>().map(storageMap).toList() : [];

String storageBytes(dynamic value) {
  final count = value is num ? value : 0;
  return count >= 1073741824
      ? '${(count / 1073741824).toStringAsFixed(1)} GiB'
      : '${(count / 1048576).toStringAsFixed(0)} MiB';
}

/// Cached storage observations are cheap reads. Only an explicit scan walks
/// registered roots; cleanup always uses the preview's ownership generations.
class StorageController extends FeatureBloc {
  StorageController(DashboardBloc connection, {required this.onUpdate})
    : super(
        connection,
        initial: const {
          'loaded': false,
          'data': <String, dynamic>{},
          'preview': <String, dynamic>{},
          'legacyPreview': <String, dynamic>{},
          'repairPreview': <String, dynamic>{},
        },
      ) {
    _generation = connection.generation;
    _role = '${connection.operator.value['role']}';
    _connected = connection.state.connected;
    bindStream(stream, (_) => _updated(), key: 'projection');
    refresh();
  }
  final void Function() onUpdate;
  late int _generation;
  late String _role;
  late bool _connected;
  String legacyProject = '', legacyExecutionId = '';
  final Set<String> selected = {};
  bool get disposed => isClosing;
  int get generation => connection.generation;
  bool get loaded => state.values['loaded'] == true;
  bool get loading => state.reading.isNotEmpty;
  bool get busy => state.busy;
  Map<String, dynamic> get data => storageMap(state.values['data']);
  Map<String, dynamic> get preview => storageMap(state.values['preview']);
  Map<String, dynamic> get legacyPreview =>
      storageMap(state.values['legacyPreview']);
  Map<String, dynamic> get repairPreview =>
      storageMap(state.values['repairPreview']);
  String? get error => state.values['error'] as String?;
  String? get message => state.values['notice'] as String?;
  bool get canManage => available && admin;
  bool get scanning => data['scanning'] == true;
  bool get canPreviewLegacy =>
      legacyProject.trim().isNotEmpty &&
      RegExp(r'^[a-f0-9]{32}$').hasMatch(legacyExecutionId.trim());
  Map<String, dynamic> get inventory => storageMap(data['inventory']);
  Map<String, dynamic> get policy => storageMap(data['policy']);
  List<Map<String, dynamic>> get entries =>
      storageEntries(inventory['entries']);

  void _updated() {
    if (isClosing) return;
    final role = '${connection.operator.value['role']}';
    if (_generation != connection.generation ||
        _role != role ||
        _connected != connection.state.connected) {
      _generation = connection.generation;
      _role = role;
      _connected = connection.state.connected;
      stopPolling('scan');
      legacyProject = legacyExecutionId = '';
      selected.clear();
    }
    onUpdate();
    if (canManage && !loaded && !loading && !busy) refresh();
  }

  void select(String id, bool enabled) {
    if (busy ||
        loading ||
        scanning ||
        !entries.any((entry) => entry['id'] == id && entry['eligible'] == true))
      return;
    if (enabled) {
      selected.add(id);
    } else {
      selected.remove(id);
    }
    patch({'preview': <String, dynamic>{}, 'notice': null});
  }

  void cancelPreview() {
    patch({'preview': <String, dynamic>{}});
  }

  void reviewRepair(Map<String, dynamic>? entry) {
    if (!busy && !loading && !scanning)
      patch({'repairPreview': entry ?? <String, dynamic>{}});
  }

  void legacyInputs({String? project, String? executionId}) {
    if (busy || loading || scanning) return;
    if (project != null) legacyProject = project;
    if (executionId != null) legacyExecutionId = executionId;
    patch({'legacyPreview': <String, dynamic>{}, 'notice': null});
  }

  void cancelLegacyPreview() {
    patch({'legacyPreview': <String, dynamic>{}});
  }

  void _schedule(Map<String, dynamic> snapshot) {
    stopPolling('scan');
    if (snapshot['scanning'] == true)
      poll('scan', const Duration(seconds: 2), refresh);
  }

  Future<void> refresh() async {
    if (!canManage || loading || busy) return;
    final epoch = connection.generation;
    selected.clear();
    await read<void>(
      'storage',
      () async {
        try {
          final result = storageMap(await connection.request('storage'));
          if (!current(epoch) || !canManage) return const FeatureResult();
          _schedule(result);
          return FeatureResult(
            patch: {'data': result, 'loaded': true, 'error': null},
          );
        } catch (failure) {
          if (!current(epoch) || !canManage) return const FeatureResult();
          stopPolling('scan');
          return FeatureResult(
            patch: {
              'error': 'Storage status unavailable: $failure',
              'data': <String, dynamic>{},
              'loaded': true,
            },
          );
        }
      },
      replace: false,
      start: const {
        'preview': <String, dynamic>{},
        'repairPreview': <String, dynamic>{},
        'legacyPreview': <String, dynamic>{},
      },
    );
  }

  Future<void> action(
    String action, [
    Map<String, dynamic> body = const {},
  ]) async {
    if (!canManage || busy || loading || scanning) return;
    final epoch = connection.generation;
    final request = <String, dynamic>{'action': action, ...body};
    if (action == 'preview') {
      if (selected.isEmpty) return;
      request['ids'] = selected.toList();
    }
    if (action == 'reclaim') {
      final ids = preview['ids'], expected = storageMap(preview['expected']);
      if (ids is! List || ids.isEmpty || expected.isEmpty) return;
      request.addAll({'ids': ids, 'expected': expected});
    }
    if (action == 'legacy-preview') {
      if (!canPreviewLegacy) return;
      request.addAll({
        'project': legacyProject.trim(),
        'execution_id': legacyExecutionId.trim(),
      });
    }
    if (action == 'legacy-adopt') {
      if (legacyPreview.isEmpty) return;
      request.addAll({'preview': legacyPreview, 'reviewed': true});
    }
    if (action == 'forget-invalid') {
      if (repairPreview['repairable'] != true) return;
      request.addAll({
        'id': repairPreview['id'],
        'row_digest': repairPreview['row_digest'],
        'reviewed': true,
      });
    }
    if (action != 'preview') selected.clear();
    await mutate<void>(
      'storage',
      () async {
        try {
          final result = storageMap(
            await connection.request('storage', request),
          );
          if (!current(epoch) || !canManage) return const FeatureResult();
          final update = <String, dynamic>{'error': null};
          if (result['snapshot'] is Map) {
            final snapshot = storageMap(result['snapshot']);
            _schedule(snapshot);
            update['data'] = snapshot;
          }
          if (action == 'preview') {
            final candidate = storageMap(result['preview']);
            if (candidate['ids'] is! List ||
                storageMap(candidate['expected']).isEmpty ||
                storageEntries(candidate['entries']).length !=
                    (candidate['ids'] as List).length ||
                storageEntries(candidate['entries']).any(
                  (entry) => entry['path'] is! String || entry['bytes'] is! num,
                )) {
              throw const FormatException(
                'No valid storage cleanup preview returned.',
              );
            }
            update['preview'] = candidate;
          } else if (action == 'legacy-preview') {
            final candidate = storageMap(result['preview']);
            if (candidate['path'] is! String ||
                candidate['fingerprint'] == null ||
                candidate['execution_id'] != legacyExecutionId.trim()) {
              throw const FormatException(
                'No valid existing-output preview returned.',
              );
            }
            update['legacyPreview'] = candidate;
          } else if (action == 'legacy-adopt') {
            update['notice'] =
                'Existing output registered as disposable. Retention cleanup may remove it; select it in the inventory for immediate reviewed cleanup. Source files and files outside the previewed directory are preserved.';
          } else if (action == 'configure') {
            update['notice'] =
                'Build mode saved for future environment plans. Existing sessions keep their approved settings.';
          } else if (action == 'forget-invalid') {
            update['notice'] =
                'Invalid registry metadata archived. No files were deleted.';
          } else if (action == 'reclaim') {
            update['notice'] =
                'Cleanup finished. Active or changed roots are retained; review the updated inventory.';
          }
          return FeatureResult(patch: update);
        } catch (failure) {
          return FeatureResult(
            patch: {
              'preview': <String, dynamic>{},
              'legacyPreview': <String, dynamic>{},
              'error': 'Storage action failed: $failure',
            },
          );
        }
      },
      admin: true,
      start: {
        'repairPreview': <String, dynamic>{},
        'legacyPreview': <String, dynamic>{},
        if (action != 'preview') 'preview': <String, dynamic>{},
        'notice': null,
        'error': null,
      },
    );
  }
}
