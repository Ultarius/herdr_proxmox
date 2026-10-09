import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'routes.dart';

const knowledgeKinds = [
  'outcome',
  'finding',
  'decision',
  'question',
  'guidance',
];
const knowledgeStates = ['reported', 'hypothesis', 'reviewed', 'superseded'];

class KnowledgePage extends StatefulWidget {
  const KnowledgePage({
    super.key,
    required this.coordinator,
    this.organizationId = '',
  });
  final AppCoordinator coordinator;
  final String organizationId;
  @override
  State<KnowledgePage> createState() => _KnowledgePageState();
}

class _KnowledgePageState extends State<KnowledgePage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  final search = TextEditingController();
  List<Map<String, dynamic>> organizations = [], records = [];
  List<String> repositories = [];
  String org = '', repository = '', kind = '', status = '';
  String? error;
  int? before;
  int request = 0;
  bool busy = false;
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
      org = organizations.any((o) => o['id'] == widget.organizationId)
          ? widget.organizationId
          : '${organizations.firstOrNull?['id'] ?? ''}';
      if (org.isEmpty) {
        setState(() {});
        return;
      }
      await load();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> load({bool more = false}) async {
    if (org.isEmpty) return;
    final current = ++request, epoch = connection.generation;
    setState(() {
      busy = true;
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
      if (mounted && current == request) setState(() => error = '$e');
    } finally {
      if (mounted && current == request) setState(() => busy = false);
    }
  }

  Future<void> mutate(Map<String, dynamic> body) async {
    final selectedOrg = org, epoch = connection.generation;
    setState(() {
      busy = true;
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
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> create() async {
    final title = TextEditingController(), body = TextEditingController();
    var selectedKind = 'finding', selectedRepo = repository;
    final form = GlobalKey<FormState>();
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
    final form = GlobalKey<FormState>();
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

  Widget filter(
    String label,
    String value,
    List<String> choices,
    void Function(String) changed,
  ) => SizedBox(
    width: 220,
    child: DropdownButtonFormField<String>(
      key: ValueKey('$label:$org:$value'),
      initialValue: value,
      isExpanded: true,
      decoration: InputDecoration(labelText: label),
      items: [
        DropdownMenuItem(value: '', child: Text('All ${label.toLowerCase()}')),
        ...choices.map(
          (v) => DropdownMenuItem(
            value: v,
            child: Text(v, overflow: TextOverflow.ellipsis),
          ),
        ),
      ],
      onChanged: (v) {
        changed(v ?? '');
        load();
      },
    ),
  );

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(20),
    children: [
      Text(
        'Project knowledge',
        style: Theme.of(context).textTheme.headlineMedium,
      ),
      const SizedBox(height: 8),
      const Text(
        'Findings, decisions and open questions preserved across agent sessions. Historical claims must be reconciled with current code. Reviewed does not mean tests passed.',
      ),
      const SizedBox(height: 16),
      if (organizations.isNotEmpty)
        DropdownButtonFormField<String>(
          key: ValueKey(org),
          initialValue: org,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Organization'),
          items: organizations
              .map(
                (o) => DropdownMenuItem(
                  value: o['id'] as String,
                  child: Text('${o['name']}'),
                ),
              )
              .toList(),
          onChanged: (v) {
            setState(() {
              org = v!;
              repository = '';
              records = [];
            });
            load();
          },
        ),
      if (organizations.isEmpty && error == null)
        const Text(
          'Create an organization to start retaining project knowledge.',
        ),
      const SizedBox(height: 12),
      TextField(
        controller: search,
        maxLength: 200,
        decoration: InputDecoration(
          labelText: 'Search knowledge',
          prefixIcon: const Icon(Icons.search),
          suffixIcon: search.text.isEmpty
              ? null
              : IconButton(
                  tooltip: 'Clear knowledge search',
                  icon: const Icon(Icons.clear),
                  onPressed: () {
                    debounce?.cancel();
                    search.clear();
                    setState(() {});
                    load();
                  },
                ),
        ),
        onChanged: (_) {
          setState(() {});
          debounce?.cancel();
          debounce = Timer(const Duration(milliseconds: 250), () => load());
        },
      ),
      Wrap(
        spacing: 12,
        runSpacing: 12,
        children: [
          filter(
            'Repositories',
            repository,
            repositories,
            (v) => repository = v,
          ),
          filter('Kinds', kind, knowledgeKinds, (v) => kind = v),
          filter('States', status, knowledgeStates, (v) => status = v),
        ],
      ),
      Wrap(
        spacing: 12,
        children: [
          TextButton.icon(
            onPressed: busy || org.isEmpty ? null : () => load(),
            icon: const Icon(Icons.refresh),
            label: const Text('Refresh knowledge'),
          ),
          if (admin)
            FilledButton.icon(
              onPressed: busy || org.isEmpty ? null : create,
              icon: const Icon(Icons.add),
              label: const Text('Add knowledge'),
            ),
        ],
      ),
      if (busy) const LinearProgressIndicator(),
      if (error != null)
        Text(
          error!,
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      if (!busy && records.isEmpty && org.isNotEmpty)
        const Padding(
          padding: EdgeInsets.all(20),
          child: Text(
            'No knowledge matches these filters. Outcomes are captured as task candidates and completed work are recorded.',
          ),
        ),
      for (final record in records)
        Card(
          child: ExpansionTile(
            key: ValueKey(record['id']),
            title: Text(
              '${record['title']}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            subtitle: Text(
              '${record['kind']} / ${record['state']} / ${record['repository'] == '' ? 'Organization-wide' : record['repository']}',
            ),
            childrenPadding: const EdgeInsets.all(16),
            expandedCrossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SelectableText('${record['body']}'),
              const SizedBox(height: 12),
              SelectableText(
                'Knowledge ID: ${record['id']}\nCaptured: ${record['created_at']}\nSource: ${(record['source'] as Map)['kind']}\nCommit: ${(record['source'] as Map)['source_sha'] ?? 'Not recorded'}\nExact-commit required checks verified: ${(record['source'] as Map)['checks_verified'] == true}',
              ),
              for (final agent
                  in (record['source'] as Map)['agents'] as List? ?? [])
                Text(
                  'Agent: ${agent['name'] ?? agent['profile_id']} / ${agent['role'] ?? ''} / ${agent['runtime'] ?? ''}',
                ),
              if ((record['source'] as Map)['run_id'] != null)
                SelectableText(
                  'Agent run: ${(record['source'] as Map)['run_id']}',
                ),
              Wrap(
                spacing: 8,
                children: [
                  if ((record['source'] as Map)['task_id'] != null)
                    TextButton(
                      onPressed: () => widget.coordinator.push(
                        TaskDetailRoute(
                          (record['source'] as Map)['task_id'] as String,
                        ),
                      ),
                      child: const Text('Open source task'),
                    ),
                  if ((record['source'] as Map)['group_id'] != null)
                    TextButton(
                      onPressed: () => widget.coordinator.push(
                        GroupRoute(
                          (record['source'] as Map)['group_id'] as String,
                        ),
                      ),
                      child: const Text('Open source group'),
                    ),
                  if (admin && record['state'] != 'superseded')
                    TextButton(
                      onPressed: busy ? null : () => review(record),
                      child: const Text('Review record'),
                    ),
                  if (admin && record['state'] != 'superseded')
                    TextButton(
                      onPressed: busy
                          ? null
                          : () => review(record, supersede: true),
                      child: const Text('Supersede record'),
                    ),
                ],
              ),
              if (record['replacement_id'] != null)
                SelectableText(
                  'Replaced by knowledge ${record['replacement_id']}',
                ),
              for (final audit in record['audit'] as List? ?? [])
                Text(
                  '${audit['action']} by ${audit['actor']} at ${audit['at']}: ${audit['reason']}',
                ),
            ],
          ),
        ),
      if (before != null)
        TextButton(
          onPressed: busy ? null : () => load(more: true),
          child: const Text('Load older knowledge'),
        ),
    ],
  );
}
