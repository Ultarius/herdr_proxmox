/// Live work belongs to an exact task, launch run and assigned profile.
/// A profile's general activity alone cannot identify which task it is doing.
String? taskLiveActivity(Map task, Map? execution) {
  if (execution == null ||
      task['run_id'] is! String ||
      (task['run_id'] as String).isEmpty ||
      task['profile_id'] == null ||
      execution['task_id'] != task['id'] ||
      execution['run_id'] != task['run_id'] ||
      execution['profile_id'] != task['profile_id']) {
    return null;
  }
  return execution['activity']?.toString();
}

/// A work phase supplements the lifecycle (for example, an open PR can be
/// undergoing a branch repair). Idle sessions never claim to be working.
typedef TaskWork = ({String activity, String label});

TaskWork? taskWork(Map task, Map? execution) {
  final activity = taskLiveActivity(task, execution);
  if (activity == 'attention')
    return (activity: 'attention', label: 'Needs attention');
  if (activity != 'working') return null;
  final repair = task['pr_repair'];
  if (repair is Map && ['starting', 'running'].contains(repair['state'])) {
    return (
      activity: activity!,
      label: repair['state'] == 'starting'
          ? 'Starting branch update'
          : 'Resolving conflicts · running checks',
    );
  }
  return (activity: activity!, label: 'Working on task');
}

/// Bindings are observations from one snapshot, never a cache of liveness.
/// Reject every claim for a duplicated task until a later snapshot resolves it.
Map<String, Map<String, dynamic>> taskExecutionBindings(
  Iterable<Map<String, dynamic>> agents, {
  required bool available,
}) {
  if (!available) return {};
  final bindings = <String, Map<String, dynamic>>{};
  final ambiguous = <String>{};
  for (final agent in agents) {
    final task = agent['task_id'];
    if (task is! String ||
        task.isEmpty ||
        agent['run_id'] == null ||
        agent['profile_id'] == null ||
        ambiguous.contains(task))
      continue;
    if (bindings.containsKey(task)) {
      bindings.remove(task);
      ambiguous.add(task);
      continue;
    }
    final activity = switch (agent['agent_status'] ?? agent['state']) {
      'idle' || 'done' => 'idle',
      'working' || 'running' || 'busy' || 'thinking' => 'working',
      'blocked' => 'attention',
      _ => 'offline',
    };
    bindings[task] = {
      'task_id': task,
      'run_id': agent['run_id'],
      'profile_id': agent['profile_id'],
      'activity': activity,
    };
  }
  return bindings;
}
