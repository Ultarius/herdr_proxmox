import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

class UpdateCard extends StatefulWidget {
  const UpdateCard({super.key});
  @override
  State<UpdateCard> createState() => _UpdateCardState();
}

class _UpdateCardState extends State<UpdateCard> {
  Map<String, dynamic>? info;
  String? error;
  bool busy = false;
  Timer? timer;
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();

  @override
  void initState() {
    super.initState();
    check();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> check({bool force = false}) async {
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() => busy = true);
    try {
      final result = await connection.request(
        force ? 'updates/check' : 'updates',
        force ? <String, dynamic>{} : null,
      );
      if (mounted && epoch == connection.generation) {
        setState(() {
          info = result;
          error = null;
        });
        if (!['queued', 'running'].contains(result['state'])) {
          timer?.cancel();
          timer = null;
        }
      }
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> install() async {
    final version = info?['latest'];
    final epoch = connection.generation;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Install $version?'),
        content: const Text(
          'The dashboard will briefly disconnect. Your projects, agent profiles and credentials are preserved. A backup is kept for recovery. Reload this page after the update completes.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Install update'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || epoch != connection.generation) return;
    setState(() => busy = true);
    try {
      await connection.request('updates/install', {'version': version});
      if (mounted) {
        setState(() => info = {...?info, 'state': 'queued'});
        timer ??= Timer.periodic(const Duration(seconds: 5), (_) => check());
      }
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Dashboard updates',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const Text(
            'Stable releases from Ultarius/herdr_proxmox. Updates are installed when you choose.',
          ),
          if (info != null) ...[
            Text(
              'Installed: ${info!['installed']} · Latest: ${info!['latest']}',
            ),
            Text('Status: ${info!['state']}'),
            if (info!['state'] == 'complete')
              const Text(
                'Update complete. Reload this page to use the new dashboard.',
              ),
            if (info!['error'] != null) Text('${info!['error']}'),
            if (info!['supported'] != true)
              const Text(
                'Install the updater service in this container before using dashboard updates.',
              ),
            if ((info!['notes'] as String? ?? '').isNotEmpty)
              Text(info!['notes'] as String),
          ],
          if (error != null) Text(error!),
          Wrap(
            spacing: 12,
            children: [
              TextButton(
                onPressed: busy ? null : () => check(force: true),
                child: const Text('Check for updates'),
              ),
              FilledButton(
                onPressed:
                    !busy &&
                        info?['available'] == true &&
                        info?['supported'] == true &&
                        !['queued', 'running'].contains(info?['state'])
                    ? install
                    : null,
                child: const Text('Install update'),
              ),
            ],
          ),
        ],
      ),
    ),
  );
}
