/// Native HTML presentation primitives for the task workflows. These retain
/// familiar constructor shapes so the gateway requests and review handlers can
/// be moved mechanically, while every rendered element is a Jaspr DOM node.
library;

import 'dart:async';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import 'package:web/web.dart' as web;
import '../ui.dart' as ui;
import '../navigation.dart' as navigation;
import 'package:herdr_dashboard_state/request_id.dart' as ids;
export 'dart:async';
export 'package:jaspr/client.dart';

enum CrossAxisAlignment { start, center, end, stretch }

enum MainAxisAlignment { start, center, end, spaceBetween }

enum MainAxisSize { min, max }

enum TextOverflow { ellipsis, visible }

enum ListTileControlAffinity { leading, trailing }

enum Alignment { topLeft, centerLeft, center }

class EdgeInsets {
  const EdgeInsets.all(double value)
    : top = value,
      right = value,
      bottom = value,
      left = value;
  const EdgeInsets.symmetric({double vertical = 0, double horizontal = 0})
    : top = vertical,
      bottom = vertical,
      left = horizontal,
      right = horizontal;
  const EdgeInsets.only({
    this.top = 0,
    this.right = 0,
    this.bottom = 0,
    this.left = 0,
  });
  static const zero = EdgeInsets.all(0);
  final double top, right, bottom, left;
  String get css => '${top}px ${right}px ${bottom}px ${left}px';
  bool get isZero => top == 0 && right == 0 && bottom == 0 && left == 0;
}

class TextStyle {
  const TextStyle({
    this.color,
    this.fontSize,
    this.fontWeight,
    this.fontFamily,
    this.heading,
  });
  final String? color, fontWeight, fontFamily, heading;
  final double? fontSize;
  TextStyle copyWith({
    String? color,
    double? fontSize,
    String? fontWeight,
    String? fontFamily,
  }) => TextStyle(
    color: color ?? this.color,
    fontSize: fontSize ?? this.fontSize,
    fontWeight: fontWeight ?? this.fontWeight,
    fontFamily: fontFamily ?? this.fontFamily,
    heading: heading,
  );
  String get css =>
      '${color == null ? '' : 'color:$color;'}${fontSize == null ? '' : 'font-size:${fontSize}px;'}${fontWeight == null ? '' : 'font-weight:$fontWeight;'}${fontFamily == null ? '' : 'font-family:$fontFamily;'}';
}

/// Typography and colors are CSS variables controlled by the Jaspr shell.
class Theme {
  static Theme of(BuildContext context) => Theme();
  final textTheme = _Typography();
  final colorScheme = _ColorScheme();
}

class _Typography {
  TextStyle get headlineLarge => const TextStyle(heading: 'h1');
  TextStyle get headlineMedium => const TextStyle(heading: 'h1');
  TextStyle get titleLarge => const TextStyle(heading: 'h2');
  TextStyle get titleMedium => const TextStyle(heading: 'h3');
  TextStyle get titleSmall => const TextStyle(heading: 'h4');
  TextStyle? get bodySmall =>
      const TextStyle(fontSize: 12, color: 'var(--color-text-secondary)');
}

class _ColorScheme {
  String get error => 'var(--color-danger-fg)';
  String get errorContainer => 'var(--color-danger-bg)';
  String get onSurfaceVariant => 'var(--color-text-secondary)';
}

class Text extends StatelessComponent {
  const Text(this.data, {super.key, this.style, this.maxLines, this.overflow});
  final String data;
  final TextStyle? style;
  final int? maxLines;
  final TextOverflow? overflow;
  @override
  Component build(BuildContext context) => Component.element(
    tag: style?.heading ?? 'span',
    classes: 'native-text',
    attributes: {
      'style':
          'margin:0;white-space:pre-wrap;overflow-wrap:anywhere;${style?.css ?? ''}',
    },
    children: [Component.text(data)],
  );
}

class SelectableText extends StatelessComponent {
  const SelectableText(this.data, {super.key, this.style});
  final String data;
  final TextStyle? style;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'pre',
    classes: 'evidence-text',
    attributes: {
      'style':
          'white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;${style?.css ?? ''}',
    },
    children: [Component.text(data)],
  );
}

