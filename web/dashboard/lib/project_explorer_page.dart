import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'package:juice/juice.dart';
import 'clipboard_copy.dart';
import 'dashboard_bloc.dart';
import 'project_git_panel.dart';
import 'project_analysis_card.dart';

class ProjectExplorerPage extends StatefulWidget {
  const ProjectExplorerPage({super.key, this.initialPath = ''});
  final String initialPath;
  @override
  State<ProjectExplorerPage> createState() => _ProjectExplorerPageState();
}

class _ProjectExplorerPageState extends State<ProjectExplorerPage> {
  late final ExplorerBloc feature;
  StreamSubscription? featureSubscription;
  Map<String, dynamic>? get directory => feature.state.value('directory');
  Map<String, dynamic>? get preview => feature.state.value('preview');
  String get path => feature.state.value<String>('path') ?? '';
  String filter = '';
  String? get error => feature.state.error;
  bool get busy => feature.state.loading;
  bool showHidden = false, gitView = false;
  @override
  void initState() {
    super.initState();
    feature = ExplorerBloc(BlocScope.get<DashboardBloc>());
    featureSubscription = feature.stream.listen((_) {
      if (mounted) setState(() {});
    });
    // A notice can link straight to one checkout's Git changes.
    if (widget.initialPath.isNotEmpty) gitView = true;
    open(widget.initialPath);
  }

  @override
  void dispose() {
    featureSubscription?.cancel();
    feature.dispose();
    super.dispose();
  }

  Future<void> open(String selected) async {
    if (await feature.open(selected) == true && mounted)
      setState(() => filter = '');
  }

  String size(dynamic bytes) {
    if (bytes == null) return '—';
    final n = (bytes as num).toDouble();
    return n < 1024
        ? '${n.toInt()} B'
        : n < 1048576
        ? '${(n / 1024).toStringAsFixed(1)} KB'
        : '${(n / 1048576).toStringAsFixed(1)} MB';
  }

