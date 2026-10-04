import 'package:juice/juice.dart';
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
  String? result;

  @override
  void dispose() {
    url.dispose();
    folder.dispose();
    super.dispose();
  }

  Future<void> clone() async {
    setState(() {
      busy = true;
      result = null;
    });
    try {
      final data = await BlocScope.get<DashboardBloc>().request(
        'projects/clone',
        {'url': url.text.trim(), 'folder': folder.text.trim()},
      );
      if (!mounted) return;
      widget.onCloned(data['cwd'] as String);
      setState(
        () => result =
            'Cloned into ${data['cwd']}. The workspace directory above is filled in. Select this path in agent profiles too.',
      );
    } catch (error) {
      if (mounted) setState(() => result = error.toString());
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
