import 'dart:async';
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
class StorageController {
  StorageController(this.connection, {required this.onUpdate}) {
    generation = connection.generation;
    role = '${connection.operator.value['role']}';
    connected = connection.state.connected;
    subscription = connection.stream.listen((_) => sessionChanged());
    connection.operator.addListener(sessionChanged);
    refresh();
  }

  final DashboardBloc connection;
  final void Function() onUpdate;
  late final StreamSubscription subscription;
  late int generation;
  late String role;
  late bool connected;
  int revision = 0;
  bool disposed = false, loaded = false, loading = false, busy = false;
  Timer? timer;
  Map<String, dynamic> data = {},
      preview = {},
      legacyPreview = {},
      repairPreview = {};
  String legacyProject = '', legacyExecutionId = '';
  final Set<String> selected = {};
  String? error, message;

  bool get canManage =>
      connection.state.connected &&
      connection.operator.value['role'] == 'admin';
  bool get scanning => data['scanning'] == true;
  bool get canPreviewLegacy =>
      legacyProject.trim().isNotEmpty &&
      RegExp(r'^[a-f0-9]{32}$').hasMatch(legacyExecutionId.trim());
  Map<String, dynamic> get inventory => storageMap(data['inventory']);
  Map<String, dynamic> get policy => storageMap(data['policy']);
  List<Map<String, dynamic>> get entries =>
      storageEntries(inventory['entries']);
  bool current(int epoch, int version) =>
      !disposed &&
      canManage &&
      generation == epoch &&
      connection.generation == epoch &&
      revision == version;

  void sessionChanged() {
    if (disposed) return;
    final nextRole = '${connection.operator.value['role']}';
    if (generation != connection.generation ||
        role != nextRole ||
        connected != connection.state.connected) {
      generation = connection.generation;
      role = nextRole;
      connected = connection.state.connected;
      revision++;
      timer?.cancel();
      loaded = loading = busy = false;
      data = {};
      preview = {};
      legacyPreview = {};
      repairPreview = {};
      legacyProject = '';
      legacyExecutionId = '';
      selected.clear();
      error = message = null;
      onUpdate();
    }
    if (canManage && !loaded && !loading) refresh();
  }

  void select(String id, bool enabled) {
    if (busy || loading || scanning) return;
    if (!entries.any((entry) => entry['id'] == id && entry['eligible'] == true))
      return;
    if (enabled) {
      selected.add(id);
    } else {
      selected.remove(id);
    }
    preview = {};
    message = null;
    onUpdate();
  }

  void cancelPreview() {
    preview = {};
    onUpdate();
  }

  void reviewRepair(Map<String, dynamic>? entry) {
    if (busy || loading || scanning) return;
    repairPreview = entry ?? {};
    onUpdate();
  }

  void legacyInputs({String? project, String? executionId}) {
    if (busy || loading || scanning) return;
    if (project != null) legacyProject = project;
    if (executionId != null) legacyExecutionId = executionId;
    legacyPreview = {};
    message = null;
    onUpdate();
  }

  void cancelLegacyPreview() {
    legacyPreview = {};
    onUpdate();
  }

  void accept(dynamic value) {
    data = storageMap(value);
    timer?.cancel();
    if (scanning) {
      timer = Timer(const Duration(seconds: 2), refresh);
    }
  }

  Future<void> refresh() async {
    if (!canManage || disposed || loading || busy) return;
    loading = true;
    selected.clear();
    preview = {};
    repairPreview = {};
    legacyPreview = {};
    onUpdate();
    final epoch = connection.generation, version = revision;
    try {
      final result = await connection.request('storage');
      if (!current(epoch, version)) return;
      accept(result);
      error = null;
    } catch (failure) {
      if (current(epoch, version)) {
        timer?.cancel();
        error = 'Storage status unavailable: $failure';
        data = {};
        preview = {};
        legacyPreview = {};
        selected.clear();
      }
    } finally {
      if (current(epoch, version)) {
        loaded = true;
        loading = false;
        onUpdate();
      }
    }
  }

  Future<void> action(
    String action, [
    Map<String, dynamic> body = const {},
  ]) async {
    if (!canManage || disposed || busy || loading || scanning) return;
    final epoch = connection.generation, version = revision;
    final request = <String, dynamic>{'action': action, ...body};
    if (action == 'preview') {
      if (selected.isEmpty) return;
      request['ids'] = selected.toList();
    }
    if (action == 'reclaim') {
      final ids = preview['ids'];
      final expected = storageMap(preview['expected']);
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
    repairPreview = {};
    legacyPreview = {};
    if (action != 'preview') {
      selected.clear();
      preview = {};
    }
    busy = true;
    error = message = null;
    onUpdate();
    try {
      final result = storageMap(await connection.request('storage', request));
      if (!current(epoch, version)) return;
      if (result['snapshot'] is Map) accept(result['snapshot']);
      if (action == 'preview') {
        final candidate = storageMap(result['preview']);
        if (candidate['ids'] is! List ||
            storageMap(candidate['expected']).isEmpty ||
            storageEntries(candidate['entries']).length !=
                (candidate['ids'] as List).length ||
            storageEntries(candidate['entries']).any(
              (entry) => entry['path'] is! String || entry['bytes'] is! num,
            ))
          throw const FormatException(
            'No valid storage cleanup preview returned.',
          );
        preview = candidate;
      } else if (action == 'legacy-preview') {
        final candidate = storageMap(result['preview']);
        if (candidate['path'] is! String ||
            candidate['fingerprint'] == null ||
            candidate['execution_id'] != legacyExecutionId.trim())
          throw const FormatException(
            'No valid existing-output preview returned.',
          );
        legacyPreview = candidate;
      } else if (action == 'legacy-adopt') {
        message =
            'Existing output registered as disposable. Retention cleanup may remove it; select it in the inventory for immediate reviewed cleanup. Source files and files outside the previewed directory are preserved.';
      } else if (action == 'configure') {
        message =
            'Build mode saved for future environment plans. Existing sessions keep their approved settings.';
      } else if (action == 'forget-invalid') {
        message = 'Invalid registry metadata archived. No files were deleted.';
      } else if (action == 'reclaim') {
        message =
            'Cleanup finished. Active or changed roots are retained; review the updated inventory.';
      }
      error = null;
    } catch (failure) {
      if (current(epoch, version)) {
        preview = {};
        legacyPreview = {};
        error = 'Storage action failed: $failure';
      }
    } finally {
      if (current(epoch, version)) {
        busy = false;
        onUpdate();
      }
    }
  }

  void dispose() {
    disposed = true;
    timer?.cancel();
    subscription.cancel();
    connection.operator.removeListener(sessionChanged);
  }
}
