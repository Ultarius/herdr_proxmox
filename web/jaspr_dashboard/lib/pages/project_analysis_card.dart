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
  ProjectAnalysisLoad? loaded;
  Map<String, dynamic> analysis = const {};
  String? failure;
  bool refreshing = false;
  Map<String, dynamic>? environmentPlan;
  Map<String, dynamic>? environmentJob;
  Timer? environmentTimer;
  int environmentGeneration = 0;

  @override
  void didUpdateComponent(ProjectAnalysisCard old) {
    super.didUpdateComponent(old);
    if (old.path != component.path) {
      environmentGeneration++;
      environmentTimer?.cancel();
      environmentPlan = environmentJob = null;
      busy = refreshing = false;
      loaded = null;
      analysis = const {};
      failure = error = null;
      refresh();
    }
  }

  @override
  void dispose() {
    environmentTimer?.cancel();
    super.dispose();
  }

  Future<void> environmentAction(
    String operation, {
    List<String>? checks,
  }) async {
    if (!admin || busy) return;
    final generation = ++environmentGeneration;
    final path = component.path;
    await perform((epoch) async {
      bool current() =>
          valid(epoch) &&
          generation == environmentGeneration &&
          component.path == path;
      final result = Map<String, dynamic>.from(
        await connection.request(
              'projects/environment/$operation',
              switch (operation) {
                'plan' => {'path': component.path},
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
          } catch (failure) {
            timer.cancel();
            if (current()) setState(() => error = '$failure');
          } finally {
            polling = false;
          }
        });
      }
    });
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
    refresh();
  }

  Future<void> refresh({bool force = false}) async {
    if (refreshing || !connection.state.connected) return;
    refreshing = true;
    final epoch = connection.generation;
    final path = component.path;
    try {
      final result = await loadProjectAnalysis(
        connection.request,
        component.path,
        force: force,
      );
      if (valid(epoch) && component.path == path)
        setState(() {
          loaded = result;
          analysis = result.report;
          failure = null;
        });
    } catch (e) {
      // A failed lookup is not an absent requirement, and must not read as one.
      if (valid(epoch) && component.path == path)
        setState(() {
          failure = '$e';
        });
    } finally {
      if (valid(epoch) && component.path == path)
        setState(() => refreshing = false);
    }
  }

  Component statePill(Map<String, dynamic> requirement) {
    if (requirement['conflicts'] is List &&
        (requirement['conflicts'] as List).isNotEmpty)
      return pill('Conflict', tone: 'danger');
    if (requirement['established'] != true) return pill('Unresolved');
    if ((requirement['constraint'] ?? '') != '')
      return pill('Required', tone: 'info');
    if ((requirement['selected'] ?? '') != '')
      return pill('Selected', tone: 'info');
    return pill('Declared');
  }

  Component requirementRow(Map<String, dynamic> requirement) {
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
        statePill(requirement),
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
    'violated' || 'missing' => 'warning',
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
    final sourceNotice = sourceComponentNotice(
      item,
      analysis,
      plan: environmentPlan,
    );
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
          '${item['path']}'
          '${item['basis'] == 'inferred' ? ' (inferred from source)' : ''}',
        ),
      ], classes: 'overline'),
      meta(
        '$ecosystems · ${languages.isEmpty ? 'no languages' : languages.join(', ')}',
        classes: 'org-note',
      ),
      for (final requirement
          in (item['requirements'] as List? ?? []).cast<Map<String, dynamic>>())
        requirementRow(requirement),
      if ((item['frameworks'] as List? ?? []).isNotEmpty)
        meta(
          'Frameworks: '
          '${(item['frameworks'] as List).map((entry) => (entry as Map)['label']).join(', ')}',
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
        await connection.request('projects/toolchain', {
          'repository': component.path,
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
    await refresh(force: true);
  }

  Future<void> clearOverride() async {
    if (!admin || busy) return;
    await perform((epoch) async {
      await connection.request('projects/toolchain', {
        'repository': component.path,
        'toolchain': '',
      });
      if (valid(epoch)) message = 'Override removed.';
    });
    await refresh(force: true);
  }

  @override
  Component build(BuildContext context) => card(
    'Discovered requirements',
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
        meta(
          'Source: ${(analysis['source'] as Map?)?['scope'] ?? 'unknown'}'
          '${'${(analysis['source'] as Map?)?['revision'] ?? ''}'.isEmpty ? '' : ' · ${(analysis['source'] as Map)['revision']}'}'
          ' · assessed ${(analysis['source'] as Map?)?['at'] ?? 'unknown'}',
        ),
        if (requirements.isNotEmpty) ...[
          el('h3', [text('Host readiness')], classes: 'overline'),
          meta(
            'Whether this machine can provide each requirement. A measured version is not a '
            'verdict; only the evaluator decides satisfaction.',
            classes: 'org-note',
          ),
          for (final requirement in requirements) readinessRow(requirement),
          meta(projectInstallerSummary(analysis), classes: 'org-note'),
        ],
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
            el('summary', [text('Unrecognised files')]),
            meta(
              'Showing ${(coverage['unknown_evidence'] as List? ?? []).length} of ${coverage['unknown']} paths.',
            ),
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
      if (!admin)
        meta('Only an administrator can add an override.', iconName: 'lock'),
      if (environmentPlan != null) ...[
        el('h3', [text('Committed SDK preparation')], classes: 'overline'),
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
            disabled: busy || !admin,
          ),
        ],
        for (final check in environmentPlan!['check_candidates'] as List? ?? [])
          button(
            '${(environmentPlan!['selected_checks'] as List? ?? []).any((item) => item['id'] == check['id']) ? 'Deselect' : 'Approve'} ${check['cwd']}: ${(check['arguments'] as List).join(' ')} (${recipeEvidenceLabel(check as Map)})',
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
            disabled: busy || !admin,
          ),
        for (final item in environmentPlan!['requirements'] as List? ?? [])
          meta(
            '${item['components']}: ${item['capability']} ${item['version']} · ${item['state']} · ${item['reason']} · Activation: ${item['activation'] ?? 'unavailable'} ${item['activation_reason'] ?? ''}',
          ),
        if (environmentJob != null) meta(environmentJobLabel(environmentJob!)),
        if (environmentPlanCanPrepare(environmentPlan) &&
            !['queued', 'running', 'ready'].contains(environmentJob?['state']))
          button(
            'Prepare SDKs',
            () => environmentAction('prepare'),
            disabled: busy || !admin,
          ),
        if (['queued', 'running'].contains(environmentJob?['state']))
          button(
            'Cancel SDK preparation',
            () => environmentAction('cancel'),
            disabled: busy || !admin,
          ),
      ],
      feedback(),
    ],
    iconName: 'search',
    subtitle:
        'Derived from this repository\'s own files. Requirements show their evidence; anything '
        'that could not be settled is listed rather than omitted.',
    actions: [
      if (admin)
        button(
          'Plan environment',
          () => environmentAction('plan'),
          disabled: busy || refreshing,
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
