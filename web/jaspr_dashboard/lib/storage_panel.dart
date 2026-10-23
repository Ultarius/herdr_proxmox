import 'package:jaspr/dom.dart' as dom;
import 'package:herdr_dashboard_state/storage.dart';
import 'ui.dart';
import 'services.dart';

class StoragePanel extends StatefulComponent {
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
      dashboardConnection,
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
  Component build(BuildContext context) {
    final c = controller;
    if (!c.canManage) return const Component.empty();
    final disabled = c.loading || c.busy || c.scanning;
    return el(
      'details',
      [
        el('summary', [text('Managed build storage')]),
        el('p', [
          text(
            c.scanning
                ? 'Scanning registered storage…'
                : c.data['sampled_at'] == null
                ? 'Scan to measure registered build outputs and download caches.'
                : '${storageBytes(c.inventory['total_bytes'])} registered · ${storageBytes(c.inventory['reclaimable_bytes'])} reclaimable',
          ),
        ]),
        el('p', [
          text(
            'Generated build outputs belong to each execution. Download caches can be reused; installed dependency trees stay private. Source edits and retained evidence are preserved.',
          ),
        ]),
        if (c.loading) el('p', [text('Loading saved storage inventory…')]),
        if (c.error != null) notice(c.error, error: true),
        if (c.message != null) notice(c.message),
        if (c.data['error'] != null) notice(c.data['error'], error: true),
        for (final pending in storageEntries(c.data['finalization_errors']))
          notice(
            'Storage finalization pending for ${pending['execution_id']}: ${pending['error']}',
            error: true,
          ),
        el('div', [
          button(
            'Scan storage',
            () => c.action('scan'),
            disabled: disabled,
            iconName: 'search',
          ),
          button('Refresh saved inventory', c.refresh, disabled: disabled),
        ], classes: 'actions'),
        if (c.data['sampled_at'] != null)
          el('p', [text('Inventory sampled: ${c.data['sampled_at']}')]),
        if (c.policy.isNotEmpty) ...[
          select(
            'Build mode for future plans',
            c.policy['build_mode'] == 'full' ? 'full' : 'compact',
            {
              'compact': 'Compact (recommended)',
              'full': 'Full debugger information',
            },
            (value) {
              if (value != c.policy['build_mode'])
                c.action('configure', {'build_mode': value});
            },
            disabled: disabled,
          ),
          el('p', [
            text(
              'Compact uses the adapter’s smaller build profile. Full debug is an explicit choice. Existing approved environments keep their settings.',
            ),
          ]),
          for (final guidance in storageEntries(c.data['build_guidance']))
            if ('${guidance['text'] ?? ''}'.isNotEmpty)
              el('p', [text(guidance['text'])]),
          el('p', [
            text(
              'Retention budgets: outputs ${storageBytes(c.policy['output_budget_bytes'])}; download caches ${storageBytes(c.policy['shared_budget_bytes'])}.',
            ),
          ]),
        ],
        for (final entry in c.entries)
          if (entry['repairable'] == true)
            el('div', [
              el('p', [
                text(
                  'Invalid registry record: ${entry['id']} · ${entry['reason'] ?? ''}',
                ),
              ]),
              button(
                'Review registry repair',
                () => c.reviewRepair(entry),
                disabled: disabled,
              ),
            ])
          else
            el('div', [
              el('label', [
                dom.input<bool>(
                  key: ValueKey(
                    'storage-${entry['id']}-${c.selected.contains('${entry['id']}')}',
                  ),
                  type: dom.InputType.checkbox,
                  checked: c.selected.contains('${entry['id']}'),
                  disabled: disabled || entry['eligible'] != true,
                  onChange: (value) => c.select('${entry['id']}', value),
                ),
                text(
                  ' ${entry['kind']} · ${storageBytes(entry['bytes'])} · ${entry['state']}',
                ),
              ]),
              el('p', [text(entry['path'])], classes: 'mono'),
              if ('${entry['reason'] ?? ''}'.isNotEmpty)
                el('p', [text(entry['reason'])]),
            ], key: ValueKey('storage-root-${entry['id']}')),
        if (c.repairPreview.isNotEmpty) ...[
          el('p', [
            text(
              'Archive invalid metadata for ${c.repairPreview['id']}? Its files will remain untouched.',
            ),
          ]),
          button(
            'Cancel registry repair',
            () => c.reviewRepair(null),
            disabled: disabled,
          ),
          button(
            'Confirm registry repair',
            () => c.action('forget-invalid'),
            disabled: disabled,
          ),
        ],
        if (c.data['sampled_at'] != null && c.entries.isEmpty)
          el('p', [
            text(
              'No registered disposable storage. Existing worktree caches require a separate reviewed cleanup.',
            ),
          ]),
        if (c.preview.isEmpty)
          button(
            'Preview cleanup (${c.selected.length} selected)',
            () => c.action('preview'),
            disabled: disabled || c.selected.isEmpty,
          )
        else ...[
          el('p', [
            text(
              'Remove ${storageBytes(c.preview['bytes'])} from ${(c.preview['ids'] as List).length} selected registered roots? Active or changed roots are checked again before cleanup.',
            ),
          ]),
          for (final entry in storageEntries(c.preview['entries']))
            el('p', [
              text('${entry['path']} · ${storageBytes(entry['bytes'])}'),
            ], classes: 'storage-cleanup-preview'),
          el('div', [
            button('Cancel cleanup', c.cancelPreview, disabled: disabled),
            button(
              'Confirm selected cleanup',
              () => c.action('reclaim'),
              disabled: disabled,
              variant: 'danger',
            ),
          ], classes: 'actions'),
        ],
        el('details', [
          el('summary', [text('Existing worktree outputs')]),
          el('p', [
            text(
              'Use the execution ID from the worktree directory, not the task ID. Herdr checks this exact worktree and refuses active executions, tracked files and unsafe links. It does not discover or adopt worktree caches automatically.',
            ),
          ]),
          el('fieldset', [
            field(
              'Project folder relative to Projects',
              c.legacyProject,
              (value) => c.legacyInputs(project: value),
            ),
            field(
              'Earlier task execution ID (32 hexadecimal characters)',
              c.legacyExecutionId,
              (value) => c.legacyInputs(executionId: value),
            ),
          ], attributes: disabled ? {'disabled': ''} : null),
          button(
            'Preview existing output',
            () => c.action('legacy-preview'),
            disabled: disabled || !c.canPreviewLegacy,
          ),
          if (c.legacyPreview.isNotEmpty) ...[
            el('p', [text(c.legacyPreview['path'])], classes: 'mono'),
            el('p', [
              text(
                '${storageBytes(c.legacyPreview['bytes'])} in ${c.legacyPreview['files'] ?? 0} files',
              ),
            ]),
            el('p', [
              text(
                'Register this exact previewed directory as disposable? Registration authorizes automatic retention cleanup, which may remove it. Files outside this directory remain outside this cleanup.',
              ),
            ]),
            el('div', [
              button(
                'Cancel registration',
                c.cancelLegacyPreview,
                disabled: disabled,
              ),
              button(
                'Confirm register disposable output',
                () => c.action('legacy-adopt'),
                disabled: disabled,
                variant: 'danger',
              ),
            ], classes: 'actions'),
          ],
        ], key: const ValueKey('legacy-build-output')),
      ],
      key: const ValueKey('managed-storage-panel'),
      classes: 'managed-storage',
    );
  }
}
