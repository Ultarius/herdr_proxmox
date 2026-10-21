/// Shared request and failure semantics for the Flutter and DOM project cards.
///
/// The gateway resolves one view — discovery, any operator override, the
/// disagreement between them and the host assessment — so the cards read a
/// single answer rather than joining two endpoints and risking a different
/// picture from the one provisioning acts on.
typedef ProjectAnalysisRequest =
    Future<dynamic> Function(String path, [Map<String, dynamic>? body]);

String projectInstallerSummary(Map analysis) {
  final bundled = analysis['provisionable'] as List? ?? [];
  return bundled.isEmpty
      ? 'No matching SDK is in the bundled installer catalog. Use Plan environment '
            'to check project-specific SDK preparation.'
      : 'The bundled installer can provide: ${bundled.join(', ')}. '
            'Use Plan environment for project-specific SDK preparation.';
}

String sourceComponentNotice(Map component, Map report, {Map? plan}) {
  final sources = (plan ?? report)['source_components'];
  if (sources is! List) return '';
  for (final item in sources.whereType<Map>()) {
    if (item['path'] == component['path']) {
      final requirements = report['requirements'];
      if (requirements is List &&
          requirements.whereType<Map>().any(
            (requirement) =>
                requirement['override'] is Map &&
                (requirement['capability'] ?? requirement['tool']) ==
                    item['capability'],
          ))
        return '';
      return 'Source-only component: ${item['reason']} '
          'It does not block SDK preparation in this environment.';
    }
  }
  return '';
}

String requestedComponentNotice(Map requirement) {
  final lookup = requirement['selection_lookup'];
  final components = lookup is Map ? lookup['components'] : null;
  return components is List && components.isNotEmpty
      ? 'Requested SDK components: ${components.join(', ')}'
      : '';
}

String recipeEvidenceLabel(Map check) =>
    '${check['source']}${check['derived'] == true ? ' · Derived companion' : ''}'
    '${check['evidence'] == null || check['evidence'] == '' ? '' : ' · Evidence: ${check['evidence']}'}';

bool environmentPlanCanPrepare(Map? plan) =>
    plan?['preparation_version'] == 1 &&
    (plan?['requirements'] as List? ?? []).isNotEmpty &&
    (plan!['requirements'] as List).every(
      (item) =>
          item['artifact'] is Map &&
          item['activation'] == 'available' &&
          ['ready', 'acquire'].contains(item['state']),
    );

String environmentJobLabel(Map job) => switch (job['state']) {
  'ready' =>
    'SDKs prepared. Dependency restore and project checks have not run.',
  'queued' => 'SDK preparation queued',
  'running' => 'Acquiring and verifying SDKs',
  'cancelled' => 'SDK preparation cancelled',
  'failed' => 'SDK preparation failed: ${job['error'] ?? ''}',
  _ => 'SDK preparation status unavailable',
};

class ProjectAnalysisLoad {
  const ProjectAnalysisLoad(this.report);
  final Map<String, dynamic> report;

  /// One resolved entry per tool, in the resolver's order.
  List<Map<String, dynamic>> get requirements =>
      (report['requirements'] as List? ?? []).cast<Map<String, dynamic>>();

  /// The entry an operator override declared, if there is one.
  Map<String, dynamic>? get overridden {
    for (final entry in requirements) {
      if (entry['override'] is Map) return entry;
    }
    return null;
  }

  Map<String, dynamic>? get override {
    final entry = overridden;
    return entry == null
        ? null
        : (entry['override'] as Map).cast<String, dynamic>();
  }

  Map<String, dynamic> assessmentOf(Map<String, dynamic> requirement) =>
      (requirement['assessment'] as Map? ?? {}).cast<String, dynamic>();

  /// An executable without a version rule proves availability, not compatibility.
  String readinessLabel(Map<String, dynamic> requirement) {
    final verdict = '${assessmentOf(requirement)['verdict'] ?? 'unknown'}';
    if (verdict != 'satisfied') return verdict;
    final override = requirement['override'] as Map?;
    final declarations = override == null
        ? [requirement, ...(requirement['discovered'] as List? ?? [])]
        : [override];
    final hasVersion = declarations.any(
      (entry) =>
          '${(entry as Map)['constraint'] ?? ''}'.isNotEmpty ||
          '${entry['selected'] ?? ''}'.isNotEmpty,
    );
    return hasVersion ? verdict : 'Installed — required version unknown';
  }
}

/// Requirement rows already show their reasons. Keep other findings visible
/// while avoiding a second copy of the same explanation beneath a component.
Iterable<Map> additionalComponentFindings(Map component) sync* {
  final shown = <String>{};
  for (final requirement in component['requirements'] as List? ?? []) {
    if (requirement['established'] != true) {
      shown.add('${requirement['reason'] ?? 'No declaration was found.'}');
    }
    for (final conflict in requirement['conflicts'] as List? ?? []) {
      shown.add('${conflict['reason']}');
    }
    final resolution = requirement['resolution'] as Map?;
    if (resolution?['reason'] != null) shown.add('${resolution!['reason']}');
  }
  for (final finding in component['unresolved'] as List? ?? []) {
    if (shown.add('${finding['reason']}')) yield finding as Map;
  }
}

Future<ProjectAnalysisLoad> loadProjectAnalysis(
  ProjectAnalysisRequest request,
  String path, {
  bool force = false,
}) async {
  final query = Uri(
    queryParameters: {'path': path, if (force) 'refresh': '1'},
  ).query;
  final report = Map<String, dynamic>.from(
    await request('projects/analysis?$query') as Map,
  );
  return ProjectAnalysisLoad(report);
}
