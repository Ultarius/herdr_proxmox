@TestOn('browser')
library;

import 'package:jaspr/client.dart';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/pages/ventures_page.dart';

// The venture form normally opens in a dialog, which the page tests cannot
// render. Mounting it directly catches build-time errors such as a `late`
// field that initState never assigns.

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 50));

Iterable<web.HTMLElement> nodes(String selector) sync* {
  final values = web.document.querySelectorAll('#venture-form-test $selector');
  for (var i = 0; i < values.length; i++) {
    yield values.item(i) as web.HTMLElement;
  }
}

Future<void> input(String label, String value) async {
  final node = nodes('label').firstWhere((node) => node.querySelector('span')?.textContent == label);
  final control = node.querySelector('input') as web.HTMLInputElement;
  control.value = value;
  control.dispatchEvent(web.Event('input', web.EventInit(bubbles: true)));
  await settle();
}

Future<void> click(String label) async {
  nodes('button').firstWhere((item) => item.textContent!.trim() == label).click();
  await settle();
}

void main() {
  late ClientAppBinding binding;
  late List<Map<String, dynamic>> saved;

  Future<void> mount({Map<String, dynamic>? initial}) async {
    web.document.body!.appendChild(web.HTMLDivElement()..id = 'venture-form-test');
    binding = ClientAppBinding();
    binding.attachRootComponent(
      VentureForm(
        organizations: const [
          {'id': 'org', 'name': 'Product organization'},
        ],
        groups: const [],
        choices: (_) => const {},
        initial: initial,
        fixedOrganization: initial == null ? null : 'org',
        onSave: saved.add,
        onCancel: () {},
      ),
      attachTo: '#venture-form-test',
    );
    await settle();
  }

  setUp(() => saved = []);

  tearDown(() {
    binding.detachRootComponent();
    web.document.getElementById('venture-form-test')!.remove();
  });

  String page() => web.document.querySelector('#venture-form-test')!.textContent!;

  test('a new venture form builds and saves the default meeting cadence', () async {
    await mount();
    expect(page(), isNot(contains('Error on building component')));
    expect(page(), contains('Meeting cadence'));
    await input('Name', 'Settlement Desk');
    await click('Create venture');
    expect(saved, hasLength(1));
    final simulation = (saved.single['configuration'] as Map)['simulation'] as Map;
    expect(simulation['meeting_cadence'], 'news_only');
    expect(simulation['daily_ticks'], isFalse);
  });

  test('editing keeps the venture\'s stored meeting cadence', () async {
    await mount(initial: {
      'name': 'Settlement Desk',
      'configuration': {
        'simulation': {'meeting_cadence': 'every_day', 'daily_ticks': true, 'tick_hour_utc': 7},
      },
    });
    expect(page(), isNot(contains('Error on building component')));
    await click('Save configuration');
    final simulation = (saved.single['configuration'] as Map)['simulation'] as Map;
    expect(simulation['meeting_cadence'], 'every_day');
    expect(simulation['tick_hour_utc'], 7);
  });
}
