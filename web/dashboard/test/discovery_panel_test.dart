import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/discovery_panel.dart';

Map<String, dynamic> catalog() => {
  'organizations': [
    {'id': 'org', 'name': 'Product organization'},
  ],
  'groups': [
    {'id': 'product', 'name': 'Product', 'organization_id': 'org'},
  ],
  'profiles': [
    {
      'id': 'maya',
      'name': 'Maya',
      'organization_id': 'org',
      'project': '/projects/repo',
    },
  ],
  'projects_root': '/projects',
  'policies': {
    'org': {
      'product_brief': 'First validated task',
      'discovery_focus': ['onboarding'],
      'discovery_group_id': 'product',
      'discovery_repository': 'repo',
      'discovery_enabled': false,
      'discovery_interval_hours': 168,
      'discovery_max_proposals': 3,
    },
  },
  'meetings': <Map<String, dynamic>>[],
};

DashboardBloc connect(
  Map<String, dynamic> snapshot,
  List<Map<String, dynamic>> posts,
) {
  final connection = DashboardBloc(
    client: MockClient((request) async {
      if (request.url.path == '/api/discovery')
        return http.Response(jsonEncode(snapshot), 200);
      if (request.url.path == '/api/tasks/discovery') {
        final body = Map<String, dynamic>.from(jsonDecode(request.body));
        posts.add(body);
        if (body['mode'] == 'configure')
          (snapshot['policies']['org'] as Map).addAll(body);
        if (body['mode'] == 'proposal')
          snapshot['meetings'][0]['proposals'][0]['state'] = 'draft';
        return http.Response('{}', 200);
      }
      return http.Response(
        '{"authenticated":true,"workspaces":[],"agents":[]}',
        200,
      );
    }),
  );
  BlocScope.register<DashboardBloc>(() => connection);
  connection.connect('token');
  connection.operator.value = {'role': 'admin'};
  return connection;
}

void main() {
  testWidgets(
    'manual discovery and direction configuration work without tasks',
    (tester) async {
      final posts = <Map<String, dynamic>>[];
      final connection = connect(catalog(), posts);
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: SingleChildScrollView(child: DiscoveryPanel())),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Product discovery'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Run discovery now'));
      await tester.tap(find.text('Run discovery now'));
      await tester.pumpAndSettle();
      expect(posts.single['mode'], 'run');
      expect(posts.single['organization_id'], 'org');
      await tester.tap(find.text('Configure discovery'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'Product brief'),
        'A new user reaches a validated task in ten minutes.',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Focus areas, separated by commas'),
        'onboarding, reliability',
      );
      await tester.tap(find.text('Save discovery direction'));
      await tester.pumpAndSettle();
      expect(posts.last['product_brief'], contains('ten minutes'));
      expect(posts.last['discovery_focus'], ['onboarding', 'reliability']);
      expect(posts.last['discovery_enabled'], false);
      expect(posts.last['discovery_max_proposals'], 3);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );

  testWidgets('a duplicate proposal can be overridden into a draft', (
    tester,
  ) async {
    final snapshot = catalog();
    snapshot['meetings'] = [
      {
        'id': 'meeting',
        'organization_id': 'org',
        'group_id': 'product',
        'state': 'ready',
        'created_at': 'today',
        'next_at': '2100-01-01T00:00:00Z',
        'evidence': {
          'source_sha': 'a' * 40,
          'coverage': 'Committed bounded files.',
          'items': <Map<String, dynamic>>[],
        },
        'rubric': <Map<String, dynamic>>[],
        'proposals': [
          {
            'key': 'proposal',
            'type': 'task',
            'state': 'duplicate',
            'duplicate_task_id': 'existing',
            'title': 'Add first-run guide',
            'problem': 'No onboarding guide.',
            'impact': 'New users can start.',
            'description': 'Document setup.',
            'acceptance_criteria': ['Explain one task.'],
            'required_checks': ['Review navigation.'],
            'evidence': ['E1'],
          },
        ],
      },
    ];
    final posts = <Map<String, dynamic>>[];
    final connection = connect(snapshot, posts);
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: SingleChildScrollView(child: DiscoveryPanel())),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Product discovery'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Discovery \u00b7 ready'));
    await tester.tap(find.text('Discovery \u00b7 ready'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Create draft anyway'));
    await tester.tap(find.text('Create draft anyway'));
    await tester.pumpAndSettle();
    expect(posts.single['decision'], 'accept');
    expect(posts.single['allow_duplicate'], true);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });

  for (final brightness in Brightness.values) {
    testWidgets('phone discovery evidence and draft approval in $brightness', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final snapshot = catalog();
      snapshot['meetings'] = [
        {
          'id': 'meeting',
          'organization_id': 'org',
          'group_id': 'product',
          'state': 'ready',
          'created_at': 'today',
          'next_at': '2100-01-01T00:00:00Z',
          'evidence': {
            'source_sha': 'a' * 40,
            'coverage': 'Committed bounded files.',
            'items': [
              {'id': 'E1', 'locator': 'docs/', 'text': 'No first-run guide.'},
            ],
          },
          'rubric': [
            {
              'focus': 'onboarding',
              'status': 'thin',
              'evidence': ['E1'],
            },
          ],
          'proposals': [
            {
              'key': 'proposal',
              'type': 'task',
              'state': 'proposed',
              'title': 'Add first-run guide',
              'problem': 'No onboarding guide.',
              'impact': 'New users can start.',
              'description': 'Document setup.',
              'acceptance_criteria': ['Explain one task.'],
              'required_checks': ['Review navigation.'],
              'evidence': ['E1'],
            },
          ],
        },
      ];
      final posts = <Map<String, dynamic>>[];
      final connection = connect(snapshot, posts);
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData(brightness: brightness),
          home: const Scaffold(
            body: SingleChildScrollView(child: DiscoveryPanel()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Product discovery'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Run discovery now'),
            )
            .onPressed,
        isNull,
      );
      await tester.ensureVisible(find.text('Discovery · ready'));
      await tester.tap(find.text('Discovery · ready'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('View cited evidence'));
      await tester.tap(find.text('View cited evidence'));
      await tester.pumpAndSettle();
      expect(find.textContaining('No first-run guide.'), findsOneWidget);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Create draft'));
      await tester.tap(find.text('Create draft'));
      await tester.pumpAndSettle();
      expect(posts.single['mode'], 'proposal');
      expect(posts.single['decision'], 'accept');
      expect(posts.single.containsKey('queue'), false);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    });
  }
}