  Widget filePreview() => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.description_outlined),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  '${preview!['path']}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              IconButton(
                tooltip: 'Copy file path',
                onPressed: () async {
                  final message = await copyTextOrDownload(
                    '${directory?['root']}/${preview!['path']}',
                    'path.txt',
                    copied: 'Path copied.',
                    downloaded:
                        'Clipboard unavailable over HTTP; path downloaded instead.',
                  );
                  if (context.mounted)
                    ScaffoldMessenger.of(
                      context,
                    ).showSnackBar(SnackBar(content: Text(message)));
                },
                icon: const Icon(Icons.copy_outlined),
              ),
              IconButton(
                tooltip: 'Close preview',
                onPressed: feature.closePreview,
                icon: const Icon(Icons.close),
              ),
            ],
          ),
          Text(size(preview!['size'])),
          const Divider(height: 24),
          if (preview!['binary'] == true)
            const Text('Preview unavailable for binary files.')
          else ...[
            if (preview!['truncated'] == true)
              const Padding(
                padding: EdgeInsets.only(bottom: 12),
                child: Text('Showing the first 64 KB.'),
              ),
            Expanded(
              child: SingleChildScrollView(
                child: SelectableText(
                  preview!['content'] as String,
                  style: const TextStyle(
                    fontFamily: 'RobotoMono',
                    fontSize: 13,
                    height: 1.6,
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final entries = (directory?['entries'] as List? ?? [])
        .where(
          (e) =>
              (showHidden || !('${e['name']}'.startsWith('.'))) &&
              '${e['name']}'.toLowerCase().contains(filter.toLowerCase()),
        )
        .toList();
    final repositoryRoot =
        path.isNotEmpty &&
        (directory?['entries'] as List? ?? []).any(
          (e) => e is Map && e['name'] == '.git',
        );
    final segments = path
        .split('/')
        .where((p) => p.isNotEmpty && p != '.')
        .toList();
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Project explorer',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Browse repositories and worktrees in the container. File previews are read-only.',
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Refresh folder',
                onPressed: busy ? null : () => open(path),
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          const SizedBox(height: 16),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(
                value: false,
                label: Text('Files'),
                icon: Icon(Icons.folder_outlined),
              ),
              ButtonSegment(
                value: true,
                label: Text('Git changes'),
                icon: Icon(Icons.account_tree_outlined),
              ),
            ],
            selected: {gitView},
            onSelectionChanged: (value) =>
                setState(() => gitView = value.single),
          ),
          const SizedBox(height: 12),
          Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              TextButton.icon(
                onPressed: busy ? null : () => open(''),
                icon: const Icon(Icons.home_outlined),
                label: const Text('Projects'),
              ),
              for (var i = 0; i < segments.length; i++) ...[
                const Icon(Icons.chevron_right, size: 18),
                TextButton(
                  onPressed: busy
                      ? null
                      : () => open(segments.take(i + 1).join('/')),
                  child: Text(segments[i]),
                ),
              ],
            ],
          ),
          if (directory != null)
            SelectableText(
              '${directory!['root']}${path.isEmpty ? '' : '/$path'}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 16,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SizedBox(
                width: 280,
                child: TextField(
                  key: ValueKey(path),
                  onChanged: (v) => setState(() => filter = v),
                  decoration: const InputDecoration(
                    prefixIcon: Icon(Icons.search),
                    hintText: 'Filter this folder',
                    isDense: true,
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
              FilterChip(
                label: const Text('Hidden files'),
                selected: showHidden,
                onSelected: (v) => setState(() => showHidden = v),
              ),
              Text('${entries.length} items'),
            ],
          ),
          const SizedBox(height: 12),
          if (repositoryRoot)
            ProjectAnalysisCard(key: ValueKey('analysis-$path'), path: path),
          if (busy) const LinearProgressIndicator(),
          if (error != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(
                error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          if (directory?['limited'] == true)
            const Text(
              'Listing limited to 1,000 items. Open a subfolder to explore further.',
            ),
          Expanded(
            child: gitView
                ? ProjectGitPanel(
                    key: ValueKey(path),
                    path: path,
                    onOpen: (selected) {
                      setState(() => gitView = false);
                      open(selected);
                    },
                  )
                : LayoutBuilder(
                    builder: (context, constraints) {
                      final wide = constraints.maxWidth >= 950;
                      final listing = Card(
                        child: Column(
                          children: [
                            Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 20,
                                vertical: 12,
                              ),
                              child: Row(
                                children: [
                                  const Expanded(
                                    child: Text(
                                      'Name',
                                      style: TextStyle(
                                        fontWeight: FontWeight.bold,
                                      ),
                                    ),
                                  ),
                                  if (wide)
                                    const SizedBox(
                                      width: 140,
                                      child: Text('Modified'),
                                    ),
                                  const SizedBox(
                                    width: 80,
                                    child: Text(
                                      'Size',
                                      textAlign: TextAlign.right,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            const Divider(height: 1),
                            Expanded(
                              child: entries.isEmpty
                                  ? Center(
                                      child: Text(
                                        filter.isNotEmpty
                                            ? 'No entries match your filter.'
                                            : (directory?['entries'] as List? ??
                                                      [])
                                                  .isNotEmpty
                                            ? 'Only hidden entries are present. Enable Show hidden.'
                                            : 'This folder is empty.',
                                      ),
                                    )
                                  : ListView.builder(
                                      itemCount: entries.length,
                                      itemBuilder: (context, index) {
                                        final entry = entries[index];
                                        final folder =
                                            entry['kind'] == 'folder';
                                        final enabled =
                                            folder || entry['kind'] == 'file';
                                        final modified = DateTime.tryParse(
                                          '${entry['modified']}',
                                        )?.toLocal();
                                        return ListTile(
                                          selected:
                                              preview?['path'] == entry['path'],
                                          leading: Icon(
                                            folder
                                                ? Icons.folder_outlined
                                                : entry['kind'] == 'link'
                                                ? Icons.link
                                                : Icons.description_outlined,
                                            color: folder
                                                ? Theme.of(
                                                    context,
                                                  ).colorScheme.primary
                                                : null,
                                          ),
                                          title: Text(
                                            '${entry['name']}',
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis,
                                          ),
                                          subtitle: enabled
                                              ? null
                                              : Text(
                                                  '${entry['kind']} · Cannot be opened',
                                                ),
                                          trailing: Row(
                                            mainAxisSize: MainAxisSize.min,
                                            children: [
                                              if (wide)
                                                SizedBox(
                                                  width: 140,
                                                  child: Text(
                                                    modified == null
                                                        ? ''
                                                        : '${modified.year}-${modified.month.toString().padLeft(2, '0')}-${modified.day.toString().padLeft(2, '0')}',
                                                    style: Theme.of(
                                                      context,
                                                    ).textTheme.bodySmall,
                                                  ),
                                                ),
                                              SizedBox(
                                                width: 80,
                                                child: Text(
                                                  size(entry['size']),
                                                  textAlign: TextAlign.right,
                                                  style: Theme.of(
                                                    context,
                                                  ).textTheme.bodySmall,
                                                ),
                                              ),
                                            ],
                                          ),
                                          onTap: busy || !enabled
                                              ? null
                                              : () => open(
                                                  entry['path'] as String,
                                                ),
                                        );
                                      },
                                    ),
                            ),
                          ],
                        ),
                      );
                      if (preview == null) return listing;
                      return wide
                          ? Row(
                              children: [
                                Expanded(child: listing),
                                const SizedBox(width: 16),
                                Expanded(child: filePreview()),
                              ],
                            )
                          : filePreview();
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
