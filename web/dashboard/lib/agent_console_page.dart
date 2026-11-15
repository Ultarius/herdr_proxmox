import 'package:herdr_dashboard_state/agent_console_bloc.dart' as console;
export 'package:herdr_dashboard_state/agent_console_bloc.dart'
    show agentConsoleRefreshInterval;
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'folder_access_panel.dart';

class AgentConsolePage extends StatefulWidget {
  const AgentConsolePage({super.key, required this.id});
  final String id;

  @override
  State<AgentConsolePage> createState() => _AgentConsolePageState();
}

class _AgentConsolePageState extends State<AgentConsolePage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final console.AgentConsoleBloc feature;
  StreamSubscription? subscription;
  Map<String, dynamic>? get profile => feature.state.profile;
  Map<String, dynamic>? get inspection => feature.state.inspection;
  List<Map<String, dynamic>> get jobs => feature.state.jobs;
  String? get error => feature.state.error;
  String? get responseError => feature.state.responseError;
  String? get inputJob => feature.state.inputJob;
  OrganizationCommand? get pendingInput => feature.state.pendingInput;
  bool get loading => feature.state.loading;
  bool get sending => feature.state.sending;

  bool showDraft = false;
  bool followOutput = true;
  bool scrollingToBottom = false;
  final consoleScroll = ScrollController();
  final detailsScroll = ScrollController();

  @override
  void initState() {
    super.initState();
    consoleScroll.addListener(() {
      if (!scrollingToBottom && consoleScroll.hasClients) {
        followOutput = consoleScroll.position.extentAfter <= 1;
      }
    });
    feature = console.AgentConsoleBloc(connection, widget.id);
    subscription = feature.stream.listen((_) {
      if (!mounted) return;
      setState(() {});
      followLatestOutput();
    });
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
    feature.dispose();
    subscription?.cancel();
    consoleScroll.dispose();
    detailsScroll.dispose();
    super.dispose();
  }

  Future<void> refresh() => feature.refresh();
  Future<void> sendKey(String key) => feature.sendKey(key);
  Future<void> submitPendingInput() => feature.retryResponse();

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
