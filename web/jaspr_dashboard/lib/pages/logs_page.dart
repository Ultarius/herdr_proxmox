import 'package:web/web.dart' as web;
import 'operations_support.dart';
import 'ops_ui.dart';

String logSize(int bytes) =>
    '${(bytes / 1000000).toStringAsFixed(bytes < 10000 ? 6 : 3)} MB';

class LogsPage extends StatefulComponent {
  const LogsPage({super.key});
  @override
  State<LogsPage> createState() => _LogsState();
}

class _LogsState extends OperationsState<LogsPage> {
  String pane = '', label = '', source = 'visible';
  List<Map<String, dynamic>> logs = [];
  String? preview;
  int total = 0;
  bool refreshing = false, loaded = false;
  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    final epoch = connection.generation;
    if (!connection.state.connected || refreshing) return;
    setState(() {
      refreshing = true;
      error = null;
    });
    try {
      final result = await connection.request('logs');
      if (valid(epoch))
        setState(() {
          logs = List<Map<String, dynamic>>.from(result['logs']);
          total = result['total_bytes'] as int;
          loaded = true;
        });
    } catch (e) {
      if (valid(epoch)) setState(() => error = '$e');
    } finally {
      if (valid(epoch)) setState(() => refreshing = false);
    }
  }

  Future<void> action(String name, Map<String, dynamic> body) => perform((
    epoch,
  ) async {
    final result = await connection.request('logs/$name', body);
    if (!valid(epoch)) return;
    if (name == 'preview')
      setState(
        () => preview =
            '${result['truncated'] == true ? '[Last 8 KB of saved text]\n' : ''}${result['text']}',
      );
    else {
      setState(() => preview = null);
      await refresh();
    }
  });
  Future<void> open(Map<String, dynamic> log, String mode) async {
    // Reserve synchronously while the browser still grants user activation.
    final window = web.window.open('about:blank', '_blank');
    final epoch = connection.generation;
    try {
      if (window == null)
        throw Exception('Allow pop-ups to open the text log.');
      window.opener = null;
      window.document.body?.textContent = 'Opening saved terminal text…';
      final data = await connection.request('logs/ticket', {
        'id': log['id'],
        'mode': mode,
      });
      if (!valid(epoch)) {
        window.close();
        return;
      }
      window.location.replace(
        Uri.base
            .resolve('/logs/view#${data['mode']}:${data['ticket']}')
            .toString(),
      );
    } catch (e) {
      window?.close();
      if (valid(epoch)) setState(() => error = '$e');
    }
  }

  Future<void> delete(Map<String, dynamic> log) async {
    final epoch = connection.generation;
    if (await confirm(
          'Delete saved snapshot?',
          '${log['label']} (${logSize(log['size_bytes'] as int)})',
          confirmLabel: 'Delete',
        ) &&
        valid(epoch))
      await action('delete', {'id': log['id']});
  }

  String sourceLabel(Object? value) => switch (value) {
    'visible' => 'Visible screen',
    'recent-unwrapped' => 'Recent history',
    _ => '${value ?? ''}',
  };

  Component storage() {
    final percent = (total / 100000000 * 100).clamp(0, 100);
    return el('div', [
      meta(
        'Stored: ${logSize(total)} / 100 MB. 1 MB = 1,000,000 bytes. Download limit: 10 MB.',
        iconName: 'hard-drive',
      ),
      el(
        'div',
        [el('span', [], classes: 'meter__fill')],
        classes: 'meter',
        attributes: {
          'role': 'meter',
          'aria-label': 'Saved log storage',
          'aria-valuemin': '0',
          'aria-valuemax': '100',
          'aria-valuenow': percent.toStringAsFixed(2),
          'aria-valuetext': '${logSize(total)} of 100 MB',
          'style': '--meter-value: ${percent.toStringAsFixed(2)}%',
          if (percent >= 90)
            'data-level': 'danger'
          else if (percent >= 75)
            'data-level': 'warning',
        },
      ),
    ], classes: 'logs-storage');
  }

  @override
  Component build(BuildContext context) => opsPage([
    pageHeader(
      'Terminal logs',
      subtitle:
          'Save retained terminal text before closing a pane. These are snapshots, not guaranteed full run transcripts. Setup/login terminals are never archived here.',
      actions: [
        button(
          'Refresh logs',
          refresh,
          disabled: busy || refreshing,
          iconName: 'refresh-cw',
        ),
      ],
    ),
    el('div', [
      card(
        'Save a snapshot',
        [
          field('Pane ID, e.g. w1:p1', pane, (v) => setState(() => pane = v)),
          field('Snapshot label (optional)', label, (v) => label = v),
          select(
            'Source',
            source,
            {
              'visible': 'Visible screen — passive read',
              'recent-unwrapped': 'Recent history (idle agent)',
            },
            (v) {
              if (!busy) setState(() => source = v);
            },
          ),
          if (connection.state.agents.isNotEmpty)
            el(
              'div',
              [
                el('span', [text('Agents')], classes: 'toolbar__label'),
                for (final agent in connection.state.agents)
                  chip(
                    '${agent['name'] ?? agent['pane_id']}',
                    pane.isNotEmpty && pane == '${agent['pane_id']}',
                    () => setState(() {
                      pane = '${agent['pane_id']}';
                      label = '${agent['name'] ?? agent['pane_id']}';
                    }),
                  ),
              ],
              classes: 'chips logs-agents',
              attributes: {
                'role': 'group',
                'aria-label': 'Choose an agent pane',
              },
            ),
          el('div', [
            meta(
              'Recent history exports up to 2,000 terminal rows. Older output may no longer be available.',
              iconName: 'info',
            ),
            button(
              'Save text snapshot',
              () => action('save', {
                'pane': pane.trim(),
                'label': label.trim().isEmpty ? pane.trim() : label.trim(),
                'source': source,
              }),
              primary: true,
              disabled: busy || pane.trim().isEmpty,
              iconName: 'download',
            ),
          ], classes: 'logs-save__footer'),
          feedback(),
        ],
        iconName: 'terminal',
        actions: [
          busy
              ? pill('Saving', tone: 'info')
              : pane.trim().isEmpty
              ? pill('No pane selected', shape: 'dash')
              : pill('Pane ${pane.trim()}', tone: 'accent'),
        ],
        classes: 'logs-save',
      ),
      card(
        'Saved snapshots',
        [
          if (loaded) storage(),
          if (refreshing && !loaded)
            meta('Loading saved snapshots…', iconName: 'hourglass'),
          if (!refreshing && !loaded && error != null)
            meta('Saved snapshots are unavailable. Refresh logs to try again.'),
          if (loaded && logs.isEmpty)
            emptyState(
              'No saved snapshots. Save a pane’s text to keep it after the pane closes.',
              iconName: 'archive',
            )
          else if (logs.isNotEmpty)
            table(
              ['Snapshot', 'Pane', 'Source', 'Created', 'Size', 'Actions'],
              [
                for (final log in logs)
                  [
                    el('span', [
                      el('strong', [text(log['label'])]),
                      el('span', [
                        text('${log['size_bytes']} bytes'),
                      ], classes: 'mono'),
                    ], classes: 'logs-name'),
                    mono(log['pane']),
                    pill(
                      sourceLabel(log['source']),
                      shape: log['source'] == 'visible' ? 'hollow' : 'filled',
                    ),
                    mono(log['created_at']),
                    el('span', [
                      mono(logSize(log['size_bytes'] as int)),
                      if (log['download_allowed'] != true)
                        pill('Over download limit', tone: 'warning'),
                    ], classes: 'logs-size'),
                    actions([
                      button(
                        'Preview last 8 KB',
                        () => action('preview', {'id': log['id']}),
                        disabled: busy,
                        small: true,
                        iconName: 'scan',
                      ),
                      button(
                        'Open text page',
                        () => open(log, 'view'),
                        small: true,
                        iconName: 'external-link',
                      ),
                      button(
                        'Download .txt',
                        () => open(log, 'download'),
                        disabled: log['download_allowed'] != true,
                        small: true,
                        iconName: 'download',
                      ),
                      button(
                        'Delete snapshot',
                        () => delete(log),
                        disabled: busy,
                        small: true,
                        variant: 'danger',
                        iconName: 'trash-2',
                      ),
                    ]),
                  ],
              ],
            ),
        ],
        iconName: 'archive',
        count: loaded ? logs.length : null,
        classes: 'logs-saved',
      ),
      if (preview != null)
        card(
          'Snapshot preview',
          [codeText(preview, label: 'Saved snapshot preview')],
          iconName: 'scan',
          actions: [
            button(
              'Close preview',
              () => setState(() => preview = null),
              small: true,
              variant: 'ghost',
              iconName: 'x',
            ),
          ],
          classes: 'logs-preview',
        ),
    ], classes: 'logs-grid'),
  ], classes: 'logs-page');
}
