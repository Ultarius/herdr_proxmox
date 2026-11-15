import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'native_task_ui.dart';
import 'operations_support.dart';
import 'ops_ui.dart';

class ToolchainCard extends StatefulComponent {
  const ToolchainCard({super.key});
  @override
  State<ToolchainCard> createState() => _ToolchainState();
}

class _ToolchainState extends FeatureOperationsState<ToolchainCard> {
  late final ToolchainBloc bloc;
  Map<String, dynamic> get sdk => bloc.sdk;
  bool get refreshing => bloc.state.loading;
  Map<String, dynamic> get toolchains => bloc.toolchains;
  bool get queued => bloc.queued;

  /// Installer-reported percentage, or null when it has not reported one.
  int? get percent {
    final value = (sdk['progress'] as Map?)?['percent'];
    return value is int && value >= 0 && value <= 100 ? value : null;
  }

  String get progressLabel => switch ((sdk['progress'] as Map?)?['phase']) {
    'starting' => 'Starting SDK installation',
    'dependencies' => 'Installing SDK dependencies',
    'download' => 'Downloading SDK',
    'verify' => 'Verifying SDK archive',
    'extract' => 'Extracting SDK archive',
    'initialize' => 'Initializing SDK',
    'done' => 'Finalizing SDK installation',
    _ => 'Installing SDK',
  };
  @override
  void initState() {
    super.initState();
    bindFeature(
      bloc = ToolchainBloc(connection),
      errorFor: (state) => state.errors['install'],
    );
    refresh();
  }

  Future<void> refresh() => bloc.refresh();
  Future<void> install(String name) async {
    await bloc.install(name);
  }

  @override
  Component build(BuildContext context) => card(
    'Herdr build tools',
    [
      if (sdk.isEmpty)
        meta('Loading build tool status…', iconName: 'hourglass')
      else if (sdk['state'] == 'unavailable')
        callout([
          calloutText(
            'Toolchain status unavailable: ${sdk['error']}. Verify the updated gateway was restarted.',
          ),
        ], tone: 'danger')
      else if (sdk['supported'] != true)
        callout([
          calloutText(
            'SDK installation service is not configured. Run bash /opt/herdr-web/install/web-install.sh as root inside the LXC, then refresh.',
          ),
        ], tone: 'warning')
      else if (sdk['catalog_error'] != null)
        callout([
          calloutText('Toolchain catalog unavailable: ${sdk['catalog_error']}'),
        ], tone: 'danger')
      else if (toolchains.isEmpty)
        emptyState(
          'This release declares no SDK toolchains in install/toolchains.json.',
          iconName: 'wrench',
        )
      else
        table(
          ['Toolchain', 'Status', 'Requirement', 'Action'],
          [
            for (final entry in toolchains.entries)
              [
                el('span', [
                  el('strong', [text(entry.value['label'] ?? entry.key)]),
                  if ((entry.value['required'] ?? '') != '')
                    mono(entry.value['required']),
                ], classes: 'toolchain-name'),
                entry.value['installed'] == true
                    ? pill('Executable detected', tone: 'success')
                    : entry.key == sdk['toolchain'] && queued
                    ? pill('Installation in progress', tone: 'info')
                    : pill('Absent', shape: 'dash'),
                text(
                  entry.value['installed'] == true
                      ? 'Builds verify the required version.'
                      : (entry.value['required'] ?? '') == ''
                      ? 'This release declares no version. Declare it before installing.'
                      : 'Required ${entry.value['required']}${(entry.value['declared_by'] ?? '') == '' ? '' : ', declared by ${entry.value['declared_by']}'}.',
                ),
                if (entry.value['installed'] != true)
                  button(
                    'Install ${entry.value['label'] ?? entry.key}',
                    () => install(entry.key),
                    primary: true,
                    disabled:
                        busy ||
                        queued ||
                        !admin ||
                        (entry.value['required'] ?? '') == '',
                    small: true,
                    iconName: 'download',
                  )
                else
                  none(),
              ],
          ],
        ),
      if (sdk['error'] != null && sdk['state'] != 'unavailable')
        callout([calloutText(sdk['error'])], tone: 'danger'),
      // The installer reports real bytes while downloading and phase boundaries
      // elsewhere, so this is determinate when it can be and honest when it
      // cannot. The card polls while queued, which drives it.
      if (queued)
        LinearProgressIndicator(
          value: percent == null ? null : percent! / 100,
          label: progressLabel,
        ),
      if (queued)
        meta('$progressLabel${percent == null ? '' : ' · $percent%'}'),
      if (sdk['state'] == 'complete' && '${sdk['output'] ?? ''}'.isNotEmpty)
        disclosure('Installation output', [codeText(sdk['output'])]),
      if (!admin && toolchains.values.any((v) => v['installed'] != true))
        meta(
          'Only an administrator can request an SDK installation.',
          iconName: 'lock',
        ),
      feedback(),
    ],
    iconName: 'wrench',
    subtitle:
        'Used to build Herdr itself from source. These versions are pinned by the deployed release and '
        'are not a requirement of any project you host. Project requirements live in the project explorer.',
    actions: [
      button(
        'Refresh toolchain status',
        refresh,
        disabled: refreshing,
        small: true,
        variant: 'ghost',
        iconName: 'refresh-cw',
      ),
    ],
    classes: 'toolchain-card',
  );
}
