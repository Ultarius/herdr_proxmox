import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/ui_colors.dart';

void main() {
  test(
    'secondary text meets normal-text contrast on dark dashboard surfaces',
    () {
      for (final surface in [0xff0b0b0b, 0xff141414, 0xff1a1a1a, 0xff222222]) {
        final contrast =
            (secondaryTextColor.computeLuminance() + .05) /
            (Color(surface).computeLuminance() + .05);
        expect(contrast, greaterThanOrEqualTo(4.5), reason: 'Surface $surface');
      }
    },
  );
}
