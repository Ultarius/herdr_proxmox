import 'dart:async';
import 'dart:convert';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'dart:math' as math;
import 'package:web/web.dart' as web;
import '../ui.dart';
import 'org_forms.dart' show Json;
import 'org_ui.dart';

const chartNodeWidth = 232.0;
const chartNodeHeight = 144.0;
const chartColumnGap = 36.0;
const chartRowGap = 56.0;
const chartPadding = 24.0;

// PointerEvent coordinates can be fractional (e.g. browser zoom/DPI scaling),
// while web 1.1.1 inherits int getters from MouseEvent. Read the JS numbers
// directly rather than throwing on a real pointer's intermediate positions.
final class _ClientPosition {
  const _ClientPosition(this.x, this.y);

  factory _ClientPosition.fromEvent(web.MouseEvent event) => _ClientPosition(
    event.getProperty<JSNumber>('clientX'.toJS).toDartDouble,
    event.getProperty<JSNumber>('clientY'.toJS).toDartDouble,
  );

  final double x;
  final double y;
}

/// The original leaf-centred forest layout, using the same fixed geometry for
/// HTML cards and SVG connector anchors. Only saved manager IDs create edges.
class ChartLayout {
  ChartLayout(
    List<Json> profiles, [
    List<Json> groups = const [],
    double? viewportWidth,
  ]) {
    final byId = {for (final p in profiles) '${p['id']}': p};
    final visited = <String>{};
    double nextLeaf = 0;
    double place(Json profile, int depth) {
      final id = '${profile['id']}';
      visited.add(id);
      final xs = <double>[];
      for (final child in profiles.where((p) => p['manager_id'] == id)) {
        if (!visited.contains(child['id'])) xs.add(place(child, depth + 1));
      }
      final x = xs.isEmpty
          ? nextLeaf++ * (chartNodeWidth + chartColumnGap)
          : (xs.first + xs.last) / 2;
      positions[id] = (
        x + chartPadding,
        depth * (chartNodeHeight + chartRowGap) + chartPadding,
      );
      maxDepth = math.max(maxDepth, depth);
      return x;
    }

    for (final p in profiles.where((p) => !byId.containsKey(p['manager_id']))) {
      if (!visited.contains(p['id'])) place(p, 0);
    }
    for (final p in profiles) {
      if (!visited.contains(p['id'])) place(p, 0);
    }
    // A set of independent hires has no reporting levels. Pack that set into
    // readable rows rather than shrinking one very wide row into tiny cards.
    // Any real manager relationship keeps the tree layout above unchanged.
    if (viewportWidth != null &&
        profiles.length > 1 &&
        profiles.every((p) => !byId.containsKey(p['manager_id']))) {
      final columns = math.min(
        profiles.length,
        math.min(
          4,
          math.max(
            2,
            ((viewportWidth - 64 + chartColumnGap) /
                    (chartNodeWidth + chartColumnGap))
                .floor(),
          ),
        ),
      );
      positions.clear();
      for (var i = 0; i < profiles.length; i++) {
        positions['${profiles[i]['id']}'] = (
          chartPadding + (i % columns) * (chartNodeWidth + chartColumnGap),
          chartPadding + (i ~/ columns) * (chartNodeHeight + chartRowGap),
        );
      }
      maxDepth = (profiles.length / columns).ceil() - 1;
    }
    width = positions.isEmpty
        ? chartNodeWidth + 2 * chartPadding
        : positions.values
              .map((p) => p.$1 + chartNodeWidth + chartPadding)
              .reduce(math.max);
    final agentsHeight = positions.isEmpty
        ? 0.0
        : (maxDepth + 1) * chartNodeHeight +
              maxDepth * chartRowGap +
              2 * chartPadding;
    final columns = math.min(3, math.max(1, groups.length));
    groupTop = agentsHeight + (profiles.isEmpty ? chartPadding : 28);
    for (var i = 0; i < groups.length; i++) {
      groupPositions['${groups[i]['id']}'] = (
        chartPadding + (i % columns) * (chartNodeWidth + chartColumnGap),
        groupTop + 28 + (i ~/ columns) * (chartNodeHeight + 24),
      );
    }
    if (groups.isNotEmpty)
      width = math.max(
        width,
        columns * chartNodeWidth +
            (columns - 1) * chartColumnGap +
            2 * chartPadding,
      );
    height = groups.isEmpty
        ? math.max(192, agentsHeight)
        : groupTop +
              28 +
              ((groups.length / columns).ceil() - 1) * (chartNodeHeight + 24) +
              chartNodeHeight +
              chartPadding;
  }
  final positions = <String, (double, double)>{};
  final groupPositions = <String, (double, double)>{};
  int maxDepth = 0;
  late double width, height, groupTop;
  List<Json> reportingProfiles(List<Json> profiles) => profiles.where((p) {
    final parent = positions['${p['manager_id']}'],
        child = positions['${p['id']}'];
    return parent != null && child != null && parent.$2 < child.$2;
  }).toList();
}

