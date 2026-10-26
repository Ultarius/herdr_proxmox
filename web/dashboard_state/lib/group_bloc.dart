import 'dart:convert';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'organization_bloc.dart';
import 'request_id.dart';

const activeGroupJobStates = [
  'queued',
  'running',
  'waiting_for_members',
  'needs_attention',
  'waiting_for_input',
];
const groupHistoryLimit = 20;

class GroupState extends BlocState {
  const GroupState({
    this.group,
    this.profiles = const [],
    this.jobs = const [],
    this.allJobs = const [],
    this.memberStates = const {},
    this.live = const {},
    this.loading = false,
    this.busy = false,
    this.loaded = false,
    this.error,
    this.readError,
    this.pending,
    this.nextBefore,
  });
  final Map<String, dynamic>? group;
  final List<Map<String, dynamic>> profiles, jobs, allJobs;
  final Map<String, dynamic> memberStates;
  final Map<String, Map<String, dynamic>> live;
  final bool loading, busy, loaded;
  final String? error, readError;
  final OrganizationCommand? pending;
  final int? nextBefore;
}

class GroupRefresh extends EventBase {
  GroupRefresh({this.older = false});
  final bool older;
}

class GroupMutation extends EventBase {
  GroupMutation(this.action, this.body, {this.retry = false});
  final String action;
  final Map<String, dynamic> body;
  final bool retry;
  bool succeeded = false, removed = false;
}

class GroupQuery extends EventBase {
  GroupQuery(this.path, this.body);
  final String path;
  final Map<String, dynamic> body;
  Map<String, dynamic>? result;
}

class GroupProposal extends EventBase {
  GroupProposal(this.jobId, this.proposalKey);
  final String jobId, proposalKey;
  Map<String, dynamic>? result;
}

class GroupClearFailure extends EventBase {}

class _GroupSessionChanged extends EventBase {}

