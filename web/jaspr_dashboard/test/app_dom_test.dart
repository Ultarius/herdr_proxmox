@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/app.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/navigation.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;

Future<void> settle([int milliseconds = 60]) =>
    Future<void>.delayed(Duration(milliseconds: milliseconds));
Iterable<web.HTMLElement> nodes(String selector) sync* {
  final elements = web.document.querySelectorAll('#app-dom-test $selector');
  for (var i = 0; i < elements.length; i++) {
    yield elements.item(i) as web.HTMLElement;
  }
}

web.HTMLButtonElement button(String label) =>
    nodes('button').firstWhere((node) => node.textContent == label)
        as web.HTMLButtonElement;
Future<void> click(String label) async {
  button(label).click();
  await settle();
}

Future<void> input(String label, String value, {String scope = ''}) async {
  final node = nodes(
    '$scope label',
  ).firstWhere((node) => node.querySelector('span')?.textContent == label);
  final control = node.querySelector('input') as web.HTMLInputElement;
  control.value = value;
  control.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await settle();
}

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  final requests = <http.Request>[];
  bool authenticated = true;
  List<Map<String, dynamic>> liveAgents = [];
  Completer<void>? switchAccount;
  Map<String, dynamic> directory() => {
    'organizations': [
      {
        'id': 'other',
        'name': 'Other team',
        'purpose': 'Other work',
        'instructions': '',
      },
      {
        'id': 'org',
        'name': 'Engineering',
        'purpose': 'Reliable software',
        'instructions': 'Review evidence',
      },
    ],
    'profiles': [
      {
        'id': 'maya',
        'name': 'Maya',
        'organization_id': 'other',
        'group_id': null,
        'role': 'Engineer',
        'runtime': 'codex',
        'project': '/projects/repo',
        'use_worktree': true,
      },
    ],
    'groups': [
      {
        'id': 'product',
        'name': 'Product',
        'organization_id': 'other',
        'members': [],
      },
    ],
    'archived_groups': [],
    'jobs': [],
  };
  Map<String, dynamic> snapshot() => {
    'workspaces': [
      {
        'workspace_id': 'workspace-1',
        'label': 'Original workspace',
        'cwd': '/projects/repo',
      },
    ],
    'agents': liveAgents,
    'herdr_server': 'running',
  };

  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'app-dom-test');
    binding = ClientAppBinding();
  });
  setUp(() {
    requests.clear();
    liveAgents = [];
    authenticated = true;
    switchAccount = null;
    web.window.history.replaceState(null, '', '#/');
    ui.messages.value = null;
  });
  tearDown(() async {
    switchAccount?.complete();
    switchAccount = null;
    ui.closeDialogs();
    await settle();
    binding.detachRootComponent();
    await connection.disconnect();
    await BlocScope.reset();
    web.document.getElementById('app-dom-test')!.textContent = '';
    web.window.history.replaceState(null, '', '#/');
  });

  Future<void> mount({String route = '/'}) async {
    web.window.history.replaceState(null, '', '#$route');
    connection = DashboardBloc(
      client: MockClient((request) async {
        requests.add(request);
        final path = request.url.path;
        Map<String, dynamic> response;
        if (path == '/api/session') {
          if (request.method == 'POST') {
            if (switchAccount != null) await switchAccount!.future;
            authenticated = true;
          }
          response = {
            'authenticated': authenticated,
            'role': 'admin',
            'operator': request.method == 'POST'
                ? 'new-account'
                : 'restored-account',
          };
        } else if (path == '/api/session/logout') {
          authenticated = false;
          response = {};
        } else if (path == '/api/snapshot') {
          response = snapshot();
        } else if (path == '/api/organizations/directory' ||
            path == '/api/organizations/state') {
          response = directory();
        } else if (path == '/api/knowledge') {
          response = {
            'records': [],
            'repositories': ['repo'],
            'next_before': null,
          };
        } else if (path == '/api/organizations/activity') {
          response = {'groups': directory()['groups'], 'jobs': []};
        } else if (path == '/api/models') {
          response = {};
        } else if (path == '/api/resources') {
          response = {'available': false};
        } else if (path == '/api/integration') {
          response = {'alerts': []};
        } else if (path == '/api/projects/jobs') {
          response = {'jobs': []};
        } else {
          response = {
            'workspaces': [],
            'agents': [],
            'tasks': [],
            'jobs': [],
            'groups': [],
          };
        }
        return http.Response(jsonEncode(response), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    binding.attachRootComponent(
      const DashboardApp(),
      attachTo: '#app-dom-test',
    );
    await settle(100);
  }

  test('background snapshot refresh keeps overview disclosures open', () async {
    await mount();
    final terminal =
        nodes('details').firstWhere(
              (node) =>
                  node
                      .querySelector('summary')
                      ?.textContent
                      ?.contains('Terminal access') ==
                  true,
            )
            as web.HTMLDetailsElement;
    (terminal.querySelector('summary') as web.HTMLElement).click();
    await settle();
    expect(terminal.open, isTrue);
    for (var update = 0; update < 2; update++) {
      liveAgents = [
        {'name': 'Active agent $update'},
      ];
      await connection.send(DashboardCommand('refresh', null, true));
      await settle();
      expect(nodes('details').contains(terminal), isTrue);
      expect(terminal.open, isTrue);
      expect(
        nodes('main').single.textContent,
        contains('Active agent $update'),
      );
    }
  });

  test(
    'controlled workspace summary keeps clicks when a parent refresh lands before toggle',
    () async {
      await mount();
      final details =
          web.document.getElementById('overview-project-form')
              as web.HTMLDetailsElement;
      for (final open in [true, false, true]) {
        (details.querySelector('summary') as web.HTMLElement).click();
        ui.messages.value = 'Background update $open';
        await settle();
        expect(details.open, open);
      }
      (details.querySelector('summary') as web.HTMLElement).click();
      (details.querySelector('summary') as web.HTMLElement).click();
      await settle();
      expect(details.open, isTrue);
    },
  );

  test(
    'persisted session restores original collaboration route and sidebar directory',
    () async {
      await mount(route: '/organization?view=collaborate');
      expect(connection.state.connected, true);
      expect(
        requests
            .where((request) => request.url.path == '/api/session')
            .single
            .method,
        'GET',
      );
      expect(
        requests.every(
          (request) => !request.headers.containsKey('Authorization'),
        ),
        true,
      );
      expect(currentRoute.toString(), '/organization?view=collaborate');
      expect(nodes('main').single.textContent, contains('Chat with an agent'));
      expect(nodes('.sidebar').single.textContent, contains('Maya'));
      expect(nodes('.sidebar').single.textContent, contains('Product'));
      expect(
        nodes('.sidebar').single.textContent,
        contains('restored-account'),
      );
      final link = nodes(
        'a.nav-item',
      ).firstWhere((node) => node.textContent == 'Tasks');
      link.click();
      await settle();
      expect(currentRoute.path, '/tasks');
      expect(
        nodes('a.nav-item[aria-current=page]').single.textContent,
        'Tasks',
      );
    },
  );

  test(
    'original scoped knowledge route survives sidebar navigation and hash back',
    () async {
      await mount(route: '/knowledge/org');
      final list = requests.lastWhere(
        (request) => request.url.path == '/api/knowledge',
      );
      expect((jsonDecode(list.body) as Map)['organization_id'], 'org');
      expect(nodes('main').single.textContent, contains('Project knowledge'));
      // Establish an explicit entry so browser back returns to this deep link.
      navigate('/organization?view=collaborate');
      await settle();
      navigate('/knowledge/org');
      await settle();
      nodes(
        'a.nav-item',
      ).firstWhere((node) => node.textContent == 'Overview').click();
      await settle();
      expect(currentRoute.path, '/');
      expect(nodes('main h1').single.textContent, 'Overview');
      web.window.history.back();
      await settle(120);
      expect(currentRoute.path, '/knowledge/org');
      expect(nodes('main').single.textContent, contains('Project knowledge'));
      final restoredList = requests.lastWhere(
        (request) => request.url.path == '/api/knowledge',
      );
      expect((jsonDecode(restoredList.body) as Map)['organization_id'], 'org');
    },
  );

  test(
    'sign-in clears its token and sign-out cancels pending dialogs on generation change',
    () async {
      authenticated = false;
      await mount();
      await input('Dashboard access token', 'secret-token');
      final token =
          nodes('main input[type=password]').single as web.HTMLInputElement;
      final signIn = nodes('main form').single as web.HTMLFormElement;
      signIn.dispatchEvent(
        web.Event('submit', web.EventInit(bubbles: true, cancelable: true)),
      );
      expect(token.value, '');
      await settle(100);
      expect(connection.state.connected, true);
      final authentication = requests.singleWhere(
        (request) =>
            request.url.path == '/api/session' && request.method == 'POST',
      );
      expect(authentication.headers['Authorization'], 'Bearer secret-token');
      expect(
        requests
            .where((request) => request.url.path != '/api/session')
            .every((request) => !request.headers.containsKey('Authorization')),
        true,
      );
      final pending = ui.showDialog<String>(
        'Pending approval',
        (_) => ui.text('Awaiting review'),
      );
      await settle();
      expect(nodes('.dialog-host .dialog').length, 1);
      final epoch = connection.generation;
      await click('Sign out');
      expect(connection.generation, greaterThan(epoch));
      expect(connection.state.connected, false);
      expect(await pending, isNull);
      expect(nodes('.dialog-host .dialog'), isEmpty);
      expect(
        (nodes('main input[type=password]').single as web.HTMLInputElement)
            .value,
        '',
      );
      expect(
        requests.any((request) => request.url.path == '/api/session/logout'),
        true,
      );
    },
  );

  test(
    'workspace rename confirmation from the previous account cannot send a mutation',
    () async {
      await mount();
      await click('Rename');
      await input(
        'Workspace label',
        'Wrong-account rename',
        scope: '.dialog-layer',
      );
      switchAccount = Completer<void>();
      final switched = connection.signIn('other-account-token');
      await settle();
      final form = nodes('.dialog-layer form').single as web.HTMLFormElement;
      form.dispatchEvent(
        web.Event('submit', web.EventInit(bubbles: true, cancelable: true)),
      );
      await settle();
      expect(
        requests.where(
          (request) => request.url.path == '/api/workspaces/rename',
        ),
        isEmpty,
      );
      switchAccount!.complete();
      switchAccount = null;
      await switched;
      await settle();
      expect(connection.operator.value['name'], 'new-account');
      expect(nodes('.dialog-host .dialog'), isEmpty);
      expect(
        nodes('.workspace-tile').single.textContent,
        contains('Original workspace'),
      );
      expect(
        requests.where(
          (request) => request.url.path == '/api/workspaces/rename',
        ),
        isEmpty,
      );
    },
  );

  test(
    'top create-workspace action opens and scrolls to the native details form',
    () async {
      await mount();
      final details =
          web.document.getElementById('overview-project-form')
              as web.HTMLDetailsElement;
      expect(details.open, false);
      var scrollCalls = 0;
      final original = details.getProperty<JSFunction>('scrollIntoView'.toJS);
      details.setProperty(
        'scrollIntoView'.toJS,
        (() {
          scrollCalls++;
          original.callAsFunction(details);
        }).toJS,
      );
      try {
        final topAction = nodes(
          '.page-header button',
        ).firstWhere((node) => node.textContent == 'Create workspace');
        topAction.click();
        await settle();
        expect(details.open, true);
        expect(scrollCalls, 1);
        expect(details.querySelector('form'), isNotNull);
        expect(details.querySelector('input'), isNotNull);
        expect(
          requests.where(
            (request) => request.url.path == '/api/workspaces/create',
          ),
          isEmpty,
        );
      } finally {
        details.setProperty('scrollIntoView'.toJS, original);
      }
    },
  );
  test(
    'sidebar restores runtime colors and trusts only verified profile ownership',
    () async {
      for (final path in [
        '../web/css/tokens.css',
        '../web/styles.css',
        '../web/css/components.css',
      ]) {
        final ready = Completer<void>();
        final stylesheet = web.HTMLLinkElement()
          ..rel = 'stylesheet'
          ..href = path;
        stylesheet.addEventListener(
          'load',
          ((web.Event _) => ready.complete()).toJS,
        );
        web.document.head!.appendChild(stylesheet);
        await ready.future.timeout(const Duration(seconds: 5));
      }
      liveAgents = [
        {'profile_id': 'maya', 'agent_status': 'idle'},
      ];
      await mount(route: '/knowledge');
      web.HTMLElement marker() =>
          nodes('.sidebar a[href="#/agents/maya"] .status-dot').single;
      expect(marker().getAttribute('data-tone'), 'success');
      expect(marker().getAttribute('data-shape'), 'filled');
      final probe = web.HTMLSpanElement()..style.color = 'var(--color-success)';
      web.document.body!.appendChild(probe);
      expect(
        web.window.getComputedStyle(marker()).backgroundColor,
        web.window.getComputedStyle(probe).color,
      );
      probe.remove();
      for (final entry in {
        'done': 'success',
        'working': 'info',
        'blocked': 'warning',
      }.entries) {
        liveAgents = [
          {'profile_id': 'maya', 'agent_status': entry.key},
        ];
        await connection.send(DashboardCommand('refresh'));
        await settle();
        expect(marker().getAttribute('data-tone'), entry.value);
      }
      // A reused pane/alias without a verified profile binding is not this agent.
      liveAgents = [
        {'name': 'Maya', 'pane_id': 'workspace-1:p1', 'agent_status': 'idle'},
      ];
      await connection.send(DashboardCommand('refresh'));
      await settle();
      expect(marker().getAttribute('data-tone'), 'neutral');
      expect(marker().getAttribute('data-shape'), 'dash');
    },
  );
}
