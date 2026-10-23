@TestOn('browser')
library;

import 'dart:js_interop';
import 'package:test/test.dart';
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/app.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'fixtures/dashboard_pages.dart';

Future<void> until(bool Function() ready) async {
  final end = DateTime.now().add(const Duration(seconds: 5));
  while (!ready()) {
    if (DateTime.now().isAfter(end))
      fail(
        'Route did not settle: ${web.document.querySelector('main')?.textContent}',
      );
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
}

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  late web.HTMLDivElement root;
  final assets = <web.Element>[];
  final originalHash = web.window.location.hash;
  final originalTheme = web.document.documentElement!.getAttribute(
    'data-theme',
  );
  late String? storedTheme;

  setUp(() => storedTheme = web.window.localStorage.getItem('herdr.theme'));

  setUpAll(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    root = web.HTMLDivElement()..id = 'dashboard-routes-test';
    root.style.cssText = 'position:absolute;left:0;top:0;width:2560px';
    web.document.body!.appendChild(root);
    for (final path in [
      'css/tokens.css',
      'styles.css',
      'css/components.css',
      'css/composites.css',
      'css/overview.css',
      'css/org-chart.css',
      'css/task-workflow.css',
      'css/builds.css',
      'css/organization.css',
      'css/pages.css',
      'vendor/xterm/xterm.css',
    ]) {
      final ready = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/$path';
      link.addEventListener('load', ((web.Event _) => ready.complete()).toJS);
      web.document.head!.appendChild(link);
      assets.add(link);
      await ready.future.timeout(const Duration(seconds: 5));
    }
    final ready = Completer<void>();
    final script = web.HTMLScriptElement()
      ..src = '../web/vendor/xterm/xterm.js';
    script.addEventListener('load', ((web.Event _) => ready.complete()).toJS);
    web.document.head!.appendChild(script);
    assets.add(script);
    await ready.future.timeout(const Duration(seconds: 5));
    binding = ClientAppBinding();
  });

  tearDown(() async {
    ui.closeDialogs();
    binding.detachRootComponent();
    await connection.disconnect();
    await BlocScope.reset();
    root.textContent = '';
    if (storedTheme == null) {
      web.window.localStorage.removeItem('herdr.theme');
    } else {
      web.window.localStorage.setItem('herdr.theme', storedTheme!);
    }
  });
  tearDownAll(() {
    for (final asset in assets) asset.remove();
    root.remove();
    web.window.history.replaceState(null, '', originalHash);
    if (originalTheme == null)
      web.document.documentElement!.removeAttribute('data-theme');
    else
      web.document.documentElement!.setAttribute('data-theme', originalTheme);
  });

  Future<void> mount(String route) async {
    web.window.history.replaceState(null, '', '#$route');
    connection = DashboardBloc(
      client: MockClient((request) async => dashboardPageResponse(request)),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    binding.attachRootComponent(
      const DashboardApp(),
      attachTo: '#dashboard-routes-test',
    );
    await until(
      () => connection.state.connected && root.querySelector('main h1') != null,
    );
  }

  for (final (route, title) in [
    ('/', 'Overview'),
    ('/agents', 'Agent activity'),
    ('/explorer', 'Project explorer'),
    ('/organization', 'Engineering'),
    ('/organization?view=team', 'Engineering'),
    ('/tasks', 'Tasks'),
    ('/tasks?view=list', 'Tasks'),
    ('/tasks/review-dashboard', 'Review dashboard keyboard behavior'),
    ('/builds', 'Builds'),
    ('/knowledge', 'Project knowledge'),
    ('/knowledge/engineering', 'Project knowledge'),
    ('/logs', 'Terminal logs'),
    ('/configuration', 'CLI accounts'),
    ('/agents/alice', 'Maya'),
    ('/groups/architecture', 'Architecture'),
  ]) {
    test(
      '$route uses the available canvas and exposes a page heading',
      () async {
        await mount(route);
        final main = root.querySelector('.main-content')! as web.HTMLElement;
        final frame = root.querySelector('.page-frame')! as web.HTMLElement;
        final style = web.window.getComputedStyle(main);
        final available =
            main.clientWidth -
            double.parse(style.paddingLeft.replaceFirst('px', '')) -
            double.parse(style.paddingRight.replaceFirst('px', ''));
        expect(frame.getBoundingClientRect().width, closeTo(available, 1));
        expect(frame.clientWidth, greaterThan(1440));
        await until(
          () => root.querySelector('main h1')!.textContent!.contains(title),
        );
        expect(root.querySelectorAll('main h1').length, 1);
      },
    );
  }

  test('theme radios use one tab stop and arrow/Home/End selection', () async {
    await mount('/explorer');
    final group = root.querySelector('[role=radiogroup]')! as web.HTMLElement;
    void key(String value) => group
        .querySelector('[tabindex="0"]')!
        .dispatchEvent(
          web.KeyboardEvent(
            'keydown',
            web.KeyboardEventInit(key: value, bubbles: true, cancelable: true),
          ),
        );
    key('End');
    await until(
      () =>
          group
              .querySelector('[aria-label="Dark theme"]')!
              .getAttribute('aria-checked') ==
          'true',
    );
    expect(group.querySelectorAll('[tabindex="0"]').length, 1);
    expect(
      web.document.activeElement,
      group.querySelector('[aria-label="Dark theme"]'),
    );
    key('ArrowRight');
    await until(
      () =>
          group
              .querySelector('[aria-label="Light theme"]')!
              .getAttribute('aria-checked') ==
          'true',
    );
    key('Home');
    expect(
      group
          .querySelector('[aria-label="Light theme"]')!
          .getAttribute('aria-checked'),
      'true',
    );
  });
}
