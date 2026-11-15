import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'package:herdr_dashboard_state/installation_status.dart';
import 'build_timeline.dart';
import 'operations_support.dart';
import 'update_card.dart';

class BuildsPage extends StatefulComponent {
  const BuildsPage({super.key});
  @override
  State<BuildsPage> createState() => _BuildsState();
}

class _BuildsState extends OperationsState<BuildsPage> {
  late final BuildsBloc feature;
  StreamSubscription? featureSubscription;
  List<Map<String, dynamic>> get runs =>
      List<Map<String, dynamic>>.from(feature.state.values['runs']);
  Map<String, dynamic>? get identity => feature.state.value('identity');
  Map<String, dynamic>? get deployment => feature.state.value('deployment');
  Map<String, dynamic>? get buildService => feature.state.value('buildService');
  String get executor => feature.state.value<String>('executor') ?? 'gateway';
  int get queued => feature.state.value<int>('queued') ?? 0;
  String? get downloading => feature.state.reading.contains('download')
      ? feature.state.value<String>('downloadId')
      : null;
  bool get busy => feature.state.busy;
  String? get error => feature.state.error;
  String? get message => feature.state.message;
  bool get refreshing => feature.state.reading.contains('refresh');
  bool get loaded => feature.state.values['loaded'] == true;
  Map<String, dynamic>? get performance => feature.state.value('performance');
  int get performanceEpoch =>
      feature.state.value<int>('performanceEpoch') ?? -1;
  bool get loadingPerformance => feature.state.reading.contains('performance');
  String? get performanceError => feature.state.errors['performance'];
  @override
  void initState() {
    super.initState();
    feature = BuildsBloc(connection);
    featureSubscription = feature.stream.listen((_) {
      if (!mounted) return;
      setState(() {});
    });
    refresh();
  }

  @override
  void dispose() {
    featureSubscription?.cancel();
    feature.dispose();
    super.dispose();
  }

  Future<void> refresh() => feature.refresh();
  bool get deploying => ['queued', 'running'].contains(deployment?['state']);
  Future<void> loadPerformance() => feature.loadPerformance();

  Component performanceCard() {
    final data = performanceEpoch == connection.generation ? performance : null;
    final operations = data?['operations'] as Map? ?? {};
    return card('Performance diagnostics', [
      paragraph(
        'Measured gateway process activity since restart. Timings overlap and must not be summed. Worker phase timings are retained on individual builds; this is not a fleet-wide total.',
      ),
      button(
        'Refresh performance diagnostics',
        loadPerformance,
        disabled: loadingPerformance,
      ),
      if (performanceError != null)
        el(
          'p',
          [text(performanceError)],
          classes: 'error',
          attributes: {'role': 'alert'},
        ),
      if (data != null) ...[
        paragraph(
          'Process ${data['pid']} · since ${data['started_at']} · percentiles use the latest ${data['sample_window']} samples per operation.',
        ),
        if (operations.isEmpty)
          paragraph('No instrumented operations recorded yet.'),
        for (final entry in operations.entries)
          paragraph(
            '${entry.key}: ${entry.value['count']} calls, ${entry.value['failed']} failed · p50 ${entry.value['p50_ms']} ms · p95 ${entry.value['p95_ms']} ms · max ${entry.value['max_ms']} ms',
          ),
        paragraph(
          'Repository query cache: ${data['counters']?['repository.cache_hit'] ?? 0} hits · ${data['counters']?['repository.cache_miss'] ?? 0} misses.',
        ),
        paragraph(
          'Task submissions: ${data['counters']?['task.submit_accepted'] ?? 0} admitted · ${data['counters']?['task.submit_limited'] ?? 0} limited. Limits apply per gateway process; retry the same captured request after the reported delay.',
        ),
      ],
    ], iconName: 'activity');
  }

  bool get installingBuildService =>
      ['queued', 'running'].contains(buildService?['state']);

  Future<void> installBuildService() async => feature.installService();

