import 'dart:async';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;

export 'package:jaspr/client.dart';

Component text(Object? value) => Component.text('${value ?? ''}');
Component el(
  String tag,
  List<Component> children, {
  Key? key,
  String? classes,
  Map<String, String>? attributes,
  Map<String, EventCallback>? events,
}) => tag == 'details'
    ? disclosureElement(
        children,
        key: key,
        classes: classes,
        attributes: attributes,
        events: events,
      )
    : Component.element(
        key: key,
        tag: tag,
        classes: classes,
        attributes: attributes,
        events: events,
        children: children,
      );

/// A native disclosure that remembers user toggles during background renders.
/// `attributes['open']` is an initial default; supply [open] only when the
/// calling component owns the open state (including its summary interaction).
Component disclosureElement(
  List<Component> children, {
  Key? key,
  String? classes,
  Map<String, String>? attributes,
  Map<String, EventCallback>? events,
  bool? open,
}) => _Disclosure(
  key: key,
  classes: classes,
  attributes: attributes,
  events: events,
  open: open,
  children: children,
);

class _Disclosure extends StatefulComponent {
  const _Disclosure({
    super.key,
    this.classes,
    this.attributes,
    this.events,
    this.open,
    required this.children,
  });
  final String? classes;
  final Map<String, String>? attributes;
  final Map<String, EventCallback>? events;
  final bool? open;
  final List<Component> children;

  bool get declaredOpen => attributes?.containsKey('open') == true;

  @override
  State<_Disclosure> createState() => _DisclosureState();
}

class _DisclosureState extends State<_Disclosure> {
  late bool open;
  web.HTMLDetailsElement? element;

  @override
  void initState() {
    super.initState();
    open = component.open ?? component.declaredOpen;
  }

  @override
  Component build(BuildContext context) {
    // A parent update can arrive before the browser's queued toggle event.
    // Read uncontrolled state from the clicked element so an update cannot
    // undo the click. Controlled callers update their state synchronously.
    final renderedOpen = component.open ?? element?.open ?? open;
    return Component.element(
      tag: 'details',
      classes: component.classes,
      attributes: {...?component.attributes, if (renderedOpen) 'open': ''}
        ..removeWhere((name, _) => name == 'open' && !renderedOpen),
      events: {
        ...?component.events,
        'click': (event) {
          if (!mounted) return;
          element = event.currentTarget as web.HTMLDetailsElement;
          component.events?['click']?.call(event);
        },
        'toggle': (event) {
          if (!mounted) return;
          if (event.target == event.currentTarget) {
            element = event.currentTarget as web.HTMLDetailsElement;
            final value = element!.open;
            if (value != open) setState(() => open = value);
          }
          component.events?['toggle']?.call(event);
        },
      },
      children: component.children,
    );
  }
}

/// Phase 2 button. [variant] is primary, secondary, ghost or danger
/// (`primary: true` is kept for existing callers). An [iconName] is drawn
/// before the label; the label stays the button's whole text content.
Component button(
  String label,
  void Function()? onClick, {
  bool disabled = false,
  bool primary = false,
  String? classes,
  String? iconName,
  String? variant,
  bool small = false,
}) => dom.button(
  [if (iconName != null) icon(iconName), text(label)],
  type: dom.ButtonType.button,
  disabled: disabled || onClick == null,
  classes: [
    'btn',
    switch (variant ?? (primary ? 'primary' : 'secondary')) {
      'danger' => 'btn--secondary btn--danger',
      final name => 'btn--$name',
    },
    if (small) 'btn--sm',
    ?classes,
  ].join(' '),
  onClick: onClick,
);

/// Icon-only button; [label] is its accessible name, tooltip and text content.
Component iconButton(
  String label,
  String iconName,
  void Function()? onClick, {
  bool disabled = false,
  String? classes,
  bool small = false,
  Map<String, String> attributes = const {},
}) => dom.button(
  [
    icon(iconName),
    el('span', [text(label)], classes: 'visually-hidden'),
  ],
  type: dom.ButtonType.button,
  disabled: disabled || onClick == null,
  classes: [
    'btn btn--ghost btn--icon',
    if (small) 'btn--sm',
    ?classes,
  ].join(' '),
  attributes: {'title': label, ...attributes},
  onClick: onClick,
);

