import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/app_theme.dart';
import 'package:herdr_dashboard/overview_panels.dart';

void main() {
  final agents = [
    {
      'display_name': 'Integration coordinator',
      'pane_id': 'w3:p1',
      'agent_status': 'working',
    },
    {'display_name': 'Max', 'pane_id': 'w4:p1', 'agent_status': 'idle'},
    {'display_name': 'Olaf', 'pane_id': 'w3:p2', 'agent_status': 'working'},
  ];

  // Agent names are plain Text widgets. find.text also matches the search
  // field's EditableText, so scope name assertions to Text only.
  Finder nameText(String value) => find.byWidgetPredicate(
    (widget) => widget is Text && widget.data == value,
  );

  Future<void> pumpPanel(
    WidgetTester tester,
    List<Map<String, dynamic>> agents, {
    double width = 1440,
    Brightness brightness = Brightness.light,
  }) async {
    tester.view.physicalSize = Size(width, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        theme: dashboardTheme(brightness),
        home: Scaffold(
          body: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: AgentPanel(agents: agents, onManage: () {}),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('search filters live agents by name', (tester) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), 'Max');
    await tester.pumpAndSettle();
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsNothing);
    expect(nameText('Integration coordinator'), findsNothing);
  });

  testWidgets('search filters live agents by workspace', (tester) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), 'w4');
    await tester.pumpAndSettle();
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsNothing);
    expect(nameText('Integration coordinator'), findsNothing);
  });

  testWidgets('search is case-insensitive', (tester) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), 'max');
    await tester.pumpAndSettle();
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsNothing);
  });

  testWidgets('search composes with the status filter', (tester) async {
    await pumpPanel(tester, agents);
    await tester.tap(find.widgetWithText(ChoiceChip, 'Working'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'w3');
    await tester.pumpAndSettle();
    expect(nameText('Integration coordinator'), findsOneWidget);
    expect(nameText('Olaf'), findsOneWidget);
    expect(nameText('Max'), findsNothing);
  });

  testWidgets('search survives a status filter change', (tester) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), 'w3');
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ChoiceChip, 'Idle'));
    await tester.pumpAndSettle();
    expect(nameText('Integration coordinator'), findsNothing);
    expect(nameText('Olaf'), findsNothing);
    expect(nameText('Max'), findsNothing);
    expect(find.text('No agents match "w3".'), findsOneWidget);
  });

  testWidgets('clear button resets the search', (tester) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), 'Max');
    await tester.pumpAndSettle();
    expect(nameText('Olaf'), findsNothing);
    expect(find.byTooltip('Clear search'), findsOneWidget);
    await tester.tap(find.byTooltip('Clear search'));
    await tester.pumpAndSettle();
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsOneWidget);
    expect(nameText('Integration coordinator'), findsOneWidget);
    expect(find.byTooltip('Clear search'), findsNothing);
  });

  testWidgets('no-results state offers a clear action', (tester) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), 'nobody');
    await tester.pumpAndSettle();
    expect(find.text('No agents match "nobody".'), findsOneWidget);
    expect(nameText('Max'), findsNothing);
    expect(nameText('Olaf'), findsNothing);
    await tester.tap(find.text('Clear search'));
    await tester.pumpAndSettle();
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsOneWidget);
  });

  testWidgets('search trims whitespace and whitespace input stays clearable', (
    tester,
  ) async {
    await pumpPanel(tester, agents);
    await tester.enterText(find.byType(TextField), '  MAX  ');
    await tester.pumpAndSettle();
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsNothing);
    await tester.enterText(find.byType(TextField), '   ');
    await tester.pumpAndSettle();
    expect(nameText('Olaf'), findsOneWidget);
    expect(find.byTooltip('Clear search'), findsOneWidget);
    await tester.tap(find.byTooltip('Clear search'));
    await tester.pumpAndSettle();
    expect(
      tester.widget<TextField>(find.byType(TextField)).controller!.text,
      isEmpty,
    );
  });

  testWidgets('clearing search preserves the status filter', (tester) async {
    await pumpPanel(tester, agents);
    await tester.tap(find.widgetWithText(ChoiceChip, 'Idle'));
    await tester.enterText(find.byType(TextField), 'nobody');
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Clear search'));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<ChoiceChip>(find.widgetWithText(ChoiceChip, 'Idle'))
          .selected,
      isTrue,
    );
    expect(nameText('Max'), findsOneWidget);
    expect(nameText('Olaf'), findsNothing);
  });

  testWidgets('live updates retain query and filter while refreshing matches', (
    tester,
  ) async {
    await pumpPanel(tester, agents);
    await tester.tap(find.widgetWithText(ChoiceChip, 'Working'));
    await tester.enterText(find.byType(TextField), 'olaf');
    await tester.pumpAndSettle();
    await pumpPanel(tester, [
      {'display_name': 'Olaf', 'pane_id': 'w3:p2', 'agent_status': 'idle'},
      {
        'display_name': 'Olaf assistant',
        'pane_id': 'w5:p1',
        'agent_status': 'working',
      },
    ]);
    expect(
      tester.widget<TextField>(find.byType(TextField)).controller!.text,
      'olaf',
    );
    expect(
      tester
          .widget<ChoiceChip>(find.widgetWithText(ChoiceChip, 'Working'))
          .selected,
      isTrue,
    );
    expect(nameText('Olaf'), findsNothing);
    expect(nameText('Olaf assistant'), findsOneWidget);
  });

  testWidgets('Manage action is preserved alongside search', (tester) async {
    var managed = false;
    tester.view.physicalSize = const Size(1440, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        theme: dashboardTheme(Brightness.light),
        home: Scaffold(
          body: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: AgentPanel(agents: agents, onManage: () => managed = true),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Manage'));
    await tester.pumpAndSettle();
    expect(managed, isTrue);
  });

  for (final brightness in Brightness.values) {
    for (final width in [320.0, 390.0, 1440.0]) {
      testWidgets('$brightness agent search at $width pixels has no overflow', (
        tester,
      ) async {
        await pumpPanel(tester, agents, width: width, brightness: brightness);
        await tester.enterText(find.byType(TextField), 'coordinator');
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.tap(find.widgetWithText(ChoiceChip, 'Working'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.tap(find.byTooltip('Clear search'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      });
    }
  }
}
