import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'package:herdr_dashboard_state/project_environment.dart';
import 'package:herdr_dashboard_state/project_analysis.dart';
import 'operations_support.dart';
import 'ops_ui.dart';
import 'org_forms.dart' show editRecord;

/// What the selected project needs, discovered from the repository itself.
///
/// Nothing here asks the operator for a tool name. Requirements come from
/// manifests, with their evidence shown; anything the analysis could not settle
/// is listed as unresolved rather than left out. An operator override exists,
/// but as a supplement for what discovery cannot establish, not as the entry
/// point.
class ProjectAnalysisCard extends StatefulComponent {
  const ProjectAnalysisCard({super.key, required this.path});
  final String path;
  @override
  State<ProjectAnalysisCard> createState() => _ProjectAnalysisState();
}

class _ProjectAnalysisState extends OperationsState<ProjectAnalysisCard> {
  late ProjectAnalysisBloc feature;
  StreamSubscription? featureSubscription;
  ProjectAnalysisLoad? get loaded => feature.state.value('loaded');
  bool editingOverride = false;
  String? get failure => feature.state.errors['analysis'];
  bool get refreshing => feature.state.reading.contains('analysis');
  bool get busy => feature.state.busy;
  String? get error =>
      editingOverride ? null : feature.state.errors['override'];
  String? get message => feature.state.message;
  Map<String, dynamic> get analysis => loaded?.report ?? const {};
  final expanded = <String, bool>{};
  late final ProjectEnvironmentController environment;
  Map<String, dynamic>? get environmentPlan => environment.plan;
  Map<String, dynamic>? get environmentJob => environment.job;
  Map<String, dynamic>? get environmentStatus => environment.status;
  String? get environmentFailure => environment.lookupError;
  bool get environmentBusy => busy || environment.busy || environment.loading;

  @override
  void didUpdateComponent(ProjectAnalysisCard old) {
    super.didUpdateComponent(old);
    if (old.path != component.path) {
      environment.reset(component.path);
      expanded.clear();
      featureSubscription?.cancel();
      feature.dispose();
      bindFeature();
      refresh();
    }
  }

  @override
  void dispose() {
    featureSubscription?.cancel();
    feature.dispose();
    environment.dispose();
    super.dispose();
  }

  void bindFeature() {
    feature = ProjectAnalysisBloc(connection, component.path);
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
    if (operation == 'plan') setState(() => expanded['settings'] = true);
    await environment.action(operation, checks: checks, jobId: jobId);
  }

  List<Map<String, dynamic>> get components =>
      (analysis['components'] as List? ?? []).cast<Map<String, dynamic>>();
  Map<String, dynamic> get coverage =>
      (analysis['coverage'] as Map?)?.cast<String, dynamic>() ?? {};
  List<Map<String, dynamic>> get unresolved =>
      (analysis['unresolved'] as List? ?? []).cast<Map<String, dynamic>>();
  List<Map<String, dynamic>> get requirements =>
      loaded?.requirements ?? const [];
  Map<String, dynamic>? get operatorOverride => loaded?.override;
  Map<String, dynamic>? get overriddenEntry => loaded?.overridden;

  @override
  void initState() {
    super.initState();
    bindFeature();
    initializeEnvironment();
    refresh();
  }

  Future<void> refresh({bool force = false}) async {
    final currentFeature = feature;
    final path = component.path;
    unawaited(environment.load(path));
    await currentFeature.refresh(force: force);
  }

  Component statePill(
    Map<String, dynamic> requirement, {
    bool sourceOnly = false,
  }) {
    if (requirement['conflicts'] is List &&
        (requirement['conflicts'] as List).isNotEmpty)
      return pill('Conflict', tone: 'danger');
    if (sourceOnly) return pill('Source only');
    if (requirement['established'] != true) return pill('Unresolved');
    if ((requirement['constraint'] ?? '') != '')
      return pill('Required', tone: 'info');
    if ((requirement['selected'] ?? '') != '')
      return pill('Selected', tone: 'info');
    return pill('Declared');
  }

