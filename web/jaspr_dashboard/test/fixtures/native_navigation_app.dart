// Standalone production-app fixture for trusted Chrome keyboard interactions.
import 'dart:convert';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'package:herdr_jaspr_dashboard/app.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_jaspr_dashboard/ui.dart';
import 'package:http/testing.dart';
import 'package:http/http.dart' as http;
import 'package:juice/juice.dart';
import 'package:web/web.dart' as web;
import 'dashboard_pages.dart';

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  var inspections = 0;
  final connection = DashboardBloc(
    client: MockClient((request) async {
      final response = dashboardPageResponse(request);
      if (request.url.path != '/api/organizations/inspect') return response;
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      web.window.setProperty(
        'nativeInspectionRequests'.toJS,
        (++inspections).toJS,
      );
      data['output'] = '${data['output']}\r\nLive refresh $inspections';
      return http.Response(
        jsonEncode(data),
        response.statusCode,
        headers: response.headers,
      );
    }),
  );
  BlocScope.register<DashboardBloc>(() => connection);
  runApp(const DashboardApp());
}