/// Shared conversation snapshot, pagination, live inspection and durable command
/// retries. Dialogs and selected tabs are presentation concerns of each view.
class GroupBloc extends JuiceBloc<GroupState> {
  GroupBloc(this.connection, this.groupId)
    : super(const GroupState(), [
        () => UseCaseBuilder.typed(
          () => _RefreshGroup(),
          // The loading guard drops ordinary duplicate reads; concurrent
          // dispatch still admits a fresh session while an old read settles.
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _MutateGroup(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _QueryGroup(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _ProposeGroup(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _ClearGroup(),
          concurrency: EventConcurrency.concurrent,
        ),
        () => UseCaseBuilder.typed(
          () => _ResetGroup(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]) {
    _session = session;
    bindStream(connection.stream, (_) {
      if (_session != session) {
        _sessionChanged();
      } else {
        refresh();
      }
    }, key: 'connection');
    connection.operator.addListener(_sessionChanged);
    connection.notificationRevision.addListener(_notification);
    bindStream(Stream.periodic(const Duration(seconds: 1)), (_) {
      final active = _jobs.any(
        (j) => activeGroupJobStates.contains(j['state']),
      );
      if (++_pollTick % (active && liveViewing ? 1 : 10) == 0) refresh();
    }, key: 'poll');
    refresh();
  }
  final DashboardBloc connection;
  final String groupId;
  bool liveViewing = true;
  int _sessionRevision = 0, _readRevision = 0, _pollTick = 0;
  Map<String, dynamic>? _group;
  List<Map<String, dynamic>> _profiles = [], _jobs = [], _allJobs = [];
  Map<String, dynamic> _memberStates = {};
  Map<String, Map<String, dynamic>> _live = {};
  bool _loading = false, _busy = false, _loaded = false;
  String? _signature, _error, _readError;
  OrganizationCommand? _pending;
  int? _nextBefore;
  final _proposalRequests = <String, String>{};
  String _session = '';
  String get session =>
      '${connection.generation}:${connection.state.connected}:${connection.operator.value['role']}';
  void _sessionChanged() {
    if (isClosing || _session == session) return;
    _session = session;
    _sessionRevision++;
    send(_GroupSessionChanged());
  }

  bool _current(int epoch) =>
      !isClosing &&
      connection.state.connected &&
      !connection.disconnecting &&
      epoch == _sessionRevision &&
      _session == session;
  void _notification() {
    refresh();
  }

  void _publish(void Function(GroupState) emit) => emit(
    GroupState(
      group: _group,
      profiles: List.unmodifiable(_profiles),
      jobs: List.unmodifiable(_jobs),
      allJobs: List.unmodifiable(_allJobs),
      memberStates: Map.unmodifiable(_memberStates),
      live: Map.unmodifiable(_live),
      loading: _loading,
      busy: _busy,
      loaded: _loaded,
      error: _error,
      readError: _readError,
      pending: _pending,
      nextBefore: _nextBefore,
    ),
  );
  Future<void> refresh() => send(GroupRefresh());
  Future<void> loadOlder() => send(GroupRefresh(older: true));
  Future<GroupMutation> submit(
    String action,
    Map<String, dynamic> body, {
    bool retry = false,
  }) async {
    final event = GroupMutation(action, body, retry: retry);
    await send(event);
    return event;
  }

  Future<Map<String, dynamic>?> query(
    String path,
    Map<String, dynamic> body,
  ) async {
    final event = GroupQuery(path, body);
    await send(event);
    return event.result;
  }

  Future<Map<String, dynamic>?> startProposal(
    String jobId,
    String proposalKey,
  ) async {
    final event = GroupProposal(jobId, proposalKey);
    await send(event);
    return event.result;
  }

  Future<void> clearFailure() => send(GroupClearFailure());
  void _reset(void Function(GroupState) emit) {
    _readRevision++;
    _group = null;
    _profiles = [];
    _jobs = [];
    _allJobs = [];
    _memberStates = {};
    _live = {};
    _loading = _busy = _loaded = false;
    _signature = _error = _readError = null;
    _pending = null;
    _nextBefore = null;
    _proposalRequests.clear();
    _publish(emit);
    if (connection.state.connected) refresh();
  }

  Future<void> _refresh(
    bool older,
    void Function(GroupState) emit, {
    bool afterMutation = false,
  }) async {
    if (_loading ||
        (_busy && !afterMutation) ||
        !_current(_sessionRevision) ||
        (older && _nextBefore == null))
      return;
    final epoch = _sessionRevision, revision = ++_readRevision;
    bool current() => _current(epoch) && revision == _readRevision;
    _loading = true;
    try {
      final data = await connection.request('organizations/history', {
        'group_id': groupId,
        if (older) 'before': _nextBefore else 'limit': groupHistoryLimit,
      });
      if (!current()) return;
      if (older) {
        final ids = _jobs.map((j) => j['id']).toSet();
        _jobs = [
          ..._jobs,
          ...List<Map<String, dynamic>>.from(data['jobs'])
              .where((j) => j['kind'] == 'discussion' && !ids.contains(j['id']))
              .toList()
              .reversed,
        ];
        _nextBefore = data['next_before'] as int?;
        _readError = null;
      } else {
        final found =
            List<Map<String, dynamic>>.from(
              data['groups'] ?? [],
            ).where((g) => g['id'] == groupId).firstOrNull ??
            data['group'] as Map<String, dynamic>?;
        final signature = jsonEncode(data);
        if (signature != _signature || _readError != null) {
          _signature = signature;
          _readError = null;
          _group = found;
          if (_jobs.length <= groupHistoryLimit)
            _nextBefore = data['next_before'] as int?;
          _memberStates = Map<String, dynamic>.from(
            data['member_states'] ?? {},
          );
          _allJobs = List<Map<String, dynamic>>.from(data['jobs']);
          _profiles = List<Map<String, dynamic>>.from(data['profiles'])
              .where((p) => p['organization_id'] == found?['organization_id'])
              .toList();
          final older = _jobs.where(
            (j) => !(data['jobs'] as List).any((n) => n['id'] == j['id']),
          );
          _jobs = [
            ...List<Map<String, dynamic>>.from(data['jobs'])
                .where(
                  (j) => j['kind'] == 'discussion' && j['group_id'] == groupId,
                )
                .toList()
                .reversed,
            ...older,
          ];
          _loaded = true;
        }
        if (found != null)
          connection.selectedOrganization.value =
              found['organization_id'] as String;
        final active = _jobs
            .where((j) => activeGroupJobStates.contains(j['state']))
            .firstOrNull;
        // History is useful while the terminal inspection is still pending.
        // Publish it now so the view can render the live-output loading state.
        _publish(emit);
        if (liveViewing && found != null && active != null) {
          try {
            final result = Map<String, dynamic>.from(
              await connection.request('organizations/inspect', {
                'organization_id': found['organization_id'],
                'job_id': active['id'],
                'request_id':
                    'group_stream_${DateTime.now().microsecondsSinceEpoch}',
              }),
            );
            if (!current()) return;
            final previous = _live[active['id']];
            if ((result['contributions'] as List? ?? []).isEmpty &&
                previous != null)
              result['contributions'] = previous['contributions'];
            if ((result['artifact_draft'] as String? ?? '').isEmpty &&
                previous != null)
              result['artifact_draft'] = previous['artifact_draft'];
            _live = {..._live, active['id'] as String: result};
          } catch (error) {
            if (current())
              _live = {
                ..._live,
                active['id'] as String: {
                  ...?_live[active['id']],
                  'inspection_error': '$error',
                },
              };
          }
        }
      }
    } catch (error) {
      if (current()) _readError = '$error';
    } finally {
      if (current()) {
        _loading = false;
        _publish(emit);
      }
    }
  }

  Future<void> _mutate(
    GroupMutation event,
    void Function(GroupState) emit,
  ) async {
    if (_busy ||
        _group == null ||
        !_current(_sessionRevision) ||
        (!event.retry && _pending != null) ||
        (event.retry && _pending == null))
      return;
    final epoch = _sessionRevision;
    _readRevision++;
    _loading = false;
    _pending = event.retry
        ? _pending
        : OrganizationCommand(event.action, {
            ...event.body,
            'organization_id': _group!['organization_id'],
          });
    _busy = true;
    _error = null;
    _publish(emit);
    try {
      await _pending!.execute(connection);
      if (!_current(epoch)) return;
      event.removed = _pending!.action == 'remove_group';
      _pending = null;
      if (event.action == 'group' || event.removed)
        await connection.request('organizations/directory');
      if (!_current(epoch)) return;
      await _refresh(false, emit, afterMutation: true);
      event.succeeded = true;
    } catch (error) {
      if (_current(epoch)) _error = '$error';
    } finally {
      if (_current(epoch)) {
        _busy = false;
        _publish(emit);
      }
    }
  }

  Future<void> _query(GroupQuery event, void Function(GroupState) emit) async {
    final epoch = _sessionRevision;
    if (!_current(epoch)) return;
    try {
      final result = await connection.request(event.path, event.body);
      if (_current(epoch)) event.result = Map<String, dynamic>.from(result);
    } catch (error) {
      if (_current(epoch)) {
        _error = '$error';
        _publish(emit);
      }
    }
  }

  Future<void> _proposal(
    GroupProposal event,
    void Function(GroupState) emit,
  ) async {
    if (_busy ||
        _pending != null ||
        !_current(_sessionRevision) ||
        connection.operator.value['role'] != 'admin')
      return;
    final epoch = _sessionRevision;
    final key = '${event.jobId}:${event.proposalKey}';
    final request = _proposalRequests.putIfAbsent(key, newRequestId);
    _busy = true;
    _error = null;
    _publish(emit);
    try {
      final result = await connection.request('tasks/start_proposal', {
        'job_id': event.jobId,
        'proposal_key': event.proposalKey,
        'request_id': request,
      });
      if (!_current(epoch)) return;
      if (result['id'] is! String || (result['id'] as String).isEmpty)
        throw StateError(
          'The gateway did not return the created task. Retry to recover it.',
        );
      event.result = Map<String, dynamic>.from(result);
      _proposalRequests.remove(key);
    } catch (error) {
      if (_current(epoch)) _error = '$error';
    } finally {
      if (_current(epoch)) {
        _busy = false;
        _publish(emit);
      }
    }
  }

  @override
  Future<void> close() {
    connection.operator.removeListener(_sessionChanged);
    _sessionRevision++;
    connection.notificationRevision.removeListener(_notification);
    _readRevision++;
    return super.close();
  }
}

class _RefreshGroup extends BlocUseCase<GroupBloc, GroupRefresh> {
  @override
  Future<void> execute(GroupRefresh event) =>
      bloc._refresh(event.older, (s) => emitUpdate(newState: s));
}

class _MutateGroup extends BlocUseCase<GroupBloc, GroupMutation> {
  @override
  Future<void> execute(GroupMutation event) =>
      bloc._mutate(event, (s) => emitUpdate(newState: s));
}

class _QueryGroup extends BlocUseCase<GroupBloc, GroupQuery> {
  @override
  Future<void> execute(GroupQuery event) =>
      bloc._query(event, (s) => emitUpdate(newState: s));
}

class _ProposeGroup extends BlocUseCase<GroupBloc, GroupProposal> {
  @override
  Future<void> execute(GroupProposal event) =>
      bloc._proposal(event, (s) => emitUpdate(newState: s));
}

class _ClearGroup extends BlocUseCase<GroupBloc, GroupClearFailure> {
  @override
  Future<void> execute(GroupClearFailure event) async {
    if (bloc._busy) return;
    bloc._pending = null;
    bloc._error = null;
    bloc._publish((s) => emitUpdate(newState: s));
  }
}

class _ResetGroup extends BlocUseCase<GroupBloc, _GroupSessionChanged> {
  @override
  Future<void> execute(_GroupSessionChanged event) async =>
      bloc._reset((s) => emitUpdate(newState: s));
}
