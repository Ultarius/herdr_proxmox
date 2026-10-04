import 'package:juice/juice.dart';
import 'package:xterm/xterm.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';
import 'ssh_access_card.dart';
import 'update_card.dart';

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
  List<Map<String, dynamic>> clis = [];
  String? session;
  String? selected;
  String? error;
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
    });
    final epoch = ++generation;
    try {
      final data = await connection.request('cli-setup/start', {'cli': cli});
      if (!mounted || epoch != generation || !connection.state.connected) {
        await connection.request('cli-setup/close', {'id': data['id']});
        return;
      }
      clearTerminal();
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
        terminal.write('\x1b[2J\x1b[H[Earlier output expired]\r\n');
      }
      terminal.write(data['output'] as String);
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
    terminal.mainBuffer.clear();
    terminal.altBuffer.clear();
    terminal.write('\x1b[?1049l\x1b[2J\x1b[H');
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
                'CLI configuration',
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
                    child: const Text('Check configuration'),
                  ),
                ],
              ),
              if (!bloc.state.connected)
                const Text(
                  'Connect with your dashboard token on the Workspaces page first.',
                )
              else ...[
                const Text(
                  'Configure the CLIs as the LXC herdr user. Open the authorization URL printed by the CLI in your browser, then return here to enter any code. Use arrow keys and Enter for menus.',
                ),
                const SizedBox(height: 12),
                for (final cli in clis)
                  Card(
                    child: ListTile(
                      title: Text(
                        '${cli['name']} — ${cli['status'].toString().replaceAll('_', ' ')}',
                      ),
                      subtitle: Text('${cli['detail']}'),
                      trailing: FilledButton(
                        onPressed:
                            busy || session != null || cli['installed'] != true
                            ? null
                            : () => start(cli['id'] as String),
                        child: const Text('Configure'),
                      ),
                    ),
                  ),
                if (session != null) ...[
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 12,
                    children: [
                      Text(
                        'Setup terminal: $selected ${running ? "" : "(ended)"}',
                      ),
                      TextButton(
                        onPressed: () => sendInput('\x03'),
                        child: const Text('Ctrl+C'),
                      ),
                      TextButton(
                        onPressed: stop,
                        child: const Text('Close terminal'),
                      ),
                    ],
                  ),
                  SizedBox(
                    height: 420,
                    child: TerminalView(terminal, autofocus: true),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: paste,
                    obscureText: true,
                    decoration: const InputDecoration(
                      labelText: 'Paste authorization code or API key',
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
                const UpdateCard(),
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
