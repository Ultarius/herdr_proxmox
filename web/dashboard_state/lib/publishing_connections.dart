import 'dashboard_bloc.dart';

const publishingTokenSourceLabel = 'Publishing token source';
const manualPublishingTokenLabel = 'Enter a new token';
const publishingSourcesUnavailable =
    'Saved connections could not be loaded. Enter a token manually, or reopen this dialog to retry.';
const publishingPushTokenNotice =
    'The push token needs Contents read/write and Metadata read on the branch destination. '
    'Add Workflows read/write only for workflow file changes. Protected publication accepts distinct fine-grained tokens only; '
    'the prefix proves format, not access. Herdr never pushes a protected upstream. '
    'Agents and gateway share a Linux user; file permissions do not isolate same-user agents.';
const publishingPrTokenNotice =
    'Protected private-fork publication requires a separate upstream fine-grained token: Pull requests read/write, '
    'Contents read, Metadata read, Checks read and Commit statuses read. Do not grant upstream Contents write. '
    'A fine-grained token cannot select another owner’s private fork. You open the draft PR on GitHub; Herdr verifies and adopts it. '
    'Leave blank only to preserve an existing upstream credential, which setup revalidates.';
const publishingModeNotice =
    'The operator’s fork-only policy is enforced by the gateway: branches go only to its configured fork. '
    'For private personal repositories under different owners, open the draft PR using the compare link, then check for adoption. '
    'Setup reads do not prove PR creation or check-monitoring capability. These settings use one credential pair for every allowed repository. Unprotected repositories retain their configured publication mode.';

String? publishingPolicyNotice(
  Map github,
) => switch (github['protected_publication_state']) {
  'absent' =>
    'Fork-only protection is not configured: the operator policy file is absent. Publishing uses ordinary repository settings.',
  'empty' =>
    'Fork-only protection is not enabled: the operator policy contains no protected repositories. Publishing uses ordinary repository settings.',
  _ => null,
};

Map? protectedPublishingPolicy(Map github, String upstream) {
  final policies = github['protected_publication'];
  if (policies is! Map) return null;
  final value = policies[upstream.toLowerCase()];
  return value is Map ? value : null;
}

bool manualPrRequired(Map github, String upstream) {
  final entries = github['publication'];
  final entry = entries is Map ? entries[upstream] : null;
  return protectedPublishingPolicy(github, upstream)?['arrangement'] ==
          'manual_adoption' ||
      entry is Map && entry['arrangement'] == 'manual_adoption';
}

Uri? githubCompareUri(Object? value) {
  if (value is! String) return null;
  final uri = Uri.tryParse(value);
  if (uri == null ||
      uri.scheme != 'https' ||
      uri.host != 'github.com' ||
      uri.port != 443 ||
      uri.userInfo.isNotEmpty ||
      uri.hasFragment ||
      !RegExp(
        r'^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/compare/[^\s]+$',
      ).hasMatch(uri.path))
    return null;
  return uri;
}

const savedPublishingTokenNotice =
    'Every branch destination must match this connection’s repository. Direct mode allows '
    'only that one repository; fork mode lists the upstream and must target this connection’s fork. '
    'Enter a token manually to publish to multiple destinations. '
    'The gateway copies this token for publishing. Rotating or removing its repository '
    'connection will not update publishing. Cloning access does not verify write permissions.';

/// Shared source options and suggestion rules for both dashboard forms.
class PublishingTokenSelection {
  PublishingTokenSelection(this.sources);
  final List<Map<String, String>> sources;
  String selectedId = '';
  bool get manual => selectedId.isEmpty;
  Map<String, String> get options => {
    '': manualPublishingTokenLabel,
    for (final source in sources)
      source['id']!: publishingConnectionLabel(source),
  };
  String? get repository {
    for (final source in sources) {
      if (source['id'] == selectedId) return source['repository'];
    }
    return null;
  }

  String select(String? id, String currentRepositories) {
    final previous = repository;
    selectedId = options.containsKey(id) ? id! : '';
    final next = repository;
    return next != null &&
            (currentRepositories.trim().isEmpty ||
                currentRepositories.trim() == previous)
        ? next
        : currentRepositories;
  }
}

String publishingConnectionLabel(Map<String, String> source) =>
    source['label'] == source['repository']
    ? source['repository']!
    : '${source['label']} · ${source['repository']}';

/// Metadata only. The saved PAT is resolved by the gateway when setup is saved.
Future<List<Map<String, String>>> publishingTokenConnections(
  DashboardBloc connection,
) async {
  bool allowed() =>
      connection.state.connected &&
      connection.operator.value['role'] == 'admin';
  if (!allowed())
    throw StateError(
      'Only an administrator can select publishing credentials.',
    );
  final generation = connection.generation;
  final data = await connection.request('projects/connections');
  if (!allowed() || generation != connection.generation)
    throw StateError('Connection changed. Reopen GitHub publishing.');
  if (data is! Map || data['connections'] is! List)
    throw StateError('Saved repository connections are unavailable.');
  return [
    for (final item in data['connections'] as List)
      if (item is Map &&
          item['kind'] == 'token' &&
          item['id'] is String &&
          RegExp(r'^[a-f0-9]{32}$').hasMatch(item['id'] as String) &&
          item['repository'] is String &&
          RegExp(
            r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$',
          ).hasMatch(item['repository'] as String) &&
          !(item['repository'] as String)
              .split('/')
              .any((part) => part == '.' || part == '..'))
        {
          'id': item['id'] as String,
          'repository': item['repository'] as String,
          'label': item['label'] is String
              ? item['label'] as String
              : item['repository'] as String,
        },
  ];
}
