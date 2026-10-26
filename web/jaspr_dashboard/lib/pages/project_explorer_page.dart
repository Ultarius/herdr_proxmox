import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'operations_support.dart';
import 'project_git_panel.dart';
import 'project_analysis_card.dart';
import 'ops_ui.dart';

class ProjectExplorerPage extends StatefulComponent {
  const ProjectExplorerPage({super.key, this.initialPath = ''});
  final String initialPath;
  @override
  State<ProjectExplorerPage> createState() => _ExplorerState();
}

class _ExplorerState extends OperationsState<ProjectExplorerPage> {
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
    feature = ExplorerBloc(connection);
    featureSubscription = feature.stream.listen((_) {
      if (mounted) setState(() {});
    });
    gitView = component.initialPath.isNotEmpty;
    open(component.initialPath);
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

  Future<void> copyPath() async {
    final result = await copyText(
      '${directory?['root']}/${preview!['path']}',
      filename: 'path.txt',
    );
    if (mounted) setState(() => message = result);
  }

  Component viewTab(String label, String iconName, bool selected, String id) =>
      el(
        'button',
        [icon(iconName), text(label)],
        classes: 'tabs__tab',
        attributes: {
          'type': 'button',
          'role': 'tab',
          'id': id,
          'aria-selected': '$selected',
          'tabindex': selected ? '0' : '-1',
          'aria-controls': 'explorer-panel',
        },
        events: {
          'click': (_) => setState(() => gitView = label == 'Git changes'),
        },
      );

