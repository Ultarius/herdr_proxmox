import 'package:herdr_dashboard_state/folder_access.dart';
import 'package:juice/juice.dart';

class FolderAccessPanel extends StatelessWidget {
  const FolderAccessPanel({super.key, required this.inspection, this.name});
  final Map? inspection;
  final String? name;

  @override
  Widget build(BuildContext context) {
    final request = FolderAccessRequest.fromInspection(inspection);
    if (request == null) return const SizedBox.shrink();
    final foreground = Theme.of(context).colorScheme.onTertiaryContainer;
    return Card(
      color: Theme.of(context).colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              name == null
                  ? 'Requested folder access'
                  : '$name · Requested folder access',
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(color: foreground),
            ),
            const SizedBox(height: 8),
            Text(
              FolderAccessRequest.explanation,
              style: TextStyle(color: foreground),
            ),
            for (final path in request.paths)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: SelectableText(
                  path,
                  style: TextStyle(fontFamily: 'monospace', color: foreground),
                ),
              ),
            if (request.truncated)
              Text(
                'Some patterns are omitted. Review the full console prompt.',
                style: TextStyle(color: foreground),
              ),
            const SizedBox(height: 8),
            Text(
              FolderAccessRequest.settingsHint,
              style: TextStyle(color: foreground),
            ),
          ],
        ),
      ),
    );
  }
}
