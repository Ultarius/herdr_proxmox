@TestOn('browser')
library;

import 'dart:convert';
import 'package:herdr_jaspr_dashboard/pages/org_forms.dart' as forms;
import 'dart:js_interop';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'package:herdr_jaspr_dashboard/pages/organization_page.dart';
import 'package:herdr_jaspr_dashboard/pages/organization_automation.dart';
import 'package:herdr_jaspr_dashboard/pages/group_page.dart';
import 'package:herdr_jaspr_dashboard/pages/collaboration_panel.dart';
import 'package:herdr_jaspr_dashboard/pages/org_chart.dart';

class _Host extends StatefulComponent {
  const _Host(this.child);
  final Component child;
  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> {
  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    ui.dialogChanges.addListener(changed);
  }

  @override
  void dispose() {
    ui.dialogChanges.removeListener(changed);
    super.dispose();
  }

  @override
  Component build(BuildContext context) =>
      dom.div([component.child, ui.buildDialogs()]);
}

Future<void> settle() async =>
    Future<void>.delayed(const Duration(milliseconds: 45));
Iterable<web.HTMLElement> nodes(String selector) sync* {
  final all = web.document.querySelectorAll('#org-dom-test $selector');
  for (var i = 0; i < all.length; i++) yield all.item(i) as web.HTMLElement;
}

web.HTMLButtonElement btn(String value) =>
    nodes('button').firstWhere((n) => n.textContent == value)
        as web.HTMLButtonElement;
Future<void> click(String value) async {
  btn(value).click();
  await settle();
}

web.HTMLElement control(String label) {
  final n = nodes('label').firstWhere(
    (n) =>
        n.querySelector('span')?.textContent == label || n.textContent == label,
  );
  return (n.querySelector('input,select,textarea') ??
          web.document.getElementById(n.getAttribute('for')!))!
      as web.HTMLElement;
}

Future<void> input(String label, String value) async {
  final n = control(label);
  if (n is web.HTMLInputElement) n.value = value;
  if (n is web.HTMLTextAreaElement) n.value = value;
  n.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await settle();
}

Future<void> choose(String label, String value) async {
  final n = control(label) as web.HTMLSelectElement;
  n.value = value;
  n.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
  await settle();
}

Map<String, dynamic> org() => {
  'id': 'org',
  'name': 'Engineering',
  'purpose': 'Build reliable tools',
  'instructions': 'Review the evidence',
};
Map<String, dynamic> agent(String id) => {
  'id': id,
  'organization_id': 'org',
  'name': id == 'alice' ? 'Alice' : 'Bob',
  'role': 'Engineer',
  'runtime': 'codex',
  'persona': 'Preserve behavior',
  'project': '/projects/repo',
  'provider': '',
  'model': '',
  'reasoning': '',
  'version': 1,
};
Map<String, dynamic> group() => {
  'id': 'group',
  'organization_id': 'org',
  'name': 'Architecture',
  'description': 'Review the design',
  'members': ['alice', 'bob'],
  'read_only': true,
  'review_scope': 'topic',
};
Map<String, dynamic> snapshot({List<Map<String, dynamic>> jobs = const []}) => {
  'organizations': [org()],
  'profiles': [agent('alice'), agent('bob')],
  'groups': [group()],
  'jobs': jobs,
};
Map<String, dynamic> modelCatalog() => {
  'models': [
    {
      'id': 'model-1',
      'name': 'Account model',
      'provider': 'opencode-go',
      'reasoning': ['high'],
    },
  ],
  'supports_variants': true,
  'source': 'opencode account',
};

