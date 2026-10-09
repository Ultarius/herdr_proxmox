import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/update_card.dart';

void main() {
  testWidgets('alpha dropdown requires explicit selection and confirmation', (
    tester,
  ) async {
    const tag = 'alpha-login-123456abcdef-b42';
    final installs = <Map<String, dynamic>>[];
    final bloc = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/updates/alphas')) {
          return http.Response(
            jsonEncode({
              'releases': [
                {'version': tag, 'notes': 'Feature alpha/login at abc'},
              ],
            }),
            200,
          );
        }
        if (request.url.path.endsWith('/updates/install')) {
          installs.add(jsonDecode(request.body) as Map<String, dynamic>);
          return http.Response('{"state":"queued"}', 200);
        }
        if (request.url.path.endsWith('/updates')) {
          return http.Response(
            '{"installed":"v1.0.0","latest":"v1.0.0","state":"idle","supported":true,"available":false}',
            200,
          );
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => bloc);
    bloc.connect('token');
    await bloc.stream.firstWhere((_) => bloc.state.refreshed != null);
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: SingleChildScrollView(child: UpdateCard())),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(DropdownButtonFormField<String>), findsNothing);
    await tester.tap(find.text('Browse alpha builds'));
    await tester.pumpAndSettle();
    final install = find.widgetWithText(FilledButton, 'Install selected alpha');
    expect(tester.widget<FilledButton>(install).onPressed, isNull);
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text(tag).last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(install);
    await tester.tap(install);
    await tester.pumpAndSettle();
    expect(find.textContaining('experimental feature-branch'), findsOneWidget);
    expect(installs, isEmpty);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(installs, isEmpty);
    await tester.tap(install);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Install update').last);
    await tester.pumpAndSettle();
    expect(installs.single['version'], tag);
    expect(installs.single['channel'], 'alpha');
    expect(installs.single['confirm_alpha'], isTrue);
    await tester.pumpWidget(const SizedBox.shrink());
    await bloc.disconnect();
    await BlocScope.reset();
  });
}
