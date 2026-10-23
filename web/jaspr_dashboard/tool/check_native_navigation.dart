// Run from web/jaspr_dashboard: dart run tool/check_native_navigation.dart
// CDP sends trusted keyboard/pointer input: dispatchEvent cannot test the UA's
// native focus traversal or Escape/cancel behavior. No live gateway is used.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

class Chrome {
  Chrome(this.socket) {
    socket.listen((message) {
      final data = jsonDecode(message as String) as Map<String, dynamic>;
      final id = data['id'];
      if (id != null) {
        final call = pending.remove(id);
        if (data['error'] != null) {
          call?.completeError(StateError('${data['error']}'));
        } else {
          call?.complete(data['result'] as Map<String, dynamic>);
        }
      } else if (data['method'] == 'Runtime.exceptionThrown') {
        errors.add('${data['params']['exceptionDetails']}');
      }
    });
  }
  final WebSocket socket;
  final pending = <int, Completer<Map<String, dynamic>>>{};
  final errors = <String>[];
  int nextId = 0;
  String? session;

  Future<Map<String, dynamic>> send(
    String method, [
    Map<String, dynamic> params = const {},
  ]) {
    final id = ++nextId;
    final result = Completer<Map<String, dynamic>>();
    pending[id] = result;
    socket.add(
      jsonEncode({
        'id': id,
        'method': method,
        'params': params,
        if (session != null) 'sessionId': session,
      }),
    );
    return result.future.timeout(
      const Duration(seconds: 15),
      onTimeout: () {
        pending.remove(id);
        throw TimeoutException('Chrome did not answer $method');
      },
    );
  }

  Future<dynamic> evaluate(String expression) async {
    final result = await send('Runtime.evaluate', {
      'expression': expression,
      'returnByValue': true,
      'awaitPromise': true,
    });
    if (result['exceptionDetails'] != null)
      throw StateError('${result['exceptionDetails']}');
    return result['result']['value'];
  }

