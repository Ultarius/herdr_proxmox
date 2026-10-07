import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/app_shell.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets('builds page belongs to Settings on mobile', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final connection = DashboardBloc(
      client: MockClient(
        (_) async =>
            http.Response('{"workspaces":[],"agents":[],"alerts":[]}', 200),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    await tester.pumpWidget(
      MaterialApp(
        home: AppShell(
          coordinator: AppCoordinator(),
          section: 'builds',
          child: const Text('Build content'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      tester.widget<NavigationBar>(find.byType(NavigationBar)).selectedIndex,
      4,
    );
    expect(find.text('Build content'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await BlocScope.reset();
  });
}