void main() {
  late ClientAppBinding binding;
  DashboardBloc? connection;
  final posts = <Map<String, dynamic>>[];
  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'org-dom-test');
    binding = ClientAppBinding();
  });
  setUp(() => posts.clear());
  tearDown(() async {
    for (final b in nodes(
      '.dialog-layer button',
    ).where((b) => b.textContent == 'Close').toList())
      b.click();
    await settle();
    binding.detachRootComponent();
    if (connection != null) {
      await connection!.disconnect();
      connection = null;
    }
    await BlocScope.reset();
    web.document.getElementById('org-dom-test')!.textContent = '';
  });
  Future<void> mount(
    Component child, {
    List<Map<String, dynamic>> jobs = const [],
    String? failAction,
    bool Function(http.Request)? failWhen,
    String? startedTaskId,
    String role = 'admin',
    dynamic Function(http.Request)? response,
    Map<String, dynamic>? projectListing,
  }) async {
    var lost = false;
    connection = DashboardBloc(
      client: MockClient((request) async {
        final path = request.url.path;
        if (request.method == 'POST') {
          posts.add({
            'path': path,
            ...jsonDecode(request.body) as Map<String, dynamic>,
          });
          if ((path.endsWith('/$failAction') ||
                  failWhen?.call(request) == true) &&
              !lost) {
            lost = true;
            return http.Response('{"error":"response lost"}', 503);
          }
        }
        final data =
            (path.endsWith('/projects/browse')
                ? projectListing ??
                      {
                        'root': '/projects',
                        'kind': 'folder',
                        'entries': [
                          for (final name in ['app', 'other', 'new-app'])
                            {'name': name, 'path': name, 'kind': 'folder'},
                          {
                            'name': 'notes.txt',
                            'path': 'notes.txt',
                            'kind': 'file',
                          },
                          {'name': 'linked', 'path': 'linked', 'kind': 'link'},
                        ],
                      }
                : response?.call(request)) ??
            (path.endsWith('/cli-setup')
                ? {
                    // The hire form offers only the runtimes the gateway reports
                    // as ready, so the fixture must report them.
                    'clis': [
                      for (final entry in {
                        'codex': 'Codex',
                        'claude': 'Claude',
                        'opencode': 'OpenCode',
                        'agy': 'Antigravity',
                      }.entries)
                        {
                          'id': entry.key,
                          'name': entry.value,
                          'installed': true,
                          'status': 'not_configured',
                          'ready': null,
                        },
                    ],
                  }
                : path.endsWith('/organizations/state') ||
                      path.endsWith('/organizations/activity')
                ? snapshot(jobs: jobs)
                : path.endsWith('/organizations/history')
                ? {
                    ...snapshot(jobs: jobs),
                    'group': group(),
                    'member_states': {
                      'alice': {'active': false, 'status': 'off'},
                      'bob': {'active': false, 'status': 'off'},
                    },
                  }
                : path.endsWith('/models')
                ? (request.method == 'POST'
                      ? modelCatalog()
                      : {
                          'runtimes': {'opencode': modelCatalog()},
                        })
                : path.endsWith('/tasks/start_proposal')
                ? {'id': startedTaskId}
                : path.endsWith('/organizations/session')
                ? {'results': []}
                : path.endsWith('/session')
                ? {'authenticated': true}
                : {'workspaces': [], 'agents': []});
        final result = await data;
        if (result is http.Response) return result;
        return http.Response(jsonEncode(result), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection!);
    connection!.connect('test');
    await settle();
    connection!.operator.value = {'role': role};
    binding.attachRootComponent(_Host(child), attachTo: '#org-dom-test');
    await settle();
  }

  List<Map<String, dynamic>> actions(String action) =>
      posts.where((p) => p['path'] == '/api/organizations/$action').toList();

  test(
    'group loading and failed history use status and alert announcements',
    () async {
      final history = Completer<http.Response>();
      await mount(
        const GroupPage(id: 'group'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/history'))
            return history.future;
          return null;
        },
      );
      expect(nodes('[role=status]').single.textContent, 'Loading group…');
      expect(nodes('h1').single.textContent, 'Discussion group');
      expect(nodes('[role=alert]'), isEmpty);
      history.complete(
        http.Response('{"error":"Group history unavailable"}', 503),
      );
      await settle();
      expect(
        nodes('[role=alert]').single.textContent,
        contains('Group history unavailable'),
      );
      expect(nodes('[role=status]'), isEmpty);
    },
  );

  test(
    'organization and group tabs retain keyboard access to scrollable tables',
    () async {
      await mount(const OrganizationPage(initialView: 'chart'));
      final chart = nodes('#org-view-tab-chart').single;
      chart.focus();
      chart.dispatchEvent(
        web.KeyboardEvent(
          'keydown',
          web.KeyboardEventInit(
            key: 'ArrowRight',
            bubbles: true,
            cancelable: true,
          ),
        ),
      );
      await settle();
      expect(web.document.activeElement, nodes('#org-view-tab-team').single);
      expect(
        nodes('#org-view-panel').single.getAttribute('aria-labelledby'),
        'org-view-tab-team',
      );
      expect(nodes('[role=region][aria-label=Team]').single.tabIndex, 0);
    },
  );

  Map<String, dynamic> proposalJob() => {
    'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'kind': 'discussion',
    'state': 'artifact_ready',
    'organization_id': 'org',
    'group_id': 'group',
    'proposal_details': [
      {
        'key': 'proposal-1',
        'title': 'Follow up',
        'description': 'Acceptance criteria and checks',
        'profile_id': 'alice',
        'status': 'skipped',
        'reason': 'Discussion created before automatic follow-up was enabled.',
      },
    ],
  };

  web.HTMLInputElement policyInput(String key) =>
      nodes('.dialog-layer [data-field="$key"] input').single
          as web.HTMLInputElement;

  test(
    'SDK preparation and native checks require separate permission',
    () async {
      var policy = <String, dynamic>{};
      await mount(
        const OrganizationAutomation(organizationId: 'org'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/automation')) {
            return {'policy': policy};
          }
          if (request.url.path.endsWith('/tasks/automation')) {
            policy = Map<String, dynamic>.from(jsonDecode(request.body));
            return {'policy': policy};
          }
          return null;
        },
      );
      await click('Edit policy');
      expect(policyInput('auto_prepare').checked, isFalse);
      expect(policyInput('auto_prepare_checks').checked, isFalse);
      expect(policyInput('auto_prepare_agent').checked, isFalse);
      policyInput('auto_prepare').click();
      await settle();
      await click('Save');
      expect(policy['auto_prepare'], isTrue);
      expect(policy['auto_prepare_checks'], isFalse);
      expect(policy['auto_prepare_agent'], isFalse);
      await click('Edit policy');
      expect(policyInput('auto_prepare').checked, isTrue);
      expect(policyInput('auto_prepare_checks').checked, isFalse);
      policyInput('auto_prepare_checks').click();
      await settle();
      await click('Save');
      expect(policy['auto_prepare'], isTrue);
      expect(policy['auto_prepare_checks'], isTrue);
      expect(policy['auto_prepare_agent'], isFalse);
      await click('Edit policy');
      policyInput('auto_prepare_agent').click();
      await settle();
      await click('Save');
      expect(policy['auto_prepare_agent'], isTrue);
      expect(policy['auto_prepare'], isTrue);
      expect(policy['auto_prepare_checks'], isTrue);
    },
  );

  test(
    'release planning has a separate opt-in and does not enable task execution',
    () async {
      var policy = <String, dynamic>{};
      await mount(
        const OrganizationAutomation(organizationId: 'org'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/automation'))
            return {'policy': policy};
          if (request.url.path.endsWith('/tasks/automation')) {
            policy = Map<String, dynamic>.from(jsonDecode(request.body));
            return {'policy': policy};
          }
          return null;
        },
      );
      await click('Edit policy');
      expect(policyInput('auto_create_releases').checked, isFalse);
      policyInput('auto_create_releases').click();
      await settle();
      await click('Save');
      expect(policy['auto_create_releases'], isTrue);
      expect(policy['auto_queue_proposals'], isFalse);
      expect(policy['auto_capture'], isFalse);
      expect(policy['auto_validate'], isFalse);
      expect(policy['auto_publish'], isFalse);
      await click('Edit policy');
      expect(policyInput('auto_create_releases').checked, isTrue);
      await click('Cancel');
    },
  );

  test(
    'publication routes validate repository overrides and preserve separate grants',
    () async {
      var policy = <String, dynamic>{};
      await mount(
        const OrganizationAutomation(organizationId: 'org'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/automation'))
            return {'policy': policy};
          if (request.url.path.endsWith('/tasks/automation')) {
            policy = Map<String, dynamic>.from(jsonDecode(request.body));
            return {'policy': policy};
          }
          return null;
        },
      );
      await click('Edit policy');
      final mode =
          nodes('.dialog-layer [data-field="publication_mode"] select').single
              as web.HTMLSelectElement;
      expect(mode.value, 'release');
      mode.value = 'both';
      mode.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
      await settle();
      var overrides = policyInput('publication_repositories');
      overrides.value = '{bad';
      overrides.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await click('Save');
      expect(policy, isEmpty);
      overrides = policyInput('publication_repositories');
      expect(
        nodes(
          '.dialog-layer [data-field="publication_repositories"]',
        ).single.getAttribute('aria-invalid'),
        'true',
      );
      overrides.value = '{"repo":"task"}';
      overrides.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await click('Save');
      expect(policy['publication_mode'], 'both');
      expect(policy['publication_repositories'], {'repo': 'task'});
      expect(policy['auto_publish'], isFalse);
    },
  );

  test(
    'discussion artifacts explain release creation and point to the board',
    () async {
      await mount(
        const GroupPage(id: 'group'),
        jobs: [
          {
            ...proposalJob(),
            'result': 'Release proposal',
            'release_plan_outcome': {
              'state': 'created',
              'version': 'alpha.16',
              'id': 'release-one',
            },
          },
        ],
      );
      await click('Artifacts');
      expect(
        web.document.body!.textContent,
        contains('Release plan alpha.16 created'),
      );
      expect(
        nodes(
          'button',
        ).where((button) => button.textContent == 'View workflow board'),
        hasLength(1),
      );
    },
  );

  test(
    'policy rejection stays in the modal, highlights its rule and preserves edits',
    () async {
      var policy = <String, dynamic>{
        'auto_capture': false,
        'auto_validate': false,
        'auto_publish': false,
        'daily_cap': 5,
      };
      await mount(
        const OrganizationAutomation(organizationId: 'org'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/automation'))
            return {'policy': policy};
          if (request.url.path.endsWith('/tasks/automation')) {
            final body = Map<String, dynamic>.from(jsonDecode(request.body));
            if (body['auto_validate'] == true)
              return http.Response(
                jsonEncode({
                  'error':
                      'Automatic validation needs the durable build service.',
                  'field_errors': {
                    'auto_validate':
                        'Install the durable build service under Builds & deployments.',
                  },
                }),
                400,
              );
            policy = body;
            return {'policy': policy};
          }
          return null;
        },
      );
      await click('Edit policy');
      policyInput('auto_capture').click();
      policyInput('auto_validate').click();
      await input('Daily task limit', '7');
      await click('Save');
      expect(nodes('[role="dialog"]'), hasLength(1));
      final summary = nodes('.org-form__summary').single;
      expect(
        summary.textContent,
        contains('Automatic validation needs the durable build service.'),
      );
      expect(
        summary.textContent,
        contains('Validate captured candidates automatically'),
      );
      expect(summary.parentElement!.firstElementChild, summary);
      expect(web.document.activeElement, summary);
      expect(policyInput('auto_validate').getAttribute('aria-invalid'), 'true');
      final errorId = policyInput(
        'auto_validate',
      ).getAttribute('aria-describedby')!;
      expect(
        web.document.getElementById(errorId)!.textContent,
        contains('Builds & deployments'),
      );
      expect(
        nodes('.org-form__item--invalid').single.getAttribute('data-field'),
        'auto_validate',
      );
      expect(policyInput('auto_validate').checked, isTrue);
      expect(policyInput('auto_capture').checked, isTrue);
      expect(policyInput('daily_cap').value, '7');
      expect(policy['auto_validate'], isFalse);
      policyInput('auto_validate').click();
      await settle();
      await click('Save');
      expect(nodes('[role="dialog"]'), isEmpty);
      expect(policy['auto_validate'], isFalse);
      expect(policy['auto_capture'], isTrue);
      expect(policy['daily_cap'], 7);
      expect(posts, hasLength(2));
    },
  );

  for (final editBeforeRetry in [false, true]) {
    test(
      'policy modal retains retry identity unless the draft changes: $editBeforeRetry',
      () async {
        var policy = <String, dynamic>{
          'auto_capture': false,
          'auto_validate': false,
          'auto_publish': false,
        };
        await mount(
          const OrganizationAutomation(organizationId: 'org'),
          failAction: 'automation',
          response: (request) {
            if (request.url.path.endsWith('/tasks/automation'))
              policy = Map<String, dynamic>.from(jsonDecode(request.body));
            if (request.url.path.endsWith('/organizations/automation') ||
                request.url.path.endsWith('/tasks/automation'))
              return {'policy': policy};
            return null;
          },
        );
        await click('Edit policy');
        await click('Save');
        expect(nodes('[role="dialog"]'), hasLength(1));
        expect(
          nodes('.org-form__summary').single.textContent,
          contains('response lost'),
        );
        if (editBeforeRetry) {
          policyInput('auto_capture').click();
          await settle();
        }
        await click('Save');
        expect(nodes('[role="dialog"]'), isEmpty);
        expect(posts, hasLength(2));
        expect(
          posts.last['request_id'],
          editBeforeRetry
              ? isNot(posts.first['request_id'])
              : posts.first['request_id'],
        );
      },
    );
  }

  test(
    'pending policy submission disables duplicate saves and modal dismissal',
    () async {
      final reply = Completer<http.Response>();
      await mount(
        const OrganizationAutomation(organizationId: 'org'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/automation'))
            return {
              'policy': {'auto_capture': false},
            };
          if (request.url.path.endsWith('/tasks/automation'))
            return reply.future;
          return null;
        },
      );
      try {
        await click('Edit policy');
        await click('Save');
        expect(btn('Saving…').disabled, isTrue);
        expect(btn('Cancel').disabled, isTrue);
        expect(btn('Close').disabled, isTrue);
        expect(policyInput('auto_capture').matches(':disabled'), isTrue);
        nodes('[role="dialog"]').single.dispatchEvent(
          web.KeyboardEvent(
            'keydown',
            web.KeyboardEventInit(key: 'Escape', bubbles: true),
          ),
        );
        btn('Saving…').click();
        await settle();
        expect(nodes('[role="dialog"]'), hasLength(1));
        expect(posts, hasLength(1));
        reply.complete(http.Response('{"error":"Please retry"}', 503));
        await settle();
        expect(btn('Save').disabled, isFalse);
        expect(btn('Close').disabled, isFalse);
        expect(nodes('[role="dialog"]'), hasLength(1));
      } finally {
        if (!reply.isCompleted)
          reply.complete(http.Response('{"error":"Please retry"}', 503));
        await settle();
      }
    },
  );

  test(
    'invalid policy limits highlight the field before any request',
    () async {
      await mount(
        const OrganizationAutomation(organizationId: 'org'),
        response: (request) =>
            request.url.path.endsWith('/organizations/automation')
            ? {
                'policy': {'daily_cap': 5},
              }
            : null,
      );
      await click('Edit policy');
      await input('Daily task limit', '-1');
      await click('Save');
      expect(nodes('[role="dialog"]'), hasLength(1));
      expect(
        nodes('.org-form__item--invalid').single.getAttribute('data-field'),
        'daily_cap',
      );
      expect(
        nodes('.org-form__summary').single.textContent,
        contains('Daily task limit'),
      );
      expect(posts, isEmpty);
    },
  );

  test(
    'manual proposal retry preserves request identity and opens the task',
    () async {
      final oldHash = web.window.location.hash;
      addTearDown(
        () => web.window.history.replaceState(
          null,
          '',
          oldHash.isEmpty ? '#' : oldHash,
        ),
      );
      await mount(
        const GroupPage(id: 'group'),
        jobs: [proposalJob()],
        failAction: 'start_proposal',
        startedTaskId: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      );
      await click('Proposals');
      await click('Start as task');
      expect(
        posts.where((p) => p['path'] == '/api/tasks/start_proposal'),
        isEmpty,
      );
      await click('Confirm');
      await click('Start as task');
      await click('Confirm');
      final starts = posts
          .where((p) => p['path'] == '/api/tasks/start_proposal')
          .toList();
      expect(starts.length, 2);
      expect(starts[0]['request_id'], starts[1]['request_id']);
      expect(starts[0]['job_id'], proposalJob()['id']);
      expect(starts[0]['proposal_key'], 'proposal-1');
      expect(
        web.window.location.hash,
        '#/tasks/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      );
    },
  );

  test('manual proposal control is hidden from non-administrators', () async {
    await mount(
      const GroupPage(id: 'group'),
      jobs: [proposalJob()],
      role: 'operator',
    );
    await click('Proposals');
    expect(
      nodes('button').where((n) => n.textContent == 'Start as task'),
      isEmpty,
    );
  });

  test(
    'manual proposal confirmation cannot cross an account generation',
    () async {
      await mount(const GroupPage(id: 'group'), jobs: [proposalJob()]);
      await click('Proposals');
      await click('Start as task');
      await connection!.disconnect();
      await settle();
      await click('Confirm');
      expect(
        posts.where((p) => p['path'] == '/api/tasks/start_proposal'),
        isEmpty,
      );
    },
  );

  test(
    'empty project explorer blocks team preview but permits a bare organization',
    () async {
      await mount(
        const OrganizationPage(initialView: 'team'),
        projectListing: {'root': '/projects', 'kind': 'folder', 'entries': []},
        response: (request) {
          if (request.url.path.endsWith('/organizations/templates'))
            return {
              'roles': [
                {'id': 'lead_developer', 'name': 'Lead Developer'},
              ],
            };
          if (request.url.path.endsWith('/organizations/state'))
            return snapshot();
          return {'authenticated': true};
        },
      );
      await click('Create organization');
      await input('Organization name', 'Empty team');
      await input('Purpose', 'Hire later');
      (control('Lead Developer') as web.HTMLInputElement).click();
      await settle();
      expect(btn('Preview team').disabled, true);
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('No project folders are available.'),
      );
      await click('Save');
      expect(actions('save'), isEmpty);
      (control('Lead Developer') as web.HTMLInputElement).click();
      await settle();
      await click('Save');
      expect(actions('save'), hasLength(1));
    },
  );

  test(
    'organization presets preview personas and invalidate changed project settings',
    () async {
      await mount(
        const OrganizationPage(initialView: 'team'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/templates')) {
            if (request.method == 'GET')
              return {
                'roles': [
                  {'id': 'lead_developer', 'name': 'Lead Developer'},
                ],
                'policies': {'balanced': 'Balanced', 'economy': 'Lowest cost'},
                'runtime_names': {'codex': 'Codex', 'claude': 'Claude Code'},
              };
            return {
              'template_plan': 'preview-digest',
              'warnings': [],
              'accounts': [
                {
                  'name': 'Codex',
                  'available': true,
                  'reason': 'CLI reports signed in.',
                },
                {
                  'name': 'Claude Code',
                  'available': false,
                  'reason': 'Herdr integration missing.',
                },
              ],
              'members': [
                {
                  'name': 'Lead Developer',
                  'runtime': 'claude',
                  'model_name': 'Sonnet',
                  'manager_role': '',
                  'reasoning': 'high',
                  'selection_note': 'Balanced choice',
                  'persona': 'Plan and coordinate. Report to the owner.',
                },
              ],
            };
          }
          if (request.url.path.endsWith('/organizations/state'))
            return snapshot();
          if (request.url.path.endsWith('/session'))
            return {'authenticated': true};
          return {'workspaces': [], 'agents': []};
        },
      );
      await click('Create organization');
      await input('Organization name', 'Template team');
      await input('Purpose', 'Build tools');
      (control('Lead Developer') as web.HTMLInputElement).click();
      await settle();
      expect(control('Team project directory'), isA<web.HTMLSelectElement>());
      final folders = nodes('select option').map((n) => n.textContent).toList();
      expect(folders, contains('/projects/app'));
      expect(folders, isNot(contains('/projects/notes.txt')));
      expect(folders, isNot(contains('/projects/linked')));
      await choose('Team project directory', '/projects/app');
      await input('Maximum concurrent sessions', '2');
      await input('Sessions per 24 hours', '10');
      await choose('Preferred CLI', 'codex');
      await click('Save');
      expect(actions('save'), isEmpty);
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('Preview the selected team'),
      );
      await click('Preview team');
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('Claude Code: Excluded'),
      );
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('Lead Developer · claude · Sonnet'),
      );
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('Plan and coordinate.'),
      );
      await choose('Team project directory', '/projects/other');
      await click('Save');
      expect(actions('save'), isEmpty);
      await click('Preview team');
      await click('Save');
      final saved = actions('save').single;
      expect(saved['template_roles'], ['lead_developer']);
      expect(saved['project'], '/projects/other');
      expect(saved['model_policy'], 'balanced');
      expect(saved['runtime_preferences'], ['codex']);
      expect(saved['resource_limits'], {
        'max_active_sessions': 2,
        'max_active_meetings': 1,
        'daily_session_cap': 10,
      });
      expect(saved['template_plan'], 'preview-digest');
      expect(saved.containsKey('preset_lead_developer'), false);
      expect(actions('hire'), isEmpty);
    },
  );

  test(
    'custom member names personas models and duplicates survive re-preview',
    () async {
      await mount(
        const OrganizationPage(initialView: 'team'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/templates')) {
            if (request.method == 'GET')
              return {
                'roles': [
                  {
                    'id': 'lead_developer',
                    'name': 'Lead Developer',
                    'persona_instruction': 'Plan and coordinate.',
                  },
                ],
                'policies': {'balanced': 'Balanced'},
                'runtime_names': {'claude': 'Claude Code'},
              };
            final body = jsonDecode(request.body) as Map<String, dynamic>;
            final specs = body['team_members'] as List;
            return {
              'template_plan': 'custom-preview',
              'member_specs': specs,
              'warnings': [],
              'runtimes': {
                'claude': {
                  'models': [
                    {
                      'provider': '',
                      'id': 'claude-sonnet-5-5',
                      'name': 'Sonnet',
                      'reasoning': ['medium', 'high'],
                    },
                  ],
                },
              },
              'members': [
                for (final spec in specs)
                  {
                    'template_key': spec['key'],
                    'name': spec['name'] ?? 'Lead Developer',
                    'runtime': 'claude',
                    'provider': '',
                    'model': 'claude-sonnet-5-5',
                    'model_name': 'Sonnet',
                    'reasoning': spec['reasoning'] ?? 'high',
                    'manager_name': 'Organization owner',
                    'manager_role': '',
                    'persona': spec['persona'] ?? 'Plan and coordinate.',
                    'selection_note': 'Fits this role',
                  },
              ],
            };
          }
          if (request.url.path.endsWith('/organizations/state'))
            return snapshot();
          if (request.url.path.endsWith('/session'))
            return {'authenticated': true};
          return {'workspaces': [], 'agents': []};
        },
      );
      await click('Create organization');
      await input('Organization name', 'Custom team');
      await input('Purpose', 'Build tools');
      (control('Lead Developer') as web.HTMLInputElement).click();
      await settle();
      await choose('Team project directory', '/projects/app');
      nodes(
        'summary',
      ).firstWhere((n) => n.textContent == 'Configure Lead Developer').click();
      await settle();
      await input('Member name', 'Maya');
      await input('Persona responsibilities', 'Lead the API migration.');
      await click('Add another Lead Developer');
      await click('Preview team');
      await choose('Member CLI', 'claude');
      await choose('Member model', jsonEncode(['', 'claude-sonnet-5-5']));
      await choose('Member reasoning', 'medium');
      await choose('Reports to', '');
      await click('Save');
      expect(actions('save'), isEmpty);
      await click('Preview team');
      await click('Save');
      final saved = actions('save').single;
      final members = saved['team_members'] as List;
      expect(members.length, 2);
      expect(members[0]['name'], 'Maya');
      expect(members[0]['persona'], 'Lead the API migration.');
      expect(members[0]['model'], 'claude-sonnet-5-5');
      expect(members[0]['reasoning'], 'medium');
      expect(members[0]['manager_key'], '');
      expect(members[0]['key'], isNot(members[1]['key']));
      expect(saved['template_roles'], ['lead_developer']);
    },
  );

  test(
    'team preset fills the draft and selected roles can still be changed',
    () async {
      await mount(
        const OrganizationPage(initialView: 'team'),
        response: (request) {
          if (request.url.path.endsWith('/organizations/templates')) {
            if (request.method == 'GET')
              return {
                'roles': [
                  {'id': 'lead_developer', 'name': 'Lead Developer'},
                  {'id': 'backend_developer', 'name': 'Backend Developer'},
                ],
                'policies': {'balanced': 'Balanced'},
                'bundles': [
                  {
                    'id': 'frontend_backend',
                    'name': 'Frontend/backend team',
                    'description': 'A focused engineering team.',
                    'model_policy': 'balanced',
                    'members': [
                      {
                        'key': 'lead_developer',
                        'template_role': 'lead_developer',
                      },
                      {
                        'key': 'backend_developer',
                        'template_role': 'backend_developer',
                      },
                    ],
                  },
                ],
              };
            final body = jsonDecode(request.body) as Map<String, dynamic>;
            return {
              'template_plan': 'bundle-plan',
              'members': [],
              'member_specs': body['team_members'],
            };
          }
          if (request.url.path.endsWith('/organizations/state'))
            return snapshot();
          if (request.url.path.endsWith('/session'))
            return {'authenticated': true};
          return {'workspaces': [], 'agents': []};
        },
      );
      await click('Create organization');
      await input('Organization name', 'Preset team');
      await input('Purpose', 'Build tools');
      await choose('Team preset', 'frontend_backend');
      expect((control('Lead Developer') as web.HTMLInputElement).checked, true);
      expect(
        (control('Backend Developer') as web.HTMLInputElement).checked,
        true,
      );
      (control('Backend Developer') as web.HTMLInputElement).click();
      await settle();
      await choose('Team project directory', '/projects/app');
      await click('Preview team');
      await click('Save');
      final saved = actions('save').single;
      expect(saved['team_preset'], 'frontend_backend');
      expect(saved['template_roles'], ['lead_developer']);
      expect((saved['team_members'] as List).length, 1);
    },
  );

  test(
    'saved template versions load edit retry and create with pinned provenance',
    () async {
      const identifier = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
      var latest = 1;
      final versions = <int, Map<String, dynamic>>{
        1: {
          'team_members': [
            {
              'key': 'lead',
              'template_role': 'lead_developer',
              'name': 'Maya',
              'persona': 'Plan delivery.',
              'manager_key': '',
              'use_worktree': true,
            },
          ],
          'model_policy': 'balanced',
          'runtime_preferences': ['codex', 'claude'],
          'resource_limits': {
            'max_active_sessions': 3,
            'max_active_meetings': 1,
            'daily_session_cap': 20,
          },
        },
      };
      await mount(
        const OrganizationPage(initialView: 'team'),
        failWhen: (r) =>
            r.url.path.endsWith('/templates/library') &&
            (jsonDecode(r.body) as Map)['action'] == 'save',
        response: (request) {
          if (request.url.path.endsWith('/templates/library')) {
            final body = jsonDecode(request.body) as Map<String, dynamic>;
            if (body['action'] == 'save')
              versions[++latest] = Map<String, dynamic>.from(
                body['definition'],
              );
            final version = body['action'] == 'save'
                ? latest
                : body['version'] as int;
            return {
              'id': identifier,
              'version': version,
              'latest_version': latest,
              'name': 'API team',
              'description': 'A portable API team',
              'definition': versions[version],
            };
          }
          if (request.url.path.endsWith('/organizations/templates')) {
            if (request.method == 'GET')
              return {
                'roles': [
                  {
                    'id': 'lead_developer',
                    'name': 'Lead Developer',
                    'persona_instruction': 'Plan delivery.',
                  },
                ],
                'policies': {'balanced': 'Balanced'},
                'runtime_names': {'codex': 'Codex', 'claude': 'Claude Code'},
                'saved_templates': [
                  {
                    'id': identifier,
                    'name': 'API team',
                    'version': 1,
                    'versions': [
                      {'version': 1, 'name': 'API team'},
                    ],
                  },
                ],
              };
            final body = jsonDecode(request.body) as Map<String, dynamic>;
            return {
              'template_plan': 'version-2-plan',
              'member_specs': body['team_members'],
              'members': [],
            };
          }
          if (request.url.path.endsWith('/organizations/state'))
            return snapshot();
          if (request.url.path.endsWith('/session'))
            return {'authenticated': true};
          return {'workspaces': [], 'agents': []};
        },
      );
      await click('Create organization');
      await input('Organization name', 'New organization');
      await input('Purpose', 'Build tools');
      await choose('Saved template version', '$identifier@1');
      expect((control('Lead Developer') as web.HTMLInputElement).checked, true);
      expect(
        (control('Maximum concurrent sessions') as web.HTMLInputElement).value,
        '3',
      );
      nodes(
        'summary',
      ).firstWhere((n) => n.textContent == 'Configure Maya').click();
      await settle();
      await input('Member name', 'Maya v2');
      nodes(
        'summary',
      ).firstWhere((n) => n.textContent == 'Save reusable template').click();
      await settle();
      await click('Save new version');
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('response lost'),
      );
      await click('Save new version');
      final saves = actions(
        'templates/library',
      ).where((p) => p['action'] == 'save').toList();
      expect(saves.length, 2);
      expect(saves[0]['request_id'], saves[1]['request_id']);
      expect((saves[1]['definition'] as Map).containsKey('project'), false);
      expect(saves[1]['expected_version'], 1);
      await choose('Team project directory', '/projects/new-app');
      await click('Preview team');
      await click('Save');
      final body = actions('save').single;
      expect(body['template_id'], identifier);
      expect(body['template_version'], 2);
      expect(body['runtime_preferences'], ['codex', 'claude']);
      expect((body['team_members'] as List).single['name'], 'Maya v2');
    },
  );

  test(
    'hire preserves CLI model, worktree and permission defaults when runtime changes',
    () async {
      await mount(const OrganizationPage(initialView: 'team'));
      await click('Hire an agent');
      await click('Save');
      expect(actions('hire'), isEmpty);
      await input('Name', 'Charlie');
      await input('Role', 'Reviewer');
      await input('Persona instructions', 'Review exact changes');
      await choose('Runtime', 'opencode');
      expect(
        (control('Permissions') as web.HTMLSelectElement).value,
        'dashboard_outputs',
      );
      await choose('Provider', 'opencode-go');
      await choose('Model', 'model-1');
      await choose('Variant / reasoning', 'high');
      await input(
        'Additional accessible path globs',
        '/projects/shared/**\n/projects/shared/**',
      );
      await choose('Runtime', 'claude');
      expect(
        nodes(
          'label',
        ).where((n) => n.querySelector('span')?.textContent == 'Permissions'),
        isEmpty,
      );
      expect((control('Model') as web.HTMLSelectElement).value, '');
      await choose('Runtime', 'opencode');
      await choose('Provider', 'opencode-go');
      await choose('Model', 'model-1');
      await choose('Variant / reasoning', 'high');
      await click('Save');
      final p = actions('hire').single;
      expect(p['organization_id'], 'org');
      expect(p['runtime'], 'opencode');
      expect(p['provider'], 'opencode-go');
      expect(p['model'], 'model-1');
      expect(p['reasoning'], 'high');
      expect(p['permission_mode'], 'dashboard_outputs');
      expect(p['accessible_paths'], isEmpty);
      expect(p['use_worktree'], true);
      expect(p['request_id'], isNotEmpty);
    },
  );

  test(
    'session restart is inspection-gated and a lost response retries the same identity',
    () async {
      final job = {
        'id': 'run',
        'organization_id': 'org',
        'kind': 'launch',
        'profile_id': 'alice',
        'state': 'persona_sent',
        'alias': 'Alice',
        'pane_id': '%1',
        'profile': agent('alice'),
      };
      await mount(
        const OrganizationPage(initialView: 'runs'),
        jobs: [job],
        failAction: 'session',
      );
      await click('Restart in assigned checkout');
      expect(actions('session'), isEmpty);
      await click('Cancel');
      expect(actions('session'), isEmpty);
      await click('Restart in assigned checkout');
      await click('I inspected the idle session');
      expect(actions('session').length, 1);
      await click('Restart in assigned checkout');
      await click('I inspected the idle session');
      final calls = actions('session');
      expect(calls.length, 2);
      expect(calls[0]['request_id'], calls[1]['request_id']);
      expect(calls[1]['inspected'], true);
      expect(calls[1]['mode'], 'restart');
      expect(calls[1]['job_id'], 'run');
    },
  );

  test(
    'custom CLI settings trim input and preserve catalogue-independent values',
    () async {
      await mount(const OrganizationPage(initialView: 'team'));
      await click('Hire an agent');
      await input('Name', 'Custom hire');
      await input('Role', 'Reviewer');
      await input('Persona instructions', 'Review changes');
      await choose('Runtime', 'opencode');
      await choose('Provider', 'manual');
      await input('Provider (manual)', '  new-provider  ');
      await choose('Model', 'manual');
      await input('Model (manual)', '  newly-released-model  ');
      await click('Save');
      final p = actions('hire').single;
      expect(p['provider'], 'new-provider');
      expect(p['model'], 'newly-released-model');
      expect(p['reasoning'], '');
    },
  );

  test(
    'original collaborate deep links retain chat and group controls',
    () async {
      await mount(const OrganizationPage(initialView: 'collaborate'));
      expect(btn('Send prompt').disabled, true);
      expect(btn('Create group').disabled, false);
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('Chat with an agent'),
      );
    },
  );

  test(
    'session cleanup only submits explicitly chosen inspected released sessions',
    () async {
      final jobs = [
        {
          'id': 'released',
          'organization_id': 'org',
          'kind': 'launch',
          'profile_id': 'alice',
          'state': 'released',
          'alias': 'Alice',
          'pane_id': '%1',
        },
        {
          'id': 'busy',
          'organization_id': 'org',
          'kind': 'launch',
          'profile_id': 'bob',
          'state': 'running',
          'alias': 'Bob',
          'pane_id': '%2',
        },
      ];
      await mount(const OrganizationPage(initialView: 'runs'), jobs: jobs);
      await click('Clean up obsolete panes');
      expect(btn('Archive and close selected').disabled, true);
      expect(actions('session'), isEmpty);
      final checkbox =
          nodes('.dialog input[type=checkbox]').single as web.HTMLInputElement;
      checkbox.click();
      await settle();
      await click('Archive and close selected');
      final p = actions('session').single;
      expect(p['mode'], 'cleanup');
      expect(p['job_ids'], ['released']);
      expect(p['inspected'], true);
      expect(p['organization_id'], 'org');
    },
  );

  test(
    'group retry preserves durable identity and account change clears pending commands',
    () async {
      await mount(const GroupPage(id: 'group'), failAction: 'discuss');
      await click('Start discussion');
      expect(actions('discuss').length, 1);
      await click('Retry same request');
      expect(actions('discuss').length, 2);
      expect(
        actions('discuss')[0]['request_id'],
        actions('discuss')[1]['request_id'],
      );
      expect(actions('discuss')[0]['group_id'], 'group');
      expect(actions('discuss')[0]['organization_id'], 'org');
      await connection!.disconnect();
      await settle();
      expect(
        nodes('button').where((n) => n.textContent == 'Retry same request'),
        isEmpty,
      );
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        isNot(contains('Architecture')),
      );
    },
  );

  test(
    'chat recovery requires inspected-idle confirmation and preserves original prompt',
    () async {
      final job = {
        'id': 'chat',
        'kind': 'chat',
        'profile_id': 'alice',
        'organization_id': 'org',
        'state': 'uncertain',
        'prompt': 'Original reviewed question',
        'delivery': {'stage': 'command_returned'},
      };
      await mount(
        CollaborationPanel(
          organization: org(),
          profiles: [agent('alice'), agent('bob')],
        ),
        jobs: [job],
      );
      await choose('Agent', 'alice');
      await click('Recover saved reply');
      expect(actions('recover'), isEmpty);
      await click('Cancel');
      expect(actions('recover'), isEmpty);
      await click('Recover saved reply');
      await click('Confirm');
      final p = actions('recover').single;
      expect(p['job_id'], 'chat');
      expect(p['chat'], true);
      expect(p['inspected'], true);
      expect(p.containsKey('prompt'), false);
    },
  );

  test(
    'a group preparation confirmation cannot cross an account generation',
    () async {
      final job = {
        'id': 'meeting',
        'kind': 'discussion',
        'organization_id': 'org',
        'group_id': 'group',
        'state': 'queued',
        'preparation_version': 1,
        'discussion_submitted_at': null,
      };
      await mount(const GroupPage(id: 'group'), jobs: [job]);
      await click('Cancel waiting meeting');
      await connection!.disconnect();
      connection!.connect('new account');
      await settle();
      await click('Confirm');
      expect(actions('cancel_discussion'), isEmpty);
    },
  );

  test(
    'a chat recovery confirmation cannot cross an account generation',
    () async {
      final job = {
        'id': 'chat',
        'kind': 'chat',
        'profile_id': 'alice',
        'organization_id': 'org',
        'state': 'uncertain',
        'prompt': 'Original question',
      };
      await mount(
        CollaborationPanel(organization: org(), profiles: [agent('alice')]),
        jobs: [job],
      );
      await choose('Agent', 'alice');
      await click('Recover saved reply');
      await connection!.disconnect();
      connection!.connect('new account');
      await settle();
      await click('Confirm');
      expect(actions('recover'), isEmpty);
    },
  );

  test(
    'organization chart renders manager links as accessible DOM and SVG',
    () async {
      final child = agent('bob')..['manager_id'] = 'alice';
      await mount(
        OrgChart(
          profiles: [agent('alice'), child],
          agents: const [],
          jobs: const [],
          onSelect: (_) {},
        ),
      );
      expect(nodes('svg path').length, 1);
      expect(nodes('canvas'), isEmpty);
      expect(btn('Alice').disabled, false);
      expect(
        nodes('.status').map((n) => n.textContent),
        contains('No live session'),
      );
      final beforeZoom = double.parse(
        nodes('.org-chart-scene').single.getAttribute('data-zoom')!,
      );
      await click('Zoom in');
      final afterZoom = double.parse(
        nodes('.org-chart-scene').single.getAttribute('data-zoom')!,
      );
      expect(afterZoom, closeTo((beforeZoom + .15).clamp(.1, 2.5), .0001));
    },
  );

  test(
    'backup import reviews exact counts and sends complete configuration without launching',
    () async {
      final data = {
        'format': 'herdr-configuration',
        'version': 1,
        'organizations': [org()],
        'profiles': [agent('alice'), agent('bob')],
        'groups': [group()],
      };
      await mount(const OrganizationPage(initialView: 'team'));
      await click('Import configuration');
      final picker =
          web.document.querySelector('input[type=file]')
              as web.HTMLInputElement;
      final transfer = web.DataTransfer();
      transfer.items.add(
        web.File(
          [jsonEncode(data).toJS].toJS,
          'backup.json',
          web.FilePropertyBag(type: 'application/json'),
        ),
      );
      picker.files = transfer.files;
      picker.dispatchEvent(web.Event('change'));
      await settle();
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains('1 organizations, 2 agents, 1 groups.'),
      );
      expect(actions('import'), isEmpty);
      await click('Import');
      final p = actions('import').single;
      expect(p['organizations'], data['organizations']);
      expect(p['profiles'], data['profiles']);
      expect(p['groups'], data['groups']);
      expect(actions('launch'), isEmpty);
    },
  );
  test(
    'template preview renders actual worktree and permission settings',
    () async {
      await mount(dom.div([]));
      final form = forms.organizationForm(
        null,
        {
          'roles': [
            {'id': 'lead_developer', 'name': 'Lead Developer'},
          ],
          'policies': {'balanced': 'Balanced'},
        },
        (body) async => {
          'member_specs': body['team_members'],
          'members': [
            {
              'template_key': (body['team_members'] as List).first['key'],
              'name': 'Shared reviewer',
              'runtime': 'opencode',
              'model_name': 'Coder',
              'reasoning': '',
              'manager_name': 'Organization owner',
              'selection_note': '',
              'persona': 'Review changes.',
              'use_worktree': false,
              'permission_mode': 'full_autonomy',
              'accessible_paths': ['/shared/**'],
            },
          ],
        },
        null,
        true,
        {'/projects/app': '/projects/app'},
      );
      await settle();
      (control('Lead Developer') as web.HTMLInputElement).click();
      await settle();
      await choose('Team project directory', '/projects/app');
      await click('Preview team');
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        contains(
          'Shared project directory · Full autonomy · Additional access: /shared/**',
        ),
      );
      expect(
        web.document.getElementById('org-dom-test')!.textContent,
        isNot(contains('Separate worktree · CLI default permissions')),
      );
      await click('Cancel');
      await form;
    },
  );

  test(
    'template save preserves newer draft edits and advances saved identity',
    () async {
      await mount(dom.div([]));
      final pending = Completer<Map<String, dynamic>>();
      final next = Completer<Map<String, dynamic>>();
      Map<String, dynamic>? submitted;
      final form = forms.organizationForm(
        null,
        {
          'roles': [
            {
              'id': 'lead_developer',
              'name': 'Lead Developer',
              'persona_instruction': 'Lead delivery.',
            },
          ],
          'policies': {'balanced': 'Balanced'},
        },
        null,
        (body) {
          submitted = body;
          return pending.isCompleted ? next.future : pending.future;
        },
        true,
        {'/projects/app': '/projects/app'},
      );
      await settle();
      (control('Lead Developer') as web.HTMLInputElement).click();
      await settle();
      await input('Template name', 'Saved team');
      await click('Save as new template');
      expect(submitted, isNotNull);
      await input('Member name', 'Edited during save');
      final definition = Map<String, dynamic>.from(submitted!['definition']);
      definition['team_members'] = [
        {
          ...(definition['team_members'] as List).single as Map,
          'name': 'Lead Developer',
          'persona': 'Lead delivery.',
          'manager_key': '',
          'use_worktree': true,
        },
      ];
      pending.complete({
        'id': 'a' * 32,
        'version': 1,
        'latest_version': 1,
        'name': 'Saved team',
        'description': '',
        'definition': definition,
      });
      await settle();
      await settle();
      expect(
        (control('Member name') as web.HTMLInputElement).value,
        'Edited during save',
      );
      expect(
        nodes('button').any((n) => n.textContent == 'Save new version'),
        isTrue,
        reason:
            'A successful save should establish the saved identity even when newer draft edits prevent hydration.',
      );
      await click('Save new version');
      expect(submitted!['template_id'], 'a' * 32);
      expect(submitted!['expected_version'], 1);
      next.complete({
        ...await pending.future,
        'version': 2,
        'latest_version': 2,
      });
      await settle();
      await click('Cancel');
      await form;
    },
  );
}
