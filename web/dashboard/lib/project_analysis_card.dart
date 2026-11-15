import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'package:herdr_dashboard_state/project_environment.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard_state/project_analysis.dart';
import 'dashboard_bloc.dart';

/// Repository discovery is separate from the release's Herdr build tools.
class ProjectAnalysisCard extends StatefulWidget {
  const ProjectAnalysisCard({super.key, required this.path});
  final String path;
  @override
  State<ProjectAnalysisCard> createState() => _ProjectAnalysisCardState();
}

class _ProjectAnalysisCardState extends State<ProjectAnalysisCard> {
  late final DashboardBloc connection;
  late ProjectAnalysisBloc feature;
  StreamSubscription? featureSubscription;
  ProjectAnalysisLoad? get loaded => feature.state.value('loaded');
  bool editingOverride = false;
  String? get failure =>
      feature.state.errors['analysis'] ??
      (editingOverride ? null : feature.state.errors['override']);
  bool get refreshing => feature.state.reading.contains('analysis');
  bool get saving => feature.state.busy;
  final expanded = <String, bool>{};
  int settingsRequest = 0;
  int repositoryRequest = 0;
  late final ProjectEnvironmentController environment;
  Map<String, dynamic>? get environmentPlan => environment.plan;
  Map<String, dynamic>? get environmentJob => environment.job;
  Map<String, dynamic>? get environmentStatus => environment.status;
  String? get environmentFailure => environment.lookupError;
  bool get environmentBusy => saving || environment.busy || environment.loading;

  @override
  void dispose() {
    featureSubscription?.cancel();
    feature.dispose();
    environment.dispose();
    super.dispose();
  }

  void bindFeature() {
    feature = ProjectAnalysisBloc(connection, widget.path);
    featureSubscription = feature.stream.listen((_) {
      if (mounted) setState(() {});
    });
  }

  void initializeEnvironment() {
    environment = ProjectEnvironmentController(
      request: connection.request,
      onUpdate: () {
        if (mounted) setState(() {});
      },
      mounted: () => mounted && connection.state.connected,
      connectionGeneration: () => connection.generation,
    );
  }

  Future<void> environmentAction(
    String operation, {
    List<String>? checks,
    String? jobId,
  }) async {
    if (!admin || environmentBusy) return;
    if (operation == 'plan')
      setState(() {
        expanded['settings'] = true;
        settingsRequest++;
      });
    await environment.action(operation, checks: checks, jobId: jobId);
  }

  @override
  void initState() {
    super.initState();
    connection = BlocScope.get<DashboardBloc>();
    bindFeature();
    initializeEnvironment();
    refresh();
  }