class OrgChart extends StatefulComponent {
  const OrgChart({
    super.key,
    required this.profiles,
    required this.agents,
    required this.jobs,
    required this.onSelect,
    this.groups = const [],
    this.onSelectGroup,
    this.onConfigure,
  });
  final List<Json> profiles, agents, jobs, groups;
  final void Function(Json) onSelect;
  final void Function(Json)? onSelectGroup, onConfigure;
  @override
  State<OrgChart> createState() => _OrgChartState();
}

class _OrgChartState extends State<OrgChart> {
  static int nextId = 0;
  late final String viewportId = 'organization-chart-${nextId++}';
  web.ResizeObserver? observer;
  Timer? mounting;
  double zoom = 1, offsetX = 0, offsetY = 0;
  double measuredWidth = 0, measuredHeight = 0;
  final pointers = <int, _ClientPosition>{};
  final pointerOrigins = <int, _ClientPosition>{};
  bool dragging = false;
  bool suppressPointerClick = false;
  Timer? clickSuppression;
  late ChartLayout layout;
  late List<Json> profiles;
  String signature = '';
  bool fitting = true;

  @override
  void initState() {
    super.initState();
    updateLayout();
    mounting = Timer.periodic(const Duration(milliseconds: 20), (_) {
      final viewport =
          web.document.getElementById(viewportId) as web.HTMLElement?;
      if (!mounted || viewport == null) return;
      mounting?.cancel();
      observer = web.ResizeObserver(
        ((JSArray<web.ResizeObserverEntry> _, web.ResizeObserver __) {
          measure(viewport);
        }).toJS,
      );
      observer!.observe(viewport);
      measure(viewport);
    });
  }

  void updateLayout() {
    profiles = component.profiles.where((p) => p['group_id'] == null).toList();
    final next = jsonEncode([
      for (final p in profiles) [p['id'], p['manager_id']],
      for (final g in component.groups) g['id'],
    ]);
    layout = ChartLayout(
      profiles,
      component.groups,
      measuredWidth > 0 ? measuredWidth : null,
    );
    if (next != signature) {
      signature = next;
      fitting = true;
    }
  }

  @override
  void didUpdateComponent(OrgChart old) {
    super.didUpdateComponent(old);
    updateLayout();
    if (fitting)
      scheduleMicrotask(() {
        final viewport =
            web.document.getElementById(viewportId) as web.HTMLElement?;
        if (mounted && fitting && viewport != null) fit(viewport);
      });
  }

  @override
  void dispose() {
    mounting?.cancel();
    observer?.disconnect();
    pointers.clear();
    pointerOrigins.clear();
    clickSuppression?.cancel();
    super.dispose();
  }

  void measure(web.HTMLElement viewport) {
    if (viewport.clientWidth == measuredWidth &&
        viewport.clientHeight == measuredHeight)
      return;
    measuredWidth = viewport.clientWidth.toDouble();
    measuredHeight = viewport.clientHeight.toDouble();
    updateLayout();
    fit(viewport);
  }

  void fit([web.HTMLElement? viewport]) {
    viewport ??= web.document.getElementById(viewportId) as web.HTMLElement?;
    if (viewport == null ||
        viewport.clientWidth == 0 ||
        viewport.clientHeight == 0)
      return;
    final width = viewport.clientWidth.toDouble(),
        height = viewport.clientHeight.toDouble();
    setState(() {
      fitting = true;
      zoom = math
          .min(
            1.0,
            math.min(
              (width - 32) / layout.width,
              (height - 32) / layout.height,
            ),
          )
          .clamp(.1, 2.5);
      offsetX = (width - layout.width * zoom) / 2;
      offsetY = (height - layout.height * zoom) / 2;
    });
  }

