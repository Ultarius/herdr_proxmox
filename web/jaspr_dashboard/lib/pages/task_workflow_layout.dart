part of 'task_workflow.dart';

// Graph sizes (Phase 2 workflow viewer): compact 184 × 96 cards (104 with
// lanes off, when the foot also holds the assignee tile), 32px column gaps,
// a 120px lane header column (48px on phones) and 12px stage padding.
const _nodeW = 184.0;
const _nodeH = 96.0;
const _nodeHTile = 104.0;
const _releaseH = 136.0;
const _releaseGap = 16.0;
const _colGap = 32.0;
const _rowGap = 8.0;
const _lanePad = 8.0;
const _laneHead = 120.0;
const _laneHeadCompact = 48.0;
const _pad = 12.0;
const _arrow = 6.0;
const _handle = 5.0; // connector ring: 8px, centered on the card border
const _bend = 13.0; // where a bend turns inside a column gap
const _stagger = 4.0; // bends that share a gap spread by up to this much
const _radius = 5.0;

class _Link {
  const _Link(
    this.from,
    this.to,
    this.kind, {
    this.state = '',
    this.reason = '',
  });
  final String from, to, kind;
  final String state, reason;
}

class _Box {
  const _Box(
    this.id,
    this.agent,
    this.column,
    this.x,
    this.y, {
    this.kind = 'task',
  });
  final String id, agent;
  final String kind;
  final int column;
  final double x, y;
}

class _Lane {
  const _Lane(this.agent, this.y, this.height);
  final String agent;
  final double y, height;
}

/// One routed connector: the line and its arrowhead.
class _Route {
  const _Route(this.line, this.head);
  final String line, head;
}

extension _Layout on _TaskWorkflowState {
  double get nodeH => lanes ? _nodeH : _nodeHTile;
  double boxHeight(_Box box) => box.kind == 'release' ? _releaseH : nodeH;

