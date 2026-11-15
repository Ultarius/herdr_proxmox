import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'feature_view.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'overview_panels.dart';
import 'recent_activity.dart';
import 'storage_panel.dart';

class ResourcePanel extends StatefulWidget {
  const ResourcePanel({super.key});
  @override
  State<ResourcePanel> createState() => _ResourcePanelState();
}

class _ResourcePanelState extends State<ResourcePanel>
    with FeatureView<ResourcePanel> {
  late final ResourcesBloc bloc;
  Map<String, dynamic>? get data => bloc.data;
  bool get loading => bloc.state.loading;
  String? get error => featureError == null
      ? null
      : 'Resource monitor unavailable; displayed values may be stale.';
  @override
  void initState() {
    super.initState();
    bindFeature(bloc = ResourcesBloc(BlocScope.get<DashboardBloc>()));
    refresh();
  }

  Future<void> refresh() => bloc.refresh();

  String bytes(dynamic value) =>
      '${((value as num? ?? 0) / 1048576).toStringAsFixed(0)} MiB';
  String percent(dynamic value) =>
      value == null ? 'Sampling…' : '${(value as num).toStringAsFixed(1)}%';
  double ratio(dynamic used, dynamic total) => (total as num? ?? 0) > 0
      ? ((used as num? ?? 0) / (total as num)).toDouble()
      : 0;
  Widget meter(IconData icon, String text, num value) => Container(
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.surfaceContainerLow,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: 12),
        Text(text, style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 14),
        LinearProgressIndicator(
          value: value.toDouble().clamp(0, 1),
          borderRadius: BorderRadius.circular(10),
          minHeight: 7,
        ),
      ],
    ),
  );
  @override
  Widget build(BuildContext context) {
    final d = data;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            PanelHeading(
              'System health',
              Icons.monitor_heart_outlined,
              action: IconButton(
                onPressed: loading ? null : refresh,
                tooltip: 'Refresh resources',
                icon: const Icon(Icons.refresh),
              ),
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
              LayoutBuilder(
                builder: (context, constraints) {
                  final width = constraints.maxWidth < 700
                      ? constraints.maxWidth
                      : (constraints.maxWidth - 16) / 2;
                  return Wrap(
                    spacing: 16,
                    runSpacing: 16,
                    children: [
                      SizedBox(
                        width: width,
                        child: Column(
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: meter(
                                    Icons.memory,
                                    'CPU ${percent(d['cpu_percent'])} · ${d['cpu_cores']} cores',
                                    (d['cpu_percent'] as num? ?? 0) / 100,
                                  ),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: meter(
                                    Icons.storage_outlined,
                                    'Memory ${bytes(d['memory_used'])} / ${bytes(d['memory_total'])}',
                                    ratio(d['memory_used'], d['memory_total']),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 12),
                            Text(
                              'Swap ${bytes(d['swap_used'])} / ${bytes(d['swap_total'])}',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                            if (d['storage'] case final Map disk)
                              if (disk['available'] == true) ...[
                                const SizedBox(height: 12),
                                meter(
                                  Icons.disc_full_outlined,
                                  'Disk ${bytes(disk['free_bytes'])} available',
                                  ratio(
                                    disk['used_bytes'],
                                    disk['total_bytes'],
                                  ),
                                ),
                              ],
                          ],
                        ),
                      ),
                      SizedBox(width: width, child: const RecentActivity()),
                    ],
                  );
                },
              ),
              if (d['build_capacity'] case final Map capacity) ...[
                const SizedBox(height: 8),
                Text(
                  'Heavy builds: ${capacity['active'] == null ? 'idle' : 'running'}'
                  ' · ${capacity['waiting'] ?? 0} waiting'
                  ' · up to ${capacity['compiler_jobs'] ?? 1} compiler jobs per build',
                ),
                if ('${capacity['waiting_reason'] ?? ''}'.isNotEmpty)
                  Text('${capacity['waiting_reason']} New builds will wait.'),
              ],
              if (d['storage'] case final Map disk)
                if (disk['blocked'] == true)
                  Text(
                    'Storage needs attention: ${disk['reason']}',
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
              if ((d['memory_total'] as num) > 0 &&
                  (d['memory_used'] as num) / (d['memory_total'] as num) >= .9)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'High memory usage — avoid launching more agents.',
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
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
            const StoragePanel(key: ValueKey('managed-storage')),
          ],
        ),
      ),
    );
  }
}
