import 'org_form_values.dart';
import 'dart:async';
import 'dart:convert';
import 'package:herdr_dashboard_state/project_directory_choices.dart';
import 'package:web/web.dart' as web;
import '../services.dart';
import '../ui.dart';
import '../navigation.dart';
import 'org_forms.dart';
import 'model_catalog.dart';
import 'org_chart.dart';
import 'collaboration_panel.dart';
import 'organization_automation.dart';
import 'org_ui.dart';

class OrganizationPage extends StatefulComponent {
  const OrganizationPage({super.key, this.initialView = 'chart'});
  final String initialView;
  @override
  State<OrganizationPage> createState() => _OrganizationPageState();
}

class _OrganizationPageState extends State<OrganizationPage> {
  DashboardBloc get connection => dashboardConnection;
  late OrganizationBloc bloc;
  final subscriptions = <StreamSubscription>[];
  Timer? timer;
  int generation = -1;
  late String view = component.initialView == 'collaboration'
      ? 'collaborate'
      : component.initialView;
  String? error;
  String? readinessError;
  bool cleaningSessions = false, transferring = false;
  final sessionRequests = <String, String>{};
  ModelCatalog catalog = const ModelCatalog({});
  String? get selected => connection.selectedOrganization.value;
  set selected(String? value) => connection.selectedOrganization.value = value;
  void selectionChanged() {
    if (mounted) setState(() {});
  }

  void notificationRefresh() {
    if (connection.state.connected && bloc.pending == null)
      bloc.send(OrganizationCommand('refresh'));
  }

