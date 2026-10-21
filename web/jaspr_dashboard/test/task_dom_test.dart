@TestOn('browser')
library;

import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'package:herdr_jaspr_dashboard/pages/tasks_page.dart';
import 'package:herdr_jaspr_dashboard/pages/knowledge_page.dart';
import 'package:herdr_jaspr_dashboard/pages/discovery_panel.dart';

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
    Future<void>.delayed(const Duration(milliseconds: 30));
Iterable<web.HTMLElement> elements(String selector) sync* {
  final nodes = web.document.querySelectorAll('#task-dom-test $selector');
  for (var i = 0; i < nodes.length; i++) yield nodes.item(i) as web.HTMLElement;
}

web.HTMLButtonElement button(String text) {
  bool matches(web.HTMLElement e) =>
      (e.textContent ?? '')
          .replaceFirst(e.querySelector('.native-icon')?.textContent ?? '', '')
          .trim() ==
      text;
  final inDialog = elements('.dialog-layer:last-child button').where(matches);
  return (inDialog.isNotEmpty
          ? inDialog.first
          : elements('button').where(matches).first)
      as web.HTMLButtonElement;
}

Future<void> click(String text) async {
  button(text).click();
  await settle();
}

web.HTMLElement field(String label) {
  final node = elements('label').firstWhere((e) => e.textContent == label);
  return web.document.getElementById(node.getAttribute('for')!)
      as web.HTMLElement;
}

Future<void> type(String label, String value) async {
  final node = field(label);
  if (node is web.HTMLInputElement) node.value = value;
  if (node is web.HTMLTextAreaElement) node.value = value;
  node.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await settle();
}

Future<void> choose(String label, String value) async {
  final node = field(label) as web.HTMLSelectElement;
  node.value = value;
  node.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
  await settle();
}

