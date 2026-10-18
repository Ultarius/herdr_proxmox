@TestOn('browser')
library;

import 'dart:convert';
import 'dart:async';
import 'dart:js_interop';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/ui.dart';
import 'package:herdr_jaspr_dashboard/pages/task_workflow.dart';

class _Host extends StatefulComponent {
  const _Host(this.ready);
  final void Function(_HostState) ready;
  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> {
  List<Map<String, dynamic>> tasks = [];
  List<Map<String, dynamic>> releases = [];
  final releaseActions = <Map<String, dynamic>>[];
  Future<Map<String, dynamic>> Function(String, String?, int?)? historyLoader;
  final opened = <String>[];
  bool visible = true;
  void hideWorkflow() => setState(() => visible = false);
  void replace(List<Map<String, dynamic>> rows) => setState(() => tasks = rows);
  void replaceReleases(List<Map<String, dynamic>> rows) =>
      setState(() => releases = rows);
  @override
  void initState() {
    super.initState();
    component.ready(this);
  }

  @override
  Component build(BuildContext context) => !visible
      ? const Component.empty()
      : TaskWorkflow(
          tasks: tasks,
          releases: releases,
          onLoadReleaseHistory: historyLoader,
          onReleaseAction: (action, body) =>
              releaseActions.add({'action': action, ...body}),
          profiles: const [
            {'id': 'worker', 'name': 'Actual worker'},
            {'id': 'reviewer', 'name': 'Independent reviewer'},
          ],
          groups: const [
            {'id': 'release-group', 'name': 'Release evidence team'},
          ],
          onOpenTask: opened.add,
          executor: 'service',
          listView: el('div', [
            text('Original task actions preserved'),
            button('Open original task', () => opened.add('original-list')),
          ]),
        );
}

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 25));
Iterable<web.HTMLElement> nodes(String selector) sync* {
  final list = web.document.querySelectorAll('#workflow-test $selector');
  for (var i = 0; i < list.length; i++) yield list.item(i) as web.HTMLElement;
}

web.HTMLElement task(String id) => nodes('[data-node="$id"]').single;
Future<void> click(String name) async {
  nodes('button').firstWhere((node) => node.textContent == name).click();
  await settle();
}

Future<void> choose(String label, String value) async {
  final control =
      nodes('label')
              .firstWhere(
                (node) => node.querySelector('span')?.textContent == label,
              )
              .querySelector('select')
          as web.HTMLSelectElement;
  control.value = value;
  control.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
  await settle();
}

void key(web.HTMLElement element, String value) => element.dispatchEvent(
  web.KeyboardEvent(
    'keydown',
    web.KeyboardEventInit(key: value, bubbles: true, cancelable: true),
  ),
);

Map<String, dynamic> row(
  String id, {
  String state = 'draft',
  String profile = 'worker',
  Map<String, dynamic>? source,
}) => {
  'id': id,
  'title': 'Task $id',
  'state': state,
  'profile_id': profile,
  'repository': 'repo',
  'assigned_agent': {'id': 'template', 'name': 'Template identity'},
  if (source != null) 'source': source,
};

