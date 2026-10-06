import 'package:flutter/material.dart';

String agentStatus(Map<String, dynamic> agent) =>
    '${agent['agent_status'] ?? agent['state'] ?? agent['status'] ?? 'unknown'}';
String agentName(Map<String, dynamic> agent) =>
    '${agent['display_name'] ?? agent['name'] ?? agent['agent'] ?? agent['pane_id'] ?? 'Agent'}';
String workspaceFor(Map<String, dynamic> agent) =>
    '${agent['workspace_id'] ?? '${agent['pane_id'] ?? ''}'.split(':').first}';
bool isWorking(Map<String, dynamic> agent) =>
    ['working', 'running', 'busy'].contains(agentStatus(agent));

class StatusPill extends StatelessWidget {
  const StatusPill(this.status, {super.key});
  final String status;
  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final active = ['working', 'running', 'connected'].contains(status);
    final idle = ['idle', 'done', 'waiting'].contains(status);
    final color = active
        ? (dark ? const Color(0xff78dfa5) : const Color(0xff146c40))
        : idle
        ? (dark ? const Color(0xffffd477) : const Color(0xff805900))
        : Theme.of(context).colorScheme.onSurfaceVariant;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .12),
        borderRadius: BorderRadius.circular(30),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, size: 8, color: color),
          const SizedBox(width: 6),
          Text(
            status.isEmpty
                ? 'Unknown'
                : '${status[0].toUpperCase()}${status.substring(1)}',
            style: TextStyle(
              color: color,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class OverviewSummary extends StatelessWidget {
  const OverviewSummary({
    super.key,
    required this.agents,
    required this.workspaces,
    required this.connected,
    required this.onAgents,
    required this.onWorkspaces,
    required this.onConnection,
  });
  final int agents, workspaces;
  final bool connected;
  final VoidCallback onAgents, onWorkspaces, onConnection;
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, size) {
      final mobile = size.maxWidth < 650;
      Widget item(
        String title,
        String value,
        IconData icon,
        VoidCallback action,
      ) => Card(
        child: InkWell(
          onTap: action,
          borderRadius: BorderRadius.circular(16),
          child: Padding(
            padding: EdgeInsets.all(mobile ? 16 : 22),
            child: Row(
              children: [
                CircleAvatar(
                  backgroundColor: Theme.of(
                    context,
                  ).colorScheme.surfaceContainerLow,
                  foregroundColor: Theme.of(context).colorScheme.primary,
                  child: Icon(icon),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: mobile
                      ? Text(title)
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              title,
                              style: TextStyle(
                                color: Theme.of(
                                  context,
                                ).colorScheme.onSurfaceVariant,
                              ),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              value,
                              style: Theme.of(context).textTheme.headlineSmall,
                            ),
                          ],
                        ),
                ),
                if (mobile)
                  Text(
                    value,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                const SizedBox(width: 12),
                const Icon(Icons.chevron_right, size: 20),
              ],
            ),
          ),
        ),
      );
      final cards = [
        item('Agents', '$agents', Icons.smart_toy_outlined, onAgents),
        item('Workspaces', '$workspaces', Icons.folder_outlined, onWorkspaces),
        item(
          'Connection',
          connected ? 'Connected' : 'Unavailable',
          Icons.wifi,
          onConnection,
        ),
      ];
      return mobile
          ? Column(
              children: [
                for (final card in cards)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: card,
                  ),
              ],
            )
          : Row(
              children: [
                for (var i = 0; i < cards.length; i++) ...[
                  if (i > 0) const SizedBox(width: 16),
                  Expanded(child: cards[i]),
                ],
              ],
            );
    },
  );
}

