import 'dashboard_bloc.dart';

const publishingTokenSourceLabel = 'Publishing token source';
const manualPublishingTokenLabel = 'Enter a new token';
const publishingSourcesUnavailable =
    'Saved connections could not be loaded. Enter a token manually, or reopen this dialog to retry.';
const publishingPushTokenNotice =
    'The push token needs Contents write on the branch destination. Workflow changes may require Workflows write. '
    'If this token also handles upstream PRs, it needs Pull requests write there. Setup verifies identity and account access; '
    'actual publishing verifies token permissions. Keep branch protection enabled.\n\n'
    'Agents and gateway share a Linux user; credentials are not isolated from same-user agents. Use only trusted agents and repositories.';
const publishingPrTokenNotice =
    'An optional separate token handles the private upstream while the token above pushes to the fork. '
    'Select the upstream repository and grant Pull requests write, Contents read for base-branch checks, '
    'Checks read, and Commit statuses read for status monitoring. '
    'The PR account needs access to the fork as required by GitHub, but its token does not need the fork selected for setup. '
    'Leave blank to preserve an existing PR credential; without one, the push token is used for both repositories.';
const publishingModeNotice =
    'Direct publishes the branch and PR in the same repository. Through a fork publishes the branch to the fork '
    'and opens the PR upstream. With separate tokens, upstream reads and PR operations use the PR token; '
    'fork access and Git pushes use the push token. Setup is read-only and does not prove write permissions.';
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
