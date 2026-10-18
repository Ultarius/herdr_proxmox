@TestOn('browser')
library;

import 'dart:convert';
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
import 'package:herdr_jaspr_dashboard/pages/builds_page.dart';
import 'package:herdr_jaspr_dashboard/pages/project_analysis_card.dart';
import 'package:herdr_jaspr_dashboard/pages/update_card.dart';
import 'package:herdr_jaspr_dashboard/pages/toolchain_card.dart';
import 'package:herdr_jaspr_dashboard/pages/dashboard_access_controls.dart';
import 'package:herdr_jaspr_dashboard/pages/project_git_panel.dart';
import 'package:herdr_jaspr_dashboard/pages/integration_board.dart';
import 'package:herdr_jaspr_dashboard/pages/cli_setup_page.dart';
import 'package:herdr_jaspr_dashboard/terminal.dart';

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

Future<void> settle([int ms = 50]) =>
    Future<void>.delayed(Duration(milliseconds: ms));

/// Waits for a condition the browser reaches across frames — poll responses
/// and terminal writes land asynchronously, so a fixed delay is a race that a
/// loaded machine loses.
Future<void> until(bool Function() condition, {int timeoutMs = 5000}) async {
  final deadline = DateTime.now().add(Duration(milliseconds: timeoutMs));
  while (!condition() && DateTime.now().isBefore(deadline)) {
    await settle(25);
  }
}

Iterable<web.HTMLElement> elements(String selector) sync* {
  final nodes = web.document.querySelectorAll('#operations-test $selector');
  for (var i = 0; i < nodes.length; i++) yield nodes.item(i) as web.HTMLElement;
}

web.HTMLButtonElement button(String label) =>
    elements('button').where((e) => e.textContent?.trim() == label).last
        as web.HTMLButtonElement;
Future<void> click(String label) async {
  button(label).click();
  await settle();
}

Future<void> type(String label, String value) async {
  final item = elements(
    'label',
  ).firstWhere((e) => e.querySelector('span')?.textContent == label);
  final field = item.querySelector('input,textarea');
  if (field is web.HTMLInputElement) field.value = value;
  if (field is web.HTMLTextAreaElement) field.value = value;
  field!.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await settle();
}

Future<void> script(String url) async {
  final loaded = Completer<void>();
  final element = web.HTMLScriptElement()..src = url;
  element.addEventListener('load', ((web.Event _) => loaded.complete()).toJS);
  element.addEventListener(
    'error',
    ((web.Event _) => loaded.completeError(
      StateError('Cannot load $url'),
    )).toJS,
  );
  web.document.head!.appendChild(element);
  await loaded.future.timeout(const Duration(seconds: 5));
}

