import 'dart:async';
import 'dart:convert';

import '../services.dart';

import '../ui.dart';

import 'org_forms.dart';

import 'org_ui.dart';

class OrganizationAutomation extends StatefulComponent {
  const OrganizationAutomation({super.key, required this.organizationId});

  final String organizationId;

  @override
  State<OrganizationAutomation> createState() => _OrganizationAutomationState();
}

class _OrganizationAutomationState extends State<OrganizationAutomation> {
  DashboardBloc get connection => dashboardConnection;

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
        'organizations/automation?organization_id=${Uri.encodeQueryComponent(component.organizationId)}',
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

    await editOrganizationAutomation(
      policy!,
      save: (chosen) async {
        if (!mounted ||
            epoch != connection.generation ||
            !connection.state.connected)
          throw StateError(
            'The connection changed. Close and reopen the policy editor before saving.',
          );
        // Preserve the request identity after an uncertain response, but an edited
        // policy is a new intent and must not reuse that identity. Compare the
        // whole intent rather than its length, so an extra bookkeeping key can
        // never silently drop retry idempotency.
        final intent = {'organization_id': component.organizationId, ...chosen};
        final previous = pending == null
            ? null
            : (Json.from(pending!)..remove('request_id'));
        if (previous == null || jsonEncode(previous) != jsonEncode(intent))
          pending = {...intent, 'request_id': requestId()};
        await save(fromDialog: true);
      },
    );
  }

  Future<void> save({bool fromDialog = false}) async {
    if (busy ||
        pending == null ||
        !connection.state.connected ||
        connection.operator.value['role'] != 'admin') {
      if (fromDialog)
        throw StateError(
          'The policy cannot be saved right now. Check the connection and try again.',
        );
      return;
    }

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
      else if (fromDialog)
        throw StateError(
          'The connection changed while saving. Close and reopen the policy editor.',
        );
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() {
          error = fromDialog ? null : '$e';

          if (e is DashboardRequestException &&
              e.statusCode >= 400 &&
              e.statusCode < 500)
            pending = null;
        });
      if (fromDialog) rethrow;
    } finally {
      if (mounted) {
        setState(() => busy = false);

        if (epoch != connection.generation && connection.state.connected)
          refresh();
      }
    }
  }

  Component value(Object? v) => v is bool
      ? statePill(v ? 'on' : 'off', label: v ? 'On' : 'Off')
      : v is num
      ? el('span', [text(v)], classes: 'org-policy__num')
      : text(v);

  @override
  Component build(BuildContext context) {
    final admin =
        connection.state.connected &&
        connection.operator.value['role'] == 'admin';
    final entries =
        policy?.entries
            .where((e) => automationLabels.containsKey(e.key))
            .toList() ??
        const [];
    final switches = entries.where((e) => e.value is bool).toList();
    final limits = entries.where((e) => e.value is! bool).toList();

    return card(
      'Organization automation & limits',
      [
        if (error != null) errorLine(error),
        if (policy == null && error == null)
          note(busy ? 'Loading limits…' : 'Limits are not loaded.'),
        if (policy != null)
          el('div', [
            if (switches.isNotEmpty)
              el('section', [
                el('h3', [text('Automation')], classes: 'overline'),
                factList([
                  for (final entry in switches)
                    (automationLabels[entry.key]!, value(entry.value)),
                ], classes: 'org-policy__list'),
              ], classes: 'org-policy__group'),
            if (limits.isNotEmpty)
              el('section', [
                el('h3', [text('Limits')], classes: 'overline'),
                factList([
                  for (final entry in limits)
                    (automationLabels[entry.key]!, value(entry.value)),
                ], classes: 'org-policy__list'),
              ], classes: 'org-policy__group'),
          ], classes: 'org-policy'),
      ],
      iconName: 'shield-check',
      subtitle:
          'Shared by every group. Valid proposals are retained as drafts; limits control automatic queueing. Changing limits does not reprocess finalized discussions.',
      actions: [
        button(
          'Refresh limits',
          refresh,
          disabled: busy,
          small: true,
          variant: 'ghost',
          iconName: 'refresh-cw',
        ),
        if (pending != null)
          button(
            'Retry same policy save',
            save,
            disabled: !admin || busy,
            small: true,
          ),
        button(
          'Edit policy',
          edit,
          disabled: !admin || policy == null || busy || pending != null,
          small: true,
          iconName: 'pencil',
        ),
      ],
      classes: 'org-policy-card',
    );
  }
}

/// Display labels for the stored policy keys (shared with the edit form).
const automationLabels = <String, String>{
  'auto_queue_proposals': 'Automatically queue qualifying follow-ups',
  'auto_create_releases':
      'Create release plans from group discussions automatically',
  'paused': 'Pause automatic follow-up',
  'reap_finished_sessions':
      'Archive and close finished idle sessions automatically',
  'reap_orphaned_panes': 'Reclaim proven empty orphaned panes',
  'auto_capture': 'Capture completed candidates automatically',
  'auto_validate': 'Validate captured candidates automatically',
  'auto_publish': 'Publish validated task PRs automatically (task/both routes)',
  'publication_mode': 'Delivery route for new tasks',
  'publication_repositories': 'Repository route overrides (JSON object)',
  'auto_prepare': 'Prepare supported project SDKs before agent launch',
  'auto_prepare_restore': 'Restore project dependencies before launch',
  'auto_prepare_checks':
      'Approve supported project build/test recipes automatically',
  'auto_prepare_agent':
      'Allow a preparation agent to install Node.js when needed',
  'max_per_meeting': 'Max automatic tasks per meeting',
  'max_open_per_agent': 'Max open tasks per agent',
  'max_follow_up_depth': 'Max follow-up depth',
  'daily_cap': 'Daily task limit',
  'max_active_sessions': 'Max active sessions',
  'max_active_meetings': 'Max active meetings',
  'daily_session_cap': 'Daily session starts',
};