/// Phase 2 card: header with an optional icon, count and subtitle.
Component card(
  String title,
  List<Component> children, {
  List<Component> actions = const [],
  String? iconName,
  Object? count,
  String? subtitle,
  String? classes,
  String? id,
}) => el(
  'section',
  [
    el('div', [
      el('div', [
        el('h2', [
          if (iconName != null) icon(iconName),
          text(title),
          if (count != null) el('span', [text(count)], classes: 'count'),
        ], classes: 'card__title'),
        if (subtitle != null)
          el('p', [text(subtitle)], classes: 'card__subtitle'),
      ], classes: 'card__heading'),
      el('div', actions, classes: 'actions'),
    ], classes: 'card__header'),
    el('div', children, classes: 'card__body'),
  ],
  classes: ['card', ?classes].join(' '),
  attributes: {'id': ?id},
);

/// Phase 2 page header: overline, H1, subtitle and right-aligned actions.
Component pageHeader(
  String title, {
  String? overline,
  String? subtitle,
  List<Component> actions = const [],
}) => el('header', [
  el('div', [
    if (overline != null)
      el('p', [text(overline)], classes: 'page-header__overline'),
    el('h1', [text(title)], classes: 'page-header__title'),
    if (subtitle != null)
      el('p', [text(subtitle)], classes: 'page-header__subtitle'),
  ], classes: 'page-header__text'),
  if (actions.isNotEmpty) el('div', actions, classes: 'page-header__actions'),
], classes: 'page-header');

/// Status badge (Phase 2 pill with a tone dot drawn by CSS).
/// [shape] repeats the state without color: filled (default), hollow,
/// diamond, dash or none.
Component badge(String label, {String tone = 'neutral', String? shape}) => el(
  'span',
  [text(label)],
  classes: 'status',
  attributes: {'data-tone': tone, 'data-shape': ?shape},
);

/// Status dot whose shape repeats the state without color (WCAG 1.4.1):
/// working = filled, idle = ring, attention = diamond, offline = dash.
/// Keep the original runtime palette: idle green, working blue, attention amber.
String agentActivityTone(String activity) => switch (activity) {
  'idle' => 'success',
  'working' => 'info',
  'attention' => 'warning',
  _ => 'neutral',
};

Component statusDot(String activity, {String? tone}) => el(
  'span',
  [],
  classes: 'status-dot',
  attributes: {
    'data-tone': tone ?? agentActivityTone(activity),
    'data-activity': activity,
    // Pulsing marks live work. Carried on the dot itself so every caller —
    // including the sidebar agent list, which has no room for a role tile —
    // breathes while the agent is working.
    'data-live': '${activity == 'working'}',
    'data-shape': switch (activity) {
      'working' || 'idle' => 'filled',
      'attention' => 'diamond',
      'offline' => 'dash',
      _ => 'hollow',
    },
    'aria-hidden': 'true',
  },
);

/// Maps a free-text agent role onto the seven Phase 2 role tints.
String roleKey(Object? role) {
  final value = '${role ?? ''}'.toLowerCase();
  if (value.contains('lead') || value.contains('manager')) return 'lead';
  if (value.contains('review')) return 'reviewer';
  if (value.contains('qa') ||
      value.contains('quality') ||
      value.contains('test'))
    return 'qa';
  if (value.contains('coordinat')) return 'coordinator';
  if (value.contains('integration')) return 'integration';
  if (value.contains('worktree')) return 'worktree';
  return 'developer';
}

String initials(Object? name) {
  final words = '${name ?? ''}'
      .trim()
      .split(RegExp(r'\s+'))
      .where((word) => word.isNotEmpty)
      .toList();
  if (words.isEmpty) return '?';
  if (words.length == 1)
    return words.first
        .substring(0, words.first.length > 1 ? 2 : 1)
        .toUpperCase();
  return '${words[0][0]}${words[1][0]}'.toUpperCase();
}

/// Role-tinted initials tile; [live] draws the "working now" ring.
Component roleTile(
  Object? name, {
  Object? role,
  String size = '',
  bool live = false,
}) => el(
  'span',
  [text(initials(name))],
  classes: [
    'role-tile',
    if (size.isNotEmpty) 'role-tile--$size',
    'live-ring',
  ].join(' '),
  attributes: {
    'data-role': roleKey(role ?? name),
    'data-live': '$live',
    'aria-hidden': 'true',
  },
);

