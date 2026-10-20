import 'native_task_ui.dart';

/// Submission provenance is independent of candidate checks and publication.
class TaskRevisions extends StatelessComponent {
  const TaskRevisions({super.key, required this.task});
  final Map task;

  @override
  Component build(BuildContext context) {
    final history = task['managed_revisions'];
    final revisions = history is Map ? history['revisions'] as List? ?? [] : [];
    if (revisions.isEmpty) return const SizedBox.shrink();
    return ExpansionTile(
      title: Text('Managed revisions (${revisions.length})'),
      children: [
        const Text(
          'Commit submission is separate from candidate checks and publication.',
        ),
        for (final revision in revisions.whereType<Map>())
          ExpansionTile(
            title: Text(
              '${revision['message']} · ${switch (revision['state']) {
                'complete' => 'Committed',
                'constructed' => 'Recovery pending',
                _ => 'Submission pending',
              }}${task['candidate_revision'] == revision['id'] ? ' · Current candidate' : ''}',
            ),
            children: [
              SelectableText(
                'Revision: ${revision['id']}\nCommit: ${revision['sha'] ?? 'Not created'}\nParent: ${revision['parent']}',
              ),
              Text(
                'Author: ${revision['author']} · Committer: ${revision['committer']}\n${revision['created_at']} · ${revision['file_count']} files',
              ),
              SelectableText(
                'Frozen policy: ${revision['policy_digest'] ?? 'Not recorded'}',
              ),
              if (revision['error'] != null) Text('${revision['error']}'),
            ],
          ),
        if (history is Map && history['next_before'] != null)
          Text('Showing the ${revisions.length} most recent submissions.'),
      ],
    );
  }
}
