import 'dart:async';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;

@JS('Terminal')
extension type _Xterm._(JSObject _) implements JSObject {
  external factory _Xterm(JSObject options);
  external void open(web.HTMLElement element);
  external void write(JSString data, [JSFunction? callback]);
  external void clear();
  external void reset();
  external void resize(JSNumber cols, JSNumber rows);
  external void refresh(JSNumber start, JSNumber end);
  external void focus();
  external void dispose();
  external JSObject onData(JSFunction listener);
  external JSObject onResize(JSFunction listener);
  external JSObject onScroll(JSFunction listener);
  external void scrollToLine(JSNumber line);
  external void scrollToBottom();
  external void attachCustomKeyEventHandler(JSFunction listener);
  external JSObject get buffer;
  external JSNumber get cols;
  external JSNumber get rows;
  external web.HTMLElement? get element;
}

/// Resolves a design token as the dark scheme renders it, so the terminal
/// matches the dark terminal panel in both themes. The fallback applies when
/// the token stylesheet is not loaded (unit tests).
String _darkToken(String name, String fallback) {
  final body = web.document.body;
  if (body == null) return fallback;
  final probe = web.document.createElement('span') as web.HTMLElement;
  probe.style
    ..setProperty('display', 'none')
    ..setProperty('color-scheme', 'dark')
    ..setProperty('color', 'var($name, $fallback)');
  body.append(probe);
  final value = web.window.getComputedStyle(probe).color;
  probe.remove();
  return value.isEmpty ? fallback : value;
}

