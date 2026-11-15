@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:juice/juice.dart' hide ValueNotifier;
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/terminal.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'package:herdr_jaspr_dashboard/pages/group_page.dart';
import 'package:herdr_jaspr_dashboard/pages/agent_console_page.dart';
import 'package:herdr_jaspr_dashboard/pages/snapshot_terminal.dart';

@JS('Terminal')
external JSFunction get xtermConstructor;

class _Host extends StatefulComponent {
  const _Host(this.child);
  final ValueNotifier<Component> child;
  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> {
  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    component.child.addListener(changed);
    ui.dialogChanges.addListener(changed);
  }

  @override
  void dispose() {
    component.child.removeListener(changed);
    ui.dialogChanges.removeListener(changed);
    super.dispose();
  }

  @override
  Component build(BuildContext context) =>
      dom.div([component.child.value, ui.buildDialogs()]);
}

Future<void> until(bool Function() ready, {String? reason}) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!ready() && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
  expect(
    ready(),
    true,
    reason: reason ?? 'DOM did not reach the expected rendered state.',
  );
}

Iterable<web.HTMLElement> nodes(String selector) sync* {
  final list = web.document.querySelectorAll('#group-terminal-test $selector');
  for (var i = 0; i < list.length; i++) {
    yield list.item(i) as web.HTMLElement;
  }
}

String contents(String selector) =>
    nodes(selector).map((node) => node.textContent).join('\n');
void reveal() {
  for (final node in nodes('details')) {
    (node as web.HTMLDetailsElement).open = true;
  }
}

final injectedAssets = <web.Element>[];

Future<void> script(String path) async {
  final done = Completer<void>();
  final element = web.HTMLScriptElement()..src = path;
  element.addEventListener('load', ((web.Event _) => done.complete()).toJS);
  element.addEventListener(
    'error',
    ((web.Event _) => done.completeError(
      StateError('Missing bundled xterm asset.'),
    )).toJS,
  );
  web.document.head!.appendChild(element);
  injectedAssets.add(element);
  await done.future.timeout(const Duration(seconds: 5));
}

Future<void> stylesheet(String path) async {
  final done = Completer<void>();
  final link = web.HTMLLinkElement()
    ..rel = 'stylesheet'
    ..href = '../web/$path';
  link.addEventListener('load', ((web.Event _) => done.complete()).toJS);
  link.addEventListener(
    'error',
    ((web.Event _) => done.completeError(
      StateError('Missing terminal stylesheet $path'),
    )).toJS,
  );
  web.document.head!.appendChild(link);
  injectedAssets.add(link);
  await done.future.timeout(const Duration(seconds: 5));
}

/// Exercise the native scroll event bridge without RAF scheduling guesses.
/// Xterm may ignore its initial scrollbar synchronization event; wait for each
/// actual DOM scroll event before retrying one pixel from the requested edge.
Future<void> scrollViewportTo(
  web.HTMLElement viewport,
  int target,
  bool Function() reflected,
) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!reflected() && DateTime.now().isBefore(deadline)) {
    final scrolled = Completer<void>();
    final listener = ((web.Event _) {
      if (!scrolled.isCompleted) scrolled.complete();
    }).toJS;
    viewport.addEventListener('scroll', listener);
    try {
      viewport.scrollTop = viewport.scrollTop == target
          ? (target == 0 ? 1 : target - 1)
          : target;
      await scrolled.future.timeout(deadline.difference(DateTime.now()));
      // Let xterm's DOM handler and the adapter's scheduled observation finish.
      await Future<void>.microtask(() {});
    } finally {
      viewport.removeEventListener('scroll', listener);
    }
  }
  expect(
    reflected(),
    isTrue,
    reason:
        'Native scroll did not reach xterm buffer; scrollTop=${viewport.scrollTop}, height=${viewport.clientHeight}, extent=${viewport.scrollHeight}',
  );
}

String? terminalLine(JSObject terminal, int index) => terminal
    .getProperty<JSObject>('buffer'.toJS)
    .getProperty<JSObject>('active'.toJS)
    .callMethod<JSObject?>('getLine'.toJS, index.toJS)
    ?.callMethod<JSString>('translateToString'.toJS, true.toJS)
    .toDart;

bool terminalContains(JSObject terminal, String text) {
  final active = terminal
      .getProperty<JSObject>('buffer'.toJS)
      .getProperty<JSObject>('active'.toJS);
  final length = active.getProperty<JSNumber>('length'.toJS).toDartInt;
  return [
    for (var line = 0; line < length; line++) line,
  ].any((line) => terminalLine(terminal, line) == text);
}

/// Capture a real emulator through its public open method. Its parser, buffer,
/// scrolling and renderer continue to run unchanged.
void captureTerminal(void Function(JSObject) capture) {
  final prototype = xtermConstructor.getProperty<JSObject>('prototype'.toJS);
  final open = prototype.getProperty<JSFunction>('open'.toJS);
  prototype.setProperty(
    'open'.toJS,
    ((JSObject terminal, web.HTMLElement host) {
      capture(terminal);
      open.callAsFunction(terminal, host);
    }).toJSCaptureThis,
  );
  addTearDown(() => prototype.setProperty('open'.toJS, open));
}

web.KeyboardEvent terminalKey(String key, int keyCode) => web.KeyboardEvent(
  'keydown',
  web.KeyboardEventInit(
    key: key,
    code: key,
    keyCode: keyCode,
    which: keyCode,
    bubbles: true,
    cancelable: true,
  ),
);

