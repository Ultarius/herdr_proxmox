import 'dart:convert';

// JSON object key order is insignificant; list order still describes the intent.
Object? _orderedJson(Object? value) {
  if (value is Map) {
    final keys = value.keys.cast<String>().toList()..sort();
    return {for (final key in keys) key: _orderedJson(value[key])};
  }
  if (value is List) return value.map(_orderedJson).toList();
  return value;
}

/// Shared save/load identity; draft revisions remain owned by each form.
class TemplateLibraryState {
  Map<String, dynamic>? _source, _pending;
  int selection = 0, _operation = 0;
  bool busy = false;

  Map<String, dynamic>? get source => _source;
  set source(Map<String, dynamic>? value) {
    _source = value;
    selection++;
  }

  int begin() {
    busy = true;
    return ++_operation;
  }

  bool current(int operation) => operation == _operation;
  void finish(int operation) {
    if (current(operation)) busy = false;
  }

  Map<String, dynamic> request(
    Map<String, dynamic> intent,
    String Function() newId,
  ) {
    final previous = _pending == null
        ? null
        : (Map<String, dynamic>.from(_pending!)..remove('request_id'));
    if (jsonEncode(_orderedJson(previous)) !=
        jsonEncode(_orderedJson(intent))) {
      _pending = {
        ...jsonDecode(jsonEncode(intent)) as Map<String, dynamic>,
        'request_id': newId(),
      };
    }
    return _pending!;
  }

  bool acknowledge(Map<String, dynamic> saved, int selectedAtStart) {
    if (selection != selectedAtStart) return false;
    _source = saved;
    _pending = null;
    return true;
  }
}

String memberExecutionSummary(Map<String, dynamic> member) {
  final workspace = member['use_worktree'] == false
      ? 'Shared project directory'
      : 'Separate worktree';
  final permission = switch (member['permission_mode']) {
    'full_autonomy' => 'Full autonomy',
    'dashboard_outputs' => 'Allow dashboard outputs',
    'codex_no_approval' => 'Workspace sandbox, no command prompts',
    _ => 'CLI default permissions',
  };
  final paths = (member['accessible_paths'] as List? ?? []).cast<String>();
  return '$workspace · $permission${paths.isEmpty ? '' : ' · Additional access: ${paths.join(', ')}'}';
}
