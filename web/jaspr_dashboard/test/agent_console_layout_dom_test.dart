@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/app.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;

String Function()? consoleDiagnostics;

Future<void> until(
  bool Function() condition, {
  String Function()? diagnostics,
}) async {
  for (var i = 0; i < 150; i++) {
    if (condition()) return;
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
  fail(
    'Console did not settle: ${(diagnostics ?? consoleDiagnostics)?.call()}',
  );
}

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  late web.HTMLDivElement root;
  var revision = 1;
  var inspectedStatus = 'done';
  var failInspection = false;
  var failInput = false;
  final inputs = <Map<String, dynamic>>[];
  final assets = <web.Element>[];
  final initialHash = web.window.location.hash;
  final initialSidebar = web.document.documentElement!.getAttribute(
    'data-sidebar',
  );

  web.HTMLElement node(String selector) =>
      root.querySelector(selector)! as web.HTMLElement;

  String measurements() => jsonEncode({
    'innerWidth': web.window.innerWidth,
    'documentWidth': web.document.documentElement!.clientWidth,
    'mainWidth': root.querySelector('.main-content')?.clientWidth,
    'mainScrollWidth': root.querySelector('.main-content')?.scrollWidth,
    'bodyOverflow': web.window.getComputedStyle(web.document.body!).overflow,
    'modalWidth': root
        .querySelector('.console-modal')
        ?.getBoundingClientRect()
        .width,
    'modalOpen':
        (root.querySelector('.console-modal') as web.HTMLDialogElement?)?.open,
    'expanded': root
        .querySelector('.console-shell')
        ?.getAttribute('data-expanded'),
    'active': (web.document.activeElement as web.HTMLElement?)?.id,
    'pageParent': root.querySelector('.console-page')?.parentElement?.className,
  });

  web.HTMLElement control(String label) {
    final nodes = root.querySelectorAll('button');
    return [
      for (var i = 0; i < nodes.length; i++) nodes.item(i) as web.HTMLElement,
    ].firstWhere((node) => node.textContent == label);
  }

  setUpAll(() async {
    consoleDiagnostics = measurements;
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    root = web.HTMLDivElement()..id = 'console-layout-test';
    root.style.cssText = 'position:absolute;left:0;top:0;width:2200px';
    web.document.body!.appendChild(root);
    for (final path in [
      'css/tokens.css',
      'styles.css',
      'css/components.css',
      'css/composites.css',
      'css/pages.css',
      'vendor/xterm/xterm.css',
    ]) {
      final loaded = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/$path';
      link.addEventListener('load', ((web.Event _) => loaded.complete()).toJS);
      web.document.head!.appendChild(link);
      assets.add(link);
      await loaded.future.timeout(const Duration(seconds: 5));
    }
    final loaded = Completer<void>();
    final script = web.HTMLScriptElement()
      ..src = '../web/vendor/xterm/xterm.js';
    script.addEventListener('load', ((web.Event _) => loaded.complete()).toJS);
    web.document.head!.appendChild(script);
    assets.add(script);
    await loaded.future.timeout(const Duration(seconds: 5));
    binding = ClientAppBinding();
  });

  tearDownAll(() {
    for (final asset in assets) asset.remove();
    root.remove();
    web.window.history.replaceState(null, '', initialHash);
    if (initialSidebar == null) {
      web.document.documentElement!.removeAttribute('data-sidebar');
    } else {
      web.document.documentElement!.setAttribute(
        'data-sidebar',
        initialSidebar,
      );
    }
  });

  setUp(() async {
    revision = 1;
    inspectedStatus = 'done';
    failInspection = false;
    failInput = false;
    inputs.clear();
    root.style.width = '2200px';
    ui.messages.value = null;
    web.window.history.replaceState(null, '', '#/agents/alex');
    final directory = {
      'organizations': [
        {'id': 'studio', 'name': 'Game Studio'},
      ],
      'profiles': [
        {
          'id': 'alex',
          'organization_id': 'studio',
          'name': 'Alex Chen',
          'role': 'Developer',
          'runtime': 'opencode',
          'provider': 'opencode-go',
          'model': 'deepseek-v4.1-flash',
          'project':
              '/home/herdr/projects/a-repository-with-a-long-project-name',
        },
      ],
      'groups': [],
      'jobs': [],
    };
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations/inspect' &&
            failInspection) {
          return http.Response(
            jsonEncode({'error': 'Console inspection failed'}),
            503,
          );
        }
        if (request.url.path == '/api/organizations/input') {
          inputs.add(Map<String, dynamic>.from(jsonDecode(request.body)));
          if (failInput) {
            return http.Response(
              jsonEncode({'error': 'Response delivery failed'}),
              503,
            );
          }
          return http.Response(jsonEncode({'id': 'input-job'}), 200);
        }
        final data = switch (request.url.path) {
          '/api/session' => {
            'authenticated': true,
            'role': 'admin',
            'operator': 'review',
          },
          '/api/snapshot' => {
            'workspaces': [],
            'agents': [],
            'herdr_server': 'running',
          },
          '/api/organizations/directory' ||
          '/api/organizations/state' => directory,
          '/api/organizations/inspect' => {
            'status': inspectedStatus,
            'console_token': 'reviewed-prompt',
            if (inspectedStatus == 'blocked')
              'access_request': {
                'source': 'visible_terminal',
                'paths': ['/tmp/requested-folder'],
              },
            'output':
                '${List.generate(100, (i) => 'Output line $i').join('\n')}\nRevision $revision complete',
            'scratch_directory':
                '/home/herdr/projects/.herdr-worktrees/long-session/.cache/herdr-scratch/session',
            'reply_draft': List.generate(
              200,
              (i) => 'Draft line $i',
            ).join('\n'),
          },
          '/api/integration' => {'alerts': []},
          _ => <String, dynamic>{},
        };
        return http.Response(jsonEncode(data), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    binding.attachRootComponent(
      const DashboardApp(),
      attachTo: '#console-layout-test',
    );
    await until(
      () =>
          root.querySelector('.console-facts') != null &&
          root.querySelector('.xterm-rows') != null,
    );
    await until(() => node('.terminal-host').clientHeight > 100);
  });

  tearDown(() async {
    // Native modals are outside ui.closeDialogs()' custom dialog registry.
    // Close them even when an assertion aborts while the console is expanded.
    final dialogs = root.querySelectorAll('dialog[open]');
    for (var index = 0; index < dialogs.length; index++) {
      (dialogs.item(index)! as web.HTMLDialogElement).close();
    }
    ui.closeDialogs();
    binding.detachRootComponent();
    await connection.disconnect();
    await BlocScope.reset();
    root.textContent = '';
    web.window.history.replaceState(null, '', initialHash);
  });

  test(
    'console fills available width and grows with the workspace height',
    () async {
      final main = node('.main-content');
      final style = web.window.getComputedStyle(main);
      final available =
          main.clientWidth -
          double.parse(style.paddingLeft.replaceFirst('px', '')) -
          double.parse(style.paddingRight.replaceFirst('px', ''));
      final frame = node('.page-frame');
      expect(frame.getBoundingClientRect().width, greaterThan(1440));
      expect(frame.getBoundingClientRect().width, closeTo(available, 1));
      expect(
        node('.console-output').getBoundingClientRect().width,
        closeTo(frame.getBoundingClientRect().width, 1),
      );
      node('.workspace').style.height = '700px';
      await until(() => node('.workspace').clientHeight == 700);
      final shorter = node('.terminal-host').clientHeight;
      node('.workspace').style.height = '1100px';
      await until(
        () => node('.terminal-host').clientHeight > shorter + 300,
        diagnostics: () =>
            'main=${main.clientHeight}, page=${node('.console-page').clientHeight}, frame=${frame.clientHeight}',
      );
      expect(main.scrollHeight, lessThanOrEqualTo(main.clientHeight + 1));
    },
  );

  test('narrow console wraps metadata without horizontal overflow', () async {
    final main = node('.main-content');
    final navigation =
        root.getBoundingClientRect().width - main.getBoundingClientRect().width;
    root.style.width = '${390 + navigation}px';
    await until(
      () => (main.getBoundingClientRect().width - 390).abs() < 1,
      diagnostics: measurements,
    );
    expect(
      main.getBoundingClientRect().width,
      closeTo(390, 1),
      reason: measurements(),
    );
    expect(main.scrollWidth, lessThanOrEqualTo(main.clientWidth + 1));
    expect(node('.terminal-host').clientWidth, greaterThan(200));
  });

  test(
    'full screen retains terminal through refresh and native cancel restores focus',
    () async {
      final terminal = node('.terminal-host');
      final emulator = node('.xterm');
      final page = node('.console-page');
      final expand = control('Full screen');
      final outside = node('.topbar .notice-button');
      outside.focus();
      expect(web.document.activeElement, outside);
      expect(node('.console-shell').tagName, 'DIV');
      expect(node('.console-shell').getAttribute('role'), 'region');
      expect(node('.console-page').parentElement, node('.console-shell'));
      expect((node('.console-modal') as web.HTMLDialogElement).open, isFalse);
      expect(
        web.window.getComputedStyle(node('.console-modal')).display,
        'none',
      );
      expand.click();
      await until(
        () => node('.console-shell').getAttribute('data-expanded') == 'true',
      );
      await until(
        () =>
            web.window.getComputedStyle(node('.main-content')).overflow ==
            'hidden',
        diagnostics: measurements,
      );
      final bounds = node('.console-modal').getBoundingClientRect();
      expect(
        bounds.width,
        closeTo(web.document.documentElement!.clientWidth, 1),
        reason: measurements(),
      );
      expect(
        bounds.height,
        closeTo(web.document.documentElement!.clientHeight, 1),
        reason: measurements(),
      );
      expect(node('.console-modal').matches(':modal'), isTrue);
      expect(node('.console-modal').tagName, 'DIALOG');
      expect(expand.getAttribute('aria-pressed'), 'true');
      expect(node('.console-page'), same(page));
      expect(page.parentElement, node('.console-modal'));
      outside.focus();
      expect(
        node('.console-modal').contains(web.document.activeElement),
        isTrue,
      );
      revision++;
      control('Refresh console').click();
      await until(
        () => node('.xterm-rows').textContent!.contains('Revision 2 complete'),
      );
      // A refresh and expansion must resize the existing emulator, not remount it.
      expect(node('.terminal-host'), same(terminal));
      expect(node('.xterm'), same(emulator));
      final input = node('.xterm-helper-textarea');
      input.focus();
      node(
        '.console-modal',
      ).dispatchEvent(web.Event('cancel', web.EventInit(cancelable: true)));
      await until(
        () => node('.console-shell').getAttribute('data-expanded') == 'false',
      );
      await until(() => web.document.activeElement == expand);
      expect(node('.terminal-host'), same(terminal));
      expect(control('Full screen').getAttribute('aria-pressed'), 'false');
      expect(node('.console-shell').hasAttribute('aria-modal'), isFalse);
      expect(node('.console-modal').matches(':modal'), isFalse);
      expect(node('.console-shell').getAttribute('role'), 'region');
      expect(node('.console-page'), same(page));
      expect(page.parentElement, node('.console-shell'));
      expect(node('.xterm'), same(emulator));
      outside.focus();
      expect(web.document.activeElement, outside);
      expect(
        web.window.getComputedStyle(node('.main-content')).overflow,
        'auto',
      );
    },
  );

  test('expanded long draft is focusable and scrollable', () async {
    control('Current reply draft').click();
    control('Full screen').click();
    await until(
      () => node('.console-shell').getAttribute('data-expanded') == 'true',
    );
    final draft = node('pre[aria-label="Current reply draft"]');
    expect(draft.getAttribute('tabindex'), '0');
    expect(draft.scrollHeight, greaterThan(draft.clientHeight));
    draft.focus();
    draft.scrollTop = 200;
    expect(draft.scrollTop, 200);
    draft.focus();
    node(
      '.console-modal',
    ).dispatchEvent(web.Event('cancel', web.EventInit(cancelable: true)));
    await until(
      () => node('.console-shell').getAttribute('data-expanded') == 'false',
    );
  });

  test(
    'external native close stays closed across an immediate parent refresh',
    () async {
      final terminal = node('.terminal-host');
      control('Full screen').click();
      await until(() => node('.console-modal').matches(':modal'));
      (node('.console-modal') as web.HTMLDialogElement).close();
      ui.messages.value = 'Background update after native close';
      control('Refresh console').click();
      await until(
        () => node('.console-shell').getAttribute('data-expanded') == 'false',
      );
      expect((node('.console-modal') as web.HTMLDialogElement).open, isFalse);
      expect(node('.console-modal').matches(':modal'), isFalse);
      expect(node('.terminal-host'), same(terminal));
      expect(node('.console-page').parentElement, node('.console-shell'));
    },
  );
  test(
    'full screen keeps folder prompts, response controls and errors reachable',
    () async {
      inspectedStatus = 'blocked';
      control('Refresh console').click();
      await until(() => root.querySelector('.folder-access') != null);
      final terminal = node('.terminal-host');
      control('Full screen').click();
      await until(() => node('.console-modal').matches(':modal'));
      final shell = node('.console-modal');
      expect(shell.contains(node('.folder-access')), isTrue);
      expect(
        shell.contains(control('Confirm selected Allow / Reject')),
        isTrue,
      );
      final confirm = control('Confirm selected Allow / Reject');
      confirm.focus();
      expect(web.document.activeElement, confirm);
      failInput = true;
      confirm.click();
      await until(() => root.textContent!.contains('Response delivery failed'));
      expect(inputs.single['key'], 'enter');
      expect(shell.contains(control('Recheck pending response')), isTrue);
      expect(node('.terminal-host'), same(terminal));
      expect(shell.matches(':modal'), isTrue);
      failInspection = true;
      control('Refresh console').click();
      await until(
        () => root.textContent!.contains('Console inspection failed'),
      );
      expect(shell.textContent, contains('Console inspection failed'));
      expect(
        shell.querySelector('[role=alert]')?.textContent,
        contains('Console inspection failed'),
      );
      expect(node('.terminal-host'), same(terminal));
      control('Exit full screen').click();
      await until(() => !shell.matches(':modal'));
    },
  );

  test('native cancel exits and unmount removes modal scroll lock', () async {
    final previousOverflow = web.window
        .getComputedStyle(web.document.body!)
        .overflow;
    control('Full screen').click();
    await until(() => node('.console-modal').matches(':modal'));
    node(
      '.console-modal',
    ).dispatchEvent(web.Event('cancel', web.EventInit(cancelable: true)));
    await until(() => !node('.console-modal').matches(':modal'));
    control('Full screen').click();
    await until(() => node('.console-modal').matches(':modal'));
    web.window.location.hash = '#/';
    await until(() => root.querySelector('.console-shell') == null);
    expect(web.document.querySelector('.console-modal:modal'), isNull);
    expect(
      web.window.getComputedStyle(web.document.body!).overflow,
      previousOverflow,
    );
  });
}
