import 'package:juice/juice.dart';
import 'clipboard_copy.dart';
import 'dashboard_bloc.dart';
import 'integration.dart';
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
  Map<String, dynamic>? data;
  String? error;
  bool loading = false;
  Timer? timer;
  bool showProgress = true;
  String? notifying;
  bool updatingBase = false;
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  @override
  void initState() {
    super.initState();
    load();
    timer = Timer.periodic(
      const Duration(seconds: 10),
      (_) => load(quiet: true),
    );
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> load({bool quiet = false, bool fetch = false}) async {
    if (loading) return;
    final epoch = connection.generation;
    setState(() {
      loading = true;
      showProgress = !quiet;
      if (!quiet) error = null;
    });
    try {
      final result = await connection.request('projects/git', {
        'path': widget.path,
        'action': fetch ? 'fetch' : 'status',
      });
      if (mounted && epoch == connection.generation)
        setState(() => data = Map<String, dynamic>.from(result));
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

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
      updatingBase = true;
      error = null;
    });
    try {
      final inspected = await connection.request('projects/git', {
        'path': initial['path'],
        'base': selectedBase,
      });
      if (!mounted || epoch != connection.generation) return;
      final plan = inspected['base_update'];
      if (plan is! Map || plan['branch'] != selectedBranch)
        throw Exception('Destination branch changed. Refresh before updating.');
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
      final result = await connection.request('projects/git', {
        ...Map<String, dynamic>.from(plan),
        'action': 'update_base',
        'request_id': requestId,
      });
      if (!mounted || epoch != connection.generation) return;
      if (result['state'] != 'complete')
        throw Exception(result['reason'] ?? 'Base update requires review.');
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Base checkout updated. Agent worktrees continue independently.',
          ),
        ),
      );
      await load();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = e.toString());
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() => updatingBase = false);
    }
  }

  Future<void> diff(Map tree, Map file) async {
    final epoch = connection.generation;
    try {
      final result = await connection.request('projects/git', {
        'path': tree['path'],
        'file': file['path'],
      });
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
      if (mounted) setState(() => error = e.toString());
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

  Future<void> notify(Map tree, Map agent) async {
    setState(() {
      notifying = '${agent['profile_id']}';
      error = null;
    });
    try {
      await requestIntegration(connection, noticeFor(tree, agent));
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Merge request sent. Follow progress in the organization chat.',
            ),
          ),
        );
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => notifying = null);
    }
  }

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
              '${tree['base_behind']} commits behind ${tree['base']} — integrate when your work is ready.',
              style: const TextStyle(color: Color(0xfff1c75b)),
            ),
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
          if ((tree['base_behind'] as int? ?? 0) > 0)
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
