import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/project_analysis_card.dart';
import 'package:herdr_dashboard_state/project_analysis.dart';

void main() {
  for (final overridden in [false, true]) {
    testWidgets(
      'renders asset decision, SDK add-ons and recipe evidence (override=$overridden)',
      (tester) async {
        final sources = overridden
            ? []
            : [
                {
                  'path': 'assets/dev',
                  'reason':
                      'This heuristic can include Node servers or tools: add a Node.js override if needed.',
                },
              ];
        final connection = DashboardBloc(
          client: MockClient((request) async {
            if (request.url.path.endsWith('/environment/plan')) {
              return http.Response(
                jsonEncode({
                  'digest': 'plan',
                  'source_components': sources,
                  'requirements': [],
                  'selected_checks': [],
                  'check_candidates': [
                    {
                      'id': '.:build',
                      'cwd': '.',
                      'arguments': ['cargo', 'build', '--no-default-features'],
                      'source': 'README#headless-cargo',
                      'derived': true,
                      'evidence': 'README.md',
                    },
                  ],
                }),
                200,
              );
            }
            if (request.url.path.endsWith('/analysis')) {
              return http.Response(
                jsonEncode({
                  'source_components': sources,
                  'requirements': overridden
                      ? [
                          {
                            'tool': 'node',
                            'label': 'Node.js',
                            'components': ['assets/dev'],
                            'override': {'constraint': '20.19.2'},
                            'assessment': {'verdict': 'missing'},
                          },
                        ]
                      : [],
                  'components': [
                    {
                      'path': '.',
                      'basis': 'manifest',
                      'requirements': [
                        {
                          'tool': 'rust-toolchain',
                          'established': true,
                          'selection_lookup': {
                            'components': ['rustfmt', 'clippy'],
                          },
                        },
                      ],
                    },
                    {
                      'path': 'assets/dev',
                      'basis': 'inferred',
                      'requirements': [],
                    },
                  ],
                }),
                200,
              );
            }
            return http.Response('{"workspaces":[],"agents":[]}', 200);
          }),
        );
        BlocScope.register<DashboardBloc>(() => connection);
        connection.connect('token');
        await tester.pumpAndSettle();
        await tester.pumpWidget(
          const MaterialApp(
            home: Scaffold(body: ProjectAnalysisCard(path: 'repo')),
          ),
        );
        await tester.pumpAndSettle();
        await tester.tap(find.text('Discovered requirements'));
        await tester.pumpAndSettle();
        expect(
          find.textContaining('Source-only component:'),
          overridden ? findsNothing : findsOneWidget,
        );
        expect(
          find.text('Requested SDK components: rustfmt, clippy'),
          findsOneWidget,
        );
        if (overridden)
          expect(find.textContaining('· override'), findsOneWidget);
        await tester.ensureVisible(find.text('Plan environment'));
        await tester.tap(find.text('Plan environment'));
        await tester.pumpAndSettle();
        expect(find.textContaining('Evidence: README.md'), findsOneWidget);
        expect(find.textContaining('Derived companion'), findsOneWidget);
        if (!overridden)
          expect(find.textContaining('Node servers or tools'), findsOneWidget);
        expect(
          find.textContaining('Source-only component:'),
          overridden ? findsNothing : findsOneWidget,
        );
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        await BlocScope.reset();
      },
    );
  }

  test(
    'source helpers and bundled installer notes do not overstate SDK support',
    () {
      expect(
        projectInstallerSummary({'provisionable': []}),
        contains('Plan environment'),
      );
      expect(
        projectInstallerSummary({'provisionable': []}),
        isNot(contains('must be provided on the host')),
      );
      final component = {'path': 'assets/dev', 'basis': 'inferred'};
      final report = {
        'source_components': [
          null,
          'invalid',
          {
            'path': 'assets/dev',
            'capability': 'node',
            'reason': 'JS/TS asset.',
          },
        ],
      };
      expect(
        sourceComponentNotice(component, report),
        contains('does not block'),
      );
      expect(sourceComponentNotice({'path': '.'}, report), isEmpty);
      expect(
        sourceComponentNotice(
          component,
          report,
          plan: {'source_components': []},
        ),
        isEmpty,
      );
      expect(sourceComponentNotice(component, {}), isEmpty);
      expect(
        sourceComponentNotice(component, {
          ...report,
          'requirements': [
            {
              'tool': 'node',
              'override': {'constraint': '20.19.2'},
            },
          ],
        }, plan: report),
        isEmpty,
      );
    },
  );

  test('SDK readiness never claims dependency or test verification', () {
    expect(environmentJobLabel({'state': 'ready'}), contains('have not run'));
    expect(
      environmentPlanCanPrepare({
        'preparation_version': 1,
        'requirements': [
          {'state': 'acquire', 'artifact': {}, 'activation': 'available'},
        ],
      }),
      isTrue,
    );
    expect(
      environmentPlanCanPrepare({
        'preparation_version': 1,
        'requirements': [
          {'state': 'unsupported', 'artifact': {}},
        ],
      }),
      isFalse,
    );
    expect(
      environmentPlanCanPrepare({
        'preparation_version': 1,
        'requirements': [
          {'state': 'ready', 'artifact': {}, 'activation': 'unavailable'},
        ],
      }),
      isFalse,
    );
  });
  test('readiness distinguishes availability from version satisfaction', () {
    const load = ProjectAnalysisLoad({});
    final requirement = <String, dynamic>{
      'constraint': '',
      'assessment': {'verdict': 'satisfied'},
    };
    expect(
      load.readinessLabel(requirement),
      'Installed — required version unknown',
    );
    requirement['constraint'] = '>=3.12';
    expect(load.readinessLabel(requirement), 'satisfied');
    requirement['constraint'] = '';
    requirement['discovered'] = [
      {'selected': '3.44.8'},
    ];
    expect(load.readinessLabel(requirement), 'satisfied');
    requirement['override'] = {'constraint': ''};
    expect(
      load.readinessLabel(requirement),
      'Installed — required version unknown',
    );
    requirement['assessment'] = {'verdict': 'violated'};
    expect(load.readinessLabel(requirement), 'violated');
  });

  testWidgets('discovery shows evidence, lookup, readiness and coverage', (
    tester,
  ) async {
    var failed = false;
    var refreshRequested = false;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/analysis')) {
          refreshRequested |= request.url.queryParameters['refresh'] == '1';
          if (failed) return http.Response('{"error":"scan unavailable"}', 400);
          return http.Response(
            jsonEncode({
              'source': {
                'scope': 'working_tree',
                'at': '2026-10-16',
                'cache_seconds': 30,
                'boundary': 'repository root',
              },
              'coverage': {
                'files': 8,
                'directories': 2,
                'unknown': 6,
                'unknown_evidence': [
                  for (var i = 0; i < 6; i++) 'data/$i.unknown',
                ],
                'truncated': true,
              },
              // The resolved view the gateway serves: discovery plus the host
              // assessment, so readiness is measured rather than declared.
              'requirements': [
                {
                  'tool': 'dotnet',
                  'label': '.NET SDK',
                  'constraint': '8.0.100',
                  'notation': 'dotnet',
                  'components': ['Api'],
                  'override': null,
                  'conflicts': [],
                  'assessment': {
                    'verdict': 'missing',
                    'measured': null,
                    'selected': '',
                    'detail':
                        'No dotnet executable answered a version request.',
                    'installer': false,
                    'provisionable': false,
                    'offer': null,
                  },
                },
                {
                  'tool': 'python',
                  'label': 'Python',
                  'constraint': '',
                  'assessment': {'verdict': 'satisfied', 'measured': '3.13.5'},
                },
              ],
              'provisionable': [],
              'components': [
                {
                  'path': 'Api',
                  'basis': 'manifest',
                  'languages': [
                    {'label': 'C#'},
                  ],
                  'requirements': [
                    {
                      'tool': 'dotnet',
                      'label': '.NET SDK',
                      'selected': '8.0.100',
                      'established': true,
                      'observations': [
                        {'file': 'global.json', 'field': 'sdk.version'},
                      ],
                      'resolution': {
                        'context': {'directory': 'Api'},
                        'boundary': 'repository root',
                        'boundary_note':
                            'Host configuration above the repository root is not inspected.',
                        'shadowed': [],
                      },
                    },
                  ],
                  'unresolved': [],
                },
                {
                  'path': 'scripts',
                  'basis': 'inferred',
                  'requirements': [
                    {
                      'tool': 'python',
                      'label': 'Python',
                      'established': false,
                      'reason': 'Python version is undeclared.',
                    },
                  ],
                  'unresolved': [
                    {'reason': 'Python version is undeclared.'},
                    {'reason': 'A different issue remains.'},
                  ],
                },
              ],
              'unresolved': [
                {'what': 'directory listing', 'reason': 'Listing was capped.'},
              ],
            }),
            200,
          );
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await tester.pumpAndSettle();
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: ProjectAnalysisCard(path: 'repo')),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discovered requirements'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Evidence: global.json#sdk.version'),
      findsOneWidget,
    );
    expect(find.textContaining('SDK lookup: Api'), findsOneWidget);
    expect(find.textContaining('partial scan'), findsOneWidget);
    expect(find.text('Host readiness'), findsOneWidget);
    expect(find.textContaining('· missing'), findsOneWidget);
    expect(find.textContaining('Component: Api'), findsOneWidget);
    expect(
      find.textContaining('Installed — required version unknown'),
      findsOneWidget,
    );
    expect(
      find.textContaining('Python version is undeclared.'),
      findsOneWidget,
    );
    expect(find.textContaining('A different issue remains.'), findsOneWidget);
    expect(
      find.textContaining('No installed version was measured'),
      findsOneWidget,
    );
    expect(
      find.textContaining(
        'No matching SDK is in the bundled installer catalog',
      ),
      findsOneWidget,
    );
    final scroll = find.byType(SingleChildScrollView).first;
    await tester.scrollUntilVisible(
      find.text('Unrecognised files'),
      120,
      scrollable: find
          .descendant(of: scroll, matching: find.byType(Scrollable))
          .first,
    );
    await tester.tap(find.text('Unrecognised files'));
    await tester.pumpAndSettle();
    expect(find.text('Showing 6 of 6 paths.'), findsOneWidget);
    for (var i = 0; i < 6; i++)
      expect(find.text('data/$i.unknown'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Re-analyse'),
      120,
      scrollable: find
          .descendant(of: scroll, matching: find.byType(Scrollable))
          .first,
    );
    failed = true;
    await tester.tap(find.text('Re-analyse'));
    await tester.pumpAndSettle();
    expect(refreshRequested, isTrue);
    expect(
      find.textContaining('This does not mean no requirements exist'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });

  testWidgets('failed override save keeps the draft dialog open', (
    tester,
  ) async {
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/toolchain'))
          return http.Response('{"error":"Unknown tool"}', 400);
        if (request.url.path.endsWith('/analysis'))
          return http.Response(
            '{"components":[],"coverage":{},"source":{},"requirements":[]}',
            200,
          );
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'role': 'admin'};
    await tester.pumpAndSettle();
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: ProjectAnalysisCard(path: 'repo')),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discovered requirements'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Add override'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'php');
    await tester.enterText(find.byType(TextField).last, '^8.2');
    await tester.tap(find.text('Save override'));
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsOneWidget);
    expect(find.textContaining('Unknown tool'), findsOneWidget);
    expect(find.text('^8.2'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
}
