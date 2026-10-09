import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

class UpdateCard extends StatefulWidget {
  const UpdateCard({super.key});
  @override
  State<UpdateCard> createState() => _UpdateCardState();
}

class _UpdateCardState extends State<UpdateCard> {
  Map<String, dynamic>? info;
  String? error;
  bool busy = false;
  List<Map<String, dynamic>> alphas = [];
  String? alphaVersion;
  Timer? timer;
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();

  @override
  void initState() {
    super.initState();
    check();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> check({bool force = false}) async {
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() => busy = true);
    try {
      final result = await connection.request(
        force ? 'updates/check' : 'updates',
        force ? <String, dynamic>{} : null,
      );
      if (mounted && epoch == connection.generation) {
        setState(() {
          info = result;
          error = null;
        });
        if (!['queued', 'running'].contains(result['state'])) {
          timer?.cancel();
          timer = null;
        }
      }
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> install({bool alpha = false}) async {
    final version = alpha ? alphaVersion : info?['latest'];
    final epoch = connection.generation;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Install $version?'),
        content: Text(
          '${alpha ? "This is an experimental feature-branch build for testing. " : ""}The dashboard will briefly disconnect. Your projects, agent profiles and credentials are preserved. A backup is kept for recovery. Reload this page after the update completes.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Install update'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || epoch != connection.generation) return;
    setState(() => busy = true);
    try {
      await connection.request('updates/install', {
        'version': version,
        if (alpha) 'channel': 'alpha',
        if (alpha) 'confirm_alpha': true,
      });
      if (mounted) {
        setState(() => info = {...?info, 'state': 'queued'});
        timer ??= Timer.periodic(const Duration(seconds: 5), (_) => check());
      }
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> loadAlphas() async {
    if (busy) return;
    final epoch = connection.generation;
    setState(() => busy = true);
    try {
      final result = await connection.request(
        'updates/alphas',
        <String, dynamic>{},
      );
      if (mounted && epoch == connection.generation) {
        setState(() {
          alphas = (result['releases'] as List).cast<Map<String, dynamic>>();
          alphaVersion = null;
          error = alphas.isEmpty
              ? 'No installable alpha builds are available.'
              : null;
        });
      }
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Dashboard updates',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const Text(
            'Stable releases from Ultarius/herdr_proxmox. Updates are installed when you choose.',
          ),
          if (info != null) ...[
            Text(
              'Installed: ${info!['installed']} \u00b7 Latest: ${info!['latest']}',
            ),
            Text('Status: ${info!['state']}'),
            if (info!['check_error'] != null)
              Text('Release lookup unavailable: ${info!['check_error']}'),
            if (info!['state'] == 'complete')
              const Text(
                'Update complete. Reload this page to use the new dashboard.',
              ),
            if (info!['error'] != null) Text('${info!['error']}'),
            if (info!['deployment_mode'] == 'local')
              const Text(
                'Release installation is disabled while a local build is deployed.',
              )
            else if (info!['supported'] != true)
              const Text(
                'Install the updater service in this container before using dashboard updates.',
              ),
            if ((info!['notes'] as String? ?? '').isNotEmpty)
              Text(info!['notes'] as String),
          ],
          const SizedBox(height: 16),
          const Text('Alpha builds \u2014 feature branch testing'),
          TextButton(
            onPressed: busy ? null : loadAlphas,
            child: const Text('Browse alpha builds'),
          ),
          if (alphas.isNotEmpty) ...[
            DropdownButtonFormField<String>(
              key: ValueKey(alphaVersion),
              initialValue: alphaVersion,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Feature branch alpha build',
              ),
              items: alphas
                  .map(
                    (release) => DropdownMenuItem<String>(
                      value: release['version'] as String,
                      child: Text(
                        release['version'] as String,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  )
                  .toList(),
              onChanged: busy
                  ? null
                  : (value) => setState(() => alphaVersion = value),
            ),
            if (alphaVersion != null)
              Text(
                '${alphas.firstWhere((r) => r['version'] == alphaVersion)['notes']}',
              ),
            FilledButton(
              onPressed:
                  !busy &&
                      alphaVersion != null &&
                      info?['supported'] == true &&
                      !['queued', 'running'].contains(info?['state'])
                  ? () => install(alpha: true)
                  : null,
              child: const Text('Install selected alpha'),
            ),
          ],
          if (error != null) Text(error!),
          Wrap(
            spacing: 12,
            children: [
              TextButton(
                onPressed: busy ? null : () => check(force: true),
                child: const Text('Check for updates'),
              ),
              FilledButton(
                onPressed:
                    !busy &&
                        info?['available'] == true &&
                        info?['supported'] == true &&
                        !['queued', 'running'].contains(info?['state'])
                    ? () => install()
                    : null,
                child: const Text('Install update'),
              ),
            ],
          ),
        ],
      ),
    ),
  );
}
