import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';

const _keep = Object();

class KnowledgeState extends BlocState {
  const KnowledgeState({
    this.organizations = const [],
    this.records = const [],
    this.repositories = const [],
    this.organization = '',
    this.before,
    this.initializing = true,
    this.loading = false,
    this.mutating = false,
    this.error,
  });
  final List<Map<String, dynamic>> organizations, records;
  final List<String> repositories;
  final String organization;
  final int? before;
  final bool initializing, loading, mutating;
  final String? error;
  bool get busy => loading || mutating;

  KnowledgeState copyWith({
    List<Map<String, dynamic>>? organizations,
    List<Map<String, dynamic>>? records,
    List<String>? repositories,
    String? organization,
    Object? before = _keep,
    bool? initializing,
    bool? loading,
    bool? mutating,
    Object? error = _keep,
  }) => KnowledgeState(
    organizations: organizations ?? this.organizations,
    records: records ?? this.records,
    repositories: repositories ?? this.repositories,
    organization: organization ?? this.organization,
    before: identical(before, _keep) ? this.before : before as int?,
    initializing: initializing ?? this.initializing,
    loading: loading ?? this.loading,
    mutating: mutating ?? this.mutating,
    error: identical(error, _keep) ? this.error : error as String?,
  );
}

class KnowledgeQuery {
  const KnowledgeQuery({
    required this.organization,
    this.repository = '',
    this.kind = '',
    this.status = '',
    this.query = '',
  });
  final String organization, repository, kind, status, query;
}

class InitializeKnowledge extends EventBase {
  InitializeKnowledge(this.organization);
  final String organization;
}

class LoadKnowledge extends EventBase {
  LoadKnowledge(this.query, {this.more = false});
  final KnowledgeQuery query;
  final bool more;
}

class MutateKnowledge extends EventBase {
  MutateKnowledge(this.body);
  final Map<String, dynamic> body;
}

class ClearKnowledge extends EventBase {
  ClearKnowledge({this.reset = false});
  final bool reset;
}

/// Server observations, mutation admission and lifecycle belong to this feature,
/// independently of either renderer's forms, filters and disclosure state.
class KnowledgeBloc extends JuiceBloc<KnowledgeState> {
  KnowledgeBloc(this.connection)
    : super(const KnowledgeState(), [
        () => UseCaseBuilder.typed(
          () => _InitializeKnowledge(),
          concurrency: EventConcurrency.restartable,
        ),
        () => UseCaseBuilder.typed(
          () => _LoadKnowledge(),
          concurrency: EventConcurrency.restartable,
        ),
        () => UseCaseBuilder.typed(
          () => _MutateKnowledge(),
          concurrency: EventConcurrency.droppable,
        ),
        () => UseCaseBuilder.typed(
          () => _ClearKnowledge(),
          concurrency: EventConcurrency.concurrent,
        ),
      ]) {
    _session = session;
    bindStream(connection.stream, (_) => _sessionChanged());
    connection.operator.addListener(_sessionChanged);
  }
  final DashboardBloc connection;
  int _readRevision = 0;
  String _session = '', _preferredOrganization = '';
  bool _started = false;
  String get session =>
      '${connection.generation}:${connection.state.connected}:${connection.operator.value["role"]}';
  void _sessionChanged() {
    if (isClosing || _session == session) return;
    _session = session;
    _readRevision++;
    send(ClearKnowledge(reset: true));
    if (available && _started) initialize(_preferredOrganization);
  }

  KnowledgeQuery? _latest;
  bool get available =>
      !isClosing && connection.state.connected && !connection.disconnecting;
  bool current(String epoch) => available && epoch == session;
  Future<void> initialize(String organization) {
    _started = true;
    _preferredOrganization = organization;
    return send(InitializeKnowledge(organization));
  }

  Future<void> load(KnowledgeQuery query, {bool more = false}) {
    _latest = query;
    return send(LoadKnowledge(query, more: more));
  }

  Future<void> mutate(Map<String, dynamic> body) => send(MutateKnowledge(body));
  Future<void> clearRecords() => send(ClearKnowledge());
  @override
  Future<void> close() {
    if (!isClosing) {
      _readRevision++;
      connection.operator.removeListener(_sessionChanged);
    }
    return super.close();
  }
}