class Column extends StatelessComponent {
  const Column({
    super.key,
    this.children = const [],
    this.crossAxisAlignment = CrossAxisAlignment.start,
    this.mainAxisSize = MainAxisSize.max,
    this.mainAxisAlignment = MainAxisAlignment.start,
  });
  final List<Component> children;
  final CrossAxisAlignment crossAxisAlignment;
  final MainAxisSize mainAxisSize;
  final MainAxisAlignment mainAxisAlignment;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    classes: 'native-column',
    attributes: {
      'style':
          'display:flex;flex-direction:column;gap:8px;min-width:0;align-items:${crossAxisAlignment == CrossAxisAlignment.start ? 'stretch' : crossAxisAlignment.name};',
    },
    children: children,
  );
}

class Row extends StatelessComponent {
  const Row({
    super.key,
    this.children = const [],
    this.crossAxisAlignment = CrossAxisAlignment.center,
    this.mainAxisAlignment = MainAxisAlignment.start,
    this.mainAxisSize = MainAxisSize.max,
  });
  final List<Component> children;
  final CrossAxisAlignment crossAxisAlignment;
  final MainAxisAlignment mainAxisAlignment;
  final MainAxisSize mainAxisSize;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    classes: 'native-row',
    attributes: {
      'style':
          'display:flex;gap:8px;align-items:${crossAxisAlignment == CrossAxisAlignment.start ? 'flex-start' : crossAxisAlignment.name};justify-content:${mainAxisAlignment == MainAxisAlignment.spaceBetween ? 'space-between' : mainAxisAlignment.name};flex-wrap:wrap;',
    },
    children: children,
  );
}

class Wrap extends StatelessComponent {
  const Wrap({
    super.key,
    this.children = const [],
    this.spacing = 8,
    this.runSpacing = 8,
  });
  final List<Component> children;
  final double spacing, runSpacing;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    classes: 'actions',
    attributes: {
      'style':
          'display:flex;flex-wrap:wrap;column-gap:${spacing}px;row-gap:${runSpacing}px;align-items:center;',
    },
    children: children,
  );
}

class ListView extends StatelessComponent {
  const ListView({
    super.key,
    this.children = const [],
    this.padding = EdgeInsets.zero,
  });
  final List<Component> children;
  final EdgeInsets padding;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'section',
    classes: 'native-page',
    attributes: {
      'style':
          'display:flex;flex-direction:column;gap:12px;min-width:0;min-height:0;overflow:visible;padding:${padding.css};',
    },
    children: children,
  );
}

class Padding extends StatelessComponent {
  const Padding({super.key, required this.padding, required this.child});
  final EdgeInsets padding;
  final Component child;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    attributes: {'style': 'padding:${padding.css};min-width:0;'},
    children: [child],
  );
}

class Expanded extends StatelessComponent {
  const Expanded({super.key, required this.child});
  final Component child;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    attributes: {'style': 'flex:1;min-width:0;'},
    children: [child],
  );
}

class SizedBox extends StatelessComponent {
  const SizedBox({super.key, this.width, this.height, this.child});
  const SizedBox.shrink({super.key}) : width = 0, height = 0, child = null;
  final double? width, height;
  final Component? child;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    attributes: {
      'style':
          'max-width:100%;${width == null ? '' : 'width:${width}px;'}${height == null ? '' : 'min-height:${height}px;'}',
    },
    children: [if (child != null) child!],
  );
}

class Align extends StatelessComponent {
  const Align({super.key, required this.alignment, required this.child});
  final Alignment alignment;
  final Component child;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    attributes: {
      'style':
          'align-self:${alignment == Alignment.center ? 'center' : 'flex-start'};max-width:100%;',
    },
    children: [child],
  );
}

class SingleChildScrollView extends StatelessComponent {
  const SingleChildScrollView({super.key, required this.child});
  final Component child;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    // The application main and the constrained dialog already own scrolling.
    // Unbounded content grows inside that owner instead of creating another
    // scrolling box. Constrained evidence outputs retain their own CSS limit.
    classes: 'native-scroll-content',
    attributes: {
      'style': 'overflow:visible;min-width:0;min-height:0;max-width:100%;',
    },
    children: [child],
  );
}

