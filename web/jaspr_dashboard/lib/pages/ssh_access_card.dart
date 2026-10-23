import 'operations_support.dart';
import 'dashboard_access_controls.dart';
import 'ops_ui.dart';

class SshAccessCard extends StatefulComponent {
  const SshAccessCard({super.key});
  @override
  State<SshAccessCard> createState() => _SshState();
}

class _SshState extends OperationsState<SshAccessCard> {
  String publicKey = '';
  int? count;
  bool loading = false;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    if (loading || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final data = await connection.request('ssh-access');
      if (valid(epoch))
        setState(() {
          count = data['key_count'] as int?;
          error = null;
        });
    } catch (e) {
      if (valid(epoch)) setState(() => error = '$e');
    } finally {
      if (valid(epoch)) setState(() => loading = false);
    }
  }

  Future<void> add() => perform((epoch) async {
    final data = await connection.request('ssh-access/add', {
      'public_key': publicKey,
    });
    if (valid(epoch))
      setState(() {
        publicKey = '';
        count = data['key_count'] as int;
        message = data['added'] == true
            ? 'Public key added. You can now connect as herdr over SSH.'
            : 'This public key is already configured.';
      });
  });
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
