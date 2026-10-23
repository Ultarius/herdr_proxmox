@TestOn('browser')
library;

import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/ui.dart' as ui;

class _Controls extends ui.StatefulComponent {
  const _Controls(this.ready);
  final void Function(_ControlsState) ready;
  @override
  ui.State<_Controls> createState() => _ControlsState();
}

class _ControlsState extends ui.State<_Controls> {
  String value = 'Initial text',
      body = 'Initial multiline text',
      selected = 'a';
  Map<String, String> options = {'a': 'Alpha', 'b': 'Beta'};
  String secret = 'initial-secret';
  Future<String?>? parentResult, childResult;

  void dialogChanged() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    component.ready(this);
    ui.dialogChanges.addListener(dialogChanged);
  }

  @override
  void dispose() {
    ui.dialogChanges.removeListener(dialogChanged);
    super.dispose();
  }

  void replaceControls(String newValue, String newBody, String newSelection) {
    setState(() {
      value = newValue;
      body = newBody;
      selected = newSelection;
      secret = '';
    });
  }

  void replaceOptions() {
    setState(() {
      options = {'b': 'Updated Beta', 'c': 'Gamma'};
      selected = 'c';
    });
  }

  void openParent() {
    parentResult = ui.showDialog<String>(
      'Parent dialog',
      (close) => ui.el('div', [
        ui.field('Parent input', 'parent', (_) {}, id: 'parent-input'),
        ui.button('Open child', () {
          childResult = ui.showDialog<String>(
            'Child dialog',
            (childClose) => ui.el('div', [
              ui.field('Child input', 'child', (_) {}, id: 'child-input'),
              ui.button('Accept child', () => childClose('accepted')),
            ]),
          );
        }, classes: 'open-child'),
        ui.button(
          'Accept parent',
          () => close('accepted'),
          classes: 'accept-parent',
        ),
      ]),
    );
  }

  @override
  ui.Component build(ui.BuildContext context) => ui.el('div', [
    ui.field(
      'Controlled text',
      value,
      (v) => setState(() => value = v),
      id: 'controlled-text',
    ),
    ui.textarea(
      'Controlled multiline',
      body,
      (v) => setState(() => body = v),
      id: 'controlled-multiline',
    ),
    ui.select(
      'Controlled choice',
      selected,
      options,
      (v) => setState(() => selected = v),
    ),
    ui.field(
      'Credential',
      secret,
      (v) => setState(() => secret = v),
      id: 'controlled-secret',
      password: true,
    ),
    ui.button('Open parent', openParent, classes: 'open-parent'),
    ui.el(
      'details',
      [
        ui.el('summary', [ui.text('Polling disclosure')]),
        ui.text(value),
      ],
      attributes: {'id': 'polling-disclosure'},
    ),
    ui.el(
      'details',
      [
        ui.el('summary', [ui.text('Initially open disclosure')]),
        ui.text(body),
      ],
      attributes: {'id': 'initially-open-disclosure', 'open': ''},
    ),
    ui.buildDialogs(),
  ]);
}

Future<void> settle() async =>
    Future<void>.delayed(const Duration(milliseconds: 20));
