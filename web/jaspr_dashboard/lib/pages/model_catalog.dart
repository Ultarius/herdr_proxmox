import 'dart:async';
import 'package:herdr_dashboard_state/organization_tools_bloc.dart';
import '../ui.dart';

import '../services.dart';

import 'org_forms.dart';

String savedModelLabel(Map<String, dynamic> profile) {
  final model = profile['model'] as String? ?? '';
  final provider = profile['provider'] as String? ?? '';
  return model.isEmpty
      ? 'CLI default'
      : [if (provider.isNotEmpty) provider, model].join('/');
}

String savedReasoningLabel(Map<String, dynamic> profile) {
  final reasoning = profile['reasoning'] as String? ?? '';
  return reasoning.isEmpty ? 'Model default' : reasoning;
}

/// Sentinel item value that reveals the manual entry field.

const String manualEntry = 'manual';

/// One selectable model, as reported by the gateway catalog or by

/// `opencode models`. [provider] is empty for the single-provider CLIs.

class CatalogModel {
  const CatalogModel({
    required this.id,

    required this.name,

    this.provider = '',

    this.reasoning = const [],
  });

  final String id;

  final String name;

  final String provider;

  final List<String> reasoning;

  factory CatalogModel.fromJson(Map<String, dynamic> json) => CatalogModel(
    id: '${json['id'] ?? ''}',

    name: '${json['name'] ?? json['id'] ?? ''}',

    provider: '${json['provider'] ?? ''}',

    reasoning: List<String>.from(json['reasoning'] ?? []),
  );
}

class CatalogChoice {
  const CatalogChoice({required this.id, required this.label});

  final String id;

  final String label;

  factory CatalogChoice.fromJson(Map<String, dynamic> json) => CatalogChoice(
    id: '${json['id'] ?? ''}',

    label: '${json['label'] ?? json['id'] ?? ''}',
  );
}

/// Dropdown options for one runtime.

class RuntimeCatalog {
  const RuntimeCatalog({
    this.models = const [],

    this.efforts = const [],

    this.source = '',

    this.supportsVariants = false,
  });

  final List<CatalogModel> models;

  final List<CatalogChoice> efforts;

  final String source;

  final bool supportsVariants;

  List<String> get providers => {
    for (final model in models) model.provider,
  }.where((p) => p.isNotEmpty).toList()..sort();

  /// Models offered for a provider. An empty provider offers everything, so a

  /// hand-typed provider still gets suggestions.

  List<CatalogModel> forProvider(String provider) => models
      .where(
        (model) =>
            provider.isEmpty ||
            model.provider.isEmpty ||
            model.provider == provider,
      )
      .toList();

  factory RuntimeCatalog.fromJson(Map<String, dynamic>? json) {
    if (json == null) return const RuntimeCatalog();

    return RuntimeCatalog(
      models: [
        for (final model in (json['models'] as List? ?? const []))
          if (model is Map<String, dynamic>) CatalogModel.fromJson(model),
      ],

      efforts: [
        for (final effort in (json['efforts'] as List? ?? const []))
          if (effort is Map<String, dynamic>) CatalogChoice.fromJson(effort),
      ],

      source: '${json['source'] ?? ''}',

      supportsVariants: json['supports_variants'] == true,
    );
  }
}

/// Model catalogs keyed by runtime, from `GET /api/models`.

class ModelCatalog {
  const ModelCatalog(this.runtimes);

  final Map<String, RuntimeCatalog> runtimes;

  factory ModelCatalog.fromJson(Map<String, dynamic>? json) {
    final runtimes = json?['runtimes'];

    if (runtimes is! Map) return const ModelCatalog({});

    return ModelCatalog({
      for (final entry in runtimes.entries)
        if (entry.key is String && entry.value is Map<String, dynamic>)
          entry.key as String: RuntimeCatalog.fromJson(
            entry.value as Map<String, dynamic>,
          ),
    });
  }

  RuntimeCatalog operator [](String? runtime) =>
      runtimes[runtime] ?? const RuntimeCatalog();
}

class ModelFields extends StatefulComponent {
  const ModelFields({
    super.key,
    required this.runtime,
    required this.project,
    required this.catalog,
    required this.values,
    required this.onChanged,
  });

  final String runtime, project;
  final ModelCatalog catalog;
  final Json values;
  final void Function() onChanged;

  @override
  State<ModelFields> createState() => _ModelFieldsState();
}

class _ModelFieldsState extends State<ModelFields> {
  ModelDiscoveryBloc? discovery;
  StreamSubscription? discoverySubscription;
  RuntimeCatalog? get discovered {
    final raw = discovery?.catalog;
    if (raw == null) return null;
    final found = RuntimeCatalog.fromJson(raw);
    return RuntimeCatalog(
      models: found.models,
      efforts: found.efforts.isEmpty
          ? component.catalog[component.runtime].efforts
          : found.efforts,
      source: found.source,
      supportsVariants: found.supportsVariants,
    );
  }

  bool get busy => discovery?.state.loading ?? false;
  String? get error => discovery?.state.error ?? discovery?.warning;
  final manualKeys = <String>{};
  @override
  void initState() {
    super.initState();
    try {
      discovery = ModelDiscoveryBloc(dashboardConnection);
      discoverySubscription = discovery!.stream.listen((_) {
        if (mounted) setState(() {});
      });
    } catch (_) {
      /* Standalone draft forms may not have a connection. */
    }
    if (component.runtime == 'opencode' && component.project.isNotEmpty)
      discover();
  }

