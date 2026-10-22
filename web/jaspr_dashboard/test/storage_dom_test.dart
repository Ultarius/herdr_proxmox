@TestOn('browser')
library;

import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/storage_panel.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 60));

void main() {
  late DashboardBloc connection;
  late ClientAppBinding binding;
  late web.HTMLDivElement mount;
  late List<Map<String, dynamic>> posts;
  late Map<String, dynamic> snapshot;
  var gets = 0;
  Completer<http.Response>? delayedPreview;

  setUp(() async {
    gets = 0;
    delayedPreview = null;
    posts = [];
    snapshot = {
      'available': true,
      'sampled_at': '2026-10-22T10:00:00+00:00',
      'scanning': false,
      'finalization_errors': [
        {'execution_id': 'pending-run', 'error': 'cache busy'},
      ],
      'build_guidance': [
        {
          'capability': 'fixture-sdk',
          'mode': 'compact',
          'text': 'Fixture SDK compact mode keeps line references.',
        },
      ],
      'policy': {
        'build_mode': 'compact',
        'output_budget_bytes': 8589934592,
        'shared_budget_bytes': 4294967296,
      },
      'inventory': {
        'total_bytes': 3221225472,
        'reclaimable_bytes': 2147483648,
        'entries': [
          {
            'id': 'closed',
            'path': '/managed/closed',
            'kind': 'build-output',
            'bytes': 2147483648,
            'state': 'closed',
            'eligible': true,
            'generation': 'generation-one',
          },
          {
            'id': 'active',
            'path': '/managed/active',
            'kind': 'build-output',
            'bytes': 1073741824,
            'state': 'active',
            'eligible': false,
            'reason': 'Execution active',
            'generation': 'generation-two',
          },
        ],
      },
    };
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/storage') {
          if (request.method == 'GET') {
            gets++;
            return http.Response(jsonEncode(snapshot), 200);
          }
          final body = Map<String, dynamic>.from(jsonDecode(request.body));
          posts.add(body);
          if (body['action'] == 'preview') {
            if (delayedPreview != null) return delayedPreview!.future;
            return http.Response(
              jsonEncode({
                'preview': {
                  'ids': ['closed'],
                  'expected': {'closed': 'generation-one'},
                  'bytes': 2147483648,
                  'entries': [(snapshot['inventory'] as Map)['entries'][0]],
                },
                'snapshot': snapshot,
              }),
              200,
            );
          }
          if (body['action'] == 'legacy-preview') {
            return http.Response(
              jsonEncode({
                'preview': {
                  'id': 'legacy-debug:${body['execution_id']}',
                  'path':
                      '/projects/.herdr-worktrees/${body['execution_id']}/target/debug',
                  'bytes': 31138512896,
                  'files': 12,
                  'fingerprint': 'checked-files-fingerprint',
                  'project': body['project'],
                  'execution_id': body['execution_id'],
                },
              }),
              200,
            );
          }
          if (body['action'] == 'reclaim') {
            (snapshot['inventory'] as Map)['entries'] = [
              ((snapshot['inventory'] as Map)['entries'] as List).last,
            ];
          }
          if (body['action'] == 'configure') {
            (snapshot['policy'] as Map)['build_mode'] = body['build_mode'];
          }
          return http.Response(jsonEncode({'snapshot': snapshot}), 200);
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    mount = web.HTMLDivElement()..id = 'storage-test';
    web.document.body!.appendChild(mount);
    binding = ClientAppBinding();
    binding.attachRootComponent(
      const StoragePanel(),
      attachTo: '#storage-test',
    );
    await settle();
  });

  tearDown(() async {
    if (delayedPreview != null && !delayedPreview!.isCompleted)
      delayedPreview!.complete(http.Response('{}', 503));
    binding.detachRootComponent();
    mount.remove();
    await connection.disconnect();
    BlocScope.endAll();
  });

  web.HTMLButtonElement button(String label) {
    final buttons = mount.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
      final element = buttons.item(i) as web.HTMLButtonElement;
      if (element.textContent == label) return element;
    }
    throw StateError('Button not found: $label');
  }

  Future<void> selectClosed() async {
    final input = mount.querySelector('input') as web.HTMLInputElement;
    input.checked = true;
    input.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
    await settle();
  }

  Future<void> input(String label, String value) async {
    final labels = mount.querySelectorAll('label');
    for (var i = 0; i < labels.length; i++) {
      final element = labels.item(i) as web.HTMLElement;
      if (element.querySelector('span')?.textContent != label) continue;
      final control = element.querySelector('input') as web.HTMLInputElement;
      control.value = value;
      control.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      return;
    }
    throw StateError('Input not found: $label');
  }

  test(
    'cleanup requires a preview and sends only its exact identity generations',
    () async {
      expect(gets, 1);
      expect(
        posts,
        isEmpty,
        reason: 'Opening the panel never starts a storage scan',
      );
      expect(
        mount.textContent,
        contains('3.0 GiB registered · 2.0 GiB reclaimable'),
      );
      expect(
        mount.textContent,
        contains('Fixture SDK compact mode keeps line references.'),
      );
      expect(mount.textContent, isNot(contains('Rust')));
      expect(
        mount.textContent,
        contains('Storage finalization pending for pending-run: cache busy'),
      );
      final inputs = mount.querySelectorAll('input');
      expect((inputs.item(1) as web.HTMLInputElement).disabled, isTrue);
      expect(button('Preview cleanup (0 selected)').disabled, isTrue);
      await selectClosed();
      button('Preview cleanup (1 selected)').click();
      await settle();
      expect(posts, [
        {
          'action': 'preview',
          'ids': ['closed'],
        },
      ]);
      expect(
        mount.textContent,
        contains('Remove 2.0 GiB from 1 selected registered roots?'),
      );
      expect(
        mount.querySelector('.storage-cleanup-preview')?.textContent,
        '/managed/closed · 2.0 GiB',
      );
      button('Confirm selected cleanup').click();
      await settle();
      expect(posts.last, {
        'action': 'reclaim',
        'ids': ['closed'],
        'expected': {'closed': 'generation-one'},
      });
      expect(mount.textContent, contains('Cleanup finished.'));
      expect(mount.querySelectorAll('input[type=checkbox]').length, 1);
    },
  );

  test('a new scan cancels a prior cleanup confirmation', () async {
    await selectClosed();
    button('Preview cleanup (1 selected)').click();
    await settle();
    button('Scan storage').click();
    await settle();
    expect(posts.last, {'action': 'scan'});
    expect(mount.textContent, isNot(contains('Confirm selected cleanup')));
    expect(button('Preview cleanup (0 selected)').disabled, isTrue);
  });

  test('changing mode is explicit and applies to future plans', () async {
    final select = mount.querySelector('select') as web.HTMLSelectElement;
    select.value = 'full';
    select.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
    await settle();
    expect(posts, [
      {'action': 'configure', 'build_mode': 'full'},
    ]);
    expect(
      mount.textContent,
      contains('Build mode saved for future environment plans.'),
    );
  });

  test(
    'legacy output registration is exact, reviewed, and invalidated by input edits',
    () async {
      const identity = '2d1a9f248f894200822ad9750a2f7208';
      expect(button('Preview existing output').disabled, isTrue);
      await input(
        'Earlier task execution ID (32 hexadecimal characters)',
        identity,
      );
      expect(button('Preview existing output').disabled, isTrue);
      await input('Project folder relative to Projects', 'project');
      button('Preview existing output').click();
      await settle();
      expect(posts.single, {
        'action': 'legacy-preview',
        'project': 'project',
        'execution_id': identity,
      });
      expect(
        mount.textContent,
        contains('/projects/.herdr-worktrees/$identity/target/debug'),
      );
      expect(mount.textContent, contains('29.0 GiB in 12 files'));
      expect(
        mount.textContent,
        contains('Registration authorizes automatic retention cleanup'),
      );
      expect(posts.where((item) => item['action'] == 'legacy-adopt'), isEmpty);
      await input('Project folder relative to Projects', 'other-project');
      expect(
        mount.textContent,
        isNot(contains('Confirm register disposable output')),
      );
      button('Preview existing output').click();
      await settle();
      button('Confirm register disposable output').click();
      await settle();
      expect(posts.last['action'], 'legacy-adopt');
      expect(posts.last['reviewed'], isTrue);
      expect(posts.last['preview'], {
        'id': 'legacy-debug:$identity',
        'path': '/projects/.herdr-worktrees/$identity/target/debug',
        'bytes': 31138512896,
        'files': 12,
        'fingerprint': 'checked-files-fingerprint',
        'project': 'other-project',
        'execution_id': identity,
      });
      expect(
        mount.textContent,
        contains('Existing output registered as disposable.'),
      );
    },
  );

  test(
    'invalid registry metadata repair requires explicit reviewed confirmation',
    () async {
      ((snapshot['inventory'] as Map)['entries'] as List).add({
        'id': 'invalid-row',
        'state': 'invalid',
        'eligible': false,
        'repairable': true,
        'row_digest': 'unchanged-invalid-row',
        'reason': 'Malformed registry metadata; files retained.',
      });
      button('Refresh saved inventory').click();
      await settle();
      button('Review registry repair').click();
      await settle();
      expect(posts, isEmpty);
      expect(mount.textContent, contains('Its files will remain untouched.'));
      button('Confirm registry repair').click();
      await settle();
      expect(posts.single, {
        'action': 'forget-invalid',
        'id': 'invalid-row',
        'row_digest': 'unchanged-invalid-row',
        'reviewed': true,
      });
      expect(mount.textContent, contains('No files were deleted.'));
    },
  );

  test(
    'a role change discards pending cleanup and hides administrative storage',
    () async {
      delayedPreview = Completer<http.Response>();
      await selectClosed();
      button('Preview cleanup (1 selected)').click();
      await settle();
      connection.operator.value = {'name': 'reader', 'role': 'viewer'};
      await settle();
      delayedPreview!.complete(
        http.Response(
          jsonEncode({
            'preview': {
              'ids': ['closed'],
              'expected': {'closed': 'generation-one'},
              'bytes': 1,
            },
          }),
          200,
        ),
      );
      await settle();
      expect(mount.textContent, isEmpty);
      expect(posts.where((item) => item['action'] == 'reclaim'), isEmpty);
    },
  );
}
