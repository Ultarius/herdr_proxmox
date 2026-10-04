import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'log_window.dart';
import 'routes.dart';

String logSize(int bytes) =>
    '${(bytes / 1000000).toStringAsFixed(bytes < 10000 ? 6 : 3)} MB';

class LogsPage extends StatefulWidget {
  const LogsPage({super.key, required this.coordinator});
  final AppCoordinator coordinator;
  @override
  State<LogsPage> createState() => _LogsPageState();
}

class _LogsPageState extends State<LogsPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  final pane = TextEditingController();
  final label = TextEditingController();
  List<Map<String, dynamic>> logs = [];
  String source = 'visible';
  String? preview;
  String? error;
  bool busy = false;
  int total = 0;

  @override
  void initState() {
    super.initState();
    refresh();
  }

  @override
  void dispose() {
    pane.dispose();
    label.dispose();
    super.dispose();
  }

  Future<void> refresh() async {
    if (!connection.state.connected) return;
    final epoch = connection.generation;
    try {
      final data = await connection.request('logs');
      if (mounted && epoch == connection.generation) {
        setState(() {
          logs = List<Map<String, dynamic>>.from(data['logs']);
          total = data['total_bytes'] as int;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> action(String name, Map<String, dynamic> body) async {
    setState(() {
      busy = true;
      error = null;
    });
    final epoch = connection.generation;
    try {
      final data = await connection.request('logs/$name', body);
      if (!mounted || epoch != connection.generation) return;
      if (name == 'preview') {
        setState(
          () => preview =
              '${data['truncated'] == true ? '[Last 8 KB of saved text]\n' : ''}${data['text']}',
        );
      } else {
        setState(() => preview = null);
        await refresh();
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> open(Map<String, dynamic> log, String mode) async {
    LogWindow? window;
    try {
      window =
          LogWindow(); // Reserve the tab while the click still has user activation.
      final data = await connection.request('logs/ticket', {
        'id': log['id'],
        'mode': mode,
      });
      if (!mounted || !connection.state.connected) {
        window.close();
        return;
      }
      window.open(
        Uri.base
            .resolve('/logs/view#${data['mode']}:${data['ticket']}')
            .toString(),
      );
    } catch (e) {
      window?.close();
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> delete(Map<String, dynamic> log) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete saved snapshot?'),
        content: Text('${log['label']} (${logSize(log['size_bytes'] as int)})'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted) await action('delete', {'id': log['id']});
  }

  @override
  Widget build(BuildContext context) => JuiceBuilder<DashboardBloc>(
    builder: (context, bloc, status) => Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1200),
          child: ListView(
            padding: const EdgeInsets.all(28),
            children: [
              Text(
                'Saved terminal logs',
                style: Theme.of(context).textTheme.headlineLarge,
              ),
              Wrap(
                spacing: 12,
                children: [
                  TextButton(
                    onPressed: () =>
                        widget.coordinator.navigate(DashboardRoute()),
                    child: const Text('Workspaces'),
                  ),
                  TextButton(
                    onPressed: () =>
                        widget.coordinator.navigate(OrganizationRoute()),
                    child: const Text('Organization'),
                  ),
                  OutlinedButton(
                    onPressed: busy || !bloc.state.connected ? null : refresh,
                    child: const Text('Refresh logs'),
                  ),
                ],
              ),
              if (!bloc.state.connected)
                const Text('Connect on the dashboard first.')
              else ...[
                const Text(
                  'Save retained terminal text before closing a pane. These are snapshots, not guaranteed full run transcripts. Setup/login terminals are never archived here.',
                ),
                const SizedBox(height: 12),
                Text(
                  'Stored: ${logSize(total)} / 100 MB. 1 MB = 1,000,000 bytes. Download limit: 10 MB.',
                ),
                TextField(
                  controller: pane,
                  decoration: const InputDecoration(
                    labelText: 'Pane ID, e.g. w1:p1',
                  ),
                ),
                if (bloc.state.agents.isNotEmpty)
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final agent in bloc.state.agents)
                        ActionChip(
                          label: Text('${agent['name'] ?? agent['pane_id']}'),
                          onPressed: () {
                            pane.text = '${agent['pane_id']}';
                            label.text = '${agent['name'] ?? agent['pane_id']}';
                          },
                        ),
                    ],
                  ),
                TextField(
                  controller: label,
                  decoration: const InputDecoration(
                    labelText: 'Snapshot label (optional)',
                  ),
                ),
                DropdownButtonFormField<String>(
                  isExpanded: true,
                  initialValue: source,
                  items: const [
                    DropdownMenuItem(
                      value: 'visible',
                      child: Text('Visible screen — passive read'),
                    ),
                    DropdownMenuItem(
                      value: 'recent-unwrapped',
                      child: Text('Recent history (idle agent)'),
                    ),
                  ],
                  onChanged: busy
                      ? null
                      : (value) => setState(() => source = value!),
                ),
                const Text(
                  'Recent history exports up to 2,000 terminal rows. Older output may no longer be available.',
                ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton(
                    onPressed: busy
                        ? null
                        : () => action('save', {
                            'pane': pane.text.trim(),
                            'label': label.text.trim().isEmpty
                                ? pane.text.trim()
                                : label.text.trim(),
                            'source': source,
                          }),
                    child: const Text('Save text snapshot'),
                  ),
                ),
                const SizedBox(height: 20),
                for (final log in logs)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${log['label']} — ${logSize(log['size_bytes'] as int)}',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          Text(
                            '${log['size_bytes']} bytes · ${log['pane']} · ${log['source']} · ${log['created_at']}',
                          ),
                          Wrap(
                            spacing: 8,
                            children: [
                              TextButton(
                                onPressed: busy
                                    ? null
                                    : () =>
                                          action('preview', {'id': log['id']}),
                                child: const Text('Preview last 8 KB'),
                              ),
                              TextButton(
                                onPressed: () => open(log, 'view'),
                                child: const Text('Open text page'),
                              ),
                              TextButton(
                                onPressed: log['download_allowed'] == true
                                    ? () => open(log, 'download')
                                    : null,
                                child: const Text('Download .txt'),
                              ),
                              TextButton(
                                onPressed: busy ? null : () => delete(log),
                                child: const Text('Delete snapshot'),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                if (preview != null)
                  SelectableText(
                    preview!,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 12,
                    ),
                  ),
              ],
              if (busy) const LinearProgressIndicator(),
              if (error != null)
                Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
            ],
          ),
        ),
      ),
    ),
  );
}
