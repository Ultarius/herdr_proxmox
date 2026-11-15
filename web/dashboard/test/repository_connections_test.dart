import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/repository_connections.dart';
import 'package:herdr_dashboard/repository_connections.dart';
import 'package:juice/juice.dart';

void main() {
  testWidgets(
    'PAT picker paginates, follows manual edits, and clears stale errors',
    (tester) async {
      final posts = <Map<String, dynamic>>[];
      var deny = false;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/connections')) {
            if (request.method == 'POST') {
              final body = Map<String, dynamic>.from(jsonDecode(request.body));
              posts.add(body);
              if (body['action'] == 'discover') {
                if (deny)
                  return http.Response(
                    '{"error":"GitHub access was denied or rate limited."}',
                    403,
                  );
                return http.Response(
                  jsonEncode({
                    'repositories': [
                      {
                        'repository': body['page'] == 1
                            ? 'owner/one'
                            : 'owner/two',
                      },
                    ],
                    'next_page': body['page'] == 1 ? 2 : null,
                  }),
                  200,
                );
              }
              return http.Response('{}', 200);
            }
            return http.Response('{"connections":[],"bindings":[]}', 200);
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      addTearDown(() async {
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        BlocScope.endAll();
      });
      connection.connect('admin');
      await tester.pumpAndSettle();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: RepositoryConnectionsPanel(onChanged: (_) {}),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      Future<void> click(String text) async {
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text(text).last);
        await tester.tap(find.text(text).last);
        await tester.pumpAndSettle();
      }

      await click('Repository connections');
      await click('SSH deploy key');
      await click(repositoryTokenLabel);
      final token = find.widgetWithText(
        TextField,
        'GitHub personal access token',
      );
      await tester.enterText(token, 'github_pat_fixture');
      await click('Discover repositories');
      expect(posts.single['action'], 'discover');
      expect(
        find.textContaining('More repositories are available'),
        findsOneWidget,
      );
      await click('Load more repositories');
      expect(posts.last['page'], 2);
      expect(find.text('Load more repositories'), findsNothing);
      await click('Choose a repository');
      await click('owner/two');
      final repository = find.widgetWithText(
        TextField,
        'GitHub repository (owner/repository)',
      );
      expect(
        tester.widget<TextField>(repository).controller!.text,
        'owner/two',
      );
      expect(
        tester
            .widget<TextField>(
              find.widgetWithText(TextField, 'Connection name'),
            )
            .controller!
            .text,
        'owner/two',
      );
      final picker = find.byWidgetPredicate(
        (widget) =>
            widget is DropdownButtonFormField<String> &&
            widget.decoration.labelText == 'Discovered repository',
      );
      await tester.enterText(repository, 'owner/manual');
      await tester.pumpAndSettle();
      expect(tester.state<FormFieldState<String>>(picker).value, '');
      await tester.enterText(repository, 'owner/two');
      await tester.pumpAndSettle();
      expect(tester.state<FormFieldState<String>>(picker).value, 'owner/two');
      await click('Save token connection');
      expect(posts.last, {
        'action': 'create',
        'kind': 'token',
        'token': 'github_pat_fixture',
        'repository': 'owner/two',
        'label': 'owner/two',
      });
      expect(tester.widget<TextField>(token).controller!.text, isEmpty);
      expect(find.textContaining('Token connection saved.'), findsOneWidget);
      deny = true;
      await tester.enterText(token, 'github_pat_expired');
      await click('Discover repositories');
      expect(find.textContaining('denied'), findsOneWidget);
      await tester.enterText(token, 'github_pat_replacement');
      await tester.pumpAndSettle();
      expect(find.textContaining('denied'), findsNothing);
      expect(picker, findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      BlocScope.endAll();
    },
  );

  for (final failFirst in [false, true]) {
    testWidgets(
      'token setup and rotation retain rejected pastes (failFirst=$failFirst)',
      (tester) async {
        final posts = <Map<String, dynamic>>[];
        final records = <Map<String, dynamic>>[];
        var rejectNext = failFirst;
        final connection = DashboardBloc(
          client: MockClient((request) async {
            dynamic result = {'workspaces': [], 'agents': []};
            if (request.url.path.endsWith('/connections')) {
              result = {'connections': records, 'bindings': []};
              if (request.method == 'POST') {
                posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
                if (rejectNext)
                  return http.Response(
                    '{"error":"Save failed; try again."}',
                    422,
                  );
                if (posts.last['action'] == 'create') {
                  records.add({
                    'id': 'token-connection',
                    'kind': 'token',
                    'label': 'Private token',
                    'repository': 'owner/private',
                  });
                }
                result = records.single;
              }
            }
            return http.Response(jsonEncode(result), 200);
          }),
        );
        BlocScope.register<DashboardBloc>(() => connection);
        try {
          connection.connect('admin');
          await tester.pumpAndSettle();
          await tester.pumpWidget(
            MaterialApp(
              home: Scaffold(
                body: SingleChildScrollView(
                  child: RepositoryConnectionsPanel(onChanged: (_) {}),
                ),
              ),
            ),
          );
          await tester.pumpAndSettle();
          await tester.tap(find.text('Repository connections'));
          await tester.pumpAndSettle();
          await tester.enterText(
            find.widgetWithText(TextField, 'Connection name'),
            'Private token',
          );
          await tester.enterText(
            find.widgetWithText(
              TextField,
              'GitHub repository (owner/repository)',
            ),
            'owner/private',
          );
          await tester.tap(find.text('SSH deploy key'));
          await tester.pumpAndSettle();
          await tester.tap(find.text(repositoryTokenLabel).last);
          await tester.pumpAndSettle();
          final input = find.widgetWithText(
            TextField,
            'GitHub personal access token',
          );
          expect(tester.widget<TextField>(input).obscureText, isTrue);
          expect(tester.widget<TextField>(input).autofillHints, isNull);
          await tester.enterText(input, 'github_pat_test_fixture');
          await tester.ensureVisible(find.text('Save token connection'));
          await tester.tap(find.text('Save token connection'));
          await tester.pumpAndSettle();
          if (failFirst) {
            expect(posts.length, 1);
            expect(
              tester.widget<TextField>(input).controller!.text,
              'github_pat_test_fixture',
            );
            expect(
              find.textContaining('Save failed; try again.'),
              findsOneWidget,
            );
            rejectNext = false;
            await tester.tap(find.text('Save token connection'));
            await tester.pumpAndSettle();
          }
          expect(posts.length, failFirst ? 2 : 1);
          expect(posts.last, {
            'action': 'create',
            'label': 'Private token',
            'repository': 'owner/private',
            'kind': 'token',
            'token': 'github_pat_test_fixture',
          });
          expect(tester.widget<TextField>(input).controller!.text, isEmpty);
          expect(
            find.textContaining('Token connection saved.'),
            findsOneWidget,
          );
          expect(
            find.byWidgetPredicate(
              (widget) =>
                  (widget is Text &&
                      (widget.data ?? '').contains(
                        'github_pat_test_fixture',
                      )) ||
                  (widget is SelectableText &&
                      (widget.data ?? '').contains('github_pat_test_fixture')),
            ),
            findsNothing,
          );
          await tester.ensureVisible(find.text('Rotate token'));
          await tester.tap(find.text('Rotate token'));
          await tester.pumpAndSettle();
          final replacement = find.widgetWithText(
            TextField,
            'Replacement GitHub personal access token',
          );
          await tester.enterText(replacement, 'github_pat_replacement_fixture');
          rejectNext = failFirst;
          await tester.ensureVisible(find.text('Save replacement token'));
          await tester.tap(find.text('Save replacement token'));
          await tester.pumpAndSettle();
          if (failFirst) {
            expect(
              tester.widget<TextField>(replacement).controller!.text,
              'github_pat_replacement_fixture',
            );
            rejectNext = false;
            await tester.tap(find.text('Save replacement token'));
            await tester.pumpAndSettle();
          }
          expect(
            posts.where((post) => post['action'] == 'rotate').length,
            failFirst ? 2 : 1,
          );
          expect(posts.last, {
            'action': 'rotate',
            'id': 'token-connection',
            'token': 'github_pat_replacement_fixture',
          });
          expect(
            find.byWidgetPredicate(
              (widget) =>
                  (widget is Text &&
                      (widget.data ?? '').contains(
                        'github_pat_replacement_fixture',
                      )) ||
                  (widget is SelectableText &&
                      (widget.data ?? '').contains(
                        'github_pat_replacement_fixture',
                      )),
            ),
            findsNothing,
          );
          expect(find.textContaining('Token replaced.'), findsOneWidget);
        } finally {
          await tester.pumpWidget(const SizedBox.shrink());
          await connection.disconnect();
          BlocScope.endAll();
        }
      },
    );
  }

  test(
    'disconnected emissions do not repeatedly clear the controller and refresh disables immediately',
    () async {
      final pending = Completer<http.Response>();
      var updates = 0, cleared = 0;
      final connection = DashboardBloc(
        client: MockClient(
          (request) async => request.url.path.endsWith('/connections')
              ? pending.future
              : http.Response('{"workspaces":[],"agents":[]}', 200),
        ),
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () => updates++,
        onChanged: (_) => cleared++,
      );
      addTearDown(() async {
        controller.dispose();
        if (!pending.isCompleted) pending.complete(http.Response('{}', 200));
        await connection.disconnect();
        connection.dispose();
      });
      controller.sessionChanged();
      controller.sessionChanged();
      expect(updates, 0);
      expect(cleared, 0);
      connection.connect('admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      expect(controller.refreshing, isTrue);
      expect(updates, greaterThan(0));
      pending.complete(http.Response('{}', 200));
      await Future<void>.delayed(Duration.zero);
      expect(controller.refreshing, isFalse);
    },
  );

  test(
    'pending authentication cannot fetch connections using the default administrator role',
    () async {
      final session = Completer<http.Response>();
      var requests = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/session') return session.future;
          if (request.url.path.endsWith('/connections')) requests++;
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () {},
        onChanged: (_) {},
      );
      addTearDown(() async {
        controller.dispose();
        await connection.disconnect();
        connection.dispose();
      });
      final signingIn = connection.signIn('operator-token');
      await Future<void>.delayed(Duration.zero);
      expect(requests, 0);
      expect(controller.canManage, isFalse);
      session.complete(
        http.Response(
          '{"authenticated":true,"operator":"reader","role":"operator"}',
          200,
        ),
      );
      await signingIn;
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await Future<void>.delayed(Duration.zero);
      expect(requests, 0);
      expect(controller.canManage, isFalse);
    },
  );

  test(
    'failed connection loading waits for explicit refresh, not every snapshot',
    () async {
      var reads = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/connections')) {
            reads++;
            return http.Response('{"error":"Temporarily unavailable"}', 503);
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      connection.connect('admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () {},
        onChanged: (_) {},
      );
      addTearDown(() async {
        controller.dispose();
        await connection.disconnect();
        connection.dispose();
      });
      await Future<void>.delayed(Duration.zero);
      expect(reads, 1);
      expect(controller.message, contains('Temporarily unavailable'));
      for (var i = 0; i < 3; i++) {
        await connection.send(DashboardCommand('refresh'));
        await Future<void>.delayed(Duration.zero);
      }
      expect(reads, 1);
      await controller.refresh();
      expect(reads, 2);
    },
  );

  test(
    'role changes clear connections and reject delayed administrative results',
    () async {
      final pending = Completer<http.Response>();
      final loaded = <List<Map<String, dynamic>>>[];
      var requests = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/connections')) {
            requests++;
            return pending.future;
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      connection.connect('admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () {},
        onChanged: loaded.add,
      );
      addTearDown(() async {
        controller.dispose();
        await connection.disconnect();
        connection.dispose();
      });
      await Future<void>.delayed(Duration.zero);
      expect(requests, 1);
      connection.operator.value = {'role': 'operator'};
      pending.complete(
        http.Response(
          jsonEncode({
            'connections': [
              {'id': 'old-admin-key'},
            ],
          }),
          200,
        ),
      );
      await Future<void>.delayed(Duration.zero);
      expect(controller.canManage, isFalse);
      expect(controller.connections, isEmpty);
      expect(loaded.expand((items) => items), isEmpty);
      await controller.refresh();
      await controller.action('create', {});
      expect(requests, 1);
    },
  );
}