  @override
  void initState() {
    super.initState();
    bind();
    subscriptions.add(
      connection.stream.listen((_) {
        if (!mounted) return;
        if (generation != connection.generation ||
            !connection.state.connected) {
          bind();
          sessionRequests.clear();
          error = null;
          readinessError = null;
          catalog = const ModelCatalog({});
        }
        setState(() {});
      }),
    );
    connection.selectedOrganization.addListener(selectionChanged);
    connection.notificationRevision.addListener(notificationRefresh);
    timer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (connection.state.connected && bloc.pending == null)
        bloc.send(OrganizationCommand('refresh'));
    });
    loadCatalog();
  }

  StreamSubscription? orgSubscription;
  void bind() {
    orgSubscription?.cancel();
    if (generation >= 0) bloc.dispose();
    generation = connection.generation;
    bloc = OrganizationBloc(connection);
    orgSubscription = bloc.stream.listen((_) {
      if (mounted) {
        final valid = bloc.state.organizations.where(
          (o) => o['removed_at'] == null,
        );
        if (valid.isNotEmpty && !valid.any((o) => o['id'] == selected))
          selected = '${valid.first['id']}';
        setState(() {});
      }
    });
    bloc.send(OrganizationCommand('refresh'));
  }

  @override
  void dispose() {
    timer?.cancel();
    orgSubscription?.cancel();
    for (final s in subscriptions) s.cancel();
    connection.selectedOrganization.removeListener(selectionChanged);
    connection.notificationRevision.removeListener(notificationRefresh);
    bloc.dispose();
    super.dispose();
  }

  Future<void> loadCatalog() async {
    final epoch = connection.generation;
    try {
      final result = await connection.request('models');
      if (mounted && epoch == connection.generation)
        setState(
          () => catalog = ModelCatalog.fromJson(result is Json ? result : null),
        );
    } catch (_) {}
  }

  Future<void> submit(String action, Json body) async {
    if (!connection.state.connected || bloc.state.busy || bloc.pending != null)
      return;
    await bloc.send(OrganizationCommand(action, body));
  }

  Future<void> editOrganization([Json? org]) async {
    final epoch = connection.generation;
    Json? templates;
    var projectFolders = <String, String>{};
    String? projectFolderError;
    if (org == null) {
      try {
        templates = Json.from(
          await connection.request('organizations/templates'),
        );
      } catch (e) {
        templates = {'error': 'Role presets could not load: $e'};
      }
      try {
        projectFolders = projectDirectoryChoices(
          Json.from(await connection.request('projects/browse', {'path': ''})),
        );
      } catch (e) {
        projectFolderError = 'Project folders could not load: $e';
      }
    }
    if (!mounted || epoch != connection.generation) return;
    final values = await organizationForm(
      org,
      templates,
      (body) async {
        if (epoch != connection.generation || !connection.state.connected)
          throw StateError('Connection changed. Reopen the organization form.');
        final result = Json.from(
          await connection.request('organizations/templates', body),
        );
        if (epoch != connection.generation)
          throw StateError('Connection changed. Reopen the organization form.');
        return result;
      },
      (body) async {
        if (epoch != connection.generation || !connection.state.connected)
          throw StateError('Connection changed. Reopen the organization form.');
        final result = Json.from(
          await connection.request('organizations/templates/library', body),
        );
        if (epoch != connection.generation)
          throw StateError('Connection changed. Reopen the organization form.');
        return result;
      },
      connection.operator.value['role'] == 'admin',
      projectFolders,
      projectFolderError,
    );
    if (values == null || !mounted || epoch != connection.generation) return;
    final previous = bloc.state.organizations.map((o) => o['id']).toSet();
    await submit('save', values);
    if (mounted && org == null && bloc.state.error == null) {
      final created = bloc.state.organizations
          .where((o) => !previous.contains(o['id']))
          .firstOrNull;
      if (created != null) selected = created['id'];
    }
  }

  Future<void> hire(Json org, [Json? profile]) async {
    final epoch = connection.generation;
    // A failed lookup is a request error, not evidence of missing CLI accounts.
    var runtimes = const <Json>[];
    try {
      final status = await connection.request('cli-setup');
      runtimes = [
        for (final cli in (status['clis'] as List? ?? []))
          if (cli is Json) cli,
      ];
    } catch (failure) {
      if (mounted &&
          epoch == connection.generation &&
          connection.state.connected) {
        setState(
          () => readinessError =
              'Could not load runtime readiness: $failure. Try hiring or editing again to retry.',
        );
      }
      return;
    }
    if (!mounted ||
        epoch != connection.generation ||
        !connection.state.connected)
      return;
    setState(() => readinessError = null);
    final values = await hireForm(
      org,
      bloc.state.profiles
          .where(
            (p) => p['organization_id'] == org['id'] && p['group_id'] == null,
          )
          .toList(),
      catalog,
      profile,
      runtimes,
    );
    if (values != null && mounted && epoch == connection.generation) {
      await submit('hire', values);
      if (mounted) setState(() => view = 'team');
    }
  }

  Future<void> delegate(Json org) async {
    final epoch = connection.generation;
    final profiles = bloc.state.profiles
        .where(
          (p) =>
              p['organization_id'] == org['id'] &&
              p['group_id'] == null &&
              bloc.state.jobs.any(
                (j) =>
                    j['kind'] == 'launch' &&
                    j['profile_id'] == p['id'] &&
                    j['state'] == 'persona_sent',
              ),
        )
        .toList();
    final choices = {for (final p in profiles) '${p['id']}': '${p['name']}'};
    final v = await editRecord(
      'Delegate work',
      {'organization_id': org['id']},
      {
        'sender_id': 'From launched agent',
        'profile_id': 'To launched agent',
        'task': 'Task instructions',
      },
      required: {'sender_id', 'profile_id', 'task'},
      long: {'task'},
      limits: {'task': 8000},
      choices: {
        'sender_id': {'': 'Select agent', ...choices},
        'profile_id': {'': 'Select agent', ...choices},
      },
      validate: (v) => v['sender_id'] == v['profile_id']
          ? 'Choose a different recipient.'
          : null,
    );
    if (v != null && mounted && epoch == connection.generation)
      await submit('delegate', v);
  }

  Future<String?> reportForm(
    String title,
    String label, {
    int limit = 8000,
  }) async {
    final v = await editRecord(
      title,
      {},
      {'result': label},
      required: {'result'},
      long: {'result'},
      limits: {'result': limit},
    );
    return v?['result'];
  }

  Future<void> report(Json job) async {
    final epoch = connection.generation;
    final v = await reportForm(
      'Record completion report',
      'Operator completion report',
    );
    if (v != null && mounted && epoch == connection.generation)
      await submit('report', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'result': v,
      });
  }

  Future<void> resolveDelegation(Json job, String decision) async {
    final epoch = connection.generation;
    final reason = await reportForm(
      decision == 'complete'
          ? 'Accept inspected delegation completion'
          : 'Cancel inspected delegation',
      'Inspection evidence and reason (releases work reservation)',
      limit: 2000,
    );
    if (reason != null && mounted && epoch == connection.generation)
      await submit('resolve_delegation', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'decision': decision,
        'inspected': true,
        'reason': reason,
      });
  }

  Future<void> release(Json job) async {
    final epoch = connection.generation;
    if (await confirm(
          'Release run binding?',
          'Allows another launch. It leaves the terminal and process running. Archive and close an idle session to stop it and avoid duplicate work.',
        ) &&
        mounted &&
        epoch == connection.generation)
      await submit('release', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
      });
  }

  Future<void> manageSession(Json job, String mode) async {
    final epoch = connection.generation;
    final descriptions = {
      'restart':
          'Starts a fresh session in retained checkout using the current profile, waits for instructions and does not replay the task.',
      'continue':
          'Starts a fresh session with archived evidence and current profile. It waits for instructions and does not resume or replay the task.',
      'resume':
          'Restores the archived provider conversation binding in retained checkout. Restoration is not completed work; it waits for instructions.',
      'close':
          'Preserves available evidence before closing a verified idle session. If capture fails, its pane stays open.',
    };
    if (!await confirm(
      '$mode session?',
      '${job['profile']?['name'] ?? job['profile_id']}\nAlias: ${job['alias']}\nPane: ${job['pane_id']}\n${descriptions[mode]}',
      confirmLabel: 'I inspected the idle session',
    ))
      return;
    if (!mounted || epoch != connection.generation) return;
    final selection = 'session:${job['id']}:$mode';
    final request = sessionRequests.putIfAbsent(selection, requestId);
    try {
      await connection.request('organizations/session', {
        'organization_id': job['organization_id'],
        'job_id': job['id'],
        'mode': mode,
        'inspected': true,
        'request_id': request,
        if (job['archive_id'] != null) 'archive_id': job['archive_id'],
      });
      if (!mounted || epoch != connection.generation) return;
      sessionRequests.remove(selection);
      bloc.send(OrganizationCommand('refresh'));
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    }
  }

  Future<void> cleanupSessions(List<Json> jobs) async {
    final epoch = connection.generation;
    final organizationId = selected;
    final obsolete = jobs
        .where(
          (j) =>
              j['kind'] == 'launch' &&
              j['state'] == 'released' &&
              j['session_closed_at'] == null,
        )
        .toList();
    final chosen = <String>{};
    final ok = await showDialog<bool>(
      'Review obsolete sessions',
      (close) => el('div', [
        note(
          'Only selected released sessions are considered. Busy, blocked, changed or unidentified sessions are skipped. Repository files remain intact.',
        ),
        if (obsolete.isEmpty)
          emptyState(
            'No released sessions are waiting to close.',
            iconName: 'archive',
          ),
        el(
          'ul',
          [
            for (final j in obsolete)
              el('li', [
                el('label', [
                  el(
                    'input',
                    [],
                    attributes: {
                      'type': 'checkbox',
                      'checked': '${chosen.contains(j['id'])}',
                    },
                    events: {
                      'change': (e) {
                        if ((e.target as web.HTMLInputElement).checked) {
                          chosen.add('${j['id']}');
                        } else {
                          chosen.remove(j['id']);
                        }
                        refreshDialogs();
                      },
                    },
                  ),
                  el('span', [
                    el('span', [
                      text('${j['profile']?['name'] ?? j['profile_id']}'),
                    ], classes: 'org-select__name'),
                    el('span', [
                      mono(j['alias']),
                      text(' · '),
                      mono(j['pane_id'] ?? 'Pane identity missing'),
                      text(' · '),
                      timeStamp(j['created_at']),
                    ], classes: 'org-select__meta'),
                  ], classes: 'org-select__text'),
                ], classes: 'org-select'),
              ]),
          ],
          classes: 'org-select-list',
          attributes: {'role': 'list'},
        ),
        el('div', [
          button('Cancel', () => close(false)),
          button(
            'Archive and close selected',
            () => close(true),
            disabled: chosen.isEmpty,
            primary: true,
            iconName: 'archive',
          ),
        ], classes: 'actions org-dialog-actions'),
      ]),
    );
    if (ok != true ||
        !mounted ||
        epoch != connection.generation ||
        organizationId != selected)
      return;
    setState(() => cleaningSessions = true);
    try {
      final result = await OrganizationCommand('session', {
        'organization_id': organizationId,
        'mode': 'cleanup',
        'job_ids': chosen.toList(),
        'inspected': true,
      }).execute(connection);
      if (!mounted || epoch != connection.generation) return;
      await showDialog<void>(
        'Session cleanup results',
        (close) => el('div', [
          el(
            'ul',
            [
              for (final r in records(result['results']))
                el('li', [
                  mono(r['id']),
                  statePill(r['state']),
                  if ('${r['reason'] ?? ''}'.isNotEmpty) note(r['reason']),
                ], classes: 'org-result'),
            ],
            classes: 'org-result-list',
            attributes: {'role': 'list'},
          ),
          el('div', [
            button('Close', () => close(null)),
          ], classes: 'actions org-dialog-actions'),
        ]),
      );
      if (mounted && epoch == connection.generation)
        bloc.send(OrganizationCommand('refresh'));
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => cleaningSessions = false);
    }
  }

  Future<void> showArchives() async {
    final epoch = connection.generation;
    final organizationId = selected;
    bool current() =>
        mounted && epoch == connection.generation && organizationId == selected;
    try {
      final response = await connection.request('organizations/session', {
        'mode': 'archives',
        'organization_id': organizationId,
      });
      if (!current()) return;
      final archives = records(response['archives']);
      final chosen = await showDialog<Json>(
        'Archived sessions',
        (close) => el('div', [
          if (archives.isEmpty)
            emptyState(
              'No archives yet. Archive and close a verified idle session first.',
              iconName: 'archive',
            ),
          el(
            'ul',
            [
              for (final a in archives)
                el('li', [
                  agentTile(
                    a['identity']?['name'] ?? 'Historical agent',
                    role: a['identity']?['role'],
                    size: 'sm',
                  ),
                  el('div', [
                    el('p', [
                      text('${a['identity']?['name'] ?? 'Historical agent'}'),
                    ], classes: 'org-archive__name'),
                    el('p', [
                      mono(a['alias']),
                      text(' · Archived '),
                      timeStamp(a['archived_at']),
                      if (a['native_resume_available'] == true)
                        text(' · Native resume available'),
                    ], classes: 'org-archive__meta'),
                    if (a['terminal_unavailable'] != null)
                      note('Terminal history unavailable; saved records only.'),
                  ], classes: 'org-archive__text'),
                  button(
                    'View history',
                    () => close(a),
                    small: true,
                    iconName: 'history',
                  ),
                ], classes: 'org-archive'),
            ],
            classes: 'org-archive-list',
            attributes: {'role': 'list'},
          ),
          el('div', [
            button('Close', () => close(null)),
          ], classes: 'actions org-dialog-actions'),
        ]),
      );
      if (chosen == null || !current()) return;
      final data = await connection.request('organizations/session', {
        'mode': 'view_archive',
        'organization_id': organizationId,
        'archive_id': chosen['id'],
      });
      if (!current()) return;
      final native = data['native_resume_available'] == true;
      final action = await showDialog<String>(
        'Archived conversation: ${data['identity']?['name'] ?? 'agent'}',
        (close) => el('div', [
          note(
            native
                ? 'Bounded historical evidence, not a complete provider transcript. Native resume restores the original conversation binding and waits for instructions.'
                : 'Bounded historical evidence, not a complete provider transcript. Continuing creates a fresh conversation and waits for instructions.',
          ),
          factList([
            ('Checkout', mono(data['worktree_path'] ?? data['source_project'])),
            ('Branch', mono(data['worktree_branch'])),
            ('Task', mono(data['task_id'])),
          ], classes: 'org-dialog-facts'),
          if (data['terminal_capture']?['forced_stop'] == true)
            callout('warning', 'triangle-alert', [
              note(
                'Forced stop: visible screen only, up to 160 lines. Earlier history is omitted; reply may be incomplete.',
              ),
            ]),
          if (data['terminal_capture']?['character_truncated'] == true)
            callout('warning', 'triangle-alert', [
              note('Terminal output exceeded archive character limit.'),
            ]),
          pre(
            data['terminal'] ??
                data['terminal_unavailable'] ??
                'Terminal unavailable.',
          ),
          for (final m in data['messages'] as List? ?? []) pre(m),
          el('div', [
            button(
              'Download archive',
              () =>
                  downloadText(jsonEncode(data), 'session-${data['id']}.json'),
              iconName: 'download',
            ),
            button('Close', () => close(null)),
            if (native)
              button('Resume original conversation', () => close('resume')),
            button(
              'Continue with saved context',
              () => close('continue'),
              primary: true,
            ),
          ], classes: 'actions org-dialog-actions'),
        ]),
      );
      if (action != null && current())
        await manageSession({
          'id': data['run_id'],
          'organization_id': organizationId,
          'archive_id': data['id'],
          'alias': data['alias'],
          'pane_id': data['pane_id'],
          'profile': data['identity'],
        }, action);
    } catch (e) {
      if (current()) setState(() => error = '$e');
    }
  }

  Future<void> transfer(bool importing) async {
    if (transferring) return;
    final epoch = connection.generation;
    setState(() {
      transferring = true;
      error = null;
    });
    try {
      if (!importing) {
        final data = await connection.request('organizations/export');
        if (!mounted || epoch != connection.generation) return;
        await downloadText(
          const JsonEncoder.withIndent('  ').convert(data),
          'herdr-configuration.json',
        );
        toast(
          data['import_size_supported'] == false
              ? 'Complete backup downloaded, but it exceeds the 2 MB import limit. Increase migration capacity before replacing the LXC.'
              : 'Configuration downloaded. Keep this file before replacing the LXC.',
        );
      } else {
        final content = await pickConfiguration();
        if (content == null || !mounted || epoch != connection.generation)
          return;
        final data = jsonDecode(content) as Json;
        final importSummary = configurationImportSummary(data);
        if (!await confirm(
          'Import configuration?',
          '$importSummary Existing records will not be overwritten. Paths and permission settings are preserved; review them before launching. No agents start automatically.',
          confirmLabel: 'Import',
        ))
          return;
        if (!mounted || epoch != connection.generation) return;
        final imported = await connection.request('organizations/import', data);
        final counts = imported['imported'] as Map? ?? {};
        if (!mounted || epoch != connection.generation) return;
        await bloc.send(OrganizationCommand('refresh'));
        toast(
          'Configuration imported: ${counts['team_templates'] ?? 0} saved templates and ${counts['team_template_versions'] ?? 0} template versions. Review paths and connect CLI accounts before launching.',
        );
      }
    } catch (e) {
      if (mounted && epoch == connection.generation)
        setState(() => error = 'Configuration transfer failed: $e');
    } finally {
      if (mounted) setState(() => transferring = false);
    }
  }

  Component run(Json job, bool busy) {
    final admin = connection.operator.value['role'] == 'admin';
    final launch = job['kind'] == 'launch';
    final preparation = job['kind'] == 'environment_preparation';
    final profile = bloc.state.profiles
        .where((p) => p['id'] == job['profile_id'])
        .firstOrNull;
    final name = '${profile?['name'] ?? 'Agent'}';
    final saved = job['profile'] is Map
        ? Map<String, dynamic>.from(job['profile'])
        : null;
    final actions = <Component>[
      if (job['kind'] == 'delegate' &&
          admin &&
          [
            'delivered',
            'reported_complete',
            'needs_attention',
            'uncertain',
          ].contains(job['state'])) ...[
        if (job['state'] == 'reported_complete')
          button(
            'Accept completion and free agent',
            () => resolveDelegation(job, 'complete'),
            disabled: busy,
            small: true,
            iconName: 'circle-check',
          ),
        button(
          'Cancel after inspection',
          () => resolveDelegation(job, 'cancel'),
          disabled: busy,
          small: true,
          iconName: 'circle-x',
        ),
      ],
      if (launch &&
          ['persona_sent', 'released'].contains(job['state']) &&
          job['session_closed_at'] == null &&
          admin) ...[
        button(
          'Archive and close session',
          () => manageSession(job, 'close'),
          disabled: busy,
          small: true,
          iconName: 'archive',
        ),
        if (job['state'] == 'persona_sent')
          button(
            'Restart in assigned checkout',
            () => manageSession(job, 'restart'),
            disabled: busy,
            small: true,
            iconName: 'rotate-ccw',
          ),
      ],
      if ((launch || preparation) &&
          !['queued', 'running', 'released', 'finished'].contains(job['state']))
        button(
          'Release run binding',
          () => release(job),
          disabled: busy,
          small: true,
          iconName: 'link',
        ),
      if (job['kind'] == 'delegate' && job['state'] == 'delivered')
        button(
          'Record completion report',
          () => report(job),
          disabled: busy,
          small: true,
          iconName: 'pencil',
        ),
    ];
    return el('li', [
      if (profile != null && profile['group_id'] == null)
        agentTile(name, role: profile['role'], size: 'sm')
      else
        el(
          'span',
          [
            icon(
              profile != null ? 'message-square' : (launch ? 'play' : 'send'),
            ),
          ],
          classes: 'icon-tile icon-tile--sm',
          attributes: {'aria-hidden': 'true'},
        ),
      el('div', [
        el('div', [
          el('h3', [
            text(
              preparation
                  ? 'Environment preparation'
                  : launch
                  ? 'Launch'
                  : 'Delegation',
            ),
            el('span', [text(' · $name')], classes: 'org-run__agent'),
          ], classes: 'org-run__title'),
          statePill(job['state']),
          el('span', [
            text('Updated '),
            timeStamp(job['updated_at']),
          ], classes: 'org-run__time'),
        ], classes: 'org-run__head'),
        factList([
          if (saved != null)
            (
              'Model at launch',
              text(
                '${savedModelLabel(saved)} · Reasoning: ${savedReasoningLabel(saved)}',
              ),
            ),
          if (job['launch_arguments'] is List)
            (
              'Arguments sent to Herdr',
              el('code', [text((job['launch_arguments'] as List).join(' '))]),
            ),
          if (job['alias'] != null) ('Herdr name', mono(job['alias'])),
          if (job['alias'] != null)
            ('Pane', mono(job['pane_id'] ?? 'not allocated')),
          if (job['progress'] != null) ('Progress', textBlock(job['progress'])),
          if (job['preparation_execution_id'] != null)
            ('Preparation execution', mono(job['preparation_execution_id'])),
          if (job['parent_execution_id'] != null)
            ('Waiting launch', mono(job['parent_execution_id'])),
          if (job['task'] != null)
            ('From ${job['sender_name']}', textBlock(job['task'])),
          if (job['workspace_mode'] == 'worktree')
            ('Worktree', mono(job['worktree_path'])),
          if (job['workspace_mode'] == 'worktree')
            ('Branch', mono(job['worktree_branch'])),
          if (job['resolution'] is Map)
            (
              'Resolved',
              text(
                '${job['resolution']['decision']} · ${job['resolution']['reason']}',
              ),
            ),
          if (job['result'] != null)
            (
              'Operator completion report',
              '${job['result']}'.trim().isEmpty
                  ? noValue()
                  : textBlock(job['result']),
            ),
        ], classes: 'org-run__facts'),
        if (job['workspace_note'] != null) note(job['workspace_note']),
        if (job['error'] != null) errorLine(job['error']),
        if (actions.isNotEmpty) row(actions),
      ], classes: 'org-run__body'),
    ], classes: 'org-run');
  }

  Component teamRow(
    Json org,
    Json p,
    List<Json> profiles,
    List<Json> jobs,
    bool busy,
  ) {
    final activity = liveActivity(
      connection.state.agents
          .where((a) => a['profile_id'] == p['id'])
          .firstOrNull,
    );
    final launch = jobs
        .where(
          (j) =>
              j['kind'] == 'launch' &&
              j['profile_id'] == p['id'] &&
              j['state'] != 'released',
        )
        .firstOrNull;
    final manager = profiles
        .where((x) => x['id'] == p['manager_id'])
        .firstOrNull;
    return el('tr', [
      el(
        'th',
        [
          agentIdentity(
            p['name'],
            role: p['role'],
            meta: '${p['role']} · ${p['runtime']}',
            live: activity == 'working',
            extra: [
              el('span', [
                text(
                  manager == null
                      ? 'Reports to organization owner'
                      : 'Reports to ${manager['name']}',
                ),
              ], classes: 'org-agent__meta'),
              if (p['coordination_binding'] != null)
                el('span', [
                  text(
                    p['coordination_binding'] == 'used'
                        ? 'Used by coordination: ${(p['coordination_repositories'] as List? ?? []).join(', ')}'
                        : 'Inactive coordinator profile · no repository binding',
                  ),
                ], classes: 'org-agent__meta'),
              disclosure('Persona and responsibilities', [
                textBlock(p['persona']),
                factList([
                  ('Project', mono(p['project'])),
                  (
                    'Reports to',
                    text(manager?['name'] ?? 'Organization owner'),
                  ),
                  ('Persona', text('v${p['version']}')),
                ]),
              ], classes: 'org-team__persona'),
            ],
          ),
        ],
        classes: 'org-team__agent',
        attributes: {'scope': 'row'},
      ),
      el('td', [
        el(
          'span',
          [text(savedModelLabel(p))],
          classes: '${p['model'] ?? ''}'.isEmpty
              ? 'org-team__model org-team__model--default'
              : 'org-team__model',
        ),
        el('span', [
          text('Reasoning: ${savedReasoningLabel(p)}'),
        ], classes: 'org-team__sub'),
      ], classes: 'org-team__modelcell'),
      el('td', [
        activityPill(activity),
        if (launch != null)
          el('span', [
            text('Launch: ${'${launch['state']}'.replaceAll('_', ' ')}'),
          ], classes: 'org-team__sub'),
      ], classes: 'org-team__status'),
      el('td', [
        el('div', [
          button(
            'Launch ${p['name']}',
            () => submit('launch', {
              'organization_id': org['id'],
              'profile_id': p['id'],
            }),
            disabled:
                busy ||
                jobs.any(
                  (j) =>
                      j['kind'] == 'launch' &&
                      j['profile_id'] == p['id'] &&
                      j['state'] != 'released',
                ),
            primary: true,
            small: true,
            iconName: 'play',
          ),
          button(
            'Open console',
            () => navigate('/agents/${p['id']}'),
            small: true,
            iconName: 'terminal',
          ),
          button(
            'Edit persona',
            () => hire(org, p),
            disabled: busy,
            small: true,
            variant: 'ghost',
            iconName: 'pencil',
          ),
          button(
            'Remove agent',
            () async {
              final epoch = connection.generation;
              if (await confirm(
                    'Remove ${p['name']}?',
                    'Removes this agent and closes its bound Herdr terminal. History, artifacts and worktree files are preserved. Remove it from groups first.',
                  ) &&
                  mounted &&
                  epoch == connection.generation)
                await submit('remove_agent', {
                  'organization_id': org['id'],
                  'profile_id': p['id'],
                });
            },
            disabled: busy,
            small: true,
            variant: 'ghost',
            classes: 'btn--danger',
            iconName: 'trash-2',
          ),
        ], classes: 'org-team__buttons'),
      ], classes: 'org-team__actions'),
    ]);
  }

  Component teamView(
    Json org,
    List<Json> profiles,
    List<Json> jobs,
    bool busy,
  ) {
    Component heading(String label, String iconName) => el(
      'th',
      [
        el('span', [
          icon(iconName, classes: 'icon--sm'),
          text(label),
        ], classes: 'th'),
      ],
      attributes: {'scope': 'col'},
    );
    return card(
      'Team',
      [
        if (profiles.isEmpty)
          emptyState(
            'No agents yet. Hire an agent to start the team.',
            iconName: 'bot',
          )
        else
          el(
            'div',
            [
              el('table', [
                el('caption', [
                  text('Agents in ${org['name'] ?? 'this organization'}'),
                ], classes: 'visually-hidden'),
                el('thead', [
                  el('tr', [
                    heading('Agent', 'bot'),
                    heading('Model', 'cpu'),
                    heading('Status', 'activity'),
                    el(
                      'th',
                      [
                        el('span', [
                          text('Actions'),
                        ], classes: 'visually-hidden'),
                      ],
                      classes: 'org-team__actions',
                      attributes: {'scope': 'col'},
                    ),
                  ]),
                ]),
                el('tbody', [
                  for (final p in profiles)
                    teamRow(org, p, profiles, jobs, busy),
                ]),
              ], classes: 'table org-team'),
            ],
            classes: 'table-scroll org-team-wrap',
            attributes: {
              'role': 'region',
              'aria-label': 'Team',
              'tabindex': '0',
            },
          ),
      ],
      iconName: 'users',
      count: profiles.length,
      classes: 'org-team-card',
    );
  }

  Component runsView(List<Json> jobs, bool busy) {
    final runs = jobs.reversed
        .where((j) => ['launch', 'delegate'].contains(j['kind']))
        .toList();
    return card(
      'Runs',
      [
        if (runs.isEmpty)
          emptyState(
            'No runs yet. Launch an agent or delegate work to see its runs here.',
            iconName: 'history',
          )
        else
          el(
            'ol',
            [for (final job in runs) run(job, busy)],
            classes: 'org-runs',
            attributes: {'role': 'list'},
          ),
      ],
      iconName: 'history',
      count: runs.length,
      subtitle:
          'Delivery confirms terminal input, not completed work. Launch states are historical; see Live agents for current activity.',
      actions: [
        if (connection.operator.value['role'] == 'admin') ...[
          button(
            'Clean up obsolete panes',
            () => cleanupSessions(jobs),
            disabled: busy || cleaningSessions,
            small: true,
            iconName: 'archive',
          ),
          button(
            'Archived sessions',
            showArchives,
            disabled: busy,
            small: true,
            iconName: 'history',
          ),
        ],
      ],
      classes: 'org-runs-card',
    );
  }

  @override
  Component build(BuildContext context) {
    final state = bloc.state;
    final organizations = state.organizations
        .where((o) => o['removed_at'] == null)
        .toList();
    final org =
        organizations.where((o) => o['id'] == selected).firstOrNull ??
        organizations.firstOrNull;
    final busy = state.busy || bloc.pending != null;
    final profiles = state.profiles
        .where(
          (p) =>
              p['organization_id'] == org?['id'] &&
              p['group_id'] == null &&
              p['removed_at'] == null,
        )
        .toList();
    final jobs = state.jobs
        .where((j) => j['organization_id'] == org?['id'])
        .toList();
    final purpose = '${org?['purpose'] ?? ''}'.trim();
    return el('div', [
      pageHeader(
        org == null ? 'Organization' : '${org['name'] ?? 'Organization'}',
        overline: org == null ? null : 'Organization',
        subtitle: purpose.isEmpty ? null : purpose,
        actions: [
          button(
            'Create organization',
            () => editOrganization(),
            disabled: busy,
            iconName: 'plus',
          ),
          if (org != null)
            button(
              'Edit organization',
              () => editOrganization(org),
              disabled: busy,
              iconName: 'pencil',
            ),
          button(
            'Refresh',
            () => bloc.send(OrganizationCommand('refresh')),
            disabled: state.busy,
            iconName: 'refresh-cw',
          ),
        ],
      ),
      if (state.error != null) notice(state.error, error: true),
      if (!state.loaded && state.error == null)
        el(
          'p',
          [text('Loading organization…')],
          attributes: {'role': 'status'},
        ),
      if (error != null) notice(error, error: true),
      if (readinessError != null) notice(readinessError, error: true),
      if (bloc.pending != null)
        el('div', [
          icon('history', classes: 'icon--sm'),
          row([
            button(
              'Retry same request',
              () => bloc.send(bloc.pending!),
              disabled: state.busy,
              small: true,
            ),
            button(
              'Dismiss request',
              () => setState(() => bloc.pending = null),
              disabled: state.busy,
              small: true,
              variant: 'ghost',
            ),
          ]),
        ], classes: 'org-retry'),
      if (org == null && state.loaded)
        card('Organization', [
          emptyState(
            'No organization yet. Create an organization to hire agents.',
            iconName: 'network',
          ),
        ], iconName: 'network'),
      if (org != null) ...[
        el('div', [
          viewTabs(
            'org-view',
            'Organization views',
            {
              'chart': ('Org chart', 'network'),
              'team': ('Team', 'users'),
              'collaborate': ('Chat & groups', 'message-square'),
              'runs': ('Runs', 'history'),
              'automation': ('Automation', 'settings'),
            },
            view,
            (value) => setState(() => view = value),
          ),
          el('div', [
            button(
              'Delegate work',
              () => delegate(org),
              disabled: busy || profiles.length < 2,
              iconName: 'send',
            ),
            button(
              'Hire an agent',
              () => hire(org),
              disabled: busy,
              primary: true,
              iconName: 'plus',
            ),
          ], classes: 'org-tabsrow__actions'),
        ], classes: 'org-tabsrow'),
        tabPanel('org-view', view, [
          if (view == 'chart')
            OrgChart(
              profiles: profiles,
              agents: connection.state.agents,
              jobs: jobs,
              groups: state.groups
                  .where(
                    (g) =>
                        g['organization_id'] == org['id'] &&
                        g['removed_at'] == null,
                  )
                  .toList(),
              onSelect: (p) => navigate('/agents/${p['id']}'),
              onSelectGroup: (g) => navigate('/groups/${g['id']}'),
              onConfigure: (p) => hire(org, p),
            ),
          if (view == 'team') teamView(org, profiles, jobs, busy),
          if (view == 'collaborate')
            CollaborationPanel(
              key: ValueKey('collaboration-${org['id']}'),
              organization: org,
              profiles: profiles,
              onOpenGroup: (id) => navigate('/groups/$id'),
            ),
          if (view == 'runs') runsView(jobs, busy),
          if (view == 'automation')
            OrganizationAutomation(
              key: ValueKey('policy-${org['id']}'),
              organizationId: '${org['id']}',
            ),
        ]),
      ],
      el('div', [
        cardDisclosure(
          'Organization settings',
          [
            el('div', [
              select(
                'Organization',
                '${org?['id'] ?? ''}',
                {
                  '': 'Select organization',
                  for (final o in organizations) '${o['id']}': '${o['name']}',
                },
                (v) => selected = v.isEmpty ? null : v,
              ),
            ], classes: 'org-settings__select'),
            if (org != null)
              el('section', [
                el('h3', [text('Shared instructions')], classes: 'overline'),
                textBlock(
                  '${org['instructions'] ?? ''}'.trim().isEmpty
                      ? 'No shared instructions.'
                      : org['instructions'],
                ),
              ], classes: 'org-settings__instructions'),
          ],
          iconName: 'settings',
          hint:
              '${organizations.length} ${organizations.length == 1 ? 'organization' : 'organizations'}',
        ),
        cardDisclosure(
          'Migration & backup',
          [
            note(
              'Exports all organizations, archived entries, models, personas, permissions, project paths, worktree preferences, reporting lines and groups. Runtime sessions, discussions, repositories, recovery snapshots and credentials need separate backups.',
            ),
            row([
              button(
                'Export configuration',
                () => transfer(false),
                disabled: transferring,
                iconName: 'download',
              ),
              button(
                'Import configuration',
                () => transfer(true),
                disabled: transferring,
                iconName: 'upload',
              ),
            ]),
          ],
          iconName: 'archive',
          hint: 'Export or import the configuration',
        ),
      ], classes: 'org-admin'),
    ], classes: 'organization-page');
  }
}
