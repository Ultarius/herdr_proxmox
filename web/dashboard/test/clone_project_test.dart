import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/clone_project_card.dart';

void main() {
  testWidgets(
    'nested setup fields have independent storage and missing retry identities cannot be submitted',
    (tester) async {
      var cloneRequests = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          dynamic response = {'workspaces': [], 'agents': []};
          if (request.url.path.endsWith('/connections'))
            response = {'connections': []};
          if (request.url.path.endsWith('/jobs'))
            response = {
              'jobs': [
                {
                  'id': 'retained',
                  'folder': 'private',
                  'url': 'git@github.com:owner/private.git',
                  'connection_id': 'deleted-key',
                  'state': 'failed',
                  'error': 'Access denied',
                  'cwd': '/projects/private',
                },
              ],
            };
          if (request.url.path.endsWith('/clone')) cloneRequests++;
          return http.Response(jsonEncode(response), 200);
        }),
      );
      addTearDown(() async {
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        BlocScope.endAll();
      });
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: ExpansionTile(
                key: const PageStorageKey('dashboard-project-setup'),
                title: const Text('Project setup'),
                children: [CloneProjectCard(onCloned: (_) {})],
              ),
            ),
          ),
        ),
      );
      Future<void> click(String label) async {
        await tester.ensureVisible(find.text(label));
        await tester.tap(find.text(label));
        await tester.pumpAndSettle();
      }

      await click('Project setup');
      await click('Repository connections');
      for (final key in [
        'repository-connection-name',
        'repository-connection-repository',
        'repository-connection-folder',
      ]) {
        await tester.ensureVisible(find.byKey(PageStorageKey(key)));
        await tester.enterText(find.byKey(PageStorageKey(key)), 'fixture');
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      }
      await click('Retry in a new folder');
      expect(tester.takeException(), isNull);
      await click('Clone repository');
      expect(cloneRequests, 0);
      expect(
        find.textContaining('saved repository connection is unavailable'),
        findsOneWidget,
      );
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      BlocScope.endAll();
    },
  );

  testWidgets('clone status is recovered after navigating away', (
    tester,
  ) async {
    final jobs = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/clone')) {
          jobs.add({
            'id': 'clone',
            'folder': 'repo',
            'cwd': '/home/herdr/projects/repo',
            'state': 'queued',
            'error': '',
          });
          return http.Response(jsonEncode(jobs.first), 200);
        }
        if (request.url.path.endsWith('/jobs'))
          return http.Response(jsonEncode({'jobs': jobs}), 200);
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    addTearDown(() async {
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      BlocScope.endAll();
    });
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    final selected = <String>[];
    Widget card() => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: CloneProjectCard(onCloned: selected.add),
        ),
      ),
    );
    await tester.pumpWidget(card());
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byType(TextField).first,
      'https://example.com/repo.git',
    );
    await tester.enterText(find.byType(TextField).last, 'repo');
    await tester.ensureVisible(find.text('Clone repository'));
    await tester.tap(find.text('Clone repository'));
    await tester.pumpAndSettle();
    expect(find.text('repo · queued'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    jobs.first['state'] = 'completed';
    await tester.pumpWidget(card());
    await tester.pumpAndSettle();
    expect(find.text('repo · completed'), findsOneWidget);
    expect(selected, ['/home/herdr/projects/repo']);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    BlocScope.endAll();
  });
  testWidgets(
    'managed SSH setup, connection selection and failed clone retry are explicit',
    (tester) async {
      final connections = <Map<String, dynamic>>[];
      final bindings = <Map<String, dynamic>>[];
      final requests = <Map<String, dynamic>>[];
      final jobs = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          dynamic response = {'workspaces': [], 'agents': []};
          if (request.url.path == '/api/projects/connections') {
            if (request.method == 'POST') {
              final body = Map<String, dynamic>.from(jsonDecode(request.body));
              requests.add(body);
              if (body['action'] == 'create')
                connections.add({
                  'id': 'managed',
                  'label': 'Project SSH',
                  'repository': 'SourceOwner/project',
                  'kind': 'ssh',
                  'public_key': 'ssh-ed25519 PUBLIC test',
                  'host_verified': false,
                  'access_state': 'not_tested',
                });
              if (body['action'] == 'verify_host')
                connections.first['host_verified'] = true;
              if (body['action'] == 'test')
                connections.first['access_state'] = 'verified';
              if (body['action'] == 'bind')
                bindings.add({
                  'path': '/projects/${body['folder']}',
                  'connection_id': body['id'],
                });
              if (body['action'] == 'unbind') bindings.clear();
              if (body['action'] == 'remove')
                connections.removeWhere((item) => item['id'] == body['id']);
              response = connections.isEmpty
                  ? {'removed': true}
                  : connections.first;
            } else {
              response = {'connections': connections, 'bindings': bindings};
            }
          } else if (request.url.path == '/api/projects/jobs') {
            response = {'jobs': jobs};
          } else if (request.url.path == '/api/projects/clone') {
            final body = Map<String, dynamic>.from(jsonDecode(request.body));
            requests.add({'action': 'clone', ...body});
            jobs.clear();
            jobs.add({
              'id': 'clone',
              'folder': body['folder'],
              'url': body['url'],
              'connection_id': body['connection_id'],
              'cwd': '/projects/${body['folder']}',
              'state': 'failed',
              'error': 'Access denied. Destination retained.',
            });
            response = {...jobs.first, 'state': 'queued', 'error': ''};
          }
          return http.Response(jsonEncode(response), 200);
        }),
      );
      addTearDown(() async {
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        BlocScope.endAll();
      });
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: CloneProjectCard(onCloned: (_) {}),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Repository connections'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'Connection name'),
        'Project SSH',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'GitHub repository (owner/repository)'),
        'SourceOwner/project',
      );
      Future<void> click(String text) async {
        await tester.ensureVisible(find.text(text));
        await tester.tap(find.text(text));
        await tester.pumpAndSettle();
      }

      await click('Create SSH connection');
      expect(requests.last, {
        'action': 'create',
        'label': 'Project SSH',
        'repository': 'SourceOwner/project',
        'kind': 'ssh',
      });
      expect(find.text('ssh-ed25519 PUBLIC test'), findsOneWidget);
      await click('Verify GitHub host key');
      expect(requests.last['id'], 'managed');
      await click('Test repository access');
      expect(requests.last['url'], 'git@github.com:SourceOwner/project.git');
      await tester.ensureVisible(find.byKey(const ValueKey('')));
      await tester.tap(find.byKey(const ValueKey('')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Project SSH · ssh · verified').last);
      await tester.pumpAndSettle();
      final url = tester.widget<TextField>(
        find.byKey(const PageStorageKey('clone-url-field')),
      );
      expect(url.controller!.text, 'git@github.com:SourceOwner/project.git');
      await click('Clone repository');
      expect(requests.last, {
        'action': 'clone',
        'url': 'git@github.com:SourceOwner/project.git',
        'folder': 'project',
        'connection_id': 'managed',
      });
      expect(find.text('Access denied. Destination retained.'), findsOneWidget);
      expect(find.textContaining('Clone queued.'), findsNothing);
      final count = requests.length;
      await click('Retry in a new folder');
      expect(requests.length, count);
      await click('Clone repository');
      expect(requests.last['folder'], 'project-retry');
      expect(requests.last['connection_id'], 'managed');
      await tester.enterText(
        find.byKey(const PageStorageKey('repository-connection-folder')),
        'project',
      );
      await tester.pumpAndSettle();
      await click('Assign to existing project');
      expect(requests.last, {
        'action': 'bind',
        'id': 'managed',
        'folder': 'project',
      });
      expect(
        tester
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Remove connection'),
            )
            .onPressed,
        isNull,
      );
      await click('Unassign project');
      expect(requests.last, {
        'action': 'unbind',
        'id': 'managed',
        'folder': 'project',
      });
      final beforeRemoval = requests.length;
      await click('Remove connection');
      await click('Cancel removal');
      expect(requests.length, beforeRemoval);
      await click('Remove connection');
      await click('Confirm removal');
      expect(requests.last, {'action': 'remove', 'id': 'managed'});
      expect(find.text('ssh-ed25519 PUBLIC test'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      BlocScope.endAll();
    },
  );
}
