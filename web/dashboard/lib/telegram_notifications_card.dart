import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'feature_view.dart';

class TelegramNotificationsCard extends StatefulWidget {
  const TelegramNotificationsCard({super.key});
  @override
  State<TelegramNotificationsCard> createState() => _TelegramState();
}

class _TelegramState extends State<TelegramNotificationsCard>
    with FeatureView<TelegramNotificationsCard> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  late final TelegramNotificationsBloc bloc;
  final token = TextEditingController(), chatId = TextEditingController();
  bool enabled = false, dirty = false;
  late int generation;
  late bool administrator;
  bool get admin => connection.operator.value['role'] == 'admin';

  @override
  void initState() {
    super.initState();
    generation = connection.generation;
    administrator = admin;
    bindFeature(bloc = TelegramNotificationsBloc(connection), onChanged: sync);
    bloc.refresh();
  }

  void sync() {
    if (generation != connection.generation || administrator != admin ||
        !connection.state.connected) {
      token.clear();
      chatId.clear();
      enabled = false;
      dirty = false;
      generation = connection.generation;
      administrator = admin;
    }
    final status = bloc.status;
    if (!dirty && status != null) {
      chatId.text = '${status['chat_id'] ?? ''}';
      enabled = status['enabled'] == true;
    }
  }

  Future<void> save() async {
    final secret = token.text;
    token.clear();
    if (await bloc.configure(enabled: enabled, chatId: chatId.text, token: secret) &&
        mounted) {
      setState(() { dirty = false; sync(); });
    }
  }

  Future<void> disconnect() async {
    if (await bloc.disconnect() && mounted) {
      setState(() { token.clear(); dirty = false; sync(); });
    }
  }

  @override
  void dispose() {
    token.dispose();
    chatId.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final status = bloc.status;
    final configured = status?['token_configured'] == true;
    final busy = featureBusy;
    return Card(child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Telegram notifications', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 8),
        const Text('Receive blocking issues and approval requests from your agents.'),
        if (!admin)
          const Text('Only an administrator can configure Telegram notifications.')
        else ...[
          const SizedBox(height: 8),
          const Text('Create a bot with /newbot in @BotFather, then send /start to your bot. Get your private chat ID from @userinfobot and enter it below.'),
          const SizedBox(height: 12),
          TextField(controller: token, obscureText: true, enableSuggestions: false,
            autocorrect: false, enabled: !busy,
            onChanged: (_) => setState(() => dirty = true),
            decoration: InputDecoration(labelText: 'Bot token',
              helperText: configured ? 'Saved token — leave blank to keep it' : 'Token from @BotFather',
              border: const OutlineInputBorder())),
          const SizedBox(height: 12),
          TextField(controller: chatId, enabled: !busy,
            onChanged: (_) => setState(() => dirty = true),
            decoration: const InputDecoration(labelText: 'Private chat ID',
              hintText: '123456789', border: OutlineInputBorder())),
          SwitchListTile(contentPadding: EdgeInsets.zero,
            title: const Text('Enable Telegram notifications'), value: enabled,
            onChanged: busy ? null : (value) => setState(() { enabled = value; dirty = true; })),
          Wrap(spacing: 12, runSpacing: 8, children: [
            FilledButton(onPressed: busy || status == null ? null : save,
                child: const Text('Save Telegram settings')),
            OutlinedButton(onPressed: busy || !configured || dirty ? null :
                () async { await bloc.test(); },
                child: const Text('Send test notification')),
            TextButton(onPressed: busy || !configured ? null : disconnect,
                child: const Text('Disconnect Telegram')),
            TextButton(onPressed: busy ? null : bloc.refresh,
                child: const Text('Refresh Telegram status')),
          ]),
          if (dirty) const Text('Save your changes before sending a test.'),
          if ('${status?['last_error'] ?? ''}'.isNotEmpty) Text('${status!['last_error']}'),
        ],
        if (featureBusy) const LinearProgressIndicator(),
        if (featureError != null) Text(featureError!),
        if (featureMessage != null) Text(featureMessage!),
      ]),
    ));
  }
}
