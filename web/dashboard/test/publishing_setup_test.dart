import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'package:herdr_dashboard_state/publishing_connections.dart';
import 'helpers/dashboard_connection.dart';

void main() {
  Future<void> withPublishing(
    WidgetTester tester,
    Future<void> Function(DashboardBloc, List<Map<String, dynamic>>) body, {
    bool listingFails = false,
  }) async {
    tester.view.physicalSize = const Size(1280, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final posts = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/projects/connections') {
          if (listingFails)
            return http.Response('{"error":"Unavailable"}', 503);
          return http.Response(
            jsonEncode({
              'connections': [
                {
                  'id': 'a' * 32,
                  'kind': 'token',
                  'label': 'Saved repository PAT',
                  'repository': 'owner/repo',
                },
                {
                  'id': 'b' * 32,
                  'kind': 'token',
                  'label': 'Second PAT',
                  'repository': 'owner/other',
                },
                {
                  'id': 'c' * 32,
                  'kind': 'ssh',
                  'label': 'SSH connection',
                  'repository': 'owner/repo',
                },
              ],
            }),
            200,
          );
        }
        if (request.url.path == '/api/github/configure') {
          posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
          return http.Response('{"configured":true}', 200);
        }
        if (request.url.path == '/api/tasks')
          return http.Response(
            '{"tasks":[],"github":{"configured":false}}',
            200,
          );
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    await withDashboardConnection(tester, connection, () async {
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: TasksPage())),
      );
      await pumpUntil(
        tester,
        () => find.text('GitHub publishing').evaluate().isNotEmpty,
      );
      await clickText(tester, 'GitHub publishing');
      await pumpUntil(
        tester,
        () => find.byType(AlertDialog).evaluate().isNotEmpty,
      );
      await tester.pump(const Duration(milliseconds: 300));
      expect(
        find.byType(CircularProgressIndicator, skipOffstage: false),
        findsNothing,
        reason:
            'Waiting for publishing setup input must not leave a loading spinner running.',
      );
      await body(connection, posts);
    });
  }

  Finder sourceSelector() =>
      find.byKey(const ValueKey('publishing-token-source'));
  Finder field(String label) => find.widgetWithText(TextField, label);
  TextEditingController repositories(WidgetTester tester) => tester
      .widget<TextField>(field('Allowed owner/repository (one per line)'))
      .controller!;
  Future<void> choose(WidgetTester tester, String label) async {
    await tester.tap(sourceSelector());
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.text(label).last);
    await tester.pump(const Duration(milliseconds: 300));
  }

  Future<void> cancel(WidgetTester tester) async {
    await clickText(tester, 'Cancel');
    await pumpUntil(tester, () => find.byType(AlertDialog).evaluate().isEmpty);
    await tester.pump(const Duration(milliseconds: 300));
  }

  testWidgets('publishing reuses a saved PAT without receiving its value', (
    tester,
  ) async {
    await withPublishing(tester, (_, posts) async {
      await tester.tap(sourceSelector());
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('SSH connection · owner/repo'), findsNothing);
      await tester.tap(find.text('Saved repository PAT · owner/repo').last);
      await tester.pump(const Duration(milliseconds: 300));
      expect(field('Personal access token'), findsNothing);
      expect(find.text(savedPublishingTokenNotice), findsOneWidget);
      expect(repositories(tester).text, 'owner/repo');
      expect(posts, isEmpty);
      await clickText(tester, 'Validate and save');
      await pumpUntil(tester, () => posts.isNotEmpty);
      expect(posts.single, {
        'connection_id': 'a' * 32,
        'repositories': ['owner/repo'],
        'publication': {
          'owner/repo': {'mode': 'direct'},
        },
      });
      expect(posts.single.containsKey('token'), isFalse);
      await tester.pump(const Duration(milliseconds: 300));
      expect(tester.takeException(), isNull);
    });
  });

  testWidgets(
    'source switching updates suggestions and preserves deliberate edits',
    (tester) async {
      await withPublishing(tester, (_, posts) async {
        await choose(tester, 'Saved repository PAT · owner/repo');
        await choose(tester, 'Second PAT · owner/other');
        expect(repositories(tester).text, 'owner/other');
        await tester.enterText(
          field('Allowed owner/repository (one per line)'),
          'custom/repository',
        );
        await tester.pump();
        await choose(tester, 'Saved repository PAT · owner/repo');
        expect(repositories(tester).text, 'custom/repository');
        await cancel(tester);
        expect(posts, isEmpty);
      });
    },
  );

  testWidgets('switching to saved credentials clears the manual secret', (
    tester,
  ) async {
    await withPublishing(tester, (_, posts) async {
      await tester.enterText(
        field('Personal access token'),
        'github_pat_${'m' * 40}',
      );
      await choose(tester, 'Saved repository PAT · owner/repo');
      await choose(tester, manualPublishingTokenLabel);
      expect(
        tester
            .widget<TextField>(field('Personal access token'))
            .controller!
            .text,
        isEmpty,
      );
      await cancel(tester);
      expect(posts, isEmpty);
    });
  });

  testWidgets('a role change cannot submit a saved publishing source', (
    tester,
  ) async {
    await withPublishing(tester, (connection, posts) async {
      await choose(tester, 'Saved repository PAT · owner/repo');
      connection.operator.value = {'role': 'operator'};
      await clickText(tester, 'Validate and save');
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.byType(AlertDialog), findsNothing);
      expect(posts, isEmpty);
    });
  });

  testWidgets(
    'a failed listing leaves manual setup available with an explanation',
    (tester) async {
      await withPublishing(tester, (_, posts) async {
        expect(find.text(publishingSourcesUnavailable), findsOneWidget);
        expect(field('Personal access token'), findsOneWidget);
        await cancel(tester);
        expect(posts, isEmpty);
      }, listingFails: true);
    },
  );
}
