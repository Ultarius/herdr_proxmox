@TestOn('browser')
library;

import 'package:jaspr/client.dart';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/pages/ventures_studio.dart';

// The Studio tab is a pure function of the gateway's studio summary, so the
// first-run states (nothing set up, Herdr stopped, a named failure) are
// mounted directly instead of through a second page fixture.

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 50));

Iterable<web.HTMLElement> nodes(String selector) sync* {
  final values = web.document.querySelectorAll('#studio-test $selector');
  for (var i = 0; i < values.length; i++) {
    yield values.item(i) as web.HTMLElement;
  }
}

web.HTMLButtonElement button(String label) =>
    nodes('button').firstWhere((item) => item.textContent!.trim() == label)
        as web.HTMLButtonElement;

void main() {
  late ClientAppBinding binding;
  late List<String> actions;
  late List<String> calls;

  Future<void> mount(
    Map<String, dynamic>? studio, {
    String herdr = 'running',
    List<Map<String, dynamic>> runtimes = const [],
  }) async {
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'studio-test');
    binding = ClientAppBinding();
    binding.attachRootComponent(
      venturesStudio(
        studio: studio,
        candidates: const [],
        organizationName: 'Product organization',
        busy: false,
        onSetup: () => calls.add('setup'),
        onConfigure: () => calls.add('configure'),
        onRunPortfolio: () => calls.add('portfolio'),
        onRunScout: () => calls.add('scout'),
        onPause: (_) {},
        onRecover: (_) => calls.add('recover'),
        onDismiss: (_) => calls.add('dismiss'),
        onRecommendation: (_) {},
        onAction: (action, _) => actions.add(action),
        onRunScoutAnyway: () => calls.add('scout-anyway'),
        onStartTeam: () => calls.add('team'),
        herdr: herdr,
        runtimes: runtimes,
      ),
      attachTo: '#studio-test',
    );
    await settle();
  }

  setUp(() {
    actions = [];
    calls = [];
  });

  tearDown(() {
    binding.detachRootComponent();
    web.document.getElementById('studio-test')!.remove();
  });

  String page() => web.document.querySelector('#studio-test')!.textContent!;

  Map<String, dynamic> configured({
    Map<String, dynamic> readiness = const {},
    List<Map<String, dynamic>> runs = const [],
    Map<String, dynamic> scout = const {'cadence': 'off'},
    String mode = 'proposal',
  }) => {
    'organization_id': 'org',
    'mode': mode,
    'policy': {
      'studio_portfolio_cadence': 'weekly',
      'studio_scout_cadence': 'off',
      'studio_sources': <String>[],
      'studio_auto_min_confidence': 70,
    },
    'studio': {'group_id': 'studio-group', 'notebook': 'ventures/studio-org'},
    'schedule': {
      'portfolio': {'cadence': 'weekly', 'next_at': '2026-11-20T06:00:00Z'},
      'scout': scout,
    },
    'readiness': readiness,
    'runs': runs,
    'budget': <String, dynamic>{},
  };

  test('a fresh installation is offered the studio, not an organization', () async {
    await mount(
      null,
      herdr: 'stopped',
      runtimes: const [
        {'runtime': 'codex', 'name': 'Codex', 'ready': true, 'reason': ''},
      ],
    );
    expect(page(), isNot(contains('Error on building component')));
    expect(page(), contains('No AI venture proposals yet'));
    expect(page(), contains('You do not need to create an organization'));
    expect(page(), contains('Codex ready.'));
    expect(page(), contains('Stopped: no agent can start.'));
    button('Start Herdr').click();
    button('Set up Venture Studio').click();
    await settle();
    expect(actions, ['start_herdr']);
    expect(calls, ['setup']);
  });

  test('a stopped Herdr disables runs and offers its own action', () async {
    await mount(
      configured(
        readiness: const {
          'herdr': 'stopped',
          'can_run': false,
          'can_scout': false,
          'coverage': {'sources': 1, 'focus': 1},
          'checks': [
            {
              'id': 'herdr',
              'label': 'Herdr is running',
              'ok': false,
              'detail': 'Stopped: no agent can start.',
              'action': 'start_herdr',
            },
            {
              'id': 'team',
              'label': 'The studio team exists',
              'ok': true,
              'detail': 'Notebook, four agents and one read-only group.',
              'action': '',
            },
          ],
        },
        scout: const {
          'cadence': 'daily',
          'next_at': '2026-11-16T07:00:00Z',
          'error': '',
          'failures': 0,
          'waiting': 'Herdr is stopped. Start Herdr to run the scout review.',
        },
      ),
    );
    expect(page(), isNot(contains('Error on building component')));
    expect(button('Run scout now').disabled, isTrue);
    expect(button('Run portfolio now').disabled, isTrue);
    // Waiting is shown as waiting, never as a failed attempt.
    expect(page(), contains('waiting, nothing was started: Herdr is stopped'));
    expect(page(), isNot(contains('last scheduled attempt failed')));
    button('Start Herdr').click();
    await settle();
    expect(actions, ['start_herdr']);
  });

  test('a scout without sources needs an explicit override', () async {
    await mount(
      configured(
        readiness: const {
          'can_run': true,
          'can_scout': false,
          'can_scout_override': true,
          'coverage': {'sources': 0, 'focus': 1},
          'checks': [
            {
              'id': 'sources',
              'label': 'Scouting sources',
              'ok': false,
              'detail': 'None: the scout would have no market evidence to cite.',
              'action': 'configure',
            },
          ],
        },
      ),
    );
    expect(page(), contains('Evidence coverage: 0 source(s), 1 focus area(s)'));
    expect(button('Run scout now').disabled, isTrue);
    expect(button('Run portfolio now').disabled, isFalse);
    button('Run scout without sources').click();
    button('Configure studio').click();
    await settle();
    expect(calls, ['scout-anyway', 'configure']);
  });

  test('without a focus area the override is not offered', () async {
    // A scout without sources researches the web; without a focus it would
    // not know where to look, so the gateway refuses that run.
    await mount(
      configured(
        readiness: const {
          'can_run': true,
          'can_scout': false,
          'can_scout_override': false,
          'coverage': {'sources': 0, 'focus': 0},
          'checks': [
            {
              'id': 'sources',
              'label': 'Scouting sources',
              'ok': false,
              'optional': true,
              'detail': 'None. The scout researches the web instead.',
              'action': 'configure',
            },
            {
              'id': 'focus',
              'label': 'Focus areas',
              'ok': false,
              'detail': 'None: the scout would not know where to look.',
              'action': 'configure',
            },
          ],
        },
      ),
    );
    expect(button('Run scout now').disabled, isTrue);
    expect(
      nodes('button').where(
        (item) => item.textContent!.trim() == 'Run scout without sources',
      ),
      isEmpty,
    );
    // Missing sources are a suggestion here; the missing focus needs the operator.
    expect(page(), contains('Optional'));
    expect(page(), contains('Needs you'));
  });

  test('a failed run names its cause, its phase, its agent and one action', () async {
    await mount(
      configured(
        readiness: const {'can_run': true, 'can_scout': true},
        mode: 'autonomous',
        runs: const [
          {
            'id': '33333333333333333333333333333333',
            'kind': 'scout',
            'state': 'needs_attention',
            'error': 'Venture Scout: session preparation failed; inspect its run',
            'failure': {
              'category': 'team',
              'phase': 'preparation',
              'title': 'A studio agent did not start',
              'action': 'repair',
              'help': 'Repair releases the failed session and starts the team again.',
              'message': 'Venture Scout: session preparation failed; inspect its run',
              'agent': 'Venture Scout',
              'group_id': 'studio-group',
            },
          },
          {
            'id': '44444444444444444444444444444444',
            'kind': 'portfolio',
            'state': 'meeting',
            'waiting': {
              'category': 'permission',
              'title': 'An agent is waiting for a decision',
              'action': 'open_group',
              'help': 'Open the studio group discussion and answer the prompt.',
              'message': 'Studio facilitator asks to read evidence.json',
            },
          },
        ],
      ),
    );
    expect(page(), isNot(contains('Error on building component')));
    expect(page(), contains('Needs attention (1)'));
    expect(
      page(),
      contains('A studio agent did not start · while the agents were starting · Venture Scout'),
    );
    expect(page(), contains('The portfolio meeting is waiting: An agent is waiting for a decision'));
    expect(page(), contains('Autonomous: the studio creates ventures within the caps'));
    button('Open the studio group').click();
    await settle();
    expect(actions, ['open_group']);
  });
}
