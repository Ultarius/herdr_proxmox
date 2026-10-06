import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'overview_panels.dart';

/// Only reports changes observed while this page is open; not a durable audit.
class RecentActivity extends StatefulWidget {
  const RecentActivity({super.key});
  @override
  State<RecentActivity> createState() => _RecentActivityState();
}

class _RecentActivityState extends State<RecentActivity> {
  StreamSubscription? subscription;
  Map<String, String>? previous;
  final List<({String text, DateTime time})> events = [];
  @override
  void initState() {
    super.initState();
    final bloc = BlocScope.get<DashboardBloc>();
    observe(bloc.state);
    subscription = bloc.stream.listen((_) => observe(bloc.state));
  }

  void observe(DashboardState state) {
    if (state.refreshed == null) return;
    final current = {
      for (final a in state.agents)
        '${a['name'] ?? a['pane_id']}': '${agentName(a)} · ${agentStatus(a)}',
    };
    if (previous != null) {
      for (final entry in current.entries) {
        if (previous![entry.key] != entry.value) {
          events.insert(0, (text: entry.value, time: state.refreshed!));
        }
      }
      for (final key in previous!.keys.where(
        (key) => !current.containsKey(key),
      )) {
        events.insert(0, (
          text: '${previous![key]!.split(' · ').first} · no longer detected',
          time: state.refreshed!,
        ));
      }
      if (events.length > 8) events.removeRange(8, events.length);
    }
    previous = current;
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    subscription?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      const PanelHeading('Recent activity', Icons.history),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18),
        child: Text(
          'Changes observed on this page',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ),
      if (events.isEmpty)
        const Padding(
          padding: EdgeInsets.all(18),
          child: Text('No activity changes observed yet.'),
        ),
      for (final event in events.take(3))
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 6),
          child: Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerLow,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                Icon(
                  Icons.circle,
                  size: 8,
                  color: Theme.of(context).colorScheme.primary,
                ),
                const SizedBox(width: 10),
                Expanded(child: Text(event.text)),
                const SizedBox(width: 8),
                Text(
                  event.time.toLocal().toString().substring(11, 19),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
      const SizedBox(height: 12),
    ],
  );
}
