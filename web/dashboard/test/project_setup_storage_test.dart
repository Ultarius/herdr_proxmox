import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/main.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets(
    'project setup fields do not read expansion booleans as scroll offsets',
    (tester) async {
      tester.view.physicalSize = const Size(1600, 2400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final bloc = DashboardBloc(
        client: MockClient(
          (request) async => http.Response(
            request.url.path.endsWith('/resources')
                ? '{"available":false}'
                : '{"workspaces":[],"agents":[],"jobs":[]}',
            200,
          ),
        ),
      );
      BlocScope.register<DashboardBloc>(() => bloc);
      bloc.connect('token');
      await bloc.stream.firstWhere((_) => bloc.state.refreshed != null);
      await tester.pumpWidget(
        MaterialApp(home: Dashboard(coordinator: AppCoordinator())),
      );
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Add a project or workspace'));
      await tester.tap(find.text('Add a project or workspace'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(
        find.byKey(const PageStorageKey('workspace-name-field')),
        findsOneWidget,
      );
      expect(
        find.byKey(const PageStorageKey('clone-url-field')),
        findsOneWidget,
      );
      await tester.tap(find.text('Add a project or workspace'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Add a project or workspace'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await bloc.disconnect();
      await BlocScope.reset();
    },
  );
}