/// The locally bundled xterm.js core renders terminal rows as DOM elements.
/// No canvas or WebGL addon is loaded. VT state, alternate buffers, selection,
/// paste and keyboard input are handled by the same terminal emulator.
class BrowserTerminal {
  BrowserTerminal({int maxLines = 2000, bool readOnly = false}) {
    _term = _Xterm(
      {
            'scrollback': maxLines,
            'cols': 80,
            'rows': 24,
            'fontFamily':
                '"JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace',
            'fontSize': 13,
            'lineHeight': 1.35,
            'cursorBlink': true,
            'screenReaderMode': true,
            'disableStdin': readOnly,
            'theme': {
              'background': _darkToken('--color-surface-sunken', '#101414'),
              'foreground': _darkToken('--color-text', '#dce6e2'),
              'cursor': _darkToken('--color-accent', '#dce6e2'),
            },
          }.jsify()
          as JSObject,
    );
    if (readOnly) {
      // Snapshot output has no remote stdin. Keep keyboard navigation available
      // to the surrounding page/fullscreen controls instead of consuming Tab
      // and Escape as terminal input.
      _term.attachCustomKeyEventHandler(
        ((web.KeyboardEvent event) =>
                event.key != 'Tab' && event.key != 'Escape')
            .toJS,
      );
    }
    _term.onData(((JSString value) => onOutput?.call(value.toDart)).toJS);
    _term.onResize(
      ((JSObject size) {
        onResize?.call(
          (size.getProperty<JSNumber>('cols'.toJS)).toDartInt,
          (size.getProperty<JSNumber>('rows'.toJS)).toDartInt,
        );
      }).toJS,
    );
    _term.onScroll(((JSNumber _) => _viewportChanged()).toJS);
  }
  late final _Xterm _term;
  void Function(String)? onOutput;
  void Function(int cols, int rows)? onResize;
  void Function()? onChange;
  void Function()? onViewportChange;
  bool _disposed = false;
  bool _snapshotWriting = false;
  String? _pendingSnapshot;
  ({int line, List<String?> anchor})? _snapshotReaderPosition;
  bool _followingOutput = true;
  web.HTMLElement? _viewport;
  web.HTMLElement? _navigationRoot;
  ({int serial, double top, int fromLine, bool towardTail})?
  _pendingReaderScroll;
  Timer? _readerScrollExpiry;
  int _readerGestureSerial = 0;
  bool _pointerNavigation = false, _pointerTouch = false;
  double _pointerY = 0;
  late final JSFunction _readerGesture = ((web.Event event) {
    // package:web event wrappers erase to JSObject; branch by event.type.
    if (event.type == 'keydown') {
      final key = event as web.KeyboardEvent;
      if (!const [
        'PageUp',
        'PageDown',
        'Home',
        'End',
        'ArrowUp',
        'ArrowDown',
        ' ',
      ].contains(key.key))
        return;
      _observeReaderGesture(
        const ['PageDown', 'End', 'ArrowDown'].contains(key.key) ||
            (key.key == ' ' && !key.shiftKey),
      );
    } else if (event.type == 'wheel') {
      final wheel = event as web.WheelEvent;
      if (wheel.deltaY != 0) _observeReaderGesture(wheel.deltaY > 0);
    } else if (event.type == 'pointerdown') {
      final pointer = event as web.PointerEvent;
      final bounds = _viewport?.getBoundingClientRect();
      // A held pointer alone is never evidence that the viewport moved.
      if (pointer.pointerType != 'touch' &&
          (bounds == null || pointer.clientX < bounds.right - 22))
        return;
      _pointerNavigation = true;
      _pointerTouch = pointer.pointerType == 'touch';
      _pointerY = pointer.clientY.toDouble();
      _observeReaderGesture(true);
    }
  }).toJS;
  late final JSFunction _readerPointerMove = ((web.Event event) {
    if (!_pointerNavigation) return;
    final pointer = event as web.PointerEvent;
    final delta = pointer.clientY - _pointerY;
    _pointerY = pointer.clientY.toDouble();
    if (delta != 0)
      _observeReaderGesture(_pointerTouch ? delta < 0 : delta > 0);
  }).toJS;
  late final JSFunction _endReaderGesture = ((web.Event _) {
    _pointerNavigation = false;
  }).toJS;
  late final JSFunction _cancelReaderGesture = ((web.Event _) {
    _pointerNavigation = false;
    _clearReaderGesture();
  }).toJS;
  late final JSFunction _viewportScroll = ((web.Event _) {
    // Native scrollbar scrolling suppresses xterm's public onScroll event.
    // Observe after its handler, correlating intent with a real DOM movement.
    scheduleMicrotask(() {
      if (_disposed) return;
      final pending = _pendingReaderScroll;
      final sameMovement =
          pending != null && (_viewport!.scrollTop - pending.top).abs() <= 1;
      // Xterm can ignore a queued scrollbar synchronization event before the
      // reader's event. Keep intent until the buffer actually reflects a move.
      final readerNavigation =
          sameMovement &&
          viewportLine != pending.fromLine &&
          (viewportLine < lastViewportLine || pending.towardTail);
      if (pending != null && (readerNavigation || !sameMovement)) {
        _clearReaderGesture();
      }
      _viewportChanged(readerNavigation: readerNavigation);
    });
  }).toJS;

  void _clearReaderGesture() {
    _readerGestureSerial++;
    _readerScrollExpiry?.cancel();
    _readerScrollExpiry = null;
    _pendingReaderScroll = null;
  }

  void _observeReaderGesture(bool towardTail) {
    final viewport = _viewport;
    if (_disposed || viewport == null) return;
    _clearReaderGesture();
    final serial = _readerGestureSerial;
    final beforeTop = viewport.scrollTop;
    final beforeLine = viewportLine;
    // Xterm changes scrollTop synchronously for ordinary wheel/key input.
    // Ignored keys and pointer presses without movement must leave no intent
    // for a later, unrelated snapshot parser event to consume.
    scheduleMicrotask(() {
      if (_disposed || serial != _readerGestureSerial || viewport != _viewport)
        return;
      if (viewportLine != beforeLine) {
        _viewportChanged(readerNavigation: true);
      } else if (viewport.scrollTop != beforeTop) {
        _pendingReaderScroll = (
          serial: serial,
          top: viewport.scrollTop,
          fromLine: beforeLine,
          towardTail: towardTail,
        );
        // A queued native scroll is normally delivered in the next frame. A
        // lost/suppressed event cannot leave navigation authority indefinitely.
        _readerScrollExpiry = Timer(const Duration(milliseconds: 250), () {
          if (_pendingReaderScroll?.serial == serial) _clearReaderGesture();
        });
      }
    });
  }

