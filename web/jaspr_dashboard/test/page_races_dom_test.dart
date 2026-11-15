@TestOn('browser')
library;

import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'package:herdr_jaspr_dashboard/pages/tasks_page.dart';
import 'package:herdr_jaspr_dashboard/pages/knowledge_page.dart';

class _Host extends StatefulComponent {
  const _Host(this.child);
  final Component child;
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
    ui.dialogChanges.addListener(changed);
  }

  @override
  void dispose() {
    ui.dialogChanges.removeListener(changed);
    super.dispose();
  }

  @override
  Component build(BuildContext context) =>
      dom.div([component.child, ui.buildDialogs()]);
}

Future<void> until(
  bool Function() ready, {
  String reason = 'Page did not settle',
}) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!ready() && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(ready(), isTrue, reason: '$reason: ${visibleText()}');
}

Future<void> flush() async {
  await Future<void>.delayed(Duration.zero);
}

Iterable<web.HTMLElement> elements(String selector) sync* {
  final nodes = web.document.querySelectorAll('#race-probe $selector');
  for (var i = 0; i < nodes.length; i++) {
    yield nodes.item(i) as web.HTMLElement;
  }
}

String visibleText() =>
    web.document.getElementById('race-probe')!.textContent ?? '';

web.HTMLButtonElement button(String label) {
  bool matches(web.HTMLElement e) =>
      (e.textContent ?? '')
          .replaceFirst(e.querySelector('.native-icon')?.textContent ?? '', '')
          .trim() ==
      label;
  final dialogs = elements('.dialog-layer:last-child button').where(matches);
  return (dialogs.isNotEmpty
          ? dialogs.first
          : elements('button').where(matches).first)
      as web.HTMLButtonElement;
}

Future<void> click(String label) async {
  button(label).click();
  await flush();
}

web.HTMLElement field(String label) {
  final node = elements('label').firstWhere((e) => e.textContent == label);
  return web.document.getElementById(node.getAttribute('for')!)
      as web.HTMLElement;
}

Future<void> type(String label, String value) async {
  final node = field(label);
  if (node is web.HTMLInputElement) node.value = value;
  if (node is web.HTMLTextAreaElement) node.value = value;
  node.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await flush();
}

Future<void> choose(String label, String value) async {
  final wrapper = elements(
    'label',
  ).firstWhere((e) => e.querySelector('span')?.textContent == label);
  final node = wrapper.querySelector('select') as web.HTMLSelectElement;
  node.value = value;
  node.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
  await flush();
}

