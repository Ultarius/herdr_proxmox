import 'package:juice/juice.dart';
import 'package:herdr_dashboard_state/storage.dart';
import 'dashboard_bloc.dart';

class StoragePanel extends StatefulWidget {
  const StoragePanel({super.key});
  @override
  State<StoragePanel> createState() => _StoragePanelState();
}

class _StoragePanelState extends State<StoragePanel> {
  late final StorageController controller;

  @override
  void initState() {
    super.initState();
    controller = StorageController(
      BlocScope.get<DashboardBloc>(),
      onUpdate: () {
        if (mounted) setState(() {});
      },
    );
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    if (!c.canManage) return const SizedBox.shrink();
    final disabled = c.loading || c.busy || c.scanning;
    return ExpansionTile(
      key: const PageStorageKey('managed-storage-panel'),
      tilePadding: EdgeInsets.zero,
      title: const Text('Managed build storage'),
      subtitle: Text(
        c.scanning
            ? 'Scanning registered storage…'
            : c.data['sampled_at'] == null
            ? 'Scan to measure registered build outputs and download caches'
            : '${storageBytes(c.inventory['total_bytes'])} registered · ${storageBytes(c.inventory['reclaimable_bytes'])} reclaimable',
      ),
      childrenPadding: const EdgeInsets.symmetric(vertical: 12),
      children: [
        const Text(
          'Generated build outputs belong to each execution. Download caches can be reused; installed dependency trees stay private. Source edits and retained evidence are preserved.',
        ),
        if (c.loading) const LinearProgressIndicator(),
        if (c.error != null)
          Text(
            c.error!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        if (c.message != null) Text(c.message!),
        if (c.data['error'] != null) Text('${c.data['error']}'),
        for (final pending in storageEntries(c.data['finalization_errors']))
          Text(
            'Storage finalization pending for ${pending['execution_id']}: ${pending['error']}',
          ),
        Wrap(
          spacing: 12,
          children: [
            TextButton.icon(
              onPressed: disabled ? null : () => c.action('scan'),
              icon: const Icon(Icons.search),
              label: const Text('Scan storage'),
            ),
            TextButton(
              onPressed: disabled ? null : c.refresh,
              child: const Text('Refresh saved inventory'),
            ),
          ],
        ),
        if (c.data['sampled_at'] != null)
          Text('Inventory sampled: ${c.data['sampled_at']}'),
        if (c.policy.isNotEmpty) ...[
          DropdownButtonFormField<String>(
            initialValue: c.policy['build_mode'] == 'full' ? 'full' : 'compact',
            key: ValueKey('build-mode-${c.policy['build_mode']}'),
            decoration: const InputDecoration(
              labelText: 'Build mode for future plans',
            ),
            items: const [
              DropdownMenuItem(
                value: 'compact',
                child: Text('Compact (recommended)'),
              ),
              DropdownMenuItem(
                value: 'full',
                child: Text('Full debugger information'),
              ),
            ],
            onChanged: disabled
                ? null
                : (value) {
                    if (value != null && value != c.policy['build_mode'])
                      c.action('configure', {'build_mode': value});
                  },
          ),
          const Text(
            'Compact uses the adapter’s smaller build profile. Full debug is an explicit choice. Existing approved environments keep their settings.',
          ),
          for (final guidance in storageEntries(c.data['build_guidance']))
            if ('${guidance['text'] ?? ''}'.isNotEmpty)
              Text('${guidance['text']}'),
          Text(
            'Retention budgets: outputs ${storageBytes(c.policy['output_budget_bytes'])}; download caches ${storageBytes(c.policy['shared_budget_bytes'])}.',
          ),
        ],
        for (final entry in c.entries)
          if (entry['repairable'] == true)
            ListTile(
              title: Text('Invalid registry record: ${entry['id']}'),
              subtitle: Text('${entry['reason'] ?? ''}'),
              trailing: TextButton(
                onPressed: disabled ? null : () => c.reviewRepair(entry),
                child: const Text('Review registry repair'),
              ),
            )
          else
            CheckboxListTile(
              key: ValueKey('storage-${entry['id']}'),
              contentPadding: EdgeInsets.zero,
              value: c.selected.contains('${entry['id']}'),
              onChanged: disabled || entry['eligible'] != true
                  ? null
                  : (value) => c.select('${entry['id']}', value == true),
              title: Text(
                '${entry['kind']} · ${storageBytes(entry['bytes'])} · ${entry['state']}',
              ),
              subtitle: Text('${entry['path']}\n${entry['reason'] ?? ''}'),
              controlAffinity: ListTileControlAffinity.leading,
            ),
        if (c.repairPreview.isNotEmpty) ...[
          Text(
            'Archive invalid metadata for ${c.repairPreview['id']}? Its files will remain untouched.',
          ),
          TextButton(
            onPressed: disabled ? null : () => c.reviewRepair(null),
            child: const Text('Cancel registry repair'),
          ),
          OutlinedButton(
            onPressed: disabled ? null : () => c.action('forget-invalid'),
            child: const Text('Confirm registry repair'),
          ),
        ],
        if (c.data['sampled_at'] != null && c.entries.isEmpty)
          const Text(
            'No registered disposable storage. Existing worktree caches require a separate reviewed cleanup.',
          ),
        if (c.preview.isEmpty)
          OutlinedButton(
            onPressed: disabled || c.selected.isEmpty
                ? null
                : () => c.action('preview'),
            child: Text('Preview cleanup (${c.selected.length} selected)'),
          )
        else ...[
          Text(
            'Remove ${storageBytes(c.preview['bytes'])} from ${(c.preview['ids'] as List).length} selected registered roots? Active or changed roots are checked again before cleanup.',
          ),
          for (final entry in storageEntries(c.preview['entries']))
            Text(
              '${entry['path']} · ${storageBytes(entry['bytes'])}',
              key: ValueKey('cleanup-preview-${entry['id']}'),
            ),
          Wrap(
            spacing: 12,
            children: [
              TextButton(
                onPressed: disabled ? null : c.cancelPreview,
                child: const Text('Cancel cleanup'),
              ),
              FilledButton(
                onPressed: disabled ? null : () => c.action('reclaim'),
                child: const Text('Confirm selected cleanup'),
              ),
            ],
          ),
        ],
        ExpansionTile(
          key: const PageStorageKey('legacy-build-output'),
          tilePadding: EdgeInsets.zero,
          title: const Text('Existing worktree outputs'),
          subtitle: const Text(
            'Review one earlier execution before registering disposable generated output',
          ),
          children: [
            const Text(
              'Use the execution ID from the worktree directory, not the task ID. Herdr checks this exact worktree and refuses active executions, tracked files and unsafe links. It does not discover or adopt worktree caches automatically.',
            ),
            TextFormField(
              key: PageStorageKey('legacy-project-${c.generation}'),
              initialValue: c.legacyProject,
              enabled: !disabled,
              decoration: const InputDecoration(
                labelText: 'Project folder relative to Projects',
              ),
              onChanged: (value) => c.legacyInputs(project: value),
            ),
            TextFormField(
              key: PageStorageKey('legacy-execution-${c.generation}'),
              initialValue: c.legacyExecutionId,
              enabled: !disabled,
              decoration: const InputDecoration(
                labelText:
                    'Earlier task execution ID (32 hexadecimal characters)',
              ),
              onChanged: (value) => c.legacyInputs(executionId: value),
            ),
            TextButton(
              onPressed: disabled || !c.canPreviewLegacy
                  ? null
                  : () => c.action('legacy-preview'),
              child: const Text('Preview existing output'),
            ),
            if (c.legacyPreview.isNotEmpty) ...[
              SelectableText(
                '${c.legacyPreview['path']}',
                key: PageStorageKey(
                  'legacy-preview-path-${c.legacyPreview['id']}',
                ),
              ),
              Text(
                '${storageBytes(c.legacyPreview['bytes'])} in ${c.legacyPreview['files'] ?? 0} files',
              ),
              const Text(
                'Register this exact previewed directory as disposable? Registration authorizes automatic retention cleanup, which may remove it. Files outside this directory remain outside this cleanup.',
              ),
              Wrap(
                spacing: 12,
                children: [
                  TextButton(
                    onPressed: disabled ? null : c.cancelLegacyPreview,
                    child: const Text('Cancel registration'),
                  ),
                  FilledButton(
                    onPressed: disabled ? null : () => c.action('legacy-adopt'),
                    child: const Text('Confirm register disposable output'),
                  ),
                ],
              ),
            ],
          ],
        ),
      ],
    );
  }
}
