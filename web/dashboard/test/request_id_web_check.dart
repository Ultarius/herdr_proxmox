// Run after dart compile js, with Node, to catch web-only integer truncation.
import '../lib/request_id.dart';

void main() {
  final seen = <String>{};
  for (var i = 0; i < 1000; i++) {
    final id = newRequestId();
    if (!RegExp(r'^\d+-\d+$').hasMatch(id) || id.length > 64 || !seen.add(id)) {
      throw StateError('Invalid or duplicate request ID: $id');
    }
    final random = int.parse(id.split('-').last);
    if (random < 0 || random >= 0x100000000) {
      throw StateError('Request entropy outside the web-safe bound');
    }
  }
  print('Compiled JavaScript request IDs passed');
}