void main() {
  late ClientAppBinding binding;
  DashboardBloc? connection;
  final blockedReplies = <Completer<Object>>[];
  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    final fixture = web.HTMLDivElement()..id = 'race-probe';
    web.document.body!.appendChild(fixture);
    binding = ClientAppBinding();
  });
  tearDownAll(() => web.document.getElementById('race-probe')!.remove());
  tearDown(() async {
    for (final reply in blockedReplies) {
      if (!reply.isCompleted) reply.complete(http.Response('{}', 200));
    }
    blockedReplies.clear();
    await flush();
    ui.closeDialogs();
    binding.detachRootComponent();
    if (connection != null) await connection!.close();
    connection = null;
    await BlocScope.reset();
    web.document.getElementById('race-probe')!.textContent = '';
  });

  Future<void> mount(
    Component page,
    FutureOr<Object> Function(http.Request) responder,
  ) async {
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/snapshot') {
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }
        if (request.url.path == '/api/notifications') {
          return http.Response('{}', 200);
        }
        final result = await responder(request);
        return result is http.Response
            ? result
            : http.Response(jsonEncode(result), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection!);
    final ready = connection!.stream.firstWhere(
      (_) => connection!.state.connected && !connection!.state.busy,
    );
    connection!.connect('probe-token');
    await ready;
    binding.attachRootComponent(_Host(page), attachTo: '#race-probe');
    await until(
      () => web.document.getElementById('race-probe')!.children.length > 0,
    );
  }

  Map<String, dynamic> taskData(String title) => {
    'tasks': [
      {
        'id': 'task',
        'title': title,
        'state': 'draft',
        'organization_id': 'org',
        'repository': 'repo',
        'description': 'Race probe',
        'created_at': '2026-10-25T00:00:00Z',
      },
    ],
    'github': {},
    'releases': [],
  };

  for (final failOlder in [false, true]) {
    test(
      'a delayed task ${failOlder ? 'failure' : 'snapshot'} cannot replace a newer read',
      () async {
        final older = Completer<Object>();
        blockedReplies.add(older);
        var reads = 0;
        final pageKey = GlobalStateKey<State<TasksPage>>();
        await mount(TasksPage(key: pageKey, initialView: 'list'), (request) {
          if (request.url.path == '/api/tasks') {
            reads++;
            if (reads == 1) return taskData('Baseline task');
            if (reads == 2) return older.future;
            return taskData('Newest task');
          }
          return <String, dynamic>{};
        });
        await until(() => visibleText().contains('Baseline task'));
        connection!.notificationRevision.value++;
        await until(() => reads == 2);
        final previousRead =
            (pageKey.currentState! as dynamic).pending as Future<void>;
        connection!.notificationRevision.value++;
        await until(() => visibleText().contains('Newest task'));
        expect(reads, 3);
        older.complete(
          failOlder
              ? http.Response('{"error":"Outdated read failed"}', 503)
              : taskData('Older stale task'),
        );
        await previousRead;
        await flush();
        expect(visibleText(), contains('Newest task'));
        expect(visibleText(), isNot(contains('Older stale task')));
        expect(visibleText(), isNot(contains('Outdated read failed')));
      },
    );
  }

  test(
    'a delayed task detail cannot replace a newer selected detail',
    () async {
      final older = Completer<Object>();
      blockedReplies.add(older);
      var details = 0;
      final pageKey = GlobalStateKey<State<TasksPage>>();
      await mount(TasksPage(key: pageKey, taskId: 'task'), (request) {
        if (request.url.path == '/api/tasks') return taskData('Compact task');
        if (request.url.path == '/api/tasks/detail') {
          details++;
          if (details == 1)
            return (taskData('Baseline detail')['tasks'] as List).single;
          if (details == 2) return older.future;
          return (taskData('Newest detail')['tasks'] as List).single;
        }
        return <String, dynamic>{};
      });
      await until(() => visibleText().contains('Baseline detail'));
      connection!.notificationRevision.value++;
      await until(() => details == 2);
      final previousRead =
          (pageKey.currentState! as dynamic).pending as Future<void>;
      connection!.notificationRevision.value++;
      await until(() => visibleText().contains('Newest detail'));
      older.complete((taskData('Older stale detail')['tasks'] as List).single);
      await previousRead;
      await flush();
      expect(visibleText(), contains('Newest detail'));
      expect(visibleText(), isNot(contains('Older stale detail')));
    },
  );

  test('knowledge filter reads cannot release an in-flight mutation', () async {
    final firstWrite = Completer<Object>();
    blockedReplies.add(firstWrite);
    var writes = 0, reads = 0;
    await mount(const KnowledgePage(organizationId: 'org'), (request) {
      if (request.url.path == '/api/organizations/directory') {
        return {
          'organizations': [
            {'id': 'org', 'name': 'Team'},
          ],
          'profiles': [],
        };
      }
      if (request.url.path == '/api/knowledge') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        if (body['mode'] == 'create') {
          writes++;
          if (writes == 1) return firstWrite.future;
          return {'id': 'second-created-record'};
        }
        reads++;
        return {
          'records': [
            {
              'id': 'existing',
              'title': reads == 1 ? 'Initial knowledge' : 'Filtered knowledge',
              'body': 'Existing evidence',
              'kind': 'finding',
              'state': 'reported',
              'repository': '',
              'created_at': '2026-10-25',
              'source': {},
              'audit': [],
            },
          ],
          'repositories': ['repo'],
          'next_before': null,
        };
      }
      return <String, dynamic>{};
    });
    await until(() => visibleText().contains('Initial knowledge'));
    await click('Add knowledge');
    await until(() => visibleText().contains('Add project knowledge'));
    await type('Title', 'First knowledge');
    await type('Finding, evidence and limitations', 'A pending mutation.');
    await click('Save knowledge');
    await until(() => writes == 1);
    expect(button('Add knowledge').disabled, isTrue);
    await choose('Kinds', 'finding');
    await until(() => visibleText().contains('Filtered knowledge'));
    expect(reads, 2);
    expect(button('Add knowledge').disabled, isTrue);
    expect(writes, 1);
    firstWrite.complete({'id': 'first-created-record'});
    await until(() => !button('Add knowledge').disabled);
    expect(reads, 3, reason: 'Successful writes refresh the current filter.');
    await click('Add knowledge');
    await until(() => visibleText().contains('Add project knowledge'));
    await type('Title', 'Second knowledge');
    await type('Finding, evidence and limitations', 'Another mutation.');
    await click('Save knowledge');
    await until(() => writes == 2 && !button('Add knowledge').disabled);
  });
}
