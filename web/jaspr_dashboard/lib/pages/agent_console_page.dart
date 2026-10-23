import 'snapshot_terminal.dart';
import 'folder_access_panel.dart';
import 'dart:async';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;

import '../services.dart';

import '../ui.dart';

import 'ops_ui.dart';

Duration agentConsoleRefreshInterval(String? status) => switch (status) {
  'working' || 'running' || 'busy' || 'thinking' => const Duration(seconds: 1),

  'idle' || 'done' => const Duration(seconds: 5),

  _ => const Duration(seconds: 2),
};

class AgentConsolePage extends StatefulComponent {
  const AgentConsolePage({super.key, required this.id});

  final String id;

  @override
  State<AgentConsolePage> createState() => _AgentConsolePageState();
}

class _AgentConsolePageState extends State<AgentConsolePage> {
  DashboardBloc get connection => dashboardConnection;

  Timer? timer;

  StreamSubscription? subscription;

  Map<String, dynamic>? profile;

  Map<String, dynamic>? inspection;

  List<Map<String, dynamic>> jobs = [];

  String? error;

  String? responseError;

  String? inputJob;

  /// True only while a submitted response is still being delivered, so the

  /// snapshot poll stops once its job state has settled.

  bool trackDelivery = false;

  /// The reviewed prompt a pending response belongs to, and that response's

  /// command. A lost response must replay the same request ID, even when a new

  /// inspection shows a different prompt. New keys wait until it is reconciled.

  OrganizationCommand? pendingInput;

  DateTime? metadataReadAt;

  int generation = -1;

  bool loading = false;

  bool sending = false;

  bool showDraft = false;

  static int nextId = 0;

  late final String consoleId = 'agent-console-${nextId++}';

  bool expanded = false;

  // Keep the empty host component identical across page renders. Jaspr's DOM
  // attribute reconciler would otherwise remove showModal()'s native open
  // attribute. The page content stays reactive through its original ownership.
  late final Component modalHost = el(
    'dialog',
    [],
    classes: 'console-modal',
    attributes: {'id': '$consoleId-modal', 'aria-label': 'Agent console'},
    events: {
      'cancel': closeExpanded,
      'close': (_) {
        if (mounted && expanded && modal?.open == false) {
          restoreInlinePage();
          setState(() => expanded = false);
          restoreExpansionFocus();
        }
      },
    },
  );

  web.HTMLDialogElement? get modal =>
      web.document.getElementById('$consoleId-modal') as web.HTMLDialogElement?;

  void restoreInlinePage() {
    final shell = web.document.getElementById(consoleId);
    final page = modal?.querySelector('.console-page');
    if (shell != null && page != null) shell.insertBefore(page, modal);
  }

  void releaseModal() {
    expanded = false;
    modal?.close();
    restoreInlinePage();
  }

  void toggleExpanded() {
    final dialog = modal;
    if (dialog == null) return;
    if (expanded) {
      releaseModal();
    } else {
      final page = web.document
          .getElementById(consoleId)
          ?.querySelector('.console-page');
      if (page == null) return;
      // Move the same DOM subtree into a native top-layer host. Jaspr keeps
      // ownership of the page and its descendants, so polling keeps updating
      // the existing emulator. Restore it before deactivation removes children.
      dialog.appendChild(page);
      dialog.setAttribute(
        'aria-label',
        'Agent console for ${profile?['name'] ?? 'Agent'}',
      );
      try {
        dialog.showModal();
        expanded = true;
      } catch (_) {
        restoreInlinePage();
        rethrow;
      }
    }
    setState(() {});
    restoreExpansionFocus();
  }

  void restoreExpansionFocus() {
    Timer.run(() {
      if (!mounted) return;
      (web.document.getElementById('$consoleId-expand') as web.HTMLElement?)
          ?.focus(web.FocusOptions(preventScroll: expanded));
    });
  }

  void closeExpanded(web.Event event) {
    if (!expanded) return;
    event.preventDefault();
    toggleExpanded();
  }

