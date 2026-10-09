import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/knowledge_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  test('knowledge route keeps organization scope', () {
    final route =
        AppCoordinator().parseRouteFromUri(
              Uri.parse('/knowledge?organization=org'),
            )
            as KnowledgeRoute;
    expect(route.organizationId, 'org');
    expect(route.toUri().queryParameters['organization'], 'org');
  });
  for (final brightness in Brightness.values) {
    testWidgets('knowledge filters and provenance fit a phone in $brightness', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(390, 1800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final requests = <Map<String, dynamic>>[];
      final bloc = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/organizations/directory'))
            return http.Response(
              '{"organizations":[{"id":"org","name":"Team"}]}',
              200,
            );
          if (request.url.path.endsWith('/knowledge')) {
            requests.add(jsonDecode(request.body) as Map<String, dynamic>);
            return http.Response(
              jsonEncode({
                'records': [
                  {
                    'id': 'knowledge-1',
                    'repository': 'repo',
                    'kind': 'finding',
                    'state': 'reported',
                    'title': 'Model metadata failure',
                    'body': 'A reported cause with limitations.',
                    'created_at': '2026-10-09',
                    'source': {
                      'kind': 'task',
                      'task_id': 'task',
                      'source_sha': 'a' * 40,
                      'checks_verified': false,
                    },
                    'audit': [],
                  },
                ],
                'repositories': ['repo'],
                'next_before': null,
              }),
              200,
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => bloc);
      bloc.connect('token');
      await bloc.stream.firstWhere((_) => bloc.state.refreshed != null);
      bloc.operator.value = {'role': 'viewer', 'name': 'reader'};
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData(brightness: brightness),
          home: Scaffold(
            body: KnowledgePage(
              coordinator: AppCoordinator(),
              organizationId: 'org',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Model metadata failure'), findsOneWidget);
      expect(find.text('Add knowledge'), findsNothing);
      await tester.tap(
        find.byWidgetPredicate(
          (w) =>
              w is DropdownButtonFormField<String> &&
              w.decoration.labelText == 'States',
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('reported').last);
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'metadata');
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pumpAndSettle();
      expect(requests.last['query'], 'metadata');
      await tester.tap(find.byTooltip('Clear knowledge search'));
      await tester.pumpAndSettle();
      expect(requests.last['query'], '');
      expect(requests.last['state'], 'reported');
      await tester.tap(find.text('Model metadata failure'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('Exact-commit required checks verified: false'),
        findsOneWidget,
      );
      expect(find.text('Open source task'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await bloc.disconnect();
      await BlocScope.reset();
    });
  }
}