  @override
  Component build(BuildContext context) {
    final entries = (directory?['entries'] as List? ?? [])
        .where(
          (e) =>
              (showHidden || !'${e['name']}'.startsWith('.')) &&
              '${e['name']}'.toLowerCase().contains(filter.toLowerCase()),
        )
        .toList();
    // The listing carries hidden entries even when they are filtered out of the
    // table, so a repository root is recognisable without a second request.
    final repositoryRoot =
        path.isNotEmpty &&
        (directory?['entries'] as List? ?? []).any(
          (e) => e is Map && '${e['name']}' == '.git',
        );
    final segments = path
        .split('/')
        .where((p) => p.isNotEmpty && p != '.')
        .toList();
    return opsPage([
      pageHeader(
        'Project explorer',
        subtitle:
            'Browse repositories and worktrees in the container. File previews are read-only.',
        actions: [
          button(
            'Refresh folder',
            () => open(path),
            disabled: busy,
            iconName: 'refresh-cw',
          ),
        ],
      ),
      el(
        'div',
        [
          viewTab('Files', 'folder', !gitView, 'explorer-tab-files'),
          viewTab('Git changes', 'git-branch', gitView, 'explorer-tab-git'),
        ],
        classes: 'tabs explorer-tabs',
        attributes: {'role': 'tablist', 'aria-label': 'Explorer view'},
        events: {'keydown': tabListKeyDown},
      ),
      el('div', [
        el(
          'ol',
          [
            el('li', [
              button(
                'Projects',
                () => open(''),
                disabled: busy,
                variant: 'ghost',
                small: true,
                iconName: 'folder-open',
              ),
            ]),
            for (var i = 0; i < segments.length; i++)
              el('li', [
                button(
                  segments[i],
                  () => open(segments.take(i + 1).join('/')),
                  disabled: busy,
                  variant: 'ghost',
                  small: true,
                ),
              ]),
          ],
          classes: 'explorer-crumbs',
          attributes: {'role': 'list', 'aria-label': 'Folder path'},
        ),
        if (directory != null)
          el('span', [
            text('${directory!['root']}${path.isEmpty ? '' : '/$path'}'),
          ], classes: 'mono-chip explorer-root'),
      ], classes: 'explorer-location'),
      feedback(),
      // A repository root, not every folder: the declaration is per project, so
      // offering it while browsing a subdirectory would claim the wrong scope.
      if (repositoryRoot)
        ProjectAnalysisCard(key: ValueKey('analysis-$path'), path: path),
      el(
        'div',
        [
          if (gitView)
            ProjectGitPanel(
              key: ValueKey(path),
              path: path,
              onOpen: (selected) {
                setState(() => gitView = false);
                open(selected);
              },
            )
          else
            el(
              'div',
              [
                card(
                  'Files',
                  [
                    el('div', [
                      el('div', [
                        icon('filter', classes: 'icon--sm search-field__icon'),
                        field(
                          'Filter this folder',
                          filter,
                          (v) => setState(() => filter = v),
                          placeholder: 'Filter by name',
                        ),
                      ], classes: 'search-field explorer-filter'),
                      switchField(
                        'Hidden files',
                        showHidden,
                        (v) => setState(() => showHidden = v),
                      ),
                    ], classes: 'explorer-toolbar'),
                    if (directory?['limited'] == true)
                      callout([
                        calloutText(
                          'Listing limited to 1,000 items. Open a subfolder to explore further.',
                        ),
                      ]),
                    if (directory == null && busy)
                      meta('Loading folder…', iconName: 'hourglass')
                    else if (directory == null && error != null)
                      meta(
                        'Folder contents are unavailable. Refresh to try again.',
                      )
                    else if (entries.isEmpty)
                      emptyState(
                        filter.isNotEmpty
                            ? 'No entries match your filter.'
                            : (directory?['entries'] as List? ?? []).isNotEmpty
                            ? 'Only hidden entries are present. Enable Hidden files.'
                            : 'This folder is empty.',
                        iconName: 'folder',
                      )
                    else
                      el('div', [
                        table(
                          ['Name', 'Kind', 'Modified', 'Size'],
                          [
                            for (final entry in entries)
                              [
                                el('div', [
                                  button(
                                    '${entry['name']}',
                                    () => open(entry['path'] as String),
                                    disabled:
                                        busy ||
                                        ![
                                          'folder',
                                          'file',
                                        ].contains(entry['kind']),
                                    variant: 'ghost',
                                    small: true,
                                    iconName: entry['kind'] == 'folder'
                                        ? 'folder'
                                        : 'file-text',
                                    classes: 'file-link',
                                  ),
                                  el('span', [
                                    text(
                                      '${entry['kind']} · ${displayTime(entry['modified'])}',
                                    ),
                                  ], classes: 'file-details'),
                                ], classes: 'file-identity'),
                                text(entry['kind']),
                                mono(displayTime(entry['modified'])),
                                mono(size(entry['size'])),
                              ],
                          ],
                        ),
                      ], classes: 'file-table'),
                  ],
                  iconName: 'folder-open',
                  count: directory == null ? null : entries.length,
                  classes: 'explorer-files',
                ),
                if (preview != null)
                  card(
                    '${preview!['path']}',
                    [
                      if (preview!['binary'] == true)
                        emptyState(
                          'Preview unavailable for binary files.',
                          iconName: 'file-text',
                        )
                      else ...[
                        if (preview!['truncated'] == true)
                          callout([calloutText('Showing the first 64 KB.')]),
                        codeText(
                          preview!['content'],
                          label: 'File preview for ${preview!['path']}',
                        ),
                      ],
                    ],
                    iconName: 'file-text',
                    subtitle: size(preview!['size']),
                    actions: [
                      button(
                        'Copy file path',
                        copyPath,
                        small: true,
                        iconName: 'copy',
                      ),
                      button(
                        'Close preview',
                        feature.closePreview,
                        small: true,
                        variant: 'ghost',
                        iconName: 'x',
                      ),
                    ],
                    classes: 'explorer-preview',
                  ),
              ],
              classes: preview == null
                  ? 'explorer-panes'
                  : 'explorer-panes explorer-panes--split',
            ),
        ],
        classes: 'explorer-panel',
        attributes: {
          'id': 'explorer-panel',
          'role': 'tabpanel',
          'tabindex': '0',
          'aria-busy': '$busy',
          'aria-labelledby': gitView
              ? 'explorer-tab-git'
              : 'explorer-tab-files',
        },
      ),
    ], classes: 'explorer-page');
  }
}