  void zoomAt(double next, double x, double y) {
    next = next.clamp(.1, 2.5);
    final scale = next / zoom;
    setState(() {
      fitting = false;
      offsetX = x - (x - offsetX) * scale;
      offsetY = y - (y - offsetY) * scale;
      zoom = next;
    });
  }

  void zoomButton(double difference) {
    final viewport =
        web.document.getElementById(viewportId) as web.HTMLElement?;
    zoomAt(
      zoom + difference,
      (viewport?.clientWidth ?? measuredWidth) / 2,
      (viewport?.clientHeight ?? measuredHeight) / 2,
    );
  }

  void pointerDown(web.Event event) {
    final e = event as web.PointerEvent;
    if (e.button != 0) return;
    // A new deliberate press must remain clickable after a previous pan.
    if (pointers.isEmpty) {
      suppressPointerClick = false;
      clickSuppression?.cancel();
    }
    final target = e.target;
    if (target is web.Element &&
        target.closest('.org-chart-node__configure') != null)
      return;
    final viewport = e.currentTarget as web.HTMLElement;
    // The chart owns this gesture. Without this the browser can start its own
    // drag or text selection and cancel the pointer sequence mid-pan, which a
    // synthetic-event test never reproduces.
    e.preventDefault();
    pointers[e.pointerId] = _ClientPosition.fromEvent(e);
    pointerOrigins[e.pointerId] = pointers[e.pointerId]!;
    // Captured while the pointer is definitely active. Capturing later, from the
    // first move, can fail once the browser has already claimed the gesture, and
    // a move that leaves the viewport would then be lost.
    try {
      viewport.setPointerCapture(e.pointerId);
    } catch (_) {}
    // preventDefault suppresses the default focus, so focus explicitly to keep
    // the keyboard pan controls reachable.
    viewport.focus();
    if (pointers.length > 1) beginDrag(viewport);
  }

  void beginDrag(web.HTMLElement viewport) {
    dragging = true;
    // Synthetic events and a pointer already cancelled by the browser do not
    // have an active capture target. Panning can still finish in this viewport.
    try {
      for (final pointerId in pointers.keys)
        viewport.setPointerCapture(pointerId);
    } catch (_) {}
    viewport.focus();
    viewport.setAttribute('data-panning', '');
  }

  void pointerMove(web.Event event) {
    final e = event as web.PointerEvent;
    var previous = pointers[e.pointerId];
    if (previous == null) return;
    final next = _ClientPosition.fromEvent(e);
    if (!dragging) {
      final origin = pointerOrigins[e.pointerId]!;
      if (math.sqrt(
            math.pow(next.x - origin.x, 2) + math.pow(next.y - origin.y, 2),
          ) <
          5) {
        pointers[e.pointerId] = next;
        return;
      }
      previous = origin;
      beginDrag(e.currentTarget as web.HTMLElement);
    }
    e.preventDefault();
    if (pointers.length == 2) {
      final other = pointers.entries
          .firstWhere((p) => p.key != e.pointerId)
          .value;
      final oldDistance = math.sqrt(
        math.pow(previous.x - other.x, 2) + math.pow(previous.y - other.y, 2),
      );
      final distance = math.sqrt(
        math.pow(next.x - other.x, 2) + math.pow(next.y - other.y, 2),
      );
      final rect = (e.currentTarget as web.HTMLElement).getBoundingClientRect();
      if (oldDistance > 0)
        zoomAt(
          zoom * distance / oldDistance,
          (next.x + other.x) / 2 - rect.left,
          (next.y + other.y) / 2 - rect.top,
        );
    } else {
      final deltaX = next.x - previous.x, deltaY = next.y - previous.y;
      setState(() {
        fitting = false;
        offsetX += deltaX;
        offsetY += deltaY;
      });
    }
    pointers[e.pointerId] = next;
  }

  void pointerEnd(web.Event event) {
    final e = event as web.PointerEvent;
    pointers.remove(e.pointerId);
    pointerOrigins.remove(e.pointerId);
    if (pointers.isEmpty) {
      (e.currentTarget as web.HTMLElement).removeAttribute('data-panning');
      if (dragging) {
        suppressPointerClick = true;
        clickSuppression?.cancel();
        clickSuppression = Timer(
          const Duration(milliseconds: 400),
          () => suppressPointerClick = false,
        );
      }
      dragging = false;
    }
  }

