/// Candidate-scoped review shared by both dashboards.
typedef ValidationEnvironmentRequest =
    Future<dynamic> Function(String path, [Map<String, dynamic>? body]);

bool needsValidationEnvironmentReview(Map task) {
  if (!['review_ready', 'published', 'pr_open'].contains(task['state']) ||
      task['head_sha'] == null ||
      task['run_id'] == null)
    return false;
  final builds = task['builds'];
  final build = builds is Map ? builds[task['head_sha']] : null;
  final status = task['validation_environment_status'];
  if (status is Map) return status['state'] == 'review_required';
  return build is Map && build['recovery'] == 'review_validation_environment';
}

bool hasApprovedValidationEnvironment(Map task) =>
    ['review_ready', 'published', 'pr_open'].contains(task['state']) &&
    task['validation_environment_status'] is Map &&
    task['validation_environment_status']['state'] == 'approved';

Future<Map<String, dynamic>> reviewValidationEnvironment(
  ValidationEnvironmentRequest request,
  String taskId,
  String requestId,
) async {
  final response = await request('tasks/validation_environment', {
    'task_id': taskId,
    'mode': 'review',
    'request_id': requestId,
  });
  if (response is! Map || response['validation_environment_review'] is! Map) {
    throw StateError(
      response is Map
          ? '${response['error'] ?? response['message'] ?? 'The gateway did not return an environment review.'}'
          : 'The gateway did not return an environment review.',
    );
  }
  return Map<String, dynamic>.from(
    response['validation_environment_review'] as Map,
  );
}

Map<String, dynamic> validationEnvironmentApproval(Map review) => {
  'task_id': review['task_id'],
  'candidate': review['candidate'],
  'review_digest': review['review_digest'],
  'mode': 'approve',
  'inspected': true,
};

String validationEnvironmentReviewText(Map review) => [
  'Candidate: ${review['candidate']}',
  'Candidate revision: ${review['candidate_revision'] ?? 'Not recorded'}',
  'Task execution: ${review['run_id'] ?? 'Not recorded'}',
  'Prior approval: ${review['prior_approval'] ?? 'None'}',
  'Approved project commit: ${review['approved_commit']}',
  'Previous session plan: ${review['previous_plan_digest'] ?? 'Not recorded'}',
  'Prepared plan: ${review['plan_digest']}',
  '',
  'SDKs:',
  for (final sdk in review['toolchains'] as List? ?? [])
    '${sdk['component']}: ${sdk['name']} ${sdk['required']}',
  '',
  'Approved checks (can execute project code):',
  for (final check in review['recipes'] as List? ?? [])
    '${check['cwd']}: ${(check['arguments'] as List).join(' ')}'
        '${'${check['evidence'] ?? ''}'.isEmpty ? '' : ' · ${check['evidence']}'}',
  '',
  'Approval applies to validation of this exact commit. '
      'The agent session keeps its original environment. Approval does not run checks.',
  'This review is temporary. After reloading, review again before approving; saved approvals remain recorded.',
].join('\n');