  @override
  void didUpdateWidget(covariant ProjectAnalysisCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.path != widget.path) {
      environment.reset(widget.path);
      expanded.clear();
      settingsRequest = 0;
      repositoryRequest = 0;
      featureSubscription?.cancel();
      feature.dispose();
      bindFeature();
      refresh();
    }
  }

  bool get admin => connection.operator.value['role'] == 'admin';

  Future<void> refresh({bool force = false}) async {
    final currentFeature = feature;
    final path = widget.path;
    unawaited(environment.load(path));
    final previous = loaded;
    await currentFeature.refresh(force: force);
    if (mounted &&
        identical(currentFeature, feature) &&
        !expanded.containsKey('repository') &&
        !projectEvidenceNeedsAttention(previous?.report ?? {}) &&
        projectEvidenceNeedsAttention(loaded?.report ?? {}))
      setState(() => repositoryRequest++);
  }

  Future<void> editOverride() async {
    setState(() => editingOverride = true);
    final current = loaded?.override ?? {};
    final tool = TextEditingController(text: '${current['toolchain'] ?? ''}');
    final constraint = TextEditingController(
      text: '${current['constraint'] ?? ''}',
    );
    var notation = '${current['notation'] ?? 'semver'}';
    String? error;
    var submitting = false;
    Future<dynamic>? closed;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (context) {
        closed = ModalRoute.of(context)?.completed;
        return StatefulBuilder(
          builder: (context, update) => PopScope(
            canPop: !submitting,
            child: AlertDialog(
              title: const Text('Project requirement override'),
              content: SizedBox(
                width: 460,
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (error != null)
                        Text(
                          error!,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      TextField(
                        controller: tool,
                        enabled: !submitting,
                        decoration: const InputDecoration(
                          labelText: 'Tool (for example php or node)',
                        ),
                      ),
                      TextField(
                        controller: constraint,
                        enabled: !submitting,
                        decoration: const InputDecoration(
                          labelText: 'Version constraint (optional)',
                        ),
                      ),
                      DropdownButtonFormField<String>(
                        initialValue: notation,
                        decoration: const InputDecoration(
                          labelText: 'Version notation',
                        ),
                        items:
                            [
                                  'semver',
                                  'pep440',
                                  'poetry',
                                  'composer',
                                  'pub',
                                  'dotnet',
                                  'gomod',
                                  'rubygems',
                                ]
                                .map(
                                  (value) => DropdownMenuItem(
                                    value: value,
                                    child: Text(value),
                                  ),
                                )
                                .toList(),
                        onChanged: submitting
                            ? null
                            : (value) => update(() => notation = value!),
                      ),
                      const SizedBox(height: 12),
                      const Text(
                        'An operator override supplements discovery. Repository declarations and disagreements remain visible.',
                      ),
                    ],
                  ),
                ),
              ),
              actions: [
                TextButton(
                  onPressed: submitting ? null : () => Navigator.pop(context),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  onPressed: submitting
                      ? null
                      : () async {
                          if (tool.text.trim().isEmpty) {
                            update(() => error = 'Enter a tool identifier.');
                            return;
                          }
                          update(() {
                            submitting = true;
                            error = null;
                          });
                          try {
                            await feature.saveOverride({
                              'toolchain': tool.text.trim(),
                              'constraint': constraint.text.trim(),
                              'notation': notation,
                            });
                            if (context.mounted) Navigator.pop(context);
                          } catch (exception) {
                            if (context.mounted)
                              update(() {
                                error = '$exception';
                                submitting = false;
                              });
                          }
                        },
                  child: Text(submitting ? 'Saving…' : 'Save override'),
                ),
              ],
            ),
          ),
        );
      },
    );
    await closed;
    await feature.clearError('override');
    if (mounted) setState(() => editingOverride = false);
    tool.dispose();
    constraint.dispose();
    await refresh(force: true);
  }

  Future<void> removeOverride() async {
    try {
      await feature.saveOverride({'toolchain': ''});
    } catch (_) {} // The feature retains the backend failure for the card.
  }

  Widget requirement(Map item, {bool sourceOnly = false}) {
    final conflicts = item['conflicts'] as List? ?? [];
    final resolution = item['resolution'] as Map?;
    final state = conflicts.isNotEmpty
        ? 'Conflict'
        : sourceOnly
        ? 'Source only'
        : item['established'] != true
        ? 'Unresolved'
        : 'Declared';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${item['label'] ?? item['tool']}: ${item['constraint'] ?? ''}'
            '${(item['selected'] ?? '') == '' ? '' : ' selected ${item['selected']}'} · $state',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              color: conflicts.isNotEmpty
                  ? Theme.of(context).colorScheme.error
                  : null,
            ),
          ),
          for (final observation in item['observations'] as List? ?? [])
            Text('Evidence: ${observation['file']}#${observation['field']}'),
          if (item['established'] != true)
            Text('${item['reason'] ?? 'No declaration was found.'}'),
          for (final conflict in conflicts) Text('${conflict['reason']}'),
          if (resolution != null) ...[
            Text(
              'SDK lookup: ${(resolution['context'] as Map?)?['directory']} · boundary: ${resolution['boundary']}',
            ),
            Text('${resolution['boundary_note']}'),
            if ((resolution['shadowed'] as List? ?? []).isNotEmpty)
              Text('Shadowed declarations: ${resolution['shadowed']}'),
            if (resolution['reason'] != null) Text('${resolution['reason']}'),
          ],
          if (item['selection_lookup'] is Map)
            Text(
              '${(item['selection_lookup'] as Map)['kind'] == 'rustup' ? 'Rust toolchain' : 'FVM'} lookup: ${(item['selection_lookup'] as Map)['directory']} · '
              '${(item['selection_lookup'] as Map)['evidence']} · boundary: repository root',
            ),
          if (requestedComponentNotice(item).isNotEmpty)
            Text(requestedComponentNotice(item)),
        ],
      ),
    );
  }

  /// Whether this host can provide what the requirement states.
  ///
  /// Discovery answers what the project needs; this answers whether the machine
  /// running Herdr can supply it. They are different questions and a page that
  /// answers only the first leaves the operator to find out by trying a build.
  Widget readiness(Map item) {
    final assessment = loaded?.assessmentOf(item as Map<String, dynamic>) ?? {};
    final verdict = '${assessment['verdict'] ?? 'unknown'}';
    final label = loaded!.readinessLabel(item.cast<String, dynamic>());
    final components = (item['components'] as List? ?? []).join(', ');
    final measured = '${assessment['measured'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${item['label'] ?? item['tool']}: ${item['constraint'] ?? ''} · $label'
            '${item['override'] is Map ? ' · override' : ''}'
            '${assessment['provisionable'] == true ? ' · installable' : ''}',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              color: verdict == 'violated'
                  ? Theme.of(context).colorScheme.error
                  : null,
            ),
          ),
          if (components.isNotEmpty) Text('Component: $components'),
          Text(
            measured.isEmpty
                ? 'No installed version was measured.'
                : 'Measured $measured',
          ),
          Text('${assessment['detail'] ?? ''}'),
          if (assessment['installer'] == true &&
              assessment['provisionable'] != true)
            Text('${(assessment['offer'] as Map?)?['reason'] ?? ''}'),
        ],
      ),
    );
  }

  Widget section(
    String id,
    String title,
    List<Widget> children, {
    bool initiallyOpen = false,
  }) => ExpansionTile(
    key: PageStorageKey(
      'project-environment-${widget.path}-$id-${id == 'settings'
          ? settingsRequest
          : id == 'repository'
          ? repositoryRequest
          : 0}',
    ),
    title: Text(title),
    initiallyExpanded: expanded[id] ?? initiallyOpen,
    onExpansionChanged: (value) => expanded[id] = value,
    childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
    expandedCrossAxisAlignment: CrossAxisAlignment.start,
    children: children,
  );

  @override
  Widget build(BuildContext context) {
    final report = loaded?.report ?? {};
    final coverage = report['coverage'] as Map? ?? {};
    final source = report['source'] as Map? ?? {};
    final components = report['components'] as List? ?? [];
    final requirements = loaded?.requirements ?? const <Map<String, dynamic>>[];
    final sourceNotices = {
      for (final component in components)
        component['path']: sourceComponentNotice(component as Map, report),
    };
    final view = environmentStatus == null
        ? null
        : ProjectEnvironmentView(environmentStatus!);
    return Card(
      child: ExpansionTile(
        key: PageStorageKey('project-environment-${widget.path}'),
        title: const Text('Project environment'),
        subtitle: Text(
          refreshing
              ? 'Analysing repository…'
              : failure != null
              ? 'Analysis unavailable'
              : '${components.length} components · ${coverage['files'] ?? 0} files${coverage['truncated'] == true ? ' · partial scan' : ''}',
        ),
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 340),
            child: SingleChildScrollView(
              key: PageStorageKey('project-environment-scroll-${widget.path}'),
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (failure != null)
                    Text(
                      'Could not analyse this project: $failure. This does not mean no requirements exist.',
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    )
                  else if (loaded != null) ...[
                    if (environment.actionError != null)
                      Text(
                        environment.actionError!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    if (environment.loading)
                      const Text('Loading saved environment status…')
                    else if (environmentFailure != null)
                      Text(
                        'Saved environment status unavailable: $environmentFailure',
                      )
                    else if (view != null)
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(
                          color: Theme.of(
                            context,
                          ).colorScheme.surfaceContainerLow,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: view.current
                                ? Theme.of(context).colorScheme.primary
                                : Theme.of(context).colorScheme.outlineVariant,
                          ),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Icon(
                                  view.current
                                      ? Icons.check_circle_outline
                                      : view.prepared != null
                                      ? Icons.warning_amber_rounded
                                      : Icons.info_outline,
                                  color: view.prepared != null && !view.current
                                      ? Colors.orange
                                      : view.current
                                      ? Theme.of(context).colorScheme.primary
                                      : null,
                                ),
                                const SizedBox(width: 8),
                                Flexible(
                                  child: Text(
                                    view.label,
                                    style: const TextStyle(
                                      fontWeight: FontWeight.bold,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            for (final sdk in view.sdks)
                              Text(
                                sdk,
                                style: Theme.of(context).textTheme.titleMedium,
                              ),
                            if (view.prepared != null) Text(view.checks),
                            Text(view.detail),
                            Text('Commit: ${view.revision}'),
                            if (view.prepared != null && !view.current)
                              Text('Prepared commit: ${view.preparedRevision}'),
                            if (view.pendingPlanDetail != null)
                              Text(view.pendingPlanDetail!),
                          ],
                        ),
                      ),
                    for (final job in environment.activeJobs) ...[
                      Text(environmentJobLabel(job)),
                      Text('Preparation job: ${job['id']}'),
                      if (admin)
                        TextButton(
                          onPressed: environmentBusy
                              ? null
                              : () => environmentAction(
                                  'cancel',
                                  jobId: '${job['id']}',
                                ),
                          child: Text(
                            environment.activeJobs.length == 1
                                ? 'Cancel SDK preparation'
                                : 'Cancel preparation ${job['id']}',
                          ),
                        ),
                    ],
                    if (environmentJob != null &&
                        environmentJob!['state'] != 'ready' &&
                        !environment.activeJobs.any(
                          (job) => job['id'] == environmentJob!['id'],
                        ))
                      Text(environmentJobLabel(environmentJob!)),
                    if (projectEvidenceNeedsAttention(report))
                      const Text(
                        'Repository evidence needs review. Open Repository requirements for the findings.',
                      ),
                    section(
                      'repository',
                      'Repository requirements',
                      [
                        Text(
                          'Source: ${source['scope']}'
                          '${'${source['revision'] ?? ''}'.isEmpty ? '' : ' · ${source['revision']}'}'
                          ' · assessed ${source['at']}',
                        ),
                        Text(
                          'Boundary: ${source['boundary'] ?? 'repository root'} · cache ${source['cache_seconds'] ?? 0}s',
                        ),
                        const Text(repositoryRequirementExplanation),
                        if (components.isEmpty)
                          const Text(
                            'No component was recognised. Review the coverage and unresolved findings below.',
                          ),
                        for (final component in components) ...[
                          const Divider(),
                          Text(
                            '${projectScope(component['path'])}${component['basis'] == 'inferred' ? ' (inferred from source)' : ''}',
                            style: Theme.of(context).textTheme.titleSmall,
                          ),
                          Text(
                            'Languages: ${(component['languages'] as List? ?? []).map((e) => e['label']).join(', ')}',
                          ),
                          if (sourceNotices[component['path']]!.isNotEmpty)
                            Text(sourceNotices[component['path']]!),
                          for (final item
                              in component['requirements'] as List? ?? [])
                            requirement(
                              item as Map,
                              sourceOnly:
                                  sourceNotices[component['path']]!.isNotEmpty,
                            ),
                          if ((component['frameworks'] as List? ?? [])
                              .isNotEmpty)
                            Text(
                              'Frameworks: ${(component['frameworks'] as List).map((e) => e['label']).join(', ')}',
                            ),
                          for (final tool
                              in component['configured_tooling'] as List? ?? [])
                            Text(
                              'Configured tooling: ${tool['id']} (${tool['evidence']})',
                            ),
                          for (final command
                              in component['candidate_commands'] as List? ?? [])
                            Text(
                              'Candidate command: ${(command['command'] as List).join(' ')} (${command['source']})',
                            ),
                          for (final finding in additionalComponentFindings(
                            component,
                          ))
                            Text('${finding['what']}: ${finding['reason']}'),
                        ],
                        for (final finding
                            in report['unresolved'] as List? ?? [])
                          Text('${finding['what']}: ${finding['reason']}'),
                        Text(
                          'Scanned ${coverage['files'] ?? 0} files in ${coverage['directories'] ?? 0} directories · '
                          '${coverage['unknown'] ?? 0} unrecognised${coverage['truncated'] == true ? ' · scan incomplete' : ''}',
                        ),
                        if ((coverage['unknown'] ?? 0) > 0)
                          ExpansionTile(
                            key: PageStorageKey(
                              'project-environment-unknown-${widget.path}',
                            ),
                            title: const Text('Unrecognised files'),
                            subtitle: Text(
                              'Showing ${(coverage['unknown_evidence'] as List? ?? []).length} of ${coverage['unknown']} paths.',
                            ),
                            children: [
                              for (final path
                                  in coverage['unknown_evidence'] as List? ??
                                      [])
                                ListTile(
                                  title: SelectableText(
                                    '$path',
                                    key: PageStorageKey(
                                      'project-environment-file-${widget.path}-$path',
                                    ),
                                  ),
                                ),
                            ],
                          ),
                        if (loaded?.override != null)
                          Text(
                            'Operator override: ${loaded!.override!['toolchain']} '
                            '${loaded!.override!['constraint']} [${loaded!.override!['notation']}] · by ${loaded!.override!['actor']}',
                          ),
                      ],
                      initiallyOpen: projectEvidenceNeedsAttention(report),
                    ),
                    if (requirements.isNotEmpty)
                      section('host', 'Host SDK discovery', [
                        const Text(hostSdkExplanation),
                        for (final item in requirements) readiness(item),
                        Text(projectInstallerSummary(report)),
                      ]),
                  ],
                  Wrap(
                    spacing: 8,
                    children: [
                      if (admin)
                        TextButton(
                          onPressed: environmentBusy || refreshing
                              ? null
                              : () => environmentAction('plan'),
                          child: const Text('Plan environment'),
                        ),
                      TextButton.icon(
                        onPressed: refreshing
                            ? null
                            : () {
                                setState(() {});
                                refresh(force: true);
                              },
                        icon: const Icon(Icons.refresh),
                        label: const Text('Re-analyse'),
                      ),
                      if (admin)
                        TextButton(
                          onPressed: refreshing || saving ? null : editOverride,
                          child: const Text('Add override'),
                        ),
                      if (admin && loaded?.override != null)
                        TextButton(
                          onPressed: refreshing || saving
                              ? null
                              : removeOverride,
                          child: const Text('Remove override'),
                        ),
                    ],
                  ),
                  if (environmentPlan != null)
                    section(
                      'settings',
                      'Environment settings',
                      [
                        const Text(
                          'Approve validation checks before preparing. These commands can execute project code.',
                        ),
                        if ((environmentPlan!['requirements'] as List? ?? [])
                            .any(
                              (item) =>
                                  (item['restore_recipes'] as List? ?? []).any(
                                    (recipe) =>
                                        recipe['requires_restore'] == true,
                                  ),
                            ))
                          CheckboxListTile(
                            title: const Text('Approve dependency restoration'),
                            subtitle: const Text(
                              'Separate from SDK installation. May execute project build code.',
                            ),
                            value:
                                environmentPlan!['restore_permission']?['allowed'] ==
                                true,
                            onChanged: environmentBusy || !admin
                                ? null
                                : (_) => environmentAction('authorize-restore'),
                          ),
                        for (final check
                            in environmentPlan!['check_candidates'] as List? ??
                                [])
                          CheckboxListTile(
                            title: Text(
                              '${projectScope(check['cwd'])}: ${(check['arguments'] as List).join(' ')} (${recipeEvidenceLabel(check as Map)})',
                            ),
                            value:
                                (environmentPlan!['selected_checks'] as List? ??
                                        [])
                                    .any((item) => item['id'] == check['id']),
                            onChanged: environmentBusy || !admin
                                ? null
                                : (selected) {
                                    final ids = [
                                      for (final item
                                          in environmentPlan!['selected_checks']
                                                  as List? ??
                                              [])
                                        '${item['id']}',
                                    ];
                                    if (selected == true) {
                                      ids.add('${check['id']}');
                                    } else {
                                      ids.remove('${check['id']}');
                                    }
                                    environmentAction('select', checks: ids);
                                  },
                          ),
                        for (final item
                            in environmentPlan!['requirements'] as List? ?? [])
                          Text(
                            preparationRequirementSummary(
                              item as Map,
                              environmentPlan!,
                              environmentJob,
                            ),
                          ),
                        if (environmentPlanCanPrepare(environmentPlan) &&
                            ![
                              'queued',
                              'running',
                              'ready',
                            ].contains(environmentJob?['state']))
                          TextButton(
                            onPressed: environmentBusy || !admin
                                ? null
                                : () => environmentAction('prepare'),
                            child: const Text('Prepare SDKs'),
                          ),
                      ],
                      initiallyOpen:
                          view?.current != true ||
                          environmentStatus?['pending_plan'] == true,
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
