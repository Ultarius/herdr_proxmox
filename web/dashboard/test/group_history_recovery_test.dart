import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/group_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets(
    'unchanged history recovers read errors without clearing failed commands',
    (tester) async {
      tester.view.physicalSize = const Size(1400, 2000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var reads = 0;
      var failRead = false;
      final data = {
        'groups': [
          {
            'id': 'group',
            'organization_id': 'org',
            'name': 'Recovery',
            'description': 'Recovery topic',
            'members': <String>[],
          },
        ],
        'jobs': <Map<String, dynamic>>[],
        'profiles': <Map<String, dynamic>>[],
        'member_states': <String, dynamic>{},
      };
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/organizations/history')) {
            reads++;
            if (failRead)
              return http.Response(
                '{"error":"History temporarily unavailable"}',
                503,
              );
            return http.Response(jsonEncode(data), 200);
          }
          if (request.url.path.endsWith('/organizations/discuss'))
            return http.Response('{"error":"Command response lost"}', 503);
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      try {
        final connected = connection.stream.firstWhere(
          (_) => connection.state.refreshed != null,
        );
        connection.connect('token');
        await connected;
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(
              body: GroupPage(id: 'group', coordinator: AppCoordinator()),
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(find.text('# Recovery'), findsOneWidget);
        failRead = true;
        connection.notificationRevision.value++;
        await tester.pumpAndSettle();
        expect(
          find.textContaining('History temporarily unavailable'),
          findsOneWidget,
        );
        failRead = false;
        connection.notificationRevision.value++;
        await tester.pumpAndSettle();
        expect(reads, 3);
        expect(
          find.textContaining('History temporarily unavailable'),
          findsNothing,
        );
        await tester.ensureVisible(find.text('Start discussion'));
        await tester.tap(find.text('Start discussion'));
        await tester.pumpAndSettle();
        expect(find.textContaining('Command response lost'), findsOneWidget);
        connection.notificationRevision.value++;
        await tester.pumpAndSettle();
        expect(find.textContaining('Command response lost'), findsOneWidget);
        expect(find.text('Retry same request'), findsOneWidget);
        expect(tester.takeException(), isNull);
      } finally {
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        await BlocScope.reset();
      }
    },
  );
}