class Card extends StatelessComponent {
  const Card({super.key, required this.child, this.color});
  final Component child;
  final String? color;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'article',
    classes: 'card native-card',
    attributes: {if (color != null) 'style': 'background:$color;'},
    children: [child],
  );
}

class Icon extends StatelessComponent {
  const Icon(this.name, {super.key, this.size = 16});
  final String name;
  final double size;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'svg',
    classes: 'native-icon icon',
    attributes: {
      'aria-hidden': 'true',
      'viewBox': '0 0 24 24',
      'width': '$size',
      'height': '$size',
      'fill': 'none',
      'stroke': 'currentColor',
      'stroke-width': '1.8',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'style': 'flex-shrink:0;',
    },
    children: [
      if (name == 'download')
        Component.element(
          tag: 'path',
          attributes: {'d': 'M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5'},
          children: [],
        )
      else
        Component.element(
          tag: 'use',
          attributes: {
            'href':
                'icons.svg#i-${switch (name) {
                  'add' => 'plus',
                  'remove' => 'minus',
                  'refresh' => 'refresh-cw',
                  'arrow_back' => 'arrow-left',
                  'arrow_forward' => 'arrow-right',
                  'chevron_right' => 'chevron-right',
                  'play_arrow' => 'play',
                  'search' => 'search',
                  'clear' || 'cancel_outlined' => 'x',
                  'schedule' => 'clock',
                  'hourglass_empty' || 'hourglass_top' => 'hourglass',
                  'key' => 'key-round',
                  'verified_outlined' || 'task_alt' => 'circle-check',
                  'fact_check' || 'fact_check_outlined' => 'list-checks',
                  'build' || 'build_outlined' => 'wrench',
                  'groups' => 'users',
                  'support_agent' => 'bot',
                  'archive_outlined' => 'package',
                  'reorder' => 'list',
                  'copy_all_outlined' => 'layers',
                  'swap_horiz' => 'git-compare',
                  'history' => 'history',
                  'block' => 'ban',
                  'rate_review' => 'message-square',
                  'circle_outlined' => 'circle-dot',
                  _ => 'info',
                }}',
          },
          children: [],
        ),
    ],
  );
}

class _Button extends StatelessComponent {
  const _Button({
    super.key,
    required this.child,
    this.onPressed,
    this.primary = false,
    this.tooltip,
  });
  final Component child;
  final void Function()? onPressed;
  final bool primary;
  final String? tooltip;
  @override
  Component build(BuildContext context) => dom.button(
    [child],
    type: dom.ButtonType.button,
    disabled: onPressed == null,
    onClick: onPressed,
    classes: primary ? 'btn btn--primary' : 'btn btn--secondary',
    attributes: {if (tooltip != null) 'title': tooltip!},
  );
}

class TextButton extends _Button {
  const TextButton({super.key, required super.child, super.onPressed});
  TextButton.icon({
    Key? key,
    required Component icon,
    required Component label,
    void Function()? onPressed,
  }) : super(
         key: key,
         child: Row(children: [icon, label]),
         onPressed: onPressed,
       );
}

class FilledButton extends _Button {
  const FilledButton({super.key, required super.child, super.onPressed})
    : super(primary: true);
  FilledButton.icon({
    Key? key,
    required Component icon,
    required Component label,
    void Function()? onPressed,
  }) : super(
         key: key,
         child: Row(children: [icon, label]),
         onPressed: onPressed,
         primary: true,
       );
}

class OutlinedButton extends _Button {
  const OutlinedButton({super.key, required super.child, super.onPressed});
  OutlinedButton.icon({
    Key? key,
    required Component icon,
    required Component label,
    void Function()? onPressed,
  }) : super(
         key: key,
         child: Row(children: [icon, label]),
         onPressed: onPressed,
       );
}

class IconButton extends _Button {
  const IconButton({
    super.key,
    required Component icon,
    super.onPressed,
    super.tooltip,
  }) : super(child: icon);
  @override
  Component build(BuildContext context) => dom.button(
    [child, if (tooltip != null) Component.text(tooltip!)],
    type: dom.ButtonType.button,
    disabled: onPressed == null,
    onClick: onPressed,
    classes: 'btn btn--secondary',
    attributes: {if (tooltip != null) 'aria-label': tooltip!},
  );
}