void main() {
  late ClientAppBinding binding;
  late _HostState state;
  setUpAll(() async {
    web.document.body!.appendChild(
      web.HTMLDivElement()
        ..id = 'workflow-test'
        ..style.width = '760px',
    );
    for (final path in [
      '../web/css/tokens.css',
      '../web/styles.css',
      '../web/css/components.css',
      '../web/css/composites.css',
      '../web/css/task-workflow.css',
    ]) {
      final loaded = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = path;
      link.addEventListener('load', ((web.Event _) => loaded.complete()).toJS);
      link.addEventListener(
        'error',
        ((web.Event _) => loaded.completeError(
          StateError('Cannot load $path'),
        )).toJS,
      );
      web.document.head!.appendChild(link);
      await loaded.future.timeout(const Duration(seconds: 5));
    }
    binding = ClientAppBinding();
  });
  setUp(() async {
    binding.attachRootComponent(
      _Host((value) => state = value),
      attachTo: '#workflow-test',
    );
    await settle();
  });
  tearDown(() {
    binding.detachRootComponent();
    web.document.getElementById('workflow-test')!.textContent = '';
  });

  Map<String, dynamic> release({String state = 'collecting'}) => {
    'id': 'release-one',
    'version': 'alpha.15',
    'repository': 'repo',
    'state': state,
    'edit_version': 4,
    'planned_cut_at': '2026-10-20',
    'counts': {'selected': 2, 'incorporated': 1, 'blocked': 0},
    'members': [
      {
        'task_id': 'origin',
        'title': 'Task origin',
        'candidate_sha': 'a' * 40,
        'state': 'incorporated',
        'integration_sha': 'c' * 40,
      },
      {
        'task_id': 'follow',
        'title': 'Task follow',
        'candidate_sha': 'b' * 40,
        'state': 'pending',
      },
    ],
  };

  test(
    'release inspector shows task owners and explicitly requests group advice',
    () async {
      state.replace([row('origin', state: 'implementing'), row('follow')]);
      state.replaceReleases([release()]);
      await settle();
      expect(
        task('release:release-one').textContent,
        contains('1 implementing'),
      );
      task('release:release-one').click();
      await settle();
      expect(
        nodes('[data-release-work="origin"]').single.textContent,
        contains('Actual worker · task implementing'),
      );
      await choose('Release review group', 'release-group');
      await click('Ask group to review release evidence');
      expect(state.releaseActions.single, containsPair('action', 'review'));
      expect(
        state.releaseActions.single,
        containsPair('group_id', 'release-group'),
      );
      expect(state.releaseActions.single, containsPair('edit_version', 4));
      expect(state.opened, isEmpty);
      state.replaceReleases([
        release()
          ..['review'] = {
            'state': 'complete',
            'current': false,
            'group_id': 'release-group',
            'group_name': 'Release evidence team',
            'digest': 'packet-digest',
            'job_id': 'discussion',
            'report': 'Capture and validate missing evidence.',
          },
      ]);
      await settle();
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('Outdated review'),
      );
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('Capture and validate'),
      );
      expect(state.releaseActions.length, 1);
    },
  );

  test(
    'automatic group review has a separate explicit grant and persistent budget',
    () async {
      state.replaceReleases([release()]);
      await settle();
      task('release:release-one').click();
      await settle();
      await choose('Release review group', 'release-group');
      await click('Enable automatic release review');
      expect(
        state.releaseActions.single,
        containsPair('action', 'review_policy'),
      );
      expect(state.releaseActions.single, containsPair('enabled', true));
      expect(
        state.releaseActions.single,
        containsPair('group_id', 'release-group'),
      );
      expect(state.releaseActions.single, containsPair('max_reviews', 3));
      state.replaceReleases([
        release()
          ..['review_policy'] = {
            'enabled': true,
            'group_id': 'release-group',
            'max_reviews': 3,
            'reviews_used': 1,
            'notice': 'Waiting for required delivery inspections.',
          },
      ]);
      await settle();
      expect(nodes('.task-wf-inspector').single.textContent, contains('1 / 3'));
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('delivery inspections'),
      );
      await click('Disable automatic release review');
      expect(
        state.releaseActions.last,
        containsPair('action', 'review_policy'),
      );
      expect(state.releaseActions.last, containsPair('enabled', false));
      expect(state.releaseActions.length, 2);
    },
  );

  test(
    'draft release PR action reviews exact head and never repeats publication',
    () async {
      final published = release(state: 'published')
        ..['resulting_sha'] = 'c' * 40
        ..['revision'] = {'digest': 'manifest-digest'}
        ..['pull_preview'] = {
          'title': 'Release alpha.15',
          'body': 'Verified combined evidence.',
        };
      state.replaceReleases([published]);
      await settle();
      task('release:release-one').click();
      await settle();
      await click('Create reviewed draft release PR');
      expect(state.releaseActions.single, containsPair('action', 'pull'));
      expect(state.releaseActions.single, containsPair('head_sha', 'c' * 40));
      expect(
        state.releaseActions.single,
        containsPair('revision', 'manifest-digest'),
      );
      expect(state.releaseActions.single, containsPair('reviewed', true));
      expect(
        state.releaseActions.single,
        containsPair('body', 'Verified combined evidence.'),
      );
      expect(
        nodes(
          'button',
        ).any((b) => b.textContent == 'Publish verified release branch'),
        false,
      );
      state.replaceReleases([
        Map<String, dynamic>.from(published)
          ..['pull_request'] = {
            'state': 'complete',
            'number': 15,
            'url': 'https://github.com/example/repo/pull/15',
            'reused': true,
          },
      ]);
      await settle();
      expect(
        nodes('a')
            .where((a) => a.textContent == 'Open release PR #15')
            .single
            .getAttribute('href'),
        'https://github.com/example/repo/pull/15',
      );
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('existing PR was reconciled'),
      );
      expect(
        nodes(
          'button',
        ).any((b) => b.textContent == 'Create reviewed draft release PR'),
        false,
      );
    },
  );

  test(
    'edited release PR preview requires review again when the combined SHA changes',
    () async {
      final published = release(state: 'published')
        ..['resulting_sha'] = 'c' * 40
        ..['pull_preview'] = {
          'title': 'Release alpha.15',
          'body': 'Verified combined evidence.',
        };
      state.replaceReleases([published]);
      await settle();
      task('release:release-one').click();
      await settle();
      final input =
          nodes('label')
                  .firstWhere(
                    (n) =>
                        n.querySelector('span')?.textContent ==
                        'Release PR title',
                  )
                  .querySelector('input')
              as web.HTMLInputElement;
      input.value = 'Reviewed title';
      input.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      final body =
          nodes('label')
                  .firstWhere(
                    (n) =>
                        n.querySelector('span')?.textContent ==
                        'Release PR description',
                  )
                  .querySelector('textarea')
              as web.HTMLTextAreaElement;
      body.value = 'Description reviewed for the original commit.';
      body.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      state.replaceReleases([
        Map<String, dynamic>.from(published)..['resulting_sha'] = 'd' * 40,
      ]);
      await settle();
      final create =
          nodes('button').firstWhere(
                (b) => b.textContent == 'Create reviewed draft release PR',
              )
              as web.HTMLButtonElement;
      expect(create.disabled, true);
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('combined commit changed'),
      );
      // Editing either old field must not implicitly approve the untouched
      // counterpart for the new commit. Only the explicit reset rebinds it.
      final staleTitle =
          nodes('label')
                  .firstWhere(
                    (n) =>
                        n.querySelector('span')?.textContent ==
                        'Release PR title',
                  )
                  .querySelector('input')
              as web.HTMLInputElement;
      staleTitle.value = 'Reviewed title!';
      staleTitle.dispatchEvent(
        web.Event('input', web.EventInit(bubbles: true)),
      );
      await settle();
      expect(
        (nodes('button').firstWhere(
                  (b) => b.textContent == 'Create reviewed draft release PR',
                )
                as web.HTMLButtonElement)
            .disabled,
        true,
      );
      final staleBody =
          nodes('label')
                  .firstWhere(
                    (n) =>
                        n.querySelector('span')?.textContent ==
                        'Release PR description',
                  )
                  .querySelector('textarea')
              as web.HTMLTextAreaElement;
      expect(staleBody.value, 'Description reviewed for the original commit.');
      staleBody.value += '!';
      staleBody.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      expect(
        (nodes('button').firstWhere(
                  (b) => b.textContent == 'Create reviewed draft release PR',
                )
                as web.HTMLButtonElement)
            .disabled,
        true,
      );
      await click('Reset release PR preview');
      expect(
        (nodes('button').firstWhere(
                  (b) => b.textContent == 'Create reviewed draft release PR',
                )
                as web.HTMLButtonElement)
            .disabled,
        false,
      );
      expect(state.releaseActions, isEmpty);
    },
  );

  test(
    'release membership includes every member and preserves provenance semantics',
    () async {
      state.replace([
        row('origin'),
        row('follow', source: {'task_id': 'origin'}),
      ]);
      state.replaceReleases([release()]);
      await settle();
      final edges = nodes('[data-kind="release-membership"]').toList();
      expect(edges, hasLength(2));
      expect(edges.map((edge) => edge.getAttribute('data-from')).toSet(), {
        'origin',
        'follow',
      });
      expect(nodes('[data-kind="follow-up"]'), hasLength(1));
      expect(nodes('[data-sample="release-incorporated"]'), hasLength(1));
      expect(
        edges.where((edge) => edge.getAttribute('data-edge') == 'incorporated'),
        hasLength(1),
      );
      final card = nodes('[data-release="release-one"]').single;
      expect(card.textContent, contains('1 of 2 incorporated'));
      expect(card.textContent, contains('2026-10-20'));
      expect(
        card.getBoundingClientRect().left,
        greaterThan(task('follow').getBoundingClientRect().left),
      );
      card.click();
      await settle();
      expect(nodes('[data-release-member]'), hasLength(2));
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('does not mean merged into main'),
      );
    },
  );

  test(
    'discussion plans show unfinished membership and enable freeze after checked capture',
    () async {
      final member = <String, dynamic>{
        'task_id': 'origin',
        'title': 'Task origin',
        'selection': 'automatic',
        'candidate_sha': null,
        'eligibility_reason':
            'Waiting for a captured candidate with passing required checks.',
      };
      final planned = release()
        ..['source'] = {'kind': 'discussion', 'job_id': 'discussion-one'}
        ..['members'] = [member]
        ..['counts'] = {'selected': 1, 'incorporated': 0};
      state.replace([row('origin')]);
      state.replaceReleases([planned]);
      await settle();
      expect(nodes('[data-kind="release-membership"]'), hasLength(1));
      nodes('[data-release="release-one"]').single.click();
      await settle();
      final inspector = nodes('.task-wf-inspector').single;
      expect(inspector.textContent, contains('discussion-one'));
      expect(inspector.textContent, contains('Planned · awaiting checks'));
      expect(inspector.textContent, contains('Release plan'));
      web.HTMLButtonElement freezeButton() =>
          nodes('button').singleWhere(
                (button) =>
                    button.textContent == 'Freeze and integrate 1 tasks',
              )
              as web.HTMLButtonElement;
      expect(freezeButton().disabled, isTrue);
      state.replaceReleases([
        {
          ...planned,
          'edit_version': 5,
          'members': [
            {...member, 'candidate_sha': 'a' * 40},
          ],
        },
      ]);
      await settle();
      expect(freezeButton().disabled, isFalse);
      freezeButton().click();
      await settle();
      expect(state.releaseActions.last, containsPair('action', 'freeze'));
      expect(state.releaseActions.last, containsPair('edit_version', 5));
    },
  );

  test(
    'release inspector sends the exact revision and keyboard follows membership',
    () async {
      final frozen = release(state: 'ready')
        ..['revision'] = {
          'digest': 'manifest-digest',
          'base_sha': 'd' * 40,
          'policy_digest': 'policy-digest',
        }
        ..['resulting_sha'] = 'e' * 40
        ..['publication'] = {
          'destination': 'fork/repo',
          'branch': 'herdr/release-one',
        };
      state.replace([row('origin'), row('follow')]);
      state.replaceReleases([frozen]);
      await settle();
      key(task('origin'), 'ArrowRight');
      await settle();
      expect(
        nodes(
          '[data-release="release-one"]',
        ).single.getAttribute('aria-pressed'),
        'true',
      );
      await click('Publish verified release branch');
      expect(
        state.releaseActions.last,
        containsPair('revision', 'manifest-digest'),
      );
      expect(state.releaseActions.last, containsPair('head_sha', 'e' * 40));
      key(nodes('[data-release="release-one"]').single, 'ArrowLeft');
      await settle();
      expect(task('origin').getAttribute('aria-pressed'), 'true');
    },
  );

  test(
    'filtering tasks keeps release counts and hidden members visible in inspector',
    () async {
      state.replace([row('origin', state: 'merged'), row('follow')]);
      state.replaceReleases([release()]);
      await settle();
      (nodes('label')
                  .firstWhere((node) => node.textContent == 'Hide merged')
                  .querySelector('input')
              as web.HTMLInputElement)
          .click();
      await settle();
      expect(nodes('[data-node="origin"]'), isEmpty);
      expect(
        nodes('[data-release="release-one"]').single.textContent,
        contains('1 of 2 incorporated'),
      );
      expect(nodes('[data-kind="release-membership"]'), hasLength(1));
      nodes('[data-release="release-one"]').single.click();
      await settle();
      expect(nodes('[data-release-member]'), hasLength(2));
      state.replace([]);
      await settle();
      expect(nodes('[data-release="release-one"]'), hasLength(1));
      expect(nodes('[data-release-member]'), hasLength(2));
    },
  );

  test(
    'task membership is an explicit action and release creation is click based',
    () async {
      state.replace([row('origin'), row('follow')]);
      state.replaceReleases([release()]);
      await settle();
      task('origin').click();
      await settle();
      await click('Remove from alpha.15');
      expect(state.releaseActions.last, containsPair('task_id', 'origin'));
      expect(state.releaseActions.last, containsPair('included', false));
      await click('New release candidate');
      expect(
        nodes('.wf-release-dialog[open]').single.textContent,
        contains('New release candidate'),
      );
      expect(
        nodes('button')
            .firstWhere(
              (node) => node.textContent == 'Create release candidate',
            )
            .hasAttribute('disabled'),
        isTrue,
      );
    },
  );

  test('failed combined checks have an explicit retry action', () async {
    final failed = release(state: 'validation_failed')
      ..['revision'] = {'digest': 'frozen-manifest', 'generation': 1};
    state.replaceReleases([failed]);
    await settle();
    nodes('[data-release="release-one"]').single.click();
    await settle();
    await click('Re-run combined checks');
    expect(state.releaseActions.last, containsPair('action', 'retry'));
    expect(
      state.releaseActions.last,
      containsPair('revision', 'frozen-manifest'),
    );
    expect(
      nodes(
        'button',
      ).where((node) => node.textContent == 'Resume inspected release'),
      isEmpty,
    );
  });

  test(
    'continuous assembly authorization is separate from freeze and publication',
    () async {
      final planned = release(state: 'collecting');
      state.replace([row('origin'), row('follow')]);
      state.replaceReleases([planned]);
      await settle();
      nodes('[data-release="release-one"]').single.click();
      await settle();
      await click('Enable continuous assembly');
      expect(state.releaseActions.last, containsPair('action', 'assembly'));
      expect(state.releaseActions.last, containsPair('enabled', true));
      expect(state.releaseActions.last, containsPair('auto_repair', false));
      expect(state.releaseActions.last, containsPair('max_runs', 20));
      expect(
        state.releaseActions.any(
          (a) => a['action'] == 'freeze' || a['action'] == 'publish',
        ),
        isFalse,
      );
      state.replaceReleases([
        {
          ...planned,
          'state': 'integrating',
          'continuous': {
            'enabled': true,
            'auto_repair': false,
            'max_runs': 20,
            'max_repairs': 2,
            'runs_used': 1,
          },
          'revision': {'purpose': 'assembly', 'digest': 'assembly-snapshot'},
        },
      ]);
      await settle();
      await click('Stop assembly automation');
      expect(state.releaseActions.last, containsPair('enabled', false));
    },
  );

  test(
    'checked assembly requires explicit freeze before publication appears',
    () async {
      final checked = release(state: 'assembled')
        ..['continuous'] = {'enabled': true, 'max_runs': 20, 'max_repairs': 2}
        ..['resulting_sha'] = 'e' * 40
        ..['revision'] = {'purpose': 'assembly', 'digest': 'checked-snapshot'};
      state.replaceReleases([checked]);
      await settle();
      nodes('[data-release="release-one"]').single.click();
      await settle();
      expect(
        nodes(
          'button',
        ).where((b) => b.textContent == 'Publish verified release branch'),
        isEmpty,
      );
      await click('Freeze verified assembly as release candidate');
      expect(state.releaseActions.last, containsPair('action', 'freeze'));
      expect(
        state.releaseActions.last,
        containsPair('revision', 'checked-snapshot'),
      );
      expect(state.releaseActions.last, containsPair('head_sha', 'e' * 40));
    },
  );

  test(
    'collecting release members link to task validation without freezing',
    () async {
      state.replace([row('origin'), row('follow')]);
      state.replaceReleases([release(state: 'collecting')]);
      await settle();
      nodes('[data-release="release-one"]').single.click();
      await settle();
      final member = nodes('[data-release-member="origin"]').single;
      final buttons = member.querySelectorAll('button');
      for (var index = 0; index < buttons.length; index++) {
        final button = buttons.item(index) as web.HTMLButtonElement;
        if (button.textContent == 'Open task and validation') button.click();
      }
      await settle();
      expect(state.opened, ['origin']);
      expect(state.releaseActions, isEmpty);
    },
  );

  test('release-only repositories are selectable during creation', () async {
    state.replace([]);
    state.replaceReleases([release()..['repository'] = 'archived-project']);
    await settle();
    await click('New release candidate');
    final select =
        nodes('.task-wf-inspector select').single as web.HTMLSelectElement;
    expect(select.value, 'archived-project');
    expect(select.options.length, 1);
    expect(
      (select.options.item(0)! as web.HTMLOptionElement).value,
      'archived-project',
    );
  });

  test('release spacing and keyboard reveal use release dimensions', () async {
    state.replace([
      for (var index = 0; index < 20; index++) row('task-$index'),
    ]);
    state.replaceReleases([
      for (final id in ['release-a', 'release-b'])
        release()
          ..['id'] = id
          ..['members'] = [
            {'task_id': 'task-10', 'candidate_sha': 'a' * 40},
          ],
    ]);
    await settle();
    final first = nodes('[data-release="release-a"]').single;
    final second = nodes('[data-release="release-b"]').single;
    final firstTop = double.parse(first.style.top.replaceAll('px', ''));
    final secondTop = double.parse(second.style.top.replaceAll('px', ''));
    expect(secondTop - firstTop, 152);
    key(task('task-10'), 'ArrowRight');
    await settle();
    final viewport = nodes('.task-wf-viewport').single;
    final expected = (firstTop + 68 - viewport.clientHeight / 2).clamp(
      0,
      viewport.scrollHeight - viewport.clientHeight,
    );
    expect(viewport.scrollTop, closeTo(expected, 1));
  });

  test(
    'retained revisions page and open read-only evidence without current actions',
    () async {
      final current = release(state: 'ready')
        ..['revision'] = {'digest': 'c' * 64, 'generation': 3};
      final archived = release(state: 'ready')
        ..['revision'] = {
          'digest': 'a' * 64,
          'generation': 1,
          'base_sha': 'd' * 40,
        }
        ..['publication'] = {
          'destination': 'historic-fork/repo',
          'branch': 'historic-branch',
        }
        ..['validation'] = {
          'run_id': 'historic-build',
          'state': 'complete',
          'required_checks_verified': true,
          'checks': [
            {
              'name': 'Combined regression suite',
              'status': 'passed',
              'detail': 'Verified old artifact',
            },
          ],
        };
      final requests = <(String, String?, int?)>[];
      state.historyLoader = (id, revision, before) async {
        requests.add((id, revision, before));
        if (revision != null) return {'release': archived, 'read_only': true};
        if (before != null)
          return {
            'revisions': [
              {'digest': 'a' * 64, 'generation': 1, 'state': 'ready'},
            ],
          };
        return {
          'revisions': [
            {
              'digest': 'c' * 64,
              'generation': 3,
              'state': 'ready',
              'current': true,
            },
          ],
          'next_before': 3,
        };
      };
      state.replaceReleases([current]);
      await settle();
      nodes('[data-release="release-one"]').single.click();
      await settle();
      await click('Inspect retained revisions');
      await click('Load older revisions');
      expect(requests.last, ('release-one', null, 3));
      await click('Revision 1 · Ready for publication');
      expect(requests.last, ('release-one', 'a' * 64, null));
      final inspector = nodes('.task-wf-inspector').single;
      expect(
        inspector.textContent,
        contains('Retained revision 1 · read-only evidence'),
      );
      expect(inspector.textContent, contains('historic-fork/repo'));
      expect(inspector.textContent, contains('historic-build'));
      expect(inspector.textContent, contains('Combined regression suite'));
      expect(inspector.textContent, contains('Verified old artifact'));
      expect(nodes('[data-release-member]'), hasLength(2));
      expect(
        nodes('button').where(
          (node) => node.textContent == 'Publish verified release branch',
        ),
        isEmpty,
      );
      expect(
        nodes('button').where((node) => node.textContent == 'Revise selection'),
        isEmpty,
      );
      await click('Back to retained revisions');
      await click('Return to current release');
      expect(
        nodes('button').where(
          (node) => node.textContent == 'Publish verified release branch',
        ),
        hasLength(1),
      );
      expect(state.releaseActions, isEmpty);
    },
  );

  test('late history responses cannot replace another selection', () async {
    final pending = Completer<Map<String, dynamic>>();
    state.historyLoader = (_, _, _) => pending.future;
    state.replace([row('origin')]);
    state.replaceReleases([release()]);
    await settle();
    nodes('[data-release="release-one"]').single.click();
    await settle();
    await click('Inspect retained revisions');
    task('origin').click();
    await settle();
    pending.complete({
      'revisions': [
        {'digest': 'old', 'generation': 99, 'state': 'ready'},
      ],
    });
    await settle();
    expect(
      nodes('.task-wf-inspector').single.textContent,
      contains('Task origin'),
    );
    expect(
      nodes('.task-wf-inspector').single.textContent,
      isNot(contains('Revision 99')),
    );
  });

  test(
    'edges are deduplicated recorded follow-ups and execution waits only',
    () async {
      final rows = [
        row('origin')..['follow_up_tasks'] = {'p': 'follow'},
        row('follow', source: {'task_id': 'origin'}),
        row('waiting')..['blocking_execution'] = {'task_id': 'origin'},
        row('same-agent')..['assignment'] = {'state': 'queued', 'position': 2},
        row('unrelated', profile: 'reviewer'),
      ];
      final saved = jsonEncode(rows);
      state.replace(rows);
      await settle();
      expect(nodes('.task-wf-node').length, 5);
      expect(nodes('.task-wf-edge').length, 2);
      expect(nodes('[data-kind=follow-up]').length, 1);
      expect(nodes('[data-kind=execution-wait]').length, 1);
      expect(nodes('.task-wf-edge[data-to=same-agent]'), isEmpty);
      expect(nodes('canvas'), isEmpty);
      expect(
        nodes('.task-wf-legend').single.textContent,
        contains('Recorded follow-up'),
      );
      expect(nodes('#workflow-test').length, 0);
      expect(jsonEncode(rows), saved);
      expect(
        web.document.getElementById('workflow-test')!.textContent,
        isNot(contains('alpha.15')),
      );
    },
  );

  test(
    'selection uses actual worker, template, queue, reviewer and exact commit facts',
    () async {
      final selected = row('parallel')
        ..addAll({
          'assignment': {'state': 'queued', 'position': 4},
          'execution': {
            'mode': 'template',
            'template_id': 'template',
            'template_name': 'Lead engineer',
            'instance_profile_id': 'worker',
          },
          'acceptance': {'reviewer_profile_id': 'reviewer'},
          'run_id': 'recorded-run',
          'head_sha': 'b' * 40,
          'builds': {
            'b' * 40: {
              'target': 'b' * 40,
              'state': 'complete',
              'required_checks_verified': true,
            },
          },
        });
      state.replace([selected]);
      await settle();
      task('parallel').click();
      await settle();
      final inspector = nodes(
        '[aria-label="Selected task details"]',
      ).single.textContent!;
      expect(inspector, contains('Actual worker'));
      expect(inspector, contains('Parallel instance of Lead engineer'));
      expect(inspector, contains('Independent reviewer'));
      expect(inspector, contains('queued · order 4'));
      expect(inspector, contains('recorded-run'));
      expect(
        inspector,
        contains('Required checks verified for this exact commit'),
      );
      expect(state.opened, isEmpty);
      await click('Open task');
      expect(state.opened, ['parallel']);
    },
  );

  test(
    'keyboard follows recorded edges, highlights ancestors and descendants without siblings, Escape clears',
    () async {
      state.replace([
        row('origin'),
        row('left', source: {'task_id': 'origin'}),
        row('right', source: {'task_id': 'origin'}),
        row('leaf', source: {'task_id': 'left'}),
        row('unrelated', profile: 'reviewer'),
      ]);
      await settle();
      task('left').focus();
      task('left').click();
      await settle();
      expect(task('origin').hasAttribute('data-emphasis'), true);
      expect(task('leaf').hasAttribute('data-emphasis'), true);
      expect(task('right').hasAttribute('data-dim'), true);
      key(task('left'), 'ArrowRight');
      await settle();
      expect(task('leaf').getAttribute('aria-pressed'), 'true');
      expect(web.document.activeElement, same(task('leaf')));
      key(task('leaf'), 'ArrowLeft');
      await settle();
      expect(task('left').getAttribute('aria-pressed'), 'true');
      key(task('left'), 'Escape');
      await settle();
      expect(nodes('.task-wf-node[aria-pressed=true]'), isEmpty);
      expect(nodes('[aria-label="Selected task details"]'), isEmpty);
    },
  );

  test(
    'stale complete builds cannot pass exact-commit filters or inspector',
    () async {
      final stale = row('stale', state: 'review_ready')
        ..addAll({
          'head_sha': 'b' * 40,
          'builds': {
            'b' * 40: {
              'target': 'a' * 40,
              'state': 'complete',
              'required_checks_verified': true,
            },
          },
        });
      final verified = row('verified', state: 'review_ready')
        ..addAll({
          'head_sha': 'c' * 40,
          'builds': {
            'c' * 40: {
              'target': 'c' * 40,
              'state': 'complete',
              'required_checks_verified': true,
            },
          },
        });
      state.replace([stale, verified]);
      await settle();
      task('stale').click();
      await settle();
      expect(
        nodes('[aria-label="Selected task details"]').single.textContent,
        contains('No matching current-commit build evidence'),
      );
      await choose('Validation', 'verified');
      expect(nodes('.task-wf-node').length, 1);
      expect(task('verified').textContent, contains('Task verified'));
      await choose('Validation', 'unverified');
      expect(nodes('.task-wf-node').length, 1);
      expect(task('stale').textContent, contains('Task stale'));
      expect(state.opened, isEmpty);
    },
  );

  test(
    'workflow filters and lanes are local; List retains original task actions',
    () async {
      state.replace([
        row('merged', state: 'merged'),
        row('active', state: 'implementing'),
        row('other', profile: 'reviewer')..['repository'] = 'second',
      ]);
      await settle();
      expect(
        nodes('[role=tab][aria-selected=true]').single.textContent,
        'Workflow',
      );
      final hide =
          nodes('label')
                  .firstWhere((node) => node.textContent == 'Hide merged')
                  .querySelector('input')
              as web.HTMLInputElement;
      hide.click();
      await settle();
      expect(nodes('.task-wf-node').length, 2);
      await choose('Repository', 'repo');
      expect(nodes('.task-wf-node').length, 1);
      final lanes =
          nodes('label')
                  .firstWhere((node) => node.textContent == 'Lanes by agent')
                  .querySelector('input')
              as web.HTMLInputElement;
      lanes.click();
      await settle();
      expect(nodes('.task-wf-lane'), isEmpty);
      nodes('[role=tab]').last.click();
      await settle();
      expect(
        nodes('[role=tab][aria-selected=true]').single.textContent,
        contains('List'),
      );
      expect(
        web.document.getElementById('workflow-test')!.textContent,
        contains('Original task actions preserved'),
      );
      expect(state.opened, isEmpty);
      await click('Open original task');
      expect(state.opened, ['original-list']);
      await click('Workflow');
      expect(nodes('.task-wf-node').length, 1);
    },
  );

  test(
    'zoom changes native sizer; Fit uses actual viewport; empty-board drag pans',
    () async {
      final rows = [row('root')];
      for (var i = 0; i < 8; i++) {
        rows.add(
          row(
            'step-$i',
            source: {'task_id': i == 0 ? 'root' : 'step-${i - 1}'},
          ),
        );
      }
      state.replace(rows);
      await settle();
      final sizer = nodes('.task-wf-sizer').single;
      final originalWidth = sizer.getBoundingClientRect().width;
      await click('Zoom in');
      expect(nodes('output').single.textContent, '115%');
      expect(sizer.getBoundingClientRect().width, greaterThan(originalWidth));
      await click('Fit');
      expect(
        int.parse(nodes('output').single.textContent!.replaceAll('%', '')),
        lessThan(100),
      );
      await click('Zoom in');
      await click('Zoom in');
      final area = nodes('.task-wf-viewport').single;
      area.scrollLeft = 0;
      area.dispatchEvent(
        web.PointerEvent(
          'pointerdown',
          web.PointerEventInit(
            clientX: 160,
            clientY: 140,
            button: 0,
            pointerId: 1,
            bubbles: true,
          ),
        ),
      );
      area.dispatchEvent(
        web.PointerEvent(
          'pointermove',
          web.PointerEventInit(
            clientX: 60,
            clientY: 140,
            pointerId: 1,
            bubbles: true,
          ),
        ),
      );
      expect(area.scrollLeft, greaterThan(0));
      expect(area.hasAttribute('data-panning'), true);
      area.dispatchEvent(
        web.PointerEvent(
          'pointerup',
          web.PointerEventInit(pointerId: 1, bubbles: true),
        ),
      );
      expect(area.hasAttribute('data-panning'), false);
      expect(state.opened, isEmpty);
    },
  );

  test(
    'refresh replaces task data and clears disappeared selection; malformed cycles stay finite',
    () async {
      state.replace([
        row('one', source: {'task_id': 'two'}),
        row('two', source: {'task_id': 'one'}),
      ]);
      await settle();
      expect(nodes('.task-wf-node').length, 2);
      expect(
        nodes('.task-wf-stage').single.getAttribute('style'),
        isNot(contains('Infinity')),
      );
      task('one').click();
      await settle();
      expect(nodes('[aria-label="Selected task details"]').length, 1);
      state.replace([row('new', state: 'completed')]);
      await settle();
      expect(nodes('[aria-label="Selected task details"]'), isEmpty);
      expect(nodes('.task-wf-node').single.getAttribute('data-node'), 'new');
      state.replace([]);
      await settle();
      expect(
        nodes('.task-wf-empty').single.textContent,
        contains('No development tasks yet.'),
      );
      expect(nodes('.task-wf-edge'), isEmpty);
    },
  );
  test(
    'single-task release uses exact candidate and keeps prior receipts distinct',
    () async {
      state.replace([
        {
          ...row('one', state: 'review_ready'),
          'head_sha': 'a' * 40,
          'task_publication_allowed': false,
          'release_deliveries': [
            {
              'version': 'alpha.15',
              'stage': 'merged',
              'candidate_sha': 'b' * 40,
              'current_candidate_matches': false,
            },
          ],
        },
      ]);
      await settle();
      task('one').click();
      await settle();
      expect(
        nodes('.task-wf-inspector').single.textContent,
        contains('Receipt covers an earlier candidate'),
      );
      await click('Release this task only');
      expect(state.releaseActions.single, {
        'action': 'single',
        'task_id': 'one',
        'head_sha': 'a' * 40,
      });
      expect(
        nodes('.task-wf-node').single.textContent,
        isNot(contains('Merged')),
      );
    },
  );
  test(
    'full screen fills the viewport and preserves the same canvas, zoom and selection',
    () async {
      final oldOverflow = web.document.body!.style.overflow;
      state.replace([
        row('one'),
        row('two', source: {'task_id': 'one'}),
        row('three', source: {'task_id': 'two'}),
        row('four', source: {'task_id': 'three'}),
      ]);
      await settle();
      task('two').click();
      await settle();
      await click('Zoom in');
      final zoom = nodes('.btn-group__readout').single.textContent;
      final area = nodes('.task-wf-viewport').single;
      area.scrollLeft = 100;
      area.dispatchEvent(web.Event('scroll'));
      await settle();
      final left = area.scrollLeft;
      await click('Full screen');
      final root = nodes('.task-workflow').single;
      expect(root.getAttribute('data-expanded'), 'true');
      final bounds = root.getBoundingClientRect();
      expect(bounds.left, closeTo(0, 1));
      expect(bounds.top, closeTo(0, 1));
      expect(bounds.width, closeTo(web.window.innerWidth.toDouble(), 1));
      expect(bounds.height, closeTo(web.window.innerHeight.toDouble(), 1));
      expect(nodes('.task-wf-viewport').single, same(area));
      expect(area.clientHeight, greaterThan(100));
      expect(area.scrollLeft, closeTo(left, 1));
      expect(nodes('.btn-group__readout').single.textContent, zoom);
      expect(task('two').getAttribute('aria-pressed'), 'true');
      expect(
        web.window.getComputedStyle(web.document.body!).overflow,
        'hidden',
      );
      key(task('two'), 'Escape');
      await settle();
      expect(root.getAttribute('data-expanded'), 'false');
      expect(web.document.body!.style.overflow, oldOverflow);
      expect(task('two').getAttribute('aria-pressed'), 'true');
      expect(
        web.document.activeElement,
        nodes('button').firstWhere((node) => node.textContent == 'Full screen'),
      );
      await click('Full screen');
      nodes('[role=tab]')
          .firstWhere((node) => node.textContent?.startsWith('List') ?? false)
          .click();
      await settle();
      expect(root.getAttribute('data-expanded'), 'false');
      expect(web.document.body!.style.overflow, oldOverflow);
    },
  );

  test(
    'release details use a wide dialog, wrap long content, and adapt to a narrow workspace',
    () async {
      state.replace([row('origin'), row('follow')]);
      final current = release()
        ..['members'] = [
          {
            'task_id': 'origin',
            'title': 'A very long release member title ' * 8,
            'candidate_sha': 'a' * 64,
            'eligibility_reason': 'Evidence ' * 50,
          },
        ];
      state.replaceReleases([current]);
      await settle();
      nodes('[data-release="release-one"]').single.click();
      await settle();
      final dialog = nodes('.wf-release-dialog').single;
      final content = nodes('.wf-release-dialog .inspector__body').single;
      expect(dialog.getAttribute('role'), 'dialog');
      expect(dialog.getAttribute('aria-modal'), 'true');
      expect(
        dialog.getBoundingClientRect().width,
        greaterThan(web.window.innerWidth * .8),
      );
      expect(
        dialog.getBoundingClientRect().height,
        greaterThan(web.window.innerHeight * .8),
      );
      expect(content.scrollWidth, lessThanOrEqualTo(content.clientWidth + 1));
      expect(nodes('.task-wf-panel'), isEmpty);
      expect(dialog.matches(':modal'), true);
      task('origin').focus();
      expect(dialog.contains(web.document.activeElement), true);
      dialog.style.width = '320px';
      await settle();
      expect(dialog.getBoundingClientRect().width, lessThanOrEqualTo(360));
      expect(content.scrollWidth, lessThanOrEqualTo(content.clientWidth + 1));
      expect(
        web.window
            .getComputedStyle(content)
            .gridTemplateColumns
            .trim()
            .split(' '),
        hasLength(1),
      );
      await click('Back to workflow');
      expect(nodes('.wf-release-dialog[open]'), isEmpty);
      expect(nodes('.task-wf-board').single.hasAttribute('inert'), false);
      expect(
        web.document.activeElement,
        nodes('[data-release="release-one"]').single,
      );
    },
  );

  test(
    'release dialog traps focus and Escape returns to expanded workflow without losing a draft',
    () async {
      final published = release(state: 'published')
        ..['resulting_sha'] = 'c' * 40
        ..['pull_preview'] = {
          'title': 'Suggested title',
          'body': 'Suggested body',
        };
      state.replaceReleases([published]);
      await settle();
      await click('Full screen');
      nodes('[data-release="release-one"]').single.click();
      await settle();
      final dialog = nodes('.wf-release-dialog').single;
      expect(web.document.activeElement, dialog);
      key(dialog, 'Tab');
      expect(
        web.document.activeElement?.textContent,
        contains('Back to workflow'),
      );
      final input =
          nodes('.wf-release-dialog input').first as web.HTMLInputElement;
      input.value = 'Keep my reviewed title';
      input.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      input.focus();
      state.replaceReleases([
        {...published, 'edit_version': 5},
      ]);
      await settle();
      expect(web.document.activeElement, input);
      expect(input.value, 'Keep my reviewed title');
      key(input, 'Escape');
      await settle();
      expect(nodes('.wf-release-dialog[open]'), isEmpty);
      expect(
        nodes('.task-workflow').single.getAttribute('data-expanded'),
        'true',
      );
      expect(
        web.window.getComputedStyle(web.document.body!).overflow,
        'hidden',
      );
      await click('Show details');
      expect(
        (nodes('.wf-release-dialog input').first as web.HTMLInputElement).value,
        'Keep my reviewed title',
      );
      await click('Back to workflow');
      nodes('[data-release="release-one"]').single.click();
      await settle();
      expect(
        (nodes('.wf-release-dialog input').first as web.HTMLInputElement).value,
        'Keep my reviewed title',
      );
      await click('Back to workflow');
      await click('Exit full screen');
    },
  );

  test(
    'unmount restores document scrolling while expanded release details are open',
    () async {
      final oldOverflow = web.document.body!.style.overflow;
      state.replaceReleases([release()]);
      await settle();
      await click('Full screen');
      nodes('[data-release="release-one"]').single.click();
      await settle();
      expect(
        web.window.getComputedStyle(web.document.body!).overflow,
        'hidden',
      );
      state.hideWorkflow();
      await settle();
      expect(web.document.body!.style.overflow, oldOverflow);
      expect(nodes('.wf-release-dialog[open]'), isEmpty);
    },
  );
  test(
    'native release modal covers the whole page and Escape works after a backdrop click',
    () async {
      final outside = web.HTMLButtonElement()
        ..textContent = 'Outside navigation';
      final overlay = web.HTMLDivElement()
        ..style.position = 'fixed'
        ..style.inset = '0'
        ..style.zIndex = '999999';
      overlay.appendChild(outside);
      web.document.body!.appendChild(overlay);
      addTearDown(() => overlay.remove());
      state.replace([row('origin')]);
      state.replaceReleases([release()]);
      await settle();
      await click('Full screen');
      final releaseNode = nodes('[data-release="release-one"]').single;
      releaseNode.focus();
      releaseNode.click();
      await settle();
      final dialog =
          nodes('.wf-release-dialog').single as web.HTMLDialogElement;
      expect(dialog.open, true);
      expect(dialog.matches(':modal'), true);
      outside.focus();
      expect(dialog.contains(web.document.activeElement), true);
      final bounds = dialog.getBoundingClientRect();
      expect(
        dialog.contains(
          web.document.elementFromPoint(bounds.left + 10, bounds.top + 10),
        ),
        true,
      );
      // Native backdrop clicks are retargeted to the dialog element.
      dialog.click();
      await settle();
      expect(web.document.activeElement, dialog);
      key(web.document.activeElement! as web.HTMLElement, 'Escape');
      await settle();
      expect(dialog.open, false);
      expect(dialog.matches(':modal'), false);
      expect(releaseNode.getAttribute('aria-pressed'), 'true');
      expect(web.document.activeElement, releaseNode);
      expect(
        nodes('.task-workflow').single.getAttribute('data-expanded'),
        'true',
      );
      await click('Show details');
      dialog.dispatchEvent(
        web.Event('cancel', web.EventInit(cancelable: true)),
      );
      await settle();
      expect(dialog.open, false);
      await click('Exit full screen');
      outside.focus();
      expect(web.document.activeElement, outside);
    },
  );

  test(
    'details controls resolve to mounted elements and creation has one heading',
    () async {
      web.HTMLElement toggle() =>
          nodes('.wf-summary__actions button[aria-expanded]').single;
      void validTarget() {
        final target = toggle().getAttribute('aria-controls');
        if (target != null)
          expect(web.document.getElementById(target), isNotNull);
      }

      state.replace([row('origin')]);
      state.replaceReleases([release()]);
      await settle();
      expect(toggle().getAttribute('aria-controls'), isNull);
      await click('Show details');
      validTarget();
      await click('Hide details');
      expect(toggle().getAttribute('aria-controls'), isNull);
      nodes('[data-release="release-one"]').single.click();
      await settle();
      validTarget();
      await click('Back to workflow');
      validTarget();
      expect(toggle().getAttribute('aria-expanded'), 'false');
      await click('New release candidate');
      validTarget();
      final dialog = nodes('.wf-release-dialog[open]').single;
      final heading = web.document.getElementById(
        dialog.getAttribute('aria-labelledby')!,
      );
      expect(heading?.textContent, 'New release candidate');
      expect(
        nodes(
          '.wf-release-dialog h2',
        ).where((node) => node.textContent == 'New release candidate'),
        hasLength(1),
      );
      await click('Back to workflow');
    },
  );

  test(
    'IME Escape cannot dismiss release details or exit the expanded workflow',
    () async {
      state.replace([row('origin')]);
      state.replaceReleases([release()]);
      await settle();
      await click('Full screen');
      nodes('[data-release="release-one"]').single.click();
      await settle();
      final dialog =
          nodes('.wf-release-dialog').single as web.HTMLDialogElement;
      dialog.dispatchEvent(
        web.CompositionEvent(
          'compositionstart',
          web.CompositionEventInit(bubbles: true),
        ),
      );
      dialog.dispatchEvent(
        web.KeyboardEvent(
          'keydown',
          web.KeyboardEventInit(
            key: 'Escape',
            isComposing: true,
            bubbles: true,
            cancelable: true,
          ),
        ),
      );
      dialog.dispatchEvent(
        web.Event('cancel', web.EventInit(cancelable: true)),
      );
      await settle();
      expect(dialog.open, true);
      dialog.dispatchEvent(
        web.CompositionEvent(
          'compositionend',
          web.CompositionEventInit(bubbles: true),
        ),
      );
      key(dialog, 'Escape');
      await settle();
      expect(dialog.open, false);
      final root = nodes('.task-workflow').single;
      task('origin').click();
      await settle();
      task('origin').dispatchEvent(
        web.KeyboardEvent(
          'keydown',
          web.KeyboardEventInit(
            key: 'Escape',
            isComposing: true,
            bubbles: true,
            cancelable: true,
          ),
        ),
      );
      await settle();
      expect(root.getAttribute('data-expanded'), 'true');
      expect(task('origin').getAttribute('aria-pressed'), 'true');
      root.dispatchEvent(
        web.CompositionEvent(
          'compositionstart',
          web.CompositionEventInit(bubbles: true),
        ),
      );
      key(root, 'Escape');
      await settle();
      expect(root.getAttribute('data-expanded'), 'true');
      root.dispatchEvent(
        web.CompositionEvent(
          'compositionend',
          web.CompositionEventInit(bubbles: true),
        ),
      );
      key(root, 'Escape');
      await settle();
      expect(root.getAttribute('data-expanded'), 'false');
    },
  );

  test(
    'a disappearing release closes the modal and restores a connected focus target',
    () async {
      state.replace([row('origin')]);
      state.replaceReleases([release()]);
      await settle();
      final releaseNode = nodes('[data-release="release-one"]').single;
      releaseNode.focus();
      releaseNode.click();
      await settle();
      final dialog =
          nodes('.wf-release-dialog').single as web.HTMLDialogElement;
      expect(dialog.open, true);
      state.replaceReleases([]);
      await settle();
      expect(dialog.open, false);
      expect(dialog.matches(':modal'), false);
      expect(web.document.activeElement?.isConnected, true);
      expect(web.document.activeElement?.textContent, 'Full screen');
      expect(nodes('.task-wf-panel'), isEmpty);
      final create = nodes(
        'button',
      ).firstWhere((node) => node.textContent == 'New release candidate');
      create.focus();
      create.click();
      await settle();
      await click('Back to workflow');
      expect(web.document.activeElement, create);
    },
  );

  test(
    'CSS scroll locks preserve another overlay owner and never write inline overflow',
    () async {
      final body = web.document.body!;
      final original = body.style.overflow;
      addTearDown(() => body.style.overflow = original);
      body.style.overflow = 'auto';
      state.replaceReleases([release()]);
      await settle();
      await click('Full screen');
      expect(body.style.overflow, 'auto');
      expect(web.window.getComputedStyle(body).overflow, 'hidden');
      // Another component takes its own lock while this workspace is expanded.
      body.style.overflow = 'hidden';
      await click('Exit full screen');
      expect(body.style.overflow, 'hidden');
      body.style.overflow = 'auto';
      expect(web.window.getComputedStyle(body).overflow, 'auto');
      nodes('[data-release="release-one"]').single.click();
      await settle();
      expect(body.style.overflow, 'auto');
      expect(web.window.getComputedStyle(body).overflow, 'hidden');
      await click('Back to workflow');
      expect(body.style.overflow, 'auto');
      expect(web.window.getComputedStyle(body).overflow, 'auto');
    },
  );
}
