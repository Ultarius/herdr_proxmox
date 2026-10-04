import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/organization_bloc.dart';
import 'package:herdr_dashboard/organization_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  JuiceLoggerConfig.minLevel = Level.warning;

  test('organization request retries preserve the idempotency key', () async {
    final requests = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method == 'POST') {
          requests.add(jsonDecode(request.body) as Map<String, dynamic>);
          if (requests.length == 1)
            return http.Response('{"error":"Connection interrupted"}', 502);
          return http.Response('{"id":"org1"}', 200);
        }
        if (request.url.path == '/api/organizations')
          return http.Response(
            '{"organizations":[],"profiles":[],"jobs":[]}',
            200,
          );
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    final loaded = connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    connection.connect('token');
    await loaded;
    final bloc = OrganizationBloc(connection);
    await bloc.send(
      OrganizationCommand('save', {
        'name': 'Engineering',
        'purpose': 'Build',
        'instructions': '',
      }),
    );
    expect(bloc.state.error, contains('Connection interrupted'));
    expect(bloc.pending, isNotNull);
    await bloc.send(bloc.pending!);
    expect(requests[0]['request_id'], requests[1]['request_id']);
    expect(bloc.pending, isNull);
    expect(bloc.state.loaded, true);
    bloc.dispose();
    connection.dispose();
  });

  test('disconnect ignores an outstanding organization response', () async {
    final response = Completer<http.Response>();
    final started = Completer<void>();
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations') {
          started.complete();
          return response.future;
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    final loaded = connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    connection.connect('token');
    await loaded;
    final bloc = OrganizationBloc(connection);
    final request = bloc.send(OrganizationCommand('refresh'));
    await started.future;
    await connection.disconnect();
    response.complete(
      http.Response(
        '{"organizations":[{"name":"Private"}],"profiles":[],"jobs":[]}',
        200,
      ),
    );
    await request;
    expect(bloc.state.organizations, isEmpty);
    bloc.dispose();
    connection.dispose();
  });

  testWidgets(
    'create organization, hire, edit persona and navigate with a shared connection',
    (tester) async {
      final organizations = <Map<String, dynamic>>[];
      final profiles = <Map<String, dynamic>>[];
      final mutations = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.method == 'POST') {
            mutations.add(request.url.path);
            final data = jsonDecode(request.body) as Map<String, dynamic>;
            if (request.url.path.endsWith('/save')) {
              organizations.add({...data, 'id': 'org1'});
            } else if (request.url.path.endsWith('/hire')) {
              profiles.clear();
              profiles.add({
                ...data,
                'id': 'profile1',
                'version': mutations.length - 1,
              });
            }
            return http.Response('{"id":"saved"}', 200);
          }
          if (request.url.path == '/api/organizations')
            return http.Response(
              jsonEncode({
                'organizations': organizations,
                'profiles': profiles,
                'jobs': [],
              }),
              200,
            );
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(
        () => connection,
        lifecycle: BlocLifecycle.permanent,
      );
      final coordinator = AppCoordinator();
      await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'token');
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Organization'));
      await tester.pumpAndSettle();
      expect(find.byType(OrganizationPage), findsOneWidget);
      await tester.tap(find.text('Create organization'));
      await tester.pumpAndSettle();
      // Empty required fields cannot submit.
      await tester.tap(find.text('Save organization'));
      await tester.pumpAndSettle();
      expect(find.text('Organization name is required'), findsOneWidget);
      final orgFields = find.descendant(
        of: find.byType(OrganizationForm),
        matching: find.byType(TextFormField),
      );
      await tester.enterText(orgFields.at(0), 'Engineering');
      await tester.enterText(orgFields.at(1), 'Build reliable software');
      await tester.tap(find.text('Save organization'));
      await tester.pumpAndSettle();
      expect(find.text('Engineering'), findsOneWidget);
      await tester.tap(find.text('Hire agent'));
      await tester.pumpAndSettle();
      final hireFields = find.descendant(
        of: find.byType(HireForm),
        matching: find.byType(TextFormField),
      );
      await tester.enterText(hireFields.at(0), 'Maya');
      await tester.enterText(hireFields.at(1), 'Lead');
      await tester.enterText(
        hireFields.at(3),
        'Plan carefully and report evidence.',
      );
      await tester.tap(find.text('Save hire'));
      await tester.pumpAndSettle();
      expect(profiles.single['runtime'], 'codex');
      await tester.scrollUntilVisible(
        find.text('Maya · Lead'),
        180,
        scrollable: find.byType(Scrollable).last,
      );
      expect(find.text('Maya · Lead'), findsOneWidget);
      await tester.ensureVisible(find.text('Edit persona'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit persona'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find
            .descendant(
              of: find.byType(HireForm),
              matching: find.byType(TextFormField),
            )
            .at(3),
        'Lead with a testable plan.',
      );
      await tester.tap(find.text('Save persona'));
      await tester.pumpAndSettle();
      expect(profiles.single['persona'], 'Lead with a testable plan.');
      expect(profiles.single['id'], 'profile1');
      expect(mutations, [
        '/api/organizations/save',
        '/api/organizations/hire',
        '/api/organizations/hire',
      ]);
      await tester.drag(
        find
            .descendant(
              of: find.byType(OrganizationPage),
              matching: find.byType(ListView),
            )
            .first,
        const Offset(0, 1000),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Workspaces'));
      await tester.pumpAndSettle();
      expect(connection.state.connected, true);
      await tester.tap(find.text('Organization'));
      await tester.pumpAndSettle();
      final pageScroll = tester.state<ScrollableState>(
        find
            .descendant(
              of: find.byType(OrganizationPage),
              matching: find.byType(Scrollable),
            )
            .first,
      );
      pageScroll.position.jumpTo(250);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Team'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Maya · Lead'),
        180,
        scrollable: find.byType(Scrollable).last,
      );
      expect(find.text('Maya · Lead'), findsOneWidget);
      await tester.tap(find.text('Disconnect'));
      await tester.pumpAndSettle();
      expect(find.text('Connect on the dashboard'), findsOneWidget);
      expect(find.text('Maya · Lead'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      coordinator.dispose();
      await BlocScope.reset();
    },
  );

  testWidgets('organization forms fit a narrow screen', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => FilledButton(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) =>
                    const HireForm(organization: {'id': 'org1'}, profiles: []),
              ),
              child: const Text('Open hire'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open hire'));
    await tester.pumpAndSettle();
    expect(find.text('Save hire'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
