import 'package:juice/juice.dart';
import 'dart:convert';
import 'dashboard_bloc.dart';
import 'repository_connections.dart';
import 'package:herdr_dashboard_state/repository_connections.dart';

class CloneProjectCard extends StatefulWidget {
  const CloneProjectCard({super.key, required this.onCloned});
  final ValueChanged<String> onCloned;
  @override
  State<CloneProjectCard> createState() => _CloneProjectCardState();
}

class _CloneProjectCardState extends State<CloneProjectCard> {
  final url = TextEditingController();
  final folder = TextEditingController();
  String connectionId = '';
  String? unavailableConnection;
  List<Map<String, dynamic>> connections = [];
  bool busy = false;
  Timer? timer;
  List<Map<String, dynamic>> jobs = [];
  String? completed;
  bool polling = false;

  @override
  void initState() {
    super.initState();
    refreshJobs();
    timer = Timer.periodic(const Duration(seconds: 3), (_) => refreshJobs());
  }

  Future<void> refreshJobs() async {
    final connection = BlocScope.get<DashboardBloc>();
    if (polling || !connection.state.connected) return;
    polling = true;
    final epoch = connection.generation;
    try {
      final data = await connection.request('projects/jobs');
      if (!mounted || epoch != connection.generation) return;
      final next = List<Map<String, dynamic>>.from(data['jobs'] ?? []);
      if (jsonEncode(next) != jsonEncode(jobs)) setState(() => jobs = next);
      final latest = jobs.firstOrNull;
      if (latest != null &&
          ['failed', 'interrupted', 'completed'].contains(latest['state']) &&
          result?.startsWith('Clone queued.') == true) {
        setState(() => result = null);
      }
      if (latest?['state'] == 'completed' && latest?['id'] != completed) {
        completed = latest!['id'] as String;
        widget.onCloned(latest['cwd'] as String);
      }
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(
          () => result =
              'Clone status unavailable; the server job may still be running. $exception',
        );
    } finally {
      polling = false;
    }
  }

  String? result;

  @override
  void dispose() {
    timer?.cancel();
    url.dispose();
    folder.dispose();
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
    final connection = BlocScope.get<DashboardBloc>();
    final epoch = connection.generation;
    setState(() {
      busy = true;
      result = null;
    });
    try {
      final data = await connection.request('projects/clone', {
        'url': url.text.trim(),
        'folder': folder.text.trim(),
        if (connectionId.isNotEmpty) 'connection_id': connectionId,
      });
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        jobs.insert(0, Map<String, dynamic>.from(data));
        result =
            'Clone queued. You can navigate away and return to see its status.';
      });
      await refreshJobs();
    } catch (error) {
      if (mounted && epoch == connection.generation)
        setState(() => result = error.toString());
    } finally {
      if (mounted && epoch == connection.generation)
        setState(() => busy = false);
    }
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
          for (final job in jobs)
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
                            knownRepositoryConnection(connections, connectionId)
                            ? null
                            : connectionId;
                        if (unavailableConnection != null) connectionId = '';
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
                      onPressed: () => widget.onCloned(job['cwd'] as String),
                      child: const Text('Use project'),
                    )
                  : null,
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