  bool get followingOutput => _followingOutput;
  JSObject get _active => _term.buffer.getProperty<JSObject>('active'.toJS);
  int get viewportLine =>
      _active.getProperty<JSNumber>('viewportY'.toJS).toDartInt;
  int get lastViewportLine =>
      _active.getProperty<JSNumber>('baseY'.toJS).toDartInt;
  int get viewWidth => _term.cols.toDartInt;
  int get viewHeight => _term.rows.toDartInt;

  /// Append interactive output deltas, retaining xterm's native scroll rules.
  /// Complete polled inspections use [replaceSnapshot] and restore the reader.
  void write(String value) {
    if (_disposed) return;
    _term.write(
      value.toJS,
      (() {
        if (_disposed) return;
        _observeViewport();
        onChange?.call();
      }).toJS,
    );
  }

  void clear() {
    if (!_disposed) _term.reset();
  }

  void _viewportChanged({bool readerNavigation = false}) {
    if (_disposed) return;
    final following = viewportLine >= lastViewportLine;
    if (_snapshotWriting) {
      // RIS resets to an empty tail and parser output follows that tail. A
      // viewport below it is reader navigation, even during a long write.
      // Retain that newer intent instead of restoring the pre-write position.
      if (following && !readerNavigation) return;
      // Reaching the tail by reader navigation resumes following, while an
      // automatic parser reset/tail event must retain the reader's anchor.
      _snapshotReaderPosition = following ? null : _capturePosition();
    }
    if (following == _followingOutput) return;
    _followingOutput = following;
    onViewportChange?.call();
  }

  void _observeViewport() {
    final viewport =
        _term.element?.querySelector('.xterm-viewport') as web.HTMLElement?;
    if (viewport == _viewport) return;
    _clearReaderGesture();
    _pointerNavigation = false;
    _viewport?.removeEventListener('scroll', _viewportScroll);
    _viewport = viewport;
    viewport?.addEventListener('scroll', _viewportScroll);
    final root = _term.element;
    if (root != _navigationRoot) {
      _detachNavigation();
      _navigationRoot = root;
      for (final type in ['wheel', 'keydown', 'pointerdown']) {
        root?.addEventListener(type, _readerGesture, true.toJS);
      }
      if (root != null) {
        web.window.addEventListener('pointermove', _readerPointerMove);
        web.window.addEventListener('pointerup', _endReaderGesture);
        web.window.addEventListener('pointercancel', _cancelReaderGesture);
        web.window.addEventListener('lostpointercapture', _cancelReaderGesture);
        web.window.addEventListener('blur', _cancelReaderGesture);
      }
    }
  }

  void _detachNavigation() {
    for (final type in ['wheel', 'keydown', 'pointerdown']) {
      _navigationRoot?.removeEventListener(type, _readerGesture, true.toJS);
    }
    web.window.removeEventListener('pointermove', _readerPointerMove);
    web.window.removeEventListener('pointerup', _endReaderGesture);
    web.window.removeEventListener('pointercancel', _cancelReaderGesture);
    web.window.removeEventListener('lostpointercapture', _cancelReaderGesture);
    web.window.removeEventListener('blur', _cancelReaderGesture);
    _navigationRoot = null;
    _clearReaderGesture();
    _pointerNavigation = false;
  }

  /// Moving back to the bottom resumes following subsequent inspections.
  void jumpToLatest() {
    if (_disposed) return;
    _clearReaderGesture();
    _followingOutput = true;
    _snapshotReaderPosition = null;
    _term.scrollToBottom();
    onViewportChange?.call();
  }

  void scrollToLine(int line) {
    if (_disposed) return;
    _clearReaderGesture();
    _term.scrollToLine(line.clamp(0, lastViewportLine).toJS);
    // Explicit navigation is authoritative even when xterm emits no event
    // because the requested line was already its temporary parser tail.
    _viewportChanged(readerNavigation: true);
  }

