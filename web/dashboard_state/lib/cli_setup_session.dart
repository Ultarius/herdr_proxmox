import 'dart:async';
import 'dashboard_bloc.dart';

/// Owns a remote setup terminal until it is closed, including a start whose
/// response has not arrived yet. Both renderers share the same logout boundary.
class CliSetupSession {
  CliSetupSession(this.connection) {
    _cleanupRequest = connection.addDisconnectCleanup(close);
  }

  final DashboardBloc connection;
  late DisconnectCleanupRequest _cleanupRequest;
  Future<dynamic>? _starting;
  Future<void>? _closing;
  final Map<String, Future<void>> _closed = {};
  String? _session;
  int _revision = 0;
  bool _disposed = false;

  bool get closing => _closing != null;

  Future<Map<String, dynamic>?> start(String cli) async {
    if (_disposed ||
        closing ||
        connection.disconnecting ||
        !connection.state.connected ||
        _starting != null ||
        _session != null) {
      return null;
    }
    // The owner may have been mounted before restoration established a session.
    // Bind cleanup to the generation that actually creates the remote terminal.
    connection.removeDisconnectCleanup(close);
    _cleanupRequest = connection.addDisconnectCleanup(close);
    final revision = _revision;
    final pending = connection.request('cli-setup/start', {'cli': cli});
    _starting = pending;
    try {
      final data = Map<String, dynamic>.from(await pending);
      final id = data['id'] as String;
      if (_disposed || revision != _revision || connection.disconnecting) {
        await _close(id);
        return null;
      }
      _session = id;
      return data;
    } finally {
      if (identical(_starting, pending)) _starting = null;
    }
  }

  Future<void> _close(String id) => _closed.putIfAbsent(id, () async {
    try {
      // Dispatch while the credential is still available. A slow/unreachable
      // gateway must not keep sign-out waiting after this bounded attempt.
      await _cleanupRequest('cli-setup/close', {
        'id': id,
      }).timeout(const Duration(seconds: 2));
    } catch (_) {
      // The gateway also reaps abandoned terminals; logout remains available.
    }
  });

  Future<void> close() {
    if (_closing != null) return _closing!;
    _revision++;
    final pending = _starting;
    final id = _session;
    _session = null;
    return _closing = () async {
      if (id != null) await _close(id);
      if (pending != null) {
        try {
          final data = await pending;
          await _close(data['id'] as String);
        } catch (_) {
          // A failed/timed-out start has no known terminal identifier.
        }
      }
    }().whenComplete(() => _closing = null);
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    // Keep the hook registered until an in-flight start has been reconciled.
    // Logout during route disposal must still wait before removing credentials.
    unawaited(
      close().whenComplete(() => connection.removeDisconnectCleanup(close)),
    );
  }
}
