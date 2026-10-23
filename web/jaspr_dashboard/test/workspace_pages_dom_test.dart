@TestOn('browser')
library;

import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:jaspr/client.dart';
import 'package:juice/juice.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/pages/project_explorer_page.dart';
import 'package:herdr_jaspr_dashboard/pages/knowledge_page.dart';
import 'package:herdr_jaspr_dashboard/pages/logs_page.dart';
import 'package:herdr_jaspr_dashboard/pages/toolchain_card.dart';
import 'package:herdr_jaspr_dashboard/pages/ssh_access_card.dart';

void main() {
  late ClientAppBinding binding;
  late web.HTMLDivElement root;
  DashboardBloc? connection;
  Future<void> until(bool Function() ready) async {
    final deadline = DateTime.now().add(const Duration(seconds: 5));
    while (!ready() && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 15));
    }
    expect(
      ready(),
      isTrue,
      reason: 'Workspace UI did not settle: ${root.textContent}',
    );
  }

  Future<void> mount(
    Component page,
    FutureOr<dynamic> Function(http.Request) respond, {
    String role = 'admin',
  }) async {
    connection = DashboardBloc(
      client: MockClient((request) async {
        final response = await respond(request);
        return response is http.Response
            ? response
            : http.Response(jsonEncode(response), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection!);
    connection!.connect('fixture');
    await until(() => connection!.state.connected);
    connection!.operator.value = {'role': role};
    binding.attachRootComponent(page, attachTo: '#workspace-pages-test');
  }

  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    root = web.HTMLDivElement()..id = 'workspace-pages-test';
    web.document.body!.appendChild(root);
    binding = ClientAppBinding();
  });
  tearDown(() async {
    binding.detachRootComponent();
    await connection?.disconnect();
    await BlocScope.reset();
    root.textContent = '';
    connection = null;
  });
  tearDownAll(() => root.remove());

  test('explorer tabs have one tab stop and support arrows and Home', () async {
    await mount(
      const ProjectExplorerPage(),
      (request) => switch (request.url.path) {
        '/api/projects/browse' => {
          'kind': 'folder',
          'root': '/home/herdr/projects',
          'entries': [],
        },
        '/api/projects/git' => {'repository': false},
        _ => {'workspaces': [], 'agents': []},
      },
    );
    await until(() => root.querySelector('#explorer-tab-files') != null);
    final files = root.querySelector('#explorer-tab-files')! as web.HTMLElement;
    final git = root.querySelector('#explorer-tab-git')! as web.HTMLElement;
    expect(files.getAttribute('tabindex'), '0');
    expect(git.getAttribute('tabindex'), '-1');
    files.focus();
    files.dispatchEvent(
      web.KeyboardEvent(
        'keydown',
        web.KeyboardEventInit(
          key: 'ArrowRight',
          bubbles: true,
          cancelable: true,
        ),
      ),
    );
    await until(() => git.getAttribute('aria-selected') == 'true');
    expect(web.document.activeElement, git);
    expect(git.getAttribute('tabindex'), '0');
    expect(files.getAttribute('tabindex'), '-1');
    expect(
      root.querySelector('[role=tabpanel]')!.getAttribute('aria-labelledby'),
      git.id,
    );
    git.dispatchEvent(
      web.KeyboardEvent(
        'keydown',
        web.KeyboardEventInit(key: 'Home', bubbles: true, cancelable: true),
      ),
    );
    await until(() => files.getAttribute('aria-selected') == 'true');
    expect(web.document.activeElement, files);
  });

  test('explorer failure never reports an empty folder', () async {
    await mount(
      const ProjectExplorerPage(),
      (request) => request.url.path == '/api/projects/browse'
          ? http.Response(
              jsonEncode({'error': 'Folder permission denied'}),
              403,
            )
          : {'workspaces': [], 'agents': []},
    );
    await until(() => root.textContent!.contains('Folder permission denied'));
    expect(root.textContent, contains('Folder contents are unavailable'));
    expect(root.textContent, isNot(contains('This folder is empty')));
  });

  test('file preview keeps compact metadata and keyboard scrolling', () async {
    await mount(const ProjectExplorerPage(), (request) {
      if (request.url.path == '/api/projects/browse') {
        final data = jsonDecode(request.body) as Map;
        return data['path'] == 'notes.txt'
            ? {
                'kind': 'file',
                'path': 'notes.txt',
                'content': 'Review notes\n' * 200,
                'size': 2400,
              }
            : {
                'kind': 'folder',
                'root': '/home/herdr/projects',
                'entries': [
                  {
                    'kind': 'file',
                    'name': 'notes.txt',
                    'path': 'notes.txt',
                    'modified': '2026-10-23',
                    'size': 2400,
                  },
                ],
              };
      }
      return {'workspaces': [], 'agents': []};
    });
    await until(() => root.querySelector('.file-link') != null);
    (root.querySelector('.file-link')! as web.HTMLElement).click();
    await until(() => root.querySelector('.explorer-preview pre') != null);
    final preview =
        root.querySelector('.explorer-preview pre')! as web.HTMLElement;
    expect(preview.getAttribute('tabindex'), '0');
    expect(preview.getAttribute('aria-label'), isNotEmpty);
    expect(
      root.querySelector('.file-details')!.textContent,
      contains('file · 2026-10-23'),
    );
    preview.focus();
    expect(web.document.activeElement, preview);
  });

  test('knowledge disclosure owns a persistent labelled region', () async {
    await mount(
      const KnowledgePage(),
      (request) => switch (request.url.path) {
        '/api/organizations/directory' => {
          'organizations': [
            {'id': 'studio', 'name': 'Studio'},
          ],
        },
        '/api/knowledge' => {
          'records': [
            {
              'id': 1,
              'title': 'Release decision',
              'body': 'Ship the validated commit.',
              'kind': 'decision',
              'state': 'reviewed',
              'repository': 'repo',
              'source': {},
              'audit': [],
            },
          ],
          'repositories': ['repo'],
          'next_before': null,
        },
        _ => {'workspaces': [], 'agents': []},
      },
    );
    await until(() => root.querySelector('.knowledge-record__toggle') != null);
    final toggle =
        root.querySelector('.knowledge-record__toggle')! as web.HTMLElement;
    final region = web.document.getElementById(
      toggle.getAttribute('aria-controls')!,
    )!;
    expect(region.hasAttribute('hidden'), isTrue);
    expect(region.getAttribute('aria-labelledby'), toggle.id);
    toggle.click();
    await until(() => !region.hasAttribute('hidden'));
    expect(toggle.getAttribute('aria-expanded'), 'true');
  });

  test(
    'saved log failure does not claim zero storage or no snapshots',
    () async {
      await mount(
        const LogsPage(),
        (request) => request.url.path == '/api/logs'
            ? http.Response(
                jsonEncode({'error': 'Snapshot storage unavailable'}),
                503,
              )
            : {'workspaces': [], 'agents': []},
      );
      await until(
        () => root.textContent!.contains('Snapshot storage unavailable'),
      );
      expect(root.textContent, isNot(contains('No saved snapshots.')));
      expect(root.querySelector('[role=meter]'), isNull);
      expect(root.textContent, contains('Saved snapshots are unavailable'));
    },
  );

  test(
    'SDK pending lookup shows loading instead of missing service warning',
    () async {
      final sdk = Completer<Map<String, dynamic>>();
      await mount(
        const ToolchainCard(),
        (request) => request.url.path == '/api/sdk'
            ? sdk.future
            : {'workspaces': [], 'agents': []},
      );
      await until(
        () => root.textContent!.contains('Loading build tool status'),
      );
      expect(
        root.textContent,
        isNot(contains('SDK installation service is not configured')),
      );
      sdk.complete({'supported': true, 'toolchains': {}});
      await until(
        () => root.textContent!.contains(
          'This release declares no SDK toolchains',
        ),
      );
    },
  );

  test(
    'read-only operator sees explanation for disabled SSH and access actions',
    () async {
      await mount(
        const SshAccessCard(),
        (request) => switch (request.url.path) {
          '/api/ssh-access' => {'key_count': 1},
          '/api/dashboard-access' => {'mode': 'lan'},
          _ => {'workspaces': [], 'agents': []},
        },
        role: 'viewer',
      );
      await until(
        () => root.textContent!.contains(
          'Only an administrator can change dashboard access',
        ),
      );
      for (final button in ['Add SSH public key', 'Require SSH tunnel']) {
        final nodes = root.querySelectorAll('button');
        final control =
            [
                  for (var i = 0; i < nodes.length; i++) nodes.item(i)!,
                ].firstWhere((node) => node.textContent == button)
                as web.HTMLButtonElement;
        expect(control.disabled, isTrue, reason: button);
      }
    },
  );
}
