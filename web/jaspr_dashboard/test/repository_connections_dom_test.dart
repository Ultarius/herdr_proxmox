@TestOn('browser')
library;

import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/overview.dart';
import 'package:herdr_jaspr_dashboard/services.dart';

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 50));
Iterable<web.HTMLElement> nodes(String selector) sync* {
  final values = web.document.querySelectorAll('#repository-test $selector');
  for (var i = 0; i < values.length; i++)
    yield values.item(i) as web.HTMLElement;
}

Future<void> click(String label) async {
  nodes('button').firstWhere((item) => item.textContent == label).click();
  await settle();
}

Future<void> field(String label, String value) async {
  final element =
      nodes('label')
              .firstWhere(
                (item) => item.querySelector('span')?.textContent == label,
              )
              .querySelector('input')
          as web.HTMLInputElement;
  element.value = value;
  element.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await settle();
}

void main() {
  late DashboardBloc connection;
  late ClientAppBinding binding;
  late List<Map<String, dynamic>> connections, jobs, requests, bindings;
  Completer<http.Response>? pendingProbe;
  setUp(() async {
    pendingProbe = null;
    connections = [];
    bindings = [];
    jobs = [];
    requests = [];
    connection = DashboardBloc(
      client: MockClient((request) async {
        dynamic response = {'workspaces': [], 'agents': []};
        final path = request.url.path;
        if (path == '/api/projects/connections') {
          if (request.method == 'POST') {
            final body = Map<String, dynamic>.from(jsonDecode(request.body));
            requests.add(body);
            if (body['action'] == 'test' && pendingProbe != null)
              return pendingProbe!.future;
            if (body['action'] == 'create') {
              connections.add({
                'id': 'key-one',
                'label': body['label'],
                'repository': body['repository'],
                'kind': body['kind'],
                'public_key': 'ssh-ed25519 PUBLIC test',
                'host_verified': false,
                'access_state': 'not_tested',
              });
            } else if (body['action'] == 'verify_host') {
              connections.first['host_verified'] = true;
            } else if (body['action'] == 'test') {
              connections.first['access_state'] = 'verified';
            }
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
        } else if (path == '/api/projects/jobs') {
          response = {'jobs': jobs};
        } else if (path == '/api/projects/clone') {
          final body = Map<String, dynamic>.from(jsonDecode(request.body));
          requests.add({'action': 'clone', ...body});
          jobs = [
            {
              'id': 'job',
              'folder': body['folder'],
              'url': body['url'],
              'connection_id': body['connection_id'],
              'state': 'failed',
              'error': 'Access denied. Destination retained.',
              'cwd': '/projects/${body['folder']}',
            },
          ];
          response = {...jobs.first, 'state': 'queued', 'error': ''};
        }
        return http.Response(jsonEncode(response), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    web.document.body!.appendChild(
      web.HTMLDivElement()..id = 'repository-test',
    );
    binding = ClientAppBinding();
    binding.attachRootComponent(
      CloneProjectCard(onCloned: (_) {}),
      attachTo: '#repository-test',
    );
    await settle();
  });
  tearDown(() async {
    if (pendingProbe != null && !pendingProbe!.isCompleted)
      pendingProbe!.complete(http.Response('{}', 503));
    binding.detachRootComponent();
    web.document.getElementById('repository-test')!.remove();
    await connection.disconnect();
    BlocScope.endAll();
  });

  test(
    'SSH connection creation, verification, selection and clone retry use the explicit identity',
    () async {
      nodes('summary').single.click();
      await settle();
      await field('Connection name', 'Bevy SSH');
      await field('GitHub repository (owner/repository)', 'Ultarius/bevy_game');
      await click('Create SSH connection');
      expect(requests.last, {
        'action': 'create',
        'label': 'Bevy SSH',
        'repository': 'Ultarius/bevy_game',
        'kind': 'ssh',
      });
      expect(nodes('pre').single.textContent, 'ssh-ed25519 PUBLIC test');
      expect(
        nodes('a').single.getAttribute('href'),
        'https://github.com/Ultarius/bevy_game/settings/keys',
      );
      await click('Verify GitHub host key');
      expect(requests.last, {'action': 'verify_host', 'id': 'key-one'});
      await click('Test repository access');
      expect(requests.last, {
        'action': 'test',
        'id': 'key-one',
        'url': 'git@github.com:Ultarius/bevy_game.git',
      });
      final selector =
          nodes('label')
                  .firstWhere(
                    (item) =>
                        item.querySelector('span')?.textContent ==
                        'Repository connection',
                  )
                  .querySelector('select')
              as web.HTMLSelectElement;
      selector.value = 'key-one';
      selector.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
      await settle();
      final url =
          nodes('label')
                  .firstWhere(
                    (item) =>
                        item.querySelector('span')?.textContent ==
                        'Repository URL',
                  )
                  .querySelector('input')
              as web.HTMLInputElement;
      expect(url.value, 'git@github.com:Ultarius/bevy_game.git');
      await click('Clone project');
      expect(requests.last, {
        'action': 'clone',
        'url': 'git@github.com:Ultarius/bevy_game.git',
        'folder': 'bevy_game',
        'connection_id': 'key-one',
      });
      expect(
        nodes('.clone-job__detail').single.textContent,
        contains('Access denied'),
      );
      expect(
        nodes('.clone-project').single.textContent,
        isNot(contains('Clone queued.')),
      );
      final before = requests.length;
      await click('Retry in a new folder');
      expect(requests.length, before);
      await click('Clone project');
      expect(requests.last['folder'], 'bevy_game-retry');
      expect(requests.last['connection_id'], 'key-one');
      await field(
        'Existing project folder (direct child, optional)',
        'bevy_game',
      );
      await click('Assign to existing project');
      expect(requests.last, {
        'action': 'bind',
        'id': 'key-one',
        'folder': 'bevy_game',
      });
      expect(
        nodes('button')
            .firstWhere((node) => node.textContent == 'Remove connection')
            .hasAttribute('disabled'),
        isTrue,
      );
      await click('Unassign project');
      expect(requests.last, {
        'action': 'unbind',
        'id': 'key-one',
        'folder': 'bevy_game',
      });
      final beforeRemoval = requests.length;
      await click('Remove connection');
      await click('Cancel removal');
      expect(requests.length, beforeRemoval);
      await click('Remove connection');
      await click('Confirm removal');
      expect(requests.last, {'action': 'remove', 'id': 'key-one'});
      expect(nodes('pre'), isEmpty);
    },
  );
  test('a retained clone cannot silently reuse a missing connection', () async {
    jobs.add({
      'id': 'old-job',
      'folder': 'private',
      'url': 'git@github.com:owner/private.git',
      'connection_id': 'deleted-key',
      'state': 'failed',
      'error': 'Access denied',
      'cwd': '/projects/private',
    });
    // Remount to load the durable job without waiting for its polling timer.
    binding.detachRootComponent();
    web.document.getElementById('repository-test')!.textContent = '';
    binding.attachRootComponent(
      CloneProjectCard(onCloned: (_) {}),
      attachTo: '#repository-test',
    );
    await settle();
    await click('Retry in a new folder');
    await click('Clone project');
    expect(requests.where((request) => request['action'] == 'clone'), isEmpty);
    expect(
      nodes('.clone-project').single.textContent,
      contains('saved repository connection is unavailable'),
    );
  });
  test(
    'account changes clear connection state and ignore a late access result',
    () async {
      nodes('summary').single.click();
      await settle();
      await field('Connection name', 'Previous account');
      await field('GitHub repository (owner/repository)', 'owner/private');
      await click('Create SSH connection');
      pendingProbe = Completer<http.Response>();
      await click('Test repository access');
      await connection.disconnect();
      await settle();
      expect(nodes('pre'), isEmpty);
      expect(nodes('.repository-connection'), isEmpty);
      pendingProbe!.complete(http.Response('{"access_state":"verified"}', 200));
      await settle();
      expect(
        nodes('.clone-project').single.textContent,
        isNot(contains('Repository access verified.')),
      );
      connections.clear();
      connection.connect('new-account');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await settle();
      expect(nodes('.repository-connection'), isEmpty);
    },
  );
}
