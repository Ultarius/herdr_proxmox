import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'package:web/web.dart' as web;
import 'operations_support.dart';
import 'ops_ui.dart';

class DashboardAccessControls extends StatefulComponent {
  const DashboardAccessControls({super.key, required this.keyCount});
  final int keyCount;
  @override
  State<DashboardAccessControls> createState() => _AccessState();
}

class _AccessState extends FeatureOperationsState<DashboardAccessControls> {
  late final DashboardAccessBloc bloc;
  String? get mode => bloc.mode;
  String? get pending => bloc.pending;
  @override
  void initState() {
    super.initState();
    bindFeature(bloc = DashboardAccessBloc(connection));
    load();
  }

  Future<void> load() => bloc.refresh();

  Future<void> change(String target) async {
    if (busy ||
        pending != null ||
        mode == null ||
        (target == 'ssh' && component.keyCount < 1))
      return;
    final epoch = connection.generation;
    var tested = false;
    final confirmed = await showDialog<bool>(
      target == 'ssh' ? 'Enable SSH-only access?' : 'Enable LAN access?',
      (close) => stack([
        if (target == 'ssh') ...[
          paragraph(
            'Test this tunnel and open http://127.0.0.1:8787 first. Direct LAN access closes 5 seconds after applying.',
          ),
          codeText(
            'ssh -N -L 8787:127.0.0.1:8787 herdr@${['localhost', '127.0.0.1'].contains(web.window.location.hostname) ? '<lxc-ip>' : web.window.location.hostname}',
            label: 'SSH dashboard tunnel command',
          ),
          toggle(
            'I tested the tunnel and can open the dashboard through it.',
            tested,
            (value) {
              tested = value;
              refreshDialogs();
            },
          ),
        ] else
          paragraph(
            'The dashboard will accept direct LAN connections. Access-token login remains required. The change applies in 5 seconds.',
          ),
        actions([
          button('Cancel', () => close(false)),
          button(
            'Apply access mode',
            () => close(true),
            primary: true,
            disabled: target == 'ssh' && !tested,
          ),
        ]),
      ]),
    );
    if (confirmed != true || !valid(epoch)) return;
    await bloc.change(target, tunnelReady: tested);
  }

  @override
  Component build(BuildContext context) => card(
    'Dashboard access',
    [
      paragraph(
        'SSH keys protect SSH login. Choose whether the dashboard also requires an SSH tunnel.',
      ),
      if (mode != null)
        facts([
          ('Current mode', text(mode == 'ssh' ? 'SSH only' : 'Local network')),
          if (pending != null) ('Switching to', text(pending)),
        ]),
      actions([
        button(
          'Allow LAN access',
          () => change('lan'),
          disabled:
              !admin ||
              busy ||
              pending != null ||
              mode == null ||
              mode == 'lan',
          iconName: 'wifi',
        ),
        button(
          'Require SSH tunnel',
          () => change('ssh'),
          disabled:
              !admin ||
              busy ||
              pending != null ||
              mode == null ||
              mode == 'ssh' ||
              component.keyCount < 1,
          iconName: 'lock',
        ),
        button(
          'Refresh access status',
          load,
          disabled: busy,
          variant: 'ghost',
          iconName: 'refresh-cw',
        ),
      ]),
      if (mode != 'ssh' && component.keyCount < 1)
        meta(
          'Add an SSH public key before requiring an SSH tunnel.',
          iconName: 'info',
        ),
      if (!admin)
        meta(
          'Only an administrator can change dashboard access.',
          iconName: 'lock',
        ),
      feedback(),
    ],
    iconName: 'shield-check',
    actions: [
      if (mode != null)
        pill(
          pending != null
              ? 'Switching'
              : mode == 'ssh'
              ? 'SSH only'
              : 'Local network',
          tone: pending != null ? 'warning' : 'neutral',
          shape: pending != null ? 'filled' : 'hollow',
        ),
    ],
    classes: 'access-card',
  );
}
