import 'dart:math';

String newRequestId() {
  // Dart's web compiler truncates bitwise shifts to 32 bits, so
  // 1 << 32 can become zero. Keep the random bound a numeric literal.
  return '${DateTime.now().microsecondsSinceEpoch}-${Random.secure().nextInt(0x100000000)}';
}
