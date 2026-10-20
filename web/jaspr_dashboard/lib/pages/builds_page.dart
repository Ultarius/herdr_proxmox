import 'dart:convert';
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
  List<Map<String, dynamic>> runs = [];
  Map<String, dynamic>? identity, deployment;
  Map<String, dynamic>? buildService;
  String executor = 'gateway';
  int queued = 0;
  bool refreshing = false;
  Map<String, dynamic>? performance;
  int performanceEpoch = -1;
  bool loadingPerformance = false;
  String? performanceError;
  String? downloading;
  Timer? timer;
  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 5), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    if (refreshing || !connection.state.connected) return;
    refreshing = true;
    final epoch = connection.generation;
    try {
      final validation = await connection.request('validation');
      final deployed = await connection.request('build');
      Map<String, dynamic>? installer;
      try {
        installer = Map<String, dynamic>.from(
          await connection.request('build-service'),
        );
      } catch (_) {} // Older gateways retain the root-command fallback.
      Map<String, dynamic>? status;
      try {
        status = Map<String, dynamic>.from(await connection.request('updates'));
      } catch (_) {}
      if (valid(epoch))
        setState(() {
          runs = List<Map<String, dynamic>>.from(validation['runs'] ?? []);
          executor = '${validation['executor'] ?? 'gateway'}';
          queued = validation['queued'] as int? ?? 0;
          identity = Map<String, dynamic>.from(deployed);
          deployment = status;
          buildService = installer;
        });
    } catch (e) {
      if (valid(epoch)) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => refreshing = false);
    }
  }

  bool get deploying => ['queued', 'running'].contains(deployment?['state']);

  Future<void> loadPerformance() async {
    if (!admin || loadingPerformance || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() {
      loadingPerformance = true;
      performanceError = null;
    });
    try {
      final result = await connection.request('diagnostics/performance');
      if (valid(epoch))
        setState(() {
          performance = Map<String, dynamic>.from(result);
          performanceEpoch = epoch;
        });
    } catch (e) {
      if (valid(epoch)) setState(() => performanceError = '$e');
    } finally {
      if (mounted) setState(() => loadingPerformance = false);
    }
  }

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
        el('p', [text(performanceError)], classes: 'error'),
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

  Future<void> installBuildService() async {
    if (!admin ||
        busy ||
        installingBuildService ||
        buildService?['supported'] != true)
      return;
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
      message = null;
    });
    try {
      final result = await connection.request(
        'build-service/install',
        const {},
      );
      if (valid(epoch)) {
        setState(() {
          buildService = {'supported': true, ...result};
          message =
              'Build-service installation queued. The gateway will restart; automation settings remain unchanged.';
        });
      }
    } catch (e) {
      if (valid(epoch)) setState(() => error = '$e');
    } finally {
      // Installation restarts the gateway; a lost response must release busy.
      if (mounted) setState(() => busy = false);
    }
  }

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
    await perform((epoch) async {
      await connection.request('updates/promote', {'run_id': run['id']});
      if (valid(epoch))
        setState(
          () => message =
              'Deployment queued. The gateway restarts when deployment finishes.',
        );
      await refresh();
    });
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
    await perform((epoch) async {
      await connection.request('updates/rollback', const {});
      if (valid(epoch)) setState(() => message = 'Rollback queued.');
      await refresh();
    });
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
    await perform((epoch) async {
      await connection.request('updates/provenance', const {});
      if (valid(epoch))
        setState(() => message = 'Source identity resolution queued.');
      await refresh();
    });
  }

  Future<void> download(Map<String, dynamic> run, String kind) async {
    if (downloading != null) return;
    final epoch = connection.generation;
    setState(() {
      downloading = '${run['id']}:$kind';
      error = null;
    });
    try {
      if (kind == 'artifact' || kind == 'deployment') {
        final content = await connection.bytes(
          'validation/artifact?id=${run['id']}&kind=${kind == 'deployment' ? 'deployment' : 'static'}',
        );
        final manifest =
            run[kind == 'deployment' ? 'deployment_package' : 'artifact']
                as Map;
        final expected = manifest['bytes'];
        if (expected is! int || expected < 0 || content.length != expected)
          throw Exception(
            'Downloaded size ${content.length} does not match the manifest ($expected bytes).',
          );
        if (!valid(epoch)) return;
        await downloadBytes(content, '$kind-${run['id']}.tar.gz');
        if (valid(epoch))
          setState(
            () => message =
                '$kind package fetched and handed to the browser · ${content.length} bytes verified.',
          );
      } else if (kind == 'manifest') {
        final content = const JsonEncoder.withIndent(
          '  ',
        ).convert(run['artifact']);
        if (!valid(epoch)) return;
        await downloadText(content, 'build-${run['id']}.json');
        if (valid(epoch))
          setState(
            () => message =
                'Manifest fetched · ${utf8.encode(content).length} bytes.',
          );
      } else {
        final content = await connection.text('validation/log?id=${run['id']}');
        if (!valid(epoch)) return;
        await downloadText(content, 'validation-${run['id']}.log');
        if (valid(epoch))
          setState(
            () =>
                message = 'Log fetched · ${utf8.encode(content).length} bytes.',
          );
      }
    } catch (e) {
      if (valid(epoch)) setState(() => error = 'Download failed: $e');
    } finally {
      if (valid(epoch)) setState(() => downloading = null);
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
      subtitle: 'Build executor: $executor · $queued queued',
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
    if (executor != 'service')
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
      if (buildService!['error'] != null) paragraph(buildService!['error']),
      if (buildService!['output'] != null)
        disclosure('Build-service installation output', [
          codeText(buildService!['output']),
        ]),
    ],
    if (identity != null) _runningCard(),
    ?cadenceStrip(runs, DateTime.now()),
    _history(),
    if (admin) performanceCard(),
    const UpdateCard(),
  ], classes: 'stack builds-page');
}
