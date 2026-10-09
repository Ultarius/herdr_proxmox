import 'package:flutter/material.dart';
import 'package:zenrouter/zenrouter.dart';
import 'main.dart' show Dashboard;
import 'organization_page.dart';
import 'cli_setup_page.dart';
import 'logs_page.dart';
import 'app_shell.dart';
import 'group_page.dart';
import 'project_explorer_page.dart';
import 'builds_page.dart';
import 'tasks_page.dart';
import 'knowledge_page.dart';

class KnowledgeRoute extends AppRoute {
  KnowledgeRoute([this.organizationId = '']);
  final String organizationId;
  @override
  List<Object?> get props => [organizationId];
  @override
  Uri toUri() => Uri(
    path: '/knowledge',
    queryParameters: organizationId.isEmpty
        ? null
        : {'organization': organizationId},
  );
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'knowledge',
    child: KnowledgePage(
      key: ValueKey(organizationId),
      coordinator: coordinator,
      organizationId: organizationId,
    ),
  );
}

class TasksRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/tasks');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'tasks',
    child: TasksPage(coordinator: coordinator),
  );
}

class TaskDetailRoute extends AppRoute {
  TaskDetailRoute(this.id);
  final String id;
  @override
  List<Object?> get props => [id];
  @override
  Uri toUri() => Uri(path: '/tasks/$id');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'tasks',
    child: TasksPage(key: ValueKey(id), taskId: id, coordinator: coordinator),
  );
}

class BuildsRoute extends AppRoute {
  @override
  Uri toUri() => Uri.parse('/builds');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'builds',
    child: const BuildsPage(),
  );
}

class GroupRoute extends AppRoute {
  GroupRoute(this.id);
  final String id;
  @override
  List<Object?> get props => [id];
  @override
  Uri toUri() => Uri(path: '/groups/$id');
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'group:$id',
    child: GroupPage(key: ValueKey(id), id: id, coordinator: coordinator),
  );
}

abstract class AppRoute extends RouteTarget with RouteUnique {}

class ExplorerRoute extends AppRoute {
  ExplorerRoute([this.path = '']);
  final String path;
  @override
  List<Object?> get props => [path];
  @override
  Uri toUri() => Uri(
    path: '/explorer',
    queryParameters: path.isEmpty ? null : {'path': path},
  );
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'explorer',
    child: ProjectExplorerPage(key: ValueKey(path), initialPath: path),
  );
}

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
  OrganizationRoute([this.view = 'chart']);
  final String view;
  @override
  List<Object?> get props => [view];
  @override
  Uri toUri() => Uri(
    path: '/organization',
    queryParameters: view == 'chart' ? null : {'view': view},
  );
  @override
  Widget build(AppCoordinator coordinator, BuildContext context) => AppShell(
    coordinator: coordinator,
    section: 'organization',
    child: OrganizationPage(coordinator: coordinator, initialView: view),
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
  DefaultTransitionStrategy get transitionStrategy =>
      DefaultTransitionStrategy.none;

  @override
  AppRoute parseRouteFromUri(Uri uri) {
    if (uri.pathSegments.length == 2 && uri.pathSegments.first == 'groups')
      return GroupRoute(uri.pathSegments[1]);
    if (uri.pathSegments.length == 2 && uri.pathSegments.first == 'tasks')
      return TaskDetailRoute(uri.pathSegments[1]);
    return switch (uri.path) {
      '/' || '' => DashboardRoute(),
      '/agents' => AgentsRoute(),
      '/organization' => OrganizationRoute(
        uri.queryParameters['view'] ?? 'chart',
      ),
      '/configuration' => CliSetupRoute(),
      '/builds' => BuildsRoute(),
      '/tasks' => TasksRoute(),
      '/knowledge' => KnowledgeRoute(uri.queryParameters['organization'] ?? ''),
      '/logs' => LogsRoute(),
      '/explorer' => ExplorerRoute(uri.queryParameters['path'] ?? ''),
      _ => NotFoundRoute(uri),
    };
  }
}
