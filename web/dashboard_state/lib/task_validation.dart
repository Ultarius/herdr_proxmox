/// Match the gateway: only a merged task validates its merge result.
Object? taskValidationTarget(Map task) =>
    task['state'] == 'merged' ? task['merge_sha'] : task['head_sha'];

/// Gateway assessment is bound to the displayed target. Older gateways omit it.
bool taskValidationNeedsRecheck(Map task, Object? target) {
  final policy = task['validation_policy'];
  return target != null &&
      policy is Map &&
      (policy['target'] != target || policy['matches'] != true);
}
