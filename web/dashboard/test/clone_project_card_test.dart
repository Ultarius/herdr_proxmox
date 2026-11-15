import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/clone_project_card.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard_state/clone_jobs.dart';
import 'helpers/dashboard_connection.dart';

void main() {
  Future<void> withClone(
    WidgetTester tester, {
    required List<Map<String, dynamic>> jobs,
    required List<Map<String, dynamic>> posts,
    required void Function(String) onCloned,
    void Function(String)? onDeleted,
    String? error,
    bool folderDeleted = false,
    required Future<void> Function(DashboardBloc) body,
  }) async {
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/projects/jobs/remove') {
          final body = Map<String, dynamic>.from(jsonDecode(request.body));
          posts.add(body);
          if (error != null)
            return http.Response(jsonEncode({'error': error}), 400);
          jobs.removeWhere((job) => job['id'] == body['id']);
          return http.Response(
            jsonEncode({
              'removed': true,
              'message': folderDeleted
                  ? 'Clone folder and history entry deleted.'
                  : 'Clone history entry removed. Existing files and repository connections were preserved.',
              'folder_deleted': folderDeleted,
            }),
            200,
          );
        }
        if (request.url.path == '/api/projects/jobs')
          return http.Response(jsonEncode({'jobs': jobs}), 200);
        if (request.url.path == '/api/projects/connections')
          return http.Response('{"connections":[],"bindings":[]}', 200);
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    await withDashboardConnection(tester, connection, () async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: CloneProjectCard(onCloned: onCloned, onDeleted: onDeleted),
            ),
          ),
        ),
      );
      await pumpUntil(
        tester,
        () => find.text('Remove clone').evaluate().isNotEmpty,
      );
      await body(connection);
    });
  }

  Map<String, dynamic> job(
    String id,
    String folder, [
    String state = 'completed',
  ]) => {
    'id': id,
    'folder': folder,
    'cwd': '/projects/$folder',
    'state': state,
    'destination_identity': [1, 2],
  };

  testWidgets(
    'history removal preserves files and does not auto-select an older clone',
    (tester) async {
      final jobs = [job('a' * 32, 'new-repo'), job('b' * 32, 'older-repo')];
      final posts = <Map<String, dynamic>>[];
      final selected = <String>[];
      final deleted = <String>[];
      await withClone(
        tester,
        jobs: jobs,
        posts: posts,
        onCloned: selected.add,
        onDeleted: deleted.add,
        body: (_) async {
          expect(selected, ['/projects/new-repo']);
          await clickText(tester, 'Remove clone');
          expect(posts, isEmpty);
          expect(find.text(cloneRemovalPreservationNotice), findsOneWidget);
          expect(
            tester
                .widget<FilledButton>(
                  find.widgetWithText(FilledButton, 'Remove history entry'),
                )
                .style,
            isNull,
          );
          expect(
            tester
                .widget<CheckboxListTile>(find.byType(CheckboxListTile))
                .value,
            isFalse,
          );
          await clickText(tester, 'Remove history entry');
          expect(posts.single, {'id': 'a' * 32, 'delete_folder': false});
          expect(find.text('new-repo · completed'), findsNothing);
          await tester.pump(const Duration(seconds: 3));
          await tester.pump(const Duration(milliseconds: 100));
          expect(selected, ['/projects/new-repo']);
          expect(find.text('older-repo · completed'), findsOneWidget);
          expect(deleted, isEmpty);
          expect(
            find.textContaining(
              'Existing files and repository connections were preserved.',
            ),
            findsOneWidget,
          );
        },
      );
    },
  );

  testWidgets('folder deletion requires exact typed confirmation', (
    tester,
  ) async {
    final posts = <Map<String, dynamic>>[];
    final deleted = <String>[];
    await withClone(
      tester,
      jobs: [job('a' * 32, 'repo', 'failed')],
      posts: posts,
      onCloned: (_) {},
      folderDeleted: true,
      onDeleted: deleted.add,
      body: (_) async {
        await clickText(tester, 'Remove clone');
        await clickText(tester, 'Also permanently delete the cloned folder');
        FilledButton deletion() => tester.widget<FilledButton>(
          find.widgetWithText(FilledButton, 'Delete folder and history'),
        );
        expect(deletion().onPressed, isNull);
        expect(deletion().style, isNotNull);
        await tester.ensureVisible(find.byType(TextFormField));
        await tester.enterText(find.byType(TextFormField), 'wrong');
        await tester.pump(const Duration(milliseconds: 100));
        expect(deletion().onPressed, isNull);
        await tester.enterText(find.byType(TextFormField), 'repo');
        await tester.pump(const Duration(milliseconds: 100));
        expect(deletion().onPressed, isNotNull);
        await clickText(tester, 'Delete folder and history');
        expect(posts.single, {
          'id': 'a' * 32,
          'delete_folder': true,
          'confirm_folder': 'repo',
        });
        expect(find.text('repo · failed'), findsNothing);
        expect(deleted, ['/projects/repo']);
      },
    );
  });

  testWidgets('running clones and read-only operators cannot remove clones', (
    tester,
  ) async {
    final posts = <Map<String, dynamic>>[];
    await withClone(
      tester,
      jobs: [job('a' * 32, 'repo', 'running')],
      posts: posts,
      onCloned: (_) {},
      body: (connection) async {
        final remove = find
            .ancestor(
              of: find.text('Remove clone'),
              matching: find.byWidgetPredicate(
                (widget) => widget is TextButton,
              ),
            )
            .first;
        expect(tester.widget<TextButton>(remove).onPressed, isNull);
        connection.operator.value = {'role': 'operator'};
        await tester.pump(const Duration(milliseconds: 100));
        expect(find.text('Remove clone'), findsNothing);
        expect(posts, isEmpty);
      },
    );
  });

  testWidgets(
    'failed removal leaves the clone visible and reports the reason',
    (tester) async {
      final posts = <Map<String, dynamic>>[];
      await withClone(
        tester,
        jobs: [job('a' * 32, 'repo', 'interrupted')],
        posts: posts,
        onCloned: (_) {},
        error: 'This project is in use.',
        body: (_) async {
          await clickText(tester, 'Remove clone');
          await clickText(tester, 'Remove history entry');
          expect(find.text('repo · interrupted'), findsOneWidget);
          expect(
            find.textContaining('This project is in use.'),
            findsOneWidget,
          );
          expect(posts, hasLength(1));
        },
      );
    },
  );
  testWidgets(
    'removal announces expansion and restores focus on cancellation',
    (tester) async {
      final semantics = tester.ensureSemantics();
      try {
        await withClone(
          tester,
          jobs: [job('a' * 32, 'repo')],
          posts: [],
          onCloned: (_) {},
          body: (_) async {
            await clickText(tester, 'Remove clone');
            final trigger = tester.widget<TextButton>(
              find
                  .ancestor(
                    of: find.text('Remove clone'),
                    matching: find.byWidgetPredicate((w) => w is TextButton),
                  )
                  .first,
            );
            final expanded = tester.widget<Semantics>(
              find.byWidgetPredicate(
                (w) => w is Semantics && w.properties.expanded == true,
              ),
            );
            expect(expanded.properties.controlsNodes, {
              'clone-removal-${'a' * 32}',
            });
            expect(find.bySemanticsLabel('Remove clone repo'), findsOneWidget);
            expect(
              tester
                  .widget<CheckboxListTile>(find.byType(CheckboxListTile))
                  .focusNode!
                  .hasFocus,
              isTrue,
            );
            await clickText(tester, 'Cancel removal');
            expect(trigger.focusNode!.hasFocus, isTrue);
            expect(find.text('Remove history entry'), findsNothing);
          },
        );
      } finally {
        semantics.dispose();
      }
    },
  );

  testWidgets('legacy entries expose only history removal', (tester) async {
    final legacy = job('a' * 32, 'repo')..remove('destination_identity');
    final posts = <Map<String, dynamic>>[];
    await withClone(
      tester,
      jobs: [legacy],
      posts: posts,
      onCloned: (_) {},
      body: (_) async {
        await clickText(tester, 'Remove clone');
        expect(find.text(legacyCloneRemovalNotice), findsOneWidget);
        expect(
          tester
              .widget<CheckboxListTile>(find.byType(CheckboxListTile))
              .onChanged,
          isNull,
        );
        expect(
          tester
              .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'Remove history entry'),
              )
              .focusNode!
              .hasFocus,
          isTrue,
        );
        await clickText(tester, 'Remove history entry');
        await pumpUntil(tester, () => posts.isNotEmpty);
        expect(posts.single['delete_folder'], isFalse);
      },
    );
  });
}