class ChoiceChip extends StatelessComponent {
  const ChoiceChip({
    super.key,
    required this.label,
    required this.selected,
    this.onSelected,
  });
  final Component label;
  final bool selected;
  final void Function(bool)? onSelected;
  @override
  Component build(BuildContext context) => dom.button(
    [label],
    type: dom.ButtonType.button,
    classes: selected ? 'btn btn--secondary selected' : 'btn btn--secondary',
    disabled: onSelected == null,
    onClick: onSelected == null ? null : () => onSelected!(!selected),
    attributes: {'aria-pressed': '$selected'},
  );
}

class Chip extends StatelessComponent {
  const Chip({super.key, required this.label, this.avatar});
  final Component label;
  final Component? avatar;
  @override
  Component build(BuildContext context) =>
      dom.span(classes: 'status', [if (avatar != null) avatar!, label]);
}

class LinearProgressIndicator extends StatelessComponent {
  const LinearProgressIndicator({
    super.key,
    this.value,
    this.label = 'Loading',
  });

  /// 0..1 when the progress is known; null renders the indeterminate bar.
  final double? value;
  final String label;
  @override
  Component build(BuildContext context) => Component.element(
    tag: 'progress',
    attributes: {
      'aria-label': label,
      if (value != null && value!.isFinite)
        'value': '${(value!.clamp(0.0, 1.0) * 100).round()}',
      if (value != null && value!.isFinite) 'max': '100',
    },
    children: [],
  );
}

class ExpansionTile extends StatefulComponent {
  const ExpansionTile({
    super.key,
    required this.title,
    this.subtitle,
    this.children = const [],
    this.childrenPadding = EdgeInsets.zero,
    this.initiallyExpanded = false,
    this.onExpansionChanged,
    this.expandedCrossAxisAlignment = CrossAxisAlignment.start,
  });
  final Component title;
  final Component? subtitle;
  final List<Component> children;
  final EdgeInsets childrenPadding;
  final bool initiallyExpanded;
  final void Function(bool)? onExpansionChanged;
  final CrossAxisAlignment expandedCrossAxisAlignment;
  @override
  State<ExpansionTile> createState() => _ExpansionState();
}

class _ExpansionState extends State<ExpansionTile> {
  late bool expanded;
  @override
  void initState() {
    super.initState();
    expanded = component.initiallyExpanded;
  }

  @override
  Component build(BuildContext context) => Component.element(
    tag: 'section',
    classes: 'native-expansion',
    children: [
      dom.button(
        [component.title, if (component.subtitle != null) component.subtitle!],
        type: dom.ButtonType.button,
        classes: 'expansion-heading',
        attributes: {'aria-expanded': '$expanded'},
        onClick: () {
          setState(() => expanded = !expanded);
          component.onExpansionChanged?.call(expanded);
        },
      ),
      if (expanded)
        // A body hook for page styles; zero padding stays unstyled inline so
        // a page can pad it (the tasks page does).
        Component.element(
          tag: 'div',
          classes: 'native-expansion__body',
          attributes: {
            'style': component.childrenPadding.isZero
                ? 'min-width:0;'
                : 'padding:${component.childrenPadding.css};min-width:0;',
          },
          children: [Column(children: component.children)],
        ),
    ],
  );
}

class ListTile extends StatelessComponent {
  const ListTile({
    super.key,
    required this.title,
    this.subtitle,
    this.leading,
    this.trailing,
    this.onTap,
    this.contentPadding = EdgeInsets.zero,
  });
  final Component title;
  final Component? subtitle, leading, trailing;
  final void Function()? onTap;
  final EdgeInsets contentPadding;
  @override
  Component build(BuildContext context) => Padding(
    padding: contentPadding,
    child: Row(
      children: [
        if (leading != null) leading!,
        Expanded(
          child: onTap == null
              ? Column(children: [title, if (subtitle != null) subtitle!])
              : _Button(
                  onPressed: onTap,
                  child: Column(
                    children: [title, if (subtitle != null) subtitle!],
                  ),
                ),
        ),
        if (trailing != null) trailing!,
      ],
    ),
  );
}

class TextEditingValue {
  const TextEditingValue(this.text);
  final String text;
}