  @override
  void initState() {
    super.initState();

    generation = connection.generation;

    subscription = connection.stream.listen((_) {
      if (!mounted) return;

      if (generation != connection.generation || !connection.state.connected) {
        generation = connection.generation;

        setState(() {
          profile = null;

          inspection = null;

          jobs = [];

          inputJob = null;

          responseError = null;

          pendingInput = null;

          trackDelivery = false;

          metadataReadAt = null;
        });
      } else {
        refresh();
      }
    });

    refresh();
  }

  @override
  void deactivate() {
    // Reconcile the temporary DOM portal before Jaspr removes its owned nodes.
    releaseModal();
    super.deactivate();
  }

  @override
  void dispose() {
    releaseModal();
    timer?.cancel();

    subscription?.cancel();

    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || sending || !connection.state.connected) return;

    timer?.cancel();

    loading = true;

    final epoch = connection.generation;

    try {
      // Read the full snapshot to discover the profile, observe response

      // delivery, and refresh cached metadata once a minute. The frequent

      // terminal reads use the narrower inspect endpoint.

      if (profile == null ||
          trackDelivery ||
          metadataReadAt == null ||
          DateTime.now().difference(metadataReadAt!) >=
              const Duration(minutes: 1)) {
        final data = Map<String, dynamic>.from(
          await connection.request('organizations/state'),
        );

        if (!mounted || epoch != connection.generation) return;

        final current = List<Map<String, dynamic>>.from(
          data['profiles'],
        ).where((p) => p['id'] == component.id).firstOrNull;

        final records = List<Map<String, dynamic>>.from(data['jobs'])
            .where(
              (j) =>
                  j['profile_id'] == component.id ||
                  (j['participants'] as List? ?? []).contains(component.id),
            )
            .toList();

        setState(() {
          profile = current;

          metadataReadAt = DateTime.now();

          jobs = records;

          final delivery = records
              .where((j) => j['id'] == inputJob)
              .firstOrNull;

          if (delivery != null &&
              !['queued', 'running'].contains(delivery['state'])) {
            trackDelivery = false;
          }
        });

        if (current == null) {
          setState(() {
            inspection = null;

            error = 'Agent was not found.';
          });

          return;
        }

        connection.selectedOrganization.value =
            current['organization_id'] as String;
      }

      final current = profile;

      if (current == null) return;

      final result = await OrganizationCommand('inspect', {
        'organization_id': current['organization_id'],

        'profile_id': component.id,
      }).execute(connection);

      if (!mounted || epoch != connection.generation) return;

      setState(() {
        inspection = Map<String, dynamic>.from(result);

        error = null;
      });
    } catch (e) {
      if (mounted && epoch == connection.generation) {
        setState(() {
          error = '$e';

          inspection = null;
        });
      }
    } finally {
      loading = false;

      if (mounted && connection.state.connected) {
        timer = Timer(
          agentConsoleRefreshInterval(inspection?['status'] as String?),

          refresh,
        );
      }
    }
  }

  Future<void> sendKey(String key) async {
    if (sending ||
        pendingInput != null ||
        inspection?['status'] != 'blocked' ||
        inspection?['console_token'] == null ||
        !connection.state.connected)
      return;

    pendingInput = OrganizationCommand('input', {
      'organization_id': profile!['organization_id'],

      'profile_id': component.id,

      'key': key,

      'console_token': inspection!['console_token'],
    });

    await submitPendingInput();
  }

  Future<void> submitPendingInput() async {
    if (sending || pendingInput == null || !connection.state.connected) return;

    final epoch = connection.generation;

    setState(() {
      sending = true;

      responseError = null;
    });

    try {
      final result = await pendingInput!.execute(connection);

      if (!mounted || epoch != connection.generation) return;

      setState(() {
        inputJob = result['id'] as String;

        trackDelivery = true;

        inspection = null;

        pendingInput = null;
      });
    } catch (e) {
      // Keep the pending command: the gateway may have applied it and only the

      // response was lost, so the retry must carry the same request ID.

      if (mounted && epoch == connection.generation) {
        setState(() {
          // A 4xx admission refusal did not create an input job. A transport

          // failure or 5xx cannot establish that, so keep its replay identity.

          if (e is DashboardRequestException &&
              e.statusCode >= 400 &&
              e.statusCode < 500) {
            pendingInput = null;
          }

          responseError = '$e';
        });
      }
    } finally {
      if (mounted) setState(() => sending = false);
    }

    // The gateway queues input; its job state is the delivery evidence.

    await refresh();
  }

  /// Label, tone and dot shape for an inspected terminal status.
  (String, String, String) stateOf(String? status) => switch (status) {
    null => ('Unavailable', 'neutral', 'dash'),
    // Tone comes from the shared palette (agentActivityTone): idle green,
    // working blue, attention amber. A private table here drifted from it and
    // silently flipped the live ring below.
    'working' ||
    'running' ||
    'busy' ||
    'thinking' => ('Working', agentActivityTone('working'), 'filled'),
    'blocked' => ('Needs attention', agentActivityTone('attention'), 'diamond'),
    'idle' ||
    'done' => (capitalized(status), agentActivityTone('idle'), 'filled'),
    _ => (capitalized(status), 'neutral', 'dash'),
  };

  String capitalized(String value) => value.isEmpty
      ? value
      : '${value[0].toUpperCase()}${value.substring(1).replaceAll('_', ' ')}';

  @override
  Component build(BuildContext context) {
    final status = inspection?['status'] as String?;

    final blocked = status == 'blocked';

    final delivery = jobs.where((j) => j['id'] == inputJob).firstOrNull;

    final awaiting =
        inputJob != null &&
        (delivery == null || ['queued', 'running'].contains(delivery['state']));

    final canRespond =
        blocked &&
        !sending &&
        pendingInput == null &&
        !awaiting &&
        connection.state.connected &&
        inspection?['console_token'] != null;

    final draft = '${inspection?['reply_draft'] ?? ''}';

    final state = stateOf(status);

    final name = '${profile?['name'] ?? 'Agent'}';

    final model = [
      '${profile?['provider'] ?? ''}',
      '${profile?['model'] ?? ''}',
    ].where((part) => part.isNotEmpty).join(' / ');

    final project =
        '${inspection?['launch_directory'] ?? profile?['project'] ?? ''}';

    final cadence = agentConsoleRefreshInterval(status).inSeconds;

    // Keyed off the activity, like every other role tile in the app. Keying it
    // off the tone made the breathing ring follow the palette rather than the
    // agent, so restoring the palette silently stopped it flashing while working.
    final live = const [
      'working',
      'running',
      'busy',
      'thinking',
    ].contains(status);

    return el(
      'div',
      [
        opsPage([
          el('div', [
            if (profile != null)
              roleTile(name, role: profile!['role'], live: live),
            pageHeader(
              name,
              overline: 'Agent console',
              subtitle: profile == null
                  ? null
                  : '${profile!['role']} · ${profile!['runtime']}',
              actions: [
                pill(state.$1, tone: state.$2, shape: state.$3),
                button(
                  'Refresh console',
                  refresh,
                  disabled: sending,
                  iconName: 'refresh-cw',
                ),
              ],
            ),
          ], classes: 'console-head'),

          if (profile != null)
            facts([
              (
                'Refresh',
                text('Every $cadence ${cadence == 1 ? 'second' : 'seconds'}'),
              ),
              ('Model', model.isEmpty ? none() : text(model)),
              ('Workspace', project.isEmpty ? none() : mono(project)),
              if ('${inspection?['scratch_directory'] ?? ''}'.isNotEmpty)
                ('Session scratch', mono(inspection!['scratch_directory'])),
              if (delivery != null)
                (
                  'Terminal response',
                  el('span', [
                    pill(
                      '${delivery['state']}',
                      tone: stateTone(delivery['state']),
                      shape: shapeFor(stateTone(delivery['state'])),
                    ),
                    if ('${delivery['error'] ?? ''}'.isNotEmpty)
                      text(' ${delivery['error']}'),
                  ], classes: 'console-response'),
                ),
            ], classes: 'console-facts'),

          if (error != null)
            callout([calloutText(error)], tone: 'danger', role: 'alert'),

          if (responseError != null)
            callout(
              [calloutText(responseError)],
              tone: 'danger',
              role: 'alert',
            ),

          if (pendingInput != null)
            callout(
              [
                calloutText(
                  'Delivery of ${pendingInput!.body?['key']} is unknown. Recheck before sending another key.',
                ),
              ],
              tone: 'warning',
              iconName: 'hourglass',
              actions: [
                button(
                  'Recheck pending response',
                  submitPendingInput,
                  disabled: sending || !connection.state.connected,
                  small: true,
                ),
              ],
            ),

          if (error == null) folderAccessPanel(inspection),

          if (blocked)
            callout(
              [
                calloutText(
                  'Select Allow or Reject in the console prompt, then confirm. Escape cancels where supported.',
                ),
              ],
              tone: 'danger',
              title: 'Requires attention',
              actions: [
                for (final key in [
                  'up',
                  'down',
                  'tab',
                  'shift+tab',
                  'enter',
                  'esc',
                ])
                  button(
                    key == 'enter' ? 'Confirm selected Allow / Reject' : key,
                    () => sendKey(key),
                    disabled: !canRespond,
                    small: true,
                    variant: key == 'enter' ? 'primary' : 'secondary',
                    iconName: switch (key) {
                      'up' => 'chevron-up',
                      'down' => 'chevron-down',
                      'enter' => 'check',
                      _ => null,
                    },
                    classes: key == 'enter' ? null : 'console-key',
                  ),
              ],
            ),

          el('div', [
            terminalPanel(
              title: showDraft && draft.isNotEmpty
                  ? 'Current reply draft'
                  : 'Live console',
              iconName: showDraft && draft.isNotEmpty
                  ? 'file-text'
                  : 'terminal',
              actions: [
                if (draft.isNotEmpty)
                  button(
                    showDraft ? 'Live console' : 'Current reply draft',
                    () => setState(() => showDraft = !showDraft),
                    variant: 'ghost',
                    small: true,
                    iconName: showDraft ? 'terminal' : 'file-text',
                  ),
                dom.button(
                  [
                    icon(expanded ? 'x' : 'scan'),
                    text(expanded ? 'Exit full screen' : 'Full screen'),
                  ],
                  type: dom.ButtonType.button,
                  classes: 'btn btn--ghost btn--sm',
                  onClick: toggleExpanded,
                  attributes: {
                    'id': '$consoleId-expand',
                    'aria-pressed': '$expanded',
                    'aria-controls': '$consoleId-modal',
                  },
                ),
              ],
              body: [
                showDraft && draft.isNotEmpty
                    ? el(
                        'pre',
                        [text(draft)],
                        attributes: {
                          'tabindex': '0',
                          'aria-label': 'Current reply draft',
                        },
                      )
                    : SnapshotTerminal(
                        '${inspection?['output'] ?? 'No live session output available.'}',
                        fill: true,
                      ),
              ],
              footer: [
                meta(
                  'Refreshes every second while working, every 5 seconds while idle, and every 2 seconds while awaiting attention. Retained output may omit earlier work.',
                  iconName: 'history',
                ),
              ],
            ),
          ], classes: 'console-output'),
        ], classes: 'console-page'),
        modalHost,
      ],
      classes: 'console-shell',
      attributes: {
        'id': consoleId,
        'data-expanded': '$expanded',
        'aria-label': 'Agent console for $name',
        if (!expanded) 'role': 'region',
      },
    );
  }
}
