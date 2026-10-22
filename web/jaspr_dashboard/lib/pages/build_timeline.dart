/// Builds & deployments markup helpers (Phase 2 design): status pills and
/// rail markers, definition lists, run times, validation steps and the build
/// cadence strip. Everything renders fields the gateway already returns.
library;

import 'operations_support.dart';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', //
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];
const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/// "validation_pending" -> "Validation pending".
String stateLabel(Object? value) {
  final words = '${value ?? 'unknown'}'.replaceAll('_', ' ').trim();
  return words.isEmpty
      ? 'Unknown'
      : words[0].toUpperCase() + words.substring(1);
}

/// Tone and marker shape for a build, deployment or check state. The shape
/// repeats the state without color: filled = done or active, ring = waiting,
/// diamond = needs attention, dash = stopped.
(String, String) statusLook(Object? state) => switch ('$state') {
  'complete' || 'passed' => ('success', 'filled'),
  'running' => ('accent', 'filled'),
  'failed' || 'error' => ('danger', 'diamond'),
  'interrupted' || 'unavailable' => ('warning', 'diamond'),
  'cancelled' || 'skipped' => ('neutral', 'dash'),
  _ => ('neutral', 'hollow'),
};

Component dot(String tone, String shape, {String size = ''}) => el(
  'span',
  [],
  classes: [
    'dot',
    if (size.isNotEmpty) 'dot--$size',
    if (shape != 'filled') 'dot--$shape',
  ].join(' '),
  attributes: {'data-tone': tone, 'aria-hidden': 'true'},
);

/// Template pill whose dot repeats the state shape.
Component pill(String label, String tone, {String shape = 'filled'}) => el(
  'span',
  [dot(tone, shape), text(label)],
  classes: 'pill',
  attributes: {'data-tone': tone},
);

Component statusPill(Object? state, {String? label}) {
  final (tone, shape) = statusLook(state);
  return pill(label ?? stateLabel(state), tone, shape: shape);
}

/// 12px status-shaped marker for the timeline rail and the cadence axis.
Component marker(Object? state, {String size = 'lg'}) {
  final (tone, shape) = statusLook(state);
  return dot(tone, shape, size: size);
}

Component mono(Object? value) => el('span', [text(value)], classes: 'mono');

/// Short commit chip; the full SHA stays in the title and the evidence list.
Component shaChip(Object? sha, {String? classes}) {
  final value = '${sha ?? ''}';
  return el(
    'span',
    [
      icon('git-commit-horizontal'),
      el('span', [text('Commit ')], classes: 'visually-hidden'),
      text(value.length > 7 ? value.substring(0, 7) : value),
    ],
    classes: ['mono-chip', 'sha-chip', ?classes].join(' '),
    attributes: {'title': value},
  );
}

/// Template definition list (.dl) of label and value rows.
Component facts(List<(String, Component)> rows, {String? classes}) => el('dl', [
  for (final (label, value) in rows)
    el('div', [
      el('dt', [text(label)]),
      el('dd', [value]),
    ]),
], classes: ['dl', ?classes].join(' '));

/// Tinted message: icon, optional title and text lines (template .callout).
Component callout(
  String tone,
  String iconName,
  List<Component> lines, {
  String? title,
}) => el(
  'div',
  [
    icon(iconName, classes: 'callout__icon'),
    if (title != null) el('p', [text(title)], classes: 'callout__title'),
    for (final line in lines) el('p', [line], classes: 'callout__text'),
  ],
  classes: 'callout',
  attributes: {'data-tone': tone},
);

/// Build card note line (waiver, run note).
Component buildNote(String iconName, Object? message) => el('p', [
  icon(iconName),
  el('span', [text(message)]),
], classes: 'build-card__note');

/// Build card problem line (failed checks, retention errors).
Component buildProblem(String tone, String iconName, List<Component> message) =>
    el(
      'p',
      [icon(iconName), el('span', message)],
      classes: 'build-card__failure',
      attributes: {'data-tone': tone},
    );