  Future<void> promote(Map<String, dynamic> run) async {
    if (!admin ||
        busy ||
        refreshing ||
        deploying ||
        run['deployment_package'] is! Map)
      return;
    final epoch = connection.generation;
    final manifest = run['deployment_package'] as Map;
    final failed = (manifest['checks'] as List? ?? [])
        .where(
          (c) => c is Map && c['required'] != false && c['status'] != 'passed',
        )
        .map((c) => c['id'])
        .join(', ');
    if (!await confirm(
          'Deploy this build?',
          'Source: ${run['target']}\nBuild: ${run['id']}\nPackage hash: ${manifest['sha256']}\n\nYour administrator approval is recorded. Files and configuration are backed up. The gateway restarts and must report this build ID before deployment is complete. Downloading a package never authorizes deployment.\n\nRequired checks not passed: $failed\nEvidence complete: ${manifest['required_checks_verified'] == true}. Review the log; approval never changes an underlying check result.',
          confirmLabel: 'Approve and deploy',
        ) ||
        !valid(epoch))
      return;
    await feature.promote(run);
  }

  Future<void> rollbackDeployment() async {
    if (!admin ||
        busy ||
        deploying ||
        deployment?['rollback_available'] != true)
      return;
    final epoch = connection.generation;
    if (!await confirm(
          'Restore the last deployment?',
          'The current files and configuration are backed up first, then the previous deployment is restored and the gateway restarts. Use this when the running build is unhealthy.',
          confirmLabel: 'Restore previous deployment',
        ) ||
        !valid(epoch))
      return;
    await feature.rollback();
  }

  Future<void> repairProvenance() async {
    if (!admin ||
        busy ||
        deploying ||
        deployment?['provenance_repairable'] != true ||
        deployment?['supported'] != true)
      return;
    final epoch = connection.generation;
    if (!await confirm(
          'Resolve release source identity?',
          'The release tag is resolved to its exact commit through the GitHub API and recorded as running build provenance. Only deployment identity metadata is updated; services are not restarted.',
          confirmLabel: 'Resolve source identity',
        ) ||
        !valid(epoch))
      return;
    await feature.repairProvenance();
  }

  Future<void> download(Map<String, dynamic> run, String kind) async {
    final epoch = connection.generation;
    final file = await feature.download(run, kind);
    if (file == null || !(valid(epoch))) return;
    try {
      if (file.bytes != null) {
        await downloadBytes(file.bytes!, file.filename);
      } else {
        await downloadText(file.text!, file.filename);
      }
      if (valid(epoch)) await feature.patch({}, message: file.message);
    } catch (error) {
      if (valid(epoch))
        await feature.reportError('download', 'Download failed: $error');
    }
  }

  /// Running build in the template's next-build card. Its second section is
  /// the deployment state; both keep their admin gates and confirmations.
  Component _runningCard() {
    final id = identity!;
    final sha = id['source_sha'];
    final mode = id['deployment_mode'];
    return el(
      'section',
      [
        el('header', [
          el(
            'span',
            [icon('rocket')],
            classes: 'icon-tile icon-tile--lg',
            attributes: {'data-tone': 'accent'},
          ),
          el('div', [
            el('p', [text('Running build')], classes: 'overline'),
            el('h2', [
              el('span', [
                text(id['build_id'] ?? 'Unknown for this installation'),
              ], classes: 'next-card__version'),
              switch (mode) {
                'local' => pill('Local build', 'info'),
                'release' => pill('Release', 'success'),
                _ => pill('Mode unknown', 'neutral', shape: 'hollow'),
              },
            ], classes: 'next-card__title'),
            el('p', [
              if (sha != null) shaChip(sha),
              el('span', [
                text('Package ${id['package_version'] ?? 'Unknown'}'),
              ]),
            ], classes: 'next-card__meta'),
          ], classes: 'next-card__heading'),
          if (admin &&
              deployment?['provenance_repairable'] == true &&
              deployment?['supported'] == true &&
              !deploying)
            button(
              'Resolve source identity',
              repairProvenance,
              disabled: busy,
              iconName: 'link',
              small: true,
            ),
        ], classes: 'next-card__header'),
        el('div', [
          el('div', [
            el('h3', [text('Build identity')], classes: 'overline'),
            facts([
              (
                'Build',
                mono(id['build_id'] ?? 'Unknown for this installation'),
              ),
              ('Source', mono(sha ?? 'Unknown')),
              ('Package', text(id['package_version'] ?? 'Unknown')),
              ('Deployment mode', text(mode ?? 'Unknown')),
            ]),
          ], classes: 'next-card__section'),
          if (deployment != null) _deploymentSection(deployment!),
        ], classes: 'next-card__body'),
      ],
      classes: 'card next-card',
      attributes: {'aria-label': 'Running build'},
    );
  }

