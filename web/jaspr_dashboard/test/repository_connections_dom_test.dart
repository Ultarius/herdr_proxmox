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
  Completer<http.Response>? pendingRemoval;
  String? saveError;
  String? removalError;
  final deletedFolders = <String>[];
  setUp(() async {
    pendingProbe = null;
    pendingRemoval = null;
    saveError = null;
    removalError = null;
    deletedFolders.clear();
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
            if (body['action'] == 'discover') {
              return http.Response(
                jsonEncode({
                  'repositories': [
                    {'repository': 'owner/private', 'private': true},
                  ],
                  'next_page': null,
                }),
                200,
              );
            }
            if (['create', 'rotate'].contains(body['action']) &&
                saveError != null) {
              return http.Response(jsonEncode({'error': saveError}), 503);
            }
            if (body['action'] == 'test' && pendingProbe != null)
              return pendingProbe!.future;
            if (body['action'] == 'create') {
              connections.add({
                'id': 'key-one',
                'label': body['label'],
                'repository': body['repository'],
                'kind': body['kind'],
                if (body['kind'] == 'ssh')
                  'public_key': 'ssh-ed25519 PUBLIC test',
                'host_verified': false,
                'access_state': 'not_tested',
              });
            } else if (body['action'] == 'verify_host') {
              connections.first['host_verified'] = true;
            } else if (body['action'] == 'test') {
              connections.first['access_state'] = 'verified';
            } else if (body['action'] == 'rotate') {
              connections.first['access_state'] = 'not_tested';
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
        } else if (path == '/api/projects/jobs/remove') {
          final body = Map<String, dynamic>.from(jsonDecode(request.body));
          requests.add({'action': 'remove_clone', ...body});
          if (pendingRemoval != null) {
            final outcome = await pendingRemoval!.future;
            if (outcome.statusCode == 200)
              jobs.removeWhere((job) => job['id'] == body['id']);
            return outcome;
          }
          if (removalError != null)
            return http.Response(jsonEncode({'error': removalError}), 400);
          jobs.removeWhere((job) => job['id'] == body['id']);
          response = {
            'removed': true,
            'folder_deleted': body['delete_folder'] == true,
            'message': body['delete_folder'] == true
                ? 'Clone folder and history entry deleted.'
                : 'Clone history entry removed. Existing files were preserved.',
          };
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
      CloneProjectCard(onCloned: (_) {}, onDeleted: deletedFolders.add),
      attachTo: '#repository-test',
    );
    await settle();
  });
  tearDown(() async {
    if (pendingProbe != null && !pendingProbe!.isCompleted)
      pendingProbe!.complete(http.Response('{}', 503));
    if (pendingRemoval != null && !pendingRemoval!.isCompleted)
      pendingRemoval!.complete(http.Response('{}', 503));
    binding.detachRootComponent();
    web.document.getElementById('repository-test')!.remove();
    await connection.disconnect();
    BlocScope.endAll();
  });

  Future<void> until(bool Function() ready) async {
    final deadline = DateTime.now().add(const Duration(seconds: 5));
    while (!ready()) {
      if (DateTime.now().isAfter(deadline))
        fail(
          'Clone removal did not settle: ${nodes('.clone-project').firstOrNull?.textContent}',
        );
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
  }

  Future<void> mountJobs(String state, {bool legacy = false}) async {
    binding.detachRootComponent();
    jobs = [
      {
        'id': 'a' * 32,
        'folder': 'old-repo',
        'cwd': '/projects/old-repo',
        'state': state,
        if (!legacy) 'destination_identity': [1, 2],
      },
    ];
    binding.attachRootComponent(
      CloneProjectCard(onCloned: (_) {}, onDeleted: deletedFolders.add),
      attachTo: '#repository-test',
    );
    await until(() => nodes('.clone-job').length == 1);
  }

  test(
    'clone history removal defaults to preserving files and can be cancelled',
    () async {
      await mountJobs('failed');
      await click('Remove clone');
      expect(
        nodes('.clone-removal').single.textContent,
        contains(
          'remote repository and saved GitHub credentials are not deleted or revoked',
        ),
      );
      expect(
        nodes(
          '.clone-removal__confirm',
        ).single.classList.contains('btn--danger'),
        isFalse,
      );
      expect(
        requests.where((request) => request['action'] == 'remove_clone'),
        isEmpty,
      );
      expect(
        (nodes('.clone-removal input[type=checkbox]').single
                as web.HTMLInputElement)
            .checked,
        isFalse,
      );
      await click('Cancel removal');
      await until(() => nodes('.clone-removal').isEmpty);
      expect(web.document.activeElement, nodes('.clone-remove').single);
      await click('Remove clone');
      await click('Remove history entry');
      await until(() => nodes('.clone-job').isEmpty);
      expect(requests.last, {
        'action': 'remove_clone',
        'id': 'a' * 32,
        'delete_folder': false,
      });
      expect(
        nodes('.clone-project').single.textContent,
        contains('files were preserved'),
      );
    },
  );

  test(
    'deleting a clone folder requires opting in and typing its exact name',
    () async {
      await mountJobs('completed');
      await click('Remove clone');
      final checkbox =
          nodes('.clone-removal input[type=checkbox]').single
              as web.HTMLInputElement;
      checkbox.click();
      await until(() => nodes('.clone-removal input[type=text]').isNotEmpty);
      final confirm = nodes(
        'button',
      ).firstWhere((item) => item.textContent == 'Delete folder and history');
      expect(confirm.hasAttribute('disabled'), isTrue);
      expect(confirm.classList.contains('btn--danger'), isTrue);
      await field('Type old-repo to confirm deletion', 'wrong');
      expect(confirm.hasAttribute('disabled'), isTrue);
      await field('Type old-repo to confirm deletion', 'old-repo');
      await until(() => !confirm.hasAttribute('disabled'));
      await click('Delete folder and history');
      await until(() => nodes('.clone-job').isEmpty);
      expect(requests.last, {
        'action': 'remove_clone',
        'id': 'a' * 32,
        'delete_folder': true,
        'confirm_folder': 'old-repo',
      });
      expect(deletedFolders, ['/projects/old-repo']);
    },
  );

  test(
    'running clones cannot be removed and non-admins have no removal controls',
    () async {
      await mountJobs('running');
      expect(nodes('.clone-remove').single.hasAttribute('disabled'), isTrue);
      connection.operator.value = {'role': 'operator'};
      await until(() => nodes('.clone-remove').isEmpty);
      expect(nodes('.clone-job'), hasLength(1));
    },
  );

  test(
    'legacy clone entries allow history-only removal and focus its safe action',
    () async {
      await mountJobs('completed', legacy: true);
      await click('Remove clone');
      final checkbox =
          nodes('.clone-removal input[type=checkbox]').single
              as web.HTMLInputElement;
      expect(checkbox.disabled, isTrue);
      expect(
        nodes('.clone-removal').single.textContent,
        contains('no verified original folder identity'),
      );
      expect(
        web.document.activeElement,
        nodes('.clone-removal__confirm').single,
      );
      await click('Remove history entry');
      await until(() => nodes('.clone-job').isEmpty);
      expect(requests.last['delete_folder'], isFalse);
      expect(deletedFolders, isEmpty);
    },
  );

  test(
    'removal failure retains the entry and pending deletion prevents duplicate requests',
    () async {
      await mountJobs('interrupted');
      removalError = 'This project is in use.';
      await click('Remove clone');
      await click('Remove history entry');
      await until(
        () => nodes(
          '.clone-project',
        ).single.textContent!.contains('This project is in use.'),
      );
      expect(nodes('.clone-job'), hasLength(1));
      removalError = null;
      pendingRemoval = Completer<http.Response>();
      await click('Remove history entry');
      expect(
        nodes('button')
            .where((button) => button.textContent == 'Removing…')
            .single
            .hasAttribute('disabled'),
        isTrue,
      );
      final before = requests
          .where((request) => request['action'] == 'remove_clone')
          .length;
      nodes(
        'button',
      ).firstWhere((item) => item.textContent == 'Removing…').click();
      expect(
        requests.where((request) => request['action'] == 'remove_clone'),
        hasLength(before),
      );
      pendingRemoval!.complete(
        http.Response('{"removed":true,"message":"Removed."}', 200),
      );
      await until(() => nodes('.clone-job').isEmpty);
    },
  );

  test(
    'SSH connection creation, verification, selection and clone retry use the explicit identity',
    () async {
      nodes('.repository-connections > summary').single.click();
      await settle();
      await field('Connection name', 'Project SSH');
      await field(
        'GitHub repository (owner/repository)',
        'SourceOwner/project',
      );
      await click('Create SSH connection');
      expect(requests.last, {
        'action': 'create',
        'label': 'Project SSH',
        'repository': 'SourceOwner/project',
        'kind': 'ssh',
      });
      expect(nodes('pre').single.textContent, 'ssh-ed25519 PUBLIC test');
      expect(
        nodes('a').single.getAttribute('href'),
        'https://github.com/SourceOwner/project/settings/keys',
      );
      await click('Verify GitHub host key');
      expect(requests.last, {'action': 'verify_host', 'id': 'key-one'});
      await click('Test repository access');
      expect(requests.last, {
        'action': 'test',
        'id': 'key-one',
        'url': 'git@github.com:SourceOwner/project.git',
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
      expect(url.value, 'git@github.com:SourceOwner/project.git');
      await click('Clone project');
      expect(requests.last, {
        'action': 'clone',
        'url': 'git@github.com:SourceOwner/project.git',
        'folder': 'project',
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
      expect(requests.last['folder'], 'project-retry');
      expect(requests.last['connection_id'], 'key-one');
      await field(
        'Existing project folder (direct child, optional)',
        'project',
      );
      await click('Assign to existing project');
      expect(requests.last, {
        'action': 'bind',
        'id': 'key-one',
        'folder': 'project',
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
        'folder': 'project',
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
  test(
    'a fine-grained token can be saved, tested and selected for HTTPS cloning',
    () async {
      nodes('.repository-connections > summary').single.click();
      await settle();
      final authentication =
          nodes('select').firstWhere(
                (node) =>
                    node.getAttribute('aria-label') == 'Authentication' ||
                    node.parentElement?.textContent?.startsWith(
                          'Authentication',
                        ) ==
                        true,
              )
              as web.HTMLSelectElement;
      authentication.value = 'token';
      authentication.dispatchEvent(
        web.Event('change', web.EventInit(bubbles: true)),
      );
      await settle();
      await field('Connection name', 'Private token');
      await field('GitHub repository (owner/repository)', 'owner/private');
      final input =
          nodes('input[type=password]').single as web.HTMLInputElement;
      expect(input.getAttribute('autocomplete'), 'off');
      expect(input.getAttribute('spellcheck'), 'false');
      expect(input.getAttribute('autocorrect'), 'off');
      await field('GitHub personal access token', 'github_pat_test_fixture');
      expect(
        nodes('p').any(
          (item) => item.textContent?.contains('Contents: Read-only') == true,
        ),
        isTrue,
      );
      await click('Save token connection');
      expect(
        requests.where((request) => request['action'] == 'create').single,
        {
          'action': 'create',
          'label': 'Private token',
          'repository': 'owner/private',
          'kind': 'token',
          'token': 'github_pat_test_fixture',
        },
      );
      expect(input.value, isEmpty);
      expect(nodes('pre'), isEmpty);
      expect(
        nodes(
          'button',
        ).any((item) => item.textContent == 'Verify GitHub host key'),
        isFalse,
      );
      await click('Test repository access');
      expect(requests.last['url'], 'https://github.com/owner/private.git');
      final selector =
          nodes('select').firstWhere(
                (node) =>
                    node.parentElement?.textContent?.startsWith(
                      'Repository connection',
                    ) ==
                    true,
              )
              as web.HTMLSelectElement;
      selector.value = 'key-one';
      selector.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
      await settle();
      await click('Clone project');
      expect(requests.last, {
        'action': 'clone',
        'url': 'https://github.com/owner/private.git',
        'folder': 'private',
        'connection_id': 'key-one',
      });
      expect(
        nodes('.clone-project').single.textContent,
        isNot(contains('github_pat_test_fixture')),
      );
    },
  );
  test(
    'PAT discovery fills the repository and connection name before saving',
    () async {
      nodes('.repository-connections > summary').single.click();
      await settle();
      final authentication =
          nodes('select').firstWhere(
                (node) =>
                    node.getAttribute('aria-label') == 'Authentication' ||
                    node.parentElement?.textContent?.startsWith(
                          'Authentication',
                        ) ==
                        true,
              )
              as web.HTMLSelectElement;
      authentication.value = 'token';
      authentication.dispatchEvent(
        web.Event('change', web.EventInit(bubbles: true)),
      );
      await settle();
      await field('GitHub personal access token', 'github_pat_test_fixture');
      await click('Discover repositories');
      expect(requests.last, {
        'action': 'discover',
        'token': 'github_pat_test_fixture',
        'page': 1,
      });
      expect(requests.where((item) => item['action'] == 'create'), isEmpty);
      final picker =
          nodes('select').firstWhere(
                (node) => node.textContent!.contains('Choose a repository'),
              )
              as web.HTMLSelectElement;
      picker.value = 'owner/private';
      picker.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
      await settle();
      await field('GitHub repository (owner/repository)', 'owner/manual');
      expect(
        (nodes('select').firstWhere(
                  (node) => node.textContent!.contains('Choose a repository'),
                )
                as web.HTMLSelectElement)
            .value,
        '',
      );
      await field('GitHub repository (owner/repository)', 'owner/private');
      await click('Save token connection');
      expect(requests.last, {
        'action': 'create',
        'token': 'github_pat_test_fixture',
        'kind': 'token',
        'repository': 'owner/private',
        'label': 'owner/private',
      });
      expect(
        (nodes('input[type=password]').single as web.HTMLInputElement).value,
        isEmpty,
      );
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
    'failed token saves retain the masked paste and rotation preserves the connection',
    () async {
      nodes('.repository-connections > summary').single.click();
      await settle();
      final authentication =
          nodes('select').firstWhere(
                (node) =>
                    node.getAttribute('aria-label') == 'Authentication' ||
                    node.parentElement?.textContent?.startsWith(
                          'Authentication',
                        ) ==
                        true,
              )
              as web.HTMLSelectElement;
      authentication.value = 'token';
      authentication.dispatchEvent(
        web.Event('change', web.EventInit(bubbles: true)),
      );
      await settle();
      await field('Connection name', 'Private token');
      await field('GitHub repository (owner/repository)', 'owner/private');
      await field('GitHub personal access token', 'github_pat_test_fixture');
      saveError = 'Save failed; try again.';
      await click('Save token connection');
      expect(
        (nodes('input[type=password]').single as web.HTMLInputElement).value,
        'github_pat_test_fixture',
      );
      expect(
        requests.where((request) => request['action'] == 'create').length,
        1,
      );
      expect(
        nodes('.repository-connections').single.textContent,
        isNot(contains('github_pat_test_fixture')),
      );
      saveError = null;
      await click('Save token connection');
      expect(
        (nodes('input[type=password]').single as web.HTMLInputElement).value,
        isEmpty,
      );
      await click('Rotate token');
      await field(
        'Replacement GitHub personal access token',
        'github_pat_replacement_fixture',
      );
      saveError = 'Rotation failed; try again.';
      await click('Save replacement token');
      expect(
        (nodes('input[type=password]').last as web.HTMLInputElement).value,
        'github_pat_replacement_fixture',
      );
      expect(
        requests.where((request) => request['action'] == 'rotate').length,
        1,
      );
      saveError = null;
      await click('Save replacement token');
      expect(requests.last, {
        'action': 'rotate',
        'id': 'key-one',
        'token': 'github_pat_replacement_fixture',
      });
      expect(connections.single['id'], 'key-one');
      expect(
        requests.any(
          (request) => ['bind', 'unbind', 'remove'].contains(request['action']),
        ),
        isFalse,
      );
      expect(
        nodes('.repository-connections').single.textContent,
        isNot(contains('github_pat_replacement_fixture')),
      );
      expect(nodes('input[type=password]').length, 1);
      await field('GitHub personal access token', 'github_pat_unsaved_fixture');
      connection.operator.value = {'role': 'operator'};
      await settle();
      expect(nodes('input[type=password]'), isEmpty);
      connection.operator.value = {'role': 'admin'};
      // The full page remounts administrative content after authentication;
      // this isolated clone-card fixture has no outer role observer.
      binding.detachRootComponent();
      web.document.getElementById('repository-test')!.textContent = '';
      binding.attachRootComponent(
        CloneProjectCard(onCloned: (_) {}),
        attachTo: '#repository-test',
      );
      await settle();
      final restoredAuthentication =
          nodes('select').firstWhere(
                (node) =>
                    node.getAttribute('aria-label') == 'Authentication' ||
                    node.parentElement?.textContent?.startsWith(
                          'Authentication',
                        ) ==
                        true,
              )
              as web.HTMLSelectElement;
      restoredAuthentication.value = 'token';
      restoredAuthentication.dispatchEvent(
        web.Event('change', web.EventInit(bubbles: true)),
      );
      await settle();
      expect(
        (nodes('input[type=password]').single as web.HTMLInputElement).value,
        isEmpty,
      );
      expect(
        requests.any(
          (request) => request['token'] == 'github_pat_unsaved_fixture',
        ),
        isFalse,
      );
    },
  );
  test(
    'account changes clear connection state and ignore a late access result',
    () async {
      nodes('.repository-connections > summary').single.click();
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
