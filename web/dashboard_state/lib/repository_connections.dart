import 'feature_bloc.dart';
import 'dashboard_bloc.dart';

const repositoryTokenLabel = 'Fine-grained GitHub token (HTTPS)';
const repositoryCloneTokenGuidance =
    'For a private repository, add a GitHub token or SSH connection below, then select it to clone.';
const repositoryTokenGuidance =
    'Select the repository owner and Only select repositories on GitHub. '
    'For cloning and pulling, grant Contents: Read-only; Metadata: Read-only is included. '
    'Pushing requires Contents: Read and write. Organization approval may be required. '
    'Saving this connection does not change GitHub publishing. To reuse its token, '
    'open Tasks → GitHub publishing and choose it as the publishing token source. '
    'Keep tokens out of repository URLs.';
const repositoryTokenUrl =
    'https://github.com/settings/personal-access-tokens/new?contents=read';

String repositoryCreateLabel(String kind) => switch (kind) {
  'ssh' => 'Create SSH connection',
  'token' => 'Save token connection',
  _ => 'Create GitHub connection',
};

String repositoryConnectionUrl(Map item) => item['kind'] == 'ssh'
    ? "git@github.com:${item['repository']}.git"
    : "https://github.com/${item['repository']}.git";

String repositoryPublicKeyFilename(Map item) {
  final id = '${item['id']}';
  return "herdr-${'${item['repository']}'.replaceAll('/', '-')}-${id.length > 8 ? id.substring(0, 8) : id}.pub";
}

bool knownRepositoryConnection(List<Map<String, dynamic>> values, String id) =>
    id.isEmpty || values.any((item) => item['id'] == id);

String repositoryBindingFolder(Map binding) =>
    '${binding['path']}'.replaceAll('\\', '/').split('/').last;

/// Shared lifecycle for both dashboard renderers. Never retries a failed GET
/// on every bloc emission; the operator can explicitly refresh after an error.
class RepositoryConnectionsController extends FeatureBloc {
  RepositoryConnectionsController(
    DashboardBloc connection, {
    required this.onUpdate,
    required this.onChanged,
    this.onReset,
  }) : super(
         connection,
         initial: const {
           'connections': <Map<String, dynamic>>[],
           'bindings': <Map<String, dynamic>>[],
           'discovered': <Map<String, dynamic>>[],
           'loaded': false,
         },
       ) {
    _generation = connection.generation;
    _role = '${connection.operator.value['role']}';
    _connected = connection.state.connected;
    bindStream(stream, (_) => _updated(), key: 'projection');
    refresh();
  }
  final void Function() onUpdate;
  final void Function(List<Map<String, dynamic>>) onChanged;
  final void Function()? onReset;
  late int _generation;
  late String _role;
  late bool _connected;
  int discoveryRevision = 0;
  bool get disposed => isClosing;
  bool get loaded => state.values['loaded'] == true;
  bool get refreshing => state.reading.contains('connections');
  bool get busy => state.busy || state.reading.contains('discovery');
  List<Map<String, dynamic>> get connections =>
      List<Map<String, dynamic>>.from(state.values['connections'] ?? []);
  List<Map<String, dynamic>> get bindings =>
      List<Map<String, dynamic>>.from(state.values['bindings'] ?? []);
  List<Map<String, dynamic>> get discoveredRepositories =>
      List<Map<String, dynamic>>.from(state.values['discovered'] ?? []);
  int? get nextRepositoryPage => state.values['nextPage'] as int?;
  String? get message => state.values['notice'] as String?;
  set message(String? value) {
    patch({'notice': value});
  }

  String? get pendingRemoval => state.values['pendingRemoval'] as String?;
  bool get canManage => available && admin;

  void _updated() {
    if (isClosing) return;
    final role = '${connection.operator.value['role']}';
    if (_generation != connection.generation ||
        _role != role ||
        _connected != connection.state.connected) {
      _generation = connection.generation;
      _role = role;
      _connected = connection.state.connected;
      discoveryRevision++;
      onReset?.call();
    }
    onChanged(connections);
    onUpdate();
    if (canManage && !loaded && !refreshing && !busy) refresh();
  }

  void sessionChanged() {
    if (_generation == connection.generation &&
        _role == '${connection.operator.value['role']}' &&
        _connected == connection.state.connected)
      return;
    _updated();
  }

