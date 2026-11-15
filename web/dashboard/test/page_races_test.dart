import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:juice/juice.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'package:herdr_dashboard/knowledge_page.dart';
import 'package:herdr_dashboard/routes.dart';
import 'helpers/dashboard_connection.dart';

Map<String, dynamic> taskData(String title) => {
  'tasks': [
    {
      'id': 'task',
      'title': title,
      'state': 'draft',
      'organization_id': 'org',
      'repository': 'repo',
      'description': 'Race regression',
      'created_at': '2026-10-25T00:00:00Z',
    },
  ],
  'github': {},
};

DashboardBloc fixture(FutureOr<Object> Function(http.Request) respond) =>
    DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/snapshot') {
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }
        if (request.url.path == '/api/notifications')
          return http.Response('{}', 200);
        final result = await respond(request);
        return result is http.Response
            ? result
            : http.Response(jsonEncode(result), 200);
      }),
    );

// Resolve blocked HTTP work even when an assertion fails, before Flutter
// checks for pending timers and before the connection's ordinary teardown.
Future<void> withBlockedReply(
  WidgetTester tester,
  DashboardBloc connection,
  Completer<Object> reply,
  Future<void> Function() body,
) => withDashboardConnection(tester, connection, () async {
  try {
    await body();
  } finally {
    if (!reply.isCompleted) reply.complete(http.Response('{}', 200));
    await tester.pump();
  }
});

void main() {
  setUp(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
  });
  for (final failOlder in [false, true]) {
    testWidgets(
      'delayed task ${failOlder ? 'failure' : 'snapshot'} cannot replace newer data',
      (tester) async {
        final older = Completer<Object>();
        var reads = 0;
        final bloc = fixture((request) {
          if (request.url.path == '/api/tasks') {
            reads++;
            if (reads == 1) return taskData('Baseline task');
            if (reads == 2) return older.future;
            return taskData('Newest task');
          }
          return <String, dynamic>{};
        });
        await withBlockedReply(tester, bloc, older, () async {
          await pumpUntil(tester, () => bloc.state.refreshed != null);
          await tester.pumpWidget(
            const MaterialApp(home: Scaffold(body: TasksPage())),
          );
          await pumpUntil(
            tester,
            () => find.text('Baseline task').evaluate().isNotEmpty,
          );
          final dynamic pageState = tester.state(find.byType(TasksPage));
          bloc.notificationRevision.value++;
          await pumpUntil(tester, () => reads == 2);
          final previousRead = pageState.pending as Future<void>;
          bloc.notificationRevision.value++;
          await pumpUntil(
            tester,
            () => find.text('Newest task').evaluate().isNotEmpty,
          );
          expect(reads, 3);
          var previousFinished = false;
          previousRead.whenComplete(() => previousFinished = true);
          older.complete(
            failOlder
                ? http.Response('{"error":"Outdated read failed"}', 503)
                : taskData('Older stale task'),
          );
          await pumpUntil(tester, () => previousFinished);
          expect(find.text('Newest task'), findsWidgets);
          expect(find.text('Older stale task'), findsNothing);
          expect(find.textContaining('Outdated read failed'), findsNothing);
        });
      },
    );
  }

  testWidgets('delayed task detail cannot replace newer selected detail', (
    tester,
  ) async {
    final older = Completer<Object>();
    var details = 0;
    final bloc = fixture((request) {
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
    await withBlockedReply(tester, bloc, older, () async {
      await pumpUntil(tester, () => bloc.state.refreshed != null);
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task')),
        ),
      );
      await pumpUntil(
        tester,
        () => find.text('Baseline detail').evaluate().isNotEmpty,
      );
      final dynamic pageState = tester.state(find.byType(TasksPage));
      bloc.notificationRevision.value++;
      await pumpUntil(tester, () => details == 2);
      final previousRead = pageState.pending as Future<void>;
      bloc.notificationRevision.value++;
      await pumpUntil(
        tester,
        () => find.text('Newest detail').evaluate().isNotEmpty,
      );
      var previousFinished = false;
      previousRead.whenComplete(() => previousFinished = true);
      older.complete((taskData('Older stale detail')['tasks'] as List).single);
      await pumpUntil(tester, () => previousFinished);
      expect(find.text('Newest detail'), findsWidgets);
      expect(find.text('Older stale detail'), findsNothing);
    });
  });

  testWidgets('knowledge filter reads retain ownership of an in-flight write', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1280, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final firstWrite = Completer<Object>();
    var writes = 0, reads = 0;
    final bloc = fixture((request) {
      if (request.url.path == '/api/organizations/directory') {
        return {
          'organizations': [
            {'id': 'org', 'name': 'Team'},
          ],
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
    Finder addButton() => find.widgetWithText(FilledButton, 'Add knowledge');
    Future<void> create(String title) async {
      await tester.ensureVisible(addButton());
      await tester.tap(addButton());
      await pumpUntil(
        tester,
        () => find.text('Add project knowledge').evaluate().isNotEmpty,
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Title'),
        title,
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Finding, evidence and limitations'),
        'Knowledge evidence.',
      );
      await tester.tap(find.widgetWithText(FilledButton, 'Save knowledge'));
    }

    await withBlockedReply(tester, bloc, firstWrite, () async {
      await pumpUntil(tester, () => bloc.state.refreshed != null);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: KnowledgePage(
              coordinator: AppCoordinator(),
              organizationId: 'org',
            ),
          ),
        ),
      );
      await pumpUntil(
        tester,
        () => find.text('Initial knowledge').evaluate().isNotEmpty,
      );
      final staleCreate = tester.widget<FilledButton>(addButton()).onPressed!;
      await create('First knowledge');
      await pumpUntil(
        tester,
        () =>
            writes == 1 &&
            find.text('Add project knowledge').evaluate().isEmpty,
      );
      expect(tester.widget<FilledButton>(addButton()).onPressed, isNull);
      final kinds = find.byWidgetPredicate(
        (widget) =>
            widget is DropdownButtonFormField<String> &&
            widget.decoration.labelText == 'Kinds',
      );
      tester.widget<DropdownButtonFormField<String>>(kinds).onChanged!(
        'finding',
      );
      await pumpUntil(
        tester,
        () => find.text('Filtered knowledge').evaluate().isNotEmpty,
      );
      expect(reads, 2);
      expect(tester.widget<FilledButton>(addButton()).onPressed, isNull);
      staleCreate();
      await tester.pump();
      expect(find.text('Add project knowledge'), findsNothing);
      expect(writes, 1);
      firstWrite.complete({'id': 'first-created-record'});
      await pumpUntil(
        tester,
        () => tester.widget<FilledButton>(addButton()).onPressed != null,
      );
      expect(reads, 3);
      await create('Second knowledge');
      await pumpUntil(
        tester,
        () =>
            writes == 2 &&
            tester.widget<FilledButton>(addButton()).onPressed != null,
      );
      expect(tester.takeException(), isNull);
    });
  });
}
