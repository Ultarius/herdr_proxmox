import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/storage_panel.dart';

void main() {
  testWidgets(
    'storage cleanup and legacy registration require exact reviewed roots',
    (tester) async {
      final posts = <Map<String, dynamic>>[];
      final snapshot = {
        'available': true,
        'sampled_at': '2026-10-22T10:00:00Z',
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
        'policy': {'build_mode': 'compact'},
        'inventory': {
          'total_bytes': 2147483648,
          'reclaimable_bytes': 2147483648,
          'entries': [
            {
              'id': 'root',
              'path': '/managed/root',
              'kind': 'build-output',
              'bytes': 2147483648,
              'state': 'closed',
              'eligible': true,
              'generation': 'receipt-one',
            },
            {
              'id': 'invalid-row',
              'state': 'invalid',
              'eligible': false,
              'repairable': true,
              'row_digest': 'unchanged-invalid-row',
              'reason': 'Malformed registry metadata; files retained.',
            },
          ],
        },
      };
      final connection = DashboardBloc(
        client: MockClient((request) async {
          dynamic result = {'workspaces': [], 'agents': []};
          if (request.url.path == '/api/storage') {
            result = snapshot;
            if (request.method == 'POST') {
              final body = Map<String, dynamic>.from(jsonDecode(request.body));
              posts.add(body);
              result = <String, dynamic>{'snapshot': snapshot};
              if (body['action'] == 'preview') {
                result['preview'] = {
                  'ids': ['root'],
                  'expected': {'root': 'receipt-one'},
                  'bytes': 2147483648,
                  'entries': [(snapshot['inventory'] as Map)['entries'][0]],
                };
              }
              if (body['action'] == 'legacy-preview') {
                result['preview'] = {
                  'id': 'legacy-debug:${body['execution_id']}',
                  'path':
                      '/projects/.herdr-worktrees/${body['execution_id']}/target/debug',
                  'bytes': 31138512896,
                  'files': 12,
                  'fingerprint': 'files-fingerprint',
                  'project': body['project'],
                  'execution_id': body['execution_id'],
                };
              }
            }
          }
          return http.Response(jsonEncode(result), 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      try {
        connection.connect('token');
        await tester.pumpAndSettle();
        await tester.pumpWidget(
          const MaterialApp(
            home: Scaffold(body: SingleChildScrollView(child: StoragePanel())),
          ),
        );
        await tester.pumpAndSettle();
        expect(posts, isEmpty);
        await tester.tap(find.text('Managed build storage'));
        await tester.pumpAndSettle();
        expect(
          find.text('Fixture SDK compact mode keeps line references.'),
          findsOneWidget,
        );
        expect(
          find.text('Storage finalization pending for pending-run: cache busy'),
          findsOneWidget,
        );
        await tester.ensureVisible(find.byType(Checkbox));
        await tester.tap(find.byType(Checkbox));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Preview cleanup (1 selected)'));
        await tester.tap(find.text('Preview cleanup (1 selected)'));
        await tester.pumpAndSettle();
        expect(posts, [
          {
            'action': 'preview',
            'ids': ['root'],
          },
        ]);
        expect(
          find.textContaining(
            'Remove 2.0 GiB from 1 selected registered roots?',
          ),
          findsOneWidget,
        );
        expect(find.text('/managed/root · 2.0 GiB'), findsOneWidget);
        await tester.ensureVisible(find.text('Confirm selected cleanup'));
        await tester.tap(find.text('Confirm selected cleanup'));
        await tester.pumpAndSettle();
        expect(posts.last, {
          'action': 'reclaim',
          'ids': ['root'],
          'expected': {'root': 'receipt-one'},
        });
        expect(find.textContaining('Cleanup finished.'), findsOneWidget);
        await tester.ensureVisible(find.text('Existing worktree outputs'));
        await tester.tap(find.text('Existing worktree outputs'));
        await tester.pumpAndSettle();
        const identity = '2d1a9f248f894200822ad9750a2f7208';
        await tester.enterText(find.byType(TextFormField).first, 'project');
        await tester.enterText(find.byType(TextFormField).last, identity);
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Preview existing output'));
        await tester.tap(find.text('Preview existing output'));
        await tester.pumpAndSettle();
        expect(posts.last, {
          'action': 'legacy-preview',
          'project': 'project',
          'execution_id': identity,
        });
        expect(
          find.text(
            '/projects/.herdr-worktrees/$identity/target/debug',
            findRichText: true,
          ),
          findsOneWidget,
        );
        await tester.ensureVisible(
          find.text('Confirm register disposable output'),
        );
        await tester.tap(find.text('Confirm register disposable output'));
        await tester.pumpAndSettle();
        expect(posts.last['action'], 'legacy-adopt');
        expect(posts.last['reviewed'], isTrue);
        expect(
          (posts.last['preview'] as Map)['fingerprint'],
          'files-fingerprint',
        );
        await tester.ensureVisible(find.text('Review registry repair'));
        await tester.tap(find.text('Review registry repair'));
        await tester.pumpAndSettle();
        expect(posts.last['action'], 'legacy-adopt');
        await tester.ensureVisible(find.text('Confirm registry repair'));
        await tester.tap(find.text('Confirm registry repair'));
        await tester.pumpAndSettle();
        expect(posts.last, {
          'action': 'forget-invalid',
          'id': 'invalid-row',
          'row_digest': 'unchanged-invalid-row',
          'reviewed': true,
        });
        expect(find.textContaining('No files were deleted.'), findsOneWidget);
      } finally {
        await tester.pumpWidget(const SizedBox());
        await connection.disconnect();
        BlocScope.endAll();
      }
    },
  );
}
