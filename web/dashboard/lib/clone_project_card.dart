import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'repository_connections.dart';
import 'package:herdr_dashboard_state/repository_connections.dart';
import 'package:herdr_dashboard_state/clone_jobs.dart';

class CloneProjectCard extends StatefulWidget {
  const CloneProjectCard({super.key, required this.onCloned, this.onDeleted});
  final ValueChanged<String> onCloned;
  final ValueChanged<String>? onDeleted;
  @override
  State<CloneProjectCard> createState() => _CloneProjectCardState();
}

class _CloneProjectCardState extends State<CloneProjectCard> {
  final url = TextEditingController();
  final folder = TextEditingController();
  String connectionId = '';
  String? unavailableConnection;
  List<Map<String, dynamic>> connections = [];
  late final CloneJobsBloc bloc;
  StreamSubscription? jobsSubscription;
  int completionRevision = 0;
  bool get busy => bloc.state.busy;
  List<Map<String, dynamic>> get jobs => bloc.jobs;
  String? get removing => bloc.removing;
  String? pendingRemoval;
  bool deleteCloneFolder = false;
  String removalConfirmation = '';
  int? removalGeneration;
  late final DashboardBloc cloneConnection;
  StreamSubscription? cloneSubscription;
  late int cloneGeneration;
  late bool cloneCanRemove;
  final removalTriggers = <String, FocusNode>{};
  final deletionOptionFocus = FocusNode();
  final removalActionFocus = FocusNode();

  bool get canRemove {
    final connection = BlocScope.get<DashboardBloc>();
    return connection.state.connected &&
        connection.operator.value['role'] == 'admin';
  }

  @override
  void initState() {
    super.initState();
    cloneConnection = BlocScope.get<DashboardBloc>();
    bloc = CloneJobsBloc(cloneConnection);
    jobsSubscription = bloc.stream.listen((_) {
      if (!mounted) return;
      if (completionRevision != bloc.completionRevision) {
        completionRevision = bloc.completionRevision;
        if (bloc.clonedFolder != null) widget.onCloned(bloc.clonedFolder!);
      }
      setState(() {});
    });
    cloneGeneration = cloneConnection.generation;
    cloneCanRemove = canRemove;
    cloneConnection.operator.addListener(cloneSessionChanged);
    cloneSubscription = cloneConnection.stream.listen(
      (_) => cloneSessionChanged(),
    );
    refreshJobs();
  }

  void cloneSessionChanged() {
    if (!mounted) return;
    if (cloneGeneration == cloneConnection.generation &&
        cloneCanRemove == canRemove)
      return;
    setState(() {
      if (!canRemove || cloneGeneration != cloneConnection.generation) {
        if (connectionId.isNotEmpty) url.clear();
        connections = [];
        connectionId = '';
        unavailableConnection = null;
      }
      cloneCanRemove = canRemove;
      if (cloneGeneration != cloneConnection.generation) {
        cloneGeneration = cloneConnection.generation;
        completionRevision = 0;
        result = null;
      }
      if (!canRemove || removalGeneration != cloneGeneration)
        pendingRemoval = null;
    });
  }

  Future<void> refreshJobs() => bloc.refresh();

  String? inputError;
  String? get result => inputError ?? bloc.result;
  set result(String? value) => inputError = value;

