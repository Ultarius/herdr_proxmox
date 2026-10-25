import 'native_task_ui.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import '../services.dart';
import '../ui.dart' as ui;
import 'ops_ui.dart' as ops;

const knowledgeKinds = [
  'outcome',
  'finding',
  'decision',
  'question',
  'guidance',
];
const knowledgeStates = ['reported', 'hypothesis', 'reviewed', 'superseded'];

class KnowledgePage extends StatefulComponent {
  const KnowledgePage({
    super.key,
    this.coordinator = const AppCoordinator(),
    this.organizationId = '',
  });
  final AppCoordinator coordinator;
  final String organizationId;
  @override
  State<KnowledgePage> createState() => _KnowledgePageState();
}

class _KnowledgePageState extends State<KnowledgePage> {
  DashboardBloc get connection => dashboardConnection;
  final search = TextEditingController();
  List<Map<String, dynamic>> organizations = [], records = [];
  List<String> repositories = [];
  String org = '', repository = '', kind = '', status = '';
  String? error;
  int? before;
  int request = 0;
  bool loading = false, mutating = false, initializing = true;
  // Filter reads may finish while a write still owns the mutation guard.
  bool get busy => loading || mutating;
  Timer? debounce;
  bool get admin => connection.operator.value['role'] == 'admin';

  @override
  void initState() {
    super.initState();
    initialize();
  }

  @override
  void dispose() {
    request++;
    debounce?.cancel();
    search.dispose();
    super.dispose();
  }

