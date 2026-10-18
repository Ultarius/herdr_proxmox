import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'request_id.dart';

class OrganizationAutomation extends StatefulWidget {
  const OrganizationAutomation({super.key, required this.organizationId});
  final String organizationId;
  @override
  State<OrganizationAutomation> createState() => _OrganizationAutomationState();
}

class _OrganizationAutomationState extends State<OrganizationAutomation> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  Map<String, dynamic>? policy;
  Map<String, dynamic>? pending;
  String? error;
  bool busy = false;
  int generation = -1;
  StreamSubscription? subscription;
  @override
  void initState() {
    super.initState();
    generation = connection.generation;
    subscription = connection.stream.listen((_) {
      if (!mounted) return;
      if (generation != connection.generation || !connection.state.connected) {
        generation = connection.generation;
        setState(() {
          policy = null;
          pending = null;
          error = null;
        });
      }
      if (policy == null && connection.state.connected) refresh();
    });
    connection.notificationRevision.addListener(refresh);
    refresh();
  }

  @override
  void dispose() {
    subscription?.cancel();
    connection.notificationRevision.removeListener(refresh);
    super.dispose();
  }

  Future<void> refresh() async {
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() => busy = true);
    try {
      final result = await connection.request(
        'organizations/automation?organization_id=${Uri.encodeQueryComponent(widget.organizationId)}',
      );
      if (mounted && epoch == connection.generation)
        setState(() {
          policy = Map<String, dynamic>.from(result['policy']);
          error = null;
        });
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) {
        setState(() => busy = false);
        if (epoch != connection.generation && connection.state.connected)
          refresh();
      }
    }
  }

  Future<void> edit() async {
    final epoch = connection.generation;
    final chosen = await editOrganizationAutomation(context, policy!);
    if (!mounted || epoch != connection.generation || chosen == null) return;
    pending = {
      'organization_id': widget.organizationId,
      ...chosen,
      'request_id': newRequestId(),
    };
    await save();
  }

  Future<void> save() async {
    if (busy ||
        pending == null ||
        !connection.state.connected ||
        connection.operator.value['role'] != 'admin')
      return;
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await connection.request('tasks/automation', pending);
      if (mounted && epoch == connection.generation)
        setState(() {
          policy = Map<String, dynamic>.from(result['policy']);
          pending = null;
        });
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() {
          error = '$e';
          if (e is DashboardRequestException &&
              e.statusCode >= 400 &&
              e.statusCode < 500)
            pending = null;
        });
    } finally {
      if (mounted) {
        setState(() => busy = false);
        if (epoch != connection.generation && connection.state.connected)
          refresh();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final admin =
        connection.state.connected &&
        connection.operator.value['role'] == 'admin';
    final p = policy;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Organization automation & limits',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const Text(
          'Shared by every group in this organization. Every valid proposal is retained as a draft; limits control automatic queueing.',
        ),
        if (busy) const LinearProgressIndicator(),
        if (error != null) SelectableText(error!),
        if (p != null) ...[
          Text(
            'Max per meeting: ${p['max_per_meeting'] ?? 1} (automatically queued tasks). Max automatic follow-up depth: ${p['max_follow_up_depth'] ?? 1}.',
          ),
          Text(
            'Organization-wide queueing: ${p['auto_queue_proposals'] == true ? 'on' : 'off'}. Automatic follow-up: ${p['paused'] == true ? 'paused' : 'active'}. Individual groups may opt into queueing; needs_review proposals stay drafts.',
          ),
          Text(
            'Queue limits: ${p['max_open_per_agent'] ?? 2} open tasks per agent; ${p['daily_cap'] ?? 5} automatic tasks per day.',
          ),
          Text(
            'Session budgets: ${p['max_active_sessions'] ?? 0} active sessions; ${p['max_active_meetings'] ?? 0} active meetings; ${p['daily_session_cap'] ?? 0} starts/day. 0 disables these session limits.',
          ),
          Text(
            'Finished-session cleanup: ${p['reap_finished_sessions'] == true ? 'on' : 'off'}. Orphan cleanup: ${p['reap_orphaned_panes'] == true ? 'on' : 'off'}.',
          ),
          const Text(
            'Changing limits does not reprocess discussions that have already been evaluated.',
          ),
        ],
        Wrap(
          spacing: 8,
          children: [
            TextButton(
              onPressed: admin && p != null && !busy && pending == null
                  ? edit
                  : null,
              child: const Text('Edit policy'),
            ),
            TextButton(
              onPressed: !busy ? refresh : null,
              child: const Text('Refresh limits'),
            ),
            if (pending != null)
              TextButton(
                onPressed: admin && !busy ? save : null,
                child: const Text('Retry same policy save'),
              ),
          ],
        ),
      ],
    );
  }
}

