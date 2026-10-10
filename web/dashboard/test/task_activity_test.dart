import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/task_activity.dart';

void main() {
  test('implementing distinguishes working from capture being switched off', () {
    final working = {'state': 'implementing', 'auto_validate': true};
    expect(
      taskNextStep(working, executor: 'service'),
      'Implementation in progress; awaiting a committed candidate.',
    );
    // Capture is on but its executor is gone, so nothing will ever arrive.
    expect(
      taskNextStep(working, executor: 'gateway'),
      contains('waiting for the durable build service'),
    );
  });

  test('a task with capture off never claims the agent is still working', () {
    final idle = {'state': 'implementing', 'auto_validate': false};
    final noService = taskNextStep(idle, executor: 'unavailable');
    expect(noService, contains('the durable build service is not installed'));
    expect(noService, contains('Changes'));
    final withService = taskNextStep(idle, executor: 'service');
    expect(withService, contains('Automatic capture is off for this task'));
    expect(withService, isNot(contains('awaiting a committed candidate')));
  });

  test('other states are unaffected by the executor', () {
    final completed = {'state': 'completed', 'completion': {'outcome': 'complete'}};
    expect(taskNextStep(completed, executor: 'unavailable'),
        taskNextStep(completed, executor: 'service'));
  });
}