void main() {
  late ClientAppBinding binding;
  late ValueNotifier<Component> child;
  DashboardBloc? connection;
  setUpAll(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    web.document.body!.appendChild(
      web.HTMLDivElement()..id = 'group-terminal-test',
    );
    for (final path in [
      'css/tokens.css',
      'styles.css',
      'css/components.css',
      'css/pages.css',
      'vendor/xterm/xterm.css',
    ]) {
      await stylesheet(path);
    }
    await script('../web/vendor/xterm/xterm.js');
    binding = ClientAppBinding();
  });
  tearDown(() async {
    binding.detachRootComponent();
    // Jaspr 0.23.5 disposes synchronously; it deliberately retains detached
    // DOM nodes. Assert the lifecycle state, then clear the fixture below.
    expect(binding.rootElement, isNull);
    if (connection != null) {
      await connection!.disconnect();
      connection = null;
    }
    await BlocScope.reset();
    child.dispose();
    web.document.getElementById('group-terminal-test')!.textContent = '';
  });
  tearDownAll(() {
    for (final asset in injectedAssets) asset.remove();
    injectedAssets.clear();
    web.document.getElementById('group-terminal-test')?.remove();
  });
  void mount(Component component) {
    child = ValueNotifier(component);
    binding.attachRootComponent(_Host(child), attachTo: '#group-terminal-test');
  }

  test(
    'a snapshot inside nested closed disclosures renders on reveal and keeps updating',
    () async {
      Component panel(String text) => dom.details(
        attributes: {'data-inspection': text},
        [
          dom.summary([Component.text('Live discussion')]),
          dom.details([
            dom.summary([Component.text('Worker')]),
            SnapshotTerminal(text),
          ]),
        ],
      );
      mount(panel('\x1b[32mBefore reveal\x1b[0m\nsecond line'));
      await until(() => nodes('.terminal-host').isNotEmpty);
      expect(
        nodes('.xterm'),
        isEmpty,
        reason: 'Hidden hosts must not create zero-sized renderers.',
      );
      reveal();
      await until(() => contents('.xterm-rows').contains('Before reveal'));
      expect(
        nodes('.xterm-rows > div').first.getBoundingClientRect().height,
        greaterThan(0),
      );
      for (final node in nodes('details')) {
        (node as web.HTMLDetailsElement).open = false;
      }
      child.value = panel('Latest retained snapshot');
      await until(
        () =>
            nodes('details').first.getAttribute('data-inspection') ==
            'Latest retained snapshot',
        reason: 'The hidden replacement component has rendered',
      );
      reveal();
      await until(
        () => contents('.xterm-rows').contains('Latest retained snapshot'),
      );
      expect(contents('.xterm-rows'), isNot(contains('Before reveal')));
      expect(nodes('canvas'), isEmpty);
    },
  );

  test(
    'recycled TerminalView binds its replacement emulator and releases the old DOM',
    () async {
      final first = BrowserTerminal()..write('Old worker snapshot');
      final second = BrowserTerminal()..write('Replacement worker snapshot');
      mount(TerminalView(first, autofocus: false));
      await until(
        () => contents('.xterm-rows').contains('Old worker snapshot'),
      );
      final id = nodes('.terminal-host').single.id;
      child.value = TerminalView(second, autofocus: false);
      await until(
        () => contents('.xterm-rows').contains('Replacement worker snapshot'),
      );
      expect(nodes('.terminal-host').single.id, id);
      expect(nodes('.xterm'), hasLength(1));
      expect(contents('.xterm-rows'), isNot(contains('Old worker snapshot')));
      first.dispose();
      second.write('\r\nStill connected');
      await until(() => contents('.xterm-rows').contains('Still connected'));
      second.dispose();
    },
  );

  test(
    'queued complete snapshots replace older output without appending stale buffers',
    () async {
      final terminal = BrowserTerminal();
      mount(TerminalView(terminal, autofocus: false));
      await until(() => nodes('.xterm').isNotEmpty);
      terminal.replaceSnapshot('First inspection');
      terminal.replaceSnapshot('Second inspection');
      terminal.replaceSnapshot('Newest inspection');
      await until(() => terminal.getText().contains('Newest inspection'));
      expect(terminal.getText(), 'Newest inspection');
      expect(contents('.xterm-rows'), isNot(contains('First inspection')));
      expect(contents('.xterm-rows'), isNot(contains('Second inspection')));
      terminal.dispose();
    },
  );

  String output(int count, {int start = 0}) => [
    for (var line = start; line < count; line++) 'Retained record $line',
  ].join('\r\n');

  test(
    'queued snapshots preserve the reader viewport and follow only from the bottom',
    () async {
      final terminal = BrowserTerminal();
      addTearDown(terminal.dispose);
      mount(TerminalView(terminal, height: 180, autofocus: false));
      await until(() => nodes('.xterm').isNotEmpty);
      terminal.replaceSnapshot(output(100));
      await until(() => terminal.getText().endsWith('Retained record 99'));
      expect(terminal.lastViewportLine, greaterThan(20));
      expect(terminal.viewportLine, terminal.lastViewportLine);
      terminal.scrollToLine(18);
      expect(terminal.followingOutput, isFalse);
      terminal.replaceSnapshot(output(105));
      terminal.replaceSnapshot(output(110));
      terminal.replaceSnapshot(output(120));
      await until(() => terminal.getText().endsWith('Retained record 119'));
      expect(terminal.viewportLine, 18);
      expect(terminal.followingOutput, isFalse);
      await until(() => contents('.xterm-rows').contains('Retained record 18'));

      // A retained snapshot can drop its oldest output. The same content stays
      // visible rather than jumping down by the number of dropped lines.
      terminal.replaceSnapshot(output(125, start: 10));
      await until(() => terminal.getText().endsWith('Retained record 124'));
      expect(terminal.viewportLine, 8);
      await until(() => contents('.xterm-rows').contains('Retained record 18'));
      terminal.jumpToLatest();
      terminal.replaceSnapshot(output(130, start: 10));
      await until(() => terminal.getText().endsWith('Retained record 129'));
      expect(terminal.followingOutput, isTrue);
      expect(terminal.viewportLine, terminal.lastViewportLine);
      await until(
        () => contents('.xterm-rows').contains('Retained record 129'),
      );
    },
  );

  test('snapshot output offers Jump to latest after scrolling up', () async {
    JSObject? emulator;
    captureTerminal((terminal) => emulator = terminal);
    mount(SnapshotTerminal(output(100), height: 180));
    await until(
      () => contents('.xterm-rows').contains('Retained record 99'),
      reason: 'Initial output reaches bottom',
    );
    final host = nodes('.terminal-host').single;
    final viewport = nodes('.xterm-viewport').single;
    await until(
      () => viewport.scrollTop > 0,
      reason: 'Viewport scroll area synchronizes with initial buffer',
    );
    await scrollViewportTo(
      viewport,
      0,
      () =>
          emulator!
                  .getProperty<JSObject>('buffer'.toJS)
                  .getProperty<JSObject>('active'.toJS)
                  .getProperty<JSNumber>('viewportY'.toJS)
                  .toDartInt ==
              0 &&
          nodes('button.snapshot-terminal__follow').isNotEmpty,
    );
    expect(contents('.snapshot-terminal__follow'), 'Jump to latest');
    await until(() => contents('.xterm-rows').contains('Retained record 0'));
    child.value = SnapshotTerminal(output(110), height: 180);
    await until(
      () => terminalContains(emulator!, 'Retained record 109'),
      reason:
          'Replacement snapshot must finish parsing before checking the reader viewport',
    );
    expect(nodes('.terminal-host').single, same(host));
    expect(contents('.xterm-rows'), contains('Retained record 0'));
    expect(contents('.xterm-rows'), isNot(contains('Retained record 109')));
    nodes('button.snapshot-terminal__follow').single.click();
    await until(() => contents('.xterm-rows').contains('Retained record 109'));
    expect(web.document.activeElement, nodes('.xterm-helper-textarea').single);
    expect(nodes('.snapshot-terminal__follow'), isEmpty);
    child.value = SnapshotTerminal(output(120), height: 180);
    await until(() => contents('.xterm-rows').contains('Retained record 119'));
    expect(nodes('.snapshot-terminal__follow'), isEmpty);
  });

  test('repeated snapshot anchors restore the nearest matching run', () async {
    final terminal = BrowserTerminal();
    addTearDown(terminal.dispose);
    mount(TerminalView(terminal, height: 180, autofocus: false));
    await until(() => nodes('.xterm').isNotEmpty);
    String repeated(int count, List<int> starts) {
      final lines = [
        for (var line = 0; line < count; line++) 'Unique row $line',
      ];
      for (final start in starts) {
        for (var offset = 0; offset < 3; offset++) {
          lines[start + offset] = 'Repeated anchor $offset';
        }
      }
      return lines.join('\r\n');
    }

    terminal.replaceSnapshot(repeated(100, [18]));
    await until(() => terminal.getText().endsWith('Unique row 99'));
    terminal.scrollToLine(18);
    expect(terminal.followingOutput, isFalse);
    terminal.replaceSnapshot(repeated(120, [3, 24, 70]));
    await until(() => terminal.getText().endsWith('Unique row 119'));
    expect(
      terminal.viewportLine,
      24,
      reason: 'The first repeated run at line 3 is further from the reader',
    );
    expect(terminal.followingOutput, isFalse);
    terminal.replaceSnapshot(repeated(130, [18, 30]));
    await until(() => terminal.getText().endsWith('Unique row 129'));
    expect(
      terminal.viewportLine,
      18,
      reason: 'Equally close repeated runs prefer the lower line',
    );
  });

  test('reader scroll during a snapshot write survives its completion', () async {
    final prototype = xtermConstructor.getProperty<JSObject>('prototype'.toJS);
    final write = prototype.getProperty<JSFunction>('write'.toJS);
    JSFunction? heldCallback;
    var holdCallback = false;
    prototype.setProperty(
      'write'.toJS,
      ((JSObject emulator, JSString data, [JSFunction? callback]) {
        if (!holdCallback || callback == null) {
          write.callAsFunction(emulator, data, callback);
        } else {
          // The real parser still runs. Only its completion callback is gated so
          // user input can reliably land inside the replacement-write interval.
          write.callAsFunction(
            emulator,
            data,
            (() => heldCallback = callback).toJS,
          );
        }
      }).toJSCaptureThis,
    );
    addTearDown(() => prototype.setProperty('write'.toJS, write));
    final terminal = BrowserTerminal();
    addTearDown(terminal.dispose);
    mount(TerminalView(terminal, height: 180, autofocus: false));
    await until(() => nodes('.xterm').isNotEmpty);
    terminal.replaceSnapshot(output(100));
    await until(() => terminal.getText().endsWith('Retained record 99'));
    holdCallback = true;
    terminal.replaceSnapshot(output(120));
    await until(() => heldCallback != null);
    expect(terminal.getText(), endsWith('Retained record 119'));
    terminal.scrollToLine(10);
    expect(terminal.followingOutput, isFalse);
    final completed = Completer<void>();
    terminal.onChange = () => completed.complete();
    heldCallback!.callAsFunction(null);
    await completed.future.timeout(const Duration(seconds: 5));
    expect(terminal.viewportLine, 10);
    expect(terminal.followingOutput, isFalse);
    await until(() => contents('.xterm-rows').contains('Retained record 10'));

    // Explicitly resuming before completion overrides a reader position
    // captured earlier in this same in-flight write.
    terminal.onChange = null;
    terminal.jumpToLatest();
    heldCallback = null;
    terminal.replaceSnapshot(output(130));
    await until(() => heldCallback != null);
    terminal.scrollToLine(15);
    expect(terminal.followingOutput, isFalse);
    terminal.jumpToLatest();
    heldCallback!.callAsFunction(null);
    expect(terminal.followingOutput, isTrue);
    expect(terminal.viewportLine, terminal.lastViewportLine);
    await until(() => contents('.xterm-rows').contains('Retained record 129'));
  });

  test(
    'reader scroll before a queued snapshot parses survives its reset',
    () async {
      final prototype = xtermConstructor.getProperty<JSObject>(
        'prototype'.toJS,
      );
      final write = prototype.getProperty<JSFunction>('write'.toJS);
      (JSObject, JSString, JSFunction?)? queued;
      var holdWrite = false;
      prototype.setProperty(
        'write'.toJS,
        ((JSObject emulator, JSString data, [JSFunction? callback]) {
          if (holdWrite) {
            queued = (emulator, data, callback);
          } else {
            write.callAsFunction(emulator, data, callback);
          }
        }).toJSCaptureThis,
      );
      addTearDown(() => prototype.setProperty('write'.toJS, write));
      final terminal = BrowserTerminal();
      addTearDown(terminal.dispose);
      mount(TerminalView(terminal, height: 180, autofocus: false));
      await until(() => nodes('.xterm').isNotEmpty);
      terminal.replaceSnapshot(output(100));
      await until(() => terminal.getText().endsWith('Retained record 99'));
      holdWrite = true;
      terminal.replaceSnapshot(output(120));
      expect(queued, isNotNull);
      expect(
        terminal.getText(),
        endsWith('Retained record 99'),
        reason: 'The replacement parser has not started',
      );
      nodes('.xterm').single.dispatchEvent(terminalKey('ArrowUp', 38));
      terminal.scrollToLine(10);
      expect(terminal.followingOutput, isFalse);
      final completed = Completer<void>();
      terminal.onChange = () => completed.complete();
      final (emulator, data, callback) = queued!;
      write.callAsFunction(emulator, data, callback);
      await completed.future.timeout(const Duration(seconds: 5));
      expect(terminal.getText(), endsWith('Retained record 119'));
      expect(terminal.viewportLine, 10);
      expect(terminal.followingOutput, isFalse);
      await until(() => contents('.xterm-rows').contains('Retained record 10'));
    },
  );

  for (final pauseBeforeParser in [true, false]) {
    test(
      'scrolling up then back to bottom resumes following ${pauseBeforeParser ? 'before parser reset' : 'after parser completion'}',
      () async {
        final prototype = xtermConstructor.getProperty<JSObject>(
          'prototype'.toJS,
        );
        final write = prototype.getProperty<JSFunction>('write'.toJS);
        var hold = false;
        (JSObject, JSString, JSFunction?)? queued;
        JSFunction? completedParser;
        prototype.setProperty(
          'write'.toJS,
          ((JSObject emulator, JSString data, [JSFunction? callback]) {
            if (!hold || callback == null) {
              write.callAsFunction(emulator, data, callback);
            } else if (pauseBeforeParser) {
              queued = (emulator, data, callback);
            } else {
              write.callAsFunction(
                emulator,
                data,
                (() => completedParser = callback).toJS,
              );
            }
          }).toJSCaptureThis,
        );
        addTearDown(() => prototype.setProperty('write'.toJS, write));
        final terminal = BrowserTerminal();
        addTearDown(terminal.dispose);
        mount(TerminalView(terminal, height: 180, autofocus: false));
        await until(() => nodes('.xterm').isNotEmpty);
        final initial = Completer<void>();
        terminal.onChange = () {
          if (!initial.isCompleted) initial.complete();
        };
        terminal.replaceSnapshot(output(100));
        await initial.future.timeout(const Duration(seconds: 5));
        hold = true;
        terminal.replaceSnapshot(output(120));
        if (pauseBeforeParser) {
          expect(queued, isNotNull);
          expect(terminal.getText(), endsWith('Retained record 99'));
        } else {
          await until(() => completedParser != null);
          expect(terminal.getText(), endsWith('Retained record 119'));
        }
        terminal.scrollToLine(10);
        expect(terminal.followingOutput, isFalse);
        terminal.scrollToLine(terminal.lastViewportLine);
        expect(
          terminal.followingOutput,
          isTrue,
          reason:
              'Returning to the tail resumes following during the in-flight write',
        );
        final finished = Completer<void>();
        terminal.onChange = () {
          if (!finished.isCompleted) finished.complete();
        };
        hold = false;
        if (pauseBeforeParser) {
          final (emulator, data, callback) = queued!;
          write.callAsFunction(emulator, data, callback);
        } else {
          completedParser!.callAsFunction(null);
        }
        await finished.future.timeout(const Duration(seconds: 5));
        expect(terminal.viewportLine, terminal.lastViewportLine);
        expect(terminal.followingOutput, isTrue);
        await until(
          () => contents('.xterm-rows').contains('Retained record 119'),
        );
        final next = Completer<void>();
        terminal.onChange = () {
          if (!next.isCompleted) next.complete();
        };
        terminal.replaceSnapshot(output(130));
        await next.future.timeout(const Duration(seconds: 5));
        expect(terminal.followingOutput, isTrue);
        expect(terminal.viewportLine, terminal.lastViewportLine);
      },
    );
  }

  test(
    'native downward gesture plus public xterm scroll can resume an in-flight snapshot',
    () async {
      JSObject? captured;
      captureTerminal((emulator) => captured = emulator);
      final prototype = xtermConstructor.getProperty<JSObject>(
        'prototype'.toJS,
      );
      final write = prototype.getProperty<JSFunction>('write'.toJS);
      JSFunction? held;
      var gate = false;
      prototype.setProperty(
        'write'.toJS,
        ((JSObject emulator, JSString data, [JSFunction? callback]) {
          if (gate && callback != null) {
            write.callAsFunction(emulator, data, (() => held = callback).toJS);
          } else {
            write.callAsFunction(emulator, data, callback);
          }
        }).toJSCaptureThis,
      );
      addTearDown(() => prototype.setProperty('write'.toJS, write));
      final terminal = BrowserTerminal(readOnly: true);
      addTearDown(terminal.dispose);
      mount(TerminalView(terminal, height: 180, autofocus: false));
      await until(() => nodes('.xterm').isNotEmpty);
      final initial = Completer<void>();
      terminal.onChange = () {
        if (!initial.isCompleted) initial.complete();
      };
      terminal.replaceSnapshot(output(100));
      await initial.future.timeout(const Duration(seconds: 5));
      gate = true;
      terminal.replaceSnapshot(output(120));
      await until(() => held != null);
      final viewport = nodes('.xterm-viewport').single;
      Future<void> synchronizedScroll(int line, void Function() scroll) async {
        final synchronized = Completer<void>();
        double expectedTop() =>
            line *
            nodes('.xterm-rows > div').first.getBoundingClientRect().height;
        final nativeScroll = ((web.Event _) {
          if (!synchronized.isCompleted &&
              terminal.viewportLine == line &&
              expectedTop() > 0 &&
              (viewport.scrollTop - expectedTop()).abs() <= 1) {
            synchronized.complete();
          }
        }).toJS;
        // Public scrolling updates the buffer immediately, but xterm updates
        // its native scrollbar on a render frame and consumes that event to
        // clear its synchronization guard. A loose scrollTop < 500 check also
        // matches the stale reset position (zero), before either has happened.
        viewport.addEventListener('scroll', nativeScroll);
        try {
          scroll();
          await synchronized.future.timeout(
            const Duration(seconds: 5),
            onTimeout: () {
              fail(
                'Scroll did not synchronize: buffer=${terminal.viewportLine}, '
                'target=$line, scrollTop=${viewport.scrollTop}, expected=${expectedTop()}',
              );
            },
          );
          // The adapter observes the native event in a microtask after xterm.
          await Future<void>.microtask(() {});
        } finally {
          viewport.removeEventListener('scroll', nativeScroll);
        }
      }

      await synchronizedScroll(10, () {
        captured!.callMethod('scrollToLine'.toJS, 10.toJS);
        expect(
          terminal.followingOutput,
          isFalse,
          reason:
              'Public xterm onScroll observes upward navigation without adapter calls',
        );
      });
      await synchronizedScroll(terminal.lastViewportLine, () {
        // Script-created gestures exercise the listener path. Trusted wheel
        // input is covered separately by the external CDP interaction check.
        // Target the same tail in the DOM and buffer: a one-pixel wheel queues
        // an event near line 10 that can otherwise undo scrollToBottom before
        // the next render frame, depending on browser scheduling.
        nodes('.xterm').single.dispatchEvent(
          web.WheelEvent(
            'wheel',
            web.WheelEventInit(
              deltaY: viewport.scrollHeight.toDouble(),
              bubbles: true,
              cancelable: true,
            ),
          ),
        );
        expect(
          viewport.scrollTop,
          closeTo(viewport.scrollHeight - viewport.clientHeight, 1),
          reason:
              'The wheel gesture itself must move the native scrollbar to the tail',
        );
        captured!.callMethod('scrollToBottom'.toJS);
      });
      await until(
        () => terminal.followingOutput,
        reason:
            'Native viewport observation must recognize reader navigation back to the tail',
      );
      final finished = Completer<void>();
      terminal.onChange = () {
        if (!finished.isCompleted) finished.complete();
      };
      held!.callAsFunction(null);
      await finished.future.timeout(const Duration(seconds: 5));
      expect(terminal.followingOutput, isTrue);
      expect(terminal.viewportLine, terminal.lastViewportLine);
    },
  );

  for (final ignoredGesture in ['End', 'ArrowDown', 'scrollbar press']) {
    test(
      'ignored $ignoredGesture cannot resume following on a later parser tail event',
      () async {
        final prototype = xtermConstructor.getProperty<JSObject>(
          'prototype'.toJS,
        );
        final write = prototype.getProperty<JSFunction>('write'.toJS);
        JSFunction? held;
        var gate = false;
        prototype.setProperty(
          'write'.toJS,
          ((JSObject emulator, JSString data, [JSFunction? callback]) {
            if (gate && callback != null) {
              write.callAsFunction(
                emulator,
                data,
                (() => held = callback).toJS,
              );
            } else {
              write.callAsFunction(emulator, data, callback);
            }
          }).toJSCaptureThis,
        );
        addTearDown(() => prototype.setProperty('write'.toJS, write));
        final terminal = BrowserTerminal(readOnly: true);
        addTearDown(terminal.dispose);
        mount(TerminalView(terminal, height: 180, autofocus: false));
        await until(() => nodes('.xterm').isNotEmpty);
        final initial = Completer<void>();
        terminal.onChange = () {
          if (!initial.isCompleted) initial.complete();
        };
        terminal.replaceSnapshot(output(100));
        await initial.future.timeout(const Duration(seconds: 5));
        terminal.scrollToLine(10);
        final viewport = nodes('.xterm-viewport').single;
        await until(() => viewport.scrollTop > 0 && viewport.scrollTop < 500);
        final beforeTop = viewport.scrollTop;
        if (ignoredGesture == 'scrollbar press') {
          final bounds = viewport.getBoundingClientRect();
          viewport.dispatchEvent(
            web.PointerEvent(
              'pointerdown',
              web.PointerEventInit(
                pointerType: 'mouse',
                clientX: (bounds.right - 1).round(),
                clientY: (bounds.top + 10).round(),
                bubbles: true,
              ),
            ),
          );
        } else {
          nodes('.xterm-helper-textarea').single.dispatchEvent(
            terminalKey(ignoredGesture, ignoredGesture == 'End' ? 35 : 40),
          );
        }
        // Flush the event's post-dispatch observation rather than waiting an
        // arbitrary interval. The ignored gesture made no native viewport move.
        await Future<void>.microtask(() {});
        expect(viewport.scrollTop, beforeTop);
        expect(terminal.viewportLine, 10);
        final nativeScroll = Completer<void>();
        final listener = ((web.Event _) {
          if (!nativeScroll.isCompleted) nativeScroll.complete();
        }).toJS;
        viewport.addEventListener('scroll', listener);
        addTearDown(() => viewport.removeEventListener('scroll', listener));
        gate = true;
        terminal.replaceSnapshot(output(120));
        await until(() => held != null);
        await nativeScroll.future.timeout(const Duration(seconds: 5));
        await Future<void>.microtask(() {});
        expect(
          terminal.followingOutput,
          isFalse,
          reason:
              'A parser-induced tail event is not a continuation of the ignored gesture',
        );
        final completed = Completer<void>();
        terminal.onChange = () {
          if (!completed.isCompleted) completed.complete();
        };
        held!.callAsFunction(null);
        await completed.future.timeout(const Duration(seconds: 5));
        expect(terminal.viewportLine, 10);
        expect(terminal.followingOutput, isFalse);
        if (ignoredGesture == 'scrollbar press')
          web.window.dispatchEvent(web.Event('blur'));
      },
    );
  }

  test(
    'snapshot terminal leaves Tab and Escape available to its owner',
    () async {
      mount(const SnapshotTerminal('Read-only inspection'));
      await until(() => nodes('.xterm-helper-textarea').isNotEmpty);
      final textarea = nodes('.xterm-helper-textarea').single;
      final received = <String>[];
      final listener = ((web.KeyboardEvent event) {
        if (!event.defaultPrevented) received.add(event.key);
      }).toJS;
      final root = web.document.getElementById('group-terminal-test')!;
      root.addEventListener('keydown', listener);
      addTearDown(() => root.removeEventListener('keydown', listener));
      for (final (key, code) in [('Tab', 9), ('Escape', 27)]) {
        final event = terminalKey(key, code);
        textarea.dispatchEvent(event);
        expect(event.defaultPrevented, isFalse, reason: key);
      }
      expect(received, ['Tab', 'Escape']);

      // Positive control: the same real key codes are terminal input in a
      // live emulator, proving the snapshot handler changes their behavior.
      final live = BrowserTerminal();
      final inputs = <String>[];
      live.onOutput = inputs.add;
      addTearDown(live.dispose);
      child.value = TerminalView(live, autofocus: false);
      await until(
        () =>
            nodes('.xterm-helper-textarea').isNotEmpty &&
            nodes('.xterm-helper-textarea').single != textarea,
      );
      final liveTextarea = nodes('.xterm-helper-textarea').single;
      for (final (key, code) in [('Tab', 9), ('Escape', 27)]) {
        final event = terminalKey(key, code);
        liveTextarea.dispatchEvent(event);
        expect(event.defaultPrevented, isTrue, reason: 'Live terminal $key');
      }
      expect(inputs, ['\t', '\x1b']);
      expect(received, ['Tab', 'Escape']);
    },
  );

  test(
    'fill terminal defers its height to the owner and refits on resize',
    () async {
      JSObject? emulator;
      captureTerminal((terminal) => emulator = terminal);
      await web.document.fonts.ready.toDart;
      Component panel(int height) => dom.div(
        attributes: {
          'style':
              'display:flex;flex-direction:column;width:600px;height:${height}px',
        },
        [
          dom.div(
            attributes: {'style': 'height:48px;flex-shrink:0'},
            [Component.text('Terminal heading')],
          ),
          const SnapshotTerminal('CSS-owned fill output', fill: true),
        ],
      );
      mount(panel(280));
      await until(() => nodes('.terminal-host--fill').isNotEmpty);
      final host = nodes('.terminal-host--fill').single;
      expect(host.style.height, isEmpty);
      await until(() => nodes('.xterm').isNotEmpty);
      await until(
        () => contents('.xterm-rows').contains('CSS-owned fill output'),
      );
      bool fitted() {
        if (emulator == null || nodes('.xterm-rows > div').isEmpty)
          return false;
        final rowHeight = nodes(
          '.xterm-rows > div',
        ).first.getBoundingClientRect().height;
        final rows = emulator!.getProperty<JSNumber>('rows'.toJS).toDartInt;
        final available = host.clientHeight - 16;
        return rowHeight > 0 &&
            rows * rowHeight <= available + 1 &&
            rows * rowHeight > available - rowHeight - 1;
      }

      await until(
        fitted,
        reason:
            'Initial emulator rows fit the loaded font and available height',
      );
      final initialRows = emulator!
          .getProperty<JSNumber>('rows'.toJS)
          .toDartInt;
      expect(host.getBoundingClientRect().height, closeTo(280 - 48, 1));
      child.value = panel(420);
      await until(
        () =>
            fitted() &&
            emulator!.getProperty<JSNumber>('rows'.toJS).toDartInt >
                initialRows,
        reason:
            'Resized emulator must refit the loaded font to the taller host',
      );
      expect(nodes('.terminal-host').single, same(host));
      expect(host.style.height, isEmpty);
      expect(host.getBoundingClientRect().height, closeTo(420 - 48, 1));
    },
  );

  test(
    'keyed snapshot rows retain identities and live output after reordering',
    () async {
      Component streams(List<(String, String)> values) => dom.div([
        for (final value in values)
          dom.div(
            key: ValueKey(value.$1),
            attributes: {'data-agent': value.$1},
            [SnapshotTerminal(value.$2)],
          ),
      ]);
      mount(streams([('alice', 'Alice first'), ('bob', 'Bob first')]));
      await until(
        () =>
            contents(
              '[data-agent=alice] .xterm-rows',
            ).contains('Alice first') &&
            contents('[data-agent=bob] .xterm-rows').contains('Bob first'),
      );
      final aliceHost = nodes('[data-agent=alice] .terminal-host').single.id;
      final bobHost = nodes('[data-agent=bob] .terminal-host').single.id;
      child.value = streams([('bob', 'Bob latest'), ('alice', 'Alice latest')]);
      await until(
        () =>
            contents(
              '[data-agent=alice] .xterm-rows',
            ).contains('Alice latest') &&
            contents('[data-agent=bob] .xterm-rows').contains('Bob latest'),
      );
      expect(nodes('[data-agent=alice] .terminal-host').single.id, aliceHost);
      expect(nodes('[data-agent=bob] .terminal-host').single.id, bobHost);
      expect(
        contents('[data-agent=alice] .xterm-rows'),
        isNot(contains('Bob')),
      );
      expect(nodes('canvas'), isEmpty);
    },
  );

  Future<void> mountGroup(
    Future<http.Response> Function(http.Request) respond,
  ) async {
    connection = DashboardBloc(client: MockClient(respond));
    BlocScope.register<DashboardBloc>(() => connection!);
    connection!.connect('test');
    await until(() => connection!.state.connected);
    mount(const GroupPage(id: 'group'));
  }

  Map<String, dynamic> history() => {
    'organizations': [
      {'id': 'org', 'name': 'Team'},
    ],
    'group': {
      'id': 'group',
      'organization_id': 'org',
      'name': 'Review',
      'members': ['alice', 'bob'],
    },
    'groups': [
      {
        'id': 'group',
        'organization_id': 'org',
        'name': 'Review',
        'members': ['alice', 'bob'],
      },
    ],
    'profiles': [
      {'id': 'alice', 'organization_id': 'org', 'name': 'Alice'},
      {'id': 'bob', 'organization_id': 'org', 'name': 'Bob'},
    ],
    'jobs': [
      {
        'id': 'meeting',
        'group_id': 'group',
        'organization_id': 'org',
        'kind': 'discussion',
        'state': 'running',
      },
    ],
    'member_states': {},
  };
  http.Response ok(Object value) => http.Response(jsonEncode(value), 200);
  Map<String, dynamic> stream(
    String id,
    String output, {
    String status = 'working',
    String error = '',
  }) => {
    'profile_id': id,
    'name': id == 'alice' ? 'Alice' : 'Bob',
    'status': status,
    'error': error,
    'output': output,
  };

  test(
    'group inspections show output with empty error fields, keep disclosures through reorder and poll updates',
    () async {
      var streams = [
        stream('alice', 'Alice initial'),
        stream('bob', 'Bob initial'),
      ];
      final requests = <Map<String, dynamic>>[];
      await mountGroup((request) async {
        if (request.url.path == '/api/organizations/history')
          return ok(history());
        if (request.url.path == '/api/organizations/inspect') {
          requests.add(jsonDecode(request.body) as Map<String, dynamic>);
          return ok({
            'job_id': 'meeting',
            'streams': streams,
            'contributions': [],
            'artifact_draft': '',
          });
        }
        return ok({'workspaces': [], 'agents': []});
      });
      await until(() => nodes('.group-live-stream').length == 2);
      reveal();
      await until(
        () =>
            contents(
              '[data-profile-id=alice] .xterm-rows',
            ).contains('Alice initial') &&
            contents(
              '[data-profile-id=bob] .xterm-rows',
            ).contains('Bob initial'),
      );
      final aliceHost = nodes(
        '[data-profile-id=alice] .terminal-host',
      ).single.id;
      streams = [stream('bob', 'Bob polled'), stream('alice', 'Alice polled')];
      await until(
        () =>
            contents(
              '[data-profile-id=alice] .xterm-rows',
            ).contains('Alice polled') &&
            contents(
              '[data-profile-id=bob] .xterm-rows',
            ).contains('Bob polled'),
      );
      expect(
        nodes('[data-profile-id=alice] .terminal-host').single.id,
        aliceHost,
      );
      expect(
        (nodes('[data-profile-id=alice] details').single
                as web.HTMLDetailsElement)
            .open,
        true,
      );
      expect(requests, hasLength(greaterThanOrEqualTo(2)));
      expect(
        requests.every(
          (body) =>
              body['organization_id'] == 'org' &&
              body['job_id'] == 'meeting' &&
              '${body['request_id']}'.startsWith('group_stream_'),
        ),
        true,
      );
      expect(nodes('canvas'), isEmpty);
    },
  );

  test(
    'folder requests name each blocked participant outside closed terminals and clear on resume',
    () async {
      var blocked = true;
      await mountGroup((request) async {
        if (request.url.path == '/api/organizations/history')
          return ok(history());
        if (request.url.path == '/api/organizations/inspect') {
          return ok({
            'streams': [
              {
                ...stream(
                  'alice',
                  'Raw console',
                  status: blocked ? 'blocked' : 'working',
                ),
                'access_request': {
                  'source': 'visible_terminal',
                  'paths': [
                    '/home/herdr/.cache/*',
                    '/tmp/opencode/<literal>/*',
                  ],
                },
              },
              stream('bob', 'Working elsewhere'),
            ],
          });
        }
        return ok({'workspaces': [], 'agents': []});
      });
      await until(
        () => contents('.folder-access').contains('/home/herdr/.cache/*'),
      );
      final panel = nodes('.folder-access').single;
      expect(panel.closest('details'), isNull);
      expect(panel.textContent, contains('Alice · Requested folder access'));
      expect(
        contents('.folder-access code'),
        contains('/tmp/opencode/<literal>/*'),
      );
      expect(nodes('.folder-access literal'), isEmpty);
      expect(panel.getBoundingClientRect().height, greaterThan(0));
      blocked = false;
      await until(() => nodes('.folder-access').isEmpty);
    },
  );

  test(
    'agent console shows explicit folders and clears them after prompt resolution',
    () async {
      var blocked = true;
      var failed = false;
      connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/inspect') {
            if (failed) return http.Response('Inspection unavailable', 503);
            return ok({
              'status': blocked ? 'blocked' : 'working',
              'output': 'Raw console',
              'access_request': {
                'source': 'visible_terminal',
                'paths': ['/tmp/pinned/*'],
              },
            });
          }
          if (request.url.path.startsWith('/api/organizations'))
            return ok(history());
          return ok({'workspaces': [], 'agents': []});
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection!);
      connection!.connect('test');
      await until(() => connection!.state.connected);
      mount(const AgentConsolePage(id: 'alice'));
      await until(() => contents('.folder-access').contains('/tmp/pinned/*'));
      failed = true;
      await until(() => nodes('.folder-access').isEmpty);
      failed = false;
      await until(() => nodes('.folder-access').isNotEmpty);
      blocked = false;
      await until(() => nodes('.folder-access').isEmpty);
    },
  );

  test(
    'group terminals distinguish loading, starting, empty retained output and real errors',
    () async {
      final inspection = Completer<http.Response>();
      await mountGroup((request) async {
        if (request.url.path == '/api/organizations/history')
          return ok(history());
        if (request.url.path == '/api/organizations/inspect')
          return inspection.future;
        return ok({'workspaces': [], 'agents': []});
      });
      await until(
        () =>
            contents('[role=status]').contains('Loading live terminal output'),
      );
      inspection.complete(
        ok({
          'job_id': 'meeting',
          'streams': [
            stream('alice', '', status: 'starting'),
            stream('bob', '', status: 'idle'),
          ],
        }),
      );
      await until(
        () =>
            contents('[role=status]').contains('Session is starting') &&
            contents('[role=status]').contains('No retained terminal output'),
      );
      reveal();
      expect(contents('[role=alert]'), isEmpty);
    },
  );

  test(
    'group terminal errors remain explicit while available output is retained',
    () async {
      await mountGroup((request) async {
        if (request.url.path == '/api/organizations/history')
          return ok(history());
        if (request.url.path == '/api/organizations/inspect')
          return ok({
            'job_id': 'meeting',
            'streams': [
              stream(
                'alice',
                'Last retained evidence',
                status: 'unavailable',
                error: 'Agent binding changed.',
              ),
            ],
          });
        return ok({'workspaces': [], 'agents': []});
      });
      await until(
        () => contents('[role=alert]').contains('Agent binding changed.'),
      );
      reveal();
      await until(
        () => contents('.xterm-rows').contains('Last retained evidence'),
      );
    },
  );
}
