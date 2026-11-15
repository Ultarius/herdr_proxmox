import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'operations_support.dart';
import 'dashboard_access_controls.dart';
import 'ops_ui.dart';

class SshAccessCard extends StatefulComponent {
  const SshAccessCard({super.key});
  @override
  State<SshAccessCard> createState() => _SshState();
}

class _SshState extends FeatureOperationsState<SshAccessCard> {
  String publicKey = '';
  late final SshAccessBloc bloc;
  int? get count => bloc.count;
  bool get loading => bloc.state.loading;
  @override
  void initState() {
    super.initState();
    bindFeature(bloc = SshAccessBloc(connection));
    load();
  }

  Future<void> load() => bloc.refresh();
  Future<void> add() async {
    if (await bloc.add(publicKey) && mounted) setState(() => publicKey = '');
  }

  @override
  Component build(BuildContext context) => el('div', [
    card(
      'SSH access (optional)',
      [
        paragraph(
          'Use the dashboard directly on your LAN. Add your computer’s public key for SSH access to agent work terminals. The login user is herdr.',
        ),
        facts([
          ('Login user', mono('herdr')),
          (
            'Public keys',
            count == null
                ? text(
                    loading ? 'Loading key status…' : 'Key status unavailable.',
                  )
                : text(
                    '$count SSH public key${count == 1 ? '' : 's'} configured',
                  ),
          ),
        ]),
        if (error != null)
          button(
            'Retry key status',
            load,
            disabled: loading,
            iconName: 'refresh-cw',
          ),
        textarea(
          'SSH public key (.pub)',
          publicKey,
          (value) => publicKey = value,
        ),
        button(
          busy ? 'Adding key…' : 'Add SSH public key',
          add,
          primary: true,
          disabled: busy || !admin,
          iconName: 'plus',
        ),
        if (!admin)
          meta(
            'Only an administrator can add an SSH public key.',
            iconName: 'lock',
          ),
        feedback(),
      ],
      iconName: 'key-round',
      actions: [
        if (count != null)
          pill(
            count! > 0 ? 'Key configured' : 'No keys',
            tone: count! > 0 ? 'success' : 'neutral',
            shape: count! > 0 ? 'filled' : 'hollow',
          ),
      ],
      classes: 'ssh-card',
    ),
    DashboardAccessControls(keyCount: count ?? 0),
  ], classes: 'access-grid');
}
