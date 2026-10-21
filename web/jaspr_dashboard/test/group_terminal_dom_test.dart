@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
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

Future<void> until(bool Function() ready) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!ready() && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
  expect(
    ready(),
    true,
    reason: 'DOM did not reach the expected rendered state.',
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
  await done.future.timeout(const Duration(seconds: 5));
}

void main() {
  late ClientAppBinding binding;
  late ValueNotifier<Component> child;
  DashboardBloc? connection;
  setUpAll(() async {
    JuiceLoggerConfig.minLevel = Level.off;
    web.document.body!.appendChild(
      web.HTMLDivElement()..id = 'group-terminal-test',
    );
    web.document.head!.appendChild(
      web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/vendor/xterm/xterm.css',
    );
    await script('../web/vendor/xterm/xterm.js');
    binding = ClientAppBinding();
  });
  tearDown(() async {
    binding.detachRootComponent();
    await Future<void>.delayed(const Duration(milliseconds: 30));
    if (connection != null) {
      await connection!.disconnect();
      connection = null;
    }
    await BlocScope.reset();
    child.dispose();
    web.document.getElementById('group-terminal-test')!.textContent = '';
  });
  void mount(Component component) {
    child = ValueNotifier(component);
    binding.attachRootComponent(_Host(child), attachTo: '#group-terminal-test');
  }

  test(
    'a snapshot inside nested closed disclosures renders on reveal and keeps updating',
    () async {
      Component panel(String text) => dom.details([
        dom.summary([Component.text('Live discussion')]),
        dom.details([
          dom.summary([Component.text('Worker')]),
          SnapshotTerminal(text),
        ]),
      ]);
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
      await Future<void>.delayed(const Duration(milliseconds: 50));
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
      expect(
        panel.getBoundingClientRect().height,
        greaterThan(0),
      );
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
