import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/task_timeline.dart';

void main() {
  for (final brightness in Brightness.values) {
    testWidgets(
      'timeline preserves merge parents and scrolls on mobile $brightness',
      (tester) async {
        tester.view.physicalSize = const Size(390, 800);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final base = 'a' * 40,
            worker = 'b' * 40,
            upstream = 'c' * 40,
            candidate = 'd' * 40;
        await tester.pumpWidget(
          MaterialApp(
            theme: ThemeData(brightness: brightness),
            home: Scaffold(
              body: TaskTimeline(
                task: {
                  'base_sha': base,
                  'head_sha': candidate,
                  'commit_graph': [
                    {
                      'sha': candidate,
                      'parents': [worker, upstream],
                    },
                    {
                      'sha': worker,
                      'parents': [base],
                    },
                    {
                      'sha': upstream,
                      'parents': [base],
                    },
                  ],
                  'builds': {
                    worker: {
                      'state': 'complete',
                      'required_checks_verified': true,
                    },
                    candidate: {'state': 'failed'},
                  },
                },
              ),
            ),
          ),
        );
        expect(find.text('Base'), findsOneWidget);
        expect(find.text('Candidate'), findsOneWidget);
        expect(
          find.textContaining('Completion receipt: not verified'),
          findsOneWidget,
        );
        final icons = tester
            .widgetList<Icon>(find.byIcon(Icons.circle_outlined))
            .toList();
        expect(icons.where((i) => i.color == Colors.green), hasLength(1));
        expect(
          icons.where(
            (i) =>
                i.color == ThemeData(brightness: brightness).colorScheme.error,
          ),
          hasLength(1),
        );
        await tester.drag(
          find.byType(SingleChildScrollView),
          const Offset(-300, 0),
        );
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      },
    );
  }
  testWidgets('legacy candidate has no invented parent edge', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: TaskTimeline(
            task: {'base_sha': 'aaaaaaaa', 'head_sha': 'bbbbbbbb'},
          ),
        ),
      ),
    );
    expect(find.textContaining('load its parent edges'), findsOneWidget);
    expect(find.text('Candidate'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
