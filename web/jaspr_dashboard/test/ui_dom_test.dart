@TestOn('browser')
library;

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
      expect(parent.hasAttribute('inert'), true);
      expect(child.getAttribute('aria-modal'), 'true');
      key(input('child-input'), 'Escape');
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
      key(input('parent-input'), 'Escape');
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
    'Tab stays inside the top dialog, including the initially focused panel',
    () async {
      element('.open-parent').click();
      await settle();
      final panel = element('[aria-label="Parent dialog"]');
      final close = element('.dialog__header button');
      final last = element('.accept-parent');
      panel.focus();
      key(panel, 'Tab');
      expect(web.document.activeElement, same(close));
      close.focus();
      key(close, 'Tab', shift: true);
      expect(web.document.activeElement, same(last));
      last.focus();
      key(last, 'Tab');
      expect(web.document.activeElement, same(close));
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
}
