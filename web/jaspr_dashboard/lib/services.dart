import 'dart:js_interop';
import 'dart:typed_data';
import 'package:juice/juice.dart';
import 'package:web/web.dart' as web;
import 'blocs.dart';
import 'package:herdr_dashboard_state/request_id.dart' as ids;
import 'platform/artifact_download_web.dart' as artifacts;
import 'platform/configuration_file_web.dart' as files;

export 'blocs.dart';
export 'package:herdr_dashboard_state/integration.dart';

DashboardBloc get dashboardConnection => BlocScope.get<DashboardBloc>();
OrganizationBloc? _organization;
int _organizationEpoch = -1;
OrganizationBloc get organizationConnection {
  final connection = dashboardConnection;
  if (_organizationEpoch != connection.generation || _organization == null) {
    _organization?.dispose();
    _organization = OrganizationBloc(connection);
    _organizationEpoch = connection.generation;
  }
  return _organization!;
}

void initializeServices() {
  BlocScope.register<DashboardBloc>(
    () => DashboardBloc(),
    lifecycle: BlocLifecycle.permanent,
  );
}

String requestId() => ids.newRequestId();
Future<void> downloadText(String content, String filename) =>
    artifacts.downloadArtifact(content, filename);
Future<void> downloadTaskBackupLink(Uri uri) =>
    artifacts.downloadTaskBackupLink(uri);
Future<void> downloadBytes(
  Uint8List content,
  String filename, {
  String contentType = 'application/gzip',
}) => artifacts.downloadBinaryArtifact(
  content,
  filename,
  contentType: contentType,
);
Future<String?> pickConfiguration() => files.readConfigurationFile();
Future<String> copyText(
  String value, {
  String filename = 'copied-text.txt',
}) async {
  try {
    await web.window.navigator.clipboard.writeText(value).toDart;
    return 'Copied.';
  } catch (_) {
    await downloadText(value, filename);
    return 'Clipboard unavailable; downloaded instead.';
  }
}

void openExternal(String url) {
  final uri = Uri.tryParse(url);
  if (uri == null || !['http', 'https'].contains(uri.scheme)) return;
  web.window.open(url, '_blank', 'noopener,noreferrer');
}
