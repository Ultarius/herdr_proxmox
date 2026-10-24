export 'package:meta/meta.dart';

typedef VoidCallback = void Function();

/// UI-independent observable used by the shared dashboard blocs.
/// Listener snapshots preserve additions/removals during notification.
class ValueNotifier<T> {
  ValueNotifier(this._value);
  T _value;
  final List<VoidCallback?> _listeners = [];
  int _depth = 0;
  bool _disposed = false;

  T get value => _value;
  set value(T next) {
    if (_value == next) return;
    _value = next;
    notifyListeners();
  }

  bool get hasListeners => _listeners.any((listener) => listener != null);

  void addListener(VoidCallback listener) {
    if (_disposed) throw StateError('Notifier is disposed.');
    _listeners.add(listener);
  }

  void removeListener(VoidCallback listener) {
    final index = _listeners.indexOf(listener);
    if (index < 0) return;
    if (_depth > 0) {
      _listeners[index] = null;
    } else {
      _listeners.removeAt(index);
    }
  }

  void notifyListeners() {
    if (_disposed) throw StateError('Notifier is disposed.');
    _depth++;
    final count = _listeners.length;
    try {
      for (var i = 0; i < count; i++) {
        try {
          _listeners[i]?.call();
        } catch (error, stack) {
          // As with Flutter's notifier, one observer cannot prevent others.
          debugPrint('$error\n$stack');
        }
      }
    } finally {
      _depth--;
      if (_depth == 0) _listeners.removeWhere((listener) => listener == null);
    }
  }

  void dispose() {
    assert(_depth == 0, 'Cannot dispose during notification.');
    _disposed = true;
    _listeners.clear();
  }
}

void debugPrint(String? message, {int? wrapWidth}) {
  if (message != null) print(message);
}