DateTime? runTime(Object? value) =>
    value == null ? null : DateTime.tryParse('$value')?.toLocal();
String _two(int value) => value.toString().padLeft(2, '0');
String dayMonth(DateTime d) => '${d.day} ${_months[d.month - 1]}';
String clock(DateTime d) => '${_two(d.hour)}:${_two(d.minute)}';
String weekday(DateTime d) => _weekdays[d.weekday - 1];
String fullDate(DateTime d) =>
    '${weekday(d)} ${dayMonth(d)} ${d.year} · ${clock(d)}';
String elapsed(Duration span) {
  final s = span.inSeconds;
  if (s < 60) return '${s < 0 ? 0 : s}s';
  if (s < 3600) return '${s ~/ 60}m ${_two(s % 60)}s';
  return '${s ~/ 3600}h ${_two(s % 3600 ~/ 60)}m';
}

List<Map> checksOf(Map run) => [
  for (final check in run['checks'] is List ? run['checks'] as List : const [])
    if (check is Map) check,
];

/// Date and time beside the rail (the card repeats it on narrow screens).
Component whenColumn(DateTime? at) => el(
  'div',
  at == null
      ? [
          el('span', [text('No start time')], classes: 'build-entry__time'),
        ]
      : [
          el('span', [text(dayMonth(at))], classes: 'build-entry__day'),
          el('span', [
            text('${weekday(at)} · ${clock(at)}'),
          ], classes: 'build-entry__time'),
        ],
  classes: 'build-entry__when',
  attributes: {'aria-hidden': 'true'},
);

Component rail(Object? state) => el(
  'div',
  [
    el('span', [marker(state)], classes: 'build-entry__marker'),
  ],
  classes: 'build-entry__rail',
  attributes: {'aria-hidden': 'true'},
);

Component _fact(String iconName, String label, {String? tone, String? cls}) =>
    el(
      'li',
      [
        icon(iconName),
        el('span', [text(label)]),
      ],
      classes: ['build-card__fact', ?cls].join(' '),
      attributes: {'data-tone': ?tone},
    );

/// Facts row: start date (narrow only), duration, checks, package evidence.
Component? runFacts(Map run) {
  final started = runTime(run['started_at']);
  final finished = runTime(run['finished_at']);
  final checks = checksOf(run);
  final passed = checks.where((c) => c['status'] == 'passed').length;
  final failed = checks.where((c) => statusLook(c['status']).$1 == 'danger');
  final package = run['deployment_package'];
  final verb = switch ('${run['state']}') {
    'complete' => 'Built in',
    'failed' || 'error' => 'Failed after',
    _ => 'Ran for',
  };
  final items = [
    if (started != null)
      _fact('calendar', fullDate(started), cls: 'build-card__date'),
    if (started != null && finished != null)
      _fact('clock', '$verb ${elapsed(finished.difference(started))}'),
    if (checks.isNotEmpty)
      _fact(
        failed.isNotEmpty
            ? 'circle-x'
            : passed == checks.length
            ? 'circle-check'
            : 'circle-dashed',
        '$passed/${checks.length} checks passed'
        '${failed.isNotEmpty ? ', ${failed.length} failed' : ''}',
        tone: failed.isNotEmpty
            ? 'danger'
            : passed == checks.length
            ? 'success'
            : null,
      ),
    if (package is Map)
      package['required_checks_verified'] == true
          ? _fact('shield-check', 'Package evidence complete', tone: 'success')
          : _fact(
              'triangle-alert',
              'Package evidence incomplete',
              tone: 'warning',
            ),
  ];
  return items.isEmpty
      ? null
      : el(
          'ul',
          items,
          classes: 'build-card__facts',
          attributes: {'role': 'list'},
        );
}

