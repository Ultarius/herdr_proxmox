import 'dart:math' as math;
import 'package:flutter/material.dart';

class ChartLayout {
  ChartLayout(List<Map<String, dynamic>> profiles) {
    final byId = {for (final p in profiles) p['id'] as String: p};
    final visited = <String>{};
    double nextLeaf = 0;
    double place(Map<String, dynamic> profile, int depth) {
      final id = profile['id'] as String;
      visited.add(id);
      final children = profiles
          .where((p) => p['manager_id'] == id && !visited.contains(p['id']))
          .toList();
      final xs = <double>[];
      for (final child in children) {
        if (!visited.contains(child['id'])) xs.add(place(child, depth + 1));
      }
      final x = xs.isEmpty ? nextLeaf++ * 220 : (xs.first + xs.last) / 2;
      positions[id] = Offset(x + 30, depth * 148 + 30);
      maxDepth = math.max(maxDepth, depth);
      return x;
    }

    for (final p in profiles.where((p) => !byId.containsKey(p['manager_id']))) {
      if (!visited.contains(p['id'])) place(p, 0);
    }
    for (final p in profiles) {
      if (!visited.contains(p['id'])) place(p, 0);
    }
    size = Size(
      math.max(420, nextLeaf * 220 + 40),
      math.max(240, (maxDepth + 1) * 148 + 30),
    );
  }
  final positions = <String, Offset>{};
  int maxDepth = 0;
  late final Size size;
}

class OrgChart extends StatefulWidget {
  const OrgChart({
    super.key,
    required this.profiles,
    required this.agents,
    required this.jobs,
    required this.onSelect,
  });
  final List<Map<String, dynamic>> profiles;
  final List<Map<String, dynamic>> agents;
  final List<Map<String, dynamic>> jobs;
  final void Function(Map<String, dynamic>) onSelect;
  @override
  State<OrgChart> createState() => _OrgChartState();
}

class _OrgChartState extends State<OrgChart> {
  final transform = TransformationController();
  String fitted = '';
  Color activity(Map<String, dynamic> profile) {
    final run = widget.jobs
        .where(
          (j) =>
              j['kind'] == 'launch' &&
              j['profile_id'] == profile['id'] &&
              j['state'] != 'released',
        )
        .firstOrNull;
    final agent = run == null
        ? null
        : widget.agents
              .where(
                (a) =>
                    a['pane_id'] == run['pane_id'] && a['name'] == run['alias'],
              )
              .firstOrNull;
    return switch (agent?['agent_status']) {
      'working' => const Color(0xff3a93ff),
      'blocked' => const Color(0xffeebc32),
      'idle' || 'done' => const Color(0xff56c592),
      _ => const Color(0xff777777),
    };
  }

  @override
  void dispose() {
    transform.dispose();
    super.dispose();
  }

  void fit(Size viewport, Size canvas) {
    final scale = math.min(
      1.0,
      math.min(viewport.width / canvas.width, viewport.height / canvas.height),
    );
    transform.value = Matrix4.identity()
      ..translateByDouble(
        (viewport.width - canvas.width * scale) / 2,
        (viewport.height - canvas.height * scale) / 2,
        0,
        1,
      )
      ..scaleByDouble(scale, scale, 1, 1);
  }

