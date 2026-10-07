import 'dart:convert';
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
  List<Map<String, dynamic>> runs = [];
  Map<String, dynamic>? identity;
  Map<String, dynamic>? deployment;
  String executor = 'gateway';
  int queued = 0;
  String? error;
  String? actionError;
  String? downloading;
  bool acting = false;
  bool busy = false;
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
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    busy = true;
    try {
      final result = await connection.request('validation');
      final deployed = await connection.request('build');
      Map<String, dynamic>? status;
      try {
        status = Map<String, dynamic>.from(await connection.request('updates'));
      } catch (_) {
        // Deployment status is unavailable until the release check succeeds.
      }
      if (mounted && epoch == connection.generation)
        setState(() {
          runs = List<Map<String, dynamic>>.from(result['runs'] ?? []);
          executor = '${result['executor'] ?? 'gateway'}';
          queued = result['queued'] as int? ?? 0;
          identity = Map<String, dynamic>.from(deployed);
          deployment = status;
          error = null;
        });
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      busy = false;
    }
  }

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
    if (acting) return;
    setState(() {
      acting = true;
      actionError = null;
    });
    try {
      await connection.request('updates/promote', {'run_id': run['id']});
      if (!mounted || epoch != connection.generation) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Deployment queued. The gateway restarts when the deployment finishes.',
          ),
        ),
      );
      await refresh();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => actionError = '$e');
    } finally {
      if (mounted) setState(() => acting = false);
    }
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
    if (acting) return;
    setState(() {
      acting = true;
      actionError = null;
    });
    try {
      await connection.request('updates/rollback', const {});
      if (!mounted || epoch != connection.generation) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Rollback queued.')));
      await refresh();
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => actionError = '$e');
    } finally {
      if (mounted) setState(() => acting = false);
    }
  }

  void notify(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> download(Map<String, dynamic> run, String kind) async {
    final epoch = connection.generation;
    if (downloading != null) return;
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
        if (expected is! int || expected < 0 || content.length != expected) {
          throw Exception(
            'Downloaded size ${content.length} does not match the manifest ($expected bytes).',
          );
        }
        if (epoch != connection.generation) return;
        await downloadBinaryArtifact(content, '$kind-${run['id']}.tar.gz');
        notify(
          '$kind package fetched and handed to the browser · ${content.length} bytes verified.',
        );
      } else if (kind == 'manifest') {
        final text = const JsonEncoder.withIndent(
          '  ',
        ).convert(run['artifact']);
        await downloadArtifact(text, 'build-${run['id']}.json');
        notify('Manifest fetched · ${utf8.encode(text).length} bytes.');
      } else {
        final content = await connection.text('validation/log?id=${run['id']}');
        if (epoch != connection.generation) return;
        await downloadArtifact(content, 'validation-${run['id']}.log');
        notify('Log fetched · ${utf8.encode(content).length} bytes.');
      }
    } catch (e) {
      if (mounted && epoch == connection.generation) {
        setState(() => error = '$e');
        notify('Download failed: $e');
      }
    } finally {
      if (mounted) setState(() => downloading = null);
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
    if (acting) return;
    setState(() {
      acting = true;
      actionError = null;
    });
    try {
      await connection.request('updates/provenance', const {});
      if (mounted && epoch == connection.generation)
        notify('Source identity resolution queued.');
      await refresh();
    } catch (e) {
      if (mounted) setState(() => actionError = '$e');
    } finally {
      if (mounted) setState(() => acting = false);
    }
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
        const ListTile(
          contentPadding: EdgeInsets.zero,
          leading: Icon(Icons.warning_amber_outlined),
          title: Text('Durable build service not installed'),
          subtitle: Text(
            'Builds run inside the gateway and stop with it; automatic builds stay off. '
            'Install it as root inside the container: bash /opt/herdr-web/install/build-install.sh',
          ),
        ),
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