  /// Recorded links only (follow-up provenance and execution waits), depth
  /// columns, then lanes (one band per agent) or compact layering.
  void layoutGraph(List<Map<String, dynamic>> tasks) {
    final ids = tasks.map(_id).where((id) => id.isNotEmpty).toSet();
    final recorded = <String, _Link>{};
    void add(String from, String to, String kind) {
      if (from == to || !ids.contains(from) || !ids.contains(to)) return;
      recorded['$from:$to:$kind'] = _Link(from, to, kind);
    }

    for (final task in tasks) {
      final source = (task['source'] as Map?)?['task_id'];
      if (source != null) add('$source', _id(task), 'follow-up');
      for (final followUp
          in (task['follow_up_tasks'] as Map? ?? const {}).values) {
        add(_id(task), '$followUp', 'follow-up');
      }
      final blocking = (task['blocking_execution'] as Map?)?['task_id'];
      if (blocking != null) add('$blocking', _id(task), 'execution-wait');
    }
    links = recorded.values.toList();
    final depths = <String, int>{};
    int depth(String id, Set<String> visited) {
      if (depths.containsKey(id)) return depths[id]!;
      if (!visited.add(id)) return 0;
      var result = 0;
      for (final link in links.where((link) => link.to == id)) {
        if (!visited.contains(link.from))
          result = math.max(result, depth(link.from, {...visited}) + 1);
      }
      return depths[id] = result;
    }

    for (final id in ids) {
      depth(id, {});
    }
    final h = nodeH;
    final head = lanes ? (compact ? _laneHeadCompact : _laneHead) : 0.0;
    double colX(int column) => _pad + head + column * (_nodeW + _colGap);
    final maximum = depths.values.fold(0, math.max);
    final order = <String, int>{
      for (var i = 0; i < tasks.length; i++) _id(tasks[i]): i,
    };
    final centers = <String, double>{};
    // Barycenter: the average center of the already placed linked cards.
    double? pull(String id) {
      final ys = [
        for (final link in links)
          if (link.to == id && centers.containsKey(link.from))
            centers[link.from]!
          else if (link.from == id && centers.containsKey(link.to))
            centers[link.to]!,
      ];
      return ys.isEmpty ? null : ys.reduce((a, b) => a + b) / ys.length;
    }

    int byPull(Map a, Map b) {
      final result = (pull(_id(a)) ?? double.infinity).compareTo(
        pull(_id(b)) ?? double.infinity,
      );
      return result != 0 ? result : order[_id(a)]!.compareTo(order[_id(b)]!);
    }

    final next = <_Box>[], nextBands = <_Lane>[];
    var bottom = 0.0;
    if (lanes) {
      for (final owner in tasks.map(_agent).toSet()) {
        var tallest = h;
        for (var column = 0; column <= maximum; column++) {
          final list = [
            for (final task in tasks)
              if (_agent(task) == owner && (depths[_id(task)] ?? 0) == column)
                task,
          ]..sort(byPull);
          for (var i = 0; i < list.length; i++) {
            final y = bottom + _lanePad + i * (h + _rowGap);
            next.add(_Box(_id(list[i]), owner, column, colX(column), y));
            centers[_id(list[i])] = y + h / 2;
          }
          if (list.isNotEmpty)
            tallest = math.max(
              tallest,
              list.length * h + (list.length - 1) * _rowGap,
            );
        }
        final height = tallest + 2 * _lanePad;
        nextBands.add(_Lane(owner, bottom, height));
        bottom += height;
      }
    } else {
      for (var column = 0; column <= maximum; column++) {
        final list = [
          for (final task in tasks)
            if ((depths[_id(task)] ?? 0) == column) task,
        ]..sort(byPull);
        var floor = _pad;
        for (final task in list) {
          final want = pull(_id(task));
          final y = math.max(floor, want == null ? floor : want - h / 2);
          next.add(_Box(_id(task), _agent(task), column, colX(column), y));
          centers[_id(task)] = y + h / 2;
          floor = y + h + _rowGap;
          bottom = math.max(bottom, y + h + _pad);
        }
      }
    }
    // Membership is not a dependency: release nodes never enter depth recursion.
    final releases = visibleReleases.toList()
      ..sort((a, b) => '${a['id']}'.compareTo('${b['id']}'));
    final taskY = {for (final box in next) box.id: box.y};
    var releaseFloor = _pad;
    for (final release in releases) {
      final id = 'release:${release['id']}';
      final members = releaseMembers(release);
      final ys = [
        for (final member in members)
          if (taskY.containsKey(member['task_id'])) taskY[member['task_id']]!,
      ];
      final desired = ys.isEmpty
          ? _pad
          : ys.reduce((a, b) => a + b) / ys.length;
      final y = math.max(releaseFloor, desired);
      next.add(
        _Box(id, '', maximum + 1, colX(maximum + 1), y, kind: 'release'),
      );
      releaseFloor = y + _releaseH + _releaseGap;
      bottom = math.max(bottom, releaseFloor);
      for (final member in members) {
        if (ids.contains(member['task_id'])) {
          links.add(
            _Link(
              '${member['task_id']}',
              id,
              'release-membership',
              state: '${member['state'] ?? 'pending'}',
              reason: '${member['reason'] ?? ''}',
            ),
          );
        }
      }
    }
    boxes = next;
    bands = nextBands;
    graphWidth = next.isEmpty
        ? 0
        : colX(maximum + (releases.isEmpty ? 0 : 1)) + _nodeW + _pad;
    graphHeight = next.isEmpty ? 0 : bottom;
  }

