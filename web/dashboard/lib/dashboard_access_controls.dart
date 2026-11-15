import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'feature_view.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

class DashboardAccessControls extends StatefulWidget {
  const DashboardAccessControls({super.key, required this.keyCount});
  final int keyCount;
  @override
  State<DashboardAccessControls> createState() =>
      _DashboardAccessControlsState();
}

class _DashboardAccessControlsState extends State<DashboardAccessControls>
    with FeatureView<DashboardAccessControls> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final DashboardAccessBloc bloc;
  String? get mode => bloc.mode;
  String? get pending => bloc.pending;
  String? get message => featureError ?? featureMessage;
  bool get busy => featureBusy;
  @override
  void initState() {
    super.initState();
    bindFeature(bloc = DashboardAccessBloc(connection));
    load();
  }

  Future<void> load() => bloc.refresh();

  Future<void> change(String target) async {
    final epoch = connection.generation;
    bool tested = false;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: Text(
            target == 'ssh' ? 'Enable SSH-only access?' : 'Enable LAN access?',
          ),
          content: SizedBox(
            width: 560,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (target == 'ssh') ...[
                    const Text(
                      'Test this tunnel from your computer and open http://127.0.0.1:8787 first. Direct LAN access closes 5 seconds after applying.',
                    ),
                    const SizedBox(height: 12),
                    SelectableText(
                      'ssh -N -L 8787:127.0.0.1:8787 herdr@${Uri.base.host == "localhost" || Uri.base.host == "127.0.0.1" ? "<lxc-ip>" : Uri.base.host}',
                    ),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: tested,
                      onChanged: (value) =>
                          update(() => tested = value == true),
                      title: const Text(
                        'I tested the tunnel and can open the dashboard through it.',
                      ),
                    ),
                  ] else
                    const Text(
                      'The dashboard will accept direct LAN connections. Access-token login remains required. The change applies in 5 seconds.',
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: target == 'ssh' && !tested
                  ? null
                  : () => Navigator.pop(context, true),
              child: const Text('Apply access mode'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true ||
        !mounted ||
        !connection.state.connected ||
        epoch != connection.generation)
      return;
    await bloc.change(target, tunnelReady: tested);
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text('Dashboard access', style: Theme.of(context).textTheme.titleMedium),
      if (mode != null)
        Text(
          'Current mode: ${mode == "ssh" ? "SSH only" : "Local network"}${pending == null ? "" : " · switching to $pending"}',
        ),
      const Text(
        'SSH keys protect SSH login. Choose whether the dashboard also requires an SSH tunnel.',
      ),
      const SizedBox(height: 12),
      Wrap(
        spacing: 12,
        runSpacing: 8,
        children: [
          OutlinedButton(
            onPressed: busy || pending != null || mode == null || mode == 'lan'
                ? null
                : () => change('lan'),
            child: const Text('Allow LAN access'),
          ),
          FilledButton(
            onPressed:
                busy ||
                    pending != null ||
                    mode == null ||
                    mode == 'ssh' ||
                    widget.keyCount < 1
                ? null
                : () => change('ssh'),
            child: const Text('Require SSH tunnel'),
          ),
          TextButton(
            onPressed: busy ? null : load,
            child: const Text('Refresh access status'),
          ),
        ],
      ),
      if (message != null) Text(message!),
    ],
  );
}