  void handleActivation(web.Event event, void Function() action) {
    // Keyboard/programmatic activation is independent of the mouse gesture.
    if (suppressPointerClick && event is web.MouseEvent && event.detail > 0) {
      event.preventDefault();
      event.stopPropagation();
      suppressPointerClick = false;
      clickSuppression?.cancel();
      return;
    }
    action();
  }

  String edge((double, double) parent, (double, double) child) {
    final fromY = parent.$2 + chartNodeHeight,
        toY = child.$2,
        middle = (fromY + toY) / 2;
    return 'M ${parent.$1 + chartNodeWidth / 2} $fromY V $middle H ${child.$1 + chartNodeWidth / 2} V $toY';
  }

  String role(Json p) => p['coordination_binding'] == 'used'
      ? 'Used by coordination'
      : p['coordination_binding'] == 'inactive'
      ? 'Inactive coordinator profile'
      : '${p['role'] ?? 'Agent'}';

  Component agentNode(Json profile) {
    final id = '${profile['id']}', pos = layout.positions[id]!;
    final live = component.agents
        .where((a) => a['profile_id'] == profile['id'])
        .firstOrNull;
    final state = liveActivity(live);
    final run = component.jobs
        .where(
          (j) =>
              j['kind'] == 'launch' &&
              j['profile_id'] == profile['id'] &&
              j['state'] != 'released',
        )
        .firstOrNull;
    final name = '${profile['name'] ?? 'Agent'}',
        jobState = '${run?['state'] ?? ''}';
    final manager = profiles
        .where((p) => p['id'] == profile['manager_id'])
        .firstOrNull;
    return el(
      'article',
      [
        el('div', [
          agentTile(
            name,
            role: role(profile),
            live: state == 'working',
            classes: 'org-chart-node__avatar',
          ),
          el('div', [
            el(
              'button',
              [text(name)],
              classes: 'org-chart-node__name',
              attributes: {'type': 'button', 'title': name, 'aria-label': name},
              events: {
                'click': (e) =>
                    handleActivation(e, () => component.onSelect(profile)),
              },
            ),
            el(
              'span',
              [text(role(profile))],
              classes: 'org-chart-node__role',
              attributes: {'title': role(profile)},
            ),
          ], classes: 'org-chart-node__identity'),
          if (component.onConfigure != null)
            el(
              'button',
              [icon('settings')],
              classes: 'org-chart-node__configure',
              attributes: {
                'type': 'button',
                'title': 'Configure $name',
                'aria-label': 'Configure $name',
              },
              events: {
                'click': (e) =>
                    handleActivation(e, () => component.onConfigure!(profile)),
              },
            ),
        ], classes: 'org-chart-node__head'),
        el('div', [
          activityPill(state),
          el('span', [
            text(profile['runtime'] ?? 'CLI'),
          ], classes: 'org-chart-node__runtime'),
        ], classes: 'org-chart-node__activity'),
        el(
          'div',
          [
            text(
              manager == null
                  ? 'Organization owner'
                  : 'Reports to ${manager['name']}',
            ),
          ],
          classes: 'org-chart-node__manager',
          attributes: {
            'title': manager == null
                ? 'No saved manager'
                : 'Reports to ${manager['name']}',
          },
        ),
        if (jobState.isNotEmpty && jobState != 'persona_sent')
          el(
            'div',
            [text('Launch: ${jobState.replaceAll('_', ' ')}')],
            classes: 'org-chart-node__run',
            attributes: {
              'title': 'Historical launch state; live activity is shown above',
            },
          )
        else
          el('div', [
            text(
              live != null
                  ? 'Live console available'
                  : run != null
                  ? 'Launch recorded · inspect console'
                  : 'Saved persona · ready to launch',
            ),
          ], classes: 'org-chart-node__run'),
      ],
      classes: 'org-chart-node org-chart-node--agent',
      attributes: {
        'style':
            'left:${pos.$1}px;top:${pos.$2}px;width:${chartNodeWidth}px;height:${chartNodeHeight}px;',
        'data-profile-id': id,
        'data-manager-id': '${profile['manager_id'] ?? ''}',
        'data-activity': state,
      },
      events: {
        'contextmenu': (e) {
          if (component.onConfigure == null) return;
          e.preventDefault();
          component.onConfigure!(profile);
        },
      },
    );
  }

