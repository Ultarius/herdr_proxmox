import 'package:juice/juice.dart';
import 'package:herdr_dashboard_state/feature_bloc.dart';
export 'package:herdr_dashboard_state/feature_bloc.dart';

/// Owns a page's Juice subscription; presentation fields stay in the widget.
mixin FeatureView<T extends StatefulWidget> on State<T> {
  late final FeatureBloc feature;
  StreamSubscription? _featureSubscription;
  void bindFeature(FeatureBloc bloc, {void Function()? onChanged}) {
    feature = bloc;
    _featureSubscription = bloc.stream.listen((_) {
      if (mounted)
        setState(() {
          onChanged?.call();
        });
    });
  }

  bool get featureBusy => feature.state.busy || feature.state.loading;
  String? get featureError => feature.state.error;
  String? get featureMessage => feature.state.message;
  @override
  void dispose() {
    _featureSubscription?.cancel();
    unawaited(feature.close());
    super.dispose();
  }
}
