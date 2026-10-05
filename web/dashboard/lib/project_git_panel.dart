import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

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

  Future<void> load({bool quiet = false}) async {
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
      const SizedBox(height: 12),
      if (loading && showProgress) const LinearProgressIndicator(),
      if (error != null)
        Text(
          error!,
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      if (data?['repository'] == false)
        const Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'Open a Git repository folder to see its worktrees and changes.',
          ),
        ),
      for (final tree in data?['worktrees'] as List? ?? [])
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const CircleAvatar(
                      child: Icon(Icons.account_tree_outlined),
                    ),
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
                for (final agent in connection.state.agents.where(
                  (a) => a['cwd'] == tree['cwd'] && tree['cwd'] != null,
                ))
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      'Agent: ${agent['display_name'] ?? agent['name']} · ${agent['agent_status'] ?? 'unknown'}',
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
                    title: Text(
                      '${(tree['changes'] as List).length} changed files',
                    ),
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
        ),
      if (data?['limited'] == true)
        const Text('Showing the first 20 checkouts.'),
    ],
  );
}
