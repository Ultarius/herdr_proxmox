import 'package:flutter/material.dart';

/// Real parent edges for the bounded captured history; no implied publication.
class TaskTimeline extends StatelessWidget {
  const TaskTimeline({super.key, required this.task});
  final Map task;
  @override
  Widget build(BuildContext context) {
    final nodes = <Map>[
      for (final node in task['commit_graph'] as List? ?? [])
        if (node is Map) node,
    ];
    final head = '${task['head_sha'] ?? ''}';
    final graphMissing = head.isNotEmpty && !nodes.any((n) => n['sha'] == head);
    if (graphMissing) nodes.insert(0, {'sha': head, 'parents': []});
    final base = '${task['base_sha'] ?? ''}';
    if (base.isNotEmpty && !nodes.any((n) => n['sha'] == base))
      nodes.add({'sha': base, 'parents': []});
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
    final ordered = nodes.reversed.toList();
    final positions = <String, Offset>{};
    for (var i = 0; i < ordered.length; i++) {
      positions['${ordered[i]['sha']}'] = Offset(
        50 + i * 130,
        32 + (laneBySha['${ordered[i]['sha']}'] ?? 0) * 32,
      );
    }
    final height = 105.0 + lanes.length * 32;
    final builds = task['builds'] as Map? ?? {};
    final colors = <String, Color>{};
    for (final node in ordered) {
      final build = builds[node['sha']] as Map?;
      colors['${node['sha']}'] =
          build?['state'] == 'failed' || build?['state'] == 'error'
          ? Theme.of(context).colorScheme.error
          : build?['state'] == 'complete' &&
                build?['required_checks_verified'] == true
          ? Colors.green
          : Theme.of(context).colorScheme.primary;
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Git timeline · recorded commit parents'),
        if (graphMissing)
          const Text('Capture the candidate again to load its parent edges.'),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: SizedBox(
            width: (ordered.length * 130 + 40).toDouble(),
            height: height,
            child: Stack(
              children: [
                Positioned.fill(
                  child: CustomPaint(
                    painter: _Edges(
                      ordered,
                      positions,
                      Theme.of(context).colorScheme.outline,
                    ),
                  ),
                ),
                for (final node in ordered)
                  Positioned(
                    left: positions['${node['sha']}']!.dx - 38,
                    top: positions['${node['sha']}']!.dy - 12,
                    child: Tooltip(
                      message:
                          '${node['sha']}\n${node['sha'] == base
                              ? 'Recorded base'
                              : node['sha'] == task['head_sha']
                              ? 'Current candidate'
                              : 'Ancestor'}',
                      child: Column(
                        children: [
                          Icon(
                            Icons.circle_outlined,
                            color: colors['${node['sha']}'],
                            size: 24,
                          ),
                          Text(
                            '${node['sha']}'.substring(
                              0,
                              '${node['sha']}'.length.clamp(0, 8),
                            ),
                          ),
                          if (node['sha'] == base) const Text('Base'),
                          if (node['sha'] == task['head_sha'])
                            const Text('Candidate'),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
        const Text(
          'Green: verified build · red: failed build · other nodes: no verified pass. Up to 12 commits; absent parent edges are outside this view.',
        ),
        Text(
          'Completion receipt: ${task['completion_receipt'] == null ? 'not verified' : 'verified for ${task['completion_receipt']['commit']}'}',
        ),
        if (task['already_upstream'] == true)
          const Text(
            'Candidate tree matches the last fetched configured base. No additional file changes need a PR; this is not deployment verification.',
          ),
      ],
    );
  }
}

class _Edges extends CustomPainter {
  _Edges(this.nodes, this.positions, this.color);
  final List<Map> nodes;
  final Map<String, Offset> positions;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    for (final node in nodes) {
      final child = positions['${node['sha']}']!;
      for (final parent in node['parents'] as List? ?? []) {
        final start = positions['$parent'];
        if (start == null) continue;
        final path = Path()
          ..moveTo(start.dx, start.dy)
          ..cubicTo(
            (start.dx + child.dx) / 2,
            start.dy,
            (start.dx + child.dx) / 2,
            child.dy,
            child.dx,
            child.dy,
          );
        canvas.drawPath(path, paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _Edges old) => true;
}