class TextEditingController {
  TextEditingController({String text = ''}) : _text = text;
  String _text;
  final _listeners = <void Function()>[];
  String get text => _text;
  set text(String value) {
    if (_text == value) return;
    _text = value;
    for (final listener in List.of(_listeners)) {
      listener();
    }
  }

  TextEditingValue get value => TextEditingValue(_text);
  void addListener(void Function() listener) => _listeners.add(listener);
  void removeListener(void Function() listener) => _listeners.remove(listener);
  void clear() => text = '';
  void dispose() => _listeners.clear();
}

class ValueListenableBuilder<T> extends StatefulComponent {
  const ValueListenableBuilder({
    super.key,
    required this.valueListenable,
    required this.builder,
  });
  final TextEditingController valueListenable;
  final Component Function(BuildContext, T, Component?) builder;
  @override
  State<ValueListenableBuilder<T>> createState() => _ValueState<T>();
}

class _ValueState<T> extends State<ValueListenableBuilder<T>> {
  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    component.valueListenable.addListener(changed);
  }

  @override
  void didUpdateComponent(ValueListenableBuilder<T> old) {
    super.didUpdateComponent(old);
    if (old.valueListenable != component.valueListenable) {
      old.valueListenable.removeListener(changed);
      component.valueListenable.addListener(changed);
    }
  }

  @override
  void dispose() {
    component.valueListenable.removeListener(changed);
    super.dispose();
  }

  @override
  Component build(BuildContext context) =>
      component.builder(context, component.valueListenable.value as T, null);
}

class InputDecoration {
  const InputDecoration({
    this.labelText,
    this.hintText,
    this.helperText,
    this.prefixIcon,
    this.suffixIcon,
  });
  final String? labelText, hintText, helperText;
  final Component? prefixIcon, suffixIcon;
}

class FormKey {
  FormState? currentState;
}

class Form extends StatefulComponent {
  Form({required FormKey key, required this.child}) : formKey = key;
  final FormKey formKey;
  final Component child;
  @override
  State<Form> createState() => FormState();
}

class FormState extends State<Form> {
  final validators = <bool Function()>{};
  @override
  void initState() {
    super.initState();
    component.formKey.currentState = this;
  }

  bool validate() {
    var valid = true;
    for (final validator in List.of(validators)) {
      if (!validator()) valid = false;
    }
    return valid;
  }

  @override
  void dispose() {
    component.formKey.currentState = null;
    validators.clear();
    super.dispose();
  }

  @override
  Component build(BuildContext context) => Component.element(
    tag: 'div',
    classes: 'native-form',
    children: [component.child],
  );
}

class TextField extends StatefulComponent {
  const TextField({
    super.key,
    this.controller,
    this.onChanged,
    this.decoration = const InputDecoration(),
    this.maxLength,
    this.minLines,
    this.maxLines,
    this.obscureText = false,
    this.readOnly = false,
    this.enableSuggestions = true,
    this.autocorrect = true,
    this.validator,
    this.buildCounter,
  });
  final TextEditingController? controller;
  final void Function(String)? onChanged;
  final InputDecoration decoration;
  final int? maxLength, minLines, maxLines;
  final bool obscureText, readOnly, enableSuggestions, autocorrect;
  final String? Function(String?)? validator;
  final dynamic buildCounter;
  @override
  State<TextField> createState() => _TextFieldState();
}

class TextFormField extends TextField {
  const TextFormField({
    super.key,
    super.controller,
    super.onChanged,
    super.decoration,
    super.maxLength,
    super.minLines,
    super.maxLines,
    super.obscureText,
    super.readOnly,
    super.validator,
    super.buildCounter,
  });
}

class _TextFieldState extends State<TextField> {
  String local = '', id = '', validationError = '';
  FormState? form;
  static int count = 0;
  String get value => component.controller?.text ?? local;
  @override
  void initState() {
    super.initState();
    id = 'native-field-${++count}';
    component.controller?.addListener(changed);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    form?.validators.remove(validate);
    form = context.findAncestorStateOfType<FormState>();
    form?.validators.add(validate);
  }

