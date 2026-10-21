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
  ProjectAnalysisLoad? loaded;
  String? failure;
  bool refreshing = false;
  bool saving = false;
  int requestId = 0;
  Map<String, dynamic>? environmentPlan;
  Map<String, dynamic>? environmentJob;
  Timer? environmentTimer;
  int environmentGeneration = 0;

  @override
  void dispose() {
    environmentTimer?.cancel();
    super.dispose();
  }

  Future<void> environmentAction(
    String operation, {
    List<String>? checks,
  }) async {
    if (!admin || saving) return;
    final epoch = connection.generation;
    final path = widget.path;
    final generation = ++environmentGeneration;
    bool current() =>
        mounted &&
        connection.generation == epoch &&
        widget.path == path &&
        generation == environmentGeneration;
    setState(() {
      saving = true;
      failure = null;
    });
    try {
      final result = Map<String, dynamic>.from(
        await connection.request(
              'projects/environment/$operation',
              switch (operation) {
                'plan' => {'path': path},
                'cancel' => {'id': environmentJob!['id']},
                'authorize-restore' => {
                  'digest': environmentPlan!['digest'],
                  'allowed':
                      environmentPlan!['restore_permission']?['allowed'] !=
                      true,
                },
                'select' => {
                  'digest': environmentPlan!['digest'],
                  'checks': checks,
                },
                _ => {'digest': environmentPlan!['digest'], 'retry': true},
              },
            )
            as Map,
      );
      if (!current()) return;
      setState(() {
        if (operation == 'plan' ||
            operation == 'select' ||
            operation == 'authorize-restore') {
          environmentPlan = result;
          environmentJob = null;
        } else {
          environmentJob = result;
        }
      });
      environmentTimer?.cancel();
      if (['queued', 'running'].contains(environmentJob?['state'])) {
        var polling = false;
        environmentTimer = Timer.periodic(const Duration(seconds: 2), (
          timer,
        ) async {
          if (!current()) {
            timer.cancel();
            return;
          }
          if (polling) return;
          polling = true;
          try {
            final job = Map<String, dynamic>.from(
              await connection.request(
                    'projects/environment?job=${environmentJob!['id']}',
                  )
                  as Map,
            );
            if (!current()) return;
            setState(() => environmentJob = job);
            if (!['queued', 'running'].contains(job['state'])) timer.cancel();
          } catch (error) {
            timer.cancel();
            if (current()) setState(() => failure = '$error');
          } finally {
            polling = false;
          }
        });
      }
    } catch (error) {
      if (current()) setState(() => failure = '$error');
    } finally {
      if (current()) setState(() => saving = false);
    }
  }

  @override
  void initState() {
    super.initState();
    connection = BlocScope.get<DashboardBloc>();
    refresh();
  }

  @override
  void didUpdateWidget(covariant ProjectAnalysisCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.path != widget.path) {
      environmentTimer?.cancel();
      environmentGeneration++;
      environmentPlan = environmentJob = null;
      saving = false;
      requestId++;
      loaded = null;
      failure = null;
      refreshing = false;
      refresh();
    }
  }

  bool get admin => connection.operator.value['role'] == 'admin';

  Future<void> refresh({bool force = false}) async {
    if (refreshing || !connection.state.connected) return;
    refreshing = true;
    final epoch = connection.generation;
    final current = ++requestId;
    try {
      final result = await loadProjectAnalysis(
        connection.request,
        widget.path,
        force: force,
      );
      if (mounted && epoch == connection.generation && current == requestId) {
        setState(() {
          loaded = result;
          failure = null;
        });
      }
    } catch (error) {
      if (mounted && epoch == connection.generation && current == requestId)
        setState(() => failure = '$error');
    } finally {
      if (mounted && epoch == connection.generation && current == requestId)
        setState(() => refreshing = false);
    }
  }

  Future<void> editOverride() async {
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
                            await connection.request('projects/toolchain', {
                              'repository': widget.path,
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
    tool.dispose();
    constraint.dispose();
    await refresh(force: true);
  }

  Future<void> removeOverride() async {
    setState(() => saving = true);
    try {
      await connection.request('projects/toolchain', {
        'repository': widget.path,
        'toolchain': '',
      });
      await refresh(force: true);
    } catch (error) {
      if (mounted) setState(() => failure = '$error');
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Widget requirement(Map item) {
    final conflicts = item['conflicts'] as List? ?? [];
    final resolution = item['resolution'] as Map?;
    final state = conflicts.isNotEmpty
        ? 'Conflict'
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
              color: verdict == 'violated' || verdict == 'missing'
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

  @override
  Widget build(BuildContext context) {
    final report = loaded?.report ?? {};
    final coverage = report['coverage'] as Map? ?? {};
    final source = report['source'] as Map? ?? {};
    final components = report['components'] as List? ?? [];
    final requirements = loaded?.requirements ?? const <Map<String, dynamic>>[];
    final sourceNotices = {
      for (final component in components)
        component['path']: sourceComponentNotice(
          component as Map,
          report,
          plan: environmentPlan,
        ),
    };
    return Card(
      child: ExpansionTile(
        title: const Text('Discovered requirements'),
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
                    Text(
                      'Source: ${source['scope']}'
                      '${'${source['revision'] ?? ''}'.isEmpty ? '' : ' · ${source['revision']}'}'
                      ' · assessed ${source['at']}',
                    ),
                    Text(
                      'Boundary: ${source['boundary'] ?? 'repository root'} · cache ${source['cache_seconds'] ?? 0}s',
                    ),
                    if (requirements.isNotEmpty) ...[
                      const Divider(),
                      Text(
                        'Host readiness',
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                      Text(
                        'Whether this machine can provide each requirement. A measured version '
                        'is not a verdict; only the evaluator decides satisfaction.',
                      ),
                      for (final item in requirements) readiness(item),
                      Text(projectInstallerSummary(report)),
                    ],
                    if (components.isEmpty)
                      const Text(
                        'No component was recognised. Review the coverage and unresolved findings below.',
                      ),
                    for (final component in components) ...[
                      const Divider(),
                      Text(
                        '${component['path']}${component['basis'] == 'inferred' ? ' (inferred from source)' : ''}',
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                      Text(
                        'Languages: ${(component['languages'] as List? ?? []).map((e) => e['label']).join(', ')}',
                      ),
                      if (sourceNotices[component['path']]!.isNotEmpty)
                        Text(sourceNotices[component['path']]!),
                      for (final item
                          in component['requirements'] as List? ?? [])
                        requirement(item as Map),
                      if ((component['frameworks'] as List? ?? []).isNotEmpty)
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
                    for (final finding in report['unresolved'] as List? ?? [])
                      Text('${finding['what']}: ${finding['reason']}'),
                    Text(
                      'Scanned ${coverage['files']} files in ${coverage['directories']} directories · '
                      '${coverage['unknown'] ?? 0} unrecognised${coverage['truncated'] == true ? ' · scan incomplete' : ''}',
                    ),
                    if ((coverage['unknown'] ?? 0) > 0)
                      ExpansionTile(
                        title: const Text('Unrecognised files'),
                        subtitle: Text(
                          'Showing ${(coverage['unknown_evidence'] as List? ?? []).length} of ${coverage['unknown']} paths.',
                        ),
                        children: [
                          for (final path
                              in coverage['unknown_evidence'] as List? ?? [])
                            ListTile(title: SelectableText('$path')),
                        ],
                      ),
                    if (loaded?.override != null)
                      Text(
                        'Operator override: ${loaded!.override!['toolchain']} '
                        '${loaded!.override!['constraint']} [${loaded!.override!['notation']}] · by ${loaded!.override!['actor']}',
                      ),
                  ],
                  Wrap(
                    spacing: 8,
                    children: [
                      if (admin)
                        TextButton(
                          onPressed: saving || refreshing
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
                  if (environmentPlan != null) ...[
                    const Text('Committed SDK preparation'),
                    const Text(
                      'Approve validation checks before preparing. These commands can execute project code.',
                    ),
                    if ((environmentPlan!['requirements'] as List? ?? []).any(
                      (item) => (item['restore_recipes'] as List? ?? []).any(
                        (recipe) => recipe['requires_restore'] == true,
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
                        onChanged: saving || !admin
                            ? null
                            : (_) => environmentAction('authorize-restore'),
                      ),
                    for (final check
                        in environmentPlan!['check_candidates'] as List? ?? [])
                      CheckboxListTile(
                        title: Text(
                          '${check['cwd']}: ${(check['arguments'] as List).join(' ')} (${recipeEvidenceLabel(check as Map)})',
                        ),
                        value:
                            (environmentPlan!['selected_checks'] as List? ?? [])
                                .any((item) => item['id'] == check['id']),
                        onChanged: saving || !admin
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
                        '${item['components']}: ${item['capability']} ${item['version']} · ${item['state']} · ${item['reason']} · Activation: ${item['activation'] ?? 'unavailable'} ${item['activation_reason'] ?? ''}',
                      ),
                    if (environmentJob != null)
                      Text(environmentJobLabel(environmentJob!)),
                    if (environmentPlanCanPrepare(environmentPlan) &&
                        ![
                          'queued',
                          'running',
                          'ready',
                        ].contains(environmentJob?['state']))
                      TextButton(
                        onPressed: saving || !admin
                            ? null
                            : () => environmentAction('prepare'),
                        child: const Text('Prepare SDKs'),
                      ),
                    if ([
                      'queued',
                      'running',
                    ].contains(environmentJob?['state']))
                      TextButton(
                        onPressed: saving || !admin
                            ? null
                            : () => environmentAction('cancel'),
                        child: const Text('Cancel SDK preparation'),
                      ),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
