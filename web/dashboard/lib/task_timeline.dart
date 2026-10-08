import 'package:flutter/material.dart';

/// A read-only vertical Git graph. Build evidence belongs to its exact commit.
class TaskTimeline extends StatelessWidget {
  const TaskTimeline({
    super.key,
    required this.task,
    this.onSelect,
    this.onRefresh,
  });
  final Map task;
  final ValueChanged<String>? onSelect;
  final VoidCallback? onRefresh;

  static String shortSha(Map node) {
    final sha = '${node['sha']}';
    return sha.substring(0, sha.length.clamp(0, 8));
  }

  @override
  Widget build(BuildContext context) {
    final nodes = [
      for (final n in task['commit_graph'] as List? ?? [])
        if (n is Map) n,
    ];
    if (nodes.isEmpty || !nodes.any((n) => n['sha'] == task['head_sha'])) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Commit history unavailable'),
          Text(
            '${task['history_error'] ?? 'Refresh to load recorded commit history. Viewing history does not capture a new candidate.'}',
          ),
          if (onRefresh != null)
            TextButton.icon(
              onPressed: onRefresh,
              icon: const Icon(Icons.refresh),
              label: const Text('Refresh history'),
            ),
        ],
      );
    }
    final lanes = <String>[];
    final laneBySha = <String, int>{};
    for (final node in nodes) {
      final sha = '${node['sha']}';
      var lane = lanes.indexOf(sha);
      if (lane < 0) {
        lane = lanes.length;
        lanes.add(sha);
      }
      laneBySha[sha] = lane;
      final parents = (node['parents'] as List? ?? [])
          .map((p) => '$p')
          .toList();
      if (parents.isNotEmpty) lanes[lane] = parents.first;
      for (final parent in parents.skip(1)) {
        if (!lanes.contains(parent)) lanes.add(parent);
      }
    }
    final positions = <String, Offset>{};
    // Bound the lane gutter so a merge-heavy history cannot squeeze the text.
    final gutter = (lanes.length.clamp(1, 6) * 16 + 16).toDouble();
    for (var i = 0; i < nodes.length; i++) {
      positions['${nodes[i]['sha']}'] = Offset(
        12 + (laneBySha['${nodes[i]['sha']}'] ?? 0).clamp(0, 5) * 16,
        i * 124 + 22,
      );
    }
    final builds = task['builds'] as Map? ?? {};
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Recent commits \u00b7 newest first'),
        const SizedBox(height: 8),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: gutter,
              height: nodes.length * 124.0,
              child: CustomPaint(
                painter: _GitEdges(nodes, positions, scheme.primary),
              ),
            ),
            Expanded(
              child: Column(
                children: [
                  for (final node in nodes)
                    SizedBox(
                      height: 124,
                      child: InkWell(
                        onTap: onSelect == null
                            ? null
                            : () => onSelect!('${node['sha']}'),
                        child: Padding(
                          padding: const EdgeInsets.only(
                            left: 8,
                            right: 4,
                            bottom: 8,
                          ),
                          child: Align(
                            alignment: Alignment.topLeft,
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  '${node['subject'] ?? 'Commit'}',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: Theme.of(context).textTheme.titleSmall,
                                ),
                                Tooltip(
                                  message: '${node['sha']}',
                                  child: Text(
                                    '${node['author'] ?? 'Unknown author'} \u00b7 ${shortSha(node)}',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ),
                                if ('${node['refs'] ?? ''}'.isNotEmpty)
                                  Text(
                                    '${node['refs']}',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: Theme.of(context).textTheme.bodySmall,
                                  ),
                                Wrap(
                                  spacing: 8,
                                  children: [
                                    if (node['sha'] == task['base_sha'])
                                      const Text('Base'),
                                    if (node['sha'] == task['head_sha'])
                                      const Text('Candidate'),
                                    if (builds[node['sha']] is Map)
                                      _BuildBadge(
                                        evidence: builds[node['sha']],
                                      ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
        const Text(
          'Only recorded parent edges are shown. Older parents can be outside this bounded history.',
        ),
        if (task['already_upstream'] == true)
          const Text(
            'Candidate files match the last fetched base. Deployment is separate.',
          ),
      ],
    );
  }
}

class _BuildBadge extends StatelessWidget {
  const _BuildBadge({required this.evidence});
  final Map evidence;
  @override
  Widget build(BuildContext context) {
    final verified =
        evidence['state'] == 'complete' &&
        evidence['required_checks_verified'] == true;
    final failed = ['failed', 'error'].contains(evidence['state']);
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Text(
      verified
          ? 'Checks verified'
          : failed
          ? 'Checks failed'
          : 'Checks not verified',
      style: TextStyle(
        color: verified
            ? (dark ? Colors.green.shade400 : Colors.green.shade800)
            : failed
            ? Theme.of(context).colorScheme.error
            : null,
      ),
    );
  }
}

class _GitEdges extends CustomPainter {
  _GitEdges(this.nodes, this.positions, this.color);
  final List<Map> nodes;
  final Map<String, Offset> positions;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final pen = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    for (final node in nodes) {
      final start = positions['${node['sha']}']!;
      for (final parent in node['parents'] as List? ?? []) {
        final end = positions['$parent'];
        if (end == null) continue;
        canvas.drawPath(
          Path()
            ..moveTo(start.dx, start.dy)
            ..cubicTo(
              start.dx,
              (start.dy + end.dy) / 2,
              end.dx,
              (start.dy + end.dy) / 2,
              end.dx,
              end.dy,
            ),
          pen,
        );
      }
    }
    for (final position in positions.values) {
      canvas.drawCircle(position, 5, pen);
    }
  }

  @override
  bool shouldRepaint(covariant _GitEdges old) =>
      old.nodes != nodes || old.color != color;
}
