import 'permission_options.dart';
import 'dart:math';
import 'package:flutter/services.dart';
import 'package:juice/juice.dart';
import 'dashboard_bloc.dart';
import 'artifact_download.dart';

class CollaborationPanel extends StatefulWidget {
  const CollaborationPanel({
    super.key,
    required this.organization,
    required this.profiles,
    this.onOpenGroup,
  });
  final Map<String, dynamic> organization;
  final List<Map<String, dynamic>> profiles;
  final void Function(String)? onOpenGroup;
  @override
  State<CollaborationPanel> createState() => _CollaborationPanelState();
}

class _CollaborationPanelState extends State<CollaborationPanel> {
  DashboardBloc get connection => BlocScope.get<DashboardBloc>();
  final prompt = TextEditingController();
  List<Map<String, dynamic>> groups = [];
  List<Map<String, dynamic>> jobs = [];
  String? profile;
  String? error;
  String output = '';
  String status = '';
  String draft = '';
  String? draftJob;
  bool busy = false;
  bool polling = false;
  String? pendingAction;
  Map<String, dynamic>? pendingBody;
  Timer? timer;

  @override
  void initState() {
    super.initState();
    refresh();
    timer = Timer.periodic(const Duration(seconds: 1), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    prompt.dispose();
    super.dispose();
  }

  Map<String, dynamic> body(Map<String, dynamic> values) => {
    ...values,
    'organization_id': widget.organization['id'],
    'request_id':
        '${DateTime.now().microsecondsSinceEpoch}_${Random.secure().nextInt(0x7fffffff)}',
  };

  Future<void> refresh() async {
    if (polling || !connection.state.connected) return;
    polling = true;
    final epoch = connection.generation;
    final selected = profile;
    try {
      final data = await connection.request('organizations');
      if (!mounted || epoch != connection.generation) return;
      setState(() {
        groups = List<Map<String, dynamic>>.from(data['groups'] ?? [])
            .where((g) => g['organization_id'] == widget.organization['id'])
            .toList();
        jobs = List<Map<String, dynamic>>.from(data['jobs'])
            .where((j) => j['organization_id'] == widget.organization['id'])
            .toList();
      });
      if (selected != null) {
        final result = await connection.request(
          'organizations/inspect',
          body({'profile_id': selected}),
        );
        if (mounted && epoch == connection.generation && selected == profile) {
          setState(() {
            output = result['output'] as String;
            status = result['status'] as String;
            draft = result['reply_draft'] as String? ?? '';
            draftJob = result['reply_job_id'] as String?;
            if (pendingBody == null) error = null;
          });
        }
      }
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = exception.toString());
    } finally {
      polling = false;
    }
  }

  Future<void> submit(
    String action,
    Map<String, dynamic> values, {
    bool retry = false,
  }) async {
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    if (!retry) {
      pendingAction = action;
      pendingBody = body(values);
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await connection.request('organizations/$pendingAction', pendingBody);
      if (!mounted || epoch != connection.generation) return;
      pendingAction = null;
      pendingBody = null;
      if (action == 'chat') prompt.clear();
      await refresh();
    } catch (exception) {
      if (mounted && epoch == connection.generation)
        setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> editGroup([Map<String, dynamic>? group]) async {
    final values = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) =>
          DiscussionGroupForm(profiles: widget.profiles, group: group),
    );
    if (values != null && mounted) await submit('group', values);
  }

  bool occupied(String id) => jobs.any(
    (j) =>
        ['queued', 'running'].contains(j['state']) &&
        ((j['participants'] as List? ?? [j['profile_id']]).contains(id)),
  );

