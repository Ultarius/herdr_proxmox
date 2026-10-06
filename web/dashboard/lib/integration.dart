import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';

/// One checkout where a launched agent's branch drifted from its base branch.
///
/// A notice is advisory. Nothing is merged automatically and a busy agent is
/// never interrupted; the operator asks for an integration explicitly.
class IntegrationNotice {
  const IntegrationNotice({
    required this.profileId,
    required this.name,
    required this.path,
    required this.branch,
    this.base,
    this.behind,
    this.ahead,
    this.conflicts = 0,
    this.dirty = false,
    this.merging = false,
    this.error,
    this.lastFetch,
  });
  final String profileId;
  final String name;
  final String path;
  final String branch;
  final String? base;
  final int? behind;
  final int? ahead;
  final int conflicts;
  final bool dirty;
  final bool merging;
  final String? error;
  final String? lastFetch;

  /// Stable identity so a repeat notice is not announced twice.
  String get key =>
      '$profileId:$path:${behind ?? 0}:$conflicts:${merging ? 1 : 0}:${error ?? ''}';

  List<String> get reasons => [
    if (error != null) error!,
    if (merging) 'Merge or rebase in progress',
    if (conflicts > 0)
      '$conflicts unresolved conflict${conflicts == 1 ? '' : 's'}',
    if ((behind ?? 0) > 0)
      '$behind commits behind ${base ?? 'the base branch'}',
  ];

  String get summary => reasons.isEmpty ? 'Needs review' : reasons.join(' · ');

  String get instructions {
    final action = error != null
        ? 'Checkout inspection failed: $error. Verify the assigned checkout and report the blocker; do not start a merge without verified repository state.'
        : merging || conflicts > 0
        ? 'An integration operation is already in progress. Inspect its state, resolve the existing conflicts, and finish that operation when appropriate. Do not start another merge or rebase.'
        : 'Merge ${base ?? 'the verified base branch'} into your existing worktree branch when ready.';
    return 'Your checkout $path on branch $branch needs integration review. '
        '$action At a safe stopping point, inspect status and preserve your current work. '
        'Run relevant tests after integration. Do not reset, discard changes, switch another checkout, force-push, or deploy. '
        'Preserve unrelated work. If your assigned permissions or read-only role do not allow this, report the blocker instead.';
  }

  factory IntegrationNotice.fromJson(Map<String, dynamic> json) =>
      IntegrationNotice(
        profileId: '${json['profile_id'] ?? ''}',
        name: '${json['name'] ?? ''}',
        path: '${json['path'] ?? ''}',
        branch: '${json['branch'] ?? ''}',
        base: json['base'] as String?,
        behind: json['behind'] as int?,
        ahead: json['ahead'] as int?,
        conflicts: (json['conflicts'] as int?) ?? 0,
        dirty: json['dirty'] == true,
        merging: json['merging'] == true,
        error: json['error'] as String?,
        lastFetch: json['last_fetch'] as String?,
      );
}

/// Ask an agent to integrate the base branch into its own worktree.
///
/// This is the only step that asks an agent to act, and only when the operator
/// chooses it: the request is a normal organization chat with a longer wait,
/// and the server refuses it while the agent already has work queued.
Future<void> requestIntegration(
  DashboardBloc connection,
  IntegrationNotice notice,
) async {
  final profile =
      (connection.organizationDirectory.value['profiles'] as List? ?? [])
          .where((p) => p['id'] == notice.profileId)
          .firstOrNull;
  if (profile == null)
    throw StateError('That agent profile is no longer available.');
  await OrganizationCommand('chat', {
    'organization_id': profile['organization_id'],
    'profile_id': profile['id'],
    'prompt': notice.instructions,
    // Merging and running tests can outlast the default chat wait.
    'wait_seconds': 1800,
  }).execute(connection);
}

/// An agent can be asked to integrate only while it is not already working.
bool agentCanIntegrate(DashboardBloc connection, String profileId) {
  final agents = connection.state.agents
      .where((agent) => agent['profile_id'] == profileId)
      .toList();
  if (agents.isEmpty) return false;
  return agents.every(
    (agent) => ['idle', 'done'].contains(agent['agent_status']),
  );
}