  Component requirementRow(
    Map<String, dynamic> requirement, {
    bool sourceOnly = false,
  }) {
    final constraint = '${requirement['constraint'] ?? ''}';
    final selected = '${requirement['selected'] ?? ''}';
    final observations = (requirement['observations'] as List? ?? [])
        .cast<Map>()
        .map(
          (entry) =>
              '${entry['file']}'
              '${'${entry['field'] ?? ''}'.isEmpty ? '' : '#${entry['field']}'}',
        )
        .toList();
    return el('div', [
      el('div', [
        el('strong', [text('${requirement['label'] ?? requirement['tool']}')]),
        if (constraint.isNotEmpty) mono(constraint),
        if (selected.isNotEmpty && selected != constraint)
          el('span', [text('selected $selected')], classes: 'org-note'),
        statePill(requirement, sourceOnly: sourceOnly),
      ], classes: 'toolchain-name'),
      if (observations.isNotEmpty)
        meta('Evidence: ${observations.join(', ')}', classes: 'org-note'),
      if (requirement['established'] != true)
        meta(
          '${requirement['reason'] ?? 'No declaration was found.'}',
          classes: 'org-note',
        ),
      if (requirement['resolution'] is Map) ...[
        meta(
          'SDK lookup: ${((requirement['resolution'] as Map)['context'] as Map)['directory']} · boundary: repository root',
        ),
        meta('${(requirement['resolution'] as Map)['boundary_note']}'),
        if (((requirement['resolution'] as Map)['shadowed'] as List? ?? [])
            .isNotEmpty)
          meta(
            'Shadowed declarations: ${(requirement['resolution'] as Map)['shadowed']}',
          ),
        if ((requirement['resolution'] as Map)['reason'] != null)
          meta('${(requirement['resolution'] as Map)['reason']}'),
      ],
      if (requirement['selection_lookup'] is Map)
        meta(
          '${(requirement['selection_lookup'] as Map)['kind'] == 'rustup' ? 'Rust toolchain' : 'FVM'} lookup: ${(requirement['selection_lookup'] as Map)['directory']} · '
          '${(requirement['selection_lookup'] as Map)['evidence']} · boundary: repository root',
        ),
      if (requestedComponentNotice(requirement).isNotEmpty)
        meta(requestedComponentNotice(requirement)),
      for (final conflict in (requirement['conflicts'] as List? ?? []))
        callout([
          calloutText('${(conflict as Map)['reason']}'),
        ], tone: 'warning'),
    ], classes: 'org-form__item');
  }

  String readinessTone(String verdict) => switch (verdict) {
    'satisfied' => 'success',
    'violated' => 'warning',
    _ => 'neutral',
  };

  /// Whether this host can provide what the requirement states.
  ///
  /// Discovery answers what the project needs; this answers whether the machine
  /// running Herdr can supply it. They are different questions and a page that
  /// answers only the first leaves the operator to find out by trying a build.
  Component readinessRow(Map<String, dynamic> requirement) {
    final assessment = loaded!.assessmentOf(requirement);
    final verdict = '${assessment['verdict'] ?? 'unknown'}';
    final label = loaded!.readinessLabel(requirement);
    final component = (requirement['components'] as List? ?? []).join(', ');
    return el('div', [
      el('div', [
        el('strong', [text('${requirement['label'] ?? requirement['tool']}')]),
        if ('${requirement['constraint'] ?? ''}'.isNotEmpty)
          mono('${requirement['constraint']}'),
        pill(
          label,
          tone: label == verdict ? readinessTone(verdict) : 'neutral',
        ),
        if (requirement['override'] is Map) pill('Override', tone: 'info'),
        if (assessment['provisionable'] == true)
          pill('Installable', tone: 'info'),
      ], classes: 'toolchain-name'),
      if (component.isNotEmpty)
        meta('Component: $component', classes: 'org-note'),
      meta(
        '${'${assessment['measured'] ?? ''}'.isEmpty ? 'No installed version was measured.' : 'Measured ${assessment['measured']}'}'
        '${'${assessment['selected'] ?? ''}'.isEmpty ? '' : ' · ${assessment['selected']}'}',
        classes: 'org-note',
      ),
      meta('${assessment['detail'] ?? ''}', classes: 'org-note'),
      if (assessment['installer'] == true &&
          assessment['provisionable'] != true)
        meta(
          '${(assessment['offer'] as Map?)?['reason'] ?? ''}',
          classes: 'org-note',
        ),
    ], classes: 'org-form__item');
  }

