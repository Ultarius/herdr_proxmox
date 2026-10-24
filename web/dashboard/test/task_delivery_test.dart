import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';

void main() {
  for (final configured in [false, true])
    for (final executor in ['gateway', 'service']) {
      testWidgets(
        'capture is independent; publication needs GitHub $configured and executor $executor',
        (tester) async {
          tester.view.physicalSize = const Size(1280, 1400);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
          final task = <String, dynamic>{
            'id': 'task',
            'title': 'Implementation',
            'state': 'draft',
            'repository': 'repo',
            'organization_id': 'org',
            'auto_validate': false,
            'auto_publish': false,
          };
          final posts = <Map<String, dynamic>>[];
          final connection = DashboardBloc(
            client: MockClient((request) async {
              if (request.method == 'POST' &&
                  request.url.path.endsWith('/policy')) {
                final body = Map<String, dynamic>.from(
                  jsonDecode(request.body),
                );
                posts.add(body);
                task['auto_capture'] = body['auto_capture'];
                task['auto_validate'] = body['auto_validate'];
                task['auto_publish'] = body['auto_publish'];
              }
              return http.Response(
                jsonEncode(
                  request.url.path.endsWith('/tasks')
                      ? {
                          'tasks': [task],
                          'executor': executor,
                          'github': {'configured': configured},
                        }
                      : request.url.path.endsWith('/detail') ||
                            request.url.path.endsWith('/policy')
                      ? task
                      : {'workspaces': [], 'agents': []},
                ),
                200,
              );
            }),
          );
          BlocScope.register<DashboardBloc>(() => connection);
          connection.connect('token');
          connection.operator.value = {'role': 'admin'};
          await tester.pumpWidget(
            const MaterialApp(
              home: Scaffold(body: TasksPage(taskId: 'task')),
            ),
          );
          await tester.pumpAndSettle();
          await tester.scrollUntilVisible(
            find.text('Automation settings'),
            250,
            scrollable: find.byType(Scrollable).first,
          );
          await tester.tap(find.text('Automation settings'));
          await tester.pumpAndSettle();
          final capture = tester.widget<SwitchListTile>(
            find.widgetWithText(
              SwitchListTile,
              'Automatically capture completed candidates',
            ),
          );
          final validation = tester.widget<SwitchListTile>(
            find.widgetWithText(
              SwitchListTile,
              'Automatically validate captured candidates',
            ),
          );
          expect(capture.onChanged, isNotNull);
          expect(validation.onChanged, isNotNull);
          capture.onChanged!(true);
          await tester.pumpAndSettle();
          expect(posts.single['auto_capture'], true);
          expect(posts.single['auto_validate'], false);
          expect(posts.single['auto_publish'], false);
          posts.clear();
          final control = find.widgetWithText(
            SwitchListTile,
            'Publish verified branch and follow the configured PR/adoption workflow',
          );
          final switchTile = tester.widget<SwitchListTile>(control);
          final canPublish = configured && executor == 'service';
          expect(switchTile.onChanged, canPublish ? isNotNull : isNull);
          if (canPublish) {
            switchTile.onChanged!(true);
            await tester.pumpAndSettle();
            expect(posts.single['auto_validate'], true);
            expect(posts.single['auto_capture'], true);
            expect(posts.single['auto_publish'], true);
            expect(posts.single['task_id'], 'task');
          } else {
            expect(posts, isEmpty);
          }
          expect(tester.takeException(), isNull);
          await tester.pumpWidget(const SizedBox.shrink());
          await connection.disconnect();
          await BlocScope.reset();
        },
      );
    }
}
