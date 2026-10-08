import 'package:juice/juice.dart';
import 'package:xterm/xterm.dart';
import 'cli_setup_auth.dart';
import 'cli_setup_browser.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';
import 'ssh_access_card.dart';

class CliSetupPage extends StatefulWidget {
  const CliSetupPage({super.key, required this.coordinator});
  final AppCoordinator coordinator;
  @override
  State<CliSetupPage> createState() => _CliSetupPageState();
}

class _CliSetupPageState extends State<CliSetupPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  final terminal = Terminal(maxLines: 2000);
  final paste = TextEditingController();
  final links = CliSetupLinks();
  List<Map<String, dynamic>> clis = [];
  String? session;
  String? selected;
  String? error;
  String? inputError;
  bool busy = false;
  bool running = false;
  bool polling = false;
  int cursor = 0;
  int generation = 0;
  Timer? timer;
  Future<void> writes = Future.value();

  @override
  void initState() {
    super.initState();
    terminal.onOutput = sendInput;
    terminal.onResize = (width, height, pixelWidth, pixelHeight) {
      final id = session;
      if (id != null && running) {
        enqueue('resize', {
          'id': id,
          'cols': width.clamp(10, 400),
          'rows': height.clamp(5, 150),
        });
      }
    };
    refresh();
    timer = Timer.periodic(const Duration(milliseconds: 300), (_) => poll());
  }

  void showError(Object value) {
    if (mounted)
      setState(() => error = value.toString().replaceFirst('Exception: ', ''));
  }

  Future<void> refresh() async {
    if (!connection.state.connected) return;
    final epoch = connection.generation;
    try {
      final data = await connection.request('cli-setup');
      if (mounted &&
          connection.state.connected &&
          epoch == connection.generation) {
        setState(() => clis = List<Map<String, dynamic>>.from(data['clis']));
      }
    } catch (e) {
      showError(e);
    }
  }

  void enqueue(String action, Map<String, dynamic> body) {
    final epoch = generation;
    writes = writes.then((_) async {
      if (!mounted || epoch != generation || !connection.state.connected)
        return;
      try {
        await connection.request('cli-setup/$action', body);
      } catch (e) {
        showError(e);
      }
    });
  }

  void sendInput(String data) {
    if (session != null && running && data.isNotEmpty) {
      enqueue('input', {'id': session, 'data': data});
    }
  }

  Future<void> start(String cli) async {
    setState(() {
      busy = true;
      error = null;
      inputError = null;
    });
    final epoch = ++generation;
    try {
      final data = await connection.request('cli-setup/start', {'cli': cli});
      if (!mounted || epoch != generation || !connection.state.connected) {
        await connection.request('cli-setup/close', {'id': data['id']});
        return;
      }
      clearTerminal();
      paste.clear();
      setState(() {
        session = data['id'];
        selected = cli;
        cursor = 0;
        running = true;
      });
      enqueue('resize', {
        'id': session,
        'cols': terminal.viewWidth.clamp(10, 400),
        'rows': terminal.viewHeight.clamp(5, 150),
      });
      await poll();
    } catch (e) {
      showError(e);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> poll() async {
    final id = session;
    if (id == null || polling || !running) return;
    if (!connection.state.connected) {
      await stop();
      return;
    }
    polling = true;
    try {
      final data = await connection.request('cli-setup/poll', {
        'id': id,
        'cursor': cursor,
      });
      if (!mounted || session != id || !connection.state.connected) return;
      if (data['truncated'] == true) {
        links.clear();
        terminal.write('\x1b[2J\x1b[H[Earlier output expired]\r\n');
      }
      final output = data['output'] as String;
      terminal.write(output);
      if (output.isNotEmpty) setState(() => links.add(output));
      cursor = data['cursor'] as int;
      if (data['running'] == false) {
        terminal.write('\r\n[Setup command exited: ${data['exit_code']}]\r\n');
        setState(() => running = false);
        await refresh();
      }
    } catch (e) {
      showError(e);
      setStateIfMountedStopped();
    } finally {
      polling = false;
    }
  }

  void setStateIfMountedStopped() {
    if (mounted) setState(() => running = false);
  }

  Future<void> stop() async {
    final id = session;
    generation++;
    clearTerminal();
    paste.clear();
    if (mounted)
      setState(() {
        session = null;
        running = false;
        selected = null;
        inputError = null;
      });
    if (id != null) {
      try {
        await connection.request('cli-setup/close', {'id': id});
      } catch (e) {
        showError(e);
      }
    }
    await refresh();
  }

  void clearTerminal() {
    links.clear();
    terminal.mainBuffer.clear();
    terminal.altBuffer.clear();
    terminal.write('\x1b[?1049l\x1b[2J\x1b[H');
  }

  void submitCode() {
    if (!running || session == null) return;
    try {
      final input = setupSubmission(
        paste.text,
        claude: selected == 'claude',
        state: links.claudeState,
      );
      setState(() => inputError = null);
      sendInput(input);
      paste.clear();
    } on FormatException catch (e) {
      setState(() => inputError = e.message);
    }
  }

  Future<void> copyLink(String url) async {
    try {
      await copySetupUrl(url);
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('Sign-in URL copied.')));
      }
    } catch (_) {
      showError(
        'Copy unavailable. Select the sign-in URL and use your browser\'s Copy command.',
      );
    }
  }

  Future<void> restart() async {
    final cli = selected;
    if (cli == null || busy) return;
    await stop();
    if (mounted && connection.state.connected) await start(cli);
  }

  @override
  void dispose() {
    timer?.cancel();
    generation++;
    final id = session;
    if (id != null) {
      unawaited(
        connection
            .request('cli-setup/close', {'id': id})
            .catchError((_) => null),
      );
    }
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
                      if (!running)
                        TextButton(
                          onPressed: busy ? null : restart,
                          child: const Text('Restart sign-in'),
                        ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Open the sign-in link shown below in your browser. Use the menu controls or click the terminal to type.',
                  ),
                  const SizedBox(height: 12),
                  for (final url in links.urls) ...[
                    SelectableText(url),
                    Wrap(
                      spacing: 8,
                      children: [
                        OutlinedButton.icon(
                          onPressed: running ? () => copyLink(url) : null,
                          icon: const Icon(Icons.copy),
                          label: const Text('Copy sign-in URL'),
                        ),
                        OutlinedButton.icon(
                          onPressed: running ? () => openSetupUrl(url) : null,
                          icon: const Icon(Icons.open_in_new),
                          label: const Text('Open sign-in URL'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                  ],
                  if (!running)
                    const Text(
                      'Setup has ended. If sign-in failed, restart sign-in and authorize using the new URL. Previous codes cannot be reused.',
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
                    enabled: running,
                    obscureText: true,
                    autocorrect: false,
                    enableSuggestions: false,
                    onSubmitted: (_) => submitCode(),
                    decoration: InputDecoration(
                      errorText: inputError,
                      errorMaxLines: 3,
                      labelText: 'Authorization code or API key',
                      helperText: selected == 'claude'
                          ? 'Paste the full code from this sign-in attempt, including # and everything after it.'
                          : 'Input is hidden here. Send it only when the terminal asks.',
                      helperMaxLines: 3,
                      border: const OutlineInputBorder(),
                    ),
                  ),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: FilledButton(
                      onPressed: running ? submitCode : null,
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