/// The newest/failed flags set only the initial expansion. Later updates keep
/// the user's choice, even if this run stops being newest or newly fails.
Component checkSteps(List<Map> checks, {required bool open}) {
  final failed = checks.where((c) => statusLook(c['status']).$1 == 'danger');
  final passed = checks.every((c) => c['status'] == 'passed');
  return el(
    'details',
    [
      el('summary', [
        icon('list-checks'),
        el('span', [
          text(
            '${checks.length} validation step${checks.length == 1 ? '' : 's'}',
          ),
        ]),
        if (failed.isNotEmpty || passed)
          el('span', [
            text(failed.isNotEmpty ? '${failed.length} failed' : 'all passed'),
          ], classes: 'build-card__summary-hint'),
      ], classes: 'build-card__summary'),
      el(
        'ul',
        [
          for (final check in checks)
            el('li', [
              el('div', [
                el('span', [
                  text(check['name'] ?? check['id']),
                ], classes: 'build-step__name'),
                if ('${check['summary'] ?? ''}'.isNotEmpty)
                  el('span', [
                    text(check['summary']),
                  ], classes: 'build-step__summary'),
              ], classes: 'build-step__main'),
              statusPill(check['status']),
              el('span', [
                text('${check['duration_ms'] ?? 0} ms'),
              ], classes: 'build-step__num'),
              el('span', [
                text('exit ${check['exit_code'] ?? 'not run'}'),
              ], classes: 'build-step__num'),
            ], classes: 'build-step'),
        ],
        classes: 'build-steps',
        attributes: const {'role': 'list'},
      ),
    ],
    classes: 'build-card__steps',
    attributes: open ? const {'open': ''} : null,
  );
}