  String? _lineText(JSObject active, int index) => active
      .callMethod<JSObject?>('getLine'.toJS, index.toJS)
      ?.callMethod<JSString>('translateToString'.toJS, true.toJS)
      .toDart;

  ({int line, List<String?> anchor}) _capturePosition() => (
    line: viewportLine,
    anchor: [
      for (var offset = 0; offset < 3; offset++)
        _lineText(_active, viewportLine + offset),
    ],
  );

  int _matchingPosition(({int line, List<String?> anchor}) position) {
    final fallback = position.line.clamp(0, lastViewportLine);
    if (!position.anchor.any((line) => line?.trim().isNotEmpty ?? false)) {
      return fallback;
    }
    final active = _active;
    final decoded = <int, String?>{};
    String? lineAt(int line) =>
        decoded.putIfAbsent(line, () => _lineText(active, line));
    bool matches(int line) =>
        line >= 0 &&
        line <= lastViewportLine &&
        const [
          0,
          1,
          2,
        ].every((offset) => lineAt(line + offset) == position.anchor[offset]);
    // Most polls keep the same prefix: check the current position first, then
    // search outwards. Stop at the nearest match (lower line wins a tie), and
    // decode each candidate line at most once if retained output has shifted.
    for (var distance = 0; distance <= lastViewportLine; distance++) {
      final before = fallback - distance;
      if (matches(before)) return before;
      final after = fallback + distance;
      if (distance > 0 && matches(after)) return after;
    }
    return fallback;
  }

  /// Serialize complete inspections, including their viewport restoration.
  /// Capturing every viewport before queuing RIS would instead capture an
  /// intermediate reset position for later polls. Pending polls coalesce to the
  /// newest snapshot while a write is in progress.
  void replaceSnapshot(String value) {
    if (_disposed) return;
    _pendingSnapshot = value;
    if (!_snapshotWriting) _writeSnapshot();
  }

  void _writeSnapshot() {
    if (_disposed || _pendingSnapshot == null) return;
    final value = _pendingSnapshot!;
    _pendingSnapshot = null;
    final previous = _capturePosition();
    _snapshotReaderPosition = null;
    _snapshotWriting = true;
    // RIS must travel through the same parser queue as replacement output.
    _term.write(
      ('\x1bc$value').toJS,
      (() {
        if (_disposed) return;
        _observeViewport();
        final reader = _snapshotReaderPosition;
        _snapshotReaderPosition = null;
        _snapshotWriting = false;
        if (_followingOutput && reader == null) {
          _term.scrollToBottom();
        } else {
          _term.scrollToLine(_matchingPosition(reader ?? previous).toJS);
        }
        _viewportChanged();
        onChange?.call();
        if (_pendingSnapshot != null) _writeSnapshot();
      }).toJS,
    );
  }

  void focus() {
    if (!_disposed) _term.focus();
  }

  void resize(int cols, int rows) {
    if (!_disposed)
      _term.resize(cols.clamp(10, 400).toJS, rows.clamp(5, 150).toJS);
  }

  String getText() {
    if (_disposed) return '';
    final active = _term.buffer.getProperty<JSObject>('active'.toJS);
    final length = active.getProperty<JSNumber>('length'.toJS).toDartInt;
    final lines = <String>[];
    for (var i = 0; i < length; i++) {
      final line = active.callMethod<JSObject?>('getLine'.toJS, i.toJS);
      if (line == null) continue;
      final value = line
          .callMethod<JSString>('translateToString'.toJS, true.toJS)
          .toDart;
      final wrapped = line.getProperty<JSBoolean>('isWrapped'.toJS).toDart;
      if (wrapped && lines.isNotEmpty) {
        lines[lines.length - 1] += value;
      } else {
        lines.add(value);
      }
    }
    return lines.join('\n').trimRight();
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    onOutput = null;
    onResize = null;
    onChange = null;
    onViewportChange = null;
    _pendingSnapshot = null;
    _viewport?.removeEventListener('scroll', _viewportScroll);
    _viewport = null;
    _detachNavigation();
    _term.dispose();
  }
}

