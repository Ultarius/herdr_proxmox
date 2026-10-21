import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard_state/task_validation_environment.dart';

void main() {
  final task = <String, dynamic>{
    'state': 'review_ready',
    'head_sha': 'candidate',
    'run_id': 'run',
    'builds': {
      'candidate': {
        'blocker': 'policy_changed',
        'recovery': 'review_validation_environment',
      },
    },
  };

  test(
    'review eligibility agrees with the server and uses recovery metadata',
    () {
      for (final state in ['review_ready', 'published', 'pr_open']) {
        expect(
          needsValidationEnvironmentReview({...task, 'state': state}),
          isTrue,
        );
      }
      for (final state in [
        'implementing',
        'queued',
        'merged',
        'closed',
        'completed',
      ]) {
        expect(
          needsValidationEnvironmentReview({...task, 'state': state}),
          isFalse,
        );
      }
      for (final recovery in [
        null,
        'review_protected_inputs',
        'retry_validation',
      ]) {
        expect(
          needsValidationEnvironmentReview({
            ...task,
            'builds': {
              'candidate': {
                'blocker': 'policy_changed',
                'recovery': recovery,
                'note':
                    'The approved environment differs from the session policy',
              },
            },
          }),
          isFalse,
        );
      }
      for (final state in ['approved', 'unavailable']) {
        final updated = {
          ...task,
          'validation_environment_status': {'state': state},
        };
        expect(needsValidationEnvironmentReview(updated), isFalse);
        expect(hasApprovedValidationEnvironment(updated), state == 'approved');
      }
      expect(
        needsValidationEnvironmentReview({
          ...task,
          'validation_environment_status': {'state': 'review_required'},
        }),
        isTrue,
      );
    },
  );

  test('missing review displays a useful gateway error', () async {
    await expectLater(
      reviewValidationEnvironment(
        (path, [body]) async => {
          'message': 'Review the changed candidate first',
        },
        'task',
        'request',
      ),
      throwsA(
        isA<StateError>().having(
          (e) => e.message,
          'message',
          'Review the changed candidate first',
        ),
      ),
    );
  });

  test(
    'review identifies the execution and omits empty evidence punctuation',
    () {
      final text = validationEnvironmentReviewText({
        'candidate': 'sha',
        'candidate_revision': 'revision',
        'run_id': 'execution',
        'prior_approval': 'approval',
        'recipes': [
          {
            'cwd': '.',
            'arguments': ['test'],
          },
        ],
      });
      expect(text, contains('Candidate revision: revision'));
      expect(text, contains('Task execution: execution'));
      expect(text, contains('Prior approval: approval'));
      expect(text, isNot(contains(' · ')));
      expect(text, contains('After reloading, review again'));
    },
  );
}
