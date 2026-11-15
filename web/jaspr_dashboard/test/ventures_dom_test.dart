@TestOn('browser')
library;

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:test/test.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/pages/ventures_page.dart';
import 'package:herdr_jaspr_dashboard/services.dart';

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 50));
Iterable<web.HTMLElement> nodes(String selector) sync* {
  final values = web.document.querySelectorAll('#ventures-test $selector');
  for (var i = 0; i < values.length; i++)
    yield values.item(i) as web.HTMLElement;
}

Future<void> click(String label) async {
  nodes(
    'button',
  ).firstWhere((item) => item.textContent!.contains(label)).click();
  await settle();
}

void main() {
  late DashboardBloc connection;
  late ClientAppBinding binding;
  late List<Map<String, dynamic>> requests;
  final snapshot = {
    'organizations': [
      {'id': 'org', 'name': 'Product organization'},
    ],
    'ventures': [
      {
        'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'organization_id': 'org',
        'name': 'Settlement Desk',
        'state': 'draft',
        'capital': 100000,
        'currency': 'JPY',
        'last_tick': {
          'simulated_date': '2026-11-15',
          'state': 'ready',
          'ledger': {'score': null, 'runway_days': 99},
        },
      },
    ],
    'selected': {
      'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      'organization_id': 'org',
      'name': 'Settlement Desk',
      'state': 'qualified',
      'capital': 100000,
      'currency': 'JPY',
      'score': 64,
      'qualifying_days': 3,
      'required_days': 3,
      'survival_threshold': 60,
      'roles': {
        'analyst': ['Analyst'],
        'founder': ['Founder'],
        'red_team': ['Red Team'],
        'outreach': ['Analyst'],
      },
      'notebook': 'ventures/settlement',
      'group_id': 'g',
      'pricing': '48000 JPY per month',
      'created_at': '2026-11-15T10:00:00Z',
      'configuration': {
        'sources': {
          'feeds': ['https://news.example/feed'],
          'tags': <String>[],
          'keywords': <String>[],
        },
        'market': {'currency': 'JPY', 'geography': 'Japan'},
        'simulation': {'architecture': 'api'},
        'outreach': {'language': 'ja'},
      },
    },
    'groups': [
      {'id': 'g', 'name': 'Venture team', 'organization_id': 'org'},
    ],
    'projects_root': '/projects',
    'limits': {'max_active_ventures': 3},
    'ticks': [
      {
        'id': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        'simulated_date': '2026-11-15',
        'simulated_at': '2026-11-15T10:00:00Z',
        'state': 'ready',
        'events': [
          {
            'evidence': ['N1'],
            'summary': 'Demand rises',
            'direction': 'tailwind',
          },
        ],
        'product_changes': [
          {
            'kind': 'add',
            'title': 'Japanese onboarding',
            'reason': 'Cited change.',
            'evidence': ['N1'],
          },
        ],
        'red_team': [
          {'objection': 'Sales cycle is long', 'severity': 3},
        ],
        'outreach': [],
        'ledger': {
          'capital_before': 101000,
          'burn': 1000,
          'capital_after': 99000,
          'score': 70,
          'runway_days': 99,
        },
      },
      {
        'id': 'dddddddddddddddddddddddddddddddd',
        'simulated_date': '2026-11-14',
        'simulated_at': '2026-11-14T10:00:00Z',
        'state': 'ready',
        'events': [],
        'product_changes': [],
        'red_team': [],
        'outreach': [],
        'ledger': {
          'capital_before': 102000,
          'burn': 1000,
          'capital_after': 98000,
          'score': 62,
          'runway_days': 98,
        },
      },
    ],
    'outreach': [
      {
        'id': 'cccccccccccccccccccccccccccccccc',
        'date': '2026-11-15',
        'persona': 'cfo',
        'language': 'ja',
        'subject': 'Settlement question',
        'body': '{{company}} {{name}}様',
        'evidence': ['N1'],
        'score': 64,
        'state': 'draft',
      },
    ],
    'reviews': [
      {
        'simulated_date': '2026-11-15',
        'kind': 'news',
        'state': 'ready',
        'reason': '',
      },
    ],
    'candidates': [
      {
        'id': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
        'organization_id': 'org',
        'name': 'Carrier Desk',
        'thesis': 'Freight forwarders lose margin on invoice exceptions.',
        'product': 'Invoice reconciliation.',
        'pricing': '\$600 per month',
        'state': 'proposed',
        'confidence': 70,
        'assumptions': ['Carriers share invoice data.'],
        'kill_criteria': ['No pilot after 90 days.'],
        'evidence': ['V1'],
        'configuration': {
          'market': {'currency': 'USD'},
          'simulation': {
            'survival_threshold': 60,
            'consecutive_days': 3,
            'red_team': 5,
            'capital': 36000,
            'daily_ticks': false,
            'burn': [
              {'label': 'hosting', 'amount': 3000, 'currency': 'USD'},
            ],
          },
        },
        'scout_adjusted': {
          'survival_threshold': {'proposed': 0, 'used': 60},
        },
      },
      // Decided and foreign candidates must not count as waiting ideas.
      {
        'id': '11111111111111111111111111111111',
        'organization_id': 'org',
        'name': 'Rejected Desk',
        'state': 'rejected',
      },
      {
        'id': '22222222222222222222222222222222',
        'organization_id': 'other',
        'name': 'Foreign Desk',
        'state': 'proposed',
      },
    ],
    'studios': [
      {
        'organization_id': 'org',
        'policy': {
          'studio_portfolio_cadence': 'weekly',
          'studio_scout_cadence': 'weekly',
          'studio_max_candidates': 2,
          'studio_candidate_backlog': 6,
          'studio_sources': ['https://news.example/feed'],
          'studio_paused': false,
        },
        'studio': {
          'group_id': 'studio-group',
          'notebook': 'ventures/studio-org',
        },
        'schedule': {
          'portfolio': {
            'cadence': 'weekly',
            'next_at': '2026-11-20T06:00:00Z',
            'error': '',
            'failures': 0,
          },
          'scout': {
            'cadence': 'weekly',
            'next_at': '2026-11-15T07:00:00Z',
            'error': 'The venture candidate backlog is full.',
            'failures': 2,
          },
        },
        'lessons': [
          {'kind': 'venture', 'title': 'Failed Desk'},
        ],
        'runs': [
          {
            'id': 'ffffffffffffffffffffffffffffffff',
            'kind': 'portfolio',
            'state': 'ready',
            'summary': 'Keep building.',
            'recommendations': [
              {
                'venture_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'venture_name': 'Settlement Desk',
                'rank': 1,
                'recommendation': 'continue',
                'reason': 'The thesis remains coherent.',
                'evidence': ['V1'],
              },
            ],
          },
          {
            'id': '33333333333333333333333333333333',
            'kind': 'scout',
            'state': 'needs_attention',
            'error': 'Meeting limit reached.',
            'dispatch_failed': true,
          },
        ],
        'budget': {
          'daily_meetings': 0,
          'projected_agent_sessions': 0,
          'max_active_meetings': 1,
          'daily_session_cap': 20,
        },
      },
    ],
    'studio': {
      'organization_id': 'org',
      'policy': {
        'studio_portfolio_cadence': 'weekly',
        'studio_scout_cadence': 'weekly',
        'studio_max_candidates': 2,
        'studio_candidate_backlog': 6,
        'studio_sources': ['https://news.example/feed'],
        'studio_paused': false,
      },
      'studio': {'group_id': 'studio-group', 'notebook': 'ventures/studio-org'},
      'runs': [
        {
          'id': 'ffffffffffffffffffffffffffffffff',
          'kind': 'portfolio',
          'state': 'ready',
          'summary': 'Keep building.',
          'recommendations': [],
        },
      ],
      'budget': {
        'daily_meetings': 0,
        'projected_agent_sessions': 0,
        'max_active_meetings': 1,
        'daily_session_cap': 20,
      },
    },
    'feeds': [
      {
        'url': 'https://news.example/feed',
        'last_fetch': '2026-11-15T10:00:00Z',
        'last_error': '',
        'item_count': 1,
      },
    ],
  };

  setUp(() async {
    requests = [];
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/ventures') {
          if (request.method == 'POST') {
            requests.add(Map<String, dynamic>.from(jsonDecode(request.body)));
            return http.Response(
              jsonEncode({'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}),
              200,
            );
          }
          return http.Response.bytes(
            utf8.encode(jsonEncode(snapshot)),
            200,
            headers: {'content-type': 'application/json; charset=utf-8'},
          );
        }
        return http.Response(jsonEncode({'workspaces': [], 'agents': []}), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('admin');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'ventures-test');
    binding = ClientAppBinding();
    binding.attachRootComponent(
      const VenturesPage(),
      attachTo: '#ventures-test',
    );
    await settle();
  });

  tearDown(() async {
    binding.detachRootComponent();
    web.document.getElementById('ventures-test')!.remove();
    await connection.disconnect();
    BlocScope.endAll();
  });

  Future<void> until(bool Function() ready) async {
    final deadline = DateTime.now().add(const Duration(seconds: 15));
    while (!ready()) {
      if (DateTime.now().isAfter(deadline))
        fail('Ventures page did not settle.');
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
  }

  test(
    'portfolio and tick history render, and a manual tick posts one action',
    () async {
      await until(
        () => web.document
            .querySelector('#ventures-test')!
            .textContent!
            .contains('Settlement Desk'),
      );
      expect(nodes('h1').single.textContent, 'Ventures');
      expect(
        web.document.querySelector('#ventures-test')!.textContent,
        contains('Demand rises'),
      );
      expect(
        web.document.querySelector('#ventures-test')!.textContent,
        contains('Japanese onboarding'),
      );
      expect(
        web.document.querySelector('#ventures-test')!.textContent,
        contains('Sales cycle is long'),
      );
      expect(
        web.document
            .querySelectorAll(
              '#ventures-test svg[aria-label="Survival score history"]',
            )
            .length,
        1,
      );
      expect(
        web.document
            .querySelectorAll(
              '#ventures-test svg[aria-label="Capital history"]',
            )
            .length,
        1,
      );
      expect(
        web.document.querySelector('#ventures-test')!.textContent,
        contains('Promote to discovery'),
      );
      await click('Run tick now');
      await until(() => requests.isNotEmpty);
      expect(requests.single['mode'], 'run');
      expect(requests.single['venture_id'], 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    },
  );

  test('ideas tab shows candidate proposals', () async {
    await until(
      () =>
          web.document
              .querySelector('#ventures-test')!
              .textContent!
              .contains('Carrier Desk') ==
          false,
    );
    await click('Ideas (1)');
    await until(
      () => web.document
          .querySelector('#ventures-test')!
          .textContent!
          .contains('Carrier Desk'),
    );
    final page = web.document.querySelector('#ventures-test')!.textContent!;
    expect(page, contains('agent opinion'));
    expect(page, contains('Carriers share invoice data.'));
    // The bar is visible on the card, with what the scout had proposed.
    expect(page, contains('60 (scout proposed 0)'));
    expect(page, contains('Off until you start them'));
    // Another organization's candidate is not listed; a decided one is filed apart.
    expect(page, isNot(contains('Foreign Desk')));
    expect(page, contains('Decided (1)'));
  });

  test(
    'studio tab shows scheduler errors, names and runs needing attention',
    () async {
      await click('Studio');
      await until(
        () => web.document
            .querySelector('#ventures-test')!
            .textContent!
            .contains('Run scout now'),
      );
      final page = web.document.querySelector('#ventures-test')!.textContent!;
      expect(page, contains('The venture candidate backlog is full.'));
      expect(page, contains('(2 times)'));
      expect(page, contains('#1 Settlement Desk · continue'));
      expect(page, contains('Needs attention (1)'));
      expect(page, contains('Re-send prompt'));
      expect(page, contains('Dismiss run'));
      expect(page, contains('Lessons the studio sees (1)'));
    },
  );

  test('studio tab runs a scout action', () async {
    await click('Studio');
    await until(
      () => web.document
          .querySelector('#ventures-test')!
          .textContent!
          .contains('Run scout now'),
    );
    await click('Run scout now');
    await until(() => requests.isNotEmpty);
    expect(requests.single['mode'], 'studio_run');
    expect(requests.single['kind'], 'scout');
  });
  test('reference price input rejects typos instead of becoming zero', () {
    expect(parseReferencePrice(''), 0);
    expect(parseReferencePrice('  '), 0);
    expect(parseReferencePrice('0'), 0);
    expect(parseReferencePrice(' 48000 '), 48000);
    expect(parseReferencePrice('48,000'), isNull);
    expect(parseReferencePrice('48k'), isNull);
    expect(parseReferencePrice('-1'), isNull);
    expect(parseReferencePrice('12.5'), isNull);
  });

  test('a qualified venture shows score, roles and approves a draft', () async {
    await until(
      () => web.document
          .querySelector('#ventures-test')!
          .textContent!
          .contains('Settlement question'),
    );
    final page = web.document.querySelector('#ventures-test')!.textContent!;
    expect(page, contains('Red Team: Red Team'));
    expect(page, contains('Qualified'));
    expect(page, contains('64'));
    await click('Approve');
    await until(() => requests.isNotEmpty);
    expect(requests.single['mode'], 'outreach');
    expect(requests.single['decision'], 'approve');
    expect(requests.single['outreach_id'], 'cccccccccccccccccccccccccccccccc');
  });
}
