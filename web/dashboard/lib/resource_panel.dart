import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

class ResourcePanel extends StatefulWidget {
  const ResourcePanel({super.key});
  @override
  State<ResourcePanel> createState() => _ResourcePanelState();
}

class _ResourcePanelState extends State<ResourcePanel> {
  Timer? timer;
  Map<String, dynamic>? data;
  bool loading = false;
  String? error;
  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 10), (_) => refresh());
  }

  Future<void> refresh() async {
    if (loading) return;
    setState(() => loading = true);
    try {
      final result = await BlocScope.get<DashboardBloc>().request('resources');
      if (mounted)
        setState(() {
          data = Map<String, dynamic>.from(result);
          error = null;
        });
      if (mounted && !(timer?.isActive ?? false)) {
        timer = Timer.periodic(const Duration(seconds: 10), (_) => refresh());
      }
    } catch (_) {
      timer?.cancel();
      if (mounted)
        setState(() {
          error =
              'Resource monitor unavailable; displayed values may be stale.';
        });
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  String bytes(dynamic value) =>
      '${((value as num? ?? 0) / 1048576).toStringAsFixed(0)} MiB';
  String percent(dynamic value) =>
      value == null ? 'Sampling…' : '${(value as num).toStringAsFixed(1)}%';
  @override
  Widget build(BuildContext context) {
    final d = data;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Container resources',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            TextButton.icon(
              onPressed: loading ? null : refresh,
              icon: const Icon(Icons.refresh),
              label: const Text('Refresh resources'),
            ),
            const SizedBox(height: 8),
            if (error != null) Text(error!),
            if (d == null && error == null)
              const Text('Loading resource usage…')
            else if (d != null && d['available'] != true)
              const Text('Container metrics unavailable.')
            else if (d != null) ...[
              Text(
                'Sampled: ${DateTime.fromMillisecondsSinceEpoch(((d['sampled_at'] as num) * 1000).round()).toLocal()}',
              ),
              Wrap(
                spacing: 20,
                runSpacing: 8,
                children: [
                  Text(
                    'CPU ${percent(d['cpu_percent'])} · ${d['cpu_cores']} cores',
                  ),
                  Text(
                    'Memory ${bytes(d['memory_used'])} / ${bytes(d['memory_total'])}',
                  ),
                  Text(
                    'Swap ${bytes(d['swap_used'])} / ${bytes(d['swap_total'])}',
                  ),
                ],
              ),
              if ((d['memory_total'] as num) > 0 &&
                  (d['memory_used'] as num) / (d['memory_total'] as num) >= .9)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text(
                    'High memory usage — avoid launching more agents.',
                    style: TextStyle(color: Colors.orange),
                  ),
                ),
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('Processes by memory usage'),
                subtitle: const Text(
                  'Top 20 · process CPU uses 100% per core · RSS may include shared memory',
                ),
                children: [
                  for (final p in (d['processes'] as List? ?? []))
                    ListTile(
                      dense: true,
                      title: Text('${p['name']} · PID ${p['pid']}'),
                      trailing: Text(
                        '${bytes(p['memory_bytes'])} · CPU ${percent(p['cpu_percent'])}',
                      ),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}