void main() {
  late ClientAppBinding binding;
  DashboardBloc? connection;
  final requests = <Map<String, dynamic>>[];
  setUpAll(() async {
    JuiceLoggerConfig.minLevel = Level.off;
    web.document.body!.appendChild(
      web.HTMLDivElement()..id = 'operations-test',
    );
    final css = web.HTMLLinkElement()
      ..rel = 'stylesheet'
      ..href = '../web/vendor/xterm/xterm.css';
    web.document.head!.appendChild(css);
    await script('../web/vendor/xterm/xterm.js');
    binding = ClientAppBinding();
  });
  setUp(() => requests.clear());
  tearDown(() async {
    for (final close in elements(
      '.dialog-layer button',
    ).where((e) => e.textContent == 'Close').toList()) {
      close.click();
    }
    await settle();
    binding.detachRootComponent();
    await settle();
    if (connection != null) {
      await connection!.disconnect();
      connection = null;
    }
    await BlocScope.reset();
    web.document.getElementById('operations-test')!.textContent = '';
  });
  Future<void> mount(
    Component page,
    dynamic Function(http.Request) response, {
    String role = 'admin',
  }) async {
    connection = DashboardBloc(
      client: MockClient((r) async {
        requests.add({
          'method': r.method,
          'endpoint': r.url.path,
          'path': r.url.path,
          'query': r.url.query,
          if (r.body.isNotEmpty) ...jsonDecode(r.body) as Map<String, dynamic>,
        });
        final value = await response(r);
        if (value is http.Response) return value;
        return http.Response(jsonEncode(value), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection!);
    connection!.connect('token');
    await settle();
    connection!.operator.value = {'role': role};
    binding.attachRootComponent(_Host(page), attachTo: '#operations-test');
    await settle();
  }

  List<Map<String, dynamic>> posts(String path) => requests
      .where((r) => r['method'] == 'POST' && r['endpoint'] == '/api/$path')
      .toList();
  final build = {
    'id': 'immutable-build',
    'repository': 'repo',
    'state': 'complete',
    'target': 'a' * 40,
    'artifact': {'bytes': 3},
    'deployment_package': {
      'sha256': 'package-hash',
      'bytes': 3,
      'required_checks_verified': false,
      'checks': [
        {'id': 'required-test', 'required': true, 'status': 'failed'},
      ],
    },
  };
  dynamic buildResponse(http.Request r) {
    if (r.url.path == '/api/validation')
      return {
        'runs': [build],
        'executor': 'service',
        'queued': 0,
      };
    if (r.url.path == '/api/build')
      return {'build_id': 'running-build', 'source_sha': 'b' * 40};
    if (r.url.path == '/api/updates')
      return {
        'state': 'idle',
        'rollback_available': true,
        'provenance_repairable': true,
        'supported': true,
      };
    if (r.url.path == '/api/validation/artifact')
      return http.Response.bytes([1, 2], 200);
    return {'workspaces': [], 'agents': []};
  }

  test(
    'discovery displays source, inherited SDK evidence and host readiness',
    () async {
      await mount(const ProjectAnalysisCard(path: 'repo'), (request) {
        if (request.url.path.endsWith('/analysis')) {
          return {
            'source': {
              'scope': 'working_tree',
              'at': '2026-10-16',
              'revision': '',
            },
            'coverage': {
              'files': 2,
              'directories': 1,
              'unknown': 0,
              'truncated': true,
            },
            // The resolved view the gateway serves: discovery plus the host
            // assessment, so readiness is measured rather than declared.
            'requirements': [
              {
                'tool': 'dotnet',
                'label': '.NET SDK',
                'constraint': '8.0.100',
                'notation': 'dotnet',
                'components': ['Api'],
                'override': null,
                'conflicts': [],
                'assessment': {
                  'verdict': 'undecidable',
                  'measured': '8.0.400',
                  'selected': '/usr/bin/dotnet',
                  'detail':
                      'SDK selection for the recorded lookup context has not been assessed.',
                  'installer': false,
                  'provisionable': false,
                  'offer': null,
                },
              },
            ],
            'provisionable': [],
            'components': [
              {
                'path': 'Api',
                'basis': 'manifest',
                'languages': [
                  {'label': 'C#'},
                ],
                'ecosystems': [
                  {'label': '.NET'},
                ],
                'frameworks': [
                  {'label': 'ASP.NET Core'},
                ],
                'candidate_commands': [
                  {
                    'command': ['dotnet', 'test'],
                    'source': 'convention',
                  },
                ],
                'requirements': [
                  {
                    'tool': 'dotnet',
                    'label': '.NET SDK',
                    'selected': '8.0.100',
                    'established': true,
                    'observations': [
                      {'file': 'global.json', 'field': 'sdk.version'},
                    ],
                    'resolution': {
                      'context': {'directory': 'Api'},
                      'shadowed': [],
                      'boundary_note':
                          'Host configuration above the repository root is not inspected.',
                    },
                  },
                ],
              },
            ],
          };
        }
        return {'workspaces': [], 'agents': []};
      });
      final content = web.document
          .getElementById('operations-test')!
          .textContent!;
      expect(content, contains('Source: working_tree'));
      expect(content, contains('global.json#sdk.version'));
      expect(content, contains('SDK lookup: Api'));
      expect(content, contains('Host configuration above the repository root'));
      expect(content, contains('partial: a limit stopped the scan'));
      expect(content, contains('Host readiness'));
      expect(content, contains('Measured 8.0.400'));
      expect(content, contains('/usr/bin/dotnet'));
      expect(content, contains('Component: Api'));
      expect(content, contains('.NET · C#'));
      expect(content, contains('Frameworks: ASP.NET Core'));
      expect(content, contains('Candidate command: dotnet test (convention)'));
      expect(content, isNot(contains(' ·  · ')));
      expect(content, isNot(contains("Instance of 'Text'")));
      expect(
        content,
        contains('No requirement of this project is installable'),
      );
      await click('Re-analyse');
      expect(
        requests
            .where((r) => r['endpoint'] == '/api/projects/analysis')
            .last['query'],
        contains('refresh=1'),
      );
    },
  );

  test(
    'committed environment preparation queues and cancels without claiming test success',
    () async {
      final check = {
        'id': 'Api:build',
        'cwd': 'Api',
        'arguments': ['dotnet', 'build', 'App.csproj'],
        'source': 'convention',
      };
      Map<String, dynamic> fixture(bool approved) => {
        'digest': approved ? 'approved-plan' : 'plan',
        'preparation_version': 1,
        'check_candidates': [check],
        'selected_checks': approved ? [check] : [],
        'requirements': [
          {
            'state': 'acquire',
            'capability': 'dotnet-sdk',
            'version': '8.0.100',
            'components': ['Api'],
            'artifact': {},
            'activation': 'available',
          },
        ],
      };
      await mount(const ProjectAnalysisCard(path: 'repo'), (request) {
        if (request.url.path.endsWith('/environment/plan'))
          return fixture(false);
        if (request.url.path.endsWith('/environment/select'))
          return fixture(true);
        if (request.url.path.endsWith('/environment/prepare'))
          return {'id': 'job', 'state': 'queued'};
        if (request.url.path.endsWith('/environment/cancel'))
          return {'id': 'job', 'state': 'cancelled'};
        return {
          'components': [],
          'requirements': [],
          'workspaces': [],
          'agents': [],
        };
      });
      await click('Plan environment');
      expect(posts('projects/environment/plan').single['path'], 'repo');
      await click('Approve Api: dotnet build App.csproj (convention)');
      expect(posts('projects/environment/select').single['checks'], [
        'Api:build',
      ]);
      await click('Prepare SDKs');
      expect(
        posts('projects/environment/prepare').single['digest'],
        'approved-plan',
      );
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('SDK preparation queued'),
      );
      await click('Cancel SDK preparation');
      expect(posts('projects/environment/cancel').single['id'], 'job');
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('SDK preparation cancelled'),
      );
      expect(
        web.document.getElementById('operations-test')!.textContent,
        isNot(contains('checks passed')),
      );
      await click('Prepare SDKs');
      expect(posts('projects/environment/prepare').last['retry'], isTrue);
      await click('Cancel SDK preparation');
    },
  );

  test('Dependency approval is separate from check selection', () async {
    Map<String, dynamic> fixture(bool allowed) => {
      'digest': allowed ? 'restore-approved' : 'plan',
      'restore_permission': {'allowed': allowed},
      'requirements': [
        {
          'capability': 'python',
          'restore_recipes': [
            {'manager': 'uv', 'requires_restore': true},
          ],
        },
      ],
      'check_candidates': [],
      'selected_checks': [],
    };
    await mount(const ProjectAnalysisCard(path: 'repo'), (request) {
      if (request.url.path.endsWith('/environment/plan')) return fixture(false);
      if (request.url.path.endsWith('/environment/authorize-restore')) {
        return fixture(true);
      }
      return {
        'components': [],
        'requirements': [],
        'workspaces': [],
        'agents': [],
      };
    });
    await click('Plan environment');
    await click('Approve dependency restoration');
    final grant = posts('projects/environment/authorize-restore').single;
    expect(grant['digest'], 'plan');
    expect(grant['allowed'], isTrue);
    expect(grant.containsKey('retry'), isFalse);
    expect(posts('projects/environment/select'), isEmpty);
    expect(
      web.document.getElementById('operations-test')!.textContent,
      contains('Revoke dependency restoration'),
    );
  });

  test(
    'discovery distinguishes installed runtimes, deduplicates reasons and exposes unknown paths',
    () async {
      const reason = 'Python source was found but no version is declared.';
      await mount(const ProjectAnalysisCard(path: 'repo'), (request) {
        if (request.url.path.endsWith('/analysis'))
          return {
            'coverage': {
              'files': 7,
              'unknown': 6,
              'unknown_evidence': [
                for (var i = 0; i < 6; i++) 'data/$i.unknown',
              ],
            },
            'requirements': [
              {
                'tool': 'python',
                'label': 'Python',
                'constraint': '',
                'assessment': {'verdict': 'satisfied', 'measured': '3.13.5'},
              },
            ],
            'components': [
              {
                'path': 'scripts',
                'basis': 'inferred',
                'requirements': [
                  {
                    'tool': 'python',
                    'label': 'Python',
                    'established': false,
                    'reason': reason,
                  },
                ],
                'unresolved': [
                  {'reason': reason},
                  {'reason': 'A separate issue remains.'},
                ],
              },
            ],
          };
        return {'workspaces': [], 'agents': []};
      });
      final root = web.document.getElementById('operations-test')!;
      expect(
        root.textContent,
        contains('Installed — required version unknown'),
      );
      expect(root.textContent!.split(reason), hasLength(2));
      expect(root.textContent, contains('A separate issue remains.'));
      final details = root.querySelector('details') as web.HTMLDetailsElement;
      expect(details.open, isFalse);
      (details.querySelector('summary') as web.HTMLElement).click();
      await settle();
      expect(details.open, isTrue);
      expect(details.textContent, contains('Showing 6 of 6 paths.'));
      for (var i = 0; i < 6; i++)
        expect(details.textContent, contains('data/$i.unknown'));
    },
  );

  test(
    'build service install queues once, shows failures and supports retry',
    () async {
      var state = 'idle';
      dynamic response(http.Request r) {
        if (r.url.path == '/api/build-service/install') {
          expect(r.body, '{}');
          state = 'queued';
          return {'state': state};
        }
        if (r.url.path == '/api/build-service') {
          return {
            'state': state,
            'supported': true,
            if (state == 'failed')
              'error': 'CPU/memory cgroup controllers are unavailable.',
          };
        }
        if (r.url.path == '/api/validation')
          return {'runs': [], 'executor': 'gateway'};
        return buildResponse(r);
      }

      await mount(const BuildsPage(), response);
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('Build-service installation: Not requested'),
      );
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('Automatic capture works without this service'),
      );
      await click('Install build service');
      expect(posts('build-service/install'), hasLength(1));
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('Build-service installation: Waiting to start'),
      );
      expect(button('Installing build service…').disabled, isTrue);
      state = 'failed';
      await click('Refresh builds');
      await until(
        () => web.document
            .getElementById('operations-test')!
            .textContent!
            .contains('CPU/memory cgroup controllers are unavailable.'),
      );
      await click('Install build service');
      expect(posts('build-service/install'), hasLength(2));
    },
  );

  test('build service install is hidden from non-administrators', () async {
    await mount(const BuildsPage(), (r) {
      if (r.url.path == '/api/build-service')
        return {'state': 'idle', 'supported': true};
      if (r.url.path == '/api/validation')
        return {'runs': [], 'executor': 'gateway'};
      return buildResponse(r);
    }, role: 'operator');
    expect(
      elements('button').any((e) => e.textContent == 'Install build service'),
      isFalse,
    );
    expect(posts('build-service/install'), isEmpty);
  });

  test(
    'deployment approval shows immutable evidence and cancellation sends nothing',
    () async {
      await mount(const BuildsPage(), buildResponse);
      await click('Deploy this build');
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('package-hash'),
      );
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('Required checks not passed: required-test'),
      );
      expect(posts('updates/promote'), isEmpty);
      await click('Cancel');
      expect(posts('updates/promote'), isEmpty);
      await click('Deploy this build');
      await click('Approve and deploy');
      expect(posts('updates/promote').single['run_id'], 'immutable-build');
    },
  );
  test(
    'rollback and provenance each require their own explicit confirmation',
    () async {
      await mount(const BuildsPage(), buildResponse);
      await click('Roll back last deployment');
      expect(posts('updates/rollback'), isEmpty);
      await click('Cancel');
      await click('Roll back last deployment');
      await click('Restore previous deployment');
      expect(posts('updates/rollback'), hasLength(1));
      await click('Resolve source identity');
      expect(posts('updates/provenance'), isEmpty);
      // The dialog confirm button and page trigger share a label; choose the dialog.
      (elements('.dialog-layer button').last as web.HTMLButtonElement).click();
      await settle();
      expect(posts('updates/provenance'), hasLength(1));
    },
  );
  test(
    'downloads bind the requested build and reject a mismatched manifest byte count',
    () async {
      await mount(const BuildsPage(), buildResponse);
      await click('Download gateway + UI package');
      final artifact = requests.singleWhere(
        (r) => r['path'] == '/api/validation/artifact',
      );
      expect(artifact['query'], 'id=immutable-build&kind=deployment');
      expect(
        elements('[role=alert]').map((e) => e.textContent).join(),
        contains('does not match the manifest (3 bytes)'),
      );
    },
  );
  test(
    'performance diagnostics are on demand and show their process scope',
    () async {
      await mount(const BuildsPage(), (r) {
        if (r.url.path == '/api/diagnostics/performance')
          return {
            'pid': 42,
            'started_at': '2026-10-18T10:00:00Z',
            'sample_window': 256,
            'operations': {
              'git.command': {
                'count': 10,
                'failed': 1,
                'p50_ms': 2,
                'p95_ms': 8,
                'max_ms': 10,
              },
            },
            'counters': {'repository.cache_hit': 3, 'repository.cache_miss': 1},
          };
        return buildResponse(r);
      });
      expect(
        requests.where((r) => r['path'] == '/api/diagnostics/performance'),
        isEmpty,
      );
      await click('Refresh performance diagnostics');
      expect(web.document.body!.textContent, contains('Process 42'));
      expect(web.document.body!.textContent, contains('p95 8 ms'));
      expect(
        web.document.body!.textContent,
        contains('not a fleet-wide total'),
      );
      expect(
        requests.where((r) => r['path'] == '/api/diagnostics/performance'),
        hasLength(1),
      );
    },
  );

  test('viewer cannot deploy, rollback or resolve source identity', () async {
    await mount(const BuildsPage(), buildResponse, role: 'viewer');
    expect(
      elements(
        'button',
      ).where((e) => e.textContent == 'Refresh performance diagnostics'),
      isEmpty,
    );
    expect(
      elements('button').where(
        (e) => [
          'Deploy this build',
          'Roll back last deployment',
          'Resolve source identity',
        ].contains(e.textContent),
      ),
      isEmpty,
    );
  });
  test(
    'account generation change invalidates an open deployment approval',
    () async {
      await mount(const BuildsPage(), buildResponse);
      await click('Deploy this build');
      await connection!.disconnect();
      await click('Approve and deploy');
      expect(posts('updates/promote'), isEmpty);
    },
  );
  test(
    'alpha install preserves explicit alpha acknowledgement and selected version',
    () async {
      await mount(
        const UpdateCard(),
        (r) => r.url.path == '/api/updates'
            ? {
                'state': 'idle',
                'supported': true,
                'latest': 'v1',
                'available': true,
              }
            : r.url.path == '/api/updates/alphas'
            ? {
                'releases': [
                  {'version': 'alpha-test', 'notes': 'Experimental'},
                ],
              }
            : {'workspaces': [], 'agents': []},
      );
      await click('Browse alpha builds');
      final selected = elements('select').single as web.HTMLSelectElement;
      selected.value = 'alpha-test';
      selected.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
      await settle();
      await click('Install selected alpha');
      expect(posts('updates/install'), isEmpty);
      (elements('.dialog-layer button').last as web.HTMLButtonElement).click();
      await settle();
      expect(
        posts('updates/install').single,
        containsPair('version', 'alpha-test'),
      );
      expect(posts('updates/install').single, containsPair('channel', 'alpha'));
      expect(
        posts('updates/install').single,
        containsPair('confirm_alpha', true),
      );
    },
  );
  test(
    'SDK progress shows the phase and falls back when no percentage is known',
    () async {
      var state = 'running';
      Map<String, dynamic>? progress = {'percent': 43, 'phase': 'download'};
      await mount(
        const ToolchainCard(),
        (r) => r.url.path == '/api/sdk'
            ? {
                'supported': true,
                'state': state,
                'toolchains': {},
                'progress': progress,
              }
            : {'workspaces': [], 'agents': []},
      );
      expect(elements('progress').single.getAttribute('value'), '43');
      expect(elements('progress').single.getAttribute('max'), '100');
      expect(
        elements('progress').single.getAttribute('aria-label'),
        'Downloading SDK',
      );
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('Downloading SDK · 43%'),
      );
      progress = {'percent': 101};
      await click('Refresh toolchain status');
      expect(elements('progress').single.hasAttribute('value'), false);
      state = 'queued';
      progress = null;
      await click('Refresh toolchain status');
      expect(elements('progress').single.hasAttribute('value'), false);
      state = 'complete';
      await click('Refresh toolchain status');
      expect(elements('progress'), isEmpty);
    },
  );
  test(
    'SDK install allows only declared versions and administrators',
    () async {
      dynamic sdk(http.Request r) => r.url.path == '/api/sdk'
          ? {
              'supported': true,
              'state': 'idle',
              'toolchains': {
                'dart': {
                  'label': 'Dart',
                  'required': '3.12',
                  'installed': false,
                },
                'unversioned': {'label': 'Unversioned', 'installed': false},
              },
            }
          : {'workspaces': [], 'agents': []};
      await mount(const ToolchainCard(), sdk, role: 'viewer');
      expect(button('Install Dart').disabled, true);
      button('Install Dart').click();
      await settle();
      expect(posts('sdk/install'), isEmpty);
      connection!.operator.value = {'role': 'admin'};
      binding.detachRootComponent();
      binding.attachRootComponent(
        _Host(const ToolchainCard()),
        attachTo: '#operations-test',
      );
      await settle();
      expect(button('Install Unversioned').disabled, true);
      await click('Install Dart');
      expect(posts('sdk/install').single['toolchain'], 'dart');
    },
  );
  test(
    'SSH-only access requires a key and tested tunnel acknowledgement',
    () async {
      await mount(
        const DashboardAccessControls(keyCount: 1),
        (r) => r.url.path == '/api/dashboard-access'
            ? {'mode': 'lan'}
            : {'workspaces': [], 'agents': []},
      );
      await click('Require SSH tunnel');
      expect(button('Apply access mode').disabled, true);
      expect(posts('dashboard-access'), isEmpty);
      (elements('input[type=checkbox]').single as web.HTMLInputElement).click();
      await settle();
      await click('Apply access mode');
      expect(posts('dashboard-access').single['mode'], 'ssh');
      expect(posts('dashboard-access').single['tunnel_ready'], true);
    },
  );
  test(
    'base branch update re-inspects and binds exact plan plus request identity',
    () async {
      final plan = {
        'path': 'repo',
        'branch': 'main',
        'base': 'origin/main',
        'head': 'c' * 40,
        'target': 'd' * 40,
      };
      await mount(ProjectGitPanel(path: 'repo', onOpen: (_) {}), (r) {
        if (r.url.path == '/api/projects/git') {
          final body = jsonDecode(r.body) as Map;
          if (body['action'] == 'update_base') return {'state': 'complete'};
          return {
            'repository': true,
            'base_update': plan,
            'worktrees': [
              {
                'path': 'repo',
                'branch': 'main',
                'commit': 'c' * 40,
                'counts': {'added': 0, 'modified': 0, 'deleted': 0},
                'changes': [],
              },
            ],
          };
        }
        return {'workspaces': [], 'agents': []};
      });
      await click('Update base branch');
      await click('Review exact target');
      final inspection = posts('projects/git').last;
      expect(inspection['path'], 'repo');
      expect(inspection['base'], 'origin/main');
      expect(inspection.containsKey('action'), false);
      expect(
        web.document.getElementById('operations-test')!.textContent,
        contains('d' * 40),
      );
      await click('Update base branch');
      final update = posts(
        'projects/git',
      ).singleWhere((r) => r['action'] == 'update_base');
      expect(update['head'], 'c' * 40);
      expect(update['target'], 'd' * 40);
      expect(update['request_id'], isNotEmpty);
    },
  );
  test(
    'blocker waiver records selected immutable event and reason without a false pass',
    () async {
      final event = {
        'id': 'event',
        'repository': 'repo',
        'name': 'Maya',
        'state': 'validation_pending',
        'path': '/projects/repo',
        'target': 'e' * 40,
        'updated_at': 'exact-version',
        'verification': {
          'target_incorporated': true,
          'conflicts': 0,
          'merging': false,
        },
      };
      await mount(
        const IntegrationBoard(repository: 'repo'),
        (r) => r.url.path == '/api/integration'
            ? {
                'coordination': {
                  'configurations': [
                    {'repository': 'repo', 'enabled': false},
                  ],
                  'events': [event],
                },
              }
            : r.url.path == '/api/validation'
            ? {'executor': 'service', 'runs': []}
            : {'workspaces': [], 'agents': []},
      );
      await click('Review blockers');
      expect(button('Approve selected actions').disabled, true);
      (elements('.dialog-layer input[type=checkbox]').first
              as web.HTMLInputElement)
          .click();
      await settle();
      await type(
        'Reason for approval (maximum 1,000 characters)',
        'Accepted known validation gap',
      );
      await click('Approve selected actions');
      final approval = posts('integration/blockers').single;
      expect(approval['request_id'], matches(RegExp(r'^[0-9a-f]{32}$')));
      expect(approval['reason'], 'Accepted known validation gap');
      expect(approval['selections'], [
        {
          'id': 'event',
          'repository': 'repo',
          'target': 'e' * 40,
          'updated_at': 'exact-version',
          'action': 'waive_validation',
        },
      ]);
      expect(approval.containsKey('passed'), false);
    },
  );
  test(
    'base inspection refuses a destination branch changed since selection',
    () async {
      var inspected = false;
      await mount(ProjectGitPanel(path: 'repo', onOpen: (_) {}), (r) {
        if (r.url.path != '/api/projects/git')
          return {'workspaces': [], 'agents': []};
        final body = jsonDecode(r.body) as Map;
        if (body['base'] != null) inspected = true;
        return {
          'repository': true,
          'base_update': {
            'path': 'repo',
            'branch': inspected ? 'other-branch' : 'main',
            'base': 'origin/main',
          },
          'worktrees': [
            {
              'path': 'repo',
              'branch': 'main',
              'counts': {'added': 0, 'modified': 0, 'deleted': 0},
              'changes': [],
            },
          ],
        };
      });
      await click('Update base branch');
      await click('Review exact target');
      expect(
        elements('[role=alert]').map((e) => e.textContent).join(),
        contains('Destination branch changed'),
      );
      expect(
        posts('projects/git').where((r) => r['action'] == 'update_base'),
        isEmpty,
      );
    },
  );
  test(
    'terminal renders VT output in DOM, survives owner rerender and resizes without canvas',
    () async {
      final terminal = BrowserTerminal();
      final changes = <(int, int)>[];
      terminal.onResize = (c, r) => changes.add((c, r));
      await mount(
        TerminalView(terminal, height: 340),
        (_) => {'workspaces': [], 'agents': []},
      );
      await until(() => elements('.xterm-rows').isNotEmpty);
      terminal.write('\x1b[31mRed output\x1b[0m\r\nsecond line');
      await until(
        () =>
            elements(
              '.xterm-rows',
            ).single.textContent?.contains('Red output') ==
            true,
      );
      expect(
        elements('.xterm-rows').single.textContent,
        contains('Red output'),
      );
      expect(elements('canvas'), isEmpty);
      ui.refreshDialogs();
      await settle();
      expect(
        elements('.xterm-rows').single.textContent,
        contains('Red output'),
      );
      expect(terminal.getText(), contains('Red output\nsecond line'));
      expect(
        changes,
        isNotEmpty,
      ); // DOM cell measurements drove the initial fit.
      terminal.resize(100, 20);
      expect(changes.last, (100, 20));
      terminal.dispose();
    },
  );
  test(
    'CLI input is serialized and leaving the page closes exactly its active session',
    () async {
      await mount(const CliSetupPage(), (r) {
        if (r.url.path == '/api/cli-setup')
          return {
            'clis': [
              {
                'id': 'codex',
                'name': 'Codex',
                'installed': true,
                'status': 'not_configured',
                'detail': 'Demo',
              },
            ],
          };
        if (r.url.path == '/api/cli-setup/start')
          return {'id': 'setup-identity'};
        if (r.url.path == '/api/cli-setup/poll')
          return {
            'output':
                '\x1b[32mProvider login\x1b[0m\r\nhttps://provider.example/login\r\n',
            'cursor': 1,
            'running': true,
          };
        if (r.url.path == '/api/sdk') return {'supported': false};
        if (r.url.path == '/api/ssh-access') return {'key_count': 0};
        return {'workspaces': [], 'agents': []};
      });
      await click('Connect account');
      await until(
        () => elements(
          '.xterm-rows',
        ).any((e) => (e.textContent ?? '').contains('session')),
      );
      await type('Authorization code or API key', 'hidden-code');
      await click('Send to terminal');
      expect(posts('cli-setup/input').single['id'], 'setup-identity');
      expect(posts('cli-setup/input').single['data'], 'hidden-code\r');
      expect(
        (elements('input[type=password]').single as web.HTMLInputElement).value,
        '',
      );
      expect(elements('canvas'), isEmpty);
      binding.detachRootComponent();
      await settle();
      expect(posts('cli-setup/close').single['id'], 'setup-identity');
    },
  );
  test(
    'closing CLI invalidates queued inputs and its terminal can reopen safely',
    () async {
      final firstInput = Completer<http.Response>();
      var starts = 0;
      await mount(const CliSetupPage(), (r) {
        if (r.url.path == '/api/cli-setup')
          return {
            'clis': [
              {
                'id': 'codex',
                'name': 'Codex',
                'installed': true,
                'status': 'not_configured',
                'detail': 'Demo',
              },
            ],
          };
        if (r.url.path == '/api/cli-setup/start')
          return {'id': 'setup-${++starts}'};
        if (r.url.path == '/api/cli-setup/poll')
          return {
            'output': 'session $starts\r\n',
            'cursor': 1,
            'running': true,
          };
        if (r.url.path == '/api/cli-setup/input') return firstInput.future;
        if (r.url.path == '/api/sdk') return {'supported': false};
        if (r.url.path == '/api/ssh-access') return {'key_count': 0};
        return {'workspaces': [], 'agents': []};
      });
      await click('Connect account');
      await until(
        () => elements(
          '.xterm-rows',
        ).any((e) => (e.textContent ?? '').contains('session')),
      );
      await click('↑ Up');
      await click('↓ Down');
      await click('Close terminal');
      firstInput.complete(http.Response('{}', 200));
      await settle();
      expect(posts('cli-setup/input'), hasLength(1));
      expect(posts('cli-setup/close').single['id'], 'setup-1');
      await click('Connect account');
      await until(
        () => elements(
          '.xterm-rows',
        ).any((e) => (e.textContent ?? '').contains('session')),
      );
      expect(elements('.xterm-rows').single.textContent, contains('session 2'));
      expect(elements('canvas'), isEmpty);
    },
  );
}