class TerminalView extends StatefulComponent {
  const TerminalView(
    this.terminal, {
    super.key,
    this.height = 400,
    this.fill = false,
    this.autofocus = true,
    this.label = 'Interactive terminal',
  });
  final BrowserTerminal terminal;
  final int height;
  final bool fill;
  final bool autofocus;
  final String label;
  @override
  State<TerminalView> createState() => _TerminalViewState();
}

class _TerminalViewState extends State<TerminalView> {
  static int nextId = 0;
  late final String id = 'dom-terminal-${nextId++}';
  web.ResizeObserver? observer;
  Timer? mounting;
  BrowserTerminal? attached;
  @override
  void initState() {
    super.initState();
    // The host must exist before observing it. Closed disclosures deliberately
    // keep it at zero size, so opening xterm waits until a visible layout exists.
    mounting = Timer.periodic(const Duration(milliseconds: 20), (_) {
      final host = web.document.getElementById(id) as web.HTMLElement?;
      if (!mounted || host == null) return;
      mounting?.cancel();
      observer = web.ResizeObserver(
        ((JSArray<web.ResizeObserverEntry> _, web.ResizeObserver __) =>
                attachAndFit(host))
            .toJS,
      );
      observer!.observe(host);
      attachAndFit(host);
    });
  }

  @override
  void didUpdateComponent(TerminalView old) {
    super.didUpdateComponent(old);
    if (old.terminal != component.terminal ||
        old.height != component.height ||
        old.fill != component.fill) {
      // A stream can replace its controller while Jaspr retains the host state.
      // Rebind after its new attributes and owner have finished rendering.
      Timer.run(() {
        if (!mounted) return;
        final host = web.document.getElementById(id) as web.HTMLElement?;
        if (host != null) attachAndFit(host);
      });
    }
  }

  void attachAndFit(web.HTMLElement host) {
    if (!mounted || component.terminal._disposed) return;
    final terminal = component.terminal;
    if (attached != terminal) {
      host.textContent = '';
      attached = null;
    }
    if (host.clientWidth <= 0 || host.clientHeight <= 0) return;
    if (attached == null) {
      final existing = terminal._term.element;
      if (existing == null) {
        terminal._term.open(host);
      } else {
        // A reopened CLI setup retains its emulator, selection and listeners.
        host.appendChild(existing);
      }
      attached = terminal;
      if (component.autofocus) terminal.focus();
    }
    // Recheck after a reveal/rebind/resize in case the emulator replaces its
    // viewport element. The observer moves its listener without accumulating it.
    terminal._observeViewport();
    fit(host);
    // Revealing a disclosure may leave unchanged row/column counts. Explicit
    // refresh still paints snapshots written while its panel was hidden.
    terminal._term.refresh(0.toJS, (terminal.viewHeight - 1).toJS);
  }

  void fit(web.HTMLElement host) {
    final row = host.querySelector('.xterm-rows > div') as web.HTMLElement?;
    final sample =
        host.querySelector('.xterm-char-measure-element') as web.HTMLElement?;
    // xterm measures a run of repeated characters; one cell is that width
    // divided by the run length.
    final run = (sample?.textContent ?? '').length;
    final width =
        (sample?.getBoundingClientRect().width ?? 0) / (run > 0 ? run : 1);
    final height = row?.getBoundingClientRect().height ?? 0;
    if (width <= 0 || height <= 0 || host.clientWidth <= 0) return;
    component.terminal.resize(
      ((host.clientWidth - 22) / width).floor(),
      ((host.clientHeight - 16) / height).floor(),
    );
  }

  @override
  void dispose() {
    mounting?.cancel();
    observer?.disconnect();
    super.dispose();
  }

  @override
  Component build(BuildContext context) => dom.div(
    id: id,
    classes: 'terminal-host${component.fill ? ' terminal-host--fill' : ''}',
    attributes: {
      'style':
          '${component.fill ? '' : 'height:${component.height}px;'}'
          'padding:8px;overflow:hidden',
      'aria-label': component.label,
    },
    [],
  );
}
