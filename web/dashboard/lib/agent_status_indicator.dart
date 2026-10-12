import 'package:flutter/material.dart';

String agentActivity(String profileId, List<Map<String, dynamic>> agents) {
  final agent = agents.where((a) => a['profile_id'] == profileId).firstOrNull;
  return switch (agent?['agent_status'] ?? agent?['state']) {
    'idle' || 'done' => 'idle',
    'working' || 'running' || 'busy' || 'thinking' => 'working',
    'blocked' => 'attention',
    _ => 'offline',
  };
}

class AgentStatusIndicator extends StatefulWidget {
  const AgentStatusIndicator({super.key, required this.activity});
  final String activity;

  @override
  State<AgentStatusIndicator> createState() => _AgentStatusIndicatorState();
}

class _AgentStatusIndicatorState extends State<AgentStatusIndicator>
    with SingleTickerProviderStateMixin {
  late final pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  );
  bool get animated =>
      widget.activity == 'working' || widget.activity == 'attention';

  void updateAnimation() {
    if (animated && !MediaQuery.disableAnimationsOf(context)) {
      if (!pulse.isAnimating) pulse.repeat(reverse: true);
    } else {
      pulse.stop();
      pulse.value = 0;
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    updateAnimation();
  }

  @override
  void didUpdateWidget(AgentStatusIndicator oldWidget) {
    super.didUpdateWidget(oldWidget);
    updateAnimation();
  }

  @override
  void dispose() {
    pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = switch (widget.activity) {
      'idle' => const Color(0xff56c592),
      'working' => const Color(0xff3a93ff),
      'attention' => const Color(0xffffa14d),
      _ => Theme.of(context).colorScheme.outline,
    };
    final label = switch (widget.activity) {
      'attention' => 'Attention required',
      'working' => 'Working',
      'idle' => 'Idle',
      _ => 'No live session',
    };
    return Tooltip(
      message: label,
      child: Semantics(
        label: label,
        child: SizedBox(
          width: 20,
          height: 20,
          child: AnimatedBuilder(
            animation: pulse,
            builder: (context, _) => Center(
              child: Container(
                width: 9,
                height: 9,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: color,
                  boxShadow: animated
                      ? [
                          BoxShadow(
                            color: color.withValues(
                              alpha: .15 + pulse.value * .2,
                            ),
                            spreadRadius: 1 + pulse.value * 4,
                            blurRadius: 1 + pulse.value * 3,
                          ),
                        ]
                      : null,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
