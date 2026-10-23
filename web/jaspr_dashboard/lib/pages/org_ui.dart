import '../ui.dart';

/// Phase 2 building blocks shared by the organization, team, runs, group and
/// collaboration views. Presentation only: no handler or request lives here.

/// Turns a stored state key into sentence case: `needs_attention` →
/// "Needs attention". The stored value itself is never changed.
String humanize(Object? value) {
  final words = '${value ?? ''}'.replaceAll('_', ' ').trim();
  if (words.isEmpty) return '';
  return words[0].toUpperCase() + words.substring(1);
}

/// Status tone for run, discussion, chat, proposal and member states.
String stateTone(Object? state) {
  final value = '$state'.toLowerCase();
  if (value.contains('attention') ||
      value.contains('fail') ||
      value.contains('error') ||
      value.contains('blocked') ||
      value == 'exited')
    return 'danger';
  if (value.contains('uncertain') || value.contains('waiting'))
    return 'warning';
  if (value == 'running' || value.contains('progress')) return 'info';
  if (value.contains('complete') ||
      value.contains('verified') ||
      value.contains('created') ||
      value == 'delivered' ||
      value == 'persona_sent' ||
      value == 'working' ||
      value == 'done' ||
      value == 'on' ||
      value == 'active')
    return 'success';
  if (value == 'queued' || value.contains('ready')) return 'accent';
  return 'neutral';
}

/// Tone for a live agent or member status.
///
/// The generic state tones below put `working` in the success bucket, which is
/// right for jobs, builds and deployments but not for agents: the runtime
/// palette is idle green, working blue, attention amber. Member chips and agent
/// dots read from here so a working agent is the same colour everywhere.
String agentStatusTone(Object? status) {
  final value = '$status'.toLowerCase();
  if (const ['working', 'running', 'busy', 'thinking'].contains(value))
    return 'info';
  if (const ['idle', 'done'].contains(value)) return 'success';
  if (const ['blocked', 'attention'].contains(value)) return 'warning';
  return stateTone(status);
}

/// Shape that repeats the tone without color (WCAG 1.4.1).
String stateShape(String tone, Object? state) => switch (tone) {
  'danger' => 'diamond',
  'neutral' =>
    '$state' == 'off' || '$state' == 'stopped' || '$state'.isEmpty
        ? 'dash'
        : 'hollow',
  _ => 'filled',
};

/// Status pill for a stored state, worded in sentence case.
Component statePill(Object? state, {String? label}) {
  final tone = stateTone(state);
  return el(
    'span',
    [text(label ?? humanize(state))],
    classes: 'status',
    attributes: {'data-tone': tone, 'data-shape': stateShape(tone, state)},
  );
}

/// Live activity of an agent from the runtime snapshot (null = no session).
String liveActivity(Map<String, dynamic>? live) =>
    switch (live?['agent_status'] ?? live?['state']) {
      'idle' || 'done' => 'idle',
      'working' || 'running' || 'busy' || 'thinking' => 'working',
      'blocked' => 'attention',
      _ => 'offline',
    };
String activityLabel(String activity) => switch (activity) {
  'idle' => 'Idle',
  'working' => 'Working',
  'attention' => 'Needs attention',
  _ => 'No live session',
};
String activityTone(String activity) => agentActivityTone(activity);
String activityShape(String activity) => switch (activity) {
  'working' => 'filled',
  'attention' => 'diamond',
  'idle' => 'filled',
  _ => 'dash',
};

/// Activity pill (Working / Idle / Needs attention / No live session).
Component activityPill(String activity) => el(
  'span',
  [text(activityLabel(activity))],
  classes: 'status',
  attributes: {
    'data-tone': activityTone(activity),
    'data-shape': activityShape(activity),
    'data-activity': activity,
  },
);

/// Role tile with extra classes (ui.dart roleTile has no class slot).
Component agentTile(
  Object? name, {
  Object? role,
  String size = '',
  bool live = false,
  String? classes,
}) => el(
  'span',
  [text(initials(name))],
  classes: [
    'role-tile',
    if (size.isNotEmpty) 'role-tile--$size',
    'live-ring',
    ?classes,
  ].join(' '),
  attributes: {
    'data-role': roleKey(role ?? name),
    'data-live': '$live',
    'aria-hidden': 'true',
  },
);

/// Tile + name (+ optional meta line), the template's `.agent` cell.
Component agentIdentity(
  Object? name, {
  Object? role,
  Object? meta,
  bool live = false,
  String size = 'sm',
  List<Component> extra = const [],
}) => el('div', [
  agentTile(name, role: role, size: size, live: live),
  el('div', [
    el('span', [text(name)], classes: 'org-agent__name'),
    if (meta != null && '$meta'.isNotEmpty)
      el('span', [text(meta)], classes: 'org-agent__meta'),
    ...extra,
  ], classes: 'org-agent__text'),
], classes: 'org-agent');

/// Small member chip: xs role tile, name and an optional status dot.
Component memberChip(Object? name, {Object? role, String? status}) {
  final tone = agentStatusTone(status);
  final shape = stateShape(tone, status ?? '');
  return el('span', [
    agentTile(name, role: role, size: 'xs', live: status == 'working'),
    el('span', [text(name)], classes: 'org-chip__name'),
    if (status != null)
      el(
        'span',
        [],
        classes: ['dot', if (shape != 'filled') 'dot--$shape'].join(' '),
        attributes: {
          'data-tone': tone,
          'title': humanize(status),
          'aria-hidden': 'true',
        },
      ),
    if (status != null)
      el('span', [text(', ${humanize(status)}')], classes: 'visually-hidden'),
  ], classes: 'org-chip');
}

