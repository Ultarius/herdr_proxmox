import 'dart:async';
import 'dashboard_bloc.dart';

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
class RepositoryConnectionsController {
  RepositoryConnectionsController(
    this.connection, {
    required this.onUpdate,
    required this.onChanged,
  }) {
    generation = connection.generation;
    role = '${connection.operator.value['role']}';
    connected = connection.state.connected;
    subscription = connection.stream.listen((_) => sessionChanged());
    connection.operator.addListener(sessionChanged);
    refresh();
  }
  final DashboardBloc connection;
  final void Function() onUpdate;
  final void Function(List<Map<String, dynamic>>) onChanged;
  late final StreamSubscription subscription;
  late int generation;
  late String role;
  late bool connected;
  int revision = 0;
  bool disposed = false, loaded = false, refreshing = false, busy = false;
  List<Map<String, dynamic>> connections = [], bindings = [];
  String? message;
  String? pendingRemoval;
  void prepareRemoval(String? id) {
    pendingRemoval = id;
    onUpdate();
  }

  bool get canManage =>
      // signIn/restoreSession hydrate the operator before connecting. The
      // default pre-auth role alone must never start an administrative fetch.
      connection.state.connected &&
      connection.operator.value['role'] == 'admin';
  bool current(int epoch, int version) =>
      !disposed &&
      generation == epoch &&
      connection.generation == epoch &&
      revision == version &&
      canManage;
  void sessionChanged() {
    if (disposed) return;
    final nextRole = '${connection.operator.value['role']}';
    if (generation != connection.generation ||
        role != nextRole ||
        connected != connection.state.connected) {
      generation = connection.generation;
      role = nextRole;
      connected = connection.state.connected;
      revision++;
      loaded = refreshing = busy = false;
      connections = [];
      bindings = [];
      message = null;
      pendingRemoval = null;
      onChanged([]);
      onUpdate();
    }
    if (canManage && !loaded && !refreshing) refresh();
  }

  Future<void> refresh() async {
    if (!canManage || refreshing || disposed) return;
    refreshing = true;
    onUpdate();
    final epoch = connection.generation, version = revision;
    try {
      final data = await connection.request('projects/connections');
      if (!current(epoch, version)) return;
      if (!busy) message = null;
      connections = List<Map<String, dynamic>>.from(data['connections'] ?? []);
      bindings = List<Map<String, dynamic>>.from(data['bindings'] ?? []);
      onChanged(connections);
    } catch (error) {
      if (current(epoch, version)) message = '$error';
    } finally {
      if (current(epoch, version)) {
        loaded = true;
        refreshing = false;
        onUpdate();
      }
    }
  }

  Future<void> action(String action, Map<String, dynamic> body) async {
    if (!canManage || busy || refreshing || disposed) return;
    final epoch = connection.generation, version = revision;
    busy = true;
    message = null;
    onUpdate();
    try {
      final result = await connection.request('projects/connections', {
        'action': action,
        ...body,
      });
      if (!current(epoch, version)) return;
      if (action == 'remove') pendingRemoval = null;
      message = switch (action) {
        'create' =>
          'Connection created. Configure its key if needed, then test access.',
        'test' =>
          result['access_state'] == 'verified'
              ? 'Repository access verified.'
              : result['error']?.toString(),
        'bind' => 'Connection assigned to project.',
        'unbind' =>
          'Project connection unassigned. Operator Git settings were preserved.',
        'remove' =>
          'Connection removed locally. Revoke its deploy key on GitHub separately.',
        _ => 'GitHub host fingerprint verified.',
      };
      await refresh();
    } catch (error) {
      if (current(epoch, version)) message = '$error';
    } finally {
      if (current(epoch, version)) {
        busy = false;
        onUpdate();
      }
    }
  }

  void dispose() {
    disposed = true;
    subscription.cancel();
    connection.operator.removeListener(sessionChanged);
  }
}
