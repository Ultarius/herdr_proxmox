import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/group_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets('running group replies update inside a collapsed live panel', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var inspections = 0;
    var reply = 'First reply';
    var finished = false;
    var interrupted = false;
    var recoveries = 0;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/recover')) {
          expect(jsonDecode(request.body)['job_id'], 'discussion');
          recoveries++;
          finished = true;
          return http.Response('{"id":"discussion"}', 200);
        }
        if (request.url.path.endsWith('/inspect')) {
          inspections++;
          expect(jsonDecode(request.body)['job_id'], 'discussion');
          return http.Response(
            jsonEncode({
              'streams': [
                {
                  'profile_id': 'facilitator',
                  'name': 'Standup',
                  'status': 'working',
                  'output': 'Coordinating members',
                },
              ],
              'artifact_draft': 'Partial action brief',
              'contributions': [
                {'round': 1, 'name': 'Max', 'content': reply},
              ],
            }),
            200,
          );
        }
        if ([
          '/api/organizations',
          '/api/organizations/history',
          '/api/organizations/directory',
          '/api/organizations/state',
          '/api/organizations/activity',
        ].contains(request.url.path)) {
          return http.Response(
            jsonEncode({
              'organizations': [],
              'profiles': [],
              'groups': [
                {
                  'id': 'group',
                  'organization_id': 'org',
                  'name': 'Standup',
                  'description': 'Share work',
                  'members': ['max', 'iris'],
                },
              ],
              'jobs': [
                {
                  'id': 'discussion',
                  'kind': 'discussion',
                  'group_id': 'group',
                  'state': finished
                      ? 'artifact_ready'
                      : interrupted
                      ? 'needs_attention'
                      : 'running',
                  'result': finished ? 'Final brief' : '',
                  'contributions': [],
                },
              ],
            }),
            200,
          );
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(
      () => connection,
      lifecycle: BlocLifecycle.permanent,
    );
    final connected = connection.stream.firstWhere(
      (_) => connection.state.connected,
    );
    connection.connect('token');
    await connected;
    final coordinator = AppCoordinator();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: GroupPage(id: 'group', coordinator: coordinator),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Live discussion · 1 replies'), findsOneWidget);
    expect(find.text('First reply'), findsNothing);
    await tester.tap(find.text('Live discussion · 1 replies'));
    await tester.pumpAndSettle();
    expect(find.text('First reply'), findsOneWidget);
    expect(find.text('Partial action brief'), findsNothing);
    reply = 'Updated reply';
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.text('Updated reply'), findsOneWidget);
    expect(find.text('Standup: working'), findsOneWidget);
    await tester.tap(find.text('Discussion artifact draft · In progress'));
    await tester.pumpAndSettle();
    expect(find.text('Partial action brief'), findsOneWidget);
    interrupted = true;
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(
      find.text('Saved discussion artifact · Not finalized'),
      findsOneWidget,
    );
    await tester.ensureVisible(find.text('Recover saved artifact'));
    await tester.tap(find.text('Recover saved artifact'));
    await tester.pumpAndSettle();
    expect(recoveries, 1);
    expect(find.text('Live discussion · 1 replies'), findsNothing);
    final afterFinish = inspections;
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(inspections, afterFinish);
    await tester.ensureVisible(find.text('Artifacts'));
    await tester.tap(find.text('Artifacts'));
    await tester.pumpAndSettle();
    expect(find.text('Final brief'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
    coordinator.dispose();
  });
}
