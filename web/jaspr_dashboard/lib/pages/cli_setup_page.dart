import 'package:herdr_dashboard_state/cli_setup_bloc.dart';
import 'operations_support.dart';
import 'ops_ui.dart';
import '../terminal.dart';
import 'setup_links.dart';
import 'ssh_access_card.dart';
import 'toolchain_card.dart';

class CliSetupPage extends StatefulComponent {
  const CliSetupPage({super.key});
  @override
  State<CliSetupPage> createState() => _CliState();
}

class _CliState extends State<CliSetupPage> {
  DashboardBloc get connection => dashboardConnection;
  late final BrowserTerminal terminal;
  late final CliSetupBloc feature;
  StreamSubscription? subscription;
  String paste = '';
  String? clipboardMessage;
  int outputRevision = 0, clearRevision = 0;
  List<Map<String, dynamic>> get clis => feature.state.clis;
  Map<String, dynamic>? get integrations => feature.state.integrations;
  String? get session => feature.state.session;
  String? get selected => feature.state.selected;
  String? get error => feature.state.error;
  bool get busy => feature.state.busy;
  bool get running => feature.state.running;
  bool get refreshing => feature.state.refreshing;
  bool get loaded => feature.state.loaded;

  @override
  void initState() {
    super.initState();
    terminal = BrowserTerminal(maxLines: 2000);
    feature = CliSetupBloc(connection);
    terminal.onOutput = sendInput;
    terminal.onChange = () {
      if (mounted) setState(() {});
    };
    terminal.onResize = (cols, rows) {
      feature.resize(cols, rows);
    };
    subscription = feature.stream.listen((_) {
      if (!mounted) return;
      final state = feature.state;
      if (state.clearRevision != clearRevision) {
        clearRevision = state.clearRevision;
        terminal.clear();
        paste = '';
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

  @override
  void dispose() {
    subscription?.cancel();
    feature.dispose();
    terminal.dispose();
    paste = '';
    super.dispose();
  }

  Future<void> refresh() => feature.refresh();
  void sendInput(String value) {
    feature.input(value);
  }

  Future<void> start(String cli) => feature.openSession(cli);
  Future<void> cliAction(String action, String cli) =>
      feature.action(action, cli);
  Future<void> stop() => feature.stop();
  Future<void> copy(String value) async {
    final result = await copyText(value, filename: 'cli-setup.txt');
    if (mounted) setState(() => clipboardMessage = result);
  }

  Component feedback() => el(
    'div',
    [
      if (busy) paragraph('Working…'),
      if (clipboardMessage ?? feature.state.message case final message?)
        paragraph(message),
      if (error != null) notice(error, error: true),
    ],
    attributes: {'aria-live': 'polite'},
  );

  String integrationSummary(Map summary) {
    final connected = summary['connected'] ?? 0,
        outdated = summary['outdated'] ?? 0,
        missing = summary['missing'] ?? 0;
    final tracked = connected + outdated + missing;
    if (tracked == 0) return '';
    if (connected == tracked)
      return 'Herdr integration: all $tracked installed agents are connected. They report working/blocked state and resume finished sessions.';
    final parts = <String>[];
    for (final entry in {
      'outdated_names': 'outdated',
      'missing_names': 'not installed',
    }.entries) {
      final names = (summary[entry.key] as List? ?? []).join(', ');
      if (names.isNotEmpty) parts.add('${entry.value}: $names');
    }
    return 'Herdr integration: $connected of $tracked installed agents current${parts.isEmpty ? '' : ' — ${parts.join('; ')}'}. Agents without a current integration cannot report state or resume sessions.';
  }

  String statusLabel(Object? status) {
    final words = '${status ?? ''}'.replaceAll('_', ' ');
    return words.isEmpty
        ? words
        : '${words[0].toUpperCase()}${words.substring(1)}';
  }

  Component account(Map<String, dynamic> cli) {
    final active = session != null && selected == cli['id'];
    final ready = [
      'configured',
      'credentials_detected',
    ].contains(cli['status']);
    final integration = cli['integration'] as Map?;
    final installed = cli['installed'] == true;
    final tone = active
        ? 'info'
        : ready
        ? 'success'
        : 'neutral';
    return el(
      'section',
      [
        el('div', [
          iconTile('bot', tone: ready ? 'success' : null),
          el('div', [
            el('h2', [text(cli['name'])], classes: 'provider-card__name'),
            el('p', [text(cli['detail'])], classes: 'provider-card__detail'),
          ], classes: 'provider-card__text'),
          pill(
            active ? 'Setup open' : statusLabel(cli['status']),
            tone: tone,
            shape: !installed
                ? 'dash'
                : ready || active
                ? 'filled'
                : 'hollow',
          ),
        ], classes: 'provider-card__head'),
        if (installed && integration?['current'] == true)
          facts([
            (
              'Herdr integration',
              el('span', [
                text('${integration?['version'] ?? ''} — connected'),
              ], classes: 'provider-card__ok'),
            ),
          ]),
        if (installed && integration?['current'] != true)
          callout(
            [
              calloutText(
                integration?['installed'] == true
                    ? 'Herdr integration is outdated, needs repair or could not be verified. Until current, this agent cannot report state or resume finished sessions.'
                    : 'Herdr integration missing. Without it this agent cannot report state or resume finished sessions.',
              ),
            ],
            tone: 'warning',
            iconName: 'link',
            actions: [
              button(
                integration?['installed'] == true
                    ? 'Update Herdr integration'
                    : 'Install Herdr integration',
                () => cliAction('install_integration', cli['id'] as String),
                disabled: busy || session != null,
                small: true,
              ),
            ],
          ),
        if (session != null && !active)
          meta('Close the active setup to switch accounts.', iconName: 'info'),
        actions([
          button(
            active
                ? 'Setup in progress'
                : !installed
                ? 'CLI not installed'
                : ready
                ? 'Manage account'
                : 'Connect account',
            () => start(cli['id'] as String),
            primary: true,
            disabled: busy || session != null || !installed,
            iconName: ready ? 'settings' : 'key-round',
          ),
          if (cli['id'] == 'claude' && installed)
            button(
              'Test account',
              () => cliAction('verify', cli['id'] as String),
              disabled: busy || session != null,
              iconName: 'circle-check',
            ),
        ]),
      ],
      classes: 'card provider-card',
      attributes: {'aria-label': '${cli['name']}'},
    );
  }

  Component setupPanel() {
    final name =
        clis.where((c) => c['id'] == selected).firstOrNull?['name'] ?? selected;
    return terminalPanel(
      title: '$name setup',
      iconName: 'key-round',
      status: [
        pill(
          running ? 'Active' : 'Finished',
          tone: running ? 'success' : 'neutral',
          shape: running ? 'filled' : 'hollow',
        ),
      ],
      actions: [
        button(
          'Ctrl+C',
          () => sendInput('\x03'),
          disabled: !running,
          small: true,
          variant: 'ghost',
          classes: 'console-key',
        ),
        button(
          'Copy terminal text',
          () => copy(terminal.getText()),
          small: true,
          variant: 'ghost',
          iconName: 'copy',
        ),
        button(
          'Select terminal text',
          () => showDialog<void>(
            'Terminal text',
            (close) => stack([
              codeText(terminal.getText(), label: 'CLI setup terminal text'),
              button('Close', () => close(null)),
            ]),
          ),
          small: true,
          variant: 'ghost',
          iconName: 'file-text',
        ),
        button(
          'Close terminal',
          stop,
          small: true,
          variant: 'danger',
          iconName: 'x',
        ),
      ],
      body: [
        meta(
          'Open the sign-in link shown below. Use the controls or click the terminal to type.',
          iconName: 'info',
          classes: 'setup-hint',
        ),
        for (final link in setupLinks(terminal.getText()))
          el('div', [
            icon('link', classes: 'icon--sm'),
            el('span', [
              text(link.toString()),
            ], classes: 'mono setup-link__url'),
            actions([
              button(
                'Open link · ${link.host}',
                () => openExternal(link.toString()),
                small: true,
                primary: true,
                iconName: 'external-link',
              ),
              button(
                'Copy link',
                () => copy(link.toString()),
                small: true,
                iconName: 'copy',
              ),
            ]),
          ], classes: 'setup-link'),
        TerminalView(terminal, height: 340),
        el('div', [
          actions([
            for (final key in {
              '↑ Up': '\x1b[A',
              '↓ Down': '\x1b[B',
              'Enter': '\r',
              'Tab': '\t',
              'Escape': '\x1b',
            }.entries)
              button(
                key.key,
                () => sendInput(key.value),
                disabled: !running,
                small: true,
                classes: 'console-key',
              ),
          ]),
          el('div', [
            field(
              'Authorization code or API key',
              paste,
              (value) => paste = value,
              password: true,
            ),
            button(
              'Send to terminal',
              () {
                sendInput('$paste\r');
                setState(() => paste = '');
              },
              primary: true,
              disabled: !running,
              iconName: 'send',
            ),
          ], classes: 'setup-send'),
        ], classes: 'setup-controls'),
      ],
      footer: [
        meta(
          'Send the hidden input only when asked. The CLI controls input echo. Output stays in memory and clears when closed. Closing the page stops setup; abandoned sessions expire after 10 minutes without activity.',
          iconName: 'lock',
        ),
      ],
      classes: 'setup-panel',
    );
  }

  @override
  Component build(BuildContext context) {
    final summary = integrations != null && (integrations!['total'] ?? 0) > 0
        ? integrationSummary(integrations!)
        : '';
    final connected =
        integrations != null &&
        integrations!['connected'] ==
            (integrations!['connected'] ?? 0) +
                (integrations!['outdated'] ?? 0) +
                (integrations!['missing'] ?? 0);
    return opsPage([
      pageHeader(
        'CLI accounts',
        subtitle:
            'Connect your coding agents to their providers. Choose an account to open its sign-in terminal.',
        actions: [
          button(
            'Refresh status',
            refresh,
            disabled: busy || refreshing,
            iconName: 'refresh-cw',
          ),
        ],
      ),
      if (summary.isNotEmpty)
        callout(
          [calloutText(summary)],
          tone: connected ? 'success' : 'warning',
          iconName: 'link',
        ),
      feedback(),
      if (!loaded && refreshing)
        meta('Loading CLI accounts…', iconName: 'hourglass'),
      if (loaded && clis.isEmpty)
        emptyState(
          'No CLI accounts are configured for this installation.',
          iconName: 'terminal',
        ),
      if (clis.isNotEmpty)
        el('div', [
          for (final cli in clis) account(cli),
        ], classes: 'provider-grid'),
      if (session != null) setupPanel(),
      const SshAccessCard(),
      const ToolchainCard(),
    ], classes: 'cli-page');
  }
}