  bool resetRepositoryDiscovery({bool clearMessage = true}) {
    final changed =
        discoveredRepositories.isNotEmpty ||
        nextRepositoryPage != null ||
        (clearMessage && message != null);
    discoveryRevision++;
    patch({
      'discovered': <Map<String, dynamic>>[],
      'nextPage': null,
      if (clearMessage) 'notice': null,
    });
    return changed;
  }

  void prepareRemoval(String? id) {
    patch({'pendingRemoval': id});
  }

  Future<void> discoverRepositories(String token, {bool more = false}) async {
    if (!canManage || busy || refreshing) return;
    final page = more ? nextRepositoryPage : 1;
    if (page == null) return;
    if (!more) resetRepositoryDiscovery();
    final discovery = discoveryRevision, epoch = connection.generation;
    await read<void>('discovery', () async {
      try {
        final data = await connection.request('projects/connections', {
          'action': 'discover',
          'token': token.trim(),
          'page': page,
        });
        if (!current(epoch) || !canManage || discovery != discoveryRevision)
          return const FeatureResult();
        final previous = discoveredRepositories;
        final names = previous.map((item) => item['repository']).toSet();
        final values = [
          ...previous,
          ...List<Map<String, dynamic>>.from(
            data['repositories'] ?? [],
          ).where((item) => names.add(item['repository'])),
        ];
        final next = data['next_page'] as int?;
        final notice = data['limit_reached'] == true
            ? 'Discovery reached its listing limit. Enter owner/repository manually for additional repositories.'
            : values.isEmpty
            ? 'No repositories found. Check the token’s repository selection and organization approval, or enter owner/repository manually.'
            : next != null
            ? 'Showing ${values.length} repositories so far. More repositories are available; load more or select one now. Test repository access before cloning.'
            : 'Select a discovered repository, then save the connection. Test repository access before cloning.';
        return FeatureResult(
          patch: {'discovered': values, 'nextPage': next, 'notice': notice},
        );
      } catch (error) {
        return FeatureResult(
          patch: discovery == discoveryRevision
              ? {'notice': '$error'}
              : const {},
        );
      }
    }, start: const {'notice': null});
  }

  Future<void> refresh() async {
    if (!canManage || refreshing) return;
    await read<void>('connections', () async {
      try {
        final data = await connection.request('projects/connections');
        return FeatureResult(
          patch: {
            'connections': List<Map<String, dynamic>>.from(
              data['connections'] ?? [],
            ),
            'bindings': List<Map<String, dynamic>>.from(data['bindings'] ?? []),
            'notice': null,
            'loaded': true,
          },
        );
      } catch (error) {
        return FeatureResult(patch: {'notice': '$error', 'loaded': true});
      }
    }, replace: false);
  }

  Future<bool> action(String action, Map<String, dynamic> body) async {
    if (!canManage || busy || refreshing) return false;
    final result = await mutate<bool>(
      'connection',
      () async {
        try {
          final result = await connection.request('projects/connections', {
            'action': action,
            ...body,
          });
          final notice = switch (action) {
            'create' =>
              body['kind'] == 'token'
                  ? 'Token connection saved. Test repository access, then select it below to clone.'
                  : 'Connection created. Configure its key if needed, then test access.',
            'test' =>
              result['access_state'] == 'verified'
                  ? 'Repository access verified.'
                  : result['error']?.toString(),
            'rotate' =>
              'Token replaced. Project assignments are unchanged. Test repository access again.',
            'bind' => 'Connection assigned to project.',
            'unbind' =>
              'Project connection unassigned. Operator Git settings were preserved.',
            'remove' =>
              'Connection removed locally. Revoke its deploy key or token on GitHub separately.',
            _ => 'GitHub host fingerprint verified.',
          };
          final data = await connection.request('projects/connections');
          return FeatureResult(
            value: true,
            patch: {
              'connections': List<Map<String, dynamic>>.from(
                data['connections'] ?? [],
              ),
              'bindings': List<Map<String, dynamic>>.from(
                data['bindings'] ?? [],
              ),
              'notice': notice,
              'loaded': true,
              if (action == 'remove') 'pendingRemoval': null,
            },
          );
        } catch (error) {
          return FeatureResult(value: false, patch: {'notice': '$error'});
        }
      },
      admin: true,
      start: const {'notice': null},
    );
    return result == true;
  }
}