/// Phase 2 empty state: tinted icon, message and an optional action.
Component emptyState(
  String message, {
  String iconName = 'inbox',
  List<Component> actions = const [],
}) => el('div', [
  el('span', [icon(iconName)], classes: 'empty__icon'),
  el('p', [text(message)], classes: 'empty__title'),
  if (actions.isNotEmpty) el('div', actions, classes: 'actions'),
], classes: 'empty empty-state');

/// Stacked bar with a text legend; [segments] are (label, count, tone).
Component stackbar(
  List<(String, int, String)> segments, {
  String? label,
  bool legend = true,
}) {
  final shown = segments.where((segment) => segment.$2 > 0).toList();
  return el('div', [
    el(
      'div',
      [
        for (final segment in shown)
          el(
            'span',
            [],
            classes: 'stackbar__seg',
            attributes: {
              'data-tone': segment.$3,
              'style': '--seg: ${segment.$2}',
            },
          ),
      ],
      classes: 'stackbar',
      attributes: {'aria-hidden': 'true'},
    ),
    if (legend)
      el(
        'ul',
        [
          for (final segment in segments)
            el('li', [
              el(
                'span',
                [],
                classes: 'dot',
                attributes: {'data-tone': segment.$3},
              ),
              text(segment.$1),
              el('span', [text(segment.$2)], classes: 'legend__count'),
            ], classes: 'legend__item'),
        ],
        classes: 'legend',
        attributes: {'aria-label': ?label},
      ),
  ], classes: 'stackbar-block');
}

Component icon(String name, {String? classes}) => el(
  'svg',
  [
    el('use', [], attributes: {'href': 'icons.svg#i-$name'}),
  ],
  classes: ['icon', ?classes].join(' '),
  attributes: {
    'aria-hidden': 'true',
    'viewBox': '0 0 24 24',
    'focusable': 'false',
  },
);
Component logo() => el(
  'svg',
  [
    el(
      'rect',
      [],
      classes: 'logomark__bg',
      attributes: {'width': '28', 'height': '28', 'rx': '8'},
    ),
    el(
      'path',
      [],
      classes: 'logomark__links',
      attributes: {'d': 'M14 14V6.5M14 14l-6.5 4.5M14 14l6.5 4.5'},
    ),
    el('g', [
      for (final point in [
        (14, 14, 2.6),
        (14, 6.5, 2.0),
        (7.5, 18.5, 2.0),
        (20.5, 18.5, 2.0),
      ])
        el(
          'circle',
          [],
          attributes: {
            'cx': '${point.$1}',
            'cy': '${point.$2}',
            'r': '${point.$3}',
          },
        ),
    ], classes: 'logomark__nodes'),
  ],
  classes: 'logomark',
  attributes: {'aria-hidden': 'true', 'viewBox': '0 0 28 28'},
);
Component field(
  String label,
  String value,
  void Function(String) onInput, {
  String? id,
  String? placeholder,
  String? autocomplete,
  bool? spellcheck,
  bool? autocorrect,
  bool disabled = false,
  bool password = false,
  bool required = false,
}) => _Control(
  label,
  value,
  onInput,
  id: id,
  placeholder: placeholder,
  autocomplete: autocomplete,
  spellcheck: spellcheck,
  autocorrect: autocorrect,
  disabled: disabled,
  password: password,
  isRequired: required,
);
Component textarea(
  String label,
  String value,
  void Function(String) onInput, {
  String? id,
  String? placeholder,
  bool password = false,
  bool required = false,
  int rows = 5,
}) => _Control(
  label,
  value,
  onInput,
  id: id,
  placeholder: placeholder,
  isRequired: required,
  kind: 'textarea',
  rows: rows,
);
Component select(
  String label,
  String value,
  Map<String, String> options,
  void Function(String) onChange, {
  bool disabled = false,
}) => _Control(
  label,
  value,
  onChange,
  kind: 'select',
  options: options,
  disabled: disabled,
);

class _Control extends StatefulComponent {
  const _Control(
    this.label,
    this.value,
    this.change, {
    this.id,
    this.placeholder,
    this.autocomplete,
    this.spellcheck,
    this.autocorrect,
    this.password = false,
    this.isRequired = false,
    this.disabled = false,
    this.kind = 'input',
    this.rows = 5,
    this.options = const {},
  });
  final String label, value, kind;
  final String? id, placeholder, autocomplete;
  final bool? spellcheck, autocorrect;
  final bool password, isRequired, disabled;
  final int rows;
  final Map<String, String> options;
  final void Function(String) change;
  @override
  State<_Control> createState() => _ControlState();
}

