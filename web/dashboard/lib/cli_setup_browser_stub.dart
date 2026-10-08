import 'package:flutter/services.dart';

Future<void> copySetupUrl(String url) =>
    Clipboard.setData(ClipboardData(text: url));

void openSetupUrl(String url) {}