  Component componentBlock(Map<String, dynamic> item) {
    final sourceNotice = sourceComponentNotice(item, analysis);
    final ecosystems = (item['ecosystems'] as List? ?? [])
        .map((entry) => '${(entry as Map)['label']}')
        .join(', ');
    final languages = (item['languages'] as List? ?? []).map(
      (entry) => '${(entry as Map)['label']}',
    );
    return el('section', [
      if (sourceNotice.isNotEmpty) meta(sourceNotice, classes: 'org-note'),
      el('h3', [
        text(
          '${projectScope(item['path'])}'
          '${item['basis'] == 'inferred' ? ' (inferred from source)' : ''}',
        ),
      ], classes: 'overline'),
      meta(
        '$ecosystems · ${languages.isEmpty ? 'no languages' : languages.join(', ')}',
        classes: 'org-note',
      ),
      for (final requirement
          in (item['requirements'] as List? ?? []).cast<Map<String, dynamic>>())
        requirementRow(requirement, sourceOnly: sourceNotice.isNotEmpty),
      if ((item['frameworks'] as List? ?? []).isNotEmpty)
        meta(
          'Frameworks: '
          '${(item['frameworks'] as List).map((entry) => (entry as Map)['label']).join(', ')}',
          classes: 'org-note',
        ),
      for (final tool
          in (item['configured_tooling'] as List? ?? []).whereType<Map>())
        meta(
          'Configured tooling: ${tool['id']} (${tool['evidence']})',
          classes: 'org-note',
        ),
      for (final command
          in (item['candidate_commands'] as List? ?? []).cast<Map>())
        meta(
          'Candidate command: ${command['command'] is List ? (command['command'] as List).join(' ') : command['command']}'
          ' (${command['source'] ?? 'convention'})',
          classes: 'org-note',
        ),
      for (final finding in additionalComponentFindings(item))
        meta('Unresolved: ${finding['reason']}', classes: 'org-note'),
    ], classes: 'org-policy__group');
  }

  Future<void> setOverride() async {
    if (!admin || busy) return;
    setState(() => editingOverride = true);
    final current = Map<String, dynamic>.from(operatorOverride ?? {});
    await editRecord(
      'Project toolchain override',
      {
        'toolchain': current['toolchain'] ?? '',
        'constraint': current['constraint'] ?? '',
        'notation': current['notation'] ?? 'semver',
      },
      {
        'toolchain': 'Required tool (for example php or node)',
        'constraint': 'Version or range the project needs (optional)',
        'notation': 'How to read the constraint',
      },
      required: {'toolchain'},
      choices: const {
        'notation': {
          'semver': 'semver (npm, Cargo: ^1.2.3, >=20 <23)',
          'pep440': 'PEP 440 (Python: ~=3.8.1, >=3.10)',
          'poetry': 'Poetry (Python: ^3.11, ~3.11)',
          'composer': 'Composer (PHP: ^8.2)',
          'pub': 'pub (Dart: ^3.5.0)',
          'dotnet': '.NET (>=8.0.0)',
          'gomod': 'go modules (>=1.22)',
          'rubygems': 'RubyGems (~> 2.2)',
        },
      },
      submit: (values) async {
        await feature.saveOverride({
          'toolchain': values['toolchain'],
          'constraint': values['constraint'],
          'notation': values['notation'],
        });
      },
      extra: (_) => [
        meta(
          'An override supplements discovery and is read by the same evaluator as a discovered '
          'requirement. It is recorded with its author and time, and never replaces what the '
          'repository itself declares.',
        ),
      ],
    );
    await feature.clearError('override');
    if (mounted) setState(() => editingOverride = false);
    await refresh(force: true);
  }

  Future<void> clearOverride() async {
    if (!admin || busy) return;
    try {
      if (await feature.saveOverride({'toolchain': ''}) == true)
        await feature.patch({}, message: 'Override removed.');
    } catch (_) {} // Failure remains in the feature's operation state.
  }

