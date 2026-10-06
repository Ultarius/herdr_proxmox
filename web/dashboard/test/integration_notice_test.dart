import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/app_shell.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/integration.dart';
import 'package:herdr_dashboard/routes.dart';

DashboardBloc connectionWith(
  List<Map<String, dynamic>> alerts, {
  List<Map<String, dynamic>> agents = const [],
  List<String>? posted,
}) => DashboardBloc(
  client: MockClient((request) async {
    if (request.url.path.endsWith('/integration')) {
      return http.Response(
        jsonEncode({
          'alerts': alerts,
          'checked': '2026-10-05T10:00:00+00:00',
          'interval': 60,
        }),
        200,
      );
    }
    if (request.url.path.endsWith('/organizations/directory')) {
      return http.Response(
        jsonEncode({
          'organizations': [
            {'id': 'org1', 'name': 'Engineering'},
          ],
          'profiles': [
            {
              'id': 'p1',
              'organization_id': 'org1',
              'name': 'Max',
              'runtime': 'codex',
            },
          ],
          'groups': [],
        }),
        200,
      );
    }
    if (request.method == 'POST') posted?.add(request.url.path);
    return http.Response(
      jsonEncode({
        'workspaces': [],
        'agents': agents,
        'herdr_server': 'running',
      }),
      200,
    );
  }),
);

Map<String, dynamic> notice({
  String profileId = 'p1',
  String name = 'Max',
  int behind = 3,
  int conflicts = 0,
  bool merging = false,
}) => {
  'profile_id': profileId,
  'run_id': 'r1',
  'name': name,
  'path': '.herdr-worktrees/r1',
  'branch': 'codex/herdr-r1',
  'base': 'origin/main',
  'behind': behind,
  'ahead': 0,
  'conflicts': conflicts,
  'dirty': true,
  'merging': merging,
};

Future<DashboardBloc> connected(
  List<Map<String, dynamic>> alerts, {
  List<Map<String, dynamic>> agents = const [],
  List<String>? posted,
}) async {
  final connection = connectionWith(alerts, agents: agents, posted: posted);
  connection.connect('token');
  await connection.stream.firstWhere((_) => connection.state.refreshed != null);
  return connection;
}

/// Mount the shell, then unmount and dispose so no polling timer survives the
/// test.
Future<void> withShell(
  WidgetTester tester,
  DashboardBloc connection,
  Future<void> Function() body,
) async {
  BlocScope.register<DashboardBloc>(
    () => connection,
    lifecycle: BlocLifecycle.permanent,
  );
  final coordinator = AppCoordinator();
  await tester.pumpWidget(
    MaterialApp(
      home: AppShell(
        coordinator: coordinator,
        section: 'dashboard',
        child: const SizedBox.shrink(),
      ),
    ),
  );
  // The notice poll resolves asynchronously after the first frame.
  await tester.pump();
  await tester.pump();
  await tester.pumpAndSettle();
  try {
    await body();
  } finally {
    await tester.pumpWidget(const SizedBox.shrink());
    coordinator.dispose();
    connection.dispose();
    await BlocScope.reset();
  }
}

void main() {
  JuiceLoggerConfig.minLevel = Level.warning;

  test('a notice explains itself and produces merge instructions', () {
    final item = IntegrationNotice.fromJson(notice());
    expect(item.summary, '3 commits behind origin/main');
    expect(item.instructions, contains('Merge origin/main'));
    expect(item.instructions, contains('Do not reset'));
    expect(item.key, contains('p1'));
  });

  test('conflicts and an unfinished merge are reported', () {
    final item = IntegrationNotice.fromJson(
      notice(conflicts: 2, merging: true, behind: 0),
    );
    expect(item.reasons, contains('2 unresolved conflicts'));
    expect(item.reasons, contains('Merge or rebase in progress'));
    expect(item.reasons, isNot(contains('0 commits behind origin/main')));
    expect(item.instructions, contains('Do not start another merge or rebase'));
    expect(item.instructions, isNot(contains('Merge origin/main')));
  });

  testWidgets('new drift is announced once, repeated polls stay quiet', (
    tester,
  ) async {
    final alerts = <Map<String, dynamic>>[];
    final connection = await connected(alerts);
    await withShell(tester, connection, () async {
      alerts.add(notice());
      await tester.pump(const Duration(seconds: 15));
      await tester.pumpAndSettle();
      expect(
        find.text('Integration notice: 3 commits behind origin/main'),
        findsOneWidget,
      );
      ScaffoldMessenger.of(
        tester.element(find.byType(AppShell)),
      ).hideCurrentSnackBar();
      await tester.pump(const Duration(seconds: 9));
      await tester.pumpAndSettle();
      await tester.pump(const Duration(seconds: 15));
      await tester.pumpAndSettle();
      expect(
        find.text('Integration notice: 3 commits behind origin/main'),
        findsNothing,
      );
    });
  });

  testWidgets('a working agent cannot be asked to integrate', (tester) async {
    final working = await connected(
      const [],
      agents: [
        {'profile_id': 'p1', 'agent_status': 'working'},
      ],
    );
    expect(agentCanIntegrate(working, 'p1'), isFalse);
    working.dispose();

    final idle = await connected(
      const [],
      agents: [
        {'profile_id': 'p1', 'agent_status': 'idle'},
      ],
    );
    expect(agentCanIntegrate(idle, 'p1'), isTrue);
    expect(agentCanIntegrate(idle, 'unknown'), isFalse);
    idle.dispose();
  });

  testWidgets('no badge when every checkout is current', (tester) async {
    final clean = await connected(const []);
    await withShell(tester, clean, () async {
      expect(find.byIcon(Icons.warning_amber_rounded), findsNothing);
    });
  });

  testWidgets('the badge appears when a checkout needs attention', (
    tester,
  ) async {
    final drift = await connected([notice()]);
    await withShell(tester, drift, () async {
      expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
    });
  });

  testWidgets('the notice dialog keeps a busy agent out of reach', (
    tester,
  ) async {
    final posted = <String>[];
    final connection = await connected(
      [notice(conflicts: 1)],
      agents: [
        {'profile_id': 'p1', 'agent_status': 'working'},
      ],
      posted: posted,
    );
    await withShell(tester, connection, () async {
      await tester.tap(find.byIcon(Icons.warning_amber_rounded));
      await tester.pumpAndSettle();
      expect(find.text('Integration notices'), findsOneWidget);
      expect(find.textContaining('1 unresolved conflict'), findsOneWidget);
      expect(find.text('Busy; ask later to integrate'), findsOneWidget);
      expect(find.text('Copy instructions'), findsOneWidget);
      expect(find.text('Open Git changes'), findsOneWidget);

      final ask = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Busy; ask later to integrate'),
      );
      expect(ask.onPressed, isNull);
      // Nothing was prompted: the operator decides, and only for an idle agent.
      expect(posted, isEmpty);
    });
  });

  testWidgets('asking an idle agent sends one tracked request', (tester) async {
    final posted = <String>[];
    final connection = await connected(
      [notice()],
      agents: [
        {'profile_id': 'p1', 'agent_status': 'idle'},
      ],
      posted: posted,
    );
    await withShell(tester, connection, () async {
      await tester.tap(find.byIcon(Icons.warning_amber_rounded));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Ask to integrate'));
      await tester.pumpAndSettle();
      expect(posted, ['/api/organizations/chat']);
    });
  });
}