  Component groupNode(Json group) {
    final pos = layout.groupPositions['${group['id']}']!,
        members = group['members'] as List? ?? [];
    final people = [
      for (final id in members)
        profiles.where((p) => p['id'] == id).firstOrNull ?? {'name': id},
    ];
    final names = people.map((p) => p['name']).join(', ');
    return el(
      'article',
      [
        el('div', [
          el(
            'span',
            [icon('message-square')],
            classes: 'org-chart-node__avatar org-chart-node__avatar--group',
            attributes: {'aria-hidden': 'true'},
          ),
          el('div', [
            el(
              'button',
              [text('# ${group['name']}')],
              classes: 'org-chart-node__name',
              attributes: {
                'type': 'button',
                'title': '${group['name']}',
                'aria-label': 'Open group ${group['name']}',
                if (component.onSelectGroup == null) 'disabled': '',
              },
              events: {
                'click': (e) => handleActivation(
                  e,
                  () => component.onSelectGroup?.call(group),
                ),
              },
            ),
            el('span', [
              text('Discussion group · ${members.length} members'),
            ], classes: 'org-chart-node__role'),
          ], classes: 'org-chart-node__identity'),
        ], classes: 'org-chart-node__head'),
        el(
          'p',
          [text(group['description'] ?? '')],
          classes: 'org-chart-node__description',
          attributes: {'title': '${group['description'] ?? ''}'},
        ),
        el(
          'div',
          [
            if (people.isNotEmpty)
              el(
                'span',
                [
                  for (final p in people.take(6))
                    agentTile(p['name'], role: p['role'], size: 'xs'),
                ],
                classes: 'avatar-stack org-chart-node__stack',
                attributes: {'aria-hidden': 'true'},
              ),
            el('span', [
              text(names.isEmpty ? 'No selected members' : names),
            ], classes: 'org-chart-node__names'),
          ],
          classes: 'org-chart-node__members',
          attributes: {'title': names},
        ),
      ],
      classes: 'org-chart-node org-chart-node--group',
      attributes: {
        'style':
            'left:${pos.$1}px;top:${pos.$2}px;width:${chartNodeWidth}px;height:${chartNodeHeight}px;',
        'data-group-id': '${group['id']}',
      },
    );
  }

