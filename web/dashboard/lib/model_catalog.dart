import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

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

/// Provider, model and reasoning controls for one agent profile.
///
/// A model released after this gateway build stays selectable: each dropdown
/// keeps whatever the profile already stores, even when the catalog no longer
/// lists it, and offers a manual entry beside the curated options.
class ModelFields extends StatefulWidget {
  const ModelFields({
    super.key,
    required this.runtime,
    required this.catalog,
    required this.provider,
    required this.model,
    required this.reasoning,
    required this.project,
  });
  final String? runtime;
  final RuntimeCatalog catalog;
  final TextEditingController provider;
  final TextEditingController model;
  final TextEditingController reasoning;
  final String project;

  @override
  State<ModelFields> createState() => _ModelFieldsState();
}

class _ModelFieldsState extends State<ModelFields> {
  RuntimeCatalog? discovered;
  bool manualProvider = false;
  bool manualModel = false;
  bool busy = false;
  String? error;
  int requestId = 0;

  RuntimeCatalog get options => discovered ?? widget.catalog;
  String get selectedProvider => widget.provider.text.trim();
  String get selectedModel => widget.model.text.trim();
  String get selectedReasoning => widget.reasoning.text.trim();

  @override
  void didUpdateWidget(ModelFields old) {
    super.didUpdateWidget(old);
    // Another runtime has its own provider list and model identifiers. A
    // rebuild follows, so no setState is needed here.
    if (old.runtime != widget.runtime || old.project != widget.project) {
      requestId++;
      busy = false;
      discovered = null;
      manualProvider = false;
      manualModel = false;
      error = null;
    }
  }

  void useDefaults() {
    widget.provider.clear();
    widget.model.clear();
    widget.reasoning.clear();
    setState(() {
      discovered = null;
      manualProvider = false;
      manualModel = false;
      error = null;
    });
  }

