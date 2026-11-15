import 'package:herdr_dashboard_state/workspace_blocs.dart';
import 'package:herdr_dashboard_state/installation_status.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'artifact_download.dart';
import 'update_card.dart';

class BuildsPage extends StatefulWidget {
  const BuildsPage({super.key});
  @override
  State<BuildsPage> createState() => _BuildsPageState();
}

class _BuildsPageState extends State<BuildsPage> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
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
  bool get busy => feature.state.reading.contains('refresh');
  bool get acting => feature.state.busy;
  String? get error =>
      feature.state.errors['refresh'] ?? feature.state.errors['download'];
  String? get actionError => feature.state.errors['action'];
  String? lastNotice;
  @override
  void initState() {
    super.initState();
    feature = BuildsBloc(connection);
    featureSubscription = feature.stream.listen((_) {
      if (!mounted) return;
      final notice = feature.state.message;
      if (notice != null && notice != lastNotice) notify(notice);
      lastNotice = notice;
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
  bool get installingBuildService =>
      ['queued', 'running'].contains(buildService?['state']);

  Future<void> installBuildService() async => feature.installService();

  Future<void> promote(Map<String, dynamic> run) async {
    final epoch = connection.generation;
    final manifest = run['deployment_package'] as Map;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Deploy this build?'),
        content: SelectableText(
          'Source: ${run['target']}\nBuild: ${run['id']}\nPackage hash: ${manifest['sha256']}\n\n'
          'Your administrator approval is recorded. Files and configuration are backed up; '
          'the gateway restarts and must report this build ID before the deployment is complete. '
          'Downloading a package alone never authorizes deployment.\n\n'
          'Required checks not passed: ${(manifest['checks'] as List? ?? []).where((c) => c is Map && c['required'] != false && c['status'] != 'passed').map((c) => c['id']).join(', ')}\n'
          'Evidence complete: ${manifest['required_checks_verified'] == true}. '
          'Review the log; administrator approval never changes an underlying check result.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Approve and deploy'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || epoch != connection.generation) return;
    await feature.promote(run);
  }

  Future<void> rollbackDeployment() async {
    final epoch = connection.generation;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Restore the last deployment?'),
        content: const Text(
          'The current files and configuration are backed up first, then the previous '
          'deployment is restored and the gateway restarts. Use this when the running build is unhealthy.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Restore previous deployment'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || epoch != connection.generation) return;
    await feature.rollback();
  }

  void notify(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> download(Map<String, dynamic> run, String kind) async {
    final epoch = connection.generation;
    final file = await feature.download(run, kind);
    if (file == null || !(mounted && epoch == connection.generation)) return;
    try {
      if (file.bytes != null) {
        await downloadBinaryArtifact(file.bytes!, file.filename);
      } else {
        await downloadArtifact(file.text!, file.filename);
      }
      if (mounted && epoch == connection.generation)
        await feature.patch({}, message: file.message);
    } catch (error) {
      if (mounted && epoch == connection.generation)
        await feature.reportError('download', 'Download failed: $error');
    }
  }

  Future<void> repairProvenance() async {
    final epoch = connection.generation;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Resolve release source identity?'),
        content: const Text(
          'The release tag is resolved to its exact commit through the GitHub API and '
          'recorded as the running build provenance. Only deployment identity metadata is updated; services are not restarted.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Resolve source identity'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || epoch != connection.generation) return;
    await feature.repairProvenance();
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(24),
    children: [
      Wrap(
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(
            'Builds & deployments',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          IconButton(
            onPressed: busy ? null : refresh,
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh builds',
          ),
        ],
      ),
      const SizedBox(height: 12),
      Text('Build executor: $executor · $queued queued'),
      if (executor != 'service')
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.warning_amber_outlined),
          title: const Text('Durable build service not installed'),
          subtitle: const Text(
            'Automatic capture and validation are available with the gateway executor. Gateway builds stop on restart; install this service for restart resilience. Installing it restarts the gateway; automation stays opt-in.',
          ),
        ),
      if (executor != 'service' &&
          buildService?['supported'] == true &&
          connection.operator.value['role'] == 'admin')
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton(
            onPressed: acting || installingBuildService
                ? null
                : installBuildService,
            child: Text(
              installingBuildService
                  ? 'Installing build service…'
                  : 'Install build service',
            ),
          ),
        ),
      if (executor != 'service' && buildService?['supported'] != true)
        const ExpansionTile(
          title: Text('Build service setup'),
          children: [
            SelectableText(
              'Enable dashboard installation once as root:\nbash /opt/herdr-web/install/build-service-setup.sh\nOr install directly:\nbash /opt/herdr-web/install/build-install.sh',
            ),
          ],
        ),
      if (buildService?['state'] != null) ...[
        Text(
          'Build-service installation: ${installationStatusLabel(buildService!['state'])}',
        ),
        if (buildService!['error'] != null)
          SelectableText('${buildService!['error']}'),
        if (buildService!['output'] != null)
          ExpansionTile(
            title: const Text('Build-service installation output'),
            children: [SelectableText('${buildService!['output']}')],
          ),
      ],
      const Text(
        'Build exact integration commits from Git changes. Static artifacts and full gateway/UI packages are separate. Downloading a package does not authorize deployment.',
      ),
      if (error != null)
        Padding(
          padding: const EdgeInsets.all(12),
          child: Text(
            error!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        ),
      if (actionError != null)
        Padding(
          padding: const EdgeInsets.all(12),
          child: Text(
            actionError!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        ),
      if (identity != null)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SelectableText(
                  'Running build: ${identity!['build_id'] ?? 'Unknown for this installation'}\nSource: ${identity!['source_sha'] ?? 'Unknown'}\nPackage: ${identity!['package_version'] ?? 'Unknown'}\nDeployment mode: ${identity!['deployment_mode'] ?? 'Unknown'}',
                ),
                if (connection.operator.value['role'] == 'admin' &&
                    deployment?['provenance_repairable'] == true &&
                    deployment?['supported'] == true &&
                    !['queued', 'running'].contains(deployment?['state']))
                  TextButton.icon(
                    onPressed: acting ? null : repairProvenance,
                    icon: const Icon(Icons.link),
                    label: const Text('Resolve source identity'),
                  ),
              ],
            ),
          ),
        ),
      if (deployment != null)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Deployment: ${deployment!['state']} · ${deployment!['deployment_mode'] ?? 'release'}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                if (deployment!['build_id'] != null)
                  SelectableText('Deployed build: ${deployment!['build_id']}'),
                if (deployment!['error'] != null)
                  Text('${deployment!['error']}'),
                if (connection.operator.value['role'] == 'admin' &&
                    deployment!['rollback_available'] == true &&
                    !['queued', 'running'].contains(deployment!['state']))
                  TextButton.icon(
                    onPressed: acting ? null : rollbackDeployment,
                    icon: const Icon(Icons.restore),
                    label: const Text('Roll back last deployment'),
                  ),
              ],
            ),
          ),
        ),
      if (runs.isEmpty)
        const Padding(
          padding: EdgeInsets.all(24),
          child: Text('No validation builds recorded.'),
        ),
      for (final run in runs)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${run['repository']} · ${run['state']}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                SelectableText('Source: ${run['target']}\nBuild: ${run['id']}'),
                if (run['task_id'] != null)
                  SelectableText('Task/job: ${run['task_id']}'),
                if (run['event_ids'] is List)
                  SelectableText(
                    'Integration events: ${(run['event_ids'] as List).join(', ')}',
                  ),
                if (run['validation_waiver'] != null)
                  const Text(
                    'The originating integration event has a validation waiver. Build checks retain their actual results.',
                  ),
                Text('${run['command'] ?? 'Preparing validation'}'),
                if (run['timings'] is Map)
                  ExpansionTile(
                    title: const Text('Recorded phase timings'),
                    children: [
                      for (final entry in (run['timings'] as Map).entries)
                        if (entry.value is Map)
                          Text(
                            '${entry.key}: ${entry.value['duration_ms']} ms · ${entry.value['status']}',
                          ),
                    ],
                  ),
                if (run['checks'] is List)
                  for (final check in run['checks'] as List)
                    if (check is Map)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Text(
                          '${check['name'] ?? check['id']} · ${check['status']} · ${check['duration_ms'] ?? 0} ms · exit ${check['exit_code'] ?? 'not run'}\n${check['summary'] ?? ''}',
                        ),
                      ),
                if (run['artifact_error'] != null)
                  Text(
                    'Build output could not be retained: ${run['artifact_error']}',
                  ),
                if (run['deployment_package_error'] != null)
                  Text(
                    'Full package unavailable: ${run['deployment_package_error']}',
                  ),
                Wrap(
                  spacing: 12,
                  children: [
                    TextButton.icon(
                      onPressed: downloading == null
                          ? () => download(run, 'log')
                          : null,
                      icon: const Icon(Icons.description_outlined),
                      label: const Text('Download log'),
                    ),
                    if (run['artifact'] is Map) ...[
                      TextButton.icon(
                        onPressed: downloading == null
                            ? () => download(run, 'artifact')
                            : null,
                        icon: const Icon(Icons.download),
                        label: const Text('Download static artifact'),
                      ),
                      TextButton(
                        onPressed: downloading == null
                            ? () => download(run, 'manifest')
                            : null,
                        child: const Text('Download manifest'),
                      ),
                    ],
                    if (run['deployment_package'] is Map)
                      TextButton.icon(
                        onPressed: downloading == null
                            ? () => download(run, 'deployment')
                            : null,
                        icon: const Icon(Icons.inventory_2_outlined),
                        label: const Text('Download gateway + UI package'),
                      ),
                    if (run['deployment_package'] is Map &&
                        connection.operator.value['role'] == 'admin')
                      FilledButton.icon(
                        onPressed:
                            busy ||
                                acting ||
                                [
                                  'queued',
                                  'running',
                                ].contains(deployment?['state'])
                            ? null
                            : () => promote(run),
                        icon: const Icon(Icons.rocket_launch_outlined),
                        label: const Text('Deploy this build'),
                      ),
                  ],
                ),
                if (downloading != null &&
                    downloading!.startsWith('${run['id']}:'))
                  const LinearProgressIndicator(),
              ],
            ),
          ),
        ),
      const UpdateCard(),
    ],
  );
}