/// Cadence strip: one marker per build on a time axis that runs from the
/// day of the oldest start to the end of today (or of the newest start).
/// Labels are laid out for the axis minimum width (520px), so they never
/// overlap at any wider size; at most three label rows, the rest are titles.
Component? cadenceStrip(List<Map<String, dynamic>> runs, DateTime now) {
  final dated = [
    for (final run in runs)
      if (runTime(run['started_at']) case final at?) (run: run, at: at),
  ]..sort((a, b) => a.at.compareTo(b.at));
  if (dated.isEmpty) return null;
  final oldest = dated.first.at;
  final latest = dated.last.at.isAfter(now) ? dated.last.at : now;
  final start = DateTime(oldest.year, oldest.month, oldest.day);
  final end = DateTime(latest.year, latest.month, latest.day + 1);
  final total = end.difference(start).inSeconds;
  double x(DateTime d) =>
      (d.difference(start).inSeconds / total * 100).clamp(0, 100).toDouble();
  String pos(double value) => '${value.toStringAsFixed(3)}%';
  const width = 520.0, gap = 6.0, maxRows = 3;
  final todayPx = x(now) * width / 100;

  final days = end.difference(start).inHours / 24;
  final ticks = <(DateTime, String)>[];
  if (days <= 3) {
    for (
      var d = start;
      d.isBefore(end);
      d = DateTime(d.year, d.month, d.day, d.hour + 6)
    )
      ticks.add((d, d.hour == 0 ? dayMonth(d) : clock(d)));
  } else {
    for (
      var d = start;
      d.isBefore(end);
      d = DateTime(d.year, d.month, d.day + 1)
    )
      if (days <= 14 ||
          d == start ||
          (days <= 120 ? d.weekday == DateTime.monday : d.day == 1))
        ticks.add((d, dayMonth(d)));
  }

  final rows = <double>[];
  final marks = <Component>[];
  for (final entry in dated) {
    final target = '${entry.run['target'] ?? entry.run['id'] ?? ''}';
    final label = target.length > 7 ? target.substring(0, 7) : target;
    final w = label.length * 7.3 + 8;
    final ideal = x(entry.at) * width / 100 - w / 2;
    final left = ideal.clamp(0, width - w).toDouble();
    var row = rows.indexWhere((right) => left >= right + gap);
    if (row < 0 && rows.length < maxRows) row = rows.length;
    if (row == rows.length) rows.add(left + w);
    if (row >= 0) rows[row] = left + w;
    final state = entry.run['state'];
    final name =
        '${entry.run['repository'] ?? 'Build'} $label, '
        '${stateLabel(state).toLowerCase()}, started ${fullDate(entry.at)}';
    marks.add(
      el(
        'li',
        [
          el(
            'span',
            [
              el('span', [marker(state)], classes: 'cadence__dot'),
              el('span', [], classes: 'cadence__stem'),
              if (row >= 0)
                el(
                  'span',
                  [text(label)],
                  classes: 'cadence__label',
                  attributes: {'aria-hidden': 'true'},
                ),
              el('span', [text(name)], classes: 'visually-hidden'),
            ],
            classes: 'cadence__mark',
            attributes: {'data-status': '$state', 'title': name},
          ),
        ],
        classes: 'cadence__item',
        attributes: {
          'style':
              '--x: ${pos(x(entry.at))}; --row: ${row < 0 ? 0 : row}; '
              '--shift: ${(left - ideal).round()}px',
        },
      ),
    );
  }

  const order = [
    'running', 'queued', 'complete', 'failed', 'error', //
    'interrupted', 'cancelled',
  ];
  int rank(String state) =>
      order.contains(state) ? order.indexOf(state) : order.length;
  final counts = <String, int>{};
  for (final entry in dated) {
    counts.update('${entry.run['state']}', (n) => n + 1, ifAbsent: () => 1);
  }
  final states = counts.keys.toList()
    ..sort((a, b) => rank(a).compareTo(rank(b)));
  final failed = dated
      .where((e) => statusLook(e.run['state']).$1 == 'danger')
      .length;
  final undated = runs.length - dated.length;
  final meta = [
    '${dated.length} build${dated.length == 1 ? '' : 's'} since ${dayMonth(oldest)}',
    if (failed > 0) '$failed failed',
    if (undated > 0) '$undated without a start time',
  ].join(' · ');

  return el(
    'section',
    [
      el('header', [
        el('div', [
          el('h2', [
            icon('calendar'),
            text('Build cadence'),
          ], classes: 'card__title'),
          el('p', [text(meta)], classes: 'card__subtitle'),
        ], classes: 'card__heading'),
        el(
          'ul',
          [
            for (final state in states)
              el('li', [
                marker(state, size: 'md'),
                text(stateLabel(state)),
                el('span', [
                  text('${counts[state]}'),
                ], classes: 'legend__count'),
              ], classes: 'legend__item'),
          ],
          classes: 'legend cadence__legend',
          attributes: {'role': 'list', 'aria-label': 'Builds by state'},
        ),
      ], classes: 'card__header cadence__header'),
      el('div', [
        el(
          'div',
          [
            el(
              'div',
              [
                for (final (at, label) in ticks)
                  el(
                    'span',
                    [
                      if ((x(at) * width / 100 - todayPx).abs() >
                          label.length * 3.2 + 17 + gap)
                        el('span', [
                          text(label),
                        ], classes: 'cadence__tick-label'),
                    ],
                    classes: 'cadence__tick',
                    attributes: {'style': '--x: ${pos(x(at))}'},
                  ),
              ],
              classes: 'cadence__ticks',
              attributes: {'aria-hidden': 'true'},
            ),
            el(
              'span',
              [],
              classes: 'cadence__line',
              attributes: {'aria-hidden': 'true'},
            ),
            el(
              'span',
              [],
              classes: 'cadence__line cadence__line--future',
              attributes: {'aria-hidden': 'true'},
            ),
            el(
              'span',
              [
                el('span', [text('Today')], classes: 'cadence__today-label'),
              ],
              classes: 'cadence__today',
              attributes: {'aria-hidden': 'true'},
            ),
            el(
              'ol',
              marks,
              classes: 'cadence__marks',
              attributes: {
                'role': 'list',
                'aria-label': 'Builds by start time, oldest first',
              },
            ),
          ],
          classes: 'cadence__axis',
          attributes: {
            'style':
                '--today: ${pos(x(now))}; --rows: ${rows.isEmpty ? 1 : rows.length}',
          },
        ),
      ], classes: 'cadence__scroll'),
    ],
    classes: 'card cadence',
    attributes: {'aria-label': 'Build cadence'},
  );
}