class _ControlState extends State<_Control> {
  static int nextId = 0;
  late final String generatedId = 'herdr-control-${nextId++}';
  String get id => component.id ?? generatedId;
  @override
  void initState() {
    super.initState();
    scheduleMicrotask(syncValue);
  }

  @override
  void didUpdateComponent(_Control old) {
    super.didUpdateComponent(old);
    if (old.value != component.value || old.kind != component.kind)
      scheduleMicrotask(syncValue);
  }

  void syncValue() {
    if (!mounted) return;
    final node = web.document.getElementById(id);
    if (node == null) return;
    // Native inputs have a dirty live value independent of their HTML attribute.
    // Keep programmatic clears/provider changes in sync without moving the caret
    // when the user is simply typing the same value already in the DOM.
    if (component.kind == 'textarea') {
      final control = node as web.HTMLTextAreaElement;
      if (control.value != component.value) control.value = component.value;
    } else if (component.kind == 'select') {
      final control = node as web.HTMLSelectElement;
      if (control.value != component.value) control.value = component.value;
    } else {
      final control = node as web.HTMLInputElement;
      if (control.value != component.value) control.value = component.value;
    }
  }

  @override
  Component build(BuildContext context) => el('label', [
    el('span', [text(component.label)]),
    if (component.kind == 'textarea')
      dom.textarea(
        [text(component.value)],
        id: id,
        onInput: component.change,
        attributes: {
          'rows': '${component.rows}',
          if (component.isRequired) 'required': '',
          if (component.placeholder != null)
            'placeholder': component.placeholder!,
        },
      )
    else if (component.kind == 'select')
      el(
        'select',
        [
          for (final entry in component.options.entries)
            el(
              'option',
              [text(entry.value)],
              attributes: {
                'value': entry.key,
                if (entry.key == component.value) 'selected': '',
              },
            ),
        ],
        attributes: {'id': id, if (component.disabled) 'disabled': ''},
        events: {
          'change': (event) =>
              component.change((event.target as web.HTMLSelectElement).value),
        },
      )
    else
      dom.input<String>(
        id: id,
        value: component.value,
        type: component.password ? dom.InputType.password : dom.InputType.text,
        onInput: component.change,
        attributes: {
          if (component.isRequired) 'required': '',
          if (component.disabled) 'disabled': '',
          if (component.spellcheck != null)
            'spellcheck': '${component.spellcheck}',
          if (component.autocorrect != null)
            'autocorrect': component.autocorrect! ? 'on' : 'off',
          'autocomplete':
              component.autocomplete ??
              (component.password ? 'current-password' : 'off'),
          if (component.placeholder != null)
            'placeholder': component.placeholder!,
        },
      ),
  ], classes: 'field');
}

Component notice(Object? message, {bool error = false}) => el(
  'div',
  [text(message)],
  classes: error ? 'notice notice--danger' : 'notice',
  attributes: {'role': error ? 'alert' : 'status'},
);
Component table(List<String> headings, List<List<Component>> rows) =>
    el('div', [
      el('table', [
        el('thead', [
          el('tr', [
            for (final heading in headings)
              el('th', [text(heading)], attributes: {'scope': 'col'}),
          ]),
        ]),
        el('tbody', [
          for (final row in rows)
            el('tr', [
              for (final value in row) el('td', [value]),
            ]),
        ]),
      ]),
    ], classes: 'table-scroll');

final dialogChanges = ValueNotifier<int>(0);
final _dialogs = <_Dialog>[];
int _nextDialogId = 0;
final messages = ValueNotifier<String?>(null);
void toast(String message) => messages.value = message;
void refreshDialogs() => dialogChanges.value++;
void closeDialogs() {
  for (final dialog in _dialogs.reversed.toList()) {
    dialog.forceClose();
  }
}

/// Keeps the visible heading authoritative, with a valid name for custom bodies.
class DialogLabel {
  DialogLabel(this.headingId, {this.fallback = 'Dialog'});
  final String headingId;
  final String fallback;
  final headings = ValueNotifier<int>(0);
  String get fallbackId => '$headingId-fallback';
  String get id => headings.value > 0 ? headingId : fallbackId;
}

