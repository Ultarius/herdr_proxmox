import 'package:herdr_dashboard_state/cli_setup_bloc.dart';
import 'clipboard_copy.dart';
import 'setup_links.dart';
import 'external_link.dart';
import 'package:juice/juice.dart';
import 'package:xterm/xterm.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';
import 'ssh_access_card.dart';
import 'toolchain_card.dart';

class CliSetupPage extends StatefulWidget {
  const CliSetupPage({super.key, required this.coordinator});
  final AppCoordinator coordinator;
  @override
  State<CliSetupPage> createState() => _CliSetupPageState();
}

class _CliSetupPageState extends State<CliSetupPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  final terminal = Terminal(maxLines: 2000);
  late final CliSetupBloc feature;
  StreamSubscription? subscription;
  final paste = TextEditingController();
  int outputRevision = 0, clearRevision = 0;
  List<Map<String, dynamic>> get clis => feature.state.clis;
  Map<String, dynamic>? get integrations => feature.state.integrations;
  String? get session => feature.state.session;
  String? get selected => feature.state.selected;
  String? get error => feature.state.error;
  bool get busy => feature.state.busy;
  bool get running => feature.state.running;

  @override
  void initState() {
    super.initState();
    feature = CliSetupBloc(connection);
    terminal.onOutput = sendInput;
    terminal.onResize = (width, height, pixelWidth, pixelHeight) {
      feature.resize(width, height);
    };
    subscription = feature.stream.listen((_) {
      if (!mounted) return;
      final state = feature.state;
      if (state.clearRevision != clearRevision) {
        clearRevision = state.clearRevision;
        clearTerminal();
        paste.clear();
        if (state.session != null)
          feature.resize(terminal.viewWidth, terminal.viewHeight);
      }
      if (state.outputRevision != outputRevision) {
        outputRevision = state.outputRevision;
        terminal.write(state.output);
      }
      setState(() {});
    });
  }

  Future<void> refresh() => feature.refresh();
  void sendInput(String value) {
    feature.input(value);
  }

  Future<void> start(String cli) => feature.openSession(cli);
  Future<void> verify(String cli) => cliAction('verify', cli);
  Future<void> installIntegration(String cli) =>
      cliAction('install_integration', cli);
  Future<void> cliAction(String action, String cli) async {
    await feature.action(action, cli);
    if (mounted && feature.state.message != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(feature.state.message!)));
    }
  }

  Future<void> stop() => feature.stop();
  void clearTerminal() {
    terminal.mainBuffer.clear();
    terminal.altBuffer.clear();
    terminal.write('\x1b[?1049l\x1b[2J\x1b[H');
  }

  @override
  void dispose() {
    subscription?.cancel();
    feature.dispose();
    terminal.onOutput = null;
    terminal.onResize = null;
    clearTerminal();
    paste.dispose();
    super.dispose();
  }

  Widget accountCard(Map<String, dynamic> cli) {
    final active = session != null && selected == cli['id'];
    final ready = [
      'configured',
      'credentials_detected',
    ].contains(cli['status']);
    final color = ready
        ? const Color(0xff75d8a3)
        : active
        ? const Color(0xffff7917)
        : const Color(0xffaaaaaa);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  ready ? Icons.check_circle_outline : Icons.terminal,
                  color: color,
                  size: 22,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    '${cli['name']}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              active ? 'Setup open' : '${cli['status']}'.replaceAll('_', ' '),
              style: TextStyle(color: color),
            ),
            const SizedBox(height: 8),
            Text(
              '${cli['detail']}',
              style: const TextStyle(color: Color(0xffaaaaaa)),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: busy || session != null || cli['installed'] != true
                  ? null
                  : () => start(cli['id'] as String),
              icon: Icon(
                ready ? Icons.settings_outlined : Icons.login,
                size: 18,
              ),
              label: Text(
                active
                    ? 'Setup in progress'
                    : cli['installed'] != true
                    ? 'CLI not installed'
                    : ready
                    ? 'Manage account'
                    : 'Connect account',
              ),
            ),
            if (cli['id'] == 'claude' && cli['installed'] == true)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: OutlinedButton.icon(
                  onPressed: busy || session != null
                      ? null
                      : () => verify(cli['id'] as String),
                  icon: const Icon(Icons.play_arrow_outlined, size: 18),
                  label: const Text('Test account'),
                ),
              ),
            if (cli['installed'] == true &&
                (cli['integration'] as Map?)?['current'] == true)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  'Herdr integration '
                  '${(cli['integration'] as Map)['version'] ?? ''} — connected',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xff9fd39f),
                  ),
                ),
              ),
            if (cli['installed'] == true &&
                (cli['integration'] as Map?)?['current'] != true)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      (cli['integration'] as Map?)?['installed'] == true
                          ? 'Herdr integration is outdated, needs repair or could '
                                'not be verified. Until it is current this agent cannot '
                                'report working or blocked state, and finished sessions '
                                'cannot be resumed.'
                          : 'Herdr integration missing. Without it this agent cannot '
                                'report working or blocked state, and finished sessions '
                                'cannot be resumed.',
                      style: const TextStyle(
                        fontSize: 12,
                        color: Color(0xffaaaaaa),
                      ),
                    ),
                    const SizedBox(height: 8),
                    OutlinedButton.icon(
                      onPressed: busy || session != null
                          ? null
                          : () => installIntegration(cli['id'] as String),
                      icon: const Icon(Icons.extension_outlined, size: 18),
                      label: Text(
                        (cli['integration'] as Map?)?['installed'] == true
                            ? 'Update Herdr integration'
                            : 'Install Herdr integration',
                      ),
                    ),
                  ],
                ),
              ),
            if (session != null && !active)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'Close the active setup to switch accounts.',
                  style: TextStyle(fontSize: 12, color: Color(0xff888888)),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> copySetupText(String value) async {
    final message = copySetupClipboard(value)
        ? 'Copied.'
        : await copyTextOrDownload(value, 'cli-setup.txt');
    if (mounted)
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> selectableOutput() => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Terminal text'),
      content: SizedBox(
        width: 700,
        child: SingleChildScrollView(
          child: SelectableText(terminal.buffer.getText()),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    ),
  );

  String integrationSummary(Map<String, dynamic> summary) {
    final connected = summary['connected'] ?? 0;
    final outdated = summary['outdated'] ?? 0;
    final missing = summary['missing'] ?? 0;
    final tracked = connected + outdated + missing;
    if (tracked == 0) return '';
    if (connected == tracked) {
      return 'Herdr integration: all $tracked installed agents are connected. '
          'They report working/blocked state and resume finished sessions.';
    }
    String names(String key) =>
        ((summary[key] as List?) ?? const []).map((name) => '$name').join(', ');
    final stale = names('outdated_names');
    final absent = names('missing_names');
    final parts = <String>[];
    if (stale.isNotEmpty) parts.add('outdated: $stale');
    if (absent.isNotEmpty) parts.add('not installed: $absent');
    return 'Herdr integration: $connected of $tracked installed agents current'
        '${parts.isEmpty ? '' : ' — ${parts.join('; ')}'}. '
        'Agents without a current integration cannot report state or resume sessions.';
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
                'CLI accounts',
                style: Theme.of(context).textTheme.headlineLarge,
              ),
              const SizedBox(height: 12),
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
                    onPressed: bloc.state.connected ? refresh : null,
                    child: const Text('Refresh status'),
                  ),
                ],
              ),
              if (!bloc.state.connected)
                const Text(
                  'Connect with your dashboard token on the Workspaces page first.',
                )
              else ...[
                const Text(
                  'Connect your coding agents to their providers. Choose an account below to open its sign-in terminal.',
                ),
                if (integrations != null &&
                    (integrations!['total'] ?? 0) > 0) ...[
                  const SizedBox(height: 12),
                  Text(
                    integrationSummary(integrations!),
                    style: TextStyle(
                      fontSize: 12,
                      color:
                          integrations!['connected'] == integrations!['total']
                          ? const Color(0xff9fd39f)
                          : const Color(0xffd8b06a),
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final columns = constraints.maxWidth >= 750 ? 2 : 1;
                    final width =
                        (constraints.maxWidth - (columns - 1) * 12) / columns;
                    return Wrap(
                      spacing: 12,
                      runSpacing: 12,
                      children: [
                        for (final cli in clis)
                          SizedBox(width: width, child: accountCard(cli)),
                      ],
                    );
                  },
                ),
                if (session != null) ...[
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 12,
                    children: [
                      Text(
                        '${clis.where((c) => c['id'] == selected).firstOrNull?['name'] ?? selected} setup · ${running ? "Active" : "Finished"}',
                      ),
                      TextButton(
                        onPressed: running ? () => sendInput('\x03') : null,
                        child: const Text('Ctrl+C'),
                      ),
                      TextButton(
                        onPressed: stop,
                        child: const Text('Close terminal'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Open the sign-in link shown below in your browser. Use the menu controls or click the terminal to type.',
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    children: [
                      TextButton.icon(
                        onPressed: () =>
                            copySetupText(terminal.buffer.getText()),
                        icon: const Icon(Icons.copy),
                        label: const Text('Copy terminal text'),
                      ),
                      TextButton(
                        onPressed: selectableOutput,
                        child: const Text('Select terminal text'),
                      ),
                    ],
                  ),
                  for (final link in setupLinks(terminal.buffer.getText()))
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          SelectableText(link.toString()),
                          Wrap(
                            spacing: 8,
                            children: [
                              TextButton.icon(
                                onPressed: () {
                                  try {
                                    openExternalLink(link);
                                  } catch (exception) {
                                    ScaffoldMessenger.of(context).showSnackBar(
                                      SnackBar(content: Text('$exception')),
                                    );
                                  }
                                },
                                icon: const Icon(Icons.open_in_new),
                                label: Text('Open link · ${link.host}'),
                              ),
                              TextButton.icon(
                                onPressed: () => copySetupText(link.toString()),
                                icon: const Icon(Icons.copy),
                                label: const Text('Copy link'),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  Container(
                    clipBehavior: Clip.antiAlias,
                    decoration: BoxDecoration(
                      border: Border.all(color: const Color(0xff343434)),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    height: 340,
                    child: TerminalView(
                      terminal,
                      autofocus: true,
                      padding: const EdgeInsets.all(12),
                      textStyle: const TerminalStyle(
                        fontSize: 14,
                        fontFamily: 'RobotoMono',
                        height: 1.3,
                      ),
                    ),
                  ),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final key in const {
                        '↑ Up': '\x1b[A',
                        '↓ Down': '\x1b[B',
                        'Enter': '\r',
                        'Tab': '\t',
                        'Escape': '\x1b',
                      }.entries)
                        OutlinedButton(
                          onPressed: running
                              ? () => sendInput(key.value)
                              : null,
                          child: Text(key.key),
                        ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: paste,
                    obscureText: true,
                    decoration: const InputDecoration(
                      labelText: 'Authorization code or API key',
                      helperText:
                          'Input is hidden here. Send it only when the terminal asks.',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: FilledButton(
                      onPressed: running
                          ? () {
                              sendInput('${paste.text}\r');
                              paste.clear();
                            }
                          : null,
                      child: const Text('Send to terminal'),
                    ),
                  ),
                  const Text(
                    'The CLI controls input echo. Terminal output stays in memory and is cleared when closed. Closing the page stops setup; abandoned sessions expire after 10 minutes without activity.',
                  ),
                ],
              ],
              if (bloc.state.connected) ...[
                const SizedBox(height: 20),
                const SshAccessCard(),
                const SizedBox(height: 20),
                const ToolchainCard(),
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
