import 'package:juice/juice.dart';
import 'dart:convert';
import 'dashboard_bloc.dart';

class CloneProjectCard extends StatefulWidget {
  const CloneProjectCard({super.key, required this.onCloned});
  final ValueChanged<String> onCloned;
  @override
  State<CloneProjectCard> createState() => _CloneProjectCardState();
}

class _CloneProjectCardState extends State<CloneProjectCard> {
  final url = TextEditingController();
  final folder = TextEditingController();
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
      if (mounted) setState(() => busy = false);
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
          TextField(
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
            'Private repositories use Git credentials already configured in the container. SSH requires a configured key and trusted host. Cloning may take up to two minutes.',
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
              trailing: job['state'] == 'completed'
                  ? TextButton(
                      onPressed: () => widget.onCloned(job['cwd'] as String),
                      child: const Text('Use project'),
                    )
                  : null,
            ),
          if (result != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: SelectableText(result!),
            ),
        ],
      ),
    ),
  );
}
