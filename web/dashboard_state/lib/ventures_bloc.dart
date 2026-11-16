import 'feature_bloc.dart';
import 'request_id.dart';

/// Administrator-only venture simulation state and actions.
class VenturesBloc extends FeatureBloc {
  VenturesBloc(super.connection)
    : super(initial: {'data': <String, dynamic>{}, 'pending': null}) {
    connection.notificationRevision.addListener(_notified);
    poll('ventures', const Duration(seconds: 15), () => refresh(quiet: true));
  }

  String _selected = '';

  Map<String, dynamic> get data =>
      Map<String, dynamic>.from(state.values['data'] as Map? ?? {});
  List<Map<String, dynamic>> get ventures => _rows('ventures');
  List<Map<String, dynamic>> get organizations => _rows('organizations');
  List<Map<String, dynamic>> get groups => _rows('groups');
  List<Map<String, dynamic>> get agents => _rows('agents');
  List<Map<String, dynamic>> get ticks => _rows('ticks');
  List<Map<String, dynamic>> get outreach => _rows('outreach');
  List<Map<String, dynamic>> get feeds => _rows('feeds');
  List<Map<String, dynamic>> get reviews => _rows('reviews');
  List<Map<String, dynamic>> get candidates => _rows('candidates');
  Map<String, dynamic>? get studio => data['studio'] as Map<String, dynamic>?;
  List<Map<String, dynamic>> get studios => _rows('studios');
  Map<String, dynamic>? get selected =>
      data['selected'] as Map<String, dynamic>?;
  Map<String, dynamic>? get pending =>
      state.values['pending'] as Map<String, dynamic>?;
  Map<String, dynamic> get limits =>
      Map<String, dynamic>.from(data['limits'] as Map? ?? {});

  Map<String, dynamic>? studioFor(String organization) {
    for (final item in studios) {
      if (item['organization_id'] == organization) return item;
    }
    return studio;
  }

  /// False only when the gateway reports the module switched off.
  bool get enabled => data['enabled'] != false;

  /// The module's switch state, and its last poll fault when there is one.
  Map<String, dynamic> get module =>
      Map<String, dynamic>.from(data['module'] as Map? ?? {});

  /// Value-to-label choices the gateway validates against (architectures,
  /// personas, stages, roles, fx_sources). Empty when the gateway sent none.
  Map<String, String> choices(String key) => {
    for (final entry
        in ((data['options'] as Map?)?[key] as Map? ?? const {}).entries)
      '${entry.key}': '${entry.value}',
  };

  /// `running`, `stopped` or `unknown`; empty when the gateway sent none.
  String get herdr => '${data['herdr'] ?? ''}';

  /// Each agent CLI with whether it can run agents now, and why not.
  List<Map<String, dynamic>> get runtimes => _rows('runtimes');

  /// One administrator request whose answer the page needs: a repair plan,
  /// its result, or what the one-call setup did. Callers refresh afterwards.
  Future<Map<String, dynamic>> ask(
    String mode,
    Map<String, dynamic> body,
  ) async {
    final result = await connection.request('ventures', {
      'mode': mode,
      'request_id': newRequestId(),
      ...body,
    });
    return result is Map
        ? Map<String, dynamic>.from(result)
        : <String, dynamic>{};
  }

  List<Map<String, dynamic>> _rows(String key) =>
      List<Map<String, dynamic>>.from(data[key] as List? ?? const []);

  void _notified() => refresh(quiet: true);

  Future<void> select(String identity) async {
    if (identity == _selected) return;
    _selected = identity;
    await refresh();
  }

  Future<void> refresh({bool quiet = false}) async {
    if (!admin) {
      _selected = '';
      return;
    }
    final path = _selected.isEmpty
        ? 'ventures'
        : 'ventures?venture=${Uri.encodeQueryComponent(_selected)}';
    await read(
      'snapshot',
      () async {
        final data = Map<String, dynamic>.from(await connection.request(path));
        if (data['selected'] == null && _selected.isNotEmpty) _selected = '';
        return FeatureResult(patch: {'data': data});
      },
      replace: false,
      quiet: quiet,
    );
  }

  /// One administrator action with a stable request id for create retries.
  Future<bool> submit(
    String mode,
    Map<String, dynamic> body, {
    String? ventureId,
    String? requestId,
  }) async {
    final request = <String, dynamic>{
      'mode': mode,
      'request_id': requestId ?? newRequestId(),
      ...body,
      if (ventureId != null) 'venture_id': ventureId,
    };
    final done = await mutate<bool>(
      'action',
      () async {
        final result = await connection.request('ventures', request);
        if (mode == 'create' && result is Map && result['id'] is String) {
          _selected = result['id'] as String;
        }
        return const FeatureResult(patch: {'pending': null}, value: true);
      },
      admin: true,
      rethrowErrors: true,
      start: {'pending': request},
    );
    if (done == true) await refresh();
    return done == true;
  }

  @override
  Future<void> close() {
    if (!isClosing) connection.notificationRevision.removeListener(_notified);
    return super.close();
  }
}