  Future<void> removeClone(Map<String, dynamic> job) async {
    final connection = BlocScope.get<DashboardBloc>();
    if (busy ||
        !canRemove ||
        pendingRemoval != job['id'] ||
        removalGeneration != connection.generation ||
        (deleteCloneFolder && !cloneFolderIdentityAvailable(job)) ||
        (deleteCloneFolder && removalConfirmation != job['folder']))
      return;
    final epoch = connection.generation;
    setState(() => inputError = null);
    final data = await bloc.remove(
      job,
      deleteFolder: deleteCloneFolder,
      confirmation: removalConfirmation,
    );
    if (!mounted ||
        epoch != connection.generation ||
        !canRemove ||
        data == null)
      return;
    setState(() => pendingRemoval = null);
    if (data['folder_deleted'] == true) widget.onDeleted?.call('${job['cwd']}');
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final trigger = removalTriggers.values
          .where((node) => node.context != null)
          .firstOrNull;
      trigger?.requestFocus();
    });
  }

  Widget removalControls(Map<String, dynamic> job) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        MergeSemantics(
          child: Semantics(
            expanded: pendingRemoval == job['id'],
            controlsNodes: pendingRemoval == job['id']
                ? {'clone-removal-${job['id']}'}
                : const {},
            child: TextButton.icon(
              focusNode: removalTriggers.putIfAbsent(
                '${job['id']}',
                () => FocusNode(),
              ),
              onPressed:
                  busy ||
                      ![
                        'completed',
                        'failed',
                        'interrupted',
                      ].contains(job['state'])
                  ? null
                  : () {
                      setState(() {
                        pendingRemoval = '${job['id']}';
                        removalGeneration =
                            BlocScope.get<DashboardBloc>().generation;
                        deleteCloneFolder = false;
                        removalConfirmation = '';
                      });
                      WidgetsBinding.instance.addPostFrameCallback((_) {
                        if (mounted)
                          (cloneFolderIdentityAvailable(job)
                                  ? deletionOptionFocus
                                  : removalActionFocus)
                              .requestFocus();
                      });
                    },
              icon: const Icon(Icons.delete_outline),
              label: Text(
                'Remove clone',
                semanticsLabel: 'Remove clone ${job['folder']}',
              ),
            ),
          ),
        ),
        if (pendingRemoval == job['id'] &&
            removalGeneration == BlocScope.get<DashboardBloc>().generation)
          Semantics(
            identifier: 'clone-removal-${job['id']}',
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                border: Border.all(
                  color: Theme.of(context).colorScheme.outlineVariant,
                ),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Remove ${job['folder']} from clone history? Files are kept unless you choose deletion below.',
                  ),
                  const Text(cloneRemovalPreservationNotice),
                  if (!cloneFolderIdentityAvailable(job))
                    const Text(legacyCloneRemovalNotice),
                  CheckboxListTile(
                    focusNode: deletionOptionFocus,
                    contentPadding: EdgeInsets.zero,
                    controlAffinity: ListTileControlAffinity.leading,
                    title: const Text(
                      'Also permanently delete the cloned folder',
                    ),
                    value: deleteCloneFolder,
                    onChanged: busy || !cloneFolderIdentityAvailable(job)
                        ? null
                        : (value) => setState(
                            () => deleteCloneFolder = value ?? false,
                          ),
                  ),
                  if (deleteCloneFolder) ...[
                    Text(
                      'This deletes ${job['cwd']} and all files inside, including local changes. This cannot be undone.',
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      key: ValueKey('clone-removal-${job['id']}'),
                      initialValue: removalConfirmation,
                      enabled: !busy,
                      autocorrect: false,
                      decoration: InputDecoration(
                        labelText: 'Type ${job['folder']} to confirm deletion',
                        border: const OutlineInputBorder(),
                      ),
                      onChanged: (value) =>
                          setState(() => removalConfirmation = value),
                    ),
                    const SizedBox(height: 12),
                  ],
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      TextButton(
                        onPressed: busy
                            ? null
                            : () {
                                setState(() => pendingRemoval = null);
                                removalTriggers['${job['id']}']?.requestFocus();
                              },
                        child: const Text('Cancel removal'),
                      ),
                      FilledButton(
                        focusNode: removalActionFocus,
                        onPressed:
                            busy ||
                                (deleteCloneFolder &&
                                    removalConfirmation != job['folder'])
                            ? null
                            : () => removeClone(job),
                        style: deleteCloneFolder
                            ? FilledButton.styleFrom(
                                backgroundColor: Theme.of(
                                  context,
                                ).colorScheme.error,
                                foregroundColor: Theme.of(
                                  context,
                                ).colorScheme.onError,
                              )
                            : null,
                        child: Text(
                          removing == job['id']
                              ? 'Removing…'
                              : deleteCloneFolder
                              ? 'Delete folder and history'
                              : 'Remove history entry',
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
      ],
    ),
  );

  @override
  void dispose() {
    jobsSubscription?.cancel();
    unawaited(bloc.close());
    cloneConnection.operator.removeListener(cloneSessionChanged);
    cloneSubscription?.cancel();
    url.dispose();
    folder.dispose();
    for (final node in removalTriggers.values) {
      node.dispose();
    }
    deletionOptionFocus.dispose();
    removalActionFocus.dispose();
    super.dispose();
  }

  Future<void> clone() async {
    if (unavailableConnection != null ||
        !knownRepositoryConnection(connections, connectionId)) {
      setState(
        () => result =
            'The saved repository connection is unavailable. Refresh connections and select one before cloning.',
      );
      return;
    }
    setState(() => inputError = null);
    await bloc.clone(url.text.trim(), folder.text.trim(), connectionId);
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Clone a project',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 8),
          const Text(
            'Clone a repository into a new folder inside the container’s projects directory. Existing folders are preserved.',
          ),
          const SizedBox(height: 16),
          if (BlocScope.get<DashboardBloc>().operator.value['role'] == 'admin')
            const Text(repositoryCloneTokenGuidance),
          DropdownButtonFormField<String>(
            isExpanded: true,
            key: ValueKey(connectionId),
            initialValue: knownRepositoryConnection(connections, connectionId)
                ? connectionId
                : '',
            decoration: const InputDecoration(
              labelText: 'Repository connection',
            ),
            items: [
              const DropdownMenuItem(
                value: '',
                child: Text('Container Git credentials / public repository'),
              ),
              for (final item in connections)
                DropdownMenuItem(
                  value: '${item['id']}',
                  child: Text(
                    "${item['label']} · ${item['kind']} · ${item['access_state']}",
                  ),
                ),
            ],
            onChanged: busy
                ? null
                : (value) => setState(() {
                    unavailableConnection = null;
                    connectionId = value ?? '';
                    final item = connections
                        .where((item) => item['id'] == value)
                        .firstOrNull;
                    if (item != null) {
                      url.text = item['kind'] == 'ssh'
                          ? "git@github.com:${item['repository']}.git"
                          : "https://github.com/${item['repository']}.git";
                      if (folder.text.isEmpty)
                        folder.text = '${item['repository']}'.split('/').last;
                    }
                  }),
          ),
          TextField(
            key: const PageStorageKey('clone-url-field'),
            controller: url,
            enabled: !busy,
            decoration: const InputDecoration(
              labelText: 'Repository URL',
              hintText: 'https://github.com/owner/repository.git',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            key: const PageStorageKey('clone-folder-field'),
            controller: folder,
            enabled: !busy,
            decoration: const InputDecoration(
              labelText: 'New project folder name',
              hintText: 'my-project',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          const Text(
            'Select a managed repository connection, or use existing container Git credentials. Cloning may take up to two minutes.',
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: busy ? null : clone,
            icon: busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.download_outlined),
            label: Text(busy ? 'Cloning…' : 'Clone repository'),
          ),
          const SizedBox(height: 20),
          if (BlocScope.get<DashboardBloc>().operator.value['role'] == 'admin')
            RepositoryConnectionsPanel(
              onChanged: (values) {
                if (mounted)
                  setState(() {
                    connections = values;
                    if (unavailableConnection != null &&
                        knownRepositoryConnection(
                          values,
                          unavailableConnection!,
                        )) {
                      connectionId = unavailableConnection!;
                      unavailableConnection = null;
                    }
                    if (!values.any((item) => item['id'] == connectionId))
                      if (connectionId.isNotEmpty) {
                        unavailableConnection = connectionId;
                        connectionId = '';
                      }
                  });
              },
            ),
          for (final job in jobs)
            Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ListTile(
                  leading: Icon(
                    job['state'] == 'completed'
                        ? Icons.check_circle_outline
                        : job['state'] == 'failed'
                        ? Icons.error_outline
                        : Icons.download_outlined,
                  ),
                  title: Text('${job['folder']} · ${job['state']}'),
                  subtitle: Text(
                    (job['error'] as String? ?? '').isNotEmpty
                        ? job['error'] as String
                        : job['cwd'] as String,
                  ),
                  trailing:
                      ['failed', 'interrupted'].contains(job['state']) &&
                          job['url'] != null
                      ? TextButton(
                          onPressed: () => setState(() {
                            url.text = '${job['url']}';
                            connectionId = '${job['connection_id'] ?? ''}';
                            unavailableConnection =
                                knownRepositoryConnection(
                                  connections,
                                  connectionId,
                                )
                                ? null
                                : connectionId;
                            if (unavailableConnection != null)
                              connectionId = '';
                            final previous = '${job['folder']}';
                            folder.text =
                                '${previous.substring(0, previous.length.clamp(0, 70))}-retry';
                            result =
                                'The previous folder was preserved. Review the new folder name, then clone again.';
                          }),
                          child: const Text('Retry in a new folder'),
                        )
                      : job['state'] == 'completed'
                      ? TextButton(
                          onPressed: () =>
                              widget.onCloned(job['cwd'] as String),
                          child: const Text('Use project'),
                        )
                      : null,
                ),
                if (canRemove) removalControls(job),
              ],
            ),
          if (result != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: SelectableText(
                result!,
                key: const PageStorageKey('clone-result-message'),
              ),
            ),
        ],
      ),
    ),
  );
}