Future<Map<String, dynamic>?> editOrganizationAutomation(
  BuildContext context,
  Map<String, dynamic> policy,
) async {
  var enabled = policy['auto_queue_proposals'] == true;
  var autoCreateReleases = policy['auto_create_releases'] == true;
  var paused = policy['paused'] == true;
  var reap = policy['reap_finished_sessions'] == true;
  var reapOrphaned = policy['reap_orphaned_panes'] == true;
  var autoPrepare = policy['auto_prepare'] == true;
  var autoPrepareRestore = policy['auto_prepare_restore'] == true;
  var autoPrepareChecks = policy['auto_prepare_checks'] == true;
  var autoPrepareAgent = policy['auto_prepare_agent'] == true;
  final perMeeting = TextEditingController(
    text: '${policy['max_per_meeting'] ?? 1}',
  );
  final perAgent = TextEditingController(
    text: '${policy['max_open_per_agent'] ?? 2}',
  );
  final depth = TextEditingController(
    text: '${policy['max_follow_up_depth'] ?? 1}',
  );
  final daily = TextEditingController(text: '${policy['daily_cap'] ?? 5}');
  final sessions = TextEditingController(
    text: '${policy['max_active_sessions'] ?? 0}',
  );
  final meetings = TextEditingController(
    text: '${policy['max_active_meetings'] ?? 0}',
  );
  final starts = TextEditingController(
    text: '${policy['daily_session_cap'] ?? 0}',
  );
  final form = GlobalKey<FormState>();
  Future<dynamic>? dialogClosed;
  final selected = await showDialog<Map<String, dynamic>>(
    context: context,
    builder: (context) {
      dialogClosed = ModalRoute.of(context)?.completed;
      return StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Organization automation & limits'),
          content: SizedBox(
            width: 480,
            child: SingleChildScrollView(
              child: Form(
                key: form,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Applies to all groups in this organization, including standalone discussions. Valid proposals are kept as drafts when queueing is disabled or a queue limit is reached; publishing, merging and deployment remain manual.',
                    ),
                    SwitchListTile(
                      value: enabled,
                      onChanged: (value) =>
                          update(() => enabled = value == true),
                      title: const Text(
                        'Automatically queue qualifying follow-up proposals',
                      ),
                    ),
                    SwitchListTile(
                      value: paused,
                      onChanged: (value) =>
                          update(() => paused = value == true),
                      title: const Text('Pause automatic follow-up'),
                      subtitle: const Text(
                        'Queued work stays queued; nothing new starts automatically.',
                      ),
                    ),
                    SwitchListTile(
                      value: autoCreateReleases,
                      onChanged: (value) =>
                          update(() => autoCreateReleases = value == true),
                      title: const Text(
                        'Create release plans from group discussions automatically',
                      ),
                      subtitle: const Text(
                        'New discussions may create named plans with unfinished tasks. Task execution uses its own settings; freezing, integration and publication remain explicit.',
                      ),
                    ),
                    _limitField(perMeeting, 'Max per meeting', 10),
                    SwitchListTile(
                      value: autoPrepare,
                      onChanged: (value) =>
                          update(() => autoPrepare = value == true),
                      title: const Text(
                        'Prepare supported project SDKs before agent launch',
                      ),
                      subtitle: const Text(
                        'Wait for verified SDKs before starting. Unsupported requirements stop the launch.',
                      ),
                    ),
                    SwitchListTile(
                      value: autoPrepareRestore,
                      onChanged: (value) =>
                          update(() => autoPrepareRestore = value == true),
                      title: const Text(
                        'Restore project dependencies before launch',
                      ),
                      subtitle: const Text(
                        'Separate project-code permission. Creates an execution environment and installs frozen dependencies; builds may execute project code.',
                      ),
                    ),
                    SwitchListTile(
                      value: autoPrepareChecks,
                      onChanged: (value) =>
                          update(() => autoPrepareChecks = value == true),
                      title: const Text(
                        'Approve supported project build/test recipes automatically',
                      ),
                      subtitle: const Text(
                        'Separate from SDK installation: these commands can restore dependencies and execute project code.',
                      ),
                    ),
                    SwitchListTile(
                      value: autoPrepareAgent,
                      onChanged: (value) =>
                          update(() => autoPrepareAgent = value == true),
                      title: const Text(
                        'Allow a preparation agent to install Node.js when needed',
                      ),
                      subtitle: const Text(
                        'Requires SDK preparation. A dedicated session can download and execute installers; provenance remains an unverified claim. It shares the service account and is not a sandbox.',
                      ),
                    ),
                    _limitField(
                      perAgent,
                      'Max open per agent (queued + active)',
                      50,
                    ),
                    _limitField(depth, 'Max follow-up depth', 10),
                    _limitField(daily, 'Daily limit (organization)', 100),
                    const Text(
                      'Shared session budget: one limit over task executions, meetings, discovery and helpers. 0 turns a limit off.',
                    ),
                    _limitField(
                      sessions,
                      'Max active sessions (organization)',
                      50,
                    ),
                    _limitField(
                      meetings,
                      'Max active meetings (organization)',
                      10,
                    ),
                    _limitField(
                      starts,
                      'Daily session starts (organization)',
                      500,
                    ),
                    SwitchListTile(
                      value: reap,
                      onChanged: (value) => update(() => reap = value == true),
                      title: const Text(
                        'Close finished sessions automatically',
                      ),
                      subtitle: const Text(
                        'Reclaims memory by archiving and closing sessions whose task already finished. They stay resumable from session archives; an agent that is still working is never closed.',
                      ),
                    ),
                    SwitchListTile(
                      value: reapOrphaned,
                      onChanged: (value) =>
                          update(() => reapOrphaned = value == true),
                      title: const Text('Reclaim panes orphaned by a restart'),
                      subtitle: const Text(
                        'After a Herdr restart, panes can return as plain shells that no agent holds. This closes them once the pane is proven to run nothing and to sit in the recorded checkout. Ambiguous panes are left for you.',
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                if (form.currentState?.validate() != true) return;
                Navigator.pop(context, {
                  'auto_queue_proposals': enabled,
                  'auto_create_releases': autoCreateReleases,
                  'paused': paused,
                  'reap_finished_sessions': reap,
                  'reap_orphaned_panes': reapOrphaned,
                  'auto_prepare': autoPrepare,
                  'auto_prepare_restore': autoPrepareRestore,
                  'auto_prepare_checks': autoPrepareChecks,
                  'auto_prepare_agent': autoPrepareAgent,
                  'max_per_meeting': int.tryParse(perMeeting.text.trim()),
                  'max_open_per_agent': int.tryParse(perAgent.text.trim()),
                  'max_follow_up_depth': int.tryParse(depth.text.trim()),
                  'daily_cap': int.tryParse(daily.text.trim()),
                  'max_active_sessions': int.tryParse(sessions.text.trim()),
                  'max_active_meetings': int.tryParse(meetings.text.trim()),
                  'daily_session_cap': int.tryParse(starts.text.trim()),
                });
              },
              child: const Text('Save policy'),
            ),
          ],
        ),
      );
    },
  );
  await dialogClosed;
  perMeeting.dispose();
  perAgent.dispose();
  depth.dispose();
  daily.dispose();
  sessions.dispose();
  meetings.dispose();
  starts.dispose();
  return selected;
}

Widget _limitField(
  TextEditingController controller,
  String label,
  int maximum,
) => Padding(
  padding: const EdgeInsets.symmetric(vertical: 4),
  child: TextFormField(
    controller: controller,
    validator: (value) {
      final number = int.tryParse(value?.trim() ?? '');
      return number == null || number < 0 || number > maximum
          ? 'Enter a whole number from 0 to $maximum.'
          : null;
    },
    keyboardType: TextInputType.number,
    decoration: InputDecoration(labelText: label, isDense: true),
  ),
);
