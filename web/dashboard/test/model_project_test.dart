import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/model_catalog.dart';

void main() {
  testWidgets('changing project invalidates an in-flight model catalog', (
    tester,
  ) async {
    final stale = Completer<http.Response>();
    final projects = <String>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/models')) {
          projects.add(jsonDecode(request.body)['project'] as String);
          if (projects.length == 1) return stale.future;
          return http.Response(
            jsonEncode({
              'source': 'opencode models --verbose',
              'models': [
                {'provider': 'new', 'id': 'model', 'name': 'New model'},
              ],
            }),
            200,
          );
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    final project = ValueNotifier('/home/herdr/projects/old');
    final provider = TextEditingController();
    final model = TextEditingController();
    final reasoning = TextEditingController();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ValueListenableBuilder<String>(
            valueListenable: project,
            builder: (_, path, child) => ModelFields(
              runtime: 'opencode',
              catalog: const RuntimeCatalog(),
              provider: provider,
              model: model,
              reasoning: reasoning,
              project: path,
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    project.value = '/home/herdr/projects/new';
    await tester.pump();
    stale.complete(
      http.Response(
        jsonEncode({
          'models': [
            {'provider': 'old', 'id': 'stale', 'name': 'Stale model'},
          ],
        }),
        200,
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Refresh models'), findsOneWidget);
    expect(projects, ['/home/herdr/projects/old', '/home/herdr/projects/new']);
    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Provider',
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('new'), findsOneWidget);
    expect(find.text('old'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    BlocScope.endAll();
    project.dispose();
    provider.dispose();
    model.dispose();
    reasoning.dispose();
  });
}
