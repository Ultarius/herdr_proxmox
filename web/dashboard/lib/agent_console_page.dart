import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';

class AgentConsolePage extends StatefulWidget {
  const AgentConsolePage({super.key, required this.id});
  final String id;

  @override
  State<AgentConsolePage> createState() => _AgentConsolePageState();
}

class _AgentConsolePageState extends State<AgentConsolePage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  Timer? timer;
  StreamSubscription? subscription;
  Map<String, dynamic>? profile;
  Map<String, dynamic>? inspection;
  List<Map<String, dynamic>> jobs = [];
  String? error;
  String? responseError;
  String? inputJob;
  bool loading = false;
  bool sending = false;

  @override
  void initState() {
    super.initState();
    subscription = connection.stream.listen((_) {
      if (!connection.state.connected) {
        setState(() {
          profile = null;
          inspection = null;
          jobs = [];
          inputJob = null;
          responseError = null;
        });
      } else {
        refresh();
      }
    });
    refresh();
    timer = Timer.periodic(const Duration(seconds: 2), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    subscription?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || sending || !connection.state.connected) return;
    loading = true;
    final epoch = connection.generation;
    try {
      final data = await connection.request('organizations/state');
      if (!mounted || epoch != connection.generation) return;
      final current = List<Map<String, dynamic>>.from(
        data['profiles'],
      ).where((p) => p['id'] == widget.id).firstOrNull;
      final records = List<Map<String, dynamic>>.from(data['jobs'])
          .where(
            (j) =>
                j['profile_id'] == widget.id ||
                (j['participants'] as List? ?? []).contains(widget.id),
          )
          .toList();
      setState(() {
        profile = current;
        jobs = records;
      });
      if (current == null) {
        setState(() {
          inspection = null;
          error = 'Agent was not found.';
        });
        return;
      }
      connection.selectedOrganization.value =
          current['organization_id'] as String;
      final result = await OrganizationCommand('inspect', {
        'organization_id': current['organization_id'],
        'profile_id': widget.id,
      }).execute(connection);
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        inspection = Map<String, dynamic>.from(result);
        error = null;
      });
    } catch (e) {
      if (mounted && epoch == connection.generation) {
        setState(() {
          error = '$e';
          inspection = null;
        });
      }
    } finally {
      loading = false;
    }
  }

  Future<void> sendKey(String key) async {
    if (sending ||
        inspection?['status'] != 'blocked' ||
        inspection?['console_token'] == null ||
        !connection.state.connected)
      return;
    final epoch = connection.generation;
    setState(() {
      sending = true;
      responseError = null;
    });
    try {
      final result = await OrganizationCommand('input', {
        'organization_id': profile!['organization_id'],
        'profile_id': widget.id,
        'key': key,
        'console_token': inspection!['console_token'],
      }).execute(connection);
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        inputJob = result['id'] as String;
        inspection = null;
      });
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => responseError = '$e');
    } finally {
      if (mounted) setState(() => sending = false);
    }
    // The gateway queues input; its job state is the delivery evidence.
    await refresh();
  }

  @override
  Widget build(BuildContext context) => JuiceBuilder<DashboardBloc>(
    builder: (context, bloc, _) {
      final blocked = inspection?['status'] == 'blocked';
      final delivery = jobs.where((j) => j['id'] == inputJob).firstOrNull;
      final awaitingInput =
          inputJob != null &&
          (delivery == null ||
              ['queued', 'running'].contains(delivery['state']));
      final canRespond =
          blocked &&
          !sending &&
          !awaitingInput &&
          bloc.state.connected &&
          inspection?['console_token'] != null;
      return ListView(
        padding: const EdgeInsets.all(28),
        children: [
          Text(
            '${profile?['name'] ?? 'Agent'} console',
            style: Theme.of(context).textTheme.headlineLarge,
          ),
          if (profile != null)
            Text('${profile!['role']} · ${profile!['runtime']}'),
          const SizedBox(height: 12),
          if (!bloc.state.connected)
            const Text('Connect on the dashboard first.'),
          if (error != null) SelectableText(error!),
          if (responseError != null) SelectableText(responseError!),
          if (delivery != null) ...[
            Text('Terminal response: ${delivery['state']}'),
            if ('${delivery['error'] ?? ''}'.isNotEmpty)
              Text('${delivery['error']}'),
          ],
          if (blocked)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Requires attention',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const Text(
                      'Review the request in the console below. Use the arrow or Tab keys to select Allow or Reject in the runtime prompt, then confirm the selected response. Escape cancels where supported by the runtime.',
                    ),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final key in ['up', 'down', 'tab', 'shift+tab'])
                          OutlinedButton(
                            onPressed: canRespond ? () => sendKey(key) : null,
                            child: Text(key),
                          ),
                        FilledButton(
                          onPressed: canRespond ? () => sendKey('enter') : null,
                          child: const Text('Confirm selected Allow / Reject'),
                        ),
                        OutlinedButton(
                          onPressed: canRespond ? () => sendKey('esc') : null,
                          child: const Text('Cancel request (Esc)'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Text(
                  'Live console · ${inspection?['status'] ?? 'unavailable'}',
                ),
              ),
              IconButton(
                tooltip: 'Refresh console',
                onPressed: sending ? null : refresh,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          const Text(
            'Refreshes every 2 seconds. Shows retained terminal output; earlier work may be omitted.',
          ),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.surfaceContainerLow,
              borderRadius: BorderRadius.circular(8),
            ),
            child: SelectableText(
              '${inspection?['output'] ?? 'No live session output available.'}',
              style: const TextStyle(fontFamily: 'RobotoMono', fontSize: 13),
            ),
          ),
          if ('${inspection?['reply_draft'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 16),
            const Text('Current reply draft'),
            SelectableText('${inspection!['reply_draft']}'),
          ],
        ],
      );
    },
  );
}