  Future<void> initialize() async {
    try {
      final directory = await connection.request('organizations/directory');
      if (!mounted) return;
      organizations = List<Map<String, dynamic>>.from(
        directory['organizations'] ?? [],
      );
      org = organizations.any((o) => o['id'] == component.organizationId)
          ? component.organizationId
          : '${organizations.firstOrNull?['id'] ?? ''}';
      if (org.isEmpty) {
        setState(() {});
        return;
      }
      await load();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => initializing = false);
    }
  }

  Future<void> load({bool more = false}) async {
    if (org.isEmpty) return;
    final current = ++request, epoch = connection.generation;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final result = await connection.request('knowledge', {
        'mode': 'list',
        'organization_id': org,
        'repository': repository,
        'kind': kind,
        'state': status,
        'query': search.text,
        if (more && before != null) 'before': before,
      });
      if (!mounted || current != request || epoch != connection.generation)
        return;
      setState(() {
        final incoming = List<Map<String, dynamic>>.from(result['records']);
        records = more ? [...records, ...incoming] : incoming;
        repositories = List<String>.from(result['repositories']);
        before = result['next_before'] as int?;
      });
    } catch (e) {
      if (mounted && current == request && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted && current == request && epoch == connection.generation)
        setState(() => loading = false);
    }
  }

  Future<void> mutate(Map<String, dynamic> body) async {
    if (mutating || !connection.state.connected || org.isEmpty || !admin)
      return;
    final selectedOrg = org, epoch = connection.generation;
    setState(() {
      mutating = true;
      error = null;
    });
    try {
      await connection.request('knowledge', {
        'organization_id': selectedOrg,
        ...body,
      });
      if (mounted && org == selectedOrg && epoch == connection.generation)
        await load();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() => mutating = false);
    }
  }

  Future<void> create() async {
    if (busy || org.isEmpty || !admin) return;
    final title = TextEditingController(), body = TextEditingController();
    var selectedKind = 'finding', selectedRepo = repository;
    final form = FormKey();
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, change) => AlertDialog(
          title: const Text('Add project knowledge'),
          content: SizedBox(
            width: 560,
            child: SingleChildScrollView(
              child: Form(
                key: form,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    DropdownButtonFormField<String>(
                      initialValue: selectedRepo,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Repository scope',
                      ),
                      items: [
                        const DropdownMenuItem(
                          value: '',
                          child: Text('Organization-wide'),
                        ),
                        ...repositories.map(
                          (r) => DropdownMenuItem(
                            value: r,
                            child: Text(r, overflow: TextOverflow.ellipsis),
                          ),
                        ),
                      ],
                      onChanged: (v) => change(() => selectedRepo = v ?? ''),
                    ),
                    DropdownButtonFormField<String>(
                      initialValue: selectedKind,
                      decoration: const InputDecoration(
                        labelText: 'Knowledge kind',
                      ),
                      items: knowledgeKinds
                          .map(
                            (k) => DropdownMenuItem(value: k, child: Text(k)),
                          )
                          .toList(),
                      onChanged: (v) => change(() => selectedKind = v!),
                    ),
                    TextFormField(
                      controller: title,
                      maxLength: 180,
                      decoration: const InputDecoration(labelText: 'Title'),
                      validator: (v) => v == null || v.trim().isEmpty
                          ? 'Enter a title'
                          : null,
                    ),
                    TextFormField(
                      controller: body,
                      maxLength: 4000,
                      minLines: 4,
                      maxLines: 10,
                      decoration: const InputDecoration(
                        labelText: 'Finding, evidence and limitations',
                      ),
                      validator: (v) => v == null || v.trim().isEmpty
                          ? 'Enter knowledge text'
                          : null,
                    ),
                    const Text(
                      'Saved as reported knowledge. Review is separate from test verification. Exclude credentials.',
                    ),
                  ],
                ),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                if (form.currentState!.validate()) Navigator.pop(context, true);
              },
              child: const Text('Save knowledge'),
            ),
          ],
        ),
      ),
    );
    if (saved == true && mounted)
      await mutate({
        'mode': 'create',
        'repository': selectedRepo,
        'kind': selectedKind,
        'title': title.text,
        'body': body.text,
      });
    title.dispose();
    body.dispose();
  }

  Future<void> review(
    Map<String, dynamic> record, {
    bool supersede = false,
  }) async {
    if (busy || !admin) return;
    final reason = TextEditingController();
    String? replacement;
    final candidates = records
        .where(
          (r) =>
              r['id'] != record['id'] &&
              r['repository'] == record['repository'] &&
              r['state'] != 'superseded',
        )
        .toList();
    final form = FormKey();
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(supersede ? 'Supersede knowledge' : 'Review knowledge'),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Form(
              key: form,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'The original text and evidence stay intact. Reviewing does not certify test results.',
                  ),
                  if (supersede)
                    DropdownButtonFormField<String>(
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Replacement record',
                      ),
                      items: candidates
                          .map(
                            (r) => DropdownMenuItem(
                              value: r['id'] as String,
                              child: Text(
                                '${r['title']}',
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: (v) => replacement = v,
                      validator: (v) => v == null
                          ? 'Select a replacement; create one first if needed'
                          : null,
                    ),
                  TextFormField(
                    controller: reason,
                    maxLength: 1500,
                    minLines: 2,
                    maxLines: 5,
                    decoration: const InputDecoration(
                      labelText: 'Review reason',
                    ),
                    validator: (v) => v == null || v.trim().isEmpty
                        ? 'Explain your decision'
                        : null,
                  ),
                ],
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (form.currentState!.validate()) Navigator.pop(context, true);
            },
            child: const Text('Record decision'),
          ),
        ],
      ),
    );
    if (approved == true && mounted)
      await mutate({
        'mode': supersede ? 'supersede' : 'review',
        'id': record['id'],
        'reason': reason.text,
        if (supersede) 'replacement_id': replacement,
      });
    reason.dispose();
  }

  /// Presentation only: which records show their evidence and provenance.
  final expanded = <String>{};

  String capitalized(Object? value) {
    final word = '${value ?? ''}';
    return word.isEmpty ? word : '${word[0].toUpperCase()}${word.substring(1)}';
  }

  void searchChanged(String value) {
    search.text = value.length > 200 ? value.substring(0, 200) : value;
    setState(() {});
    debounce?.cancel();
    debounce = Timer(const Duration(milliseconds: 250), () => load());
  }

  void clearSearch() {
    debounce?.cancel();
    search.clear();
    setState(() {});
    load();
  }

  Component toolbar() => ui.el('div', [
    ui.el('div', [
      ui.icon('search', classes: 'icon--sm search-field__icon'),
      ui.field(
        'Search knowledge',
        search.text,
        searchChanged,
        placeholder: 'Search titles and evidence',
      ),
      if (search.text.isNotEmpty)
        ui.iconButton(
          'Clear knowledge search',
          'x',
          clearSearch,
          small: true,
          classes: 'search-field__clear',
        ),
    ], classes: 'search-field knowledge-search'),
    ui.el(
      'details',
      [
        ui.el('summary', [
          ui.icon('filter'),
          ui.text('Filters'),
          if ([repository, kind, status].any((value) => value.isNotEmpty))
            ui.badge(
              '${[repository, kind, status].where((value) => value.isNotEmpty).length} active',
            ),
        ]),
        ui.el('div', [
          if (organizations.isNotEmpty)
            ui.select(
              'Organization',
              org,
              {for (final o in organizations) '${o['id']}': '${o['name']}'},
              (v) {
                setState(() {
                  org = v;
                  repository = '';
                  records = [];
                });
                load();
              },
            ),
          ui.select(
            'Repositories',
            repository,
            {'': 'All repositories', for (final r in repositories) r: r},
            (v) {
              repository = v;
              load();
            },
          ),
          ui.select(
            'Kinds',
            kind,
            {
              '': 'All kinds',
              for (final k in knowledgeKinds) k: capitalized(k),
            },
            (v) {
              kind = v;
              load();
            },
          ),
          stateChips(),
        ], classes: 'knowledge-filter-fields'),
      ],
      classes: 'knowledge-filters',
      attributes: {
        if (!web.window.matchMedia('(max-width: 639.98px)').matches) 'open': '',
      },
    ),
  ], classes: 'knowledge-toolbar');

  Component stateChips() => ui.el(
    'div',
    [
      ui.el('span', [ui.text('States')], classes: 'toolbar__label'),
      for (final value in ['', ...knowledgeStates])
        ops.chip(
          value.isEmpty ? 'All' : capitalized(value),
          status == value,
          () {
            setState(() => status = value);
            load();
          },
        ),
    ],
    classes: 'chips knowledge-states',
    attributes: {'role': 'group', 'aria-label': 'Filter knowledge by state'},
  );

  Component recordView(Map<String, dynamic> record) {
    final id = '${record['id']}';
    final source = Map<String, dynamic>.from(record['source'] as Map? ?? {});
    final open = expanded.contains(id);
    final repository = record['repository'] == ''
        ? 'Organization-wide'
        : '${record['repository']}';
    final state = '${record['state']}';
    final tone = switch (state) {
      'reviewed' => 'success',
      'hypothesis' => 'warning',
      'reported' => 'info',
      _ => 'neutral',
    };
    return ui.el('li', [
      ui.el('div', [
        dom.button(
          [
            ui.el('span', [
              ui.text(record['title']),
            ], classes: 'knowledge-record__title'),
            // The full kind / state / repository line stays in the button's
            // name; the tags beside it show the same facts visually.
            ui.el('span', [
              ui.text('${record['kind']} / $state / $repository'),
            ], classes: 'visually-hidden'),
            ui.icon('chevron-down', classes: 'disclosure-chevron'),
          ],
          type: dom.ButtonType.button,
          classes: 'knowledge-record__toggle',
          attributes: {
            'id': 'knowledge-record-toggle-$id',
            'aria-expanded': '$open',
            'aria-controls': 'knowledge-record-$id',
            // Keep the absolute accessible metadata inside its disclosure;
            // otherwise its static position can escape the page's scroller.
            'style': 'position:relative;',
          },
          onClick: () => setState(() {
            if (!expanded.remove(id)) expanded.add(id);
          }),
        ),
        ui.el('div', [
          ui.el('span', [
            ui.icon(switch ('${record['kind']}') {
              'outcome' => 'circle-check',
              'decision' => 'git-merge',
              'question' => 'circle-help',
              'guidance' => 'book-open',
              _ => 'search',
            }),
            ui.text(capitalized(record['kind'])),
          ], classes: 'type-tag'),
          ops.pill(
            capitalized(state),
            tone: tone,
            shape: state == 'superseded' ? 'dash' : ops.shapeFor(tone),
          ),
          ui.el('span', [ui.text(repository)], classes: 'mono-chip'),
        ], classes: 'knowledge-record__tags'),
      ], classes: 'knowledge-record__head'),
      ui.el(
        'div',
        [
          ui.el('p', [
            ui.text(record['body']),
          ], classes: 'knowledge-record__text'),
          ops.facts([
            ('Knowledge ID', ops.mono(record['id'])),
            ('Captured', ops.mono(record['created_at'])),
            ('Source', ui.text(source['kind'])),
            ('Commit', ops.mono(source['source_sha'] ?? 'Not recorded')),
            (
              'Exact-commit required checks verified',
              ops.mono('${source['checks_verified'] == true}'),
            ),
            for (final agent in source['agents'] as List? ?? [])
              (
                'Agent',
                ui.text(
                  '${agent['name'] ?? agent['profile_id']} / ${agent['role'] ?? ''} / ${agent['runtime'] ?? ''}',
                ),
              ),
            if (source['run_id'] != null)
              ('Agent run', ops.mono(source['run_id'])),
            if (record['replacement_id'] != null)
              ('Replaced by', ui.text('knowledge ${record['replacement_id']}')),
          ]),
          ui.el('div', [
            if (source['task_id'] != null)
              ui.button(
                'Open source task',
                () => component.coordinator.push(
                  TaskDetailRoute(source['task_id'] as String),
                ),
                small: true,
                iconName: 'list-checks',
              ),
            if (source['group_id'] != null)
              ui.button(
                'Open source group',
                () => component.coordinator.push(
                  GroupRoute(source['group_id'] as String),
                ),
                small: true,
                iconName: 'users',
              ),
            if (admin && record['state'] != 'superseded')
              ui.button(
                'Review record',
                busy ? null : () => review(record),
                small: true,
                iconName: 'circle-check',
              ),
            if (admin && record['state'] != 'superseded')
              ui.button(
                'Supersede record',
                busy ? null : () => review(record, supersede: true),
                small: true,
                iconName: 'git-compare',
              ),
          ], classes: 'actions'),
          if ((record['audit'] as List? ?? []).isNotEmpty) ...[
            ui.el('h3', [ui.text('Audit')], classes: 'overline'),
            ui.el(
              'ul',
              [
                for (final audit in record['audit'] as List)
                  ui.el('li', [
                    ui.icon('history', classes: 'icon--sm'),
                    ui.text(
                      '${audit['action']} by ${audit['actor']} at ${audit['at']}: ${audit['reason']}',
                    ),
                  ]),
              ],
              classes: 'knowledge-audit',
              attributes: {'role': 'list'},
            ),
          ],
        ],
        classes: 'knowledge-record__body',
        attributes: {
          'id': 'knowledge-record-$id',
          'role': 'region',
          'aria-labelledby': 'knowledge-record-toggle-$id',
          if (!open) 'hidden': '',
        },
      ),
    ], classes: 'knowledge-record');
  }

  @override
  Component build(BuildContext context) => ops.opsPage([
    ui.pageHeader(
      'Project knowledge',
      subtitle:
          'Findings, decisions and open questions preserved across agent sessions. Historical claims must be reconciled with current code. Reviewed does not mean tests passed.',
      actions: [
        ui.button(
          'Refresh knowledge',
          busy || org.isEmpty ? null : () => load(),
          iconName: 'refresh-cw',
        ),
        if (admin)
          ui.button(
            'Add knowledge',
            busy || org.isEmpty ? null : create,
            primary: true,
            iconName: 'plus',
          ),
      ],
    ),
    ui.card(
      'Knowledge records',
      [
        toolbar(),
        if (busy || initializing)
          ui.el(
            'progress',
            [],
            attributes: {'aria-label': 'Loading knowledge'},
          ),
        if (error != null) ui.notice(error, error: true),
        if (!initializing && organizations.isEmpty && error == null)
          ui.emptyState(
            'Create an organization to start retaining project knowledge.',
            iconName: 'book-open',
          ),
        if (!initializing &&
            !busy &&
            error == null &&
            records.isEmpty &&
            org.isNotEmpty)
          ui.emptyState(
            'No knowledge matches these filters. Outcomes are captured as task candidates and completed work are recorded.',
            iconName: 'book-open',
          ),
        if (records.isNotEmpty)
          ui.el(
            'ul',
            [for (final record in records) recordView(record)],
            classes: 'knowledge-list',
            attributes: {'role': 'list'},
          ),
        if (before != null)
          ui.el('div', [
            ui.button(
              'Load older knowledge',
              busy ? null : () => load(more: true),
              iconName: 'chevron-down',
            ),
          ], classes: 'knowledge-more'),
      ],
      iconName: 'book-open',
      classes: 'knowledge-card',
    ),
  ], classes: 'knowledge-page');
}