  Future<void> export(Map<String, dynamic> job, {bool download = false}) async {
    try {
      if (download) {
        await downloadArtifact(
          job['result'] as String,
          'action-plan-${job['id']}.md',
        );
      } else {
        await Clipboard.setData(ClipboardData(text: job['result'] as String));
      }
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              download ? 'Markdown download started.' : 'Markdown copied.',
            ),
          ),
        );
    } catch (exception) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Export failed. Select the artifact text to copy it manually.',
            ),
          ),
        );
    }
  }

  Widget record(Map<String, dynamic> job) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${job['kind'] == 'chat' ? 'Message' : job['group']?['name'] ?? 'Discussion'} · ${job['state']}',
          ),
          if (job['prompt'] != null) SelectableText('You: ${job['prompt']}'),
          if (job['progress'] != null) Text('${job['progress']}'),
          if ((job['error'] as String? ?? '').isNotEmpty)
            Text('${job['error']}'),
          if ((job['result'] as String? ?? '').isNotEmpty) ...[
            Text(
              job['kind'] == 'chat'
                  ? (job['result_format'] == 'markdown'
                        ? 'Agent reply'
                        : 'Earlier terminal snapshot')
                  : 'Action artifact · Markdown',
            ),
            if (job['kind'] == 'chat' && job['result_format'] != 'markdown')
              ExpansionTile(
                title: const Text('View earlier terminal output'),
                children: [SelectableText(job['result'] as String)],
              )
            else
              ExpansionTile(
                key: ValueKey('reply-${job['id']}'),
                initiallyExpanded: false,
                title: Text(
                  job['kind'] == 'chat' ? 'View reply' : 'View artifact',
                ),
                children: [
                  SizedBox(
                    height: 280,
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.all(16),
                      child: SelectableText(job['result'] as String),
                    ),
                  ),
                ],
              ),
            TextButton(
              onPressed: () => export(job),
              child: const Text('Copy Markdown'),
            ),
            if (job['kind'] == 'discussion')
              TextButton(
                onPressed: () => export(job, download: true),
                child: const Text('Download Markdown'),
              ),
          ],
          if ((job['contributions'] as List? ?? []).isNotEmpty)
            ExpansionTile(
              title: const Text('Discussion contributions'),
              children: [
                for (final part in job['contributions'] as List)
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('${part['name']} · Round ${part['round']}'),
                        SelectableText(part['content'] as String),
                      ],
                    ),
                  ),
              ],
            ),
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      const SizedBox(height: 24),
      Text('Chat with an agent', style: Theme.of(context).textTheme.titleLarge),
      const Text(
        'Launch the agent first. New replies contain only the agent’s answer. Live terminal output is available separately for troubleshooting.',
      ),
      DropdownButtonFormField<String>(
        initialValue: profile,
        decoration: const InputDecoration(labelText: 'Agent'),
        items: [
          for (final p in widget.profiles)
            DropdownMenuItem(
              value: p['id'] as String,
              child: Text(p['name'] as String),
            ),
        ],
        onChanged: busy
            ? null
            : (value) {
                setState(() {
                  profile = value;
                  output = '';
                  status = '';
                  draft = '';
                  draftJob = null;
                  error = null;
                });
                refresh();
              },
      ),
      TextField(
        controller: prompt,
        maxLines: 4,
        maxLength: 8000,
        decoration: const InputDecoration(
          labelText: 'Ask a question or give instructions',
        ),
      ),
      FilledButton(
        onPressed:
            busy || pendingBody != null || profile == null || occupied(profile!)
            ? null
            : () {
                if (prompt.text.trim().isNotEmpty)
                  submit('chat', {
                    'profile_id': profile,
                    'prompt': prompt.text.trim(),
                  });
              },
        child: const Text('Send prompt'),
      ),
      if (status.isNotEmpty) Text('Agent status: $status'),
      if (status == 'blocked')
        const Text(
          'The agent needs input. Read its terminal output, then use the controls below. Approval applies to the option currently selected in the terminal.',
        ),
      if (draftJob != null)
        ExpansionTile(
          key: ValueKey('draft-$draftJob'),
          initiallyExpanded: false,
          title: const Text('Reply in progress'),
          subtitle: Text(
            draft.isEmpty
                ? 'Waiting for the agent to write its answer…'
                : 'Live preview · updates every second',
          ),
          children: [
            SizedBox(
              height: 220,
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: SelectableText(
                  draft.isEmpty
                      ? 'The agent is working. Expand live terminal output to see its activity.'
                      : draft,
                ),
              ),
            ),
          ],
        ),
      if (output.isNotEmpty)
        ExpansionTile(
          key: ValueKey('terminal-$profile'),
          initiallyExpanded: false,
          title: const Text('Live terminal output'),
          children: [
            SizedBox(
              height: 240,
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: SelectableText(output),
              ),
            ),
          ],
        ),
      if (profile != null && output.isNotEmpty)
        Wrap(
          spacing: 8,
          children: [
            for (final entry in const {
              'up': 'Up',
              'down': 'Down',
              'tab': 'Tab',
              'enter': 'Enter',
              'esc': 'Escape',
              'ctrl+c': 'Interrupt',
            }.entries)
              OutlinedButton(
                onPressed: busy || pendingBody != null || occupied(profile!)
                    ? null
                    : () => submit('input', {
                        'profile_id': profile,
                        'key': entry.key,
                      }),
                child: Text(entry.value),
              ),
          ],
        ),
      for (final job in jobs.reversed.where(
        (j) => j['kind'] == 'chat' && j['profile_id'] == profile,
      ))
        record(job),
      const SizedBox(height: 24),
      Text('Discussion groups', style: Theme.of(context).textTheme.titleLarge),
      const Text(
        'Choose 2–6 agents and describe the desired outcome. A dedicated group facilitator coordinates members and saves the resulting artifact. Launch every member before starting.',
      ),
      TextButton(
        onPressed: busy || pendingBody != null ? null : () => editGroup(),
        child: const Text('Create group'),
      ),
      for (final group in groups)
        SizedBox(
          width: double.infinity,
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    group['name'] as String,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    (group['description'] as String)
                        .replaceAll(RegExp(r'\s+'), ' ')
                        .trim(),
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'Members: ${(group['members'] as List).map((id) => widget.profiles.where((p) => p['id'] == id).firstOrNull?['name'] ?? id).join(', ')}',
                  ),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      if (widget.onOpenGroup != null)
                        TextButton(
                          onPressed: () =>
                              widget.onOpenGroup!(group['id'] as String),
                          child: const Text('Open group'),
                        ),
                      TextButton(
                        onPressed: busy || pendingBody != null
                            ? null
                            : () => editGroup(group),
                        child: const Text('Edit group'),
                      ),
                      FilledButton(
                        onPressed:
                            busy ||
                                pendingBody != null ||
                                (group['members'] as List).any(
                                  (id) => occupied(id as String),
                                )
                            ? null
                            : () =>
                                  submit('discuss', {'group_id': group['id']}),
                        child: const Text('Start discussion'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      if (error != null) Text(error!),
      if (pendingBody != null && !busy)
        Wrap(
          spacing: 12,
          children: [
            TextButton(
              onPressed: () => submit(pendingAction!, const {}, retry: true),
              child: const Text('Retry same request'),
            ),
            TextButton(
              onPressed: () => setState(() {
                pendingAction = null;
                pendingBody = null;
                error = null;
              }),
              child: const Text('Dismiss request'),
            ),
          ],
        ),
      for (final job in jobs.reversed.where(
        (j) => j['kind'] == 'discussion' && widget.onOpenGroup == null,
      ))
        record(job),
    ],
  );
}