  RuntimeCatalog get options =>
      discovered ?? component.catalog[component.runtime];

  Future<void> discover({bool force = false}) async {
    await discovery?.discover(
      runtime: component.runtime,
      project: component.project,
      provider: '${component.values['provider'] ?? ''}',
      model: '${component.values['model'] ?? ''}',
      force: force,
    );
  }

  @override
  void didUpdateComponent(ModelFields old) {
    super.didUpdateComponent(old);
    if (old.runtime != component.runtime || old.project != component.project) {
      discovery?.reset();
      manualKeys.clear();
      if (component.runtime == 'opencode' && component.project.isNotEmpty)
        discover();
    }
  }

  Component picker(String key, String label, Map<String, String> choices) {
    final value = '${component.values[key] ?? ''}';

    return el('div', [
      select(
        label,
        manualKeys.contains(key) ? 'manual' : value,
        {
          '': key == 'reasoning' ? 'Model default' : 'CLI default',
          ...choices,
          if (value.isNotEmpty && value != 'manual')
            value: choices[value] ?? value,
          if (key != 'reasoning') 'manual': 'Manual entry',
        },
        (v) {
          if (v == 'manual') {
            manualKeys.add(key);
            component.values[key] = '';
            if (key == 'provider') {
              component.values['model'] = '';
              component.values['reasoning'] = '';
            }
            if (key == 'model') component.values['reasoning'] = '';
          } else {
            manualKeys.remove(key);
            component.values[key] = v;
            if (key == 'provider') {
              component.values['model'] = '';
              component.values['reasoning'] = '';
            }
            if (key == 'model') component.values['reasoning'] = '';
          }
          setState(() {});
          component.onChanged();
        },
      ),
      if (manualKeys.contains(key))
        field('$label (manual)', value, (v) {
          component.values[key] = v;
        }),
    ], classes: 'org-model__picker');
  }

  @override
  void dispose() {
    discoverySubscription?.cancel();
    discovery?.dispose();
    super.dispose();
  }

  @override
  Component build(BuildContext context) => el('section', [
    el('div', [
      el('h3', [
        icon('cpu', classes: 'icon--sm'),
        text('Model and reasoning'),
      ], classes: 'org-model__title'),
      if (busy)
        el('span', [text('Checking the CLI…')], classes: 'org-model__busy'),
    ], classes: 'org-model__head'),
    el('div', [
      if (component.runtime == 'opencode')
        picker('provider', 'Provider', {
          for (final p in options.providers)
            p: p == 'opencode-go'
                ? 'OpenCode Go (opencode-go)'
                : p == 'opencode'
                ? 'OpenCode Zen (opencode)'
                : p,
        })
      else
        el('div', [
          field(
            'Provider (optional)',
            '${component.values['provider'] ?? ''}',
            (v) => component.values['provider'] = v,
          ),
        ], classes: 'org-model__picker'),
      ..._models(),
    ], classes: 'org-model__grid'),
    if (component.runtime == 'opencode')
      el('p', [
        text(
          'Go subscriptions use opencode-go; opencode selects Zen. Connect the matching provider in CLI accounts. Listing a model does not prove account access.',
        ),
      ], classes: 'org-note'),
    row([
      if (component.runtime == 'opencode') ...[
        button(
          'Discover account models',
          () => discover(),
          disabled: busy,
          small: true,
          iconName: 'search',
        ),
        button(
          'Refresh CLI models',
          () => discover(force: true),
          disabled: busy,
          small: true,
          iconName: 'refresh-cw',
        ),
      ],
      button(
        'Use CLI defaults',
        () => setState(() {
          manualKeys.clear();
          discovery?.reset();
          for (final key in ['provider', 'model', 'reasoning'])
            component.values[key] = '';
          component.onChanged();
        }),
        small: true,
        variant: 'ghost',
        iconName: 'rotate-ccw',
      ),
    ]),
    if (error != null)
      el('p', [
        icon('circle-x', classes: 'icon--sm'),
        text(error),
      ], classes: 'form-error'),
    if (options.source.isNotEmpty)
      el('p', [
        text('Catalog source: '),
        el('span', [text(options.source)], classes: 'mono'),
      ], classes: 'org-model__source'),
  ], classes: 'org-model');

  List<Component> _models() => [
    picker('model', 'Model', {
      for (final m
          in (component.runtime == 'opencode' &&
                  '${component.values['provider'] ?? ''}'.isEmpty
              ? <CatalogModel>[]
              : options.forProvider(
                  component.runtime == 'opencode'
                      ? '${component.values['provider'] ?? ''}'
                      : '',
                )))
        m.id: m.name,
    }),

    picker(
      'reasoning',
      options.supportsVariants ? 'Variant / reasoning' : 'Reasoning effort',
      {
        if (component.runtime != 'opencode')
          for (final e in options.efforts) e.id: e.label,
        for (final m in options.models.where(
          (m) =>
              component.runtime == 'opencode' &&
              options.supportsVariants &&
              m.id == component.values['model'] &&
              m.provider == component.values['provider'],
        ))
          for (final r in m.reasoning) r: r,
      },
    ),
  ];
}
