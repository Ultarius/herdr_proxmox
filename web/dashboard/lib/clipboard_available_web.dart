import 'package:web/web.dart' as web;

// Flutter's platform clipboard response can remain pending on an insecure
// embedded browser. Skip it on HTTP rather than waiting for an exception.
bool clipboardAvailable() => web.window.isSecureContext;
