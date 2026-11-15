import 'dart:async';
import 'package:jaspr/client.dart';
import 'package:jaspr/dom.dart' as dom;
import '../services.dart';
import '../ui.dart' show el;
import 'package:herdr_dashboard_state/feature_bloc.dart';
export 'package:herdr_dashboard_state/feature_bloc.dart';
export 'dart:async';
export 'package:jaspr/client.dart';
export '../blocs.dart';
export '../services.dart';
export '../ui.dart';

abstract class OperationsState<T extends StatefulComponent> extends State<T> {
  DashboardBloc get connection => dashboardConnection;
  bool busy = false;
  String? message;
  String? error;
  bool get admin => connection.operator.value['role'] == 'admin';
  bool valid(int epoch) =>
      mounted && connection.state.connected && epoch == connection.generation;
  Future<void> perform(Future<void> Function(int epoch) work) async {
    if (busy || !connection.state.connected) return;
    final epoch = connection.generation;
    setState(() {
      busy = true;
      error = null;
      message = null;
    });
    try {
      await work(epoch);
    } catch (e) {
      if (valid(epoch))
        setState(() => error = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (valid(epoch)) setState(() => busy = false);
    }
  }

  Component feedback() => dom.div(
    attributes: {'aria-live': 'polite'},
    [
      if (busy)
        dom.p(attributes: {'role': 'status'}, [Component.text('Working…')]),
      if (message != null) dom.p([Component.text(message!)]),
      if (error != null)
        dom.p(
          classes: 'error',
          attributes: {'role': 'alert'},
          [Component.text(error!)],
        ),
    ],
  );
}

/// Projects Juice feedback into the existing DOM presentation helpers.
/// Server values and operation ownership remain in the feature bloc.
abstract class FeatureOperationsState<T extends StatefulComponent>
    extends OperationsState<T> {
  late final FeatureBloc feature;
  StreamSubscription? _featureSubscription;
  void bindFeature(
    FeatureBloc bloc, {
    String? Function(FeatureState)? errorFor,
    void Function()? onChanged,
  }) {
    feature = bloc;
    _featureSubscription = bloc.stream.listen((_) {
      if (!mounted) return;
      setState(() {
        busy = bloc.state.busy || bloc.state.loading;
        error = errorFor == null ? bloc.state.error : errorFor(bloc.state);
        message = bloc.state.message;
        onChanged?.call();
      });
    });
  }

  @override
  void dispose() {
    _featureSubscription?.cancel();
    unawaited(feature.close());
    super.dispose();
  }
}

Component paragraph(Object? value) => dom.p([Component.text('${value ?? ''}')]);
Component codeText(Object? value, {String label = 'Text output'}) => dom.pre(
  classes: 'code-output',
  attributes: {'tabindex': '0', 'aria-label': label},
  [Component.text('${value ?? ''}')],
);
Component actions(List<Component> children) =>
    dom.div(classes: 'actions', children);
Component stack(List<Component> children) =>
    dom.div(classes: 'stack', children);
Component disclosure(String title, List<Component> children) => el('details', [
  dom.summary([Component.text(title)]),
  ...children,
]);
Component toggle(
  String label,
  bool checked,
  void Function(bool)? change, {
  bool disabled = false,
}) => dom.label(classes: 'toggle', [
  dom.input<bool>(
    type: dom.InputType.checkbox,
    checked: checked,
    disabled: disabled || change == null,
    onChange: change,
  ),
  Component.text(label),
]);
String displayTime(dynamic value) =>
    DateTime.tryParse('$value')?.toLocal().toString() ??
    (value == null ? 'Not checked yet' : '$value');