  @override
  Component build(BuildContext context) {
    final edges = layout.reportingProfiles(profiles),
        empty = profiles.isEmpty && component.groups.isEmpty;
    Component control(String label, String iconName, void Function() onTap) =>
        el(
          'button',
          [icon(iconName), text(label)],
          classes: 'btn-group__btn',
          attributes: {'type': 'button', 'title': label},
          events: {'click': (_) => onTap()},
        );
    return el('section', [
      el('div', [
        el('div', [
          el('h2', [
            icon('network'),
            text('Organization chart'),
          ], classes: 'org-chart__title'),
          el('span', [
            text(
              '${profiles.length} agents · ${edges.length} reporting ${edges.length == 1 ? 'line' : 'lines'} · ${component.groups.length} groups',
            ),
          ], classes: 'org-chart__summary'),
        ], classes: 'org-chart__heading'),
        el(
          'div',
          [
            control('Zoom out', 'zoom-out', () => zoomButton(-.15)),
            el(
              'output',
              [text('${(zoom * 100).round()}%')],
              classes: 'btn-group__readout',
              attributes: {'aria-live': 'polite', 'aria-label': 'Zoom level'},
            ),
            control('Zoom in', 'zoom-in', () => zoomButton(.15)),
            control('Fit chart', 'scan', fit),
            control(
              'Reset',
              'rotate-ccw',
              () => zoomAt(1, measuredWidth / 2, measuredHeight / 2),
            ),
          ],
          classes: 'btn-group org-chart__controls',
          attributes: {'role': 'group', 'aria-label': 'Chart zoom'},
        ),
      ], classes: 'org-chart__toolbar'),
      el(
        'div',
        [
          if (empty)
            el('div', [
              el('span', [icon('network')], classes: 'empty__icon'),
              el('strong', [text('Build your organization')]),
              el('p', [text('Hire your first agent to start the chart.')]),
            ], classes: 'org-chart__empty')
          else
            el(
              'div',
              [
                el(
                  'svg',
                  [
                    for (final profile in edges)
                      el(
                        'path',
                        [
                          el('title', [
                            text(
                              '${profile['name']} reports to ${profiles.where((p) => p['id'] == profile['manager_id']).first['name']}',
                            ),
                          ]),
                        ],
                        classes: 'org-chart-edge',
                        attributes: {
                          'd': edge(
                            layout.positions['${profile['manager_id']}']!,
                            layout.positions['${profile['id']}']!,
                          ),
                          'data-manager-id': '${profile['manager_id']}',
                          'data-profile-id': '${profile['id']}',
                          'fill': 'none',
                        },
                      ),
                  ],
                  classes: 'org-chart-edges',
                  attributes: {
                    'xmlns': 'http://www.w3.org/2000/svg',
                    'width': '${layout.width}',
                    'height': '${layout.height}',
                    'viewBox': '0 0 ${layout.width} ${layout.height}',
                    'role': 'img',
                    'aria-label': 'Saved reporting relationships',
                  },
                ),
                for (final profile in profiles) agentNode(profile),
                if (component.groups.isNotEmpty)
                  el(
                    'div',
                    [text('GROUPS / TOPICS')],
                    classes: 'org-chart__group-label',
                    attributes: {
                      'style':
                          'left:${chartPadding}px;top:${layout.groupTop}px;',
                    },
                  ),
                for (final group in component.groups) groupNode(group),
              ],
              classes: 'org-chart-scene',
              attributes: {
                'style':
                    'width:${layout.width}px;height:${layout.height}px;transform:translate(${offsetX}px,${offsetY}px) scale($zoom);',
                'data-zoom': '$zoom',
                'data-offset-x': '$offsetX',
                'data-offset-y': '$offsetY',
              },
            ),
        ],
        classes: 'org-chart-viewport',
        attributes: {
          'id': viewportId,
          'tabindex': '0',
          'role': 'group',
          'aria-label':
              'Organization chart. Use arrow keys to pan, plus or minus to zoom, and 0 to fit. Drag to pan or scroll to zoom.',
          'style': '--chart-natural-height:${layout.height + 40}px;',
          if (empty) 'data-empty': '',
        },
        events: {
          'pointerdown': pointerDown,
          'pointermove': pointerMove,
          'pointerup': pointerEnd,
          'pointercancel': pointerEnd,
          'lostpointercapture': pointerEnd,
          'pointerleave': (e) {
            if (!dragging) pointerEnd(e);
          },
          'wheel': (event) {
            if (empty) return;
            final e = event as web.WheelEvent;
            e.preventDefault();
            final rect = (e.currentTarget as web.HTMLElement)
                .getBoundingClientRect();
            final position = _ClientPosition.fromEvent(e);
            zoomAt(
              zoom * math.exp(-e.deltaY * .0015),
              position.x - rect.left,
              position.y - rect.top,
            );
          },
          'keydown': (event) {
            final e = event as web.KeyboardEvent;
            if ((e.target as web.Element?)?.closest('button') != null) return;
            if ([
              '+',
              '=',
              '-',
              '0',
              'ArrowLeft',
              'ArrowRight',
              'ArrowUp',
              'ArrowDown',
            ].contains(e.key))
              e.preventDefault();
            if (e.key == '+' || e.key == '=') {
              zoomButton(.15);
            } else if (e.key == '-') {
              zoomButton(-.15);
            } else if (e.key == '0') {
              fit();
            } else if (e.key.startsWith('Arrow')) {
              setState(() {
                fitting = false;
                if (e.key == 'ArrowLeft') offsetX += 32;
                if (e.key == 'ArrowRight') offsetX -= 32;
                if (e.key == 'ArrowUp') offsetY += 32;
                if (e.key == 'ArrowDown') offsetY -= 32;
              });
            }
          },
        },
      ),
      el('div', [
        el('span', [
          text(
            'Drag to pan · Scroll to zoom · Click a name: console · Right click: configure',
          ),
        ]),
        el('span', [text('Lines follow saved manager settings.')]),
      ], classes: 'org-chart__help'),
    ], classes: 'organization-chart');
  }
}