/// Definition list (`dl.dl`); rows with a null value are skipped.
Component factList(List<(String, Component?)> rows, {String? classes}) =>
    el('dl', [
      for (final row in rows)
        if (row.$2 != null)
          el('div', [
            el('dt', [text(row.$1)]),
            el('dd', [row.$2!]),
          ]),
    ], classes: ['dl', ?classes].join(' '));

Component mono(Object? value) => el('span', [text(value)], classes: 'mono');

/// Empty value: a visible em dash plus a hidden "None" (template ui.none()).
Component noValue() => el('span', [
  el('span', [text('—')], attributes: {'aria-hidden': 'true'}),
  el('span', [text('None')], classes: 'visually-hidden'),
], classes: 'none');

/// A stored timestamp as a mono `<time>`: local "yyyy-mm-dd hh:mm:ss" when it
/// parses, the stored text otherwise. The raw value stays in `datetime`.
String stamp(Object? value) {
  final raw = '${value ?? ''}';
  final parsed = DateTime.tryParse(raw)?.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return parsed == null
      ? raw
      : '${parsed.year}-${two(parsed.month)}-${two(parsed.day)} '
            '${two(parsed.hour)}:${two(parsed.minute)}:${two(parsed.second)}';
}

Component timeStamp(Object? value) {
  final raw = '${value ?? ''}';
  return el(
    'time',
    [text(stamp(raw))],
    classes: 'mono',
    attributes: {'datetime': raw, 'title': raw},
  );
}

/// Secondary note line (13px, secondary text).
Component note(Object? value, {String? classes}) =>
    el('p', [text(value)], classes: ['org-note', ?classes].join(' '));

/// Error line inside a card (danger text with a circle-x icon).
Component errorLine(Object? value) => el(
  'p',
  [icon('circle-x', classes: 'icon--sm'), text(value)],
  classes: 'form-error org-error',
  attributes: {'role': 'alert'},
);

/// Prose block that keeps the author's line breaks (descriptions, prompts).
Component textBlock(Object? value, {String? classes}) =>
    el('div', [text(value)], classes: ['org-text', ?classes].join(' '));

/// Small inline disclosure with a chevron (`details > summary`).
Component disclosure(
  String title,
  List<Component> children, {
  String? iconName,
  String? hint,
  String? classes,
}) => el('details', [
  el('summary', [
    if (iconName != null) icon(iconName, classes: 'icon--sm'),
    el(
      'span',
      [text(title)],
      classes: 'org-disclosure__title',
      attributes: {'title': title},
    ),
    if (hint != null) el('span', [text(hint)], classes: 'org-disclosure__hint'),
    icon('chevron-down', classes: 'icon--sm disclosure-chevron'),
  ], classes: 'disclosure-summary'),
  el('div', children, classes: 'org-disclosure__body'),
], classes: ['org-disclosure', ?classes].join(' '));

/// Card-sized disclosure (settings, backup): a card whose header toggles.
Component cardDisclosure(
  String title,
  List<Component> children, {
  String? iconName,
  String? hint,
}) => el('details', [
  el('summary', [
    if (iconName != null) icon(iconName),
    el('span', [
      el('span', [text(title)], classes: 'org-card-disclosure__title'),
      if (hint != null)
        el('span', [text(hint)], classes: 'org-card-disclosure__hint'),
    ], classes: 'org-card-disclosure__text'),
    icon('chevron-down', classes: 'disclosure-chevron'),
  ], classes: 'disclosure-summary'),
  el('div', children, classes: 'org-card-disclosure__body'),
], classes: 'card org-card-disclosure');

/// Template underline tabs. [items] maps a value to (label, icon). Arrow
/// keys, Home and End move the selection (roving tabindex).
Component viewTabs(
  String id,
  String label,
  Map<String, (String, String)> items,
  String selected,
  void Function(String) onSelect,
) {
  return el(
    'div',
    [
      for (final entry in items.entries)
        el(
          'button',
          [icon(entry.value.$2), text(entry.value.$1)],
          classes: 'tabs__tab',
          attributes: {
            'type': 'button',
            'role': 'tab',
            'id': '$id-tab-${entry.key}',
            'aria-controls': '$id-panel',
            'aria-selected': '${entry.key == selected}',
            'tabindex': entry.key == selected ? '0' : '-1',
          },
          events: {'click': (_) => onSelect(entry.key)},
        ),
    ],
    classes: 'tabs',
    attributes: {'role': 'tablist', 'aria-label': label},
    events: {'keydown': tabListKeyDown},
  );
}

/// The panel that belongs to [viewTabs] with the same [id].
Component tabPanel(String id, String selected, List<Component> children) => el(
  'div',
  children,
  classes: 'tabpanel org-tabpanel',
  attributes: {
    'role': 'tabpanel',
    'id': '$id-panel',
    'aria-labelledby': '$id-tab-$selected',
    'tabindex': '0',
  },
);

/// Callout (tone tint, icon, title and lines) for retry and warning states.
Component callout(
  String tone,
  String iconName,
  List<Component> children, {
  String? title,
}) => el(
  'div',
  [
    icon(iconName, classes: 'callout__icon'),
    if (title != null) el('p', [text(title)], classes: 'callout__title'),
    ...children,
  ],
  classes: 'callout org-callout',
  attributes: {'data-tone': tone},
);
