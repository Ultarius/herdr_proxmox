import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:juice/juice.dart';

Future<void> withDashboardConnection(
  WidgetTester tester,
  DashboardBloc connection,
  Future<void> Function() body,
) async {
  var cleaned = false;
  Future<void> cleanup() async {
    if (cleaned) return;
    cleaned = true;
    try {
      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      try {
        await connection.disconnect();
      } finally {
        await BlocScope.reset();
      }
    }
  }

  addTearDown(cleanup);
  try {
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('admin');
    await body();
  } finally {
    // Flutter checks pending timers before addTearDown. Cancel polling here
    // even on assertion failure; teardown remains an idempotent safety net.
    await cleanup();
  }
}

Future<void> pumpUntil(
  WidgetTester tester,
  bool Function() ready, {
  String reason = 'Expected UI state did not appear',
}) async {
  for (var attempt = 0; attempt < 40; attempt++) {
    await tester.pump(const Duration(milliseconds: 50));
    if (ready()) return;
  }
  fail(reason);
}

Future<void> clickText(WidgetTester tester, String label) async {
  final target = find.text(label).first;
  await tester.ensureVisible(target);
  await tester.pump();
  await tester.tap(target);
  await tester.pump();
}
