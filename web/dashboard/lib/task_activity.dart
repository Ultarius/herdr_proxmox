import 'package:flutter/material.dart';

/// JSON collections are dynamic: extension getters must not be dispatched on them.
String taskAgentName(
  Map task, {
  String fallback = 'Assigned agent identity unavailable',
}) {
  final assigned = task['assigned_agent'];
  if (assigned is Map &&
      assigned['name'] is String &&
      (assigned['name'] as String).trim().isNotEmpty) {
    return assigned['name'] as String;
  }
  final participants = task['participants'];
  if (participants is List) {
    for (final participant in participants) {
      if (participant is Map &&
          participant['name'] is String &&
          (participant['name'] as String).trim().isNotEmpty) {
        return participant['name'] as String;
      }
    }
  }
  return fallback;
}

String taskNextStep(Map task) {
  if (task['automation_error'] != null)
    return 'Automation needs attention: ${task['automation_error']}';
  if (task['state'] == 'draft') return 'Ready to launch the assigned agent.';
  if (task['state'] == 'implementing')
    return 'Implementation in progress; awaiting a committed candidate.';
  if (task['state'] == 'merged')
    return 'Merged on GitHub. Updating the base and deployment are separate.';
  if (task['state'] == 'closed') return 'Task closed.';
  final target = task['merge_sha'] ?? task['head_sha'];
  final build = (task['builds'] as Map?)?[target];
  if (build is Map) {
    if (build['state'] == 'complete')
      return build['required_checks_verified'] == true
          ? 'Current candidate validated; ready for review.'
          : 'Validation finished without verified required checks; inspect the evidence.';
    if (['failed', 'error', 'interrupted'].contains(build['state']))
      return 'Current candidate validation needs attention.';
    if (['queued', 'running'].contains(build['state']))
      return 'Current candidate validation ${build['state']}.';
  }
  if (task['state'] == 'pr_open')
    return 'Pull request ${task['pull']?['draft'] == true ? 'is a draft' : 'awaits review'}.';
  if (task['state'] == 'published')
    return 'Branch published; open a draft pull request.';
  return 'Validation pending for the latest candidate.';
}

const activityLabels = {
  'create': 'Task created',
  'launch': 'Implementation started',
  'candidate': 'Candidate captured',
  'build': 'Validation requested',
  'build_result': 'Validation result received',
  'publish': 'Branch published',
  'pull': 'Pull request opened',
  'refresh': 'Pull request updated',
  'pull_status': 'Pull request status changed',
  'policy': 'Automation policy changed',
  'completion_verified': 'Completion receipt verified',
  'automation_waiting': 'Automation needs attention',
  'automation_resumed': 'Automation resumed',
  'build_evidence_mismatch': 'Build evidence did not match the commit',
  'release': 'Run binding released',
  'recover': 'Reply recovered',
};

String activityLabel(Object? action) {
  final value = '${action ?? ''}';
  return activityLabels[value] ?? value.replaceAll('_', ' ');
}

/// Which section holds an entry's evidence. The activity list is the index.
String activitySection(Object? action) {
  switch ('${action ?? ''}') {
    case 'launch':
    case 'release':
      return 'Activity & agents';
    case 'candidate':
    case 'completion_verified':
    case 'publish':
    case 'pull':
    case 'refresh':
    case 'pull_status':
      return 'Changes';
    case 'build':
    case 'build_result':
    case 'build_evidence_mismatch':
      return 'Checks & builds';
    default:
      return 'Overview';
  }
}

String shortSha(Object? value) {
  final sha = '${value ?? ''}';
  if (sha.isEmpty || sha == 'null') return '';
  return sha.substring(0, sha.length.clamp(0, 8));
}

class TaskActivity extends StatelessWidget {
  const TaskActivity({super.key, required this.task, this.onOpen});
  final Map task;
  final ValueChanged<String>? onOpen;
  @override
  Widget build(BuildContext context) {
    final participants = [
      for (final p in task['participants'] as List? ?? [])
        if (p is Map) p,
    ];
    final grouped = <Map>[];
    for (final entry in task['audit'] as List? ?? []) {
      if (entry is! Map) continue;
      if (grouped.isNotEmpty &&
          grouped.last['action'] == entry['action'] &&
          grouped.last['head_sha'] == entry['head_sha'] &&
          grouped.last['actor'] == entry['actor']) {
        grouped.last = {...entry, 'count': (grouped.last['count'] as int) + 1};
      } else {
        grouped.add({...entry, 'count': 1});
      }
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Agent participation',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        if (participants.isEmpty)
          const Text('No historical agent participation recorded yet.'),
        for (final person in participants)
          Card(
            child: ExpansionTile(
              title: Text('${person['name'] ?? 'Agent identity unavailable'}'),
              subtitle: Text(
                '${person['role'] ?? ''} \u00b7 ${person['runtime'] ?? ''}',
              ),
              children: [
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SelectableText(
                        'Assigned ${person['assigned_at'] ?? 'at an unknown time'}\nSession: ${person['run_id'] ?? 'not recorded'}\nIdentity source: ${person['provenance'] ?? 'task record'}',
                      ),
                      if (person['model'] != null)
                        Text(
                          'Model: ${person['provider'] ?? ''}/${person['model']}',
                        ),
                      for (final session in task['sessions'] as List? ?? [])
                        if (session is Map &&
                            session['id'] == person['run_id']) ...[
                          Text(
                            'Session status: ${session['state'] ?? 'unknown'}',
                          ),
                          if (session['error'] != null &&
                              '${session['error']}'.isNotEmpty)
                            Text('Session issue: ${session['error']}'),
                          for (final previous
                              in session['history'] as List? ?? [])
                            if (previous is Map)
                              Text(
                                'Previous session ${previous['alias']} closed ${previous['session_closed_at']}',
                              ),
                        ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        if (task['completion_receipt'] is Map)
          ExpansionTile(
            title: const Text('Worker completion report'),
            children: [
              SelectableText(
                'Reported commit: ${task['completion_receipt']['commit']}',
              ),
              const Text(
                'Worker-reported checks; independent build evidence is on Checks & builds.',
              ),
              for (final check
                  in task['completion_receipt']['tests'] as List? ?? [])
                SelectableText('$check'),
            ],
          ),
        const SizedBox(height: 16),
        Text('Task activity', style: Theme.of(context).textTheme.titleLarge),
        if (grouped.isEmpty) const Text('No activity recorded.'),
        for (final entry in grouped.reversed)
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.circle_outlined, size: 20),
            onTap: onOpen == null
                ? null
                : () => onOpen!(activitySection(entry['action'])),
            trailing: onOpen == null ? null : const Icon(Icons.chevron_right),
            title: Text(
              '${activityLabel(entry['action'])}${entry['count'] > 1 ? ' (${entry['count']} records)' : ''}',
            ),
            subtitle: Text(
              '${entry['actor'] == 'automatic_task_policy' ? 'Task automation' : entry['actor']} \u00b7 ${entry['at']}'
              '${shortSha(entry['head_sha']).isEmpty ? (entry['state'] == null ? '' : ' \u00b7 ${entry['state']}') : ' \u00b7 ${shortSha(entry['head_sha'])}'}',
            ),
          ),
        ExpansionTile(
          title: const Text('Retained audit records (up to 500)'),
          children: [
            for (final entry in task['audit'] as List? ?? [])
              SelectableText('$entry'),
          ],
        ),
      ],
    );
  }
}