  @override
  void didUpdateComponent(TextField old) {
    super.didUpdateComponent(old);
    if (old.controller != component.controller) {
      old.controller?.removeListener(changed);
      component.controller?.addListener(changed);
    }
  }

  void changed() {
    if (!mounted) return;
    setState(() {});
    // Editing makes a textarea's value independent of its text child. Keep
    // programmatic controller changes (such as excerpt opt-in) visible too.
    scheduleMicrotask(() {
      if (!mounted) return;
      final node = web.document.getElementById(id);
      if (node is web.HTMLTextAreaElement && node.value != value) {
        node.value = value;
      }
    });
  }

  bool validate() {
    final error = component.validator?.call(value);
    setState(() => validationError = error ?? '');
    return error == null;
  }

  void input(String text) {
    if (component.readOnly) return;
    local = text;
    component.controller?.text = text;
    component.onChanged?.call(text);
  }

  @override
  void dispose() {
    form?.validators.remove(validate);
    component.controller?.removeListener(changed);
    super.dispose();
  }

  @override
  Component build(BuildContext context) => dom.div(classes: 'field', [
    if (component.decoration.labelText != null)
      dom.label(htmlFor: id, [Component.text(component.decoration.labelText!)]),
    if (component.decoration.prefixIcon != null)
      component.decoration.prefixIcon!,
    if ((component.minLines ?? 1) > 1 || (component.maxLines ?? 1) > 1)
      dom.textarea(
        [Component.text(value)],
        id: id,
        rows: component.minLines ?? 4,
        placeholder: component.decoration.hintText,
        onInput: input,
        attributes: {
          if (component.readOnly) 'readonly': '',
          'value': value,
          if (component.maxLength != null)
            'maxlength': '${component.maxLength}',
          'aria-invalid': '${validationError.isNotEmpty}',
        },
      )
    else
      dom.input<String>(
        id: id,
        type: component.obscureText
            ? dom.InputType.password
            : dom.InputType.text,
        value: value,
        onInput: input,
        attributes: {
          if (component.readOnly) 'readonly': '',
          if (component.maxLength != null)
            'maxlength': '${component.maxLength}',
          'autocomplete': component.obscureText ? 'new-password' : 'off',
          'spellcheck':
              '${!component.obscureText && component.enableSuggestions}',
          'aria-invalid': '${validationError.isNotEmpty}',
          if (component.decoration.hintText != null)
            'placeholder': component.decoration.hintText!,
        },
      ),
    if (component.decoration.suffixIcon != null)
      component.decoration.suffixIcon!,
    if (component.decoration.helperText != null)
      Text(component.decoration.helperText!),
    if (validationError.isNotEmpty)
      dom.p(
        classes: 'error',
        attributes: {'role': 'alert'},
        [Component.text(validationError)],
      ),
  ]);
}

class DropdownMenuItem<T> extends StatelessComponent {
  const DropdownMenuItem({super.key, required this.value, required this.child});
  final T value;
  final Component child;
  @override
  Component build(BuildContext context) => dom.option(value: '$value', [
    child is Text ? Component.text((child as Text).data) : child,
  ]);
}

class DropdownButton<T> extends StatefulComponent {
  const DropdownButton({
    super.key,
    this.value,
    this.initialValue,
    required this.items,
    this.onChanged,
    this.isExpanded = false,
    this.hint,
    this.decoration = const InputDecoration(),
    this.validator,
  });
  final T? value, initialValue;
  final List<DropdownMenuItem<T>> items;
  final void Function(T?)? onChanged;
  final bool isExpanded;
  final Component? hint;
  final InputDecoration decoration;
  final String? Function(T?)? validator;
  @override
  State<DropdownButton<T>> createState() => _DropdownState<T>();
}

class DropdownButtonFormField<T> extends DropdownButton<T> {
  const DropdownButtonFormField({
    super.key,
    super.value,
    super.initialValue,
    required super.items,
    super.onChanged,
    super.isExpanded,
    super.hint,
    super.decoration,
    super.validator,
  });
}

class _DropdownState<T> extends State<DropdownButton<T>> {
  T? selected;
  String validationError = '', id = '';
  FormState? form;
  static int count = 0;
  @override
  void initState() {
    super.initState();
    selected = component.initialValue ?? component.value;
    id = 'native-select-${++count}';
  }

