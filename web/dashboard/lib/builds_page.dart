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
  String? error;
  String? actionError;
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
          'Individual required checks are not yet independently verified in the package manifest. '
          'Review the validation log and any skipped, unavailable or waived checks before approving.',
          // The package records only a script-set exit status today; do not
          // imply that administrator approval creates per-check evidence.
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

  Future<void> download(Map<String, dynamic> run, String kind) async {
    final epoch = connection.generation;
    try {
      if (kind == 'artifact' || kind == 'deployment') {
        final content = await connection.bytes(
          'validation/artifact?id=${run['id']}&kind=${kind == 'deployment' ? 'deployment' : 'static'}',
        );
        if (epoch != connection.generation) return;
        await downloadBinaryArtifact(content, '$kind-${run['id']}.tar.gz');
      } else if (kind == 'manifest') {
        await downloadArtifact(
          const JsonEncoder.withIndent('  ').convert(run['artifact']),
          'build-${run['id']}.json',
        );
      } else {
        final content = await connection.text('validation/log?id=${run['id']}');
        if (epoch != connection.generation) return;
        await downloadArtifact(content, 'validation-${run['id']}.log');
      }
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
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
            child: SelectableText(
              'Running build: ${identity!['build_id'] ?? 'Unknown for this installation'}\nSource: ${identity!['source_sha'] ?? 'Unknown'}\nPackage: ${identity!['package_version'] ?? 'Unknown'}\nDeployment mode: ${identity!['deployment_mode'] ?? 'Unknown'}',
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
                Text('${run['command'] ?? 'Preparing validation'}'),
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
                      onPressed: () => download(run, 'log'),
                      icon: const Icon(Icons.description_outlined),
                      label: const Text('Download log'),
                    ),
                    if (run['artifact'] is Map) ...[
                      TextButton.icon(
                        onPressed: () => download(run, 'artifact'),
                        icon: const Icon(Icons.download),
                        label: const Text('Download static artifact'),
                      ),
                      TextButton(
                        onPressed: () => download(run, 'manifest'),
                        child: const Text('Download manifest'),
                      ),
                    ],
                    if (run['deployment_package'] is Map)
                      TextButton.icon(
                        onPressed: () => download(run, 'deployment'),
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
              ],
            ),
          ),
        ),
      const UpdateCard(),
    ],
  );
}
