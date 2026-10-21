import 'package:herdr_dashboard_state/folder_access.dart';
import '../ui.dart';
import 'ops_ui.dart';

Component folderAccessPanel(Map? inspection, {String? name}) {
  final request = FolderAccessRequest.fromInspection(inspection);
  if (request == null) return Component.fragment([]);
  return el(
    'section',
    [
      callout(
        [
          calloutText(FolderAccessRequest.explanation),
          el('ul', [
            for (final path in request.paths)
              el('li', [
                el('code', [text(path)]),
              ]),
          ], classes: 'folder-access__paths'),
          if (request.truncated)
            calloutText(
              'Some patterns are omitted. Review the full console prompt.',
            ),
          calloutText(FolderAccessRequest.settingsHint),
        ],
        title: name == null
            ? 'Requested folder access'
            : '$name · Requested folder access',
        tone: 'warning',
        iconName: 'folder',
      ),
    ],
    classes: 'folder-access',
    attributes: {'aria-label': 'Requested folder access'},
  );
}