  /// Orthogonal connectors with rounded bends. Bends turn inside column gaps;
  /// a link that skips columns runs along a channel clear of every card in the
  /// skipped columns, so it never crosses a card. Bends sharing a gap stagger.
  Map<_Link, _Route> routeLinks() {
    final at = {for (final box in boxes) box.id: box};
    final h = nodeH;
    double offset(List<_Link> group, _Link link, double Function(_Link) y) {
      if (group.length < 2) return 0;
      final sorted = group.toList()..sort((a, b) => y(a).compareTo(y(b)));
      final step = math.min(_stagger, 2 * _stagger / (group.length - 1));
      return (sorted.indexOf(link) - (group.length - 1) / 2) * step;
    }

    final leaving = <int, List<_Link>>{}, entering = <int, List<_Link>>{};
    for (final link in links) {
      leaving.putIfAbsent(at[link.from]!.column, () => []).add(link);
      entering.putIfAbsent(at[link.to]!.column, () => []).add(link);
    }
    double sourceY(_Link link) => at[link.from]!.y;
    double targetY(_Link link) => at[link.to]!.y;
    final routes = <_Link, _Route>{};
    for (final link in links) {
      final from = at[link.from]!, to = at[link.to]!;
      final sx = from.x + _nodeW + _handle, sy = from.y + boxHeight(from) / 2;
      final tip = to.x - _handle, ty = to.y + boxHeight(to) / 2;
      final end = tip - _arrow + 1;
      final first =
          from.x +
          _nodeW +
          _bend +
          offset(leaving[from.column]!, link, sourceY);
      final points = <(double, double)>[(sx, sy)];
      if (to.column == from.column + 1) {
        points.addAll([(first, sy), (first, ty)]);
      } else {
        final second =
            to.x -
            _colGap +
            _bend +
            offset(entering[to.column]!, link, targetY);
        final channel = to.column > from.column
            ? _channel(from.column, to.column, sy, ty, h)
            : graphHeight - _lanePad / 2;
        points.addAll([
          (first, sy),
          (first, channel),
          (second, channel),
          (second, ty),
        ]);
      }
      points.add((end, ty));
      routes[link] = _Route(
        _path(points),
        'M${_n(tip)} ${_n(ty)}L${_n(tip - _arrow)} ${_n(ty - _arrow * .6)}'
        'L${_n(tip - _arrow)} ${_n(ty + _arrow * .6)}Z',
      );
    }
    return routes;
  }

  /// The cheapest horizontal channel between [sy] and [ty] that is clear (by
  /// half a row gap) of every card in the columns strictly between.
  double _channel(
    int fromColumn,
    int toColumn,
    double sy,
    double ty,
    double h,
  ) {
    final blocked = [
      for (final box in boxes)
        if (box.column > fromColumn && box.column < toColumn)
          (box.y - _rowGap / 2, box.y + h + _rowGap / 2),
    ];
    bool clear(double y) =>
        blocked.every((span) => y <= span.$1 || y >= span.$2);
    final candidates = [
      sy,
      ty,
      for (final span in blocked) ...[span.$1, span.$2],
    ].where(clear).toList();
    if (candidates.isEmpty) return graphHeight - _lanePad / 2;
    double cost(double y) => (y - sy).abs() + (y - ty).abs();
    return candidates.reduce((a, b) => cost(a) <= cost(b) ? a : b);
  }
}

String _n(double value) => value.toStringAsFixed(1);

/// Polyline with rounded corners; repeated and collinear points are dropped.
String _path(List<(double, double)> raw) {
  final points = <(double, double)>[];
  for (final point in raw) {
    if (points.isNotEmpty &&
        (points.last.$1 - point.$1).abs() < .5 &&
        (points.last.$2 - point.$2).abs() < .5)
      continue;
    if (points.length >= 2) {
      final a = points[points.length - 2], b = points.last;
      if (((a.$1 - b.$1).abs() < .5 && (b.$1 - point.$1).abs() < .5) ||
          ((a.$2 - b.$2).abs() < .5 && (b.$2 - point.$2).abs() < .5))
        points.removeLast();
    }
    points.add(point);
  }
  final out = StringBuffer('M${_n(points.first.$1)} ${_n(points.first.$2)}');
  for (var i = 1; i < points.length; i++) {
    final p = points[i];
    if (i == points.length - 1) {
      out.write('L${_n(p.$1)} ${_n(p.$2)}');
      break;
    }
    final a = points[i - 1], b = points[i + 1];
    double length(double x, double y) => math.sqrt(x * x + y * y);
    final inLength = length(p.$1 - a.$1, p.$2 - a.$2);
    final outLength = length(b.$1 - p.$1, b.$2 - p.$2);
    final r = math.min(_radius, math.min(inLength, outLength) / 2);
    final ux = (p.$1 - a.$1) / inLength, uy = (p.$2 - a.$2) / inLength;
    final vx = (b.$1 - p.$1) / outLength, vy = (b.$2 - p.$2) / outLength;
    out.write(
      'L${_n(p.$1 - ux * r)} ${_n(p.$2 - uy * r)}'
      'Q${_n(p.$1)} ${_n(p.$2)} ${_n(p.$1 + vx * r)} ${_n(p.$2 + vy * r)}',
    );
  }
  return out.toString();
}
