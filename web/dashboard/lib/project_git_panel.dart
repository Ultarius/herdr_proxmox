import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'package:juice/juice.dart';
import 'clipboard_copy.dart';
import 'dashboard_bloc.dart';
import 'package:herdr_dashboard_state/integration.dart';
import 'integration_board.dart';
import 'request_id.dart';

class ProjectGitPanel extends StatefulWidget {
  const ProjectGitPanel({super.key, required this.path, required this.onOpen});
  final String path;
  final void Function(String) onOpen;
  @override
  State<ProjectGitPanel> createState() => _ProjectGitPanelState();
}

class _ProjectGitPanelState extends State<ProjectGitPanel> {
  late final GitBloc feature;
  StreamSubscription? featureSubscription;
  Map<String, dynamic>? get data => feature.state.value('data');
  String? get error => feature.state.error;
  bool get loading =>
      feature.state.reading.contains('status') ||
      feature.state.writing.contains('fetch');
  bool get showProgress => feature.state.values['showProgress'] == true;
  String? get notifying => feature.state.writing.contains('notify')
      ? feature.state.value<String>('notifying')
      : null;
  bool reviewingBase = false;
  bool get updatingBase =>
      reviewingBase || feature.state.writing.contains('baseUpdate');
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  String? lastNotice;
  @override
  void initState() {
    super.initState();
    feature = GitBloc(BlocScope.get<DashboardBloc>(), widget.path);
    featureSubscription = feature.stream.listen((_) {
      if (!mounted) return;
      final notice = feature.state.message;
      if (notice != null && notice != lastNotice)
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(notice)));
      lastNotice = notice;
      setState(() {});
    });
    load();
  }

  @override
  void dispose() {
    featureSubscription?.cancel();
    feature.dispose();
    super.dispose();
  }

  Future<void> load({bool quiet = false, bool fetch = false}) =>
      feature.load(quiet: quiet, fetch: fetch);

  Future<void> updateBase() async {
    final initial = data?['base_update'];
    if (initial is! Map || updatingBase) return;
    final base = TextEditingController(text: '${initial['base']}');
    final branch = TextEditingController(text: '${initial['branch']}');
    final selected = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Update base branch'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: branch,
                decoration: const InputDecoration(
                  labelText: 'Expected destination branch',
                ),
              ),
              TextField(
                controller: base,
                decoration: const InputDecoration(
                  labelText: 'Remote-tracking comparison ref',
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                'Only the shared checkout will advance. Local changes, divergent history and active work prevent the update. Agent worktrees keep their own branches.',
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Review exact target'),
          ),
        ],
      ),
    );
    final selectedBase = base.text.trim(), selectedBranch = branch.text.trim();
    base.dispose();
    branch.dispose();
    if (selected != true || !mounted) return;
    final epoch = connection.generation;
    setState(() {
      reviewingBase = true;
    });
    try {
      final plan = await feature.inspectBase(
        '${initial['path']}',
        selectedBase,
        selectedBranch,
      );
      if (plan == null || !(mounted && epoch == connection.generation)) return;
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text('Advance $selectedBranch?'),
          content: SelectableText(
            'Checkout: ${plan['path']}\nCurrent: ${plan['head']}\nTarget: ${plan['target']}\nComparison: $selectedBase\n\nThe old HEAD will be pinned for recovery and the request audited. This is a fast-forward only.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Update base branch'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted || epoch != connection.generation)
        return;
      final requestId = newRequestId();
      await feature.updateBase(Map<String, dynamic>.from(plan), requestId);
    } catch (e) {
      if (mounted && epoch == connection.generation)
        feature.reportError('git', e);
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() => reviewingBase = false);
    }
  }

  Future<void> diff(Map tree, Map file) async {
    final epoch = connection.generation;
    try {
      final result = await feature.diff(tree, file);
      if (result == null) return;
      if (!mounted || epoch != connection.generation) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(file['path'] as String),
          content: SizedBox(
            width: 900,
            height: 500,
            child: SingleChildScrollView(
              child: SelectableText(
                '${result['truncated'] == true ? 'Showing the first 64 KB.\n\n' : ''}${(result['diff'] as String).isEmpty ? 'No tracked diff. New files can be opened in Files.' : result['diff']}',
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ),
          actions: [
            if (file['kind'] != 'deleted')
              TextButton.icon(
                onPressed: () {
                  Navigator.pop(context);
                  widget.onOpen('${tree['path']}/${file['path']}');
                },
                icon: const Icon(Icons.description_outlined),
                label: const Text('Open file'),
              ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close'),
            ),
          ],
        ),
      );
    } catch (e) {
      if (mounted) feature.reportError('git', e);
    }
  }

  IntegrationNotice noticeFor(Map tree, [Map? agent]) => IntegrationNotice(
    profileId: '${agent?['profile_id'] ?? tree['profile_id'] ?? ''}',
    name: '${agent?['display_name'] ?? agent?['name'] ?? tree['name'] ?? ''}',
    path: '${tree['cwd']}',
    branch: '${tree['branch'] ?? ''}',
    base: tree['base'] as String?,
    behind: tree['base_behind'] as int?,
    ahead: tree['base_ahead'] as int?,
    conflicts: (tree['conflicts'] as int?) ?? 0,
    dirty:
        (tree['counts'] as Map?)?.values.fold<int>(
          0,
          (a, b) => a + (b as int),
        ) !=
        0,
  );

  Future<void> notify(Map tree, Map agent) async =>
      feature.notify(noticeFor(tree, agent));

  Widget failedCard(Map tree) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.error_outline, color: Theme.of(context).colorScheme.error),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${tree['path']}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 4),
                Text('${tree['error']}'),
              ],
            ),
          ),
        ],
      ),
    ),
  );

  Widget count(String label, dynamic value, Color color, IconData icon) => Chip(
    avatar: Icon(icon, size: 16, color: color),
    label: Text('$value $label', style: TextStyle(color: color)),
    side: BorderSide(color: color.withValues(alpha: .3)),
  );
  @override
  Widget build(BuildContext context) => ListView(
    children: [
      Row(
        children: [
          Expanded(
            child: Text(
              'Repositories & worktrees',
              style: Theme.of(context).textTheme.titleLarge,
            ),
          ),
          IconButton(
            tooltip: 'Refresh Git status',
            onPressed: loading ? null : load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      const Text(
        'Local changes refresh every 10 seconds. Tracking counts use the last fetched remote state.',
      ),
      const SizedBox(height: 8),
      Wrap(
        spacing: 12,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          OutlinedButton.icon(
            onPressed: loading || data?['repository'] != true
                ? null
                : () => load(fetch: true),
            icon: const Icon(Icons.cloud_download_outlined),
            label: const Text('Fetch remote updates'),
          ),
          Text(
            data?['last_fetch'] == null
                ? 'Remote has not been checked here.'
                : 'Last fetch: ${DateTime.tryParse(data!['last_fetch'])?.toLocal() ?? data!['last_fetch']}',
          ),
        ],
      ),
      const SizedBox(height: 12),
      if (loading && showProgress) const LinearProgressIndicator(),
      if (error != null)
        Text(
          error!,
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      if (data?['fetch_error'] != null)
        Text(
          'Fetch failed: ${data!['fetch_error']}',
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      if (data?['repository'] == false)
        const Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'Open a Git repository folder to see its worktrees and changes.',
          ),
        ),
      if (data?['repository_path'] != null)
        IntegrationBoard(
          key: ValueKey(data!['repository_path']),
          repository: data!['repository_path'] as String,
        ),
      for (final tree in data?['worktrees'] as List? ?? [])
        (tree['error'] != null ? failedCard(tree) : treeCard(tree)),
      if ((data?['base_updates'] as List? ?? []).isNotEmpty)
        ExpansionTile(
          title: const Text('Base update audit & recovery'),
          children: [
            for (final entry in data!['base_updates'])
              ListTile(
                title: Text(
                  '${entry['selection']?['branch']} · ${entry['state']}',
                ),
                subtitle: SelectableText(
                  '${entry['actor']} · ${entry['at']}\n${entry['old_head']} → ${entry['target']}\n${entry['reason'] ?? entry['note'] ?? ''}\nRecovery: ${entry['recovery_ref'] ?? 'No branch change recorded'}\nRecover into a separate checkout; preserve the current branch.',
                ),
              ),
          ],
        ),
      if (data?['limited'] == true)
        const Text('Showing the first 20 checkouts.'),
    ],
  );

  Widget treeCard(Map tree) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const CircleAvatar(child: Icon(Icons.account_tree_outlined)),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${tree['branch'] == 'HEAD' ? 'Detached checkout' : tree['branch']}',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    Text(
                      '${tree['path']} · ${tree['commit']}',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Browse checkout files',
                onPressed: () => widget.onOpen(tree['path'] as String),
                icon: const Icon(Icons.folder_open_outlined),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              count(
                'added',
                tree['counts']['added'],
                const Color(0xff70d69a),
                Icons.add_box_outlined,
              ),
              count(
                'deleted',
                tree['counts']['deleted'],
                const Color(0xffff8585),
                Icons.indeterminate_check_box_outlined,
              ),
              count(
                'modified',
                tree['counts']['modified'],
                const Color(0xfff1c75b),
                Icons.edit_outlined,
              ),
            ],
          ),
          if (tree['path'] == data?['base_update']?['path'])
            OutlinedButton.icon(
              onPressed:
                  updatingBase ||
                      loading ||
                      connection.operator.value['role'] != 'admin'
                  ? null
                  : updateBase,
              icon: const Icon(Icons.sync),
              label: Text(
                updatingBase ? 'Updating base branch…' : 'Update base branch',
              ),
            ),
          if (tree['upstream'] != null)
            Text(
              '${tree['upstream']} · ${tree['ahead']} ahead · ${tree['behind']} behind',
              style: Theme.of(context).textTheme.bodySmall,
            )
          else
            const Text(
              'No upstream tracking branch',
              style: TextStyle(fontSize: 12),
            ),
          if ((tree['base_behind'] as int? ?? 0) > 0) ...[
            const SizedBox(height: 12),
            Text(
              tree['checkout_role'] == 'coordinator'
                  ? '${tree['base_behind']} commits behind ${tree['base']}. Coordinator checkout — reporting is independent of its age.'
                  : tree['checkout_role'] == 'shared'
                  ? 'Main behind · ${tree['base_behind']} commits. Update base branch when ready.'
                  : '${tree['base_behind']} commits behind ${tree['base']} — worker needs to incorporate changes when ready.',
              style: const TextStyle(color: Color(0xfff1c75b)),
            ),
            if (tree['checkout_role'] != 'coordinator')
              TextButton.icon(
                onPressed: () async {
                  final message = await copyTextOrDownload(
                    noticeFor(tree).instructions,
                    'merge-instructions.md',
                    copied: 'Merge instructions copied.',
                    downloaded:
                        'Clipboard unavailable over HTTP; merge instructions downloaded instead.',
                  );
                  if (context.mounted)
                    ScaffoldMessenger.of(
                      context,
                    ).showSnackBar(SnackBar(content: Text(message)));
                },
                icon: const Icon(Icons.copy_outlined),
                label: const Text('Copy merge instructions'),
              ),
          ],
          if ((tree['conflicts'] as int? ?? 0) > 0)
            Text(
              '${tree['conflicts']} unresolved conflicts',
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          for (final agent in connection.state.agents.where(
            (a) => a['cwd'] == tree['cwd'] && tree['cwd'] != null,
          ))
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'Agent: ${agent['display_name'] ?? agent['name']} · ${agent['agent_status'] ?? 'unknown'}',
              ),
            ),
          if ((tree['base_behind'] as int? ?? 0) > 0 &&
              tree['checkout_role'] != 'coordinator')
            for (final agent in connection.state.agents.where(
              (a) => a['cwd'] == tree['cwd'] && a['profile_id'] != null,
            ))
              OutlinedButton.icon(
                onPressed:
                    notifying == agent['profile_id'] ||
                        !agentCanIntegrate(connection, '${agent['profile_id']}')
                    ? null
                    : () => notify(tree, agent),
                icon: const Icon(Icons.notifications_outlined),
                label: Text(
                  'Ask ${agent['display_name'] ?? agent['name']} to integrate main',
                ),
              ),
          if ((tree['changes'] as List).isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 12),
              child: Text('Working tree clean'),
            )
          else
            ExpansionTile(
              key: PageStorageKey('git-changes-${tree['path']}'),
              tilePadding: EdgeInsets.zero,
              title: Text('${(tree['changes'] as List).length} changed files'),
              children: [
                for (final file in tree['changes'])
                  ListTile(
                    dense: true,
                    leading: Text(
                      file['status'] as String,
                      style: const TextStyle(fontFamily: 'monospace'),
                    ),
                    title: Text(file['path'] as String),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => diff(tree, file),
                  ),
              ],
            ),
        ],
      ),
    ),
  );
}
