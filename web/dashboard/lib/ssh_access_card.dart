import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'dashboard_access_controls.dart';

class SshAccessCard extends StatefulWidget {
  const SshAccessCard({super.key});
  @override
  State<SshAccessCard> createState() => _SshAccessCardState();
}

class _SshAccessCardState extends State<SshAccessCard> {
  final keyInput = TextEditingController();
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  int? count;
  bool busy = false;
  String? message;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    final epoch = connection.generation;
    try {
      final data = await connection.request('ssh-access');
      if (mounted &&
          connection.state.connected &&
          epoch == connection.generation) {
        setState(() => count = data['key_count'] as int?);
      }
    } catch (error) {
      if (mounted)
        setState(
          () => message = error.toString().replaceFirst('Exception: ', ''),
        );
    }
  }

  Future<void> add() async {
    final epoch = connection.generation;
    setState(() {
      busy = true;
      message = null;
    });
    try {
      final data = await connection.request('ssh-access/add', {
        'public_key': keyInput.text,
      });
      if (mounted &&
          connection.state.connected &&
          epoch == connection.generation) {
        keyInput.clear();
        setState(() {
          count = data['key_count'] as int;
          message = data['added'] == true
              ? 'Public key added. You can now connect as herdr over SSH.'
              : 'This public key is already configured.';
        });
      }
    } catch (error) {
      if (mounted)
        setState(
          () => message = error.toString().replaceFirst('Exception: ', ''),
        );
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    keyInput.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'SSH access (optional)',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 8),
          const Text(
            'Use the dashboard directly on your LAN. Add your computer’s public key here when you want SSH access to agent work terminals. The login user is herdr.',
          ),
          if (count != null)
            Text('$count SSH public key${count == 1 ? "" : "s"} configured'),
          const SizedBox(height: 12),
          TextField(
            controller: keyInput,
            minLines: 2,
            maxLines: 4,
            decoration: const InputDecoration(
              labelText: 'SSH public key (.pub)',
              hintText: 'ssh-ed25519 AAAA… your-computer',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: busy ? null : add,
            child: Text(busy ? 'Adding key…' : 'Add SSH public key'),
          ),
          const SizedBox(height: 24),
          DashboardAccessControls(keyCount: count ?? 0),
          if (message != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(message!),
            ),
        ],
      ),
    ),
  );
}