Future<void> until(bool Function() ready, String reason) async {
  final deadline = DateTime.now().add(const Duration(seconds: 3));
  while (!ready() && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(ready(), isTrue, reason: reason);
}

web.HTMLElement element(String selector) =>
    web.document.querySelector('#ui-dom-test $selector') as web.HTMLElement;
web.HTMLInputElement input(String id) =>
    web.document.getElementById(id) as web.HTMLInputElement;
void key(web.HTMLElement node, String name, {bool shift = false}) {
  node.dispatchEvent(
    web.KeyboardEvent(
      'keydown',
      web.KeyboardEventInit(
        key: name,
        shiftKey: shift,
        bubbles: true,
        cancelable: true,
      ),
    ),
  );
}

void cancel(web.Element node) {
  node.dispatchEvent(web.Event('cancel', web.EventInit(cancelable: true)));
}

({web.HTMLElement list, List<web.HTMLButtonElement> tabs, List<int> activated})
tabFixture({bool vertical = false, bool rtl = false, int count = 5}) {
  final list = web.HTMLDivElement()
    ..setAttribute('role', 'tablist')
    ..setAttribute('aria-orientation', vertical ? 'vertical' : 'horizontal')
    ..style.direction = rtl ? 'rtl' : 'ltr';
  final tabs = <web.HTMLButtonElement>[];
  final activated = <int>[];
  for (var index = 0; index < count; index++) {
    final tab = web.HTMLButtonElement()
      ..textContent = 'Tab $index'
      ..setAttribute('role', 'tab')
      ..setAttribute('aria-selected', '${index == 0}')
      ..tabIndex = index == 0 ? 0 : -1;
    tab.addEventListener(
      'click',
      ((web.Event _) {
        activated.add(index);
        for (final candidate in tabs) {
          candidate.setAttribute('aria-selected', '${candidate == tab}');
          candidate.tabIndex = candidate == tab ? 0 : -1;
        }
      }).toJS,
    );
    tabs.add(tab);
    list.appendChild(tab);
  }
  list.addEventListener(
    'keydown',
    ((web.Event event) => ui.tabListKeyDown(event)).toJS,
  );
  web.document.getElementById('ui-dom-test')!.appendChild(list);
  return (list: list, tabs: tabs, activated: activated);
}

void main() {
  late ui.ClientAppBinding binding;
  late _ControlsState state;
  setUpAll(() {
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'ui-dom-test');
    binding = ui.ClientAppBinding();
  });
  setUp(() async {
    binding.attachRootComponent(
      _Controls((value) => state = value),
      attachTo: '#ui-dom-test',
    );
    await settle();
  });
  tearDown(() async {
    ui.closeDialogs();
    ui.messages.value = null;
    await settle();
    binding.detachRootComponent();
    web.document.getElementById('ui-dom-test')!.textContent = '';
  });

  test('background updates preserve opened and closed disclosures', () async {
    final opened = element('#polling-disclosure') as web.HTMLDetailsElement;
    final closed =
        element('#initially-open-disclosure') as web.HTMLDetailsElement;
    (opened.querySelector('summary') as web.HTMLElement).click();
    (closed.querySelector('summary') as web.HTMLElement).click();
    await settle();
    expect(opened.open, isTrue);
    expect(closed.open, isFalse);
    for (var update = 0; update < 3; update++) {
      state.replaceControls('Server update $update', 'Details $update', 'a');
      await settle();
      expect(element('#polling-disclosure'), same(opened));
      expect(opened.open, isTrue);
      expect(closed.open, isFalse);
      expect(opened.textContent, contains('Server update $update'));
    }
  });

  test(
    'an update during a click does not race the native toggle event',
    () async {
      final details = element('#polling-disclosure') as web.HTMLDetailsElement;
      for (final open in [true, false, true]) {
        (details.querySelector('summary') as web.HTMLElement).click();
        state.replaceControls('Refreshed during click', 'New details', 'a');
        await settle();
        expect(details.open, open);
      }
    },
  );

  test(
    'dirty input, textarea and select reflect programmatic changes and clears',
    () async {
      final text = input('controlled-text');
      final multiline =
          web.document.getElementById('controlled-multiline')
              as web.HTMLTextAreaElement;
      final choice = element('select') as web.HTMLSelectElement;
      final credential = input('controlled-secret');
      expect(credential.getAttribute('autocomplete'), 'current-password');
      expect(text.value, 'Initial text');
      expect(multiline.value, 'Initial multiline text');
      expect(choice.value, 'a');
      text.value = 'User text';
      text.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      multiline.value = 'User multiline\nsecond line';
      multiline.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      choice.value = 'b';
      choice.dispatchEvent(web.Event('change', web.EventInit(bubbles: true)));
      credential.value = 'user-secret';
      credential.dispatchEvent(
        web.Event('input', web.EventInit(bubbles: true)),
      );
      await settle();
      expect(state.value, 'User text');
      expect(state.body, 'User multiline\nsecond line');
      expect(state.selected, 'b');
      state.replaceControls('Server text', 'Server multiline\nnew line', 'a');
      await settle();
      expect(text.value, 'Server text');
      expect(multiline.value, 'Server multiline\nnew line');
      expect(choice.value, 'a');
      expect(credential.value, '');
      state.replaceControls('', '', 'b');
      await settle();
      expect(text.value, '');
      expect(multiline.value, '');
      expect(choice.value, 'b');
      expect(web.document.getElementById('controlled-text'), same(text));
      expect(
        web.document.getElementById('controlled-multiline'),
        same(multiline),
      );
    },
  );

  test(
    'acknowledging typed text retains its caret and generated select identity',
    () async {
      final text = input('controlled-text');
      final multiline =
          web.document.getElementById('controlled-multiline')
              as web.HTMLTextAreaElement;
      final choice = element('select') as web.HTMLSelectElement;
      final selectId = choice.id;
      text.value = 'typed input';
      text.focus();
      text.setSelectionRange(3, 3);
      text.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      expect(text.selectionStart, 3);
      expect(web.document.activeElement, same(text));
      multiline.value = 'typed multiline';
      multiline.focus();
      multiline.setSelectionRange(4, 4);
      multiline.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
      await settle();
      expect(multiline.selectionStart, 4);
      expect(web.document.activeElement, same(multiline));
      state.replaceOptions();
      await settle();
      expect((element('select') as web.HTMLSelectElement).id, selectId);
      expect(choice.value, 'c');
      expect(choice.options.length, 2);
    },
  );

  test(
    'nested dialog Escape cancels only the top and restores focus to its opener',
    () async {
      final opener = element('.open-parent');
      opener.focus();
      opener.click();
      await settle();
      expect(
        web.document.querySelectorAll('#ui-dom-test [role=dialog]').length,
        1,
      );
      final childOpener = element('.open-child');
      childOpener.focus();
      childOpener.click();
      await settle();
      final parent = element('[aria-label="Parent dialog"]');
      final child = element('[aria-label="Child dialog"]');
      expect(child.tagName, 'DIALOG');
      expect(child.matches(':modal'), isTrue);
      expect(parent.hasAttribute('inert'), isFalse);
      expect(parent.matches(':modal'), isTrue);
      expect(child.getAttribute('aria-modal'), 'true');
      input('parent-input').focus();
      expect(child.contains(web.document.activeElement), isTrue);
      cancel(child);
      expect(
        web.document.activeElement,
        same(childOpener),
        reason:
            'The browser restores lower-modal focus before a Jaspr rebuild.',
      );
      await settle();
      expect(await state.childResult, isNull);
      expect(
        web.document.querySelectorAll('#ui-dom-test [role=dialog]').length,
        1,
      );
      expect(web.document.activeElement, same(childOpener));
      var parentClosed = false;
      state.parentResult!.then((_) => parentClosed = true);
      await settle();
      expect(parentClosed, false);
      cancel(parent);
      await settle();
      expect(await state.parentResult, isNull);
      expect(
        web.document.querySelectorAll('#ui-dom-test [role=dialog]').length,
        0,
      );
      expect(web.document.activeElement, same(opener));
    },
  );

  test(
    'native modal excludes background focus and accepts all control types',
    () async {
      element('.open-parent').click();
      await until(
        () =>
            web.document.querySelector('[aria-label="Parent dialog"]:modal') !=
            null,
        'Parent is presented in the native modal top layer',
      );
      final panel = element('[aria-label="Parent dialog"]');
      final focused = web.document.activeElement;
      expect(panel.contains(focused), isTrue);
      expect(panel.hasAttribute('tabindex'), isFalse);
      final background = input('controlled-text');
      background.focus();
      expect(web.document.activeElement, same(focused));
      for (final control in <web.HTMLElement>[
        web.HTMLAnchorElement()..href = '#read-more',
        web.HTMLInputElement(),
        web.HTMLSelectElement()
          ..appendChild(web.HTMLOptionElement()..textContent = 'An option'),
        web.HTMLDetailsElement()..appendChild(
          web.document.createElement('summary')..textContent = 'More',
        ),
        web.HTMLDivElement()..tabIndex = 2,
      ]) {
        panel.appendChild(control);
        final target = control.tagName == 'DETAILS'
            ? control.firstElementChild! as web.HTMLElement
            : control;
        target.focus();
        expect(web.document.activeElement, same(target));
        background.focus();
        expect(web.document.activeElement, same(target));
      }
    },
  );

  test('native cancel respects the current busy-form guard', () async {
    var allowed = false;
    final result = ui.showDialog<String>(
      'Guarded dialog',
      (close) => ui.button('Save guarded', () => close('saved')),
      canDismiss: () => allowed,
    );
    await until(
      () =>
          web.document.querySelector('[aria-label="Guarded dialog"]:modal') !=
          null,
      'Guarded dialog opens',
    );
    final dialog =
        element('[aria-label="Guarded dialog"]') as web.HTMLDialogElement;
    final cancel = web.Event('cancel', web.EventInit(cancelable: true));
    dialog.dispatchEvent(cancel);
    key(dialog, 'Escape');
    await settle();
    expect(cancel.defaultPrevented, isTrue);
    expect(dialog.open, isTrue);
    expect(dialog.matches(':modal'), isTrue);
    expect(
      (dialog.querySelector('.dialog__header button') as web.HTMLButtonElement)
          .disabled,
      isTrue,
    );
    allowed = true;
    ui.refreshDialogs();
    await until(
      () =>
          !(dialog.querySelector('.dialog__header button')
                  as web.HTMLButtonElement)
              .disabled,
      'Dismiss button reflects the updated guard',
    );
    (dialog.querySelector('.dialog__header button') as web.HTMLButtonElement)
        .click();
    expect(await result, isNull);
    await until(() => !dialog.open, 'Closing removes native modality');
  });

  test(
    'non-dismissible dialogs ignore cancel but allow an explicit result',
    () async {
      final result = ui.showDialog<String>(
        'Required dialog',
        (close) => ui.button('Continue required', () => close('continued')),
        dismissible: false,
      );
      await until(
        () =>
            web.document.querySelector(
              '[aria-label="Required dialog"]:modal',
            ) !=
            null,
        'Required dialog opens',
      );
      final dialog =
          element('[aria-label="Required dialog"]') as web.HTMLDialogElement;
      expect(dialog.querySelector('.dialog__header button'), isNull);
      dialog.dispatchEvent(
        web.Event('cancel', web.EventInit(cancelable: true)),
      );
      key(dialog, 'Escape');
      expect(dialog.open, isTrue);
      (dialog.querySelector('button') as web.HTMLButtonElement).click();
      expect(await result, 'continued');
      expect(dialog.open, isFalse);
    },
  );

  test(
    'native initial focus honors autofocus without a dialog tabindex',
    () async {
      final result = ui.showDialog<String>(
        'Autofocus dialog',
        (_) => ui.el(
          'input',
          [],
          attributes: {
            'id': 'preferred-modal-input',
            'aria-label': 'Preferred input',
            'autofocus': '',
          },
        ),
      );
      await until(
        () =>
            web.document.querySelector(
              '[aria-label="Autofocus dialog"]:modal',
            ) !=
            null,
        'Autofocus modal opens',
      );
      final dialog = element('[aria-label="Autofocus dialog"]');
      expect(dialog.hasAttribute('tabindex'), isFalse);
      expect(web.document.activeElement, same(input('preferred-modal-input')));
      ui.refreshDialogs();
      await settle();
      expect(dialog.matches(':modal'), isTrue);
      expect(web.document.activeElement, same(input('preferred-modal-input')));
      cancel(dialog);
      expect(await result, isNull);
    },
  );

  test('Escape dismissal has one native cancel path', () async {
    var completed = false;
    final result = ui.showDialog<String>(
      'Cancel path',
      (_) => ui.text('Dialog body'),
    );
    result.then((_) => completed = true);
    await until(
      () =>
          web.document.querySelector('[aria-label="Cancel path"]:modal') !=
          null,
      'Cancel-path modal opens',
    );
    final dialog = element('[aria-label="Cancel path"]');
    final escape = web.KeyboardEvent(
      'keydown',
      web.KeyboardEventInit(key: 'Escape', bubbles: true, cancelable: true),
    );
    dialog.dispatchEvent(escape);
    await settle();
    expect(escape.defaultPrevented, isFalse);
    expect(
      completed,
      isFalse,
      reason: 'Untrusted keydown must not create a second dismissal path.',
    );
    expect(dialog.matches(':modal'), isTrue);
    cancel(dialog);
    expect(await result, isNull);
  });

  test(
    'guarded native close survives an intervening rebuild and remains pending',
    () async {
      var allowed = false;
      var completed = false;
      final result = ui.showDialog<String>(
        'Native close guard',
        (_) => ui.field(
          'Guarded draft',
          'Draft',
          (_) {},
          id: 'guarded-native-draft',
        ),
        canDismiss: () => allowed,
      );
      result.then((_) => completed = true);
      await until(
        () =>
            web.document.querySelector(
              '[aria-label="Native close guard"]:modal',
            ) !=
            null,
        'Guarded modal opens',
      );
      final dialog =
          element('[aria-label="Native close guard"]') as web.HTMLDialogElement;
      dialog.close();
      ui.refreshDialogs();
      await until(
        () => dialog.matches(':modal'),
        'Guard restores native modality after close and rebuild',
      );
      expect(completed, isFalse);
      expect(dialog.open, isTrue);
      expect(element('[aria-label="Native close guard"]'), same(dialog));
      // Another caller can reopen the closed node nonmodally before its queued
      // close event runs. The guard must reconcile that state without throwing.
      dialog.close();
      dialog.show();
      ui.refreshDialogs();
      await until(
        () => dialog.matches(':modal'),
        'Guard promotes an external nonmodal reopen',
      );
      expect(completed, isFalse);
      allowed = true;
      dialog.close();
      expect(await result.timeout(const Duration(seconds: 2)), isNull);
      await until(
        () =>
            web.document.querySelector('[aria-label="Native close guard"]') ==
            null,
        'Allowed native close removes registry and view',
      );
    },
  );

  test(
    'dialog presentation retries a temporarily missing committed element',
    () async {
      final original = web.document.getProperty<JSFunction>(
        'getElementById'.toJS,
      );
      var lookups = 0;
      web.document.setProperty(
        'getElementById'.toJS,
        ((JSObject document, JSString id) {
          if (id.toDart.startsWith('herdr-dialog-') && ++lookups < 3)
            return null;
          return original.callAsFunction(document, id);
        }).toJSCaptureThis,
      );
      addTearDown(
        () => web.document.setProperty('getElementById'.toJS, original),
      );
      final result = ui.showDialog<String>(
        'Delayed DOM',
        (_) => ui.text('Ready'),
      );
      await until(
        () =>
            web.document.querySelector('[aria-label="Delayed DOM"]:modal') !=
            null,
        'Presentation retries until the native node is available',
      );
      expect(lookups, greaterThanOrEqualTo(3));
      cancel(element('[aria-label="Delayed DOM"]'));
      expect(await result, isNull);
    },
  );

  test(
    'dialog presentation retries a transient native showModal failure',
    () async {
      final prototype = globalContext
          .getProperty<JSObject>('HTMLDialogElement'.toJS)
          .getProperty<JSObject>('prototype'.toJS);
      final original = prototype.getProperty<JSFunction>('showModal'.toJS);
      var attempts = 0;
      prototype.setProperty(
        'showModal'.toJS,
        ((JSObject dialog) {
          if (++attempts == 1)
            throw StateError('Transient native presentation failure');
          original.callAsFunction(dialog);
        }).toJSCaptureThis,
      );
      addTearDown(() => prototype.setProperty('showModal'.toJS, original));
      final result = ui.showDialog<String>(
        'Delayed native presentation',
        (_) => ui.text('Ready'),
      );
      await until(
        () =>
            web.document.querySelector(
              '[aria-label="Delayed native presentation"]:modal',
            ) !=
            null,
        'Native presentation recovers on retry',
      );
      expect(attempts, 2);
      cancel(element('[aria-label="Delayed native presentation"]'));
      expect(await result, isNull);
    },
  );

  for (final missingDom in [true, false]) {
    test(
      'permanent ${missingDom ? 'missing DOM' : 'native presentation failure'} cancels visibly without a pending dialog',
      () async {
        final target = missingDom
            ? web.document
            : globalContext
                  .getProperty<JSObject>('HTMLDialogElement'.toJS)
                  .getProperty<JSObject>('prototype'.toJS);
        final method = (missingDom ? 'getElementById' : 'showModal').toJS;
        final original = target.getProperty<JSFunction>(method);
        var attempts = 0;
        if (missingDom) {
          target.setProperty(
            method,
            ((JSObject document, JSString id) {
              if (id.toDart.startsWith('herdr-dialog-')) {
                attempts++;
                return null;
              }
              return original.callAsFunction(document, id);
            }).toJSCaptureThis,
          );
        } else {
          void rejectPresentation(JSObject _) {
            attempts++;
            throw StateError('Persistent native presentation failure');
          }

          target.setProperty(method, rejectPresentation.toJSCaptureThis);
        }
        addTearDown(() => target.setProperty(method, original));
        final result = ui.showDialog<String>(
          'Unavailable presentation',
          (_) => ui.text('Not available'),
        );
        expect(await result.timeout(const Duration(seconds: 2)), isNull);
        await until(
          () =>
              web.document.querySelector(
                '[aria-label="Unavailable presentation"]',
              ) ==
              null,
          'Failed presentation removes its registered view',
        );
        expect(attempts, 5);
        expect(ui.messages.value, contains('Unable to open the dialog'));
        expect(web.document.querySelector('.dialog:modal'), isNull);
      },
    );
  }

  test('closing restores a connected focusable SVG opener safely', () async {
    final svg =
        web.document.createElementNS('http://www.w3.org/2000/svg', 'svg')
            as web.SVGElement;
    svg.setAttribute('tabindex', '0');
    svg.setAttribute('width', '40');
    svg.setAttribute('height', '40');
    web.document.getElementById('ui-dom-test')!.appendChild(svg);
    svg.focus();
    expect(web.document.activeElement, same(svg));
    final result = ui.showDialog<String>('SVG opener', (_) => ui.text('Ready'));
    await until(
      () =>
          web.document.querySelector('[aria-label="SVG opener"]:modal') != null,
      'SVG-launched modal opens',
    );
    cancel(element('[aria-label="SVG opener"]'));
    expect(await result, isNull);
    await until(
      () => web.document.activeElement == svg,
      'Focus returns to the SVG element',
    );
  });

  test(
    'dialog refresh preserves the native element, text caret and heading name',
    () async {
      final label = ui.DialogLabel(
        'changing-dialog-heading',
        fallback: 'Initial heading',
      );
      var heading = false;
      final result = ui.showDialog<String>(
        '',
        (close) => ui.el('div', [
          if (heading)
            ui.el(
              'h2',
              [ui.text('Current heading')],
              attributes: {'id': label.headingId},
            ),
          ui.field('Draft', 'Draft text', (_) {}, id: 'modal-draft'),
        ]),
        label: label,
      );
      await until(
        () => web.document.querySelector('.dialog:modal') != null,
        'Named modal opens',
      );
      final dialog = element('.dialog');
      final draft = input('modal-draft')
        ..focus()
        ..setSelectionRange(3, 3);
      expect(dialog.getAttribute('aria-labelledby'), label.fallbackId);
      heading = true;
      label.headings.value = 1;
      await until(
        () => dialog.getAttribute('aria-labelledby') == label.headingId,
        'Mounted heading becomes the accessible dialog name',
      );
      expect(element('.dialog'), same(dialog));
      expect(dialog.matches(':modal'), isTrue);
      expect(web.document.activeElement, same(draft));
      expect(draft.selectionStart, 3);
      ui.closeDialogs();
      expect(await result, isNull);
    },
  );

  test(
    'closeDialogs cancels every pending result and removes all overlays',
    () async {
      final opener = element('.open-parent');
      opener.focus();
      opener.click();
      await settle();
      final childOpener = element('.open-child');
      childOpener.focus();
      childOpener.click();
      await settle();
      ui.closeDialogs();
      await settle();
      expect(await state.parentResult, isNull);
      expect(await state.childResult, isNull);
      expect(
        web.document.querySelectorAll('#ui-dom-test [role=dialog]').length,
        0,
      );
      expect(web.document.activeElement, same(opener));
      ui.closeDialogs();
      await settle();
      expect(
        web.document.querySelectorAll('#ui-dom-test .dialog-layer').length,
        0,
      );
    },
  );

  test(
    'session cleanup closes a busy guarded modal and releases native modality',
    () async {
      final result = ui.showDialog<String>(
        'Busy session dialog',
        (_) => ui.text('Waiting for server response'),
        canDismiss: () => false,
        dismissible: false,
      );
      await until(
        () =>
            web.document.querySelector(
              '[aria-label="Busy session dialog"]:modal',
            ) !=
            null,
        'Busy modal opens',
      );
      ui.closeDialogs();
      expect(await result, isNull);
      await until(
        () => web.document.querySelector('.dialog') == null,
        'Session cleanup removes busy modal',
      );
      final background = input('controlled-text')..focus();
      expect(web.document.activeElement, same(background));
      expect(web.document.querySelector('.dialog:modal'), isNull);
    },
  );

  test('tab keys wrap and skip disabled, hidden and inert tabs', () {
    final fixture = tabFixture(count: 7);
    fixture.tabs[1].disabled = true;
    fixture.tabs[2].setAttribute('aria-disabled', 'true');
    fixture.tabs[3].setAttribute('hidden', '');
    fixture.tabs[4].style.visibility = 'hidden';
    fixture.tabs[5].setAttribute('inert', '');
    fixture.tabs.first.focus();
    key(fixture.tabs.first, 'ArrowRight');
    expect(web.document.activeElement, same(fixture.tabs.last));
    expect(fixture.activated, [6]);
    expect(fixture.tabs.last.getAttribute('aria-selected'), 'true');
    key(fixture.tabs.last, 'ArrowRight');
    expect(web.document.activeElement, same(fixture.tabs.first));
    key(fixture.tabs.first, 'ArrowLeft');
    expect(web.document.activeElement, same(fixture.tabs.last));
    key(fixture.tabs.last, 'Home');
    expect(web.document.activeElement, same(fixture.tabs.first));
    key(fixture.tabs.first, 'End');
    expect(web.document.activeElement, same(fixture.tabs.last));
    expect(fixture.activated, [6, 0, 6, 0, 6]);
  });

  test('tabs honor orientation and RTL without consuming unrelated keys', () {
    final rtl = tabFixture(rtl: true, count: 3);
    rtl.tabs.first.focus();
    key(rtl.tabs.first, 'ArrowLeft');
    expect(web.document.activeElement, same(rtl.tabs[1]));
    key(rtl.tabs[1], 'ArrowRight');
    expect(web.document.activeElement, same(rtl.tabs.first));
    final vertical = tabFixture(vertical: true, rtl: true, count: 3);
    vertical.tabs.first.focus();
    key(vertical.tabs.first, 'ArrowUp');
    expect(web.document.activeElement, same(vertical.tabs.last));
    key(vertical.tabs.last, 'ArrowDown');
    expect(web.document.activeElement, same(vertical.tabs.first));
    final unrelated = web.KeyboardEvent(
      'keydown',
      web.KeyboardEventInit(key: 'ArrowRight', bubbles: true, cancelable: true),
    );
    vertical.tabs.first.dispatchEvent(unrelated);
    expect(unrelated.defaultPrevented, isFalse);
    expect(vertical.activated, [2, 0]);
  });

  test(
    'tabs ignore modified or composing input and leave nested lists independent',
    () {
      final outer = tabFixture(count: 3);
      final inner = tabFixture(count: 2);
      outer.list.appendChild(inner.list);
      inner.tabs.first.focus();
      key(inner.tabs.first, 'ArrowRight');
      expect(inner.activated, [1]);
      expect(outer.activated, isEmpty);
      for (final init in [
        web.KeyboardEventInit(
          key: 'Home',
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        ),
        web.KeyboardEventInit(
          key: 'Home',
          altKey: true,
          bubbles: true,
          cancelable: true,
        ),
        web.KeyboardEventInit(
          key: 'Home',
          metaKey: true,
          bubbles: true,
          cancelable: true,
        ),
        web.KeyboardEventInit(
          key: 'Home',
          shiftKey: true,
          bubbles: true,
          cancelable: true,
        ),
        web.KeyboardEventInit(
          key: 'Home',
          isComposing: true,
          bubbles: true,
          cancelable: true,
        ),
      ]) {
        final event = web.KeyboardEvent('keydown', init);
        inner.tabs.last.dispatchEvent(event);
        expect(event.defaultPrevented, isFalse);
        expect(web.document.activeElement, same(inner.tabs.last));
      }
      expect(inner.activated, [1]);
      expect(outer.activated, isEmpty);
    },
  );

  test('tabs ignore consumed keys and a list target without an active tab', () {
    final fixture = tabFixture(count: 2);
    fixture.tabs.first.focus();
    final consumed = web.KeyboardEvent(
      'keydown',
      web.KeyboardEventInit(key: 'End', bubbles: true, cancelable: true),
    );
    consumed.preventDefault();
    fixture.tabs.first.dispatchEvent(consumed);
    expect(fixture.activated, isEmpty);
    expect(web.document.activeElement, same(fixture.tabs.first));
    fixture.list.tabIndex = 0;
    fixture.list.focus();
    final fromList = web.KeyboardEvent(
      'keydown',
      web.KeyboardEventInit(key: 'Home', bubbles: true, cancelable: true),
    );
    fixture.list.dispatchEvent(fromList);
    expect(fromList.defaultPrevented, isFalse);
    expect(fixture.activated, isEmpty);
    expect(web.document.activeElement, same(fixture.list));
    final unrelated = web.HTMLInputElement();
    fixture.list.appendChild(unrelated);
    unrelated.focus();
    key(unrelated, 'ArrowRight');
    expect(fixture.activated, isEmpty);
    expect(web.document.activeElement, same(unrelated));
    final empty = tabFixture(count: 0);
    empty.list.tabIndex = 0;
    empty.list.focus();
    key(empty.list, 'End');
    expect(empty.activated, isEmpty);
    expect(web.document.activeElement, same(empty.list));
  });
}
