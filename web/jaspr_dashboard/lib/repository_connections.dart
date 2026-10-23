import 'package:herdr_dashboard_state/repository_connections.dart';
import 'ui.dart';
import 'services.dart';

class RepositoryConnectionsPanel extends StatefulComponent {
  const RepositoryConnectionsPanel({super.key, required this.onChanged});
  final void Function(List<Map<String, dynamic>>) onChanged;
  @override
  State<RepositoryConnectionsPanel> createState() =>
      _RepositoryConnectionsPanelState();
}

class _RepositoryConnectionsPanelState
    extends State<RepositoryConnectionsPanel> {
  String label = '', repository = '', kind = 'ssh', existingFolder = '';
  String token = '';
  String? rotatingId;
  String replacementToken = '';
  late final RepositoryConnectionsController controller;
  List<Map<String, dynamic>> get connections => controller.connections;
  List<Map<String, dynamic>> get bindings => controller.bindings;
  bool get busy => controller.busy || controller.refreshing;
  String? get message => controller.message;
  set message(String? value) => controller.message = value;
  @override
  void initState() {
    super.initState();
    controller = RepositoryConnectionsController(
      dashboardConnection,
      onReset: () {
        token = '';
        replacementToken = '';
        rotatingId = null;
      },
      onUpdate: () {
        if (mounted) setState(() {});
      },
      onChanged: (values) {
        if (mounted) component.onChanged(values);
      },
    );
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  Future<void> refresh() => controller.refresh();
  Future<void> action(String action, Map<String, dynamic> body) =>
      controller.action(action, body);
  String url(Map item) => repositoryConnectionUrl(item);
  Future<void> createConnection() async {
    final secret = token;
    final saved = await controller.action('create', {
      'label': label.trim(),
      'repository': repository.trim(),
      'kind': kind,
      if (kind == 'token') 'token': secret.trim(),
    });
    if (mounted && saved && token == secret) setState(() => token = '');
  }

  Future<void> rotateToken(String id) async {
    final secret = replacementToken;
    final saved = await controller.action('rotate', {
      'id': id,
      'token': secret.trim(),
    });
    if (mounted && saved && rotatingId == id && replacementToken == secret) {
      setState(() {
        replacementToken = '';
        rotatingId = null;
      });
    }
  }

  @override
  Component build(BuildContext context) => !controller.canManage
      ? const Component.empty()
      : el('details', [
          el('summary', [text('Repository connections')]),
          button(
            'Refresh connections',
            refresh,
            disabled: busy || controller.refreshing,
          ),
          el('p', [
            text(
              'Use a repository-specific SSH key, a fine-grained GitHub token, or the configured GitHub account. Credentials remain in the container. These are separate from keys used to log into the container.',
            ),
          ]),
          field('Connection name', label, (value) => label = value),
          field(
            'GitHub repository (owner/repository)',
            repository,
            (value) => repository = value,
          ),
          select(
            'Authentication',
            kind,
            {
              'ssh': 'SSH deploy key',
              'token': repositoryTokenLabel,
              'github': 'Configured GitHub account (HTTPS)',
            },
            (value) => setState(() {
              kind = value;
              token = '';
            }),
            disabled: busy,
          ),
          if (kind == 'token') ...[
            el('p', [text(repositoryTokenGuidance)]),
            el(
              'a',
              [text('Create a fine-grained token on GitHub')],
              attributes: {
                'href': repositoryTokenUrl,
                'target': '_blank',
                'rel': 'noopener noreferrer',
              },
            ),
            field(
              'GitHub personal access token',
              token,
              (value) => token = value,
              password: true,
              autocomplete: 'off',
              spellcheck: false,
              autocorrect: false,
              disabled: busy,
            ),
          ],
          button(repositoryCreateLabel(kind), createConnection, disabled: busy),
          field(
            'Existing project folder (direct child, optional)',
            existingFolder,
            (value) => setState(() => existingFolder = value),
          ),
          for (final item in connections)
            el('section', [
              el('h4', [text(item['label'])]),
              el('p', [
                text(
                  "${item['repository']} · ${item['kind']} · ${item['access_state']}",
                ),
              ]),
              for (final binding in bindings.where(
                (b) => b['connection_id'] == item['id'],
              ))
                el('div', [
                  el('p', [text("Assigned project: ${binding['path']}")]),
                  button(
                    'Unassign project',
                    () => action('unbind', {
                      'id': item['id'],
                      'folder': repositoryBindingFolder(binding),
                    }),
                    disabled: busy,
                    small: true,
                  ),
                ]),
              if (item['tested_at'] != null)
                el('p', [text("Last access test: ${item['tested_at']}")]),
              if (item['public_key'] != null) ...[
                el(
                  'pre',
                  [text(item['public_key'])],
                  attributes: {'aria-label': 'Public key for ${item['label']}'},
                ),
                button('Copy public key', () async {
                  final result = await copyText(
                    '${item['public_key']}',
                    filename: repositoryPublicKeyFilename(item),
                  );
                  if (mounted) setState(() => message = result);
                }, small: true),
                el(
                  'a',
                  [text('Open GitHub deploy keys')],
                  attributes: {
                    'href':
                        "https://github.com/${item['repository']}/settings/keys",
                    'target': '_blank',
                    'rel': 'noopener noreferrer',
                  },
                ),
                el('p', [
                  text(
                    'Add the public key as a read-only deploy key. Then verify the host and test access.',
                  ),
                ]),
                button(
                  item['host_verified'] == true
                      ? 'Re-verify GitHub host key'
                      : 'Verify GitHub host key',
                  () => action('verify_host', {'id': item['id']}),
                  disabled: busy,
                  small: true,
                ),
                if (item['host_fingerprint'] != null)
                  el('p', [text(item['host_fingerprint'])], classes: 'mono'),
              ],
              if (item['error'] != null) notice(item['error'], error: true),
              if (item['kind'] == 'token') ...[
                button(
                  'Rotate token',
                  () => setState(() {
                    rotatingId = '${item['id']}';
                    replacementToken = '';
                  }),
                  disabled: busy,
                  small: true,
                ),
                if (rotatingId == item['id']) ...[
                  el('p', [
                    text(
                      'Replace the saved token without changing project assignments. Test access afterward; revoke the old token on GitHub separately.',
                    ),
                  ]),
                  field(
                    'Replacement GitHub personal access token',
                    replacementToken,
                    (value) => replacementToken = value,
                    id: 'repository-token-rotation-${item['id']}',
                    password: true,
                    autocomplete: 'off',
                    spellcheck: false,
                    autocorrect: false,
                    disabled: busy,
                  ),
                  button(
                    'Save replacement token',
                    () => rotateToken('${item['id']}'),
                    disabled: busy,
                  ),
                  button(
                    'Cancel token rotation',
                    () => setState(() {
                      rotatingId = null;
                      replacementToken = '';
                    }),
                    disabled: busy,
                  ),
                ],
              ],
              button(
                'Test repository access',
                () => action('test', {'id': item['id'], 'url': url(item)}),
                disabled: busy,
                small: true,
              ),
              button(
                'Assign to existing project',
                () => action('bind', {
                  'id': item['id'],
                  'folder': existingFolder.trim(),
                }),
                disabled: busy || existingFolder.trim().isEmpty,
                small: true,
              ),
              button(
                'Remove connection',
                () => controller.prepareRemoval('${item['id']}'),
                disabled:
                    busy ||
                    bindings.any(
                      (binding) => binding['connection_id'] == item['id'],
                    ),
                small: true,
              ),
              if (controller.pendingRemoval == item['id']) ...[
                el('p', [
                  text(
                    'Delete this connection and its local credentials? Revoke its deploy key or token on GitHub separately.',
                  ),
                ]),
                button(
                  'Cancel removal',
                  () => controller.prepareRemoval(null),
                  disabled: busy,
                  small: true,
                ),
                button(
                  'Confirm removal',
                  () => action('remove', {'id': item['id']}),
                  disabled: busy,
                  small: true,
                ),
              ],
            ], classes: 'repository-connection'),
          if (message != null) notice(message),
        ], classes: 'repository-connections');
}