  Component _deploymentSection(Map<String, dynamic> state) => el('div', [
    el('div', [
      el('h3', [text('Deployment')], classes: 'overline'),
      statusPill(state['state']),
    ], classes: 'next-card__section-head'),
    facts([
      ('Mode', text(state['deployment_mode'] ?? 'release')),
      if (state['build_id'] != null)
        ('Deployed build', mono(state['build_id'])),
      if (state['rollback_available'] is bool)
        (
          'Rollback',
          text(
            state['rollback_available'] == true
                ? 'Previous deployment available'
                : 'No previous deployment',
          ),
        ),
    ]),
    if (state['error'] != null)
      callout('danger', 'octagon-alert', [text(state['error'])]),
    if (admin && state['rollback_available'] == true && !deploying)
      actions([
        button(
          'Roll back last deployment',
          rollbackDeployment,
          disabled: busy,
          iconName: 'rotate-ccw',
          small: true,
        ),
      ]),
  ], classes: 'next-card__section');

  Component _downloads(Map<String, dynamic> run) {
    Component get(String label, String kind) => button(
      label,
      () => download(run, kind),
      disabled: downloading != null,
      iconName: 'download',
      small: true,
    );
    return el('div', [
      el('div', [
        get('Download log', 'log'),
        if (run['artifact'] is Map) ...[
          get('Download static artifact', 'artifact'),
          get('Download manifest', 'manifest'),
        ],
        if (run['deployment_package'] is Map)
          get('Download gateway + UI package', 'deployment'),
        if (downloading?.startsWith('${run['id']}:') == true)
          el(
            'span',
            [text('Downloading…')],
            classes: 'build-card__progress',
            attributes: {'role': 'status'},
          ),
      ], classes: 'build-card__downloads'),
      if (run['deployment_package'] is Map && admin)
        button(
          'Deploy this build',
          () => promote(run),
          variant: 'primary',
          iconName: 'rocket',
          small: true,
          classes: 'build-card__deploy',
          disabled: busy || refreshing || deploying,
        ),
    ], classes: 'build-card__actions');
  }

  /// One exact-commit build: header, facts, evidence list, notes, problems,
  /// validation steps (open for the newest build with checks and any
  /// failure) and the download/deploy row.
  Component _card(Map<String, dynamic> run, {required bool newest}) {
    final checks = checksOf(run);
    final failed = [
      for (final check in checks)
        if (statusLook(check['status']).$1 == 'danger')
          '${check['name'] ?? check['id']}',
    ];
    final heading = 'build-${run['id']}-title';
    return el(
      'article',
      [
        el('header', [
          el(
            'h3',
            [text(run['repository'] ?? 'Build')],
            classes: 'build-card__title',
            attributes: {'id': heading},
          ),
          statusPill(run['state']),
          if ('${run['target'] ?? ''}'.isNotEmpty)
            shaChip(run['target'], classes: 'build-card__sha'),
        ], classes: 'build-card__head'),
        ?runFacts(run),
        if (failed.isNotEmpty)
          buildProblem('danger', 'octagon-alert', [
            el('strong', [
              text(
                '${failed.length} check${failed.length == 1 ? '' : 's'} failed',
              ),
            ]),
            text(' · ${failed.join(', ')}'),
          ]),
        facts([
          ('Source', mono(run['target'])),
          ('Build', mono(run['id'])),
          if (run['task_id'] != null) ('Task/job', mono(run['task_id'])),
          if (run['event_ids'] is List)
            ('Integration events', mono((run['event_ids'] as List).join(', '))),
          ('Command', text(run['command'] ?? 'Preparing validation')),
        ], classes: 'build-card__evidence'),
        if (run['validation_waiver'] != null)
          buildNote(
            'info',
            'The originating event has a validation waiver. Build checks retain their actual results.',
          ),
        if ('${run['note'] ?? ''}'.isNotEmpty)
          buildNote('file-text', run['note']),
        if (run['artifact_error'] != null)
          buildProblem('warning', 'triangle-alert', [
            text(
              'Build output could not be retained: ${run['artifact_error']}',
            ),
          ]),
        if (run['deployment_package_error'] != null)
          buildProblem('warning', 'triangle-alert', [
            text(
              'Full package unavailable: ${run['deployment_package_error']}',
            ),
          ]),
        if (checks.isNotEmpty)
          checkSteps(checks, open: newest || failed.isNotEmpty),
        if (run['timings'] is Map)
          disclosure('Recorded phase timings', [
            for (final entry in (run['timings'] as Map).entries)
              paragraph(
                '${entry.key}: ${entry.value['duration_ms']} ms · ${entry.value['status']}',
              ),
          ]),
        _downloads(run),
      ],
      classes: 'build-card',
      attributes: {'aria-labelledby': heading},
    );
  }