  Component projectEnvironment() {
    if (environment.loading) return meta('Loading saved environment status…');
    if (environmentFailure != null) {
      return meta('Saved environment status unavailable: $environmentFailure');
    }
    if (environmentStatus == null)
      return meta('Loading saved environment status…');
    final view = ProjectEnvironmentView(environmentStatus!);
    return callout(
      [
        for (final sdk in view.sdks)
          el('p', [
            el('strong', [text(sdk)]),
          ], classes: 'callout__text'),
        if (view.prepared != null) calloutText(view.checks),
        calloutText(view.detail),
        calloutText('Commit: ${view.revision}'),
        if (view.prepared != null && !view.current)
          calloutText('Prepared commit: ${view.preparedRevision}'),
        if (view.pendingPlanDetail != null) calloutText(view.pendingPlanDetail),
      ],
      title: view.label,
      tone: view.current
          ? 'success'
          : view.prepared != null
          ? 'warning'
          : 'neutral',
    );
  }

  bool get preparedCurrent =>
      environmentStatus != null &&
      ProjectEnvironmentView(environmentStatus!).current;

  Component section(
    String id,
    String label,
    List<Component> children, {
    bool initiallyOpen = false,
  }) {
    final open = expanded[id] ?? initiallyOpen;
    return disclosureElement(
      [
        el(
          'summary',
          [
            el('span', [text(label)], classes: 'org-disclosure__title'),
            icon('chevron-down', classes: 'icon--sm disclosure-chevron'),
          ],
          classes: 'disclosure-summary',
          events: {
            'click': (event) {
              if (!mounted) return;
              event.preventDefault();
              setState(() => expanded[id] = !(expanded[id] ?? initiallyOpen));
            },
          },
        ),
        el('div', children, classes: 'org-disclosure__body'),
      ],
      key: ValueKey('environment-${component.path}-$id'),
      classes: 'org-disclosure',
      attributes: {'data-environment-section': id},
      open: open,
    );
  }