void main() {
  late ClientAppBinding binding;
  DashboardBloc? connection;
  final posts = <Map<String, dynamic>>[];
  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    final fixture = web.HTMLDivElement()..id = 'task-dom-test';
    web.document.body!.appendChild(fixture);
    binding = ClientAppBinding();
  });
  setUp(() {
    posts.clear();
  });
  tearDown(() async {
    for (final close in elements(
      '.dialog-layer button',
    ).where((e) => e.textContent == 'Close').toList()) {
      close.click();
    }
    await settle();
    binding.detachRootComponent();
    if (connection != null) {
      await connection!.disconnect();
      connection = null;
    }
    await BlocScope.reset();
    web.document.getElementById('task-dom-test')!.textContent = '';
  });

  Future<void> mount(
    Component page,
    Map<String, dynamic> Function(http.Request) response, {
    bool loseFirst = false,
    String role = 'admin',
  }) async {
    var lost = false;
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method == 'POST') {
          posts.add({
            ...jsonDecode(request.body) as Map<String, dynamic>,
            'path': request.url.path,
          });
          if (loseFirst &&
              !lost &&
              request.url.path.startsWith('/api/tasks/')) {
            lost = true;
            return http.Response('{"error":"lost response"}', 503);
          }
        }
        return http.Response(jsonEncode(response(request)), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection!);
    connection!.connect('token');
    await settle();
    connection!.operator.value = {'role': role};
    binding.attachRootComponent(_Host(page), attachTo: '#task-dom-test');
    await settle();
  }

  Map<String, dynamic> publishedTask() => {
    'id': 'task',
    'title': 'Ready task',
    'state': 'published',
    'repository': 'repo',
    'organization_id': 'org',
    'github_repository': 'owner/repo',
    'head_sha': 'b' * 40,
    'branch': 'herdr/task-branch',
    'base_ref': 'refs/remotes/origin/main',
    'publish': {'head_sha': 'b' * 40},
    'pull_preview': {
      'title': 'Suggested title',
      'body': 'Suggested description',
      'excerpt': 'Finalized group discussion',
      'head_sha': 'b' * 40,
      'repository': 'owner/repo',
      'publishing_repository': 'contributor/repo',
      'head': 'contributor:herdr/task-branch',
      'base': 'main',
    },
  };
  Map<String, dynamic> taskResponse(
    http.Request r,
    Map<String, dynamic> task,
  ) => r.url.path.endsWith('/tasks')
      ? {
          'tasks': [task],
          'github': {'configured': true},
          'executor': 'service',
        }
      : r.url.path.endsWith('/tasks/detail')
      ? task
      : {'workspaces': [], 'agents': []};

  test(
    'environment recovery reviews exact candidate and requires explicit approval',
    () async {
      final task = publishedTask()
        ..addAll({
          'state': 'review_ready',
          'run_id': 'old-run',
          'builds': {
            'b' * 40: {
              'state': 'error',
              'blocker': 'policy_changed',
              'recovery': 'review_validation_environment',
            },
          },
        });
      final review = {
        'task_id': 'task',
        'candidate': 'b' * 40,
        'approved_commit': 'a' * 40,
        'previous_plan_digest': 'old-plan',
        'plan_digest': 'prepared-plan',
        'review_digest': 'review-token',
        'toolchains': [
          {'name': 'rust-toolchain', 'required': '1.97.1', 'component': '.'},
        ],
        'recipes': [
          {
            'cwd': '.',
            'arguments': ['cargo', 'test', '--no-default-features'],
            'evidence': 'README.md',
          },
        ],
      };
      await mount(const TasksPage(taskId: 'task'), (request) {
        if (request.url.path.endsWith('/validation_environment')) {
          final body = jsonDecode(request.body) as Map;
          if (body['mode'] == 'approve')
            task['validation_environment_status'] = {'state': 'approved'};
          return body['mode'] == 'review'
              ? {...task, 'validation_environment_review': review}
              : task;
        }
        return taskResponse(request, task);
      });
      await click('Checks & builds');
      await click('Review validation environment');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Candidate: ${'b' * 40}'),
      );
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('cargo test --no-default-features'),
      );
      await click('Cancel');
      expect(posts.where((p) => p['mode'] == 'approve'), isEmpty);
      await click('Review validation environment');
      await click('Approve for this candidate');
      final approval = posts.singleWhere((p) => p['mode'] == 'approve');
      expect(approval['candidate'], 'b' * 40);
      expect(approval['review_digest'], 'review-token');
      expect(approval['inspected'], true);
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Validation environment approved for this candidate.'),
      );
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        isNot(contains('Review validation environment')),
      );
      task['validation_environment_status'] = {
        'state': 'unavailable',
        'reason': 'Upstream validation inputs changed; update the task branch.',
      };
      await click('Refresh task');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Upstream validation inputs changed'),
      );
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        isNot(contains('Review validation environment')),
      );
      expect(posts.where((p) => '${p['path']}'.endsWith('/build')), isEmpty);
      expect(posts.where((p) => '${p['path']}'.contains('/input')), isEmpty);
    },
  );

  for (final pending in [true, false]) {
    test(
      'delivery retry is ${pending ? 'disabled' : 'available'} for an old inspection',
      () async {
        final task = publishedTask()
          ..addAll({
            'state': 'review_ready',
            'await_verification': true,
            'auto_validate': true,
            'delivery_workflow': {
              'sha': 'b' * 40,
              'state': 'attention',
              'error': 'Previous inspection requires recovery.',
              'revalidation_wait': {'inspection_pending': pending},
            },
          });
        await mount(
          const TasksPage(taskId: 'task'),
          (request) => taskResponse(request, task),
        );
        await click('Automation settings');
        expect(
          button('Retry verification and delivery inspection').disabled,
          pending,
        );
        if (pending) {
          button('Retry verification and delivery inspection').click();
          await settle();
          expect(posts, isEmpty);
        }
      },
    );
  }

  for (final selected in [false, true]) {
    test(
      'low storage remains visible on ${selected ? 'task detail' : 'workflow'} and clears after recovery',
      () async {
        final disk = <String, dynamic>{
          'blocked': true,
          'reason': 'Low disk space. Restore storage.',
        };
        final task = publishedTask();
        await mount(TasksPage(taskId: selected ? 'task' : null), (request) {
          final response = taskResponse(request, task);
          if (request.url.path.endsWith('/tasks')) response['storage'] = disk;
          return response;
        });
        expect(
          elements('.notice').any(
            (e) => e.textContent!.contains(
              'Storage needs attention: Low disk space.',
            ),
          ),
          true,
        );
        disk['blocked'] = false;
        await click(selected ? 'Refresh task' : 'Refresh tasks');
        expect(
          elements(
            '.notice',
          ).any((e) => e.textContent!.contains('Storage needs attention:')),
          false,
        );
      },
    );
  }

  test(
    'live snapshot binding identifies the current task on the board and list',
    () async {
      var activeTask = 'task', activeRun = 'current-run';
      final current = publishedTask()
        ..['state'] = 'pr_open'
        ..['title'] = 'Repair delivery contracts'
        ..['profile_id'] = 'worker'
        ..['run_id'] = 'current-run'
        ..['assigned_agent'] = {'id': 'worker', 'name': 'Nora'}
        ..['pr_repair'] = {'state': 'running', 'kind': 'validation_update'};
      final other = <String, dynamic>{
        ...current,
        'id': 'other-task',
        'title': 'Other task',
        'run_id': 'previous-run',
        'pr_repair': null,
      };
      await mount(const TasksPage(), (request) {
        if (request.url.path.endsWith('/snapshot'))
          return {
            'workspaces': [],
            'agents': [
              {
                'name': 'nora-session',
                'profile_id': 'worker',
                'agent_status': 'working',
                'run_id': activeRun,
                'task_id': activeTask,
              },
            ],
          };
        if (request.url.path.endsWith('/tasks'))
          return {
            'tasks': [other, current],
            'github': {'configured': true},
          };
        return {'workspaces': [], 'agents': []};
      });
      expect(
        elements(
          '.task-wf-node[data-work=working]',
        ).single.getAttribute('data-node'),
        'task',
      );
      expect(
        elements('.agent-chip[data-current-task=task]').single.textContent,
        contains('Repair delivery contracts'),
      );
      // A run can switch tasks without changing its Working activity label.
      activeTask = 'other-task';
      activeRun = 'previous-run';
      connection!.send(DashboardCommand('refresh'));
      await settle();
      expect(
        elements(
          '.task-wf-node[data-work=working]',
        ).single.getAttribute('data-node'),
        'other-task',
      );
      activeTask = 'task';
      activeRun = 'current-run';
      connection!.send(DashboardCommand('refresh'));
      await settle();
      elements(
        '[role=tab]',
      ).firstWhere((tab) => tab.textContent!.startsWith('List')).click();
      await settle();
      final rows = elements(
        'tr',
      ).where((row) => row.querySelector('h2') != null).toList();
      final active = rows.singleWhere(
        (row) => row.textContent!.contains('Repair delivery contracts'),
      );
      final inactive = rows.singleWhere(
        (row) => row.textContent!.contains('Other task'),
      );
      expect(active.querySelector('[data-live=true]'), isNotNull);
      expect(
        active.textContent,
        contains('Resolving conflicts · running checks'),
      );
      expect(inactive.querySelector('[data-live=true]'), isNull);
    },
  );

  test(
    'ambiguous live bindings never pick an arbitrary current task',
    () async {
      final current = publishedTask()
        ..['profile_id'] = 'worker'
        ..['run_id'] = 'current-run';
      await mount(const TasksPage(), (request) {
        if (request.url.path.endsWith('/snapshot'))
          return {
            'workspaces': [],
            'agents': [
              for (final run in ['current-run', 'unexpected-run'])
                {
                  'name': run,
                  'profile_id': 'worker',
                  'agent_status': 'working',
                  'run_id': run,
                  'task_id': 'task',
                },
            ],
          };
        return taskResponse(request, current);
      });
      expect(elements('.task-wf-node[data-work]'), isEmpty);
      expect(elements('.agent-chip[data-current-task]'), isEmpty);
    },
  );

  test(
    'freeze failures stay visible inside release details and link changed members',
    () async {
      final taskRow = publishedTask()..['head_sha'] = 'a' * 40;
      final member = <String, dynamic>{
        'task_id': 'task',
        'title': 'Task with new commit',
        'candidate_sha': 'a' * 40,
        'current_candidate_sha': 'a' * 40,
        'candidate_changed': false,
      };
      final release = <String, dynamic>{
        'id': 'release-one',
        'version': 'alpha.15',
        'repository': 'repo',
        'state': 'collecting',
        'edit_version': 1,
        'members': [member],
      };
      await mount(const TasksPage(), (request) {
        if (request.url.path.endsWith('/releases/freeze')) {
          member['candidate_changed'] = true;
          member['current_candidate_sha'] = 'b' * 40;
          taskRow['head_sha'] = 'b' * 40;
          throw Exception(
            'Task with new commit: selected candidate changed. Review Selected changes.',
          );
        }
        if (request.url.path.endsWith('/tasks'))
          return {
            'tasks': [taskRow],
            'releases': [release],
            'github': {},
          };
        return {'workspaces': [], 'agents': []};
      });
      elements('[data-node="release:release-one"]').single.click();
      await settle();
      await click('Freeze and integrate 1 tasks');
      await settle();
      final dialog = elements('dialog').single;
      expect(
        dialog.textContent,
        contains(
          'Last release action could not complete: Task with new commit',
        ),
      );
      expect(dialog.textContent, contains('Selection needs review'));
      expect(dialog.textContent, contains('Current candidate ${'b' * 40}'));
      await click('Review change: Task with new commit');
      expect(
        web.document.activeElement?.getAttribute('data-release-member'),
        'task',
      );
    },
  );

  test(
    'superseded inspection chat is visible in the activity subtitle',
    () async {
      final task = publishedTask()
        ..['audit'] = [
          {
            'action': 'delivery_superseded',
            'actor': 'gateway',
            'at': 'now',
            'head_sha': 'b' * 40,
            'previous_sha': 'a' * 40,
            'delivery_chat_id': 'inspection-old',
          },
        ];
      await mount(
        const TasksPage(taskId: 'task'),
        (request) => taskResponse(request, task),
      );
      await click('Activity & agents');
      expect(
        elements('.timeline__time').any(
          (element) => element.textContent!.contains(
            'Superseded aaaaaaaa · chat inspection-old',
          ),
        ),
        isTrue,
      );
    },
  );

  test(
    'published candidate ignores old delivery attention and offers its draft PR',
    () async {
      final task = publishedTask()
        ..['await_verification'] = true
        ..['builds'] = {
          'b' * 40: {
            'state': 'complete',
            'target': 'b' * 40,
            'required_checks_verified': true,
          },
        }
        ..['delivery_workflow'] = {
          'sha': 'a' * 40,
          'state': 'attention',
          'error': 'OLD CANDIDATE FAILURE',
        };
      await mount(
        const TasksPage(taskId: 'task'),
        (request) => taskResponse(request, task),
      );
      final text = web.document.getElementById('task-dom-test')!.textContent!;
      expect(
        text,
        isNot(contains('Delivery needs attention: OLD CANDIDATE FAILURE')),
      );
      expect(text, contains('Branch published; open a draft pull request.'));
      expect(
        elements(
          '.task-detail__next .task-detail__actions button',
        ).any((e) => e.textContent!.contains('Review draft PR')),
        isTrue,
      );
    },
  );

  test(
    'an old-policy pass offers revalidation in the next step and checks view',
    () async {
      final task = publishedTask()
        ..['state'] = 'pr_open'
        ..['merge_sha'] = 'c' * 40
        ..['builds'] = {
          'b' * 40: {
            'target': 'b' * 40,
            'state': 'complete',
            'required_checks_verified': true,
          },
        }
        ..['validation_policy'] = {
          'target': 'b' * 40,
          'matches': false,
          'state': 'stale',
          'reason':
              'Recorded checks used a different policy. Validate latest commit under the current approved policy.',
        };
      await mount(const TasksPage(taskId: 'task'), (request) {
        if (request.url.path.endsWith('/tasks/build')) {
          task['validation_policy'] = null;
          task['builds'] = {
            'b' * 40: {'target': 'b' * 40, 'state': 'queued'},
          };
          return task;
        }
        return taskResponse(request, task);
      });
      final next = elements('.task-detail__next').single;
      expect(next.textContent, contains('different policy'));
      expect(next.textContent, isNot(contains('ready for review')));
      expect(
        next.querySelector('button')?.textContent,
        'Validate latest commit',
      );
      await click('Checks & builds');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('recorded checks passed; revalidation required'),
      );
      await click('Validate latest commit');
      final request = posts.singleWhere(
        (post) => post['path'] == '/api/tasks/build',
      );
      expect(request['target'], 'b' * 40);
      expect(request['update_branch'], true);
      expect(
        elements(
          'button',
        ).where((node) => node.textContent == 'Validate latest commit'),
        isEmpty,
      );
      expect(button('Validation in progress · view build').disabled, false);
    },
  );

  test(
    'current-policy complete evidence does not offer redundant validation',
    () async {
      final task = publishedTask()
        ..['state'] = 'pr_open'
        ..['builds'] = {
          'b' * 40: {
            'target': 'b' * 40,
            'state': 'complete',
            'required_checks_verified': true,
          },
        }
        ..['validation_policy'] = {
          'target': 'b' * 40,
          'matches': true,
          'state': 'current',
        };
      await mount(
        const TasksPage(taskId: 'task'),
        (request) => taskResponse(request, task),
      );
      expect(
        elements(
          'button',
        ).where((node) => node.textContent == 'Validate latest commit'),
        isEmpty,
      );
      expect(posts, isEmpty);
    },
  );

  test(
    'validate latest commit authorizes an update and shows saved progress',
    () async {
      final task = publishedTask()..['state'] = 'review_ready';
      await mount(const TasksPage(taskId: 'task'), (request) {
        if (request.url.path.endsWith('/tasks/build')) {
          task['pr_repair'] = {
            'kind': 'validation_update',
            'state': 'queued',
            'message': 'Updating task branch before validation',
            'candidate': 'abcdef0123456789abcdef0123456789abcdef01',
            'policy_files': ['.github/workflows/ci.yml'],
          };
          return task;
        }
        return taskResponse(request, task);
      });
      await click('Validate latest commit');
      final request = posts.singleWhere(
        (post) => post['path'] == '/api/tasks/build',
      );
      expect(request['update_branch'], isTrue);
      expect(request['target'], task['head_sha']);
      expect(
        elements(
          'button',
        ).any((e) => e.textContent!.contains('View branch update')),
        isTrue,
      );
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Updating task branch before validation'),
      );
      await click('View branch update');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Updated candidate:'),
      );
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Upstream validation files: .github/workflows/ci.yml'),
      );
    },
  );

  test(
    'capture and validation can be enabled independently with the gateway executor',
    () async {
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Implementation',
        'description': 'Implement',
        'repository': 'repo',
        'organization_id': 'org',
        'state': 'draft',
        'base_ref': 'refs/remotes/origin/main',
        'base_sha': 'a' * 40,
        'auto_capture': false,
        'auto_validate': false,
        'auto_publish': false,
      };
      await mount(const TasksPage(taskId: 'task'), (request) {
        if (request.url.path.endsWith('/tasks/policy')) {
          task.addAll(
            Map<String, dynamic>.from(jsonDecode(request.body))
              ..remove('request_id'),
          );
          return task;
        }
        final result = taskResponse(request, task);
        if (request.url.path.endsWith('/tasks')) result['executor'] = 'gateway';
        return result;
      });
      await click('Automation settings');
      web.HTMLInputElement toggle(String title) =>
          elements('label.checkbox-field')
                  .firstWhere((e) => e.textContent!.startsWith(title))
                  .querySelector('input')
              as web.HTMLInputElement;
      expect(
        toggle('Automatically validate captured candidates').disabled,
        isFalse,
      );
      final capture = toggle('Automatically capture completed candidates');
      expect(capture.disabled, isFalse);
      capture.click();
      await settle();
      expect(posts.single['path'], '/api/tasks/policy');
      expect(posts.single['auto_capture'], true);
      expect(posts.single['auto_validate'], false);
      expect(posts.single['auto_publish'], false);
      toggle('Automatically validate captured candidates').click();
      await settle();
      expect(posts.last['path'], '/api/tasks/policy');
      expect(posts.last['auto_capture'], true);
      expect(posts.last['auto_validate'], true);
      expect(task['auto_publish'], false);
    },
  );

  Map<String, dynamic> publishingResponse(http.Request request) {
    final github = {
      'configured': true,
      'login': 'contributor',
      'repositories': ['owner/repo'],
      'pr_configured': true,
      'pr_login': 'owner',
      'publication': {
        'owner/repo': {
          'mode': 'fork',
          'publishing_repository': 'contributor/repo',
        },
      },
    };
    if (request.url.path == '/api/tasks')
      return {'tasks': [], 'github': github};
    if (request.url.path == '/api/github/configure') return github;
    return {'workspaces': [], 'agents': []};
  }

  test(
    'publishing sends a separate PR token without asking for its account',
    () async {
      await mount(const TasksPage(), publishingResponse);
      await click('GitHub publishing');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Separate PR credential configured for owner.'),
      );
      expect(
        elements(
          'label',
        ).where((e) => e.textContent?.contains('Pull request account') == true),
        isEmpty,
      );
      await type('Personal access token', 'p' * 40);
      await type('Pull request token (optional)', 'github_pat_${'r' * 40}');
      await click('Validate and save');
      await settle();
      expect(posts.single['path'], '/api/github/configure');
      expect(posts.single['pr_token'], 'github_pat_${'r' * 40}');
      expect(posts.single.containsKey('pr_login'), false);
      expect(posts.single.containsKey('clear_pr_credential'), false);
      expect(posts.single['publication'], {
        'owner/repo': {
          'mode': 'fork',
          'publishing_repository': 'contributor/repo',
        },
      });
    },
  );

  test(
    'publishing preserves the PR credential when its field stays blank',
    () async {
      await mount(const TasksPage(), publishingResponse);
      await click('GitHub publishing');
      await type('Personal access token', 'p' * 40);
      await click('Validate and save');
      await settle();
      expect(posts.single.containsKey('pr_token'), false);
      expect(posts.single.containsKey('pr_login'), false);
      expect(posts.single.containsKey('clear_pr_credential'), false);
    },
  );

  test(
    'publishing removes the separate PR credential only on explicit selection',
    () async {
      await mount(const TasksPage(), publishingResponse);
      await click('GitHub publishing');
      await type('Personal access token', 'p' * 40);
      final checkbox =
          elements('label')
                  .firstWhere(
                    (e) =>
                        e.textContent?.contains(
                          'Remove separate PR credential',
                        ) ==
                        true,
                  )
                  .querySelector('input')!
              as web.HTMLInputElement;
      checkbox.click();
      await settle();
      await click('Validate and save');
      await settle();
      expect(posts.single['clear_pr_credential'], true);
      expect(posts.single.containsKey('pr_token'), false);
    },
  );

  test(
    'publishing configuration cannot submit credentials after an account change',
    () async {
      await mount(const TasksPage(), publishingResponse);
      await click('GitHub publishing');
      await type('Personal access token', 'p' * 40);
      await type('Pull request token (optional)', 'github_pat_${'r' * 40}');
      await connection!.disconnect();
      await settle();
      await click('Validate and save');
      await settle();
      expect(posts, isEmpty);
    },
  );

  test(
    'release delivery hides task publication and creates a collecting single-task release',
    () async {
      final task = publishedTask()
        ..['state'] = 'review_ready'
        ..['publication_mode'] = 'release'
        ..['task_publication_allowed'] = false
        ..remove('publish');
      await mount(const TasksPage(taskId: 'task'), (r) {
        if (r.url.path == '/api/releases/single')
          return {
            'id': 'release-one',
            'version': 'urgent-task',
            'state': 'collecting',
          };
        return taskResponse(r, task);
      });
      final names = elements('button').map((e) => e.textContent).join(' ');
      expect(names, isNot(contains('Review and publish')));
      expect(names, isNot(contains('Open draft PR')));
      await click('Release this task only');
      final request = posts.single;
      expect(request['path'], '/api/releases/single');
      expect(request['task_id'], 'task');
      expect(request['head_sha'], 'b' * 40);
      expect(request['request_id'], isNotEmpty);
    },
  );

  test('publishing credentials remain restricted to administrators', () async {
    await mount(const TasksPage(), publishingResponse, role: 'operator');
    expect(button('GitHub publishing').disabled, true);
    button('GitHub publishing').click();
    await settle();
    expect(elements('input[type=password]'), isEmpty);
    expect(posts, isEmpty);
  });

  test(
    'missing SDK blockers link to configuration without triggering an installation',
    () async {
      final oldHash = web.window.location.hash;
      addTearDown(
        () => web.window.history.replaceState(
          null,
          '',
          oldHash.isEmpty ? '#' : oldHash,
        ),
      );
      final task = publishedTask()
        ..['error'] = 'Missing required SDK'
        ..['delivery_blocker'] = {'kind': 'missing_toolchain'};
      await mount(
        const TasksPage(taskId: 'task'),
        (r) => taskResponse(r, task),
      );
      await click('Install the required SDK');
      expect(web.window.location.hash, '#/configuration');
      expect(posts, isEmpty);
    },
  );

  test(
    'PR preview retains public destinations, validation, cancellation and explicit excerpt opt-in',
    () async {
      final task = publishedTask();
      await mount(
        const TasksPage(taskId: 'task'),
        (r) => taskResponse(r, task),
      );
      await click('Changes');
      await click('Open draft PR');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Open draft PR into: owner/repo'),
      );
      expect(posts, isEmpty);
      await click('Cancel');
      expect(posts, isEmpty);
      await click('Open draft PR');
      await type('PR title', '');
      await type('PR description (Markdown)', '');
      await click('Create draft PR');
      expect(posts, isEmpty);
      expect(
        elements('[role=alert]').map((e) => e.textContent).join(' '),
        contains('Enter a title.'),
      );
      await type('PR title', 'Edited title');
      await type('PR description (Markdown)', 'Edited public description');
      final description =
          field('PR description (Markdown)') as web.HTMLTextAreaElement;
      expect(description.value, isNot(contains('Finalized group discussion')));
      final checkbox =
          elements('input[type=checkbox]').single as web.HTMLInputElement;
      checkbox.click();
      await settle();
      expect(description.value, contains('Finalized group discussion'));
      await click('Create draft PR');
      expect(posts.single['path'], '/api/tasks/pull');
      expect(posts.single['title'], 'Edited title');
      expect(posts.single['body'], contains('Finalized group discussion'));
      expect(posts.single['reviewed'], true);
      expect(posts.single['head_sha'], 'b' * 40);
      expect(posts.single['request_id'], startsWith('task-'));
    },
  );

  test('task action retry keeps the exact durable request identity', () async {
    final task = {
      'id': 'task',
      'title': 'Retry candidate',
      'state': 'implementing',
      'repository': 'repo',
      'run_id': 'run',
    };
    await mount(
      const TasksPage(taskId: 'task'),
      (r) => taskResponse(r, task),
      loseFirst: true,
    );
    await click('Capture candidate');
    expect(posts.length, 1);
    await click('Capture candidate');
    expect(posts.length, 2);
    expect(posts[0]['request_id'], posts[1]['request_id']);
    expect(posts[1]['task_id'], 'task');
  });

  test(
    'publication confirmation binds the fresh reviewed exact commit',
    () async {
      final task = publishedTask()..remove('publish');
      task['state'] = 'review_ready';
      task['diff'] = 'review this exact patch';
      await mount(
        const TasksPage(taskId: 'task'),
        (r) => taskResponse(r, task),
      );
      await click('Review and publish');
      expect(posts, isEmpty);
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('review this exact patch'),
      );
      await click('Approve publication');
      expect(posts.single['path'], '/api/tasks/publish');
      expect(posts.single['head_sha'], 'b' * 40);
    },
  );

  test(
    'PR confirmation from a previous connection generation cannot publish',
    () async {
      final task = publishedTask();
      await mount(
        const TasksPage(taskId: 'task'),
        (r) => taskResponse(r, task),
      );
      await click('Changes');
      await click('Open draft PR');
      connection!.generation++;
      await click('Create draft PR');
      expect(posts, isEmpty);
    },
  );

  test(
    'task creation changes assignee to the selected managed repository',
    () async {
      await mount(
        const TasksPage(),
        (r) => r.url.path.endsWith('/organizations/directory')
            ? {
                'profiles': [
                  {
                    'id': 'maya',
                    'name': 'Maya',
                    'project': '/projects/repo1',
                    'use_worktree': true,
                  },
                  {
                    'id': 'olaf',
                    'name': 'Olaf',
                    'project': '/projects/repo2',
                    'use_worktree': true,
                  },
                ],
              }
            : r.url.path.endsWith('/projects/browse')
            ? {'root': '/projects'}
            : {'tasks': [], 'workspaces': [], 'agents': []},
      );
      await click('Create task');
      posts.clear();
      await type('Task title', 'A repo2 task');
      await type(
        'Change, acceptance criteria and required checks',
        'Keep behavior and verify required checks',
      );
      await choose('Repository', 'repo2');
      expect((field('Assigned agent') as web.HTMLSelectElement).value, 'olaf');
      await click('Create task');
      expect(posts.single['path'], '/api/tasks/create');
      expect(posts.single['repository'], 'repo2');
      expect(posts.single['profile_id'], 'olaf');
      expect(posts.single['base_ref'], '');
    },
  );

  test(
    'task completion requires reviewed evidence and explicit inspection',
    () async {
      final task = publishedTask()..['current_upstream_sha'] = 'a' * 40;
      await mount(
        const TasksPage(taskId: 'task'),
        (r) => taskResponse(r, task),
      );
      await click('Mark task done');
      expect(button('Confirm completion').disabled, true);
      await type(
        'Reviewed evidence and completion reason',
        'This work is incorporated upstream.',
      );
      expect(button('Confirm completion').disabled, true);
      (elements('input[type=checkbox]').single as web.HTMLInputElement).click();
      await settle();
      expect(button('Confirm completion').disabled, false);
      await click('Confirm completion');
      expect(posts.single['path'], '/api/tasks/complete');
      expect(posts.single['inspected'], true);
      expect(posts.single['head_sha'], 'b' * 40);
      expect(posts.single['upstream_sha'], 'a' * 40);
    },
  );

  test(
    'viewer sees evidence and cannot publish or mutate task policy',
    () async {
      final task = publishedTask();
      await mount(
        const TasksPage(taskId: 'task'),
        (r) => taskResponse(r, task),
        role: 'viewer',
      );
      await click('Changes');
      expect(button('Open draft PR').disabled, true);
      expect(button('Review and publish').disabled, true);
      button('Open draft PR').click();
      await settle();
      expect(posts, isEmpty);
    },
  );

  test('SVG commit graph retains parent edges without a canvas', () async {
    final task = publishedTask();
    task['commit_graph'] = [
      {
        'sha': 'b' * 40,
        'parents': ['a' * 40],
        'subject': 'Candidate',
        'author': 'Maya',
      },
      {'sha': 'a' * 40, 'parents': [], 'subject': 'Base', 'author': 'Maya'},
    ];
    task['base_sha'] = 'a' * 40;
    await mount(const TasksPage(taskId: 'task'), (r) => taskResponse(r, task));
    await click('Changes');
    expect(elements('svg[role=img]').length, 1);
    expect(elements('svg[role=img] path').length, 1);
    expect(elements('svg[role=img] circle').length, 2);
    expect(elements('canvas'), isEmpty);
  });

  test(
    'knowledge create and review require input and retain provenance',
    () async {
      final record = {
        'id': 'knowledge-1',
        'title': 'Reported finding',
        'body': 'Evidence and limitations',
        'kind': 'finding',
        'state': 'reported',
        'repository': 'repo',
        'created_at': '2026-10-13',
        'source': {
          'kind': 'task',
          'task_id': 'task',
          'source_sha': 'a' * 40,
          'checks_verified': false,
        },
        'audit': [],
      };
      await mount(
        const KnowledgePage(organizationId: 'org'),
        (r) => r.url.path.endsWith('/organizations/directory')
            ? {
                'organizations': [
                  {'id': 'org', 'name': 'Team'},
                ],
              }
            : r.url.path.endsWith('/knowledge')
            ? {
                'records': [record],
                'repositories': ['repo'],
                'next_before': null,
              }
            : {'workspaces': [], 'agents': []},
      );
      posts.clear();
      await click('Add knowledge');
      await click('Save knowledge');
      expect(posts, isEmpty);
      await type('Title', 'New finding');
      await type(
        'Finding, evidence and limitations',
        'A scoped reported finding',
      );
      await click('Save knowledge');
      expect(posts.first['mode'], 'create');
      expect(posts.first['organization_id'], 'org');
      expect(posts.first['kind'], 'finding');
      posts.clear();
      await click('Reported findingfinding / reported / repo');
      expect(
        web.document.getElementById('task-dom-test')!.textContent,
        contains('Exact-commit required checks verified: false'),
      );
      await click('Review record');
      await click('Record decision');
      expect(posts, isEmpty);
      await type('Review reason', 'Reviewed current code and limitations');
      await click('Record decision');
      expect(posts.first['mode'], 'review');
      expect(posts.first['id'], 'knowledge-1');
    },
  );

  test(
    'discovery config and run retain organization scope and proposal-only semantics',
    () async {
      final snapshot = <String, dynamic>{
        'organizations': [
          {'id': 'org', 'name': 'Product organization'},
        ],
        'groups': [
          {'id': 'product', 'name': 'Product', 'organization_id': 'org'},
        ],
        'profiles': [
          {'id': 'maya', 'organization_id': 'org', 'project': '/projects/repo'},
        ],
        'projects_root': '/projects',
        'policies': {
          'org': {
            'product_brief': 'Validated tasks',
            'discovery_group_id': 'product',
            'discovery_repository': 'repo',
            'discovery_interval_hours': 168,
            'discovery_max_proposals': 3,
          },
        },
        'meetings': [],
      };
      await mount(
        const DiscoveryPanel(),
        (r) => r.url.path.endsWith('/discovery')
            ? snapshot
            : {'workspaces': [], 'agents': []},
      );
      await click(
        'Product discoveryProduct goals → grounded proposals → approved drafts',
      );
      await click('Configure discovery');
      await type('Product brief', 'Grounded task discovery');
      await click('Save discovery direction');
      expect(posts.single['path'], '/api/tasks/discovery');
      expect(posts.single['organization_id'], 'org');
      expect(posts.single['mode'], 'configure');
      expect(posts.single['discovery_enabled'], false);
      expect(posts.single['discovery_group_id'], 'product');
      expect(posts.single['discovery_repository'], 'repo');
      posts.clear();
      await click('Run discovery now');
      expect(posts.single['mode'], 'run');
      expect(posts.single['organization_id'], 'org');
      expect(posts.single.containsKey('launch'), false);
    },
  );

  test(
    'knowledge supersession requires replacement and a recorded reason',
    () async {
      Map<String, dynamic> record(String id) => {
        'id': id,
        'title': id,
        'body': 'Reported evidence',
        'kind': 'finding',
        'state': 'reported',
        'repository': 'repo',
        'created_at': '2026-10-13',
        'source': {'kind': 'manual'},
        'audit': [],
      };
      await mount(
        const KnowledgePage(organizationId: 'org'),
        (r) => r.url.path.endsWith('/organizations/directory')
            ? {
                'organizations': [
                  {'id': 'org', 'name': 'Team'},
                ],
              }
            : r.url.path.endsWith('/knowledge')
            ? {
                'records': [record('original'), record('replacement')],
                'repositories': ['repo'],
                'next_before': null,
              }
            : {'workspaces': [], 'agents': []},
      );
      posts.clear();
      await click('originalfinding / reported / repo');
      await click('Supersede record');
      await type('Review reason', 'Replaced by newer verified source evidence');
      await click('Record decision');
      expect(posts, isEmpty);
      await choose('Replacement record', 'replacement');
      await click('Record decision');
      expect(posts.first['mode'], 'supersede');
      expect(posts.first['replacement_id'], 'replacement');
      expect(posts.first['id'], 'original');
    },
  );
  test(
    'task index opens the workflow and retains original list navigation',
    () async {
      await mount(
        const TasksPage(),
        (request) => taskResponse(request, publishedTask()),
      );
      final tabs = elements('button[role="tab"]').toList();
      final workflow = tabs.firstWhere(
        (node) => node.textContent!.startsWith('Workflow'),
      );
      final list = tabs.firstWhere(
        (node) => node.textContent!.startsWith('List'),
      );
      expect(workflow.getAttribute('aria-selected'), 'true');
      expect(button('Create task').disabled, false);
      expect(elements('canvas'), isEmpty);
      posts.clear();
      list.click();
      await settle();
      expect(list.getAttribute('aria-selected'), 'true');
      expect(
        elements('h2').any((node) => node.textContent == 'Ready task'),
        true,
      );
      expect(button('Open task').disabled, false);
      workflow.click();
      await settle();
      expect(workflow.getAttribute('aria-selected'), 'true');
      expect(
        posts,
        isEmpty,
        reason: 'Changing presentation never changes task state.',
      );
      list.click();
      await settle();
      await click('Open task');
      expect(web.window.location.hash, '#/tasks/task');
      expect(posts, isEmpty);
    },
  );
}
