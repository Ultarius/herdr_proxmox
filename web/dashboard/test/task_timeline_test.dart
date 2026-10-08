import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/task_timeline.dart';

void main() {
  for (final brightness in Brightness.values) {
    for (final width in [320.0, 390.0]) {
      testWidgets(
        'vertical merge graph and exact build badges at $width $brightness',
        (tester) async {
          tester.view.physicalSize = Size(width, 900);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
        final base = 'a' * 40,
            worker = 'b' * 40,
            upstream = 'c' * 40,
            candidate = 'd' * 40;
        String? selected;
        await tester.pumpWidget(
          MaterialApp(
            theme: ThemeData(brightness: brightness),
            home: Scaffold(
              body: SingleChildScrollView(
                child: TaskTimeline(
                  onSelect: (sha) => selected = sha,
                  task: {
                    'base_sha': base,
                    'head_sha': candidate,
                    'commit_graph': [
                      {
                        'sha': candidate,
                        'parents': [worker, upstream],
                        'subject': 'Merge upstream',
                      },
                      {
                        'sha': worker,
                        'parents': [base],
                        'subject': 'Implementation',
                        'refs': 'herdr/task-abcdefabcdef',
                      },
                      {
                        'sha': upstream,
                        'parents': [base],
                        'subject': 'Upstream fixes',
                      },
                      {'sha': base, 'parents': [], 'subject': 'Initial base'},
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
          ),
        );
        expect(find.text('Base'), findsOneWidget);
        expect(find.text('Candidate'), findsOneWidget);
        expect(find.text('Checks verified'), findsOneWidget);
        expect(find.text('Checks failed'), findsOneWidget);
        expect(find.text('herdr/task-abcdefabcdef'), findsOneWidget);
        await tester.tap(find.text('Merge upstream'));
        expect(selected, candidate);
        expect(tester.takeException(), isNull);
      },
    );
    }
  }
  testWidgets(
    'missing history shows a read-only refresh instead of floating nodes',
    (tester) async {
      var refreshed = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: TaskTimeline(
              onRefresh: () => refreshed = true,
              task: const {'base_sha': 'aaaaaaaa', 'head_sha': 'bbbbbbbb'},
            ),
          ),
        ),
      );
      expect(find.text('Commit history unavailable'), findsOneWidget);
      expect(find.text('Candidate'), findsNothing);
      await tester.tap(find.text('Refresh history'));
      expect(refreshed, isTrue);
      expect(tester.takeException(), isNull);
    },
  );
}