  /// Vertical timeline, newest first: [start time] [status marker] [card].
  /// Without any start times the date column folds away.
  Component _history() {
    final dated = runs.any((run) => runTime(run['started_at']) != null);
    final newest = runs.indexWhere((run) => checksOf(run).isNotEmpty);
    return el(
      'section',
      [
        el('div', [
          el('h2', [
            icon('history'),
            text('Build timeline'),
            if (runs.isNotEmpty)
              el('span', [text('${runs.length}')], classes: 'count'),
          ], classes: 'history__title'),
          el('p', [
            text(
              'Build exact integration commits from Git changes. Static artifacts and gateway/UI packages are separate. Downloading never authorizes deployment.',
            ),
          ], classes: 'history__subtitle'),
        ], classes: 'history__head'),
        if (runs.isEmpty)
          el('div', [
            emptyState('No validation builds recorded.', iconName: 'package'),
          ], classes: 'card history__empty')
        else
          el(
            'ol',
            [
              for (final (index, run) in runs.indexed)
                el(
                  'li',
                  [
                    if (dated) whenColumn(runTime(run['started_at'])),
                    rail(run['state']),
                    _card(run, newest: index == newest),
                  ],
                  key: ValueKey('build-${run['id']}'),
                  classes: 'build-entry',
                  attributes: {
                    'data-status': '${run['state']}',
                    'data-edge': [
                      if (index == 0) 'first',
                      if (index == runs.length - 1) 'last',
                    ].join(' '),
                  },
                ),
            ],
            classes: dated
                ? 'build-timeline'
                : 'build-timeline build-timeline--undated',
            attributes: {'role': 'list'},
          ),
      ],
      classes: 'history',
      attributes: {'aria-label': 'Build timeline'},
    );
  }

  @override
  Component build(BuildContext context) => el('div', [
    pageHeader(
      'Builds & deployments',
      subtitle: loaded
          ? 'Build executor: $executor · $queued queued'
          : 'Validation history and deployment status',
      actions: [
        button(
          'Refresh builds',
          refresh,
          disabled: refreshing,
          iconName: 'refresh-cw',
        ),
      ],
    ),
    el('div', [feedback()], classes: 'builds-feedback'),
    if (!loaded && error == null)
      el('p', [text('Loading builds…')], attributes: {'role': 'status'}),
    if (loaded && executor != 'service')
      callout('warning', 'triangle-alert', [
        paragraph(
          'Manual capture and builds are available. Automatic capture works without this service, and automatic validation uses the gateway executor. Gateway builds stop on restart; install this service for restart resilience. Installation restarts the gateway and keeps automation opt-in.',
        ),
        if (buildService?['supported'] == true && admin)
          button(
            installingBuildService
                ? 'Installing build service…'
                : 'Install build service',
            installBuildService,
            disabled: busy || installingBuildService,
          ),
        if (buildService?['supported'] != true)
          disclosure('Build service setup', [
            codeText(
              'Enable dashboard installation once as root:\nbash /opt/herdr-web/install/build-service-setup.sh\nOr install directly:\nbash /opt/herdr-web/install/build-install.sh',
            ),
          ]),
      ], title: 'Durable build service not installed.'),
    if (buildService?['state'] != null) ...[
      paragraph(
        'Build-service installation: ${installationStatusLabel(buildService!['state'])}',
      ),
      if (buildService!['error'] != null)
        el('p', [text(buildService!['error'])], attributes: {'role': 'alert'}),
      if (buildService!['output'] != null)
        disclosure('Build-service installation output', [
          codeText(buildService!['output']),
        ]),
    ],
    if (identity != null) _runningCard(),
    ?cadenceStrip(runs, DateTime.now()),
    if (loaded) _history(),
    if (admin) performanceCard(),
    const UpdateCard(),
  ], classes: 'stack builds-page');
}
