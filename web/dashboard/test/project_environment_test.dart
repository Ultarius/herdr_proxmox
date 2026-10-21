import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard_state/project_analysis.dart';
import 'package:herdr_dashboard_state/project_environment.dart';

Map<String, dynamic> proposal(String digest) => {
  'digest': digest,
  'requirements': [
    {
      'capability': 'node',
      'state': 'ready',
      'components': ['tools'],
    },
  ],
  'check_candidates': [],
  'selected_checks': [],
};
Map<String, dynamic> saved({
  String revision = 'head',
  Map? plan,
  Map? job,
  List<Map>? active,
  bool ready = false,
}) => {
  'project': 'repo',
  'revision': revision,
  'plan_current': true,
  'plan': plan ?? proposal('B'),
  'job': job,
  'active_jobs': active ?? [],
  'prepared': ready
      ? {
          'current': true,
          'revision': revision,
          'requirements': [],
          'selected_check_count': 2,
        }
      : null,
};

void main() {
  ProjectEnvironmentController controller(ProjectAnalysisRequest request) {
    final result = ProjectEnvironmentController(
      request: request,
      onUpdate: () {},
      mounted: () => true,
      connectionGeneration: () => 1,
    );
    addTearDown(result.dispose);
    return result;
  }

  testWidgets(
    'all active plans remain visible and polled across proposal edits',
    (tester) async {
      final calls = <String>[];
      final first = {'id': 'A-job', 'digest': 'A', 'state': 'running'};
      final second = {'id': 'C-job', 'digest': 'C', 'state': 'queued'};
      final state = controller((path, [body]) async {
        calls.add(path);
        if (body != null) return proposal('new-${path.split('/').last}');
        if (path.contains('?job='))
          return path.endsWith('A-job') ? first : second;
        return saved(active: [first, second]);
      });
      await state.load('repo');
      expect(state.plan!['digest'], 'B');
      expect(state.job, isNull);
      for (final operation in ['plan', 'select', 'authorize-restore']) {
        await state.action(operation, checks: ['test']);
        expect(state.activeJobs.map((value) => value['id']), [
          'A-job',
          'C-job',
        ]);
        await tester.pump(const Duration(seconds: 2));
      }
      expect(calls.where((path) => path.endsWith('?job=A-job')), hasLength(3));
      expect(calls.where((path) => path.endsWith('?job=C-job')), hasLength(3));
      expect(state.lookupError, isNull);
      state.dispose();
    },
  );

  testWidgets('failed refresh clears controls and stops active polling', (
    tester,
  ) async {
    var fail = false, polls = 0;
    final job = {'id': 'job', 'state': 'running', 'digest': 'B'};
    final state = controller((path, [body]) async {
      if (path.contains('?job=')) {
        polls++;
        return job;
      }
      if (fail) throw StateError('status storage failed');
      return saved(job: job, active: [job]);
    });
    await state.load('repo');
    fail = true;
    await state.load('repo');
    expect(state.lookupError, contains('status storage failed'));
    expect(state.status, isNull);
    expect(state.plan, isNull);
    expect(state.job, isNull);
    expect(state.activeJobs, isEmpty);
    await tester.pump(const Duration(seconds: 4));
    expect(polls, 0);
  });

  testWidgets(
    'failed action is separate from lookup failure and keeps the existing run',
    (tester) async {
      var polls = 0;
      final job = {'id': 'job', 'state': 'running', 'digest': 'A'};
      final state = controller((path, [body]) async {
        if (body != null) throw StateError('preparation denied');
        if (path.contains('?job=')) {
          polls++;
          return job;
        }
        return saved(active: [job]);
      });
      await state.load('repo');
      await state.action('prepare');
      expect(state.actionError, contains('prepare'));
      expect(state.actionError, contains('preparation denied'));
      expect(state.lookupError, isNull);
      expect(state.status, isNotNull);
      expect(state.activeJobs, hasLength(1));
      await tester.pump(const Duration(seconds: 2));
      expect(polls, 1);
      state.dispose();
    },
  );

  for (final terminal in ['ready', 'cancelled', 'failed']) {
    testWidgets(
      'a terminal $terminal action response reloads saved status immediately',
      (tester) async {
        var reads = 0, prepared = false;
        final state = controller((path, [body]) async {
          if (body != null) {
            prepared = terminal == 'ready';
            return {'id': 'job', 'state': terminal, 'digest': 'B'};
          }
          reads++;
          return saved(ready: prepared);
        });
        await state.load('repo');
        await state.action('prepare');
        expect(reads, 2);
        expect(state.status!['prepared'] != null, terminal == 'ready');
        expect(state.loading, isFalse);
        expect(state.busy, isFalse);
      },
    );
  }

  test('a slow environment lookup cannot delay repository analysis', () async {
    final waiting = Completer<Map<String, dynamic>>();
    final state = controller((path, [body]) => waiting.future);
    final loading = state.load('repo');
    final report = await loadProjectAnalysis((path, [body]) async {
      expect(path, startsWith('projects/analysis?'));
      return {'requirements': [], 'components': []};
    }, 'repo');
    expect(report.report['components'], isEmpty);
    expect(state.loading, isTrue);
    waiting.complete(saved());
    await loading;
  });

  test('A to B to A ignores the old A environment response', () async {
    final old = Completer<Map<String, dynamic>>();
    var count = 0;
    final state = controller((path, [body]) async {
      if (++count == 1) return old.future;
      return saved(revision: 'new-$count');
    });
    final previous = state.load('A');
    await state.load('B');
    await state.load('A');
    old.complete(saved(revision: 'old-A'));
    await previous;
    expect(state.status!['revision'], 'new-3');
  });

  test(
    'reconnecting resets an abandoned action before loading the same project',
    () async {
      var epoch = 1;
      final pending = Completer<Map<String, dynamic>>();
      final state = ProjectEnvironmentController(
        request: (path, [body]) async =>
            body != null ? pending.future : saved(revision: 'epoch-$epoch'),
        onUpdate: () {},
        mounted: () => true,
        connectionGeneration: () => epoch,
      );
      addTearDown(state.dispose);
      await state.load('repo');
      final previous = state.action('prepare');
      expect(state.busy, isTrue);
      epoch++;
      await state.load('repo');
      pending.complete({'id': 'old-job', 'state': 'ready'});
      await previous;
      expect(state.busy, isFalse);
      expect(state.status!['revision'], 'epoch-2');
      expect(state.job, isNull);
    },
  );

  for (final invalid in [
    saved()..['plan'] = ['bad'],
    saved()..['active_jobs'] = 'bad',
    saved()..['job'] = {'state': 'ready'},
    saved(ready: true)
      ..['prepared'] = {
        'current': true,
        'revision': 'head',
        'requirements': 'bad',
      },
    saved(
      plan: proposal('B')
        ..['check_candidates'] = [
          {'arguments': 'not a list'},
        ],
    ),
    saved(plan: proposal('B')..['restore_permission'] = 'bad'),
  ]) {
    test(
      'malformed saved data becomes unavailable without throwing during rendering: $invalid',
      () async {
        final state = controller((path, [body]) async => invalid);
        await state.load('repo');
        expect(state.lookupError, isNotNull);
        expect(state.status, isNull);
        expect(state.plan, isNull);
      },
    );
  }

  test(
    'ready requirements, activation diagnostics and both commit identities are readable',
    () {
      expect(
        preparationRequirementSummary(
          {
            'state': 'ready',
            'reason': 'SDK was found',
            'activation_reason': 'Adapter is available',
          },
          proposal('B'),
          null,
        ),
        allOf(
          contains('SDK available'),
          contains('SDK was found'),
          contains('Adapter is available'),
        ),
      );
      final view = ProjectEnvironmentView({
        'revision': 'new-head',
        'prepared': {
          'current': false,
          'revision': 'old-head',
          'selected_check_count': 1,
        },
        'pending_plan': true,
      });
      expect(view.revision, 'new-head');
      expect(view.preparedRevision, 'old-head');
      expect(view.checks, '1 approved validation check');
      expect(view.pendingPlanDetail, contains('newer plan'));
    },
  );
}
