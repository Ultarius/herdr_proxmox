import 'package:flutter/material.dart';
import 'package:zenrouter/zenrouter.dart';
import 'main.dart' show Dashboard;
import 'organization_page.dart';
import 'cli_setup_page.dart';
import 'logs_page.dart';
import 'app_shell.dart';

abstract class AppRoute extends RouteTarget with RouteUnique {}

class LogsRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/logs');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'logs',
    child: LogsPage(coordinator: coordinator),
  );
}

class CliSetupRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/configuration');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'configuration',
    child: CliSetupPage(coordinator: coordinator),
  );
}

class DashboardRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'dashboard',
    child: Dashboard(coordinator: coordinator),
  );
}

class AgentsRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/agents');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'agents',
    child: Dashboard(coordinator: coordinator, agentsOnly: true),
  );
}

class OrganizationRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/organization');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'organization',
    child: OrganizationPage(coordinator: coordinator),
  );
}

class NotFoundRoute extends AppRoute {
  NotFoundRoute(this.uri);
  final Uri uri;
  @override
  Uri toUri() => uri;
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => Scaffold(
    body: Center(
      child: FilledButton(
        onPressed: () => coordinator.replace(DashboardRoute()),
        child: const Text('Return to dashboard'),
      ),
    ),
  );
}

class AppCoordinator extends Coordinator<AppRoute> {
  @override
  AppRoute parseRouteFromUri(Uri uri) => switch (uri.path) {
    '/' || '' => DashboardRoute(),
    '/agents' => AgentsRoute(),
    '/organization' => OrganizationRoute(),
    '/configuration' => CliSetupRoute(),
    '/logs' => LogsRoute(),
    _ => NotFoundRoute(uri),
  };
}
