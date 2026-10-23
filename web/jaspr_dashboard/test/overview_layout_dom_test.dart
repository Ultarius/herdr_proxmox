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

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 80));

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  late web.HTMLDivElement root;
  late List<Map<String, dynamic>> agents;

  web.HTMLElement node(String selector) =>
      root.querySelector(selector)! as web.HTMLElement;

  Future<void> toggle(String selector) async {
    (node(selector).querySelector('summary')! as web.HTMLElement).click();
    await settle();
  }

  setUpAll(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    root = web.HTMLDivElement()..id = 'overview-layout-test';
    // Exercise real container sizes independently of the test runner's window.
    // Only the fixture width changes; all application layout rules are shipped CSS.
    root.style.position = 'absolute';
    root.style.left = '0';
    root.style.top = '0';
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
    ]) {
      final loaded = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/$path';
      link.addEventListener('load', ((web.Event _) => loaded.complete()).toJS);
      link.addEventListener(
        'error',
        ((web.Event _) => loaded.completeError(
          StateError('Cannot load $path'),
        )).toJS,
      );
      web.document.head!.appendChild(link);
      await loaded.future.timeout(const Duration(seconds: 5));
    }
    binding = ClientAppBinding();
  });

  setUp(() {
    agents = [
      {'name': 'Review agent', 'agent_status': 'idle'},
    ];
    ui.messages.value = null;
    root.style.width = '2200px';
  });

  tearDown(() async {
    ui.closeDialogs();
    binding.detachRootComponent();
    await connection.disconnect();
    await BlocScope.reset();
    root.textContent = '';
    web.window.history.replaceState(null, '', '#/');
  });

  Future<void> mount({String route = '/'}) async {
    web.window.history.replaceState(null, '', '#$route');
    connection = DashboardBloc(
      client: MockClient((request) async {
        final data = switch (request.url.path) {
          '/api/session' => {
            'authenticated': true,
            'role': 'admin',
            'operator': 'layout-reviewer',
          },
          '/api/snapshot' => {
            'workspaces': [
              {
                'workspace_id': 'workspace-1',
                'label': 'Repository workspace',
                'cwd': '/home/herdr/projects/repository',
              },
            ],
            'agents': agents,
            'herdr_server': 'running',
          },
          '/api/resources' => {
            'available': true,
            'sampled_at': 1792742400,
            'cpu_percent': 12,
            'cpu_cores': 4,
            'memory_total': 8589934592,
            'memory_used': 1073741824,
            'swap_total': 0,
            'swap_used': 0,
            'processes': [
              {
                'name':
                    'A long process name to exercise narrow resource tables',
                'pid': 1234,
                'memory_bytes': 104857600,
                'cpu_percent': 4,
              },
            ],
          },
          '/api/organizations/directory' => {
            'organizations': [],
            'profiles': [],
            'groups': [],
          },
          '/api/projects/connections' => {'connections': [], 'bindings': []},
          '/api/projects/jobs' => {'jobs': []},
          '/api/storage' => {'inventory': {}, 'policy': {}},
          '/api/integration' => {'alerts': []},
          _ => <String, dynamic>{},
        };
        return http.Response(jsonEncode(data), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    binding.attachRootComponent(
      const DashboardApp(),
      attachTo: '#overview-layout-test',
    );
    await settle();
  }

  test(
    'wide overview fills available width and its columns stack independently',
    () async {
      await mount();
      final main = node('.main-content');
      final style = web.window.getComputedStyle(main);
      final available =
          main.clientWidth -
          double.parse(style.paddingLeft.replaceFirst('px', '')) -
          double.parse(style.paddingRight.replaceFirst('px', ''));
      final frame = node('.page-frame').getBoundingClientRect();
      expect(frame.width, greaterThan(1440));
      expect(frame.width, closeTo(available, 1));

      final primary = node('.overview-lane--primary').getBoundingClientRect();
      final secondary = node(
        '.overview-lane--secondary',
      ).getBoundingClientRect();
      expect(primary.right, lessThan(secondary.left));
      expect(primary.top, closeTo(secondary.top, 1));
      expect(primary.width, greaterThan(secondary.width));
      expect(
        node('.overview-workspaces').getBoundingClientRect().left,
        closeTo(primary.left, 1),
      );
      // A spanning create action inside this auto-fit grid would keep empty
      // tracks alive, leaving a lone workspace confined to a narrow tile.
      expect(
        node('.ws-grid .workspace-tile').getBoundingClientRect().width,
        closeTo(node('.ws-grid').getBoundingClientRect().width, 1),
      );
      final activity = node('.overview-activity');
      expect(activity.getBoundingClientRect().left, closeTo(secondary.left, 1));

      final workspacesTop = node(
        '.overview-workspaces',
      ).getBoundingClientRect().top;
      final activityTop = activity.getBoundingClientRect().top;
      final healthHeight = node(
        '.overview-health',
      ).getBoundingClientRect().height;
      await toggle('.processes');
      expect((node('.processes') as web.HTMLDetailsElement).open, isTrue);
      expect(
        node('.overview-health').getBoundingClientRect().height,
        greaterThan(healthHeight),
      );
      expect(activity.getBoundingClientRect().top, greaterThan(activityTop));
      expect(
        node('.overview-workspaces').getBoundingClientRect().top,
        closeTo(workspacesTop, 1),
      );
      expect(
        node('#overview-project-form').getBoundingClientRect().width,
        closeTo(frame.width, 1),
      );
    },
  );

  test(
    'narrow container stacks both lanes without overflowing opened forms',
    () async {
      await mount();
      // The runner's desktop viewport retains its navigation rail/sidebar.
      // Account for those columns so the actual main content is phone-width;
      // viewport-specific mobile shell rules are covered by visual validation.
      final main = node('.main-content');
      final navigationWidth =
          root.getBoundingClientRect().width -
          main.getBoundingClientRect().width;
      root.style.width = '${390 + navigationWidth}px';
      await settle();
      expect(main.getBoundingClientRect().width, closeTo(390, 1));
      await toggle('#overview-project-form');
      await toggle('.processes');
      await toggle('.managed-storage');
      final primary = node('.overview-lane--primary').getBoundingClientRect();
      final secondary = node(
        '.overview-lane--secondary',
      ).getBoundingClientRect();
      expect(primary.left, closeTo(secondary.left, 1));
      expect(primary.width, closeTo(secondary.width, 1));
      expect(secondary.top, greaterThanOrEqualTo(primary.bottom));
      expect(main.scrollWidth, lessThanOrEqualTo(main.clientWidth + 1));
      final frame = node('.page-frame');
      expect(frame.scrollWidth, lessThanOrEqualTo(frame.clientWidth + 1));
      expect(primary.width, greaterThan(0));
    },
  );

  test(
    'snapshot refresh preserves both columns and expanded resource state',
    () async {
      await mount();
      final health = node('.overview-health');
      final activity = node('.overview-activity');
      final processes = node('.processes') as web.HTMLDetailsElement;
      final terminal =
          root.querySelectorAll('.overview-lane--secondary > details').item(0)!
              as web.HTMLDetailsElement;
      await toggle('.processes');
      (terminal.querySelector('summary')! as web.HTMLElement).click();
      await settle();
      expect(terminal.open, isTrue);
      agents = [
        {'name': 'Review agent', 'agent_status': 'blocked'},
      ];
      await connection.send(DashboardCommand('refresh', null, true));
      await settle();
      expect(node('.overview-health'), same(health));
      expect(node('.overview-activity'), same(activity));
      expect(node('.processes'), same(processes));
      expect(processes.open, isTrue);
      expect(root.contains(terminal), isTrue);
      expect(terminal.open, isTrue);
      expect(activity.textContent, contains('is now blocked'));
    },
  );

  test(
    'agents route keeps agents and health in separate fluid lanes',
    () async {
      await mount(route: '/agents');
      expect(
        node('.main-content').classList.contains('main-content--fluid'),
        isTrue,
      );
      expect(
        node('.page-frame').getBoundingClientRect().width,
        greaterThan(1440),
      );
      expect(root.querySelector('.overview-workspaces'), isNull);
      expect(root.querySelector('.overview-activity'), isNull);
      expect(node('.overview-lane--primary .overview-agents'), isNotNull);
      expect(node('.overview-lane--secondary .overview-health'), isNotNull);
      final primary = node('.overview-lane--primary').getBoundingClientRect();
      final secondary = node(
        '.overview-lane--secondary',
      ).getBoundingClientRect();
      expect(primary.right, lessThan(secondary.left));
    },
  );
}
