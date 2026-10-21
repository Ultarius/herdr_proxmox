import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'folder_access_panel.dart';

Duration agentConsoleRefreshInterval(String? status) => switch (status) {
  'working' || 'running' || 'busy' || 'thinking' => const Duration(seconds: 1),
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

  /// True only while a submitted response is still being delivered, so the
  /// snapshot poll stops once its job state has settled.
  bool trackDelivery = false;

  /// The reviewed prompt a pending response belongs to, and that response's
  /// command. A lost response must replay the same request ID, even when a new
  /// inspection shows a different prompt. New keys wait until it is reconciled.
  OrganizationCommand? pendingInput;
  DateTime? metadataReadAt;
  int generation = -1;
  bool loading = false;
  bool sending = false;
  bool showDraft = false;
  bool followOutput = true;
  bool scrollingToBottom = false;
  final consoleScroll = ScrollController();
  final detailsScroll = ScrollController();

  @override
  void initState() {
    super.initState();
    generation = connection.generation;
    consoleScroll.addListener(() {
      if (!scrollingToBottom && consoleScroll.hasClients) {
        followOutput = consoleScroll.position.extentAfter <= 1;
      }
    });
    subscription = connection.stream.listen((_) {
      if (!mounted) return;
      if (generation != connection.generation || !connection.state.connected) {
        generation = connection.generation;
        setState(() {
          profile = null;
          inspection = null;
          jobs = [];
          inputJob = null;
          responseError = null;
          pendingInput = null;
          trackDelivery = false;
          metadataReadAt = null;
        });
      } else {
        refresh();
      }
    });
    refresh();
  }

  void followLatestOutput() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !followOutput || !consoleScroll.hasClients) return;
      scrollingToBottom = true;
      consoleScroll.jumpTo(consoleScroll.position.maxScrollExtent);
      scrollingToBottom = false;
    });
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
      // Read the full snapshot to discover the profile, observe response
      // delivery, and refresh cached metadata once a minute. The frequent
      // terminal reads use the narrower inspect endpoint.
      if (profile == null ||
          trackDelivery ||
          metadataReadAt == null ||
          DateTime.now().difference(metadataReadAt!) >=
              const Duration(minutes: 1)) {
        final data = Map<String, dynamic>.from(
          await connection.request('organizations/state'),
        );
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
          metadataReadAt = DateTime.now();
          jobs = records;
          final delivery = records
              .where((j) => j['id'] == inputJob)
              .firstOrNull;
          if (delivery != null &&
              !['queued', 'running'].contains(delivery['state'])) {
            trackDelivery = false;
          }
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
      }
      final current = profile;
      if (current == null) return;
      final result = await OrganizationCommand('inspect', {
        'organization_id': current['organization_id'],
        'profile_id': widget.id,
      }).execute(connection);
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        inspection = Map<String, dynamic>.from(result);
        error = null;
      });
      followLatestOutput();
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
        pendingInput != null ||
        inspection?['status'] != 'blocked' ||
        inspection?['console_token'] == null ||
        !connection.state.connected)
      return;
    pendingInput = OrganizationCommand('input', {
      'organization_id': profile!['organization_id'],
      'profile_id': widget.id,
      'key': key,
      'console_token': inspection!['console_token'],
    });
    await submitPendingInput();
  }

  Future<void> submitPendingInput() async {
    if (sending || pendingInput == null || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() {
      sending = true;
      responseError = null;
    });
    try {
      final result = await pendingInput!.execute(connection);
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        inputJob = result['id'] as String;
        trackDelivery = true;
        inspection = null;
        pendingInput = null;
      });
    } catch (e) {
      // Keep the pending command: the gateway may have applied it and only the
      // response was lost, so the retry must carry the same request ID.
      if (mounted && epoch == connection.generation) {
        setState(() {
          // A 4xx admission refusal did not create an input job. A transport
          // failure or 5xx cannot establish that, so keep its replay identity.
          if (e is DashboardRequestException &&
              e.statusCode >= 400 &&
              e.statusCode < 500) {
            pendingInput = null;
          }
          responseError = '$e';
        });
      }
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
          pendingInput == null &&
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
                        if (inspection?['scratch_directory'] != null)
                          SelectableText(
                            'Session scratch: ${inspection!['scratch_directory']}',
                          ),
                        const SizedBox(height: 12),
                        if (!bloc.state.connected)
                          const Text('Connect on the dashboard first.'),
                        if (error != null) SelectableText(error!),
                        if (responseError != null)
                          SelectableText(responseError!),
                        if (pendingInput != null && !sending) ...[
                          Text(
                            'Delivery of ${pendingInput!.body?['key']} is unknown. Recheck that response before sending another key.',
                          ),
                          OutlinedButton(
                            onPressed: bloc.state.connected
                                ? submitPendingInput
                                : null,
                            child: const Text('Recheck pending response'),
                          ),
                        ],
                        if (delivery != null) ...[
                          Text('Terminal response: ${delivery['state']}'),
                          if ('${delivery['error'] ?? ''}'.isNotEmpty)
                            Text('${delivery['error']}'),
                        ],
                        if (error == null)
                          FolderAccessPanel(inspection: inspection),
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
                        'Refreshes every second while working, every 5 seconds while idle, and every 2 seconds while awaiting attention. Retained output may omit earlier work.',
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
