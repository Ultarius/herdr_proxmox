import 'package:flutter_test/flutter_test.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/task_activity.dart';

void main() {
  test('validation uses the candidate until the task is merged', () {
    final task = <String, dynamic>{
      'state': 'pr_open',
      'head_sha': 'candidate',
      // An old or stray merge identity must not change the validation target.
      'merge_sha': 'merge',
      'validation_policy': {'target': 'candidate', 'matches': true},
      'builds': {
        'candidate': {
          'target': 'candidate',
          'state': 'complete',
          'required_checks_verified': true,
        },
      },
    };
    expect(taskValidationTarget(task), 'candidate');
    expect(taskNextStep(task), contains('ready for review'));
    task['state'] = 'merged';
    expect(taskValidationTarget(task), 'merge');
    task.remove('merge_sha');
    expect(taskValidationTarget(task), isNull);
  });

  testWidgets(
    'superseded chat reference is visible without expanding raw audit',
    (tester) async {
      final entry = {
        'action': 'delivery_superseded',
        'actor': 'gateway',
        'at': 'now',
        'head_sha': 'b' * 40,
        'previous_sha': 'a' * 40,
        'delivery_chat_id': 'inspection-old',
      };
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: TaskActivity(
                task: {
                  'audit': [entry],
                },
              ),
            ),
          ),
        ),
      );
      expect(
        find.textContaining('Superseded aaaaaaaa · chat inspection-old'),
        findsOneWidget,
      );
      expect(find.text('$entry'), findsNothing);
    },
  );

  test(
    'a previous candidate delivery error does not replace current publication',
    () {
      final task = {
        'state': 'published',
        'head_sha': 'current',
        'await_verification': true,
        'publish': {'head_sha': 'current'},
        'builds': {
          'current': {
            'state': 'complete',
            'target': 'current',
            'required_checks_verified': true,
          },
        },
        'delivery_workflow': {
          'sha': 'previous',
          'state': 'attention',
          'error': 'Old failure',
        },
      };
      expect(
        taskNextStep(task),
        'Branch published; open a draft pull request.',
      );
      (task['delivery_workflow'] as Map)['sha'] = 'current';
      expect(
        taskNextStep(task),
        contains('Delivery needs attention: Old failure'),
      );
    },
  );

  test(
    'delivery states retain the agent and do not claim review readiness',
    () {
      final task = {
        'state': 'pr_open',
        'head_sha': 'sha',
        'auto_publish': true,
        'delivery_workflow': <String, dynamic>{
          'state': 'waiting_agent',
          'sha': 'sha',
        },
      };
      expect(taskNextStep(task), contains('task agent to inspect'));
      (task['delivery_workflow'] as Map)['state'] = 'waiting_remote';
      expect(taskNextStep(task), contains('GitHub mergeability and CI'));
      (task['delivery_workflow'] as Map)['state'] = 'complete';
      expect(taskNextStep(task), contains('checked by the task agent'));
      (task['delivery_workflow'] as Map)['state'] = 'attention';
      (task['delivery_workflow'] as Map)['error'] = 'PR permission denied';
      expect(taskNextStep(task), contains('PR permission denied'));
    },
  );

  test(
    'implementing distinguishes working from capture being switched off',
    () {
      final working = {'state': 'implementing', 'auto_validate': true};
      expect(
        taskNextStep(working, executor: 'service'),
        'Implementation in progress; awaiting a committed candidate.',
      );
      // Capture runs independently of the validation executor.
      expect(
        taskNextStep(working, executor: 'gateway'),
        'Implementation in progress; awaiting a committed candidate.',
      );
    },
  );

  test('a task with capture off never claims the agent is still working', () {
    final idle = {'state': 'implementing', 'auto_validate': false};
    final noService = taskNextStep(idle, executor: 'unavailable');
    expect(noService, contains('Automatic capture is off for this task'));
    expect(noService, contains('Changes'));
    final withService = taskNextStep(idle, executor: 'service');
    expect(withService, contains('Automatic capture is off for this task'));
    expect(withService, isNot(contains('awaiting a committed candidate')));
  });

  test(
    'capture and validation flags independently describe implementation',
    () {
      expect(
        taskNextStep({
          'state': 'implementing',
          'auto_capture': true,
          'auto_validate': false,
        }, executor: 'gateway'),
        'Implementation in progress; awaiting a committed candidate.',
      );
      expect(
        taskNextStep({
          'state': 'implementing',
          'auto_capture': false,
          'auto_validate': true,
        }, executor: 'service'),
        contains('Automatic capture is off'),
      );
    },
  );

  test('other states are unaffected by the executor', () {
    final completed = {
      'state': 'completed',
      'completion': {'outcome': 'complete'},
    };
    expect(
      taskNextStep(completed, executor: 'unavailable'),
      taskNextStep(completed, executor: 'service'),
    );
  });

  test('a build for another target never validates the current candidate', () {
    const head = 'b';
    const base = 'a';
    final task = {
      'state': 'review_ready',
      'head_sha': head,
      'builds': {
        // Keyed by the current candidate, but the record's own target is the
        // base: a stale build must not report this candidate as validated.
        head: {
          'state': 'complete',
          'target': base,
          'required_checks_verified': true,
        },
      },
    };
    final step = taskNextStep(task);
    expect(step, isNot(contains('validated')));
    expect(step, isNot(contains('ready for review')));
    // The same build keyed and targeted at the candidate does validate it.
    final exact = {
      'state': 'review_ready',
      'head_sha': head,
      'builds': {
        head: {
          'state': 'complete',
          'target': head,
          'required_checks_verified': true,
        },
      },
    };
    expect(taskNextStep(exact), contains('ready for review'));
  });
  test(
    'historical checks do not claim readiness under a changed or unknown policy',
    () {
      final head = 'a' * 40;
      final task = <String, dynamic>{
        'state': 'pr_open',
        'head_sha': head,
        'builds': {
          head: {
            'target': head,
            'state': 'complete',
            'required_checks_verified': true,
          },
        },
        'validation_policy': {
          'target': head,
          'matches': false,
          'state': 'stale',
          'reason': 'Policy changed. Validate latest commit.',
        },
      };
      expect(taskValidationNeedsRecheck(task, head), true);
      expect(taskNextStep(task), 'Policy changed. Validate latest commit.');
      task['validation_policy'] = {
        'target': head,
        'matches': false,
        'state': 'unavailable',
        'reason': 'Current policy could not be assessed.',
      };
      expect(taskNextStep(task), 'Current policy could not be assessed.');
      task['validation_policy'] = {'target': 'b' * 40, 'matches': true};
      expect(taskValidationNeedsRecheck(task, head), true);
      expect(taskNextStep(task), contains('another commit'));
      task['validation_policy'] = {'target': head, 'matches': true};
      expect(taskValidationNeedsRecheck(task, head), false);
      expect(taskNextStep(task), contains('ready for review'));
    },
  );
}