  @override
  void didUpdateComponent(DropdownButton<T> old) {
    super.didUpdateComponent(old);
    if (old.value != component.value ||
        old.initialValue != component.initialValue)
      selected = component.value ?? component.initialValue;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    form?.validators.remove(validate);
    form = context.findAncestorStateOfType<FormState>();
    form?.validators.add(validate);
  }

  bool validate() {
    final error = component.validator?.call(selected);
    setState(() => validationError = error ?? '');
    return error == null;
  }

  @override
  void dispose() {
    form?.validators.remove(validate);
    super.dispose();
  }

  @override
  Component build(BuildContext context) => dom.div(classes: 'field', [
    if (component.decoration.labelText != null)
      dom.label(htmlFor: id, [Component.text(component.decoration.labelText!)]),
    dom.select(
      id: id,
      value: selected == null ? '' : '$selected',
      disabled: component.onChanged == null,
      attributes: {'aria-invalid': '${validationError.isNotEmpty}'},
      onChange: (values) {
        final value = values.firstOrNull;
        final match = component.items
            .where((item) => '${item.value}' == value)
            .firstOrNull;
        setState(() => selected = match?.value);
        component.onChanged?.call(selected);
      },
      [
        if (selected == null)
          dom.option(value: '', [component.hint ?? const Text('Select…')]),
        for (final item in component.items)
          dom.option(value: '${item.value}', selected: item.value == selected, [
            item.child is Text
                ? Component.text((item.child as Text).data)
                : item.child,
          ]),
      ],
    ),
    if (component.decoration.helperText != null)
      Text(component.decoration.helperText!),
    if (validationError.isNotEmpty)
      dom.p(
        classes: 'error',
        attributes: {'role': 'alert'},
        [Component.text(validationError)],
      ),
  ]);
}

class CheckboxListTile extends StatelessComponent {
  const CheckboxListTile({
    super.key,
    required this.value,
    this.onChanged,
    required this.title,
    this.subtitle,
    this.controlAffinity = ListTileControlAffinity.leading,
    this.dense = false,
  });
  final bool value, dense;
  final void Function(bool?)? onChanged;
  final Component title;
  final Component? subtitle;
  final ListTileControlAffinity controlAffinity;
  @override
  Component build(BuildContext context) =>
      dom.label(classes: 'checkbox-field', [
        dom.input<bool>(
          type: dom.InputType.checkbox,
          checked: value,
          disabled: onChanged == null,
          onChange: (v) => onChanged?.call(v),
        ),
        title,
        if (subtitle != null) subtitle!,
      ]);
}

class SwitchListTile extends StatelessComponent {
  const SwitchListTile({
    super.key,
    required this.value,
    this.onChanged,
    required this.title,
    this.subtitle,
  });
  final bool value;
  final void Function(bool)? onChanged;
  final Component title;
  final Component? subtitle;
  @override
  Component build(BuildContext context) => CheckboxListTile(
    value: value,
    onChanged: onChanged == null ? null : (v) => onChanged!(v == true),
    title: title,
    subtitle: subtitle,
  );
}

class Builder extends StatelessComponent {
  const Builder({super.key, required this.builder});
  final Component Function(BuildContext) builder;
  @override
  Component build(BuildContext context) => builder(context);
}

class StatefulBuilder extends StatefulComponent {
  const StatefulBuilder({super.key, required this.builder});
  final Component Function(BuildContext, void Function(void Function()))
  builder;
  @override
  State<StatefulBuilder> createState() => _BuilderState();
}

class _BuilderState extends State<StatefulBuilder> {
  @override
  Component build(BuildContext context) => component.builder(context, setState);
}

class AlertDialog extends StatelessComponent {
  const AlertDialog({
    super.key,
    required this.title,
    this.content,
    this.actions = const [],
  });
  final Component title;
  final Component? content;
  final List<Component> actions;
  @override
  Component build(BuildContext context) => Column(
    children: [
      // Styled as the dialog's heading rather than body text: this component
      // renders inside the dialog body, so an unstyled title reads as the first
      // paragraph of the content.
      _DialogHeading(title),
      if (content != null) content!,
      Wrap(children: actions),
    ],
  );
}

