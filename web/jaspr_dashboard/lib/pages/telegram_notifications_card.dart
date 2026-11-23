import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'operations_support.dart';
import 'ops_ui.dart';

class TelegramNotificationsCard extends StatefulComponent {
  const TelegramNotificationsCard({super.key});
  @override
  State<TelegramNotificationsCard> createState() => _TelegramState();
}

class _TelegramState extends FeatureOperationsState<TelegramNotificationsCard> {
  late final TelegramNotificationsBloc bloc;
  String token = '', chatId = '';
  bool enabled = false, dirty = false;
  late int generation;
  late bool administrator;

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
      token = '';
      chatId = '';
      enabled = false;
      dirty = false;
      generation = connection.generation;
      administrator = admin;
    }
    final status = bloc.status;
    if (!dirty && status != null) {
      chatId = '${status['chat_id'] ?? ''}';
      enabled = status['enabled'] == true;
    }
  }

  Future<void> save() async {
    final secret = token;
    setState(() => token = '');
    if (await bloc.configure(enabled: enabled, chatId: chatId, token: secret) &&
        mounted) {
      setState(() { dirty = false; sync(); });
    }
  }

  Future<void> disconnect() async {
    if (await bloc.disconnect() && mounted) {
      setState(() { token = ''; dirty = false; sync(); });
    }
  }

  @override
  Component build(BuildContext context) {
    final status = bloc.status;
    final configured = status?['token_configured'] == true;
    final savedEnabled = status?['enabled'] == true;
    return card('Telegram notifications', [
      paragraph('Receive blocking issues and approval requests from your agents.'),
      if (!admin)
        meta('Only an administrator can configure Telegram notifications.',
            iconName: 'lock')
      else ...[
        paragraph('Create a bot with /newbot in @BotFather, then send /start to your bot. Get your private chat ID from @userinfobot and enter it below.'),
        field('Bot token', token,
            (value) => setState(() { token = value; dirty = true; }),
            password: true, autocomplete: 'new-password',
            placeholder: configured ? 'Saved token — leave blank to keep it' : 'Token from @BotFather',
            disabled: busy),
        field('Private chat ID', chatId,
            (value) => setState(() { chatId = value; dirty = true; }),
            placeholder: '123456789', disabled: busy),
        switchField('Enable Telegram notifications', enabled,
            (value) => setState(() { enabled = value; dirty = true; }),
            disabled: busy),
        actions([
          button('Save Telegram settings', save, primary: true,
              disabled: busy || status == null),
          button('Send test notification', () async { await bloc.test(); },
              disabled: busy || !configured || dirty, iconName: 'send'),
          button('Disconnect Telegram', disconnect,
              disabled: busy || !configured),
          button('Refresh Telegram status', bloc.refresh,
              disabled: busy, iconName: 'refresh-cw'),
        ]),
        if (dirty) meta('Save your changes before sending a test.'),
        if ('${status?['last_error'] ?? ''}'.isNotEmpty)
          callout([calloutText(status!['last_error'])], tone: 'warning'),
      ],
      feedback(),
    ], iconName: 'bell', actions: [
      if (admin && status != null)
        pill(savedEnabled ? 'Enabled' : 'Disabled',
            tone: savedEnabled ? 'success' : 'neutral',
            shape: savedEnabled ? 'filled' : 'hollow'),
    ]);
  }
}
