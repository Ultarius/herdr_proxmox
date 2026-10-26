import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'feature_view.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

/// Provisionable development toolchains, as declared by the deployed release.
///
/// Each toolchain is named and pinned by `install/toolchains.json`, so this
/// states which one is absent instead of reporting a single yes/no SDK flag.
class ToolchainCard extends StatefulWidget {
  const ToolchainCard({super.key});
  @override
  State<ToolchainCard> createState() => _ToolchainCardState();
}

class _ToolchainCardState extends State<ToolchainCard>
    with FeatureView<ToolchainCard> {
  late final DashboardBloc connection;
  late final ToolchainBloc bloc;
  Map<String, dynamic> get sdk => bloc.sdk;
  bool get busy => featureBusy;
  bool get refreshing => bloc.state.loading;
  String? get requestError => bloc.state.errors['install'];
  Map<String, dynamic> get toolchains => bloc.toolchains;
  bool get queued => bloc.queued;
  @override
  void initState() {
    super.initState();
    connection = BlocScope.get<DashboardBloc>();
    bindFeature(bloc = ToolchainBloc(connection));
    refresh();
  }

  Future<void> refresh({bool showProgress = false}) => bloc.refresh();
  Future<void> install(String toolchain) async {
    if (await bloc.install(toolchain) && mounted && featureMessage != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(featureMessage!)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final role = connection.operator.value['role'];
    final absent = toolchains.values
        .where((toolchain) => toolchain['installed'] != true)
        .length;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Herdr build tools',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                ),
                IconButton(
                  tooltip: 'Refresh toolchain status',
                  onPressed: refreshing
                      ? null
                      : () => refresh(showProgress: true),
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Used to build Herdr itself from source. These versions are pinned by the '
              'deployed release and are not a requirement of any project you host. '
              'Project requirements live in the project explorer.',
            ),
            if (sdk['state'] == 'unavailable') ...[
              const SizedBox(height: 12),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.warning_amber_outlined),
                title: const Text('Toolchain status unavailable'),
                subtitle: Text(
                  '${sdk['error']}\nVerify that the updated gateway was restarted.',
                ),
              ),
            ] else if (sdk['supported'] != true) ...[
              const SizedBox(height: 12),
              const ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.build_outlined),
                title: Text('SDK installation service is not configured'),
                subtitle: Text(
                  'Run bash /opt/herdr-web/install/web-install.sh as root inside the LXC to register the SDK service, then refresh this page.',
                ),
              ),
            ] else if (sdk['catalog_error'] != null) ...[
              const SizedBox(height: 12),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.warning_amber_outlined),
                title: const Text('Toolchain catalog unavailable'),
                subtitle: Text('${sdk['catalog_error']}'),
              ),
            ] else if (toolchains.isEmpty) ...[
              const SizedBox(height: 12),
              const ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.help_outline),
                title: Text('This release declares no SDK toolchains'),
                subtitle: Text(
                  'The gateway reads install/toolchains.json from the deployed release. A build blocked on a missing toolchain will not resolve until it lists one.',
                ),
              ),
            ] else ...[
              const SizedBox(height: 12),
              for (final entry in toolchains.entries) ...[
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(
                    entry.value['installed'] == true
                        ? Icons.check_circle_outline
                        : Icons.build_outlined,
                  ),
                  title: Text(
                    '${entry.value['label'] ?? entry.key}'
                    '${(entry.value['required'] ?? '') == '' ? '' : ' ${entry.value['required']}'}',
                  ),
                  subtitle: entry.value['installed'] == true
                      ? const Text(
                          'Executable detected. Builds verify the required version.',
                        )
                      : Text(
                          entry.key == sdk['toolchain'] && queued
                              ? 'Installation is in progress.'
                              : (entry.value['required'] ?? '') == ''
                              ? 'This release declares no version for it. State the version '
                                    'where the catalog reads it before installing.'
                              : 'Absent. Required ${entry.value['required']}'
                                    '${(entry.value['declared_by'] ?? '') == '' ? '' : ', declared by ${entry.value['declared_by']}'}. '
                                    'A root service installs it on request.',
                        ),
                ),
                if (entry.value['installed'] != true)
                  FilledButton.icon(
                    onPressed:
                        busy ||
                            queued ||
                            role != 'admin' ||
                            (entry.value['required'] ?? '') == ''
                        ? null
                        : () => install(entry.key),
                    icon: const Icon(Icons.download),
                    label: Text(
                      entry.key == sdk['toolchain'] && queued
                          ? 'Installation running'
                          : 'Install ${entry.value['label'] ?? entry.key}',
                    ),
                  ),
              ],
            ],
            if ('${sdk['error'] ?? ''}'.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  '${sdk['error']}',
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            if (requestError != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  requestError!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            if (sdk['state'] == 'complete' &&
                '${sdk['output'] ?? ''}'.isNotEmpty)
              ExpansionTile(
                title: const Text('Installation output'),
                children: [SelectableText('${sdk['output']}')],
              ),
            if (absent > 0 && role != 'admin' && sdk['state'] != 'unavailable')
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'Only an administrator can request an SDK installation.',
                ),
              ),
            if (refreshing) const LinearProgressIndicator(),
          ],
        ),
      ),
    );
  }
}