class DiscussionGroupForm extends StatefulWidget {
  const DiscussionGroupForm({super.key, required this.profiles, this.group});
  final List<Map<String, dynamic>> profiles;
  final Map<String, dynamic>? group;
  @override
  State<DiscussionGroupForm> createState() => _DiscussionGroupFormState();
}

class _DiscussionGroupFormState extends State<DiscussionGroupForm> {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.group?['name']);
  late final description = TextEditingController(
    text: widget.group?['description'],
  );
  late final members = List<String>.from(widget.group?['members'] ?? []);
  late bool readOnly = widget.group?['read_only'] ?? true;
  late bool useWorktree = widget.group?['use_worktree'] ?? true;
  late String permissions = widget.group?['permission_mode'] ?? 'default';
  late final accessiblePaths = TextEditingController(
    text: (widget.group?['accessible_paths'] as List? ?? []).join('\n'),
  );
  String? error;
  @override
  void dispose() {
    name.dispose();
    description.dispose();
    accessiblePaths.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    insetPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
    constraints: const BoxConstraints(maxWidth: 960),
    title: Text(
      widget.group == null
          ? 'Create discussion group'
          : 'Edit discussion group',
    ),
    content: SizedBox(
      width: 900,
      height: MediaQuery.sizeOf(context).height * 0.68,
      child: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: name,
                maxLength: 120,
                decoration: const InputDecoration(labelText: 'Group name'),
                validator: (value) => value == null || value.trim().isEmpty
                    ? 'Enter a name'
                    : null,
              ),
              TextFormField(
                controller: description,
                maxLength: 8000,
                minLines: 8,
                maxLines: 14,
                textAlignVertical: TextAlignVertical.top,
                decoration: const InputDecoration(
                  labelText: 'Topic, desired outcome and constraints',
                  alignLabelWithHint: true,
                  border: OutlineInputBorder(),
                ),
                validator: (value) => value == null || value.trim().isEmpty
                    ? 'Describe the discussion'
                    : null,
              ),
              SwitchListTile(
                title: const Text('Read-only discussion'),
                subtitle: const Text(
                  'Default: advise and create artifacts. Members are instructed not to change projects. This is a discussion rule, not a sandbox.',
                ),
                value: readOnly,
                onChanged: (v) => setState(() => readOnly = v),
              ),
              PermissionOptions(
                value: permissions,
                paths: accessiblePaths,
                onChanged: (v) => setState(() => permissions = v),
              ),
              SwitchListTile(
                title: const Text('Use a Git worktree'),
                subtitle: const Text(
                  'The facilitator gets a separate checkout from committed HEAD on its next launch. Non-Git folders use the selected directory. Members keep their own settings.',
                ),
                value: useWorktree,
                onChanged: (v) => setState(() => useWorktree = v),
              ),
              const Text(
                'Group permissions apply to the group agent on its next launch. Member agents keep their own permission settings. Non-default policies require OpenCode members.',
              ),
              for (final profile in widget.profiles.where(
                (p) => p['group_id'] == null,
              ))
                CheckboxListTile(
                  title: Text('${profile['name']} · ${profile['role']}'),
                  value: members.contains(profile['id']),
                  onChanged: (checked) => setState(() {
                    if (checked == true) {
                      members.add(profile['id'] as String);
                    } else {
                      members.remove(profile['id']);
                    }
                  }),
                ),
              if (error != null) Text(error!),
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
          if (!form.currentState!.validate()) return;
          if (members.length < 2 || members.length > 6) {
            setState(() => error = 'Choose 2–6 agents');
            return;
          }
          Navigator.pop(context, {
            if (widget.group != null) 'id': widget.group!['id'],
            'name': name.text.trim(),
            'description': description.text.trim(),
            'read_only': readOnly,
            'use_worktree': useWorktree,
            'permission_mode': permissions,
            'accessible_paths': PermissionOptions.parsePaths(
              accessiblePaths.text,
            ),
            'members': members,
          });
        },
        child: const Text('Save group'),
      ),
    ],
  );
}
