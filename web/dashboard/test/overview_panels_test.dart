import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/app_theme.dart';
import 'package:herdr_dashboard/overview_panels.dart';

void main() {
  final agents = [
    {
      'display_name': 'Integration coordinator with a long display name',
      'pane_id': 'w3:p1',
      'agent_status': 'working',
    },
    {'display_name': 'Max', 'pane_id': 'w4:p1', 'agent_status': 'idle'},
  ];
  for (final brightness in Brightness.values) {
    for (final width in [320.0, 390.0, 1440.0]) {
      testWidgets(
        '$brightness overview at $width pixels has no overflow and filters status',
        (tester) async {
          tester.view.physicalSize = Size(width, 1600);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
          var renamed = false;
          await tester.pumpWidget(
            MaterialApp(
              theme: dashboardTheme(brightness),
              home: Scaffold(
                body: SingleChildScrollView(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      children: [
                        OverviewSummary(
                          agents: 2,
                          workspaces: 1,
                          connected: true,
                          onAgents: () {},
                          onWorkspaces: () {},
                          onConnection: () {},
                        ),
                        AgentPanel(agents: agents, onManage: () {}),
                        WorkspacePanel(
                          workspaces: const [
                            {
                              'workspace_id': 'w4',
                              'label': 'A project with a long workspace name',
                            },
                          ],
                          agents: agents,
                          onFocus: (_) {},
                          onRename: (_) => renamed = true,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          );
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await tester.tap(find.widgetWithText(ChoiceChip, 'Working'));
          await tester.pumpAndSettle();
          expect(find.text('Max'), findsNothing);
          expect(
            find.text('Integration coordinator with a long display name'),
            findsOneWidget,
          );
          await tester.tap(find.widgetWithText(ChoiceChip, 'Idle'));
          await tester.pumpAndSettle();
          expect(find.text('Max'), findsOneWidget);
          expect(
            find.text('Integration coordinator with a long display name'),
            findsNothing,
          );
          await tester.tap(find.byTooltip('Workspace actions'));
          await tester.pumpAndSettle();
          await tester.tap(find.text('Rename'));
          await tester.pumpAndSettle();
          expect(renamed, isTrue);
          expect(tester.takeException(), isNull);
        },
      );
    }
  }
}
