import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'feature_view.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

class UpdateCard extends StatefulWidget {
  const UpdateCard({super.key});
  @override
  State<UpdateCard> createState() => _UpdateCardState();
}

class _UpdateCardState extends State<UpdateCard> with FeatureView<UpdateCard> {
  late final DashboardUpdatesBloc bloc;
  Map<String, dynamic>? get info => bloc.info;
  List<Map<String, dynamic>> get alphas => bloc.alphas;
  String? alphaVersion;
  String? get error =>
      featureError ??
      (bloc.state.values['alphasLoaded'] == true && alphas.isEmpty
          ? 'No installable alpha builds are available.'
          : null);
  bool get busy => featureBusy;
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  @override
  void initState() {
    super.initState();
    bindFeature(
      bloc = DashboardUpdatesBloc(connection),
      onChanged: () {
        if (!alphas.any((release) => release['version'] == alphaVersion))
          alphaVersion = null;
      },
    );
    check();
  }

  Future<void> check({bool force = false}) => bloc.check(force: force);

  Future<void> install({bool alpha = false}) async {
    final version = alpha ? alphaVersion : info?['latest'];
    // A null version would otherwise produce an "Install null?" confirmation
    // and send a meaningless install request.
    if (version == null) return;
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
    await bloc.install('$version', alpha: alpha);
  }

  Future<void> loadAlphas() async {
    await bloc.loadAlphas();
    if (mounted) setState(() => alphaVersion = null);
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
              'Installed: ${info!['installed']} \u00b7 Latest: ${info!['latest'] ?? 'Unavailable'}',
            ),
            Text('Status: ${info!['state']}'),
            // A diagnosis is the actionable message, so it replaces the raw
            // exception rather than sitting beside it. The raw error stays the
            // only signal when the failure is not a connectivity problem, and
            // the journal always records it.
            if (info!['check_diagnostic'] != null)
              Text('${info!['check_diagnostic']}')
            else if (info!['check_error'] != null)
              Text('Release lookup unavailable: ${info!['check_error']}'),
            if (info!['check_error'] != null && info!['check_failures'] != null)
              Text(
                'Release lookup failed ${info!['check_failures']} times since ${info!['check_failed_since'] ?? 'the first failed check'}. Counts reset when the gateway restarts.',
              ),
            if (info!['state'] == 'complete' && info!['check_error'] != null)
              const Text(
                'The previous installation completed. The current release check failed.',
              ),
            if (info!['state'] == 'complete' && info!['check_error'] == null)
              const Text(
                'Update complete. Reload this page to use the new dashboard.',
              ),
            if (info!['error'] != null) Text('${info!['error']}'),
            if (info!['recovery_error'] != null)
              Text('Recovery failed: ${info!['recovery_error']}'),
            if (info!['backup_verification'] is Map)
              Text(
                info!['backup_verification']['method'] == 'manifest'
                    ? 'Backup contents verified against its manifest.'
                    : 'Legacy backup checked using build identity, required files and database integrity.',
              ),
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
                '${alphas.where((r) => r['version'] == alphaVersion).firstOrNull?['notes'] ?? ''}',
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