class _Dialog {
  _Dialog(
    this.title,
    this.content,
    this.close,
    this.forceClose,
    this.dismissible,
    this.label,
    this.canDismiss,
  );
  final String title;
  final Component Function() content;
  final void Function() close;
  final void Function() forceClose;
  final bool dismissible;
  final DialogLabel? label;
  final bool Function()? canDismiss;
  final String id = 'herdr-dialog-${_nextDialogId++}';
  web.HTMLDialogElement? element;
}

Future<T?> showDialog<T>(
  String title,
  Component Function(void Function(T?) close) content, {
  bool dismissible = true,
  DialogLabel? label,
  bool Function()? canDismiss,
}) {
  final result = Completer<T?>();
  final previouslyFocused = web.document.activeElement;
  // Headings mount while their dialog is building; rebuild after that frame.
  void headingChanged() => scheduleMicrotask(refreshDialogs);
  label?.headings.addListener(headingChanged);
  late _Dialog dialog;
  void close(T? value, {bool force = false}) {
    if (result.isCompleted) return;
    if (!force && value == null && canDismiss?.call() == false) return;
    _dialogs.remove(dialog);
    label?.headings.removeListener(headingChanged);
    result.complete(value);
    // Close before removing the component so the browser leaves the top layer
    // and can restore focus to its opener, including another modal dialog.
    dialog.element?.close();
    refreshDialogs();
    scheduleMicrotask(() {
      if (previouslyFocused?.isConnected ?? false) {
        if (previouslyFocused!.namespaceURI == 'http://www.w3.org/2000/svg') {
          (previouslyFocused as web.SVGElement).focus();
        } else if (previouslyFocused.namespaceURI ==
            'http://www.w3.org/1999/xhtml') {
          (previouslyFocused as web.HTMLElement).focus();
        }
      }
    });
  }

  dialog = _Dialog(
    title,
    () => content(close),
    () => close(null),
    () => close(null, force: true),
    dismissible,
    label,
    canDismiss,
  );
  _dialogs.add(dialog);
  refreshDialogs();
  return result.future;
}

Future<bool> confirm(
  String title,
  String message, {
  String confirmLabel = 'Confirm',
}) async =>
    await showDialog<bool>(
      title,
      (close) => el('div', [
        el('p', [text(message)]),
        el('div', [
          button('Cancel', () => close(false)),
          button(confirmLabel, () => close(true), primary: true),
        ], classes: 'actions'),
      ]),
    ) ==
    true;

/// Standard automatic-activation tab navigation. Attach to the tablist's
/// `keydown` event; callers keep ownership of selected state and tabindex.
void tabListKeyDown(web.Event event) {
  final key = event as web.KeyboardEvent;
  if (key.defaultPrevented ||
      key.isComposing ||
      key.altKey ||
      key.ctrlKey ||
      key.metaKey ||
      key.shiftKey) {
    return;
  }
  final list = event.currentTarget as web.HTMLElement;
  final target = event.target as web.Element?;
  if (target?.closest('[role="tablist"]') != list) return;
  final active = target?.closest('[role="tab"]');
  final vertical = list.getAttribute('aria-orientation') == 'vertical';
  final rtl = web.window.getComputedStyle(list).direction == 'rtl';
  final forward = vertical
      ? 'ArrowDown'
      : rtl
      ? 'ArrowLeft'
      : 'ArrowRight';
  final backward = vertical
      ? 'ArrowUp'
      : rtl
      ? 'ArrowRight'
      : 'ArrowLeft';
  if (!['Home', 'End', forward, backward].contains(key.key)) return;
  final nodes = list.querySelectorAll('[role="tab"]');
  final tabs = <web.HTMLElement>[];
  for (var index = 0; index < nodes.length; index++) {
    final tab = nodes.item(index) as web.HTMLElement;
    if (tab.closest('[role="tablist"]') == list &&
        !tab.matches(':disabled, [aria-disabled="true"]') &&
        tab.closest('[inert], [hidden]') == null &&
        tab.getClientRects().length > 0 &&
        web.window.getComputedStyle(tab).visibility != 'hidden') {
      tabs.add(tab);
    }
  }
  final current = tabs.indexWhere((tab) => tab == active);
  if (current < 0) return;
  final next = switch (key.key) {
    'Home' => 0,
    'End' => tabs.length - 1,
    _ => (current + (key.key == forward ? 1 : -1)) % tabs.length,
  };
  key.preventDefault();
  tabs[next].focus();
  tabs[next].click();
}