  @override
  Component build(BuildContext context) => card(
    'Project environment',
    [
      if (failure != null)
        callout(
          [
            calloutText(
              'Could not analyse this project: $failure. This is a failed lookup, not an absence '
              'of requirements.',
            ),
          ],
          tone: 'danger',
          iconName: 'triangle-alert',
        )
      else ...[
        if (environment.actionError != null)
          meta(environment.actionError, classes: 'error'),
        projectEnvironment(),
        for (final job in environment.activeJobs)
          el('section', [
            meta(environmentJobLabel(job)),
            meta('Preparation job: ${job['id']}', classes: 'mono'),
            if (admin)
              button(
                environment.activeJobs.length == 1
                    ? 'Cancel SDK preparation'
                    : 'Cancel preparation ${job['id']}',
                () => environmentAction('cancel', jobId: '${job['id']}'),
                disabled: environmentBusy,
              ),
          ]),
        if (environmentJob != null &&
            environmentJob!['state'] != 'ready' &&
            !environment.activeJobs.any(
              (job) => job['id'] == environmentJob!['id'],
            ))
          meta(environmentJobLabel(environmentJob!)),
        if (projectEvidenceNeedsAttention(analysis))
          callout([
            calloutText(
              'Repository evidence needs review. Open Repository requirements for the findings.',
            ),
          ], tone: 'warning'),
        section(
          'repository',
          'Repository requirements',
          [
            meta(
              'Source: ${(analysis['source'] as Map?)?['scope'] ?? 'unknown'}'
              '${'${(analysis['source'] as Map?)?['revision'] ?? ''}'.isEmpty ? '' : ' · ${(analysis['source'] as Map)['revision']}'}'
              ' · assessed ${(analysis['source'] as Map?)?['at'] ?? 'unknown'}',
            ),
            meta(
              'Boundary: ${(analysis['source'] as Map?)?['boundary'] ?? 'repository root'} · cache ${(analysis['source'] as Map?)?['cache_seconds'] ?? 0}s',
            ),
            meta(repositoryRequirementExplanation, classes: 'org-note'),
            for (final item in components) componentBlock(item),
            if (components.isEmpty)
              emptyState(
                'No component was recognised here. The coverage below says what was seen.',
                iconName: 'search',
              ),
            for (final finding in unresolved)
              callout([
                calloutText(
                  '${finding['what']}: ${finding['reason']}'
                  '${'${finding['evidence'] ?? ''}'.isEmpty ? '' : ' (${finding['evidence']})'}',
                ),
              ], tone: 'neutral'),
            meta(
              'Scanned ${coverage['files'] ?? 0} files in ${coverage['directories'] ?? 0} directories'
              '${coverage['truncated'] == true ? ' (partial: a limit stopped the scan)' : ''}'
              '${(coverage['unknown'] ?? 0) == 0 ? '' : ' · ${coverage['unknown']} unrecognised'}',
            ),
            if ((coverage['unknown'] ?? 0) > 0)
              el('details', [
                el('summary', [
                  text('Unrecognised files'),
                  el('span', [
                    text(
                      ' · Showing ${(coverage['unknown_evidence'] as List? ?? []).length} of ${coverage['unknown']} paths.',
                    ),
                  ], classes: 'org-disclosure__hint'),
                ]),
                for (final path in coverage['unknown_evidence'] as List? ?? [])
                  meta(path, classes: 'mono'),
              ]),
            if (operatorOverride != null)
              meta(
                'Operator override: ${operatorOverride!['toolchain']} '
                '${operatorOverride!['constraint'] ?? ''} '
                '[${operatorOverride!['notation'] ?? 'semver'}]'
                '${(overriddenEntry == null ? null : loaded!.assessmentOf(overriddenEntry!))?['provisionable'] == true ? ' · installation available' : ' · no satisfying installer'}'
                ' · by ${operatorOverride!['actor']}.',
              ),
          ],
          initiallyOpen: projectEvidenceNeedsAttention(analysis),
        ),
        if (requirements.isNotEmpty)
          section('host', 'Host SDK discovery', [
            meta(hostSdkExplanation, classes: 'org-note'),
            for (final requirement in requirements) readinessRow(requirement),
            meta(projectInstallerSummary(analysis), classes: 'org-note'),
          ]),
      ],
      if (!admin)
        meta('Only an administrator can add an override.', iconName: 'lock'),
      if (environmentPlan != null)
        section(
          'settings',
          'Environment settings',
          [
            meta(
              'Approve validation checks before preparing. These commands can execute project code.',
            ),
            if ((environmentPlan!['requirements'] as List? ?? []).any(
              (item) => (item['restore_recipes'] as List? ?? []).any(
                (recipe) => recipe['requires_restore'] == true,
              ),
            )) ...[
              meta(
                'Dependency restoration is separate from SDK installation and may execute project build code.',
              ),
              button(
                environmentPlan!['restore_permission']?['allowed'] == true
                    ? 'Revoke dependency restoration'
                    : 'Approve dependency restoration',
                () => environmentAction('authorize-restore'),
                disabled: environmentBusy || !admin,
              ),
            ],
            for (final check
                in environmentPlan!['check_candidates'] as List? ?? [])
              button(
                '${(environmentPlan!['selected_checks'] as List? ?? []).any((item) => item['id'] == check['id']) ? 'Deselect' : 'Approve'} ${projectScope(check['cwd'])}: ${(check['arguments'] as List).join(' ')} (${recipeEvidenceLabel(check as Map)})',
                () {
                  final ids = [
                    for (final item
                        in environmentPlan!['selected_checks'] as List? ?? [])
                      '${item['id']}',
                  ];
                  if (ids.contains('${check['id']}')) {
                    ids.remove('${check['id']}');
                  } else {
                    ids.add('${check['id']}');
                  }
                  environmentAction('select', checks: ids);
                },
                disabled: environmentBusy || !admin,
              ),
            for (final item in environmentPlan!['requirements'] as List? ?? [])
              meta(
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
              button(
                'Prepare SDKs',
                () => environmentAction('prepare'),
                disabled: environmentBusy || !admin,
              ),
          ],
          initiallyOpen:
              !preparedCurrent || environmentStatus?['pending_plan'] == true,
        ),
      feedback(),
    ],
    iconName: 'search',
    subtitle: 'Prepared SDKs and approved validation checks for this project.',
    actions: [
      if (admin)
        button(
          'Plan environment',
          () => environmentAction('plan'),
          disabled: environmentBusy || refreshing,
          small: true,
        ),
      button(
        'Re-analyse',
        () => refresh(force: true),
        disabled: refreshing,
        small: true,
        variant: 'ghost',
        iconName: 'refresh-cw',
      ),
      button(
        'Add override',
        setOverride,
        disabled: !admin || busy,
        small: true,
        iconName: 'pencil',
      ),
      if (operatorOverride != null)
        button(
          'Remove override',
          clearOverride,
          disabled: !admin || busy,
          small: true,
          variant: 'ghost',
          iconName: 'trash-2',
        ),
    ],
    classes: 'toolchain-card project-analysis-card',
  );
}
