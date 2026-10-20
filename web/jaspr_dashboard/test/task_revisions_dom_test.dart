@TestOn('browser')
library;

import 'package:jaspr/client.dart';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/pages/task_revisions.dart';

void main() {
  late ClientAppBinding binding;
  setUpAll(() {
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'revisions-test');
    binding = ClientAppBinding();
  });
  tearDown(() {
    binding.detachRootComponent();
    web.document.getElementById('revisions-test')!.textContent = '';
  });

  Future<void> mount(Map task) async {
    binding.attachRootComponent(
      TaskRevisions(task: task),
      attachTo: '#revisions-test',
    );
    await Future<void>.delayed(const Duration(milliseconds: 30));
  }

  test('legacy tasks do not fabricate managed provenance', () async {
    await mount({'head_sha': 'legacy'});
    expect(web.document.getElementById('revisions-test')!.textContent, isEmpty);
  });

  test(
    'revision history distinguishes commit, candidate and recovery evidence',
    () async {
      await mount({
        'candidate_revision': 'new-revision',
        'managed_revisions': {
          'next_before': 10,
          'revisions': [
            {
              'id': 'new-revision',
              'state': 'complete',
              'sha': 'new-commit',
              'parent': 'old-commit',
              'message': '<img id="untrusted-revision">',
              'author': 'Agent Nora',
              'committer': 'Herdr',
              'created_at': '2026-10-18',
              'file_count': 2,
              'policy_digest': 'frozen-policy',
            },
            {
              'id': 'pending-revision',
              'state': 'constructed',
              'sha': 'retained-commit',
              'parent': 'new-commit',
              'message': 'Pending update',
              'author': 'Agent Nora',
              'committer': 'Herdr',
              'created_at': '2026-10-18',
              'file_count': 1,
              'error': 'Task index changed during submission.',
            },
          ],
        },
      });
      final root = web.document.getElementById('revisions-test')!;
      (root.querySelector('button') as web.HTMLElement).click();
      await Future<void>.delayed(const Duration(milliseconds: 30));
      for (final prefix in ['<img', 'Pending update']) {
        final buttons = root.querySelectorAll('button');
        for (var i = 0; i < buttons.length; i++) {
          final button = buttons.item(i) as web.HTMLElement;
          if ((button.textContent ?? '').startsWith(prefix)) {
            button.click();
            break;
          }
        }
        await Future<void>.delayed(const Duration(milliseconds: 30));
      }
      final text = root.textContent!;
      expect(text, contains('Managed revisions (2)'));
      expect(text, contains('Committed · Current candidate'));
      expect(text, contains('Recovery pending'));
      expect(text, contains('Author: Agent Nora · Committer: Herdr'));
      expect(text, contains('Parent: old-commit'));
      expect(text, contains('Frozen policy: frozen-policy'));
      expect(text, contains('Task index changed during submission.'));
      expect(text, contains('separate from candidate checks and publication'));
      expect(text, contains('2 most recent'));
      expect(web.document.querySelector('#untrusted-revision'), isNull);
      final buttons = root.querySelectorAll('button');
      expect(buttons.length, 3);
      for (var i = 0; i < buttons.length; i++) {
        expect(
          (buttons.item(i) as web.HTMLElement).hasAttribute('aria-expanded'),
          isTrue,
        );
      }
    },
  );
}
