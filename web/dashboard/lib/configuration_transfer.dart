import 'dart:convert';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'artifact_download.dart';
import 'configuration_file.dart';

class ConfigurationTransfer extends StatefulWidget {
  const ConfigurationTransfer({super.key, required this.onImported});
  final Future<void> Function() onImported;
  @override
  State<ConfigurationTransfer> createState() => _ConfigurationTransferState();
}

class _ConfigurationTransferState extends State<ConfigurationTransfer> {
  bool busy = false;
  String? message;
  Future<void> transfer(bool importing) async {
    setState(() {
      busy = true;
      message = null;
    });
    try {
      final connection = BlocScope.get<DashboardBloc>();
      if (!importing) {
        final data = await connection.request('organizations/export');
        // Preserve complete backups even when the destination import limit is
        // exceeded; warn clearly rather than downloading silently truncated data.
        final oversized = data['import_size_supported'] == false;
        await downloadArtifact(
          const JsonEncoder.withIndent('  ').convert(data),
          'herdr-configuration.json',
        );
        message = oversized
            ? 'Backup downloaded, but exceeds the 2 MB import limit. Do not replace the LXC until migration capacity is increased.'
            : 'Configuration download started. Keep this file before replacing the LXC.';
      } else {
        final content = await readConfigurationFile();
        if (content == null || !mounted) return;
        final data = jsonDecode(content) as Map<String, dynamic>;
        if (data['format'] != 'herdr-configuration' || data['version'] != 1)
          throw const FormatException('Unsupported configuration format.');
        final confirmed = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Import configuration?'),
            content: Text(
              '${(data['organizations'] as List).length} organizations, ${(data['profiles'] as List).length} agents, ${(data['groups'] as List).length} groups.\nExisting records will not be overwritten. Paths and permission settings are preserved; review them for this container before launching. No agents start automatically.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Import'),
              ),
            ],
          ),
        );
        if (confirmed != true) return;
        await connection.request('organizations/import', data);
        await widget.onImported();
        message =
            'Configuration imported. Review project paths and connect CLI accounts before launching.';
      }
    } catch (e) {
      message = 'Configuration transfer failed: $e';
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ExpansionTile(
    title: const Text('Migration & backup'),
    subtitle: const Text('Export organizations, agent settings and groups'),
    children: [
      const Padding(
        padding: EdgeInsets.all(12),
        child: Text(
          'Includes archived entries, models, personas, permissions, project paths, worktree preferences, reporting lines and group membership. Runtime sessions, discussions, repository files, recovery snapshots and credentials need separate backups.',
        ),
      ),
      Wrap(
        spacing: 12,
        children: [
          OutlinedButton.icon(
            onPressed: busy ? null : () => transfer(false),
            icon: const Icon(Icons.download),
            label: const Text('Export configuration'),
          ),
          OutlinedButton.icon(
            onPressed: busy ? null : () => transfer(true),
            icon: const Icon(Icons.upload),
            label: const Text('Import configuration'),
          ),
        ],
      ),
      if (message != null)
        Padding(
          padding: const EdgeInsets.all(12),
          child: SelectableText(message!),
        ),
    ],
  );
}
