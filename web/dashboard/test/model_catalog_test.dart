import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/model_catalog.dart';

final catalog = ModelCatalog.fromJson({
  'runtimes': {
    'codex': {
      'models': [
        {'id': 'gpt-6-astra', 'name': 'GPT-6 Astra'},
        {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1 Sol'},
        {'id': 'gpt-6-luna', 'name': 'GPT-6 Luna'},
      ],
      'efforts': [
        {'id': '', 'label': 'Model default'},
        {'id': 'minimal', 'label': 'Minimal'},
        {'id': 'high', 'label': 'High'},
        {'id': 'xhigh', 'label': 'Extra high'},
      ],
      'source': 'curated',
    },
    'opencode': {
      'models': [
        {
          'provider': 'anthropic',
          'id': 'claude-opus-5-5',
          'name': 'Claude Opus 5.5',
          'reasoning': ['high', 'max'],
        },
        {'provider': 'openai', 'id': 'gpt-6-astra', 'name': 'GPT-6 Astra'},
      ],
      'efforts': [
        {'id': '', 'label': 'Model default'},
        {'id': 'high', 'label': 'High'},
      ],
      'supports_variants': true,
      'source': 'opencode models --verbose',
    },
  },
});

void main() {
  Future<void> open(
    WidgetTester tester, {
    required String? runtime,
    required TextEditingController provider,
    required TextEditingController model,
    required TextEditingController reasoning,
    RuntimeCatalog? options,
    Size size = const Size(1000, 1400),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Form(
            child: ModelFields(
              runtime: runtime,
              catalog: options ?? catalog[runtime],
              provider: provider,
              model: model,
              reasoning: reasoning,
              project: '/home/herdr/projects',
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('Go provider selects Go models independently of Zen', (
    tester,
  ) async {
    final provider = TextEditingController(text: 'opencode-go');
    final model = TextEditingController(text: 'same-model');
    final reasoning = TextEditingController();
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    await open(
      tester,
      runtime: 'opencode',
      provider: provider,
      model: model,
      reasoning: reasoning,
      options: const RuntimeCatalog(
        models: [
          CatalogModel(
            provider: 'opencode',
            id: 'same-model',
            name: 'Zen model',
          ),
          CatalogModel(
            provider: 'opencode-go',
            id: 'same-model',
            name: 'Go model',
          ),
        ],
      ),
    );
    expect(find.text('OpenCode Go (opencode-go)'), findsOneWidget);
    expect(find.text('Go model'), findsOneWidget);
    expect(find.text('Zen model'), findsNothing);
    expect(provider.text, 'opencode-go');
  });

  testWidgets('model and reasoning dropdowns offer the catalog', (
    tester,
  ) async {
    final provider = TextEditingController();
    final model = TextEditingController();
    final reasoning = TextEditingController();
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    await open(
      tester,
      runtime: 'codex',
      provider: provider,
      model: model,
      reasoning: reasoning,
    );
    expect(find.text('CLI default'), findsOneWidget);
    expect(find.text('Model default'), findsOneWidget);

    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Model',
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('GPT-6 Astra'), findsOneWidget);
    expect(find.text('GPT-6.1 Sol'), findsOneWidget);
    expect(find.text('Custom value'), findsOneWidget);
    await tester.tap(find.text('GPT-6.1 Sol').last);
    await tester.pumpAndSettle();
    expect(model.text, 'gpt-6.1-sol');

    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Reasoning effort',
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Extra high'), findsOneWidget);
    await tester.tap(find.text('Extra high').last);
    await tester.pumpAndSettle();
    expect(reasoning.text, 'xhigh');
    expect(tester.takeException(), isNull);
  });

  testWidgets('a stored model outside the catalog stays selectable', (
    tester,
  ) async {
    final provider = TextEditingController();
    final model = TextEditingController(text: 'gpt-7-preview');
    final reasoning = TextEditingController();
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    await open(
      tester,
      runtime: 'codex',
      provider: provider,
      model: model,
      reasoning: reasoning,
    );
    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Model',
      ),
    );
    await tester.pumpAndSettle();
    // Shown as the current selection and as an open menu item.
    expect(find.text('gpt-7-preview (not in catalog)'), findsWidgets);
    await tester.tap(find.text('GPT-6 Astra').last);
    await tester.pumpAndSettle();
    expect(model.text, 'gpt-6-astra');
  });

  testWidgets('the OpenCode provider narrows the model list', (tester) async {
    final provider = TextEditingController();
    final model = TextEditingController();
    final reasoning = TextEditingController();
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    await open(
      tester,
      runtime: 'opencode',
      provider: provider,
      model: model,
      reasoning: reasoning,
    );
    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Provider',
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('anthropic'), findsOneWidget);
    expect(find.text('openai'), findsOneWidget);
    await tester.tap(find.text('anthropic').last);
    await tester.pumpAndSettle();
    expect(provider.text, 'anthropic');

    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Model',
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Claude Opus 5.5'), findsOneWidget);
    expect(find.text('GPT-6 Astra'), findsNothing);
    await tester.tap(find.text('Claude Opus 5.5').last);
    await tester.pumpAndSettle();
    expect(model.text, 'claude-opus-5-5');

    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Reasoning variant',
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('high').last);
    await tester.pumpAndSettle();
    expect(reasoning.text, 'high');
    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Provider',
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('openai').last);
    await tester.pumpAndSettle();
    expect(model.text, isEmpty);
    expect(reasoning.text, isEmpty);
  });

  testWidgets('an OpenCode model without a provider is rejected', (
    tester,
  ) async {
    final provider = TextEditingController();
    final model = TextEditingController(text: 'gpt-6-astra');
    final reasoning = TextEditingController();
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    await open(
      tester,
      runtime: 'opencode',
      provider: provider,
      model: model,
      reasoning: reasoning,
    );
    final form = tester.state<FormState>(find.byType(Form));
    expect(form.validate(), isFalse);
    await tester.pumpAndSettle();
    expect(find.text('Choose a provider for this model.'), findsOneWidget);
  });

  testWidgets('manual entry and CLI defaults clear the stored settings', (
    tester,
  ) async {
    final provider = TextEditingController(text: 'openai');
    final model = TextEditingController(text: 'gpt-6-astra');
    final reasoning = TextEditingController(text: 'high');
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    await open(
      tester,
      runtime: 'codex',
      provider: provider,
      model: model,
      reasoning: reasoning,
    );
    await tester.tap(
      find.byWidgetPredicate(
        (w) =>
            w is DropdownButtonFormField<String> &&
            w.decoration.labelText == 'Model',
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Custom value').last);
    await tester.pumpAndSettle();
    expect(find.text('Model ID'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).last, 'gpt-6.1-sol');
    await tester.pumpAndSettle();
    expect(model.text, 'gpt-6.1-sol');

    await tester.tap(find.text('Use CLI defaults'));
    await tester.pumpAndSettle();
    expect(model.text, isEmpty);
    expect(provider.text, isEmpty);
    expect(reasoning.text, isEmpty);
  });

  testWidgets('Antigravity and an unchosen runtime show no model fields', (
    tester,
  ) async {
    final provider = TextEditingController();
    final model = TextEditingController();
    final reasoning = TextEditingController();
    addTearDown(provider.dispose);
    addTearDown(model.dispose);
    addTearDown(reasoning.dispose);
    for (final runtime in ['agy', null]) {
      await open(
        tester,
        runtime: runtime,
        provider: provider,
        model: model,
        reasoning: reasoning,
      );
      expect(find.byType(ModelFields), findsOneWidget);
      expect(find.text('Reasoning effort'), findsNothing);
      expect(find.text('Use CLI defaults'), findsNothing);
    }
  });

  test('a gateway without a catalog leaves an empty option set', () {
    final empty = ModelCatalog.fromJson({'workspaces': []});
    expect(empty['codex'].models, isEmpty);
    expect(empty['codex'].efforts, isEmpty);
    expect(empty['missing'].models, isEmpty);
  });
}