class _DialogView extends StatefulComponent {
  const _DialogView(this.dialog, {required super.key});
  final _Dialog dialog;
  @override
  State<_DialogView> createState() => _DialogViewState();
}

class _DialogViewState extends State<_DialogView> {
  Timer? presentation;
  int presentationAttempts = 0;

  @override
  void initState() {
    super.initState();
    // showModal needs a connected element. Start after the DOM commit, and
    // retry transient mounting/presentation failures without stranding a Future.
    presentation = Timer(Duration.zero, present);
  }

  void present() {
    presentation?.cancel();
    presentation = null;
    final dialog = component.dialog;
    if (!mounted || !_dialogs.contains(dialog)) return;
    presentationAttempts++;
    final element = web.document.getElementById(dialog.id);
    if (element?.tagName == 'DIALOG' && element!.isConnected) {
      final native = element as web.HTMLDialogElement;
      dialog.element = native;
      try {
        if (!native.matches(':modal')) {
          // A nonmodal show/open from an external caller must not prevent the
          // guarded dialog from returning to the browser's modal top layer.
          if (native.open) native.close();
          native.showModal();
        }
        presentationAttempts = 0;
        return;
      } catch (_) {
        // A detached node or a transient native presentation failure can be
        // retried after the next commit. Persistent failures cancel below.
      }
    }
    if (presentationAttempts < 5) {
      presentation = Timer(const Duration(milliseconds: 20), present);
    } else {
      toast('Unable to open the dialog. Please try again.');
      dialog.forceClose();
    }
  }

  @override
  void dispose() {
    presentation?.cancel();
    final dialog = component.dialog;
    if (dialog.element?.open == true) dialog.element!.close();
    dialog.element = null;
    super.dispose();
  }

  @override
  Component build(BuildContext context) {
    final dialog = component.dialog;
    final top = _dialogs.isNotEmpty && _dialogs.last == dialog;
    return el('div', [
      el(
        'dialog',
        [
          el('div', [
            // Only when there is one: an empty heading still takes space and
            // leaves a gap under the close button. Dialogs built through the
            // Flutter-shaped AlertDialog carry their own title instead.
            if (dialog.title.isNotEmpty) el('h2', [text(dialog.title)]),
            if (dialog.label case final label?)
              el(
                'h2',
                [text(label.fallback)],
                classes: 'visually-hidden',
                attributes: {'id': label.fallbackId},
              ),
            if (dialog.dismissible)
              button(
                'Close',
                dialog.close,
                disabled: dialog.canDismiss?.call() == false,
              ),
          ], classes: 'dialog__header'),
          dialog.content(),
        ],
        classes: 'dialog',
        attributes: {
          'id': dialog.id,
          // Jaspr reconciles actual DOM attributes on every build. Preserve a
          // currently open native dialog, never a previous presentation state.
          if (dialog.element?.open == true) 'open': '',
          'role': 'dialog',
          'aria-modal': '$top',
          if (dialog.label != null)
            'aria-labelledby': dialog.label!.id
          else
            'aria-label': dialog.title,
        },
        events: {
          'cancel': (event) {
            // Keep result completion and canDismiss authoritative. Native
            // modality handles Tab and background inertness for all controls.
            event.preventDefault();
            if (_dialogs.isNotEmpty &&
                _dialogs.last == dialog &&
                dialog.dismissible) {
              dialog.close();
            }
          },
          'close': (_) {
            if (!_dialogs.contains(dialog)) return;
            // close events are queued. A later presentation may already have
            // returned the same node to the top layer before this event runs.
            if (dialog.element?.matches(':modal') == true) return;
            dialog.close();
            // An external native close must not bypass a busy form's guard.
            if (_dialogs.contains(dialog)) {
              presentationAttempts = 0;
              present();
            }
          },
        },
      ),
    ], classes: 'dialog-layer');
  }
}

Component buildDialogs() => el('div', [
  for (final dialog in _dialogs) _DialogView(dialog, key: Key(dialog.id)),
], classes: 'dialog-host');
