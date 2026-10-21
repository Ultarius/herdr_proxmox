/// A display-only observation of a current CLI permission prompt, never a grant.
class FolderAccessRequest {
  FolderAccessRequest._(this.paths, this.truncated);
  final List<String> paths;
  final bool truncated;

  static FolderAccessRequest? fromInspection(Map? inspection) {
    final request = inspection?['access_request'];
    if (inspection?['status'] != 'blocked' ||
        '${inspection?['error'] ?? ''}'.isNotEmpty ||
        request is! Map ||
        request['source'] != 'visible_terminal' ||
        request['paths'] is! List) {
      return null;
    }
    final paths = (request['paths'] as List)
        .whereType<String>()
        .where((p) => p.isNotEmpty)
        .take(20)
        .toList();
    return paths.isEmpty
        ? null
        : FolderAccessRequest._(paths, request['truncated'] == true);
  }

  static const explanation =
      'From the current terminal prompt. Review the console before selecting Allow once, Allow always or Reject.';
  static const settingsHint =
      'Allow dashboard outputs covers reply and artifact folders. Additional accessible path globs in Edit persona or Edit group apply on the next launch.';
}
