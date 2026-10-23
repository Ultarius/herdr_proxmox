import '../ui.dart';
import '../terminal.dart';

/// A terminal inspection is a complete retained snapshot, not a websocket
/// output delta. Replace its DOM buffer only when the snapshot changes.
class SnapshotTerminal extends StatefulComponent {
  const SnapshotTerminal(
    this.output, {
    super.key,
    this.height = 360,
    this.fill = false,
  });
  final String output;
  final int height;
  final bool fill;
  @override
  State<SnapshotTerminal> createState() => _SnapshotTerminalState();
}

class _SnapshotTerminalState extends State<SnapshotTerminal> {
  late final BrowserTerminal terminal;
  @override
  void initState() {
    super.initState();
    terminal = BrowserTerminal(readOnly: true);
    terminal.onViewportChange = () {
      if (mounted) setState(() {});
    };
    update();
  }

  void update() {
    terminal.replaceSnapshot(
      component.output.replaceAll(RegExp(r'(?<!\r)\n'), '\r\n'),
    );
  }

  @override
  void didUpdateComponent(SnapshotTerminal old) {
    super.didUpdateComponent(old);
    if (old.output != component.output) update();
  }

  @override
  void dispose() {
    terminal.dispose();
    super.dispose();
  }

  @override
  Component build(BuildContext context) => el(
    'div',
    [
      TerminalView(
        terminal,
        height: component.height,
        fill: component.fill,
        autofocus: false,
        label: 'Retained terminal output',
      ),
      if (!terminal.followingOutput)
        button(
          'Jump to latest',
          () {
            terminal.jumpToLatest();
            terminal.focus();
          },
          small: true,
          classes: 'snapshot-terminal__follow',
        ),
    ],
    classes:
        'snapshot-terminal${component.fill ? ' snapshot-terminal--fill' : ''}',
  );
}
