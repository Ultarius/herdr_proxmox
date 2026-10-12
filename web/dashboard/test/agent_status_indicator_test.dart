import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/agent_status_indicator.dart';

void main() {
  test(
    'activity belongs to the verified profile and handles missing sessions',
    () {
      for (final entry in {
        'idle': 'idle',
        'done': 'idle',
        'working': 'working',
        'blocked': 'attention',
        'thinking': 'working',
        'unknown': 'offline',
      }.entries) {
        expect(
          agentActivity('lead', [
            {'profile_id': 'lead', 'agent_status': entry.key},
          ]),
          entry.value,
        );
      }
      expect(
        agentActivity('lead', [
          {'profile_id': 'other', 'agent_status': 'working'},
        ]),
        'offline',
      );
    },
  );

  testWidgets(
    'working and attention pulse, idle stays green, reduced motion stops animation',
    (tester) async {
      Future<void> render(String activity, {bool reduced = false}) =>
          tester.pumpWidget(
            MaterialApp(
              home: MediaQuery(
                data: MediaQueryData(disableAnimations: reduced),
                child: Center(child: AgentStatusIndicator(activity: activity)),
              ),
            ),
          );
      BoxDecoration decoration() =>
          tester
                  .widget<Container>(
                    find.descendant(
                      of: find.byType(AgentStatusIndicator),
                      matching: find.byType(Container),
                    ),
                  )
                  .decoration!
              as BoxDecoration;
      await render('working');
      expect(find.byTooltip('Working'), findsOneWidget);
      expect(decoration().color, const Color(0xff3a93ff));
      final initial = decoration().boxShadow!.single.spreadRadius;
      await tester.pump(const Duration(milliseconds: 450));
      expect(decoration().boxShadow!.single.spreadRadius, greaterThan(initial));
      await render('attention');
      expect(find.byTooltip('Attention required'), findsOneWidget);
      expect(decoration().color, const Color(0xffffa14d));
      await render('idle');
      await tester.pumpAndSettle();
      expect(decoration().color, const Color(0xff56c592));
      expect(decoration().boxShadow, isNull);
      await render('working', reduced: true);
      await tester.pumpAndSettle();
      final stopped = decoration().boxShadow!.single.spreadRadius;
      await tester.pump(const Duration(milliseconds: 450));
      expect(decoration().boxShadow!.single.spreadRadius, stopped);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
