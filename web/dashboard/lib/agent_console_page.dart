import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';

Duration agentConsoleRefreshInterval(String? status) => switch (status) {
  'working' ||
  'running' ||
  'busy' ||
  'thinking' => const Duration(milliseconds: 250),
  'idle' || 'done' => const Duration(seconds: 5),
  _ => const Duration(seconds: 2),
};

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
  bool showDraft = false;
  final consoleScroll = ScrollController();
  final detailsScroll = ScrollController();

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
  }

  @override
  void dispose() {
    timer?.cancel();
    subscription?.cancel();
    consoleScroll.dispose();
    detailsScroll.dispose();
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || sending || !connection.state.connected) return;
    timer?.cancel();
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
      if (mounted && connection.state.connected) {
        timer = Timer(
          agentConsoleRefreshInterval(inspection?['status'] as String?),
          refresh,
        );
      }
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
      final hasDraft = '${inspection?['reply_draft'] ?? ''}'.isNotEmpty;
      return LayoutBuilder(
        builder: (context, constraints) => Padding(
          padding: EdgeInsets.all(constraints.maxWidth < 600 ? 12 : 20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              ConstrainedBox(
                constraints: BoxConstraints(
                  maxHeight: (constraints.maxHeight - 40) * .55,
                ),
                child: Scrollbar(
                  controller: detailsScroll,
                  child: SingleChildScrollView(
                    controller: detailsScroll,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          '${profile?['name'] ?? 'Agent'} console',
                          style: Theme.of(context).textTheme.headlineSmall,
                        ),
                        if (profile != null)
                          Text('${profile!['role']} · ${profile!['runtime']}'),
                        const SizedBox(height: 12),
                        if (!bloc.state.connected)
                          const Text('Connect on the dashboard first.'),
                        if (error != null) SelectableText(error!),
                        if (responseError != null)
                          SelectableText(responseError!),
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
                                    style: Theme.of(
                                      context,
                                    ).textTheme.titleLarge,
                                  ),
                                  const Text(
                                    'Select Allow or Reject in the console prompt, then confirm. Escape cancels where supported.',
                                  ),
                                  const SizedBox(height: 12),
                                  Wrap(
                                    spacing: 8,
                                    runSpacing: 8,
                                    children: [
                                      for (final key in [
                                        'up',
                                        'down',
                                        'tab',
                                        'shift+tab',
                                      ])
                                        OutlinedButton(
                                          onPressed: canRespond
                                              ? () => sendKey(key)
                                              : null,
                                          child: Text(key),
                                        ),
                                      FilledButton(
                                        onPressed: canRespond
                                            ? () => sendKey('enter')
                                            : null,
                                        child: const Text(
                                          'Confirm selected Allow / Reject',
                                        ),
                                      ),
                                      OutlinedButton(
                                        onPressed: canRespond
                                            ? () => sendKey('esc')
                                            : null,
                                        child: const Text(
                                          'Cancel request (Esc)',
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ),
                      ],
                    ),
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
                  if (hasDraft)
                    TextButton(
                      onPressed: () => setState(() => showDraft = !showDraft),
                      child: Text(
                        showDraft ? 'Live console' : 'Current reply draft',
                      ),
                    ),
                  const Tooltip(
                    message:
                        'Refreshes every 0.25 seconds while working, every 5 seconds while idle, and every 2 seconds while awaiting attention. Retained output may omit earlier work.',
                    child: Icon(Icons.info_outline, size: 18),
                  ),
                  IconButton(
                    tooltip: 'Refresh console',
                    onPressed: sending ? null : refresh,
                    icon: const Icon(Icons.refresh),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Expanded(
                child: Container(
                  key: const ValueKey('agent-console-viewport'),
                  clipBehavior: Clip.antiAlias,
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerLow,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Scrollbar(
                    controller: consoleScroll,
                    thumbVisibility: true,
                    child: SingleChildScrollView(
                      controller: consoleScroll,
                      child: Align(
                        alignment: Alignment.topLeft,
                        child: SelectableText(
                          showDraft && hasDraft
                              ? '${inspection!['reply_draft']}'
                              : '${inspection?['output'] ?? 'No live session output available.'}',
                          style: const TextStyle(
                            fontFamily: 'RobotoMono',
                            fontSize: 13,
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    },
  );
}
