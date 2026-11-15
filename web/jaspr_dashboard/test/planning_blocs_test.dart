import 'dart:convert';
import 'package:test/test.dart';
import 'package:juice/juice.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/planning_blocs.dart';

http.Response response(Object value, [int status = 200]) =>
    http.Response(jsonEncode(value), status);
Future<void> connect(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere(
    (_) => connection.state.refreshed != null,
  );
  connection.connect('admin');
  await ready;
}

http.Response snapshot() => response({'workspaces': [], 'agents': []});

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test(
    'product discovery owns observations and refreshes after an approved action',
    () async {
      var accepted = false;
      Map<String, dynamic>? mutation;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/discovery')
            return response({
              'organizations': [
                {'id': 'org'},
              ],
              'accepted': accepted,
            });
          if (request.url.path == '/api/tasks/discovery') {
            mutation = Map<String, dynamic>.from(jsonDecode(request.body));
            accepted = true;
            return response({});
          }
          return snapshot();
        }),
      );
      final bloc = ProductDiscoveryBloc(connection);
      try {
        await connect(connection);
        await bloc.refresh();
        expect(bloc.data['accepted'], isFalse);
        await bloc.submit('org', {'mode': 'proposal', 'decision': 'accept'});
        expect(mutation, {
          'organization_id': 'org',
          'mode': 'proposal',
          'decision': 'accept',
        });
        expect(bloc.data['accepted'], isTrue);
        expect(bloc.state.busy, isFalse);
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'automation retries retain an uncertain intent and edited policy gets a new identity',
    () async {
      final saved = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/tasks/automation') {
            saved.add(Map<String, dynamic>.from(jsonDecode(request.body)));
            if (saved.length == 1)
              return response({'error': 'Gateway response lost'}, 503);
            return response({
              'policy': {'daily_cap': saved.last['daily_cap']},
            });
          }
          if (request.url.path == '/api/organizations/automation')
            return response({
              'policy': {'daily_cap': 2},
            });
          return snapshot();
        }),
      );
      final bloc = OrganizationAutomationBloc(connection, 'org');
      try {
        await connect(connection);
        await bloc.refresh();
        bloc.prepare({'daily_cap': 3});
        await bloc.save();
        expect(bloc.pending, isNotNull);
        expect(bloc.pageError, contains('Gateway response lost'));
        await bloc.save();
        expect(saved[1]['request_id'], saved[0]['request_id']);
        expect(bloc.pending, isNull);
        expect(bloc.policy?['daily_cap'], 3);
        bloc.prepare({'daily_cap': 4});
        expect(bloc.pending?['request_id'], isNot(saved[0]['request_id']));
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'automation validation failures remain in the editor and discard refused retry intent',
    () async {
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/tasks/automation')
            return response({
              'error': 'Daily cap is outside the allowed range',
              'field_errors': {'daily_cap': 'Choose a smaller cap'},
            }, 400);
          return snapshot();
        }),
      );
      final bloc = OrganizationAutomationBloc(connection, 'org');
      try {
        await connect(connection);
        bloc.prepare({'daily_cap': 999});
        await expectLater(
          bloc.save(fromDialog: true),
          throwsA(isA<DashboardRequestException>()),
        );
        expect(bloc.pending, isNull);
        expect(bloc.pageError, isNull);
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'collaboration owns selected inspection and replays the same failed command',
    () async {
      final sent = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/activity')
            return response({
              'groups': [
                {'id': 'group', 'organization_id': 'org'},
              ],
              'jobs': [],
            });
          if (request.url.path == '/api/organizations/inspect')
            return response({
              'output': 'Live terminal',
              'status': 'idle',
              'reply_draft': 'Draft',
              'reply_job_id': 'job',
            });
          if (request.url.path == '/api/organizations/chat') {
            sent.add(Map<String, dynamic>.from(jsonDecode(request.body)));
            return sent.length == 1
                ? response({'error': 'Response uncertain'}, 503)
                : response({});
          }
          return snapshot();
        }),
      );
      final bloc = CollaborationBloc(connection, 'org');
      try {
        await connect(connection);
        bloc.select('agent');
        await bloc.refresh();
        expect(bloc.output, 'Live terminal');
        expect(bloc.draftJob, 'job');
        expect(
          await bloc.submit('chat', {
            'profile_id': 'agent',
            'prompt': 'Review',
          }),
          isFalse,
        );
        expect(bloc.pending, isNotNull);
        expect(await bloc.submit('chat', {}, retry: true), isTrue);
        expect(sent[1], sent[0]);
        expect(bloc.pending, isNull);
        await connection.disconnect();
        expect(bloc.jobs, isEmpty);
        expect(bloc.output, isEmpty);
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );
}