class _DialogHeading extends StatefulComponent {
  const _DialogHeading(this.title);
  final Component title;
  @override
  State<_DialogHeading> createState() => _DialogHeadingState();
}

class _DialogHeadingState extends State<_DialogHeading> {
  ui.DialogLabel? label;
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final next = context
        .dependOnInheritedComponentOfExactType<_DialogScope>()
        ?.label;
    if (next != label) {
      label?.headings.value--;
      label = next;
      label?.headings.value++;
    }
  }

  @override
  void dispose() {
    label?.headings.value--;
    super.dispose();
  }

  @override
  Component build(BuildContext context) =>
      dom.h2([component.title], id: label?.headingId, classes: 'dialog__title');
}

class _DialogScope extends InheritedComponent {
  const _DialogScope({
    required super.child,
    required this.close,
    required this.completed,
    required this.label,
  });
  final void Function(dynamic) close;
  final Future<void> completed;
  final ui.DialogLabel label;
  @override
  bool updateShouldNotify(_DialogScope oldComponent) =>
      oldComponent.label != label;
}

Future<T?> showDialog<T>({
  required BuildContext context,
  required Component Function(BuildContext) builder,
  String semanticLabel = 'Dialog',
}) async {
  final completed = Completer<void>();
  final label = ui.DialogLabel(
    'dialog-title-${ids.newRequestId()}',
    fallback: semanticLabel,
  );
  try {
    return await ui.showDialog<T>(
      '',
      (close) => _DialogScope(
        close: (value) => close(value as T?),
        completed: completed.future,
        label: label,
        child: Builder(builder: builder),
      ),
      label: label,
    );
  } finally {
    if (!completed.isCompleted) completed.complete();
  }
}

class DialogRoute<T> {
  DialogRoute({required this.context, required this.builder});
  final BuildContext context;
  final Component Function(BuildContext) builder;
  final _completed = Completer<void>();
  Future<void> get completed => _completed.future;
}

class Navigator {
  static Navigator of(BuildContext context, {bool rootNavigator = false}) =>
      Navigator();
  static void pop<T>(BuildContext context, [T? value]) {
    final scope =
        context
                .getElementForInheritedComponentOfExactType<_DialogScope>()
                ?.component
            as _DialogScope?;
    scope?.close(value);
  }

  Future<T?> push<T>(DialogRoute<T> route) async {
    try {
      return await showDialog<T>(
        context: route.context,
        builder: route.builder,
      );
    } finally {
      if (!route._completed.isCompleted) route._completed.complete();
    }
  }
}

class ModalRoute {
  ModalRoute(this.completed);
  final Future<void> completed;
  static ModalRoute? of(BuildContext context) {
    final scope =
        context
                .getElementForInheritedComponentOfExactType<_DialogScope>()
                ?.component
            as _DialogScope?;
    return scope == null ? null : ModalRoute(scope.completed);
  }
}

class SnackBar {
  const SnackBar({required this.content});
  final Component content;
}

class ScaffoldMessenger {
  static ScaffoldMessenger of(BuildContext context) => ScaffoldMessenger();
  void showSnackBar(SnackBar snack) {
    if (snack.content is Text) ui.toast((snack.content as Text).data);
  }
}

class AppCoordinator {
  const AppCoordinator();
  void push(_Route route) => navigation.navigate(route.path);
  void replace(_Route route) => navigation.navigate(route.path, replace: true);
  void navigate(_Route route) => navigation.navigate(route.path);
}

abstract class _Route {
  String get path;
}

class TaskDetailRoute extends _Route {
  TaskDetailRoute(this.id);
  final String id;
  @override
  String get path => '/tasks/${Uri.encodeComponent(id)}';
}

class TasksRoute extends _Route {
  @override
  String get path => '/tasks';
}

class BuildsRoute extends _Route {
  @override
  String get path => '/builds';
}

class GroupRoute extends _Route {
  GroupRoute(this.id);
  final String id;
  @override
  String get path => '/groups/${Uri.encodeComponent(id)}';
}

class OrganizationRoute extends _Route {
  OrganizationRoute([this.section = '']);
  final String section;
  @override
  String get path =>
      '/organization${section.isEmpty ? '' : '?view=${Uri.encodeQueryComponent(section)}'}';
}