class WorkspacePanel extends StatelessWidget {
  const WorkspacePanel({
    super.key,
    required this.workspaces,
    required this.agents,
    required this.onFocus,
    required this.onRename,
    this.busy = false,
  });
  final List<Map<String, dynamic>> workspaces, agents;
  final void Function(Map<String, dynamic>) onFocus, onRename;
  final bool busy;
  @override
  Widget build(BuildContext context) => Card(
    child: Column(
      children: [
        const PanelHeading('Workspaces', Icons.folder_outlined),
        if (workspaces.isEmpty)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'No workspaces yet. Create a workspace to get started.',
            ),
          ),
        for (final workspace in workspaces) ...[
          const Divider(height: 1),
          Builder(
            builder: (context) {
              final members = agents
                  .where((a) => workspaceFor(a) == workspace['workspace_id'])
                  .toList();
              return ListTile(
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 6,
                ),
                leading: const CircleAvatar(child: Icon(Icons.folder_outlined)),
                title: Text(
                  '${workspace['label'] ?? workspace['workspace_id']}',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  '${workspace['workspace_id']} · ${members.length} ${members.length == 1 ? 'agent' : 'agents'}',
                ),
                onTap: busy ? null : () => onFocus(workspace),
                trailing: PopupMenuButton<String>(
                  tooltip: 'Workspace actions',
                  enabled: !busy,
                  onSelected: (value) => value == 'focus'
                      ? onFocus(workspace)
                      : onRename(workspace),
                  itemBuilder: (_) => const [
                    PopupMenuItem(
                      value: 'focus',
                      child: Text('Focus in Herdr'),
                    ),
                    PopupMenuItem(value: 'rename', child: Text('Rename')),
                  ],
                ),
              );
            },
          ),
        ],
      ],
    ),
  );
}

class PanelHeading extends StatelessWidget {
  const PanelHeading(this.title, this.icon, {super.key, this.action});
  final String title;
  final IconData icon;
  final Widget? action;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(18),
    child: Row(
      children: [
        Icon(icon, size: 22),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            title,
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
        ),
        if (action != null) action!,
      ],
    ),
  );
}

class AgentPanel extends StatefulWidget {
  const AgentPanel({super.key, required this.agents, required this.onManage});
  final List<Map<String, dynamic>> agents;
  final VoidCallback onManage;
  @override
  State<AgentPanel> createState() => _AgentPanelState();
}

class _AgentPanelState extends State<AgentPanel> {
  String filter = 'All';
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, size) {
      final desktop = size.maxWidth >= 650;
      final agents = widget.agents
          .where(
            (agent) =>
                filter == 'All' ||
                (filter == 'Working'
                    ? isWorking(agent)
                    : ['idle', 'done', 'waiting'].contains(agentStatus(agent))),
          )
          .toList();
      return Card(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            PanelHeading(
              'Agents',
              Icons.smart_toy_outlined,
              action: TextButton(
                onPressed: widget.onManage,
                child: const Text('Manage'),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final value in ['All', 'Working', 'Idle'])
                    ChoiceChip(
                      label: Text(value),
                      selected: filter == value,
                      onSelected: (_) => setState(() => filter = value),
                    ),
                ],
              ),
            ),
            if (desktop)
              Container(
                color: Theme.of(context).colorScheme.surfaceContainerLow,
                padding: const EdgeInsets.symmetric(
                  horizontal: 22,
                  vertical: 12,
                ),
                child: const Row(
                  children: [
                    Expanded(flex: 3, child: Text('Name')),
                    Expanded(child: Text('Workspace')),
                    SizedBox(width: 130, child: Text('Status')),
                  ],
                ),
              ),
            if (agents.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Text('No agents match this view.'),
              ),
            for (final agent in agents) ...[
              const Divider(height: 1),
              Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    CircleAvatar(
                      child: Icon(
                        agent['entity_type'] == 'group'
                            ? Icons.forum_outlined
                            : Icons.smart_toy_outlined,
                        size: 20,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 3,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            agentName(agent),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          if (!desktop)
                            Text(
                              workspaceFor(agent),
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                        ],
                      ),
                    ),
                    if (desktop) Expanded(child: Text(workspaceFor(agent))),
                    const SizedBox(width: 8),
                    SizedBox(
                      width: desktop ? 130 : null,
                      child: Align(
                        alignment: Alignment.centerLeft,
                        child: StatusPill(agentStatus(agent)),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      );
    },
  );
}
