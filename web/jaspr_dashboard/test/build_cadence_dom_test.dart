@TestOn('browser')
library;

import 'dart:async';
import 'dart:js_interop';
import 'package:jaspr/client.dart';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/pages/build_timeline.dart';

void main() {
  late ClientAppBinding binding;
  late web.HTMLDivElement root;
  final assets = <web.Element>[];
  setUpAll(() async {
    root = web.HTMLDivElement()
      ..id = 'build-cadence-test'
      ..style.width = '343px';
    web.document.body!.appendChild(root);
    for (final path in [
      'css/tokens.css',
      'styles.css',
      'css/components.css',
      'css/composites.css',
      'css/builds.css',
    ]) {
      final loaded = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/$path';
      link.addEventListener('load', ((web.Event _) => loaded.complete()).toJS);
      link.addEventListener(
        'error',
        ((web.Event _) => loaded.completeError(
          StateError('Cannot load $path'),
        )).toJS,
      );
      web.document.head!.appendChild(link);
      assets.add(link);
      await loaded.future.timeout(const Duration(seconds: 5));
    }
    binding = ClientAppBinding();
  });
  tearDown(() {
    binding.detachRootComponent();
    root.textContent = '';
  });
  tearDownAll(() {
    for (final asset in assets) asset.remove();
    root.remove();
  });

  test(
    'narrow build cadence is a named keyboard-focusable scroll region',
    () async {
      final now = DateTime(2026, 10, 23, 12);
      binding.attachRootComponent(
        cadenceStrip([
          {
            'id': 'review-build',
            'state': 'complete',
            'started_at': '2026-10-23T10:00:00',
            'repository': 'herdr_proxmox',
          },
        ], now)!,
        attachTo: '#build-cadence-test',
      );
      final deadline = DateTime.now().add(const Duration(seconds: 5));
      while (true) {
        final candidate = root.querySelector('.cadence__scroll');
        if (candidate != null && candidate.scrollWidth > candidate.clientWidth)
          break;
        if (DateTime.now().isAfter(deadline)) {
          fail(
            'Build cadence did not render its scrollable timeline: '
            '${root.innerHTML}',
          );
        }
        await Future<void>.delayed(const Duration(milliseconds: 10));
      }
      final viewport =
          root.querySelector('.cadence__scroll')! as web.HTMLElement;
      expect(viewport.scrollWidth, greaterThan(viewport.clientWidth));
      expect(viewport.getAttribute('role'), 'region');
      expect(viewport.getAttribute('aria-label'), 'Build cadence timeline');
      expect(viewport.tabIndex, 0);
      viewport.focus();
      expect(web.document.activeElement, viewport);
      viewport.scrollLeft =
          -80; // The reverse-row scroller opens at its latest end.
      expect(viewport.scrollLeft, lessThan(0));
      expect(root.scrollWidth, lessThanOrEqualTo(root.clientWidth + 1));
    },
  );
}