class _InitializeKnowledge
    extends BlocUseCase<KnowledgeBloc, InitializeKnowledge> {
  @override
  Future<void> execute(InitializeKnowledge event) async {
    if (!bloc.available) return;
    final epoch = bloc.session;
    emitUpdate(newState: bloc.state.copyWith(initializing: true, error: null));
    try {
      final directory = await bloc.connection.request(
        'organizations/directory',
      );
      if (!bloc.current(epoch) || isSuperseded) return;
      final organizations = List<Map<String, dynamic>>.from(
        directory['organizations'] ?? [],
      );
      final org = organizations.any((item) => item['id'] == event.organization)
          ? event.organization
          : '${organizations.firstOrNull?['id'] ?? ''}';
      emitUpdate(
        newState: bloc.state.copyWith(
          organizations: organizations,
          organization: org,
          initializing: false,
        ),
      );
      if (org.isNotEmpty)
        await bloc.load(
          bloc._latest?.organization == org
              ? bloc._latest!
              : KnowledgeQuery(organization: org),
        );
    } catch (error) {
      if (bloc.current(epoch) && !isSuperseded)
        emitUpdate(
          newState: bloc.state.copyWith(initializing: false, error: '$error'),
        );
    }
  }
}

class _LoadKnowledge extends BlocUseCase<KnowledgeBloc, LoadKnowledge> {
  @override
  Future<void> execute(LoadKnowledge event) async {
    if (!bloc.available || event.query.organization.isEmpty) return;
    final epoch = bloc.session;
    final revision = ++bloc._readRevision;
    final query = event.query;
    final before = bloc.state.before;
    emitUpdate(
      newState: bloc.state.copyWith(
        organization: query.organization,
        loading: true,
        error: null,
      ),
    );
    try {
      final result = await bloc.connection.request('knowledge', {
        'mode': 'list',
        'organization_id': query.organization,
        'repository': query.repository,
        'kind': query.kind,
        'state': query.status,
        'query': query.query,
        if (event.more && before != null) 'before': before,
      });
      if (!bloc.current(epoch) ||
          revision != bloc._readRevision ||
          isSuperseded)
        return;
      final incoming = List<Map<String, dynamic>>.from(result['records'] ?? []);
      emitUpdate(
        newState: bloc.state.copyWith(
          records: event.more ? [...bloc.state.records, ...incoming] : incoming,
          repositories: List<String>.from(result['repositories'] ?? []),
          before: result['next_before'] as int?,
          loading: false,
        ),
      );
    } catch (error) {
      if (bloc.current(epoch) &&
          revision == bloc._readRevision &&
          !isSuperseded)
        emitUpdate(
          newState: bloc.state.copyWith(loading: false, error: '$error'),
        );
    }
  }
}

class _MutateKnowledge extends BlocUseCase<KnowledgeBloc, MutateKnowledge> {
  @override
  Future<void> execute(MutateKnowledge event) async {
    if (!bloc.available ||
        bloc._latest == null ||
        bloc.connection.operator.value['role'] != 'admin')
      return;
    final epoch = bloc.session;
    final org = bloc._latest!.organization;
    emitUpdate(newState: bloc.state.copyWith(mutating: true, error: null));
    try {
      await bloc.connection.request('knowledge', {
        'organization_id': org,
        ...event.body,
      });
      if (!bloc.current(epoch)) return;
      final query = bloc._latest;
      if (query != null && query.organization == org) await bloc.load(query);
      if (bloc.current(epoch))
        emitUpdate(newState: bloc.state.copyWith(mutating: false));
    } catch (error) {
      if (bloc.current(epoch))
        emitUpdate(
          newState: bloc.state.copyWith(mutating: false, error: '$error'),
        );
    }
  }
}

class _ClearKnowledge extends BlocUseCase<KnowledgeBloc, ClearKnowledge> {
  @override
  Future<void> execute(ClearKnowledge event) async {
    bloc._readRevision++;
    emitUpdate(
      newState: event.reset
          ? const KnowledgeState()
          : bloc.state.copyWith(records: [], before: null),
    );
  }
}