  @override
  Widget build(BuildContext context) {
    final layout = ChartLayout(widget.profiles);
    return Container(
      height: (MediaQuery.sizeOf(context).height - 240).clamp(300, 650),
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: const Color(0xff101010),
        border: Border.all(color: const Color(0xff292929)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final signature =
              '${constraints.biggest}:${widget.profiles.map((p) => '${p['id']}:${p['manager_id']}').join(',')}';
          if (signature != fitted) {
            fitted = signature;
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (mounted && fitted == signature)
                fit(constraints.biggest, layout.size);
            });
          }
          return Stack(
            children: [
              if (widget.profiles.isEmpty)
                const Center(
                  child: Text(
                    'Hire your first agent to build the org chart.',
                    style: TextStyle(color: Color(0xff777777)),
                  ),
                )
              else
                InteractiveViewer(
                  transformationController: transform,
                  constrained: false,
                  minScale: .1,
                  maxScale: 2.5,
                  boundaryMargin: const EdgeInsets.all(600),
                  child: SizedBox(
                    width: layout.size.width,
                    height: layout.size.height,
                    child: Stack(
                      children: [
                        Positioned.fill(
                          child: CustomPaint(
                            painter: _Connections(
                              layout.positions,
                              widget.profiles,
                            ),
                          ),
                        ),
                        for (final profile in widget.profiles)
                          Positioned(
                            left: layout.positions[profile['id']]!.dx,
                            top: layout.positions[profile['id']]!.dy,
                            width: 188,
                            height: 88,
                            child: Material(
                              color: const Color(0xff191919),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(9),
                                side: const BorderSide(
                                  color: Color(0xff2a2a2a),
                                ),
                              ),
                              child: InkWell(
                                borderRadius: BorderRadius.circular(9),
                                onTap: () => widget.onSelect(profile),
                                child: Padding(
                                  padding: const EdgeInsets.all(12),
                                  child: Row(
                                    children: [
                                      Container(
                                        width: 30,
                                        height: 30,
                                        decoration: const BoxDecoration(
                                          color: Color(0xff242424),
                                          shape: BoxShape.circle,
                                        ),
                                        child: Icon(
                                          profile['manager_id'] == ''
                                              ? Icons.workspace_premium_outlined
                                              : Icons.code,
                                          size: 17,
                                          color: const Color(0xffbbbbbb),
                                        ),
                                      ),
                                      const SizedBox(width: 10),
                                      Expanded(
                                        child: Column(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          mainAxisAlignment:
                                              MainAxisAlignment.center,
                                          children: [
                                            Text(
                                              '${profile['name']}',
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: const TextStyle(
                                                fontSize: 12,
                                                fontWeight: FontWeight.w700,
                                              ),
                                            ),
                                            Text(
                                              '${profile['role']}',
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: const TextStyle(
                                                fontSize: 10,
                                                color: Color(0xffaaaaaa),
                                              ),
                                            ),
                                            Row(
                                              children: [
                                                Container(
                                                  width: 5,
                                                  height: 5,
                                                  decoration: BoxDecoration(
                                                    shape: BoxShape.circle,
                                                    color: activity(profile),
                                                  ),
                                                ),
                                                const SizedBox(width: 5),
                                                Text(
                                                  '${profile['runtime']}',
                                                  style: const TextStyle(
                                                    fontSize: 9,
                                                    color: Color(0xff777777),
                                                  ),
                                                ),
                                              ],
                                            ),
                                          ],
                                        ),
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
                ),
              Positioned(
                right: 12,
                top: 12,
                child: Column(
                  children: [
                    IconButton.filledTonal(
                      tooltip: 'Zoom in',
                      visualDensity: VisualDensity.compact,
                      onPressed: () =>
                          transform.value = transform.value.clone()
                            ..scaleByDouble(1.2, 1.2, 1, 1),
                      icon: const Icon(Icons.add, size: 16),
                    ),
                    IconButton.filledTonal(
                      tooltip: 'Zoom out',
                      visualDensity: VisualDensity.compact,
                      onPressed: () =>
                          transform.value = transform.value.clone()
                            ..scaleByDouble(1 / 1.2, 1 / 1.2, 1, 1),
                      icon: const Icon(Icons.remove, size: 16),
                    ),
                    TextButton(
                      onPressed: () => fit(
                        Size(constraints.maxWidth, constraints.maxHeight),
                        layout.size,
                      ),
                      child: const Text('Fit', style: TextStyle(fontSize: 11)),
                    ),
                  ],
                ),
              ),
              const Positioned(
                left: 18,
                bottom: 14,
                child: Text(
                  'Drag to pan · Scroll to zoom · Select an agent to edit',
                  style: TextStyle(fontSize: 10, color: Color(0xff777777)),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _Connections extends CustomPainter {
  _Connections(this.positions, this.profiles);
  final Map<String, Offset> positions;
  final List<Map<String, dynamic>> profiles;
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = const Color(0xff333333)
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke;
    for (final profile in profiles) {
      final parent = positions[profile['manager_id']],
          child = positions[profile['id']];
      if (parent == null || child == null || parent.dy >= child.dy) continue;
      final start = parent + const Offset(94, 88),
          end = child + const Offset(94, 0);
      final middle = (start.dy + end.dy) / 2;
      canvas.drawPath(
        Path()
          ..moveTo(start.dx, start.dy)
          ..lineTo(start.dx, middle)
          ..lineTo(end.dx, middle)
          ..lineTo(end.dx, end.dy),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _Connections oldDelegate) => true;
}
