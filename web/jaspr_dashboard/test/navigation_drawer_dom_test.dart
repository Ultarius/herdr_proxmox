@TestOn('browser')
library;

import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'package:http/testing.dart';
import 'package:jaspr/client.dart';
import 'package:juice/juice.dart';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/app.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;
import 'fixtures/dashboard_pages.dart';

@JS('Object.defineProperty')
external void defineProperty(
  JSObject object,
  JSString key,
  JSObject descriptor,
);

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  late web.HTMLDivElement root;
  late web.MediaQueryList media;
  late JSFunction originalMatchMedia;
  final assets = <web.Element>[];
  final originalHash = web.window.location.hash;

  Future<void> until(bool Function() ready, String reason) async {
    final deadline = DateTime.now().add(const Duration(seconds: 5));
    while (!ready()) {
      if (DateTime.now().isAfter(deadline)) {
        fail(
          '$reason; active=${web.document.activeElement?.outerHTML}; '
          'navigation=${root.querySelector('#dashboard-navigation')?.outerHTML}',
        );
      }
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
  }

  void setMobile(bool mobile) {
    defineProperty(
      media,
      'matches'.toJS,
      {'value': mobile, 'configurable': true}.jsify()! as JSObject,
    );
    media.dispatchEvent(web.Event('change'));
  }

  web.HTMLDialogElement getDrawer() =>
      root.querySelector('#dashboard-navigation')! as web.HTMLDialogElement;
  web.HTMLButtonElement getTrigger() =>
      root.querySelector('.drawer-toggle')! as web.HTMLButtonElement;

  Future<void> open() async {
    getTrigger().click();
    await until(
      () =>
          getDrawer().matches(':modal') &&
          web.document.activeElement == root.querySelector('.sidebar__close'),
      'Drawer did not open modally and focus its close control',
    );
    expect(getTrigger().getAttribute('aria-expanded'), 'true');
    expect(getTrigger().getAttribute('aria-controls'), getDrawer().id);
  }

  setUpAll(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    root = web.HTMLDivElement()..id = 'navigation-drawer-test';
    web.document.body!.appendChild(root);
    for (final path in [
      'css/tokens.css',
      'styles.css',
      'css/components.css',
      'css/composites.css',
      'css/pages.css',
    ]) {
      final ready = Completer<void>();
      final link = web.HTMLLinkElement()
        ..rel = 'stylesheet'
        ..href = '../web/$path';
      link.addEventListener('load', ((web.Event _) => ready.complete()).toJS);
      link.addEventListener(
        'error',
        ((web.Event _) => ready.completeError(
          StateError('Cannot load $path'),
        )).toJS,
      );
      web.document.head!.appendChild(link);
      assets.add(link);
      await ready.future.timeout(const Duration(seconds: 5));
    }
    binding = ClientAppBinding();
    // The standard runner has a desktop viewport. This fixture controls the JS
    // breakpoint and makes its close control/scrollport visible; the trusted
    // browser check exercises the real responsive stylesheet at phone widths.
    final fixtureStyle = web.HTMLStyleElement()
      ..textContent =
          '#navigation-drawer-test .sidebar__close{display:flex}'
          '#navigation-drawer-test .drawer-toggle{display:inline-flex}'
          '#navigation-drawer-test .sidebar__scroll{height:100px;flex:none}';
    web.document.head!.appendChild(fixtureStyle);
    assets.add(fixtureStyle);
  });

  setUp(() async {
    originalMatchMedia = web.window.getProperty<JSFunction>('matchMedia'.toJS);
    media = web.window.matchMedia('(min-width: 0px)');
    setMobile(true);
    // Use the browser's real dialog and EventTarget, controlling only the
    // breakpoint so both branches run in the standard Chrome test viewport.
    web.window.setProperty(
      'matchMedia'.toJS,
      ((JSString query) => query.toDart == '(max-width: 1023.98px)'
              ? media
              : originalMatchMedia.callAsFunction(web.window, query))
          .toJS,
    );
    web.window.history.replaceState(null, '', '#/explorer');
    connection = DashboardBloc(
      client: MockClient((request) async => dashboardPageResponse(request)),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    binding.attachRootComponent(
      const DashboardApp(),
      attachTo: '#navigation-drawer-test',
    );
    await until(
      () => connection.state.connected && root.querySelector('main h1') != null,
      'Dashboard did not load',
    );
  });

  tearDown(() async {
    ui.closeDialogs();
    final panel = root.querySelector('#dashboard-navigation');
    if (panel?.tagName == 'DIALOG' && (panel as web.HTMLDialogElement).open)
      panel.close();
    binding.detachRootComponent();
    await connection.disconnect();
    await BlocScope.reset();
    root.textContent = '';
    web.window.setProperty('matchMedia'.toJS, originalMatchMedia);
  });
  tearDownAll(() {
    for (final asset in assets) asset.remove();
    root.remove();
    web.window.history.replaceState(null, '', originalHash);
  });

  test(
    'drawer opens modally, locks scrolling, and cancel restores focus',
    () async {
      await open();
      expect(
        web.window.getComputedStyle(web.document.body!).overflow,
        'hidden',
      );
      expect(
        web.window
            .getComputedStyle(root.querySelector('.main-content')!)
            .overflow,
        'hidden',
        reason: 'The drawer must lock the workspace scroller too',
      );
      (root.querySelector('.notice-button')! as web.HTMLElement).focus();
      expect(getDrawer().contains(web.document.activeElement), isTrue);
      final cancel = web.Event('cancel', web.EventInit(cancelable: true));
      getDrawer().dispatchEvent(cancel);
      expect(cancel.defaultPrevented, isTrue);
      await until(
        () => !getDrawer().open && web.document.activeElement == getTrigger(),
        'Cancel did not close and restore the drawer trigger',
      );
      expect(getTrigger().getAttribute('aria-expanded'), 'false');
    },
  );

  test(
    'external native close synchronizes state and keyboard activation stays open',
    () async {
      await open();
      final disclosure =
          root.querySelector('.nav-group__toggle')! as web.HTMLElement;
      disclosure
          .click(); // Keyboard/HTMLElement.click() has detail=0 and x=y=0.
      await until(
        () => disclosure.getAttribute('aria-expanded') == 'false',
        'Agents disclosure did not toggle',
      );
      expect(getDrawer().matches(':modal'), isTrue);
      getDrawer().close();
      await until(
        () =>
            getTrigger().getAttribute('aria-expanded') == 'false' &&
            web.document.activeElement == getTrigger(),
        'Native close left stale drawer state',
      );
    },
  );

  test('only a pointer click on the backdrop dismisses the drawer', () async {
    await open();
    final drawer = getDrawer();
    final bounds = drawer.getBoundingClientRect();
    drawer.dispatchEvent(
      web.MouseEvent(
        'click',
        web.MouseEventInit(
          bubbles: true,
          detail: 1,
          clientX: bounds.right.ceil() + 20,
          clientY: 30,
        ),
      ),
    );
    await until(
      () => !drawer.open && web.document.activeElement == getTrigger(),
      'Backdrop pointer click did not dismiss',
    );
  });

  test(
    'a session change closes navigation without restoring its trigger',
    () async {
      await open();
      await connection.disconnect();
      await until(
        () =>
            !getDrawer().open &&
            getTrigger().getAttribute('aria-expanded') == 'false',
        'Session change left a modal drawer',
      );
      expect(web.document.activeElement, isNot(getTrigger()));
    },
  );

  test(
    'breakpoint changes release modality and preserve search and scroll',
    () async {
      await open();
      final search =
          root.querySelector('.nav-search input')! as web.HTMLInputElement;
      search.value = 'Maya';
      search.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await until(
        () => root.querySelectorAll('#nav-agents a').length == 1,
        'Navigation search did not filter',
      );
      final scroller =
          root.querySelector('.sidebar__scroll')! as web.HTMLElement;
      scroller.style.height = '100px';
      scroller.style.flex = 'none';
      scroller.scrollTop = 120;
      final offset = scroller.scrollTop;
      expect(offset, greaterThan(0));
      setMobile(false);
      await until(
        () => root.querySelector('#dashboard-navigation')?.tagName == 'ASIDE',
        'Widening did not restore complementary navigation',
      );
      expect(root.querySelector('dialog:modal'), isNull);
      expect(getTrigger().getAttribute('aria-expanded'), 'false');
      expect(
        (root.querySelector('.nav-search input')! as web.HTMLInputElement)
            .value,
        'Maya',
      );
      expect(
        (root.querySelector('.sidebar__scroll')! as web.HTMLElement).scrollTop,
        closeTo(offset, 1),
      );
      final desktopScroller =
          root.querySelector('.sidebar__scroll')! as web.HTMLElement;
      desktopScroller.scrollTop = offset + 35;
      final returnOffset = desktopScroller.scrollTop;
      expect(returnOffset, greaterThan(0));
      setMobile(true);
      await until(
        () => root.querySelector('#dashboard-navigation')?.tagName == 'DIALOG',
        'Narrowing did not restore the mobile drawer',
      );
      expect(getDrawer().open, isFalse);
      await until(
        () =>
            ((root.querySelector('.sidebar__scroll')! as web.HTMLElement)
                            .scrollTop -
                        returnOffset)
                    .abs() <=
                1 &&
            (root.querySelector('.nav-search input')! as web.HTMLInputElement)
                    .value ==
                'Maya',
        'Narrowing did not preserve the desktop search and scroll position',
      );
      await open();
      expect(
        (root.querySelector('.sidebar__scroll')! as web.HTMLElement).scrollTop,
        closeTo(returnOffset, 1),
        reason: 'Opening the resized drawer must retain the reader position',
      );
      expect(
        (root.querySelector('.nav-search input')! as web.HTMLInputElement)
            .value,
        'Maya',
      );
    },
  );
}
