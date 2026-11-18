/// Framework-independent form rules copied from the existing dashboard UI.
/// They prepare form values; gateway repositories and blocs still own actions.
const codexNoApprovalMode = 'codex_no_approval';
const codexPermissionChoices = {
  'default': 'CLI defaults',
  codexNoApprovalMode: 'Workspace sandbox, no command prompts',
};
const openCodePermissionChoices = {
  'default': 'CLI defaults',
  'dashboard_outputs': 'Allow dashboard outputs',
  'full_autonomy': 'Full autonomy',
};

Map<String, String> permissionChoices(String runtime) => switch (runtime) {
  'codex' => codexPermissionChoices,
  'opencode' => openCodePermissionChoices,
  _ => const {'default': 'CLI defaults'},
};

void updateHireRuntime(
  Map<String, dynamic> values,
  String runtime, {
  required bool creating,
}) {
  for (final key in ['provider', 'model', 'reasoning']) values[key] = '';
  if (creating && runtime == 'opencode')
    values['permission_mode'] = 'dashboard_outputs';
  if (!permissionChoices(runtime).containsKey(values['permission_mode']))
    values['permission_mode'] = 'default';
  if (runtime != 'opencode') {
    values['accessible_paths'] = '';
  }
}

List<String> parseAccessiblePaths(String source) => source
    .split('\n')
    .map((p) => p.trim())
    .where((p) => p.isNotEmpty)
    .toSet()
    .toList();
String? validateAccessiblePaths(String source) {
  final p = parseAccessiblePaths(source);
  if (p.length > 20) return 'Use at most 20 patterns.';
  if (p.any(
    (p) =>
        p.length > 500 ||
        p.contains('\u0000') ||
        !(p.startsWith('/') || p.startsWith('~/') || p.startsWith(r'$HOME/')),
  ))
    return 'Use absolute or home-relative patterns under 500 characters.';
  return null;
}

String configurationImportSummary(Map<String, dynamic> data) {
  if (data['format'] != 'herdr-configuration' || data['version'] != 1)
    throw const FormatException('Unsupported configuration format.');
  return '${(data['organizations'] as List).length} organizations, ${(data['profiles'] as List).length} agents, ${(data['groups'] as List).length} groups.';
}

/// Only the runtimes the gateway will accept, for the hire form's dropdown.
///
/// The CLI snapshot reports readiness with the same rule the add-agent and
/// launch paths enforce, so a CLI that is missing, whose integration is absent
/// or outdated, or whose version cannot be verified is not offered at all -
/// rather than being presented and then refused on save. An agent being edited
/// keeps its own runtime even when it is no longer ready, labelled with the
/// reason, so opening the form cannot silently change the value.
Map<String, String> runtimeChoices(
  List<Map<String, dynamic>> runtimes, [
  Map<String, dynamic>? profile,
]) {
  final current = '${profile?['runtime'] ?? ''}';
  final choices = <String, String>{};
  for (final cli in runtimes) {
    final id = '${cli['id'] ?? ''}';
    if (id.isEmpty) continue;
    final label = '${cli['name'] ?? id}';
    final problem = '${cli['ready'] ?? ''}';
    if (problem.isEmpty) {
      choices[id] = label;
    } else if (id == current) {
      choices[id] = '$label - not ready: $problem';
    }
  }
  if (current.isNotEmpty && !choices.containsKey(current)) {
    choices[current] = 'Runtime $current - readiness unavailable';
  }
  if (choices.isEmpty) {
    // Saying so beats an empty list: the reason is always something the
    // operator can fix on the CLI accounts page.
    return {'': 'No runtime is ready - connect a CLI account first'};
  }
  return {'': 'Choose runtime', ...choices};
}
