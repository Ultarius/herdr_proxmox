import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'feature_view.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'dashboard_access_controls.dart';

class SshAccessCard extends StatefulWidget {
  const SshAccessCard({super.key});
  @override
  State<SshAccessCard> createState() => _SshAccessCardState();
}

class _SshAccessCardState extends State<SshAccessCard>
    with FeatureView<SshAccessCard> {
  final keyInput = TextEditingController();
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final SshAccessBloc bloc;
  int? get count => bloc.count;
  bool get busy => featureBusy;
  String? get message => featureError ?? featureMessage;
  @override
  void initState() {
    super.initState();
    bindFeature(bloc = SshAccessBloc(connection));
    load();
  }

  Future<void> load() => bloc.refresh();
  Future<void> add() async {
    if (await bloc.add(keyInput.text) && mounted) keyInput.clear();
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
