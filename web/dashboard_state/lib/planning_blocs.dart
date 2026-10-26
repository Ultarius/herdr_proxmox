import 'dart:convert';
import 'feature_bloc.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'request_id.dart';

class ProductDiscoveryBloc extends FeatureBloc {
  ProductDiscoveryBloc(super.connection)
    : super(initial: {'data': <String, dynamic>{}}) {
    poll(
      'discovery',
      const Duration(seconds: 15),
      () => _expanded ? refresh() : Future.value(),
    );
  }
  bool _expanded = false;
  Map<String, dynamic> get data =>
      Map<String, dynamic>.from(state.values['data'] as Map? ?? {});
  void setExpanded(bool value) => _expanded = value;
  Future<void> refresh() async {
    if (!admin) return;
    await read('snapshot', () async {
      final data = Map<String, dynamic>.from(
        await connection.request('discovery'),
      );
      return FeatureResult(patch: {'data': data});
    }, replace: false);
  }

  Future<void> submit(String? organization, Map<String, dynamic> body) async {
    final done = await mutate<bool>('direction', () async {
      await connection.request('tasks/discovery', {
        'organization_id': organization,
        ...body,
      });
      return const FeatureResult(value: true);
    }, admin: true);
    if (done == true) await refresh();
  }
}

class OrganizationAutomationBloc extends FeatureBloc {
  OrganizationAutomationBloc(super.connection, this.organizationId) {
    connection.notificationRevision.addListener(_notified);
  }
  final String organizationId;
  Map<String, dynamic>? get policy =>
      state.values['policy'] as Map<String, dynamic>?;
  Map<String, dynamic>? get pending =>
      state.values['pending'] as Map<String, dynamic>?;
  String? get pageError => state.error;
  void _notified() => refresh();
  Future<void> refresh() async {
    await read('policy-read', () async {
      final data = await connection.request(
        'organizations/automation?organization_id=${Uri.encodeQueryComponent(organizationId)}',
      );
      return FeatureResult(
        patch: {'policy': Map<String, dynamic>.from(data['policy'])},
      );
    }, replace: false);
  }

  void prepare(Map<String, dynamic> chosen) {
    final intent = {'organization_id': organizationId, ...chosen};
    final previous = pending == null
        ? null
        : (Map<String, dynamic>.from(pending!)..remove('request_id'));
    if (previous == null || jsonEncode(previous) != jsonEncode(intent))
      patch({
        'pending': {...intent, 'request_id': newRequestId()},
      });
  }

  Future<void> save({bool fromDialog = false}) async {
    if (state.busy || pending == null || !available || !admin) {
      if (fromDialog)
        throw StateError(
          'The policy cannot be saved right now. Check the connection and try again.',
        );
      return;
    }
    final request = pending!;
    try {
      await mutate('policy-save', () async {
        try {
          final result = await connection.request('tasks/automation', request);
          return FeatureResult(
            patch: {
              'policy': Map<String, dynamic>.from(result['policy']),
              'pending': null,
            },
          );
        } catch (error) {
          if (error is DashboardRequestException &&
              error.statusCode >= 400 &&
              error.statusCode < 500)
            await patch({'pending': null});
          rethrow;
        }
      }, rethrowErrors: true);
    } catch (_) {
      if (fromDialog) {
        await clearError('policy-save');
        rethrow;
      }
    }
  }

  @override
  Future<void> close() {
    if (!isClosing) connection.notificationRevision.removeListener(_notified);
    return super.close();
  }
}

class CollaborationBloc extends FeatureBloc {
  CollaborationBloc(super.connection, this.organizationId)
    : super(
        initial: {
          'groups': <Map<String, dynamic>>[],
          'jobs': <Map<String, dynamic>>[],
          'output': '',
          'status': '',
          'draft': '',
        },
      ) {
    poll('activity', const Duration(seconds: 1), () {
      if (profile != null || ++_pollTick % 10 == 0) return refresh();
      return Future.value();
    });
  }
  final String organizationId;
  int _pollTick = 0;
  String? get profile => state.values['profile'] as String?;
  OrganizationCommand? get pending =>
      state.values['pending'] as OrganizationCommand?;
  List<Map<String, dynamic>> get groups =>
      List<Map<String, dynamic>>.from(state.values['groups'] ?? []);
  List<Map<String, dynamic>> get jobs =>
      List<Map<String, dynamic>>.from(state.values['jobs'] ?? []);
  String get output => '${state.values['output'] ?? ''}';
  String get status => '${state.values['status'] ?? ''}';
  String get draft => '${state.values['draft'] ?? ''}';
  String? get draftJob => state.values['draftJob'] as String?;
  void select(String? profile) {
    patch({
      'profile': profile,
      'output': '',
      'status': '',
      'draft': '',
      'draftJob': null,
    });
  }

  void abandon() {
    patch({'pending': null});
    clearError('command');
  }

  Map<String, dynamic> body(Map<String, dynamic> values) => OrganizationCommand(
    'inspect',
    {...values, 'organization_id': organizationId},
  ).body!;
  Future<void> refresh() async {
    final selected = profile;
    await read(
      'activity',
      () async {
        final data = await connection.request('organizations/activity', {
          'organization_id': organizationId,
        });
        final patch = <String, dynamic>{
          'groups': List<Map<String, dynamic>>.from(
            data['groups'] ?? [],
          ).where((g) => g['organization_id'] == organizationId).toList(),
          'jobs': List<Map<String, dynamic>>.from(
            data['jobs'] ?? [],
          ).where((j) => j['organization_id'] == organizationId).toList(),
        };
        if (selected != null) {
          final result = await connection.request(
            'organizations/inspect',
            body({'profile_id': selected}),
          );
          if (selected == profile)
            patch.addAll({
              'output': result['output'] as String,
              'status': result['status'] as String,
              'draft': result['reply_draft'] as String? ?? '',
              'draftJob': result['reply_job_id'] as String?,
            });
        }
        return FeatureResult(patch: patch);
      },
      quiet: true,
      replace: false,
    );
  }

  Future<bool> submit(
    String action,
    Map<String, dynamic> values, {
    bool retry = false,
  }) async {
    final command = retry
        ? pending
        : OrganizationCommand(action, {
            ...values,
            'organization_id': organizationId,
          });
    if (command == null) return false;
    final done = await mutate<bool>('command', () async {
      await command.execute(connection);
      return const FeatureResult(patch: {'pending': null}, value: true);
    }, start: {'pending': command});
    if (done == true) await refresh();
    return done == true;
  }
}
