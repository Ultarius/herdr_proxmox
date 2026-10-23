import 'package:herdr_dashboard_state/repository_connections.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'clipboard_copy.dart';
import 'external_link.dart';

class RepositoryConnectionsPanel extends StatefulWidget {
  const RepositoryConnectionsPanel({super.key, required this.onChanged});
  final ValueChanged<List<Map<String, dynamic>>> onChanged;
  @override
  State<RepositoryConnectionsPanel> createState() =>
      _RepositoryConnectionsPanelState();
}

class _RepositoryConnectionsPanelState
    extends State<RepositoryConnectionsPanel> {
  final label = TextEditingController(),
      repository = TextEditingController(),
      token = TextEditingController(),
      replacementToken = TextEditingController(),
      folder = TextEditingController();
  String kind = 'ssh';
  String? rotatingId;
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
      BlocScope.get<DashboardBloc>(),
      onReset: () {
        token.clear();
        replacementToken.clear();
        rotatingId = null;
      },
      onUpdate: () {
        if (mounted) setState(() {});
      },
      onChanged: (values) {
        if (mounted) widget.onChanged(values);
      },
    );
  }

  @override
  void dispose() {
    controller.dispose();
    label.dispose();
    repository.dispose();
    token.dispose();
    replacementToken.dispose();
    folder.dispose();
    super.dispose();
  }

  Future<void> refresh() => controller.refresh();
  Future<void> action(String action, Map<String, dynamic> body) =>
      controller.action(action, body);
  String url(Map item) => repositoryConnectionUrl(item);
  Future<void> createConnection() async {
    final secret = token.text;
    final saved = await controller.action('create', {
      'label': label.text.trim(),
      'repository': repository.text.trim(),
      'kind': kind,
      if (kind == 'token') 'token': secret.trim(),
    });
    if (mounted && saved && token.text == secret) token.clear();
  }

  Future<void> rotateToken(String id) async {
    final secret = replacementToken.text;
    final saved = await controller.action('rotate', {
      'id': id,
      'token': secret.trim(),
    });
    if (mounted &&
        saved &&
        rotatingId == id &&
        replacementToken.text == secret) {
      setState(() {
        replacementToken.clear();
        rotatingId = null;
      });
    }
  }

  @override
  Widget build(BuildContext context) => !controller.canManage
      ? const SizedBox.shrink()
      : ExpansionTile(
          key: const PageStorageKey('repository-connections-panel'),
          title: const Text('Repository connections'),
          childrenPadding: const EdgeInsets.all(12),
          children: [
            TextButton(
              onPressed: busy || controller.refreshing ? null : refresh,
              child: const Text('Refresh connections'),
            ),
            const Text(
              'Use a repository-specific SSH key, a fine-grained GitHub token, or the configured GitHub account. Credentials remain in the container. These are separate from keys used to log into the container.',
            ),
            TextField(
              key: const PageStorageKey('repository-connection-name'),
              controller: label,
              decoration: const InputDecoration(labelText: 'Connection name'),
            ),
            TextField(
              key: const PageStorageKey('repository-connection-repository'),
              controller: repository,
              decoration: const InputDecoration(
                labelText: 'GitHub repository (owner/repository)',
              ),
            ),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: kind,
              decoration: const InputDecoration(labelText: 'Authentication'),
              items: const [
                DropdownMenuItem(value: 'ssh', child: Text('SSH deploy key')),
                DropdownMenuItem(
                  value: 'token',
                  child: Text(repositoryTokenLabel),
                ),
                DropdownMenuItem(
                  value: 'github',
                  child: Text('Configured GitHub account (HTTPS)'),
                ),
              ],
              onChanged: busy
                  ? null
                  : (value) => setState(() {
                      kind = value!;
                      token.clear();
                    }),
            ),
            if (kind == 'token') ...[
              const Text(repositoryTokenGuidance),
              TextButton(
                onPressed: () =>
                    openExternalLink(Uri.parse(repositoryTokenUrl)),
                child: const Text('Create a fine-grained token on GitHub'),
              ),
              TextField(
                key: const PageStorageKey('repository-connection-token'),
                controller: token,
                enabled: !busy,
                obscureText: true,
                autofillHints: null,
                enableSuggestions: false,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'GitHub personal access token',
                ),
              ),
            ],
            FilledButton(
              onPressed: busy ? null : createConnection,
              child: Text(repositoryCreateLabel(kind)),
            ),
            TextField(
              key: const PageStorageKey('repository-connection-folder'),
              controller: folder,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                labelText: 'Existing project folder (direct child, optional)',
              ),
            ),
            for (final item in connections)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${item['label']}',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    Text(
                      "${item['repository']} · ${item['kind']} · ${item['access_state']}",
                    ),
                    for (final binding in bindings.where(
                      (b) => b['connection_id'] == item['id'],
                    ))
                      Row(
                        children: [
                          Expanded(
                            child: Text("Assigned project: ${binding['path']}"),
                          ),
                          TextButton(
                            onPressed: busy
                                ? null
                                : () => action('unbind', {
                                    'id': item['id'],
                                    'folder': repositoryBindingFolder(binding),
                                  }),
                            child: const Text('Unassign project'),
                          ),
                        ],
                      ),
                    if (item['tested_at'] != null)
                      Text("Last access test: ${item['tested_at']}"),
                    if (item['public_key'] != null) ...[
                      SelectableText(
                        '${item['public_key']}',
                        key: PageStorageKey(
                          'repository-public-key-${item['id']}',
                        ),
                      ),
                      TextButton(
                        onPressed: () async {
                          final result = await copyTextOrDownload(
                            '${item['public_key']}',
                            repositoryPublicKeyFilename(item),
                          );
                          if (mounted) setState(() => message = result);
                        },
                        child: const Text('Copy public key'),
                      ),
                      TextButton(
                        onPressed: () => openExternalLink(
                          Uri.parse(
                            "https://github.com/${item['repository']}/settings/keys",
                          ),
                        ),
                        child: const Text('Open GitHub deploy keys'),
                      ),
                      const Text(
                        'Add the public key as a read-only deploy key, then verify the host and test access.',
                      ),
                      TextButton(
                        onPressed: busy
                            ? null
                            : () => action('verify_host', {'id': item['id']}),
                        child: Text(
                          item['host_verified'] == true
                              ? 'Re-verify GitHub host key'
                              : 'Verify GitHub host key',
                        ),
                      ),
                      if (item['host_fingerprint'] != null)
                        SelectableText(
                          '${item['host_fingerprint']}',
                          key: PageStorageKey(
                            'repository-host-key-${item['id']}',
                          ),
                        ),
                    ],
                    if (item['error'] != null) Text('${item['error']}'),
                    if (item['kind'] == 'token') ...[
                      TextButton(
                        onPressed: busy
                            ? null
                            : () => setState(() {
                                rotatingId = '${item['id']}';
                                replacementToken.clear();
                              }),
                        child: const Text('Rotate token'),
                      ),
                      if (rotatingId == item['id']) ...[
                        const Text(
                          'Replace the saved token without changing project assignments. Test access afterward; revoke the old token on GitHub separately.',
                        ),
                        TextField(
                          key: PageStorageKey(
                            'repository-token-rotation-${item['id']}',
                          ),
                          controller: replacementToken,
                          enabled: !busy,
                          obscureText: true,
                          autofillHints: null,
                          enableSuggestions: false,
                          autocorrect: false,
                          decoration: const InputDecoration(
                            labelText:
                                'Replacement GitHub personal access token',
                          ),
                        ),
                        TextButton(
                          onPressed: busy
                              ? null
                              : () => rotateToken('${item['id']}'),
                          child: const Text('Save replacement token'),
                        ),
                        TextButton(
                          onPressed: busy
                              ? null
                              : () => setState(() {
                                  rotatingId = null;
                                  replacementToken.clear();
                                }),
                          child: const Text('Cancel token rotation'),
                        ),
                      ],
                    ],
                    Wrap(
                      spacing: 8,
                      children: [
                        TextButton(
                          onPressed: busy
                              ? null
                              : () => action('test', {
                                  'id': item['id'],
                                  'url': url(item),
                                }),
                          child: const Text('Test repository access'),
                        ),
                        TextButton(
                          onPressed: busy || folder.text.trim().isEmpty
                              ? null
                              : () => action('bind', {
                                  'id': item['id'],
                                  'folder': folder.text.trim(),
                                }),
                          child: const Text('Assign to existing project'),
                        ),
                        TextButton(
                          onPressed:
                              busy ||
                                  bindings.any(
                                    (binding) =>
                                        binding['connection_id'] == item['id'],
                                  )
                              ? null
                              : () =>
                                    controller.prepareRemoval('${item['id']}'),
                          child: const Text('Remove connection'),
                        ),
                      ],
                    ),
                    if (controller.pendingRemoval == item['id']) ...[
                      const Text(
                        'Delete this connection and its local credentials? Revoke its deploy key or token on GitHub separately.',
                      ),
                      Wrap(
                        children: [
                          TextButton(
                            onPressed: busy
                                ? null
                                : () => controller.prepareRemoval(null),
                            child: const Text('Cancel removal'),
                          ),
                          TextButton(
                            onPressed: busy
                                ? null
                                : () => action('remove', {'id': item['id']}),
                            child: const Text('Confirm removal'),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
            if (message != null)
              SelectableText(
                message!,
                key: const PageStorageKey('repository-connection-message'),
              ),
          ],
        );
}