Future<Json?> editOrganizationAutomation(
  Json policy, {
  Future<void> Function(Json)? save,
}) async {
  final defaults = <String, dynamic>{
    'auto_queue_proposals': false,
    'auto_create_releases': false,
    'paused': false,
    'auto_validate': false,
    'auto_capture': policy['auto_capture'] ?? policy['auto_validate'] ?? false,
    'auto_publish': false,
    'publication_mode': 'release',
    'auto_prepare': false,
    'auto_prepare_restore': false,
    'auto_prepare_checks': false,
    'auto_prepare_agent': false,
    'reap_finished_sessions': false,
    'reap_orphaned_panes': false,
    'max_per_meeting': 1,
    'max_open_per_agent': 2,
    'max_follow_up_depth': 1,
    'daily_cap': 5,
    'max_active_sessions': 0,
    'max_active_meetings': 0,
    'daily_session_cap': 0,
    ...policy,
    'publication_repositories': jsonEncode(
      policy['publication_repositories'] ?? {},
    ),
  };

  final maxima = {
    'max_per_meeting': 10,
    'max_open_per_agent': 50,
    'max_follow_up_depth': 10,
    'daily_cap': 100,
    'max_active_sessions': 50,
    'max_active_meetings': 10,
    'daily_session_cap': 500,
  };

  const labels = automationLabels;
  Json normalized(Json values) => {
    for (final key in labels.keys)
      key: key == 'publication_repositories'
          ? jsonDecode('${values[key]}')
          : maxima.containsKey(key)
          ? int.parse('${values[key]}')
          : values[key],
  };

  final result = await editRecord(
    'Organization automation & limits',
    defaults,
    labels,
    choices: const {
      'publication_mode': {
        'release': 'Release PRs',
        'both': 'Release or task PRs',
        'task': 'Task PRs',
      },
    },
    submit: save == null ? null : (values) => save(normalized(values)),
    flags: {
      'auto_queue_proposals',
      'auto_create_releases',
      'paused',
      'reap_finished_sessions',
      'reap_orphaned_panes',
      'auto_validate',
      'auto_capture',
      'auto_publish',
      'auto_prepare',
      'auto_prepare_restore',
      'auto_prepare_checks',
      'auto_prepare_agent',
    },
    extra: (v) => [
      note(
        'Delivery routes are recorded when tasks are created. Existing tasks keep their route. Both allows either path; it does not publish twice. Release freezing and publication remain explicit. Overrides map repository paths to release, both or task.',
      ),
      note(
        'Release planning applies to new discussions with an explicit named plan. Planned tasks join the board before completion; their candidate follows the latest captured commit only after required checks pass. Task queueing, capture and validation use their own settings. Freezing, integration and publication remain explicit actions.',
      ),
      note(
        'Managed SDK preparation uses trusted providers. Agent-assisted preparation is a separate opt-in for Node.js: a dedicated session can download and run installers in its allocation, and its provenance remains an unverified claim. This shares the service account and is not a sandbox. Locked Dependency restoration requires its own permission and can execute project build code. Native check approval is separate: build and test commands can restore dependencies and execute project code. Unsupported requirements stop the launch; disabling preparation does not silently resume a waiting launch.',
      ),
      note(
        'Valid proposals remain drafts when queueing is disabled or limits are reached. Pausing leaves queued work queued. A session budget of 0 disables that limit. Finished-session cleanup retains archives and never closes working agents; orphan cleanup requires verified empty panes. Capture checks the agent receipt and committed candidate without a build service. Automatic validation uses the current build executor. Gateway builds stop on restart; install the durable build service under Builds & deployments for restart resilience. Automatic publication requires that service and automatic validation.',
      ),
    ],
    validateFields: (v) {
      final errors = <String, String>{};
      for (final entry in maxima.entries) {
        final n = int.tryParse('${v[entry.key]}');
        if (n == null || n < 0 || n > entry.value)
          errors[entry.key] =
              '${labels[entry.key]} must be a whole number from 0 to ${entry.value}.';
      }
      try {
        final overrides = jsonDecode('${v['publication_repositories']}');
        if (overrides is! Map ||
            overrides.values.any(
              (mode) => !['release', 'both', 'task'].contains(mode),
            )) {
          errors['publication_repositories'] =
              'Enter an object mapping repository paths to release, both or task.';
        }
      } catch (_) {
        errors['publication_repositories'] =
            'Enter a valid JSON object, such as {}.';
      }
      return errors;
    },
  );

  if (result == null) return null;

  return normalized(result);
}