  /// Let the CLI enumerate the connected account's own catalog. Codex and
  /// Claude expose no such command, so this stays OpenCode-only.
  Future<void> discover() async {
    final connection = BlocScope.get<DashboardBloc>();
    final epoch = connection.generation;
    final current = ++requestId;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await connection.request('models', {
        'runtime': widget.runtime,
        'project': widget.project,
      });
      if (!mounted || epoch != connection.generation || current != requestId)
        return;
      final found = RuntimeCatalog.fromJson(
        result is Map<String, dynamic> ? result : null,
      );
      if (found.models.isEmpty) {
        setState(() => error = 'The CLI reported no models.');
        return;
      }
      setState(() {
        discovered = RuntimeCatalog(
          models: found.models,
          efforts: found.efforts.isEmpty ? options.efforts : found.efforts,
          source: found.source,
          supportsVariants: found.supportsVariants,
        );
      });
    } catch (e) {
      if (!mounted || epoch != connection.generation || current != requestId)
        return;
      setState(() => error = e.toString());
    } finally {
      if (mounted && current == requestId) setState(() => busy = false);
    }
  }

  /// Offer the catalog, an empty default, and any value already stored.
  List<CatalogChoice> items(
    List<CatalogChoice> offered,
    String selected, {
    required String defaultLabel,
  }) => [
    CatalogChoice(id: '', label: defaultLabel),
    for (final choice in offered)
      if (choice.id.isNotEmpty) choice,
    if (selected.isNotEmpty && !offered.any((choice) => choice.id == selected))
      CatalogChoice(id: selected, label: '$selected (not in catalog)'),
  ];

  Widget picker({
    required String label,
    required String defaultLabel,
    required String selected,
    required List<CatalogChoice> offered,
    required ValueChanged<String> changed,
    required bool manual,
    required VoidCallback toggleManual,
    VoidCallback? clearManual,
    String? Function(String?)? validator,
    String helper = '',
  }) => DropdownButtonFormField<String>(
    // The field keeps its own selection, so the key re-reads initialValue.
    key: ValueKey(
      '$label-$manual-$selected-${offered.map((o) => o.id).join(',')}',
    ),
    isExpanded: true,
    initialValue: manual ? manualEntry : selected,
    decoration: InputDecoration(labelText: label, helperText: helper),
    validator: validator,
    items: [
      for (final choice in items(offered, selected, defaultLabel: defaultLabel))
        DropdownMenuItem(value: choice.id, child: Text(choice.label)),
      if (label != 'Reasoning variant' && label != 'Reasoning effort')
        DropdownMenuItem(
          value: manualEntry,
          child: Text(manual ? 'Entering a custom value' : 'Custom value'),
        ),
    ],
    onChanged: (value) {
      if (value == null) return;
      if (value == manualEntry) {
        setState(() => toggleManual());
        changed('');
        return;
      }
      if (manual) clearManual?.call();
      changed(value);
      setState(() {});
    },
  );

  Widget manualField(
    TextEditingController controller,
    String label, {
    String helper = '',
  }) => TextFormField(
    controller: controller,
    decoration: InputDecoration(labelText: label, helperText: helper),
    onChanged: (_) => setState(() {}),
  );

  @override
  Widget build(BuildContext context) {
    final runtime = widget.runtime;
    if (runtime == null || runtime == 'agy') return const SizedBox.shrink();
    final catalog = options;
    final offered = runtime == 'opencode' && selectedProvider.isEmpty
        ? <CatalogModel>[]
        : catalog.forProvider(runtime == 'opencode' ? selectedProvider : '');

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (runtime == 'opencode')
          picker(
            label: 'Provider',
            defaultLabel: 'CLI default',
            selected: selectedProvider,
            offered: [
              for (final provider in catalog.providers)
                CatalogChoice(id: provider, label: provider),
            ],
            helper: 'OpenCode launches provider/model, so both are required.',
            manual: manualProvider,
            toggleManual: () => manualProvider = !manualProvider,
            clearManual: () => manualProvider = false,
            validator: (value) =>
                selectedModel.isNotEmpty && selectedProvider.isEmpty
                ? 'Choose a provider for this model.'
                : null,
            changed: (value) {
              widget.provider.text = value;
              widget.model.clear();
              widget.reasoning.clear();
            },
          ),
        if (runtime != 'opencode' || manualProvider) ...[
          const SizedBox(height: 12),
          manualField(
            widget.provider,
            'Provider',
            helper: runtime == 'claude'
                ? 'Leave empty to use the account configured in Claude.'
                : 'Optional provider ID for this CLI.',
          ),
        ],
        const SizedBox(height: 12),
        picker(
          label: 'Model',
          defaultLabel: 'CLI default',
          selected: selectedModel,
          offered: [
            for (final model in offered)
              CatalogChoice(id: model.id, label: model.name),
          ],
          helper: runtime == 'opencode'
              ? 'Choose a provider first. Launched as provider/model.'
              : offered.isEmpty
              ? 'This CLI reported no models.'
              : '${offered.length} models available.',
          manual: manualModel,
          toggleManual: () => manualModel = !manualModel,
          clearManual: () => manualModel = false,
          changed: (value) {
            widget.model.text = value;
            widget.reasoning.clear();
          },
        ),
        if (manualModel) ...[
          const SizedBox(height: 12),
          manualField(
            widget.model,
            'Model ID',
            helper: 'The exact identifier your CLI expects.',
          ),
        ],
        const SizedBox(height: 12),
        picker(
          label: runtime == 'opencode'
              ? 'Reasoning variant'
              : 'Reasoning effort',
          defaultLabel: 'Model default',
          selected: selectedReasoning,
          offered: runtime == 'opencode'
              ? [
                  if (catalog.supportsVariants)
                    for (final variant
                        in offered
                                .where(
                                  (m) =>
                                      m.id == selectedModel &&
                                      m.provider == selectedProvider,
                                )
                                .firstOrNull
                                ?.reasoning ??
                            <String>[])
                      CatalogChoice(id: variant, label: variant),
                ]
              : catalog.efforts,
          helper: runtime == 'opencode' && !catalog.supportsVariants
              ? 'Interactive reasoning variants require OpenCode v2. Choose Model default on v1.'
              : 'Only variants reported for this model are offered.',
          manual: false,
          toggleManual: () {},
          changed: (value) => widget.reasoning.text = value,
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          children: [
            if (runtime == 'opencode')
              TextButton.icon(
                onPressed: busy ? null : discover,
                icon: busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh, size: 18),
                label: Text(
                  busy
                      ? 'Reading models'
                      : catalog.source.startsWith('opencode')
                      ? 'Refresh models'
                      : 'Load models from OpenCode',
                ),
              ),
            TextButton(
              onPressed: useDefaults,
              child: const Text('Use CLI defaults'),
            ),
          ],
        ),
        if (error != null)
          Text(error!, style: const TextStyle(color: Color(0xffff6b6b))),
      ],
    );
  }
}

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
