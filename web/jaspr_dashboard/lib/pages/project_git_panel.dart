import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'operations_support.dart';
import 'integration_board.dart';
import 'ops_ui.dart';

class ProjectGitPanel extends StatefulComponent {
  const ProjectGitPanel({super.key, required this.path, required this.onOpen});
  final String path;
  final void Function(String) onOpen;
  @override
  State<ProjectGitPanel> createState() => _GitState();
}

class _GitState extends OperationsState<ProjectGitPanel> {
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
  String? get message => feature.state.message;
  @override
  void initState() {
    super.initState();
    feature = GitBloc(connection, component.path);
    featureSubscription = feature.stream.listen((_) {
      if (!mounted) return;
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
    if (initial is! Map || updatingBase || loading || !admin) return;
    final epoch = connection.generation;
    var base = '${initial['base']}', branch = '${initial['branch']}';
    final selected = await showDialog<bool>(
      'Update base branch',
      (close) => stack([
        field('Expected destination branch', branch, (v) => branch = v),
        field('Remote-tracking comparison ref', base, (v) => base = v),
        paragraph(
          'Only the shared checkout will advance. Local changes, divergent history and active work prevent the update. Agent worktrees keep their own branches.',
        ),
        actions([
          button('Cancel', () => close(false)),
          button('Review exact target', () => close(true), primary: true),
        ]),
      ]),
    );
    if (selected != true || !valid(epoch)) return;
    base = base.trim();
    branch = branch.trim();
    setState(() {
      reviewingBase = true;
    });
    try {
      final plan = await feature.inspectBase(
        '${initial['path']}',
        base,
        branch,
      );
      if (plan == null || !(valid(epoch))) return;
      if (!await confirm(
            'Advance $branch?',
            'Checkout: ${plan['path']}\nCurrent: ${plan['head']}\nTarget: ${plan['target']}\nComparison: $base\n\nThe old HEAD is pinned for recovery and the request audited. This is a fast-forward only.',
            confirmLabel: 'Update base branch',
          ) ||
          !valid(epoch))
        return;
      await feature.updateBase(Map<String, dynamic>.from(plan), requestId());
    } catch (e) {
      if (valid(epoch)) feature.reportError('git', e);
    } finally {
      if (valid(epoch)) setState(() => reviewingBase = false);
    }
  }

  Future<void> diff(Map tree, Map file) async {
    final epoch = connection.generation;
    try {
      final result = await feature.diff(tree, file);
      if (result == null) return;
      if (!valid(epoch)) return;
      await showDialog<void>(
        '${file['path']}',
        (close) => stack([
          codeText(
            '${result['truncated'] == true ? 'Showing the first 64 KB.\n\n' : ''}${(result['diff'] as String).isEmpty ? 'No tracked diff. New files can be opened in Files.' : result['diff']}',
            label: 'Git diff for ${file['path']}',
          ),
          actions([
            if (file['kind'] != 'deleted')
              button('Open file', () {
                close(null);
                component.onOpen('${tree['path']}/${file['path']}');
              }),
            button('Close', () => close(null)),
          ]),
        ]),
      );
    } catch (e) {
      if (valid(epoch)) feature.reportError('git', e);
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
    conflicts: tree['conflicts'] as int? ?? 0,
    dirty:
        (tree['counts'] as Map?)?.values.fold<int>(
          0,
          (a, b) => a + (b as int),
        ) !=
        0,
  );
  Future<void> notify(Map tree, Map agent) async =>
      feature.notify(noticeFor(tree, agent));

  Component treeCard(Map tree) {
    if (tree['error'] != null)
      return card(
        '${tree['path']}',
        [
          callout([calloutText(tree['error'])], tone: 'danger'),
        ],
        iconName: 'git-branch',
        classes: 'git-tree',
      );
    final behind = tree['base_behind'] as int? ?? 0;
    final conflicts = tree['conflicts'] as int? ?? 0;
    final changes = tree['changes'] as List;
    final coordinator = tree['checkout_role'] == 'coordinator';
    return card(
      '${tree['branch'] == 'HEAD' ? 'Detached checkout' : tree['branch']}',
      [
        el('div', [
          for (final label in ['added', 'deleted', 'modified'])
            pill(
              '${tree['counts'][label]} $label',
              tone: (tree['counts'][label] as num? ?? 0) > 0
                  ? 'info'
                  : 'neutral',
              shape: (tree['counts'][label] as num? ?? 0) > 0
                  ? 'filled'
                  : 'hollow',
            ),
        ], classes: 'git-counts'),
        facts([
          ('Checkout', mono(tree['path'])),
          ('Commit', mono(tree['commit'])),
          (
            'Upstream',
            text(
              tree['upstream'] == null
                  ? 'No upstream tracking branch'
                  : '${tree['upstream']} · ${tree['ahead']} ahead · ${tree['behind']} behind',
            ),
          ),
          for (final agent in connection.state.agents.where(
            (a) => a['cwd'] == tree['cwd'] && tree['cwd'] != null,
          ))
            (
              'Agent',
              text(
                '${agent['display_name'] ?? agent['name']} · ${agent['agent_status'] ?? 'unknown'}',
              ),
            ),
        ]),
        if (behind > 0)
          callout(
            [
              calloutText(
                coordinator
                    ? '${tree['base_behind']} commits behind ${tree['base']}. Coordinator checkout — reporting is independent of its age.'
                    : tree['checkout_role'] == 'shared'
                    ? 'Main behind · ${tree['base_behind']} commits. Update base branch when ready.'
                    : '${tree['base_behind']} commits behind ${tree['base']} — worker needs to incorporate changes when ready.',
              ),
            ],
            tone: coordinator ? 'neutral' : 'warning',
            iconName: 'git-pull-request',
            actions: [
              if (!coordinator)
                button(
                  'Copy merge instructions',
                  () async {
                    final result = await copyText(
                      noticeFor(tree).instructions,
                      filename: 'merge-instructions.md',
                    );
                    if (mounted) setState(() => message = result);
                  },
                  small: true,
                  iconName: 'copy',
                ),
              if (!coordinator)
                for (final agent in connection.state.agents.where(
                  (a) => a['cwd'] == tree['cwd'] && a['profile_id'] != null,
                ))
                  button(
                    'Ask ${agent['display_name'] ?? agent['name']} to integrate main',
                    () => notify(tree, agent),
                    disabled:
                        notifying == agent['profile_id'] ||
                        !agentCanIntegrate(
                          connection,
                          '${agent['profile_id']}',
                        ),
                    small: true,
                    iconName: 'send',
                  ),
            ],
          ),
        if (conflicts > 0)
          callout([
            calloutText('${tree['conflicts']} unresolved conflicts'),
          ], tone: 'danger'),
        if (changes.isEmpty)
          meta(
            'Working tree clean',
            iconName: 'circle-check',
            classes: 'git-clean',
          )
        else
          el('details', [
            el('summary', [
              icon('file-text', classes: 'icon--sm'),
              text('${changes.length} changed files'),
            ]),
            el(
              'ul',
              [
                for (final file in changes)
                  el('li', [
                    button(
                      '${file['status']} · ${file['path']}',
                      () => diff(tree, file),
                      variant: 'ghost',
                      small: true,
                      classes: 'git-file',
                    ),
                    if (file['kind'] != null)
                      pill(
                        '${file['kind']}',
                        tone: file['kind'] == 'deleted' ? 'danger' : 'neutral',
                        shape: file['kind'] == 'deleted' ? 'diamond' : 'hollow',
                      ),
                  ]),
              ],
              classes: 'git-files',
              attributes: {'role': 'list'},
            ),
          ], classes: 'git-changes'),
      ],
      iconName: tree['branch'] == 'HEAD'
          ? 'git-commit-horizontal'
          : 'git-branch',
      actions: [
        button(
          'Browse checkout files',
          () => component.onOpen(tree['path'] as String),
          small: true,
          variant: 'ghost',
          iconName: 'folder-open',
        ),
        if (tree['path'] == data?['base_update']?['path'])
          button(
            updatingBase ? 'Updating base branch…' : 'Update base branch',
            updateBase,
            disabled: updatingBase || loading || !admin,
            small: true,
            iconName: 'git-merge',
          ),
      ],
      classes: 'git-tree',
    );
  }

  @override
  Component build(BuildContext context) => el('div', [
    card(
      'Repositories & worktrees',
      [
        meta(
          data?['last_fetch'] == null
              ? 'Remote has not been checked here.'
              : 'Last fetch: ${displayTime(data!['last_fetch'])}',
          iconName: 'clock',
        ),
        if (loading && showProgress)
          meta('Loading Git status…', iconName: 'hourglass'),
        feedback(),
        if (data?['fetch_error'] != null)
          callout([
            calloutText('Fetch failed: ${data!['fetch_error']}'),
          ], tone: 'danger'),
        if (data?['repository'] == false)
          emptyState(
            'Open a Git repository folder to see its worktrees and changes.',
            iconName: 'git-branch',
          ),
      ],
      iconName: 'git-branch',
      subtitle:
          'Local changes refresh every 10 seconds. Tracking counts use the last fetched remote state.',
      actions: [
        button(
          'Refresh Git status',
          load,
          disabled: loading,
          small: true,
          iconName: 'refresh-cw',
        ),
        button(
          'Fetch remote updates',
          () => load(fetch: true),
          disabled: loading || data?['repository'] != true,
          small: true,
          iconName: 'download',
        ),
      ],
      classes: 'git-overview',
    ),
    if (data?['repository_path'] != null)
      IntegrationBoard(
        key: ValueKey(data!['repository_path']),
        repository: data!['repository_path'] as String,
      ),
    if ((data?['worktrees'] as List? ?? []).isNotEmpty)
      el('div', [
        for (final tree in data?['worktrees'] as List? ?? []) treeCard(tree),
      ], classes: 'git-trees'),
    if ((data?['base_updates'] as List? ?? []).isNotEmpty)
      el('details', [
        el('summary', [icon('history'), text('Base update audit & recovery')]),
        meta(
          'Recover into a separate checkout; preserve the current branch.',
          iconName: 'info',
        ),
        table(
          ['Branch', 'State', 'Actor', 'Time', 'Change', 'Recovery', 'Note'],
          [
            for (final entry in data!['base_updates'])
              [
                text(entry['selection']?['branch']),
                pill(
                  '${entry['state']}',
                  tone: stateTone(entry['state']),
                  shape: shapeFor(stateTone(entry['state'])),
                ),
                text(entry['actor']),
                mono(entry['at']),
                mono('${entry['old_head']} → ${entry['target']}'),
                mono(entry['recovery_ref'] ?? 'No branch change recorded'),
                text(entry['reason'] ?? entry['note'] ?? ''),
              ],
          ],
        ),
      ], classes: 'card git-audit'),
    if (data?['limited'] == true)
      meta('Showing the first 20 checkouts.', iconName: 'info'),
  ], classes: 'stack git-panel');
}
