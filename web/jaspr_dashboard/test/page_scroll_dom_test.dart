@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/app.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'package:herdr_jaspr_dashboard/pages/native_task_ui.dart' as native;

Future<void> settle([int duration = 60]) =>
    Future<void>.delayed(Duration(milliseconds: duration));
Iterable<web.HTMLElement> nodes(String selector) sync* {
  final all = web.document.querySelectorAll('#page-scroll-test $selector');
  for (var i = 0; i < all.length; i++) yield all.item(i) as web.HTMLElement;
}

bool verticallyScrollable(web.HTMLElement node) {
  final overflow = web.window.getComputedStyle(node).overflowY;
  return ['auto', 'scroll'].contains(overflow) &&
      node.scrollHeight > node.clientHeight + 1;
}

List<Map<String, dynamic>> measurements() => [
  for (final node in [
    web.document.documentElement!,
    web.document.body!,
    ...nodes(
      '.app,.workspace,.main-content,.page-frame,.ops-page,.card,.knowledge-list',
    ),
  ])
    {
      'node': node.tagName,
      'class': node.className,
      'height': node.clientHeight,
      'content': node.scrollHeight,
      'overflowY': web.window.getComputedStyle(node).overflowY,
    },
];

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  final requests = <Map<String, dynamic>>[];
  bool mountedApp = false;
  setUpAll(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    web.document.body!.appendChild(
      web.HTMLDivElement()..id = 'page-scroll-test',
    );
    for (final path in [
      'css/tokens.css',
      'styles.css',
      'css/components.css',
      'css/composites.css',
      'css/overview.css',
      'css/org-chart.css',
      'css/task-workflow.css',
      'css/builds.css',
      'css/organization.css',
      'css/pages.css',
    ]) {
      final done = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/$path';
      link.addEventListener('load', ((web.Event _) => done.complete()).toJS);
      link.addEventListener(
        'error',
        ((web.Event _) => done.completeError(
          StateError('Cannot load $path'),
        )).toJS,
      );
      web.document.head!.appendChild(link);
      await done.future.timeout(const Duration(seconds: 5));
    }
    binding = ClientAppBinding();
  });
  setUp(() {
    requests.clear();
    mountedApp = false;
    web.window.history.replaceState(null, '', '#/knowledge/org');
  });
  tearDown(() async {
    ui.closeDialogs();
    await settle();
    binding.detachRootComponent();
    if (mountedApp) {
      await connection.disconnect();
      await BlocScope.reset();
    }
    web.document.getElementById('page-scroll-test')!.textContent = '';
  });
  Future<void> mountKnowledge() async {
    connection = DashboardBloc(
      client: MockClient((request) async {
        final path = request.url.path;
        if (request.method == 'POST')
          requests.add({
            'path': path,
            ...jsonDecode(request.body) as Map<String, dynamic>,
          });
        final data = path == '/api/session'
            ? {'authenticated': true, 'role': 'admin', 'operator': 'reader'}
            : path == '/api/organizations/directory'
            ? {
                'organizations': [
                  {'id': 'org', 'name': 'Engineering'},
                ],
                'profiles': [],
                'groups': [],
              }
            : path == '/api/knowledge'
            ? {
                'records': [
                  for (var i = 0; i < 55; i++)
                    {
                      'id': 'record-$i',
                      'title': 'Knowledge record $i',
                      'kind': 'finding',
                      'state': 'reported',
                      'repository': 'repo',
                      'body': List.filled(
                        140,
                        'Recorded evidence from current code, with limitations.',
                      ).join('\n'),
                      'created_at': '2026-10-14',
                      'source': {'kind': 'manual', 'checks_verified': false},
                      'audit': [],
                    },
                ],
                'repositories': ['repo'],
                'next_before': null,
              }
            : path == '/api/integration'
            ? {'alerts': []}
            : {'workspaces': [], 'agents': []};
        return http.Response(jsonEncode(data), 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    mountedApp = true;
    binding.attachRootComponent(
      const DashboardApp(),
      attachTo: '#page-scroll-test',
    );
    await settle(100);
  }

  void expectSinglePageScroller() {
    final main = nodes('.main-content').single;
    final inner = nodes('.main-content *').where(verticallyScrollable).toList();
    final document = web.document.scrollingElement!;
    expect(
      verticallyScrollable(main),
      true,
      reason: jsonEncode(measurements()),
    );
    expect(inner, isEmpty, reason: jsonEncode(measurements()));
    expect(
      document.scrollHeight,
      lessThanOrEqualTo(document.clientHeight + 1),
      reason: jsonEncode(measurements()),
    );
  }

  test(
    'long knowledge page has only the application content scrollbar before and after search and expansion',
    () async {
      await mountKnowledge();
      expect(nodes('.knowledge-record').length, 55);
      expectSinglePageScroller();
      final label = nodes('label').firstWhere(
        (node) => node.querySelector('span')?.textContent == 'Search knowledge',
      );
      final search = label.querySelector('input') as web.HTMLInputElement;
      search.value = 'record';
      search.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle(310);
      expect(requests.last['query'], 'record');
      expectSinglePageScroller();
      nodes('.knowledge-record__toggle').first.click();
      await settle();
      expect(nodes('.knowledge-record__body:not([hidden])').length, 1);
      expectSinglePageScroller();
      final main = nodes('.main-content').single;
      main.scrollTop = 300;
      expect(main.scrollTop, greaterThan(0));
      expect(web.document.scrollingElement!.scrollTop, 0);
    },
  );

  test(
    'native page and dialog content wrappers do not add unconstrained scroll containers',
    () async {
      binding.attachRootComponent(
        native.ListView(
          children: [
            native.SingleChildScrollView(
              child: native.Column(
                children: [
                  for (var i = 0; i < 90; i++) native.Text('Page line $i'),
                ],
              ),
            ),
          ],
        ),
        attachTo: '#page-scroll-test',
      );
      await settle();
      expect(nodes('.native-page *').where(verticallyScrollable), isEmpty);
      expect(
        web.window.getComputedStyle(nodes('.native-page').single).overflowY,
        'visible',
      );
      expect(
        web.window
            .getComputedStyle(nodes('.native-scroll-content').single)
            .overflowY,
        'visible',
      );
    },
  );

  test(
    'intentional constrained evidence output retains its own scroll surface',
    () async {
      binding.attachRootComponent(
        native.SelectableText(
          List.filled(200, 'Build log evidence').join('\n'),
        ),
        attachTo: '#page-scroll-test',
      );
      await settle();
      final evidence = nodes('.evidence-text').single;
      expect(verticallyScrollable(evidence), true);
      expect(evidence.clientHeight, lessThanOrEqualTo(420));
    },
  );

  test(
    'long native modal content scrolls in the constrained dialog only',
    () async {
      await mountKnowledge();
      final closed = ui.showDialog<void>(
        'Long evidence dialog',
        (close) => native.Column(
          children: [
            native.SingleChildScrollView(
              child: native.Column(
                children: [
                  for (var i = 0; i < 150; i++)
                    native.Text('Recorded review evidence line $i'),
                ],
              ),
            ),
            native.TextButton(
              onPressed: () => close(null),
              child: const native.Text('Done'),
            ),
          ],
        ),
      );
      await settle();
      final dialog = nodes('.dialog').single;
      expect(verticallyScrollable(dialog), true);
      expect(nodes('.dialog *').where(verticallyScrollable), isEmpty);
      expect(verticallyScrollable(nodes('.dialog-layer').single), false);
      expect(
        web.document.scrollingElement!.scrollHeight,
        lessThanOrEqualTo(web.document.scrollingElement!.clientHeight + 1),
      );
      ui.closeDialogs();
      await closed;
      await settle();
      expectSinglePageScroller();
    },
  );
}