  Future<void> until(String expression, String reason) async {
    final end = DateTime.now().add(const Duration(seconds: 8));
    while (await evaluate(expression) != true) {
      if (DateTime.now().isAfter(end)) {
        final details = await evaluate('''({hash:location.hash,
          active:document.activeElement?.outerHTML,
          dialogs:[...document.querySelectorAll('dialog')].map(d=>({
            class:d.className,open:d.open,modal:d.matches(':modal')}))})''');
        throw StateError('$reason: ${jsonEncode(details)}');
      }
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
  }

  Future<void> check(String expression, String reason) async {
    await until(expression, reason);
    stdout.writeln('PASS $reason');
  }

  Future<void> key(String value, {bool shift = false}) async {
    final code = {'Tab': 9, 'Escape': 27, 'Enter': 13}[value] ?? 0;
    final params = {
      'key': value,
      'code': value,
      'windowsVirtualKeyCode': code,
      'nativeVirtualKeyCode': code,
      'modifiers': shift ? 8 : 0,
    };
    await send('Input.dispatchKeyEvent', {
      'type': value == 'Enter' ? 'keyDown' : 'rawKeyDown',
      ...params,
      if (value == 'Enter') 'text': '\r',
    });
    await send('Input.dispatchKeyEvent', {'type': 'keyUp', ...params});
  }

  Future<void> click(String selector) async {
    await until(
      '''(()=>{const n=document.querySelector(${jsonEncode(selector)});
      if(!n)return false;n.scrollIntoView({block:'nearest'});
      const r=n.getBoundingClientRect();return !!r.width&&!!r.height&&
        n.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));})()''',
      'Click target remained covered: $selector',
    );
    final rect =
        await evaluate(
              '''(()=>{const n=document.querySelector(${jsonEncode(selector)});
      if(!n)throw Error('Missing click target');n.scrollIntoView({block:'nearest'});
      const r=n.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,
        width:r.width,height:r.height};})()''',
            )
            as Map<String, dynamic>;
    if (rect['width'] == 0 || rect['height'] == 0)
      throw StateError('Invisible click target $selector');
    for (final type in ['mousePressed', 'mouseReleased']) {
      await send('Input.dispatchMouseEvent', {
        'type': type,
        'x': rect['x'],
        'y': rect['y'],
        'button': 'left',
        'clickCount': 1,
      });
    }
  }

  Future<void> resize(int width) => send('Emulation.setDeviceMetricsOverride', {
    'width': width,
    'height': 844,
    'deviceScaleFactor': 1,
    'mobile': false,
  });

  Future<void> wheel(int delta) async {
    final rect =
        await evaluate(
              '''(()=>{const r=document.querySelector('.xterm-viewport').getBoundingClientRect();
      return {x:r.left+r.width/2,y:r.top+r.height/2}})()''',
            )
            as Map<String, dynamic>;
    await send('Input.dispatchMouseEvent', {
      'type': 'mouseWheel',
      ...rect,
      'deltaX': 0,
      'deltaY': delta,
    });
  }

  Future<void> focusCycle(String selector) async {
    // Chrome may traverse browser chrome between the last and first controls;
    // BODY with no :focus represents that intermediate state, not background
    // document focus. The next trusted Tab must return to the same modal.
    var browserChrome = false;
    for (final reverse in [false, true]) {
      final visited = <int>{};
      for (var i = 0; i < 30; i++) {
        await evaluate('window.nativePreviousFocus=document.activeElement');
        await key('Tab', shift: reverse);
        final state =
            await evaluate(
                  '''(()=>{const d=document.querySelector(${jsonEncode(selector)}),
          a=document.activeElement;return {inside:!!d&&d.contains(a),
          chrome:a===document.body&&!a.matches(':focus'),
          index:d?[...d.querySelectorAll('*')].indexOf(a):-1,
          moved:a!==window.nativePreviousFocus};})()''',
                )
                as Map<String, dynamic>;
        if (state['inside'] != true &&
            (state['chrome'] != true || browserChrome))
          throw StateError('Focus escaped $selector: ${jsonEncode(state)}');
        if (state['inside'] == true && state['moved'] != true)
          throw StateError(
            'Tab did not advance focus in $selector: ${jsonEncode(state)}',
          );
        browserChrome = state['chrome'] == true;
        if (state['inside'] == true) visited.add(state['index'] as int);
      }
      if (browserChrome) {
        await key('Tab', shift: reverse);
        await until(
          'document.querySelector(${jsonEncode(selector)}).contains(document.activeElement)',
          'Focus did not return from browser chrome',
        );
        browserChrome = false;
      }
      if (visited.length < 2)
        throw StateError(
          'Trusted ${reverse ? 'Shift+Tab' : 'Tab'} did not move focus in $selector',
        );
    }
    stdout.writeln('PASS trusted Tab and Shift+Tab stay within $selector');
  }
}

Future<void> copyTree(Directory source, Directory target) async {
  await target.create(recursive: true);
  await for (final entry in source.list()) {
    final name = entry.uri.pathSegments.where((s) => s.isNotEmpty).last;
    if (entry is Directory) {
      await copyTree(entry, Directory.fromUri(target.uri.resolve('$name/')));
    } else if (entry is File) {
      await entry.copy(target.uri.resolve(name).toFilePath());
    }
  }
}

Future<void> main() async {
  final temp = await Directory.systemTemp.createTemp(
    'herdr-native-navigation-',
  );
  Process? process;
  HttpServer? server;
  Chrome? chrome;
  StreamSubscription? stderrEvents;
  try {
    await copyTree(Directory('web/css'), Directory('${temp.path}/css'));
    await copyTree(Directory('web/vendor'), Directory('${temp.path}/vendor'));
    await File('web/styles.css').copy('${temp.path}/styles.css');
    await File('web/icons.svg').copy('${temp.path}/icons.svg');
    await File('${temp.path}/index.html').writeAsString('''<!doctype html>
      <html lang="en" data-theme="dark"><head><meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1"><title>Native navigation checks</title>
      ${['css/tokens.css', 'styles.css', 'css/components.css', 'css/composites.css', 'css/overview.css', 'css/org-chart.css', 'css/task-workflow.css', 'css/builds.css', 'css/organization.css', 'css/pages.css', 'vendor/xterm/xterm.css'].map((p) => '<link rel="stylesheet" href="$p">').join()}
      <script src="vendor/xterm/xterm.js"></script></head><body>
      <script defer src="main.dart.js"></script></body></html>''');
    final compilation = await Process.run(Platform.resolvedExecutable, [
      'compile',
      'js',
      'test/fixtures/native_navigation_app.dart',
      '-O1',
      '-o',
      '${temp.path}/main.dart.js',
    ]);
    if (compilation.exitCode != 0)
      throw StateError(
        'Fixture compilation failed: ${compilation.stdout}\n${compilation.stderr}',
      );
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      final path = request.uri.path == '/'
          ? 'index.html'
          : request.uri.path.substring(1);
      final file = File.fromUri(temp.uri.resolve(path));
      if (!file.path.startsWith(temp.path + Platform.pathSeparator) ||
          !await file.exists()) {
        request.response.statusCode = 404;
      } else {
        request.response.headers.set(
          'Content-Type',
          path.endsWith('.js')
              ? 'text/javascript'
              : path.endsWith('.css')
              ? 'text/css'
              : path.endsWith('.svg')
              ? 'image/svg+xml'
              : 'text/html',
        );
        await request.response.addStream(file.openRead());
      }
      await request.response.close();
    });
    final endpoint = Completer<String>();
    final candidates = [
      if (Platform.environment['CHROME_EXECUTABLE']
          case final String executable)
        executable,
      if (Platform.isWindows)
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
      if (Platform.isMacOS)
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      'google-chrome',
      'chromium',
      'chromium-browser',
    ];
    for (final executable in candidates) {
      try {
        process = await Process.start(executable, [
          '--headless',
          '--disable-gpu',
          '--disable-background-networking',
          '--remote-debugging-port=0',
          '--user-data-dir=${temp.path}/profile',
          'about:blank',
        ]);
        break;
      } on ProcessException {
        /* Try the next installed Chrome name. */
      }
    }
    if (process == null)
      throw StateError('Chrome not found; set CHROME_EXECUTABLE');
    process.stdout.drain<void>();
    stderrEvents = process.stderr
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen((line) {
          final match = RegExp(
            r'DevTools listening on (ws://\S+)',
          ).firstMatch(line);
          if (match != null && !endpoint.isCompleted)
            endpoint.complete(match[1]!);
        });
    chrome = Chrome(
      await WebSocket.connect(
        await endpoint.future.timeout(const Duration(seconds: 15)),
      ),
    );
    final target = await chrome.send('Target.createTarget', {
      'url': 'about:blank',
    });
    final attached = await chrome.send('Target.attachToTarget', {
      'targetId': target['targetId'],
      'flatten': true,
    });
    chrome.session = attached['sessionId'] as String;
    await chrome.send('Page.enable');
    await chrome.send('Runtime.enable');
    await chrome.send('Page.bringToFront');
    await chrome.resize(768);
    final origin = 'http://127.0.0.1:${server.port}/';
    await chrome.send('Page.navigate', {'url': '$origin#/explorer'});
    await chrome.until(
      "!!document.querySelector('main h1')",
      'Dashboard did not mount',
    );
    await chrome.click('.drawer-toggle');
    await chrome.check(
      "document.querySelector('.sidebar').matches(':modal') && document.activeElement.matches('.sidebar__close')",
      'drawer opens modally with close control focused',
    );
    await chrome.check(
      "(()=>{const r=document.querySelector('.sidebar').getBoundingClientRect();return r.top===0&&Math.abs(r.bottom-innerHeight)<1&&getComputedStyle(document.querySelector('.main-content')).overflow==='hidden'})()",
      'drawer fills viewport height and locks workspace scrolling',
    );
    await chrome.evaluate(
      "document.querySelector('.nav-group__toggle').focus()",
    );
    await chrome.key('Enter');
    await chrome.check(
      "document.querySelector('.sidebar').matches(':modal') && document.querySelector('.nav-group__toggle').getAttribute('aria-expanded')==='false'",
      'keyboard disclosure activation keeps drawer open',
    );
    await chrome.focusCycle('.sidebar:modal');
    await chrome.key('Escape');
    await chrome.check(
      "!document.querySelector('.sidebar').open && document.activeElement.matches('.drawer-toggle')",
      'trusted Escape closes drawer and restores trigger',
    );
    await chrome.click('.drawer-toggle');
    await chrome.until(
      "document.querySelector('.sidebar').matches(':modal')",
      'Drawer did not reopen',
    );
    await chrome.resize(1440);
    await chrome.check(
      "document.querySelector('.sidebar').tagName==='ASIDE' && !document.querySelector('dialog:modal')",
      'desktop breakpoint releases drawer modality',
    );
    await chrome.send('Page.navigate', {'url': '$origin#/agents/alice'});
    await chrome.until(
      "!!document.querySelector('.console-shell .terminal-host .xterm')",
      'Console did not mount',
    );
    await chrome.check(
      "document.querySelector('.console-shell').tagName==='DIV' && document.querySelector('.console-shell').getAttribute('role')==='region'",
      'inline console has region semantics',
    );
    await chrome.evaluate(
      "window.originalTerminal=document.querySelector('.xterm')",
    );
    await chrome.wheel(-800);
    await chrome.until(
      "!!document.querySelector('.snapshot-terminal__follow')",
      'Scrolling up did not pause following',
    );
    final inspection =
        await chrome.evaluate('window.nativeInspectionRequests') as int;
    await chrome.until(
      'window.nativeInspectionRequests > $inspection',
      'No live refresh arrived while reading',
    );
    await chrome.check(
      "!!document.querySelector('.snapshot-terminal__follow')",
      'live refresh preserves paused reader position',
    );
    await chrome.wheel(16000);
    await chrome.check(
      "!document.querySelector('.snapshot-terminal__follow')",
      'trusted downward scrolling resumes following at the bottom',
    );
    await chrome.click('.console-shell [aria-pressed="false"]');
    await chrome.until(
      "!!document.querySelector('.console-modal:modal')",
      'Console did not expand',
    );
    await chrome.focusCycle('.console-modal:modal');
    await chrome.key('Escape');
    await chrome.check(
      "!document.querySelector('.console-modal:modal') && document.querySelector('.console-shell .xterm')===window.originalTerminal && document.activeElement.getAttribute('aria-pressed')==='false'",
      'console Escape restores inline terminal and invoking control',
    );
    await chrome.send('Page.navigate', {
      'url': '$origin#/organization?view=team',
    });
    await chrome.until(
      "[...document.querySelectorAll('main button')].some(n=>n.textContent.trim()==='Create organization')",
      'Create organization did not load',
    );
    await chrome.evaluate(
      "window.formOpener=[...document.querySelectorAll('main button')].find(n=>n.textContent.trim()==='Create organization');window.formOpener.id='native-create-organization'",
    );
    await chrome.click('#native-create-organization');
    await chrome.until(
      "!!document.querySelector('.dialog:modal')",
      'Shared form did not open',
    );
    await chrome.check(
      "getComputedStyle(document.querySelector('.main-content')).overflow==='hidden'",
      'shared modal locks the workspace scroller',
    );
    await chrome.focusCycle('.dialog:modal');
    await chrome.key('Escape');
    await chrome.check(
      "!document.querySelector('.dialog:modal') && document.activeElement===window.formOpener",
      'shared form native cancel restores its opener',
    );
    if (chrome.errors.isNotEmpty)
      throw StateError('Uncaught browser exceptions: ${chrome.errors}');
    stdout.writeln('All native navigation checks passed.');
  } finally {
    await chrome?.socket.close();
    process?.kill();
    if (process != null)
      await process.exitCode.timeout(
        const Duration(seconds: 5),
        onTimeout: () => -1,
      );
    await stderrEvents?.cancel();
    await server?.close(force: true);
    // The generated fixture, profile and compiled code live only in our newly
    // created temporary directory; no checkout or user browser profile is touched.
    try {
      final resolved = await temp.resolveSymbolicLinks();
      final parent = await Directory.systemTemp.resolveSymbolicLinks();
      if (!resolved.startsWith(parent + Platform.pathSeparator) ||
          !temp.uri.pathSegments
              .where((s) => s.isNotEmpty)
              .last
              .startsWith('herdr-native-navigation-'))
        throw StateError(
          'Refusing to remove an unexpected temporary path: $resolved',
        );
      await temp.delete(recursive: true);
    } on FileSystemException catch (error) {
      stderr.writeln(
        'Could not remove temporary browser fixture ${temp.path}: $error',
      );
    }
  }
}
