import 'package:test/test.dart';
import 'package:juice/juice.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/feature_bloc.dart';

Future<DashboardBloc> connection() async {
  final result = DashboardBloc(
    client: MockClient(
      (_) async => http.Response(
        '{"workspaces":[],"agents":[],"herdr_server":"running"}',
        200,
      ),
    ),
  );
  final loaded = result.stream.firstWhere(
    (_) => result.state.refreshed != null,
  );
  result.connect('first');
  await loaded;
  return result;
}

class TestTimer implements Timer {
  bool active = true;
  @override
  bool get isActive => active;
  @override
  int get tick => 0;
  @override
  void cancel() => active = false;
}

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test('latest keyed read owns data and emits the Juice lifecycle', () async {
    final transport = await connection();
    final feature = FeatureBloc(transport, initial: const {'count': 0});
    final oldReply = Completer<FeatureResult<int>>();
    final oldStarted = Completer<void>();
    final statuses = <StreamStatus<FeatureState>>[];
    final sub = feature.stream.listen(statuses.add);
    try {
      final old = feature.read<int>('list', () {
        oldStarted.complete();
        return oldReply.future;
      });
      await oldStarted.future;
      expect(feature.state.reading, {'list'});
      expect(
        await feature.read<int>(
          'list',
          () async => const FeatureResult(patch: {'count': 2}, value: 2),
        ),
        2,
      );
      oldReply.complete(const FeatureResult(patch: {'count': 1}, value: 1));
      expect(await old, isNull);
      expect(feature.state.values['count'], 2);
      expect(feature.state.reading, isEmpty);
      expect(statuses.any((status) => status is WaitingStatus), isTrue);
      expect(statuses.any((status) => status is UpdatingStatus), isTrue);
      expect(() => feature.state.values['count'] = 9, throwsUnsupportedError);
    } finally {
      if (!oldReply.isCompleted) oldReply.complete(const FeatureResult());
      await sub.cancel();
      await feature.close();
      await transport.close();
    }
  });

  test(
    'a write invalidates old reads and independently rejects duplicate writes',
    () async {
      final transport = await connection();
      final feature = FeatureBloc(transport);
      final oldReply = Completer<FeatureResult<void>>();
      final oldStarted = Completer<void>();
      final writeReply = Completer<FeatureResult<bool>>();
      final writeStarted = Completer<void>();
      var sideEffects = 0;
      try {
        final old = feature.read<void>('poll', () {
          oldStarted.complete();
          return oldReply.future;
        });
        await oldStarted.future;
        final write = feature.mutate<bool>('save', () {
          sideEffects++;
          writeStarted.complete();
          return writeReply.future;
        });
        await writeStarted.future;
        expect(feature.state.busy, isTrue);
        expect(feature.state.reading, isEmpty);
        expect(
          await feature.mutate<bool>('save', () async {
            sideEffects++;
            return const FeatureResult(value: true);
          }),
          isNull,
        );
        expect(
          await feature.read<bool>('poll', () async {
            fail('Polling cannot enter while a write owns the feature.');
          }),
          isNull,
        );
        oldReply.complete(const FeatureResult(patch: {'value': 'stale'}));
        await old;
        expect(feature.state.busy, isTrue);
        writeReply.complete(
          const FeatureResult(patch: {'value': 'saved'}, value: true),
        );
        expect(await write, isTrue);
        expect(feature.state.values['value'], 'saved');
        expect(sideEffects, 1);
        expect(feature.state.busy, isFalse);
      } finally {
        if (!oldReply.isCompleted) oldReply.complete(const FeatureResult());
        if (!writeReply.isCompleted) writeReply.complete(const FeatureResult());
        await feature.close();
        await transport.close();
      }
    },
  );

  test(
    'post-write coalesced refresh starts fresh while an old poll is unresolved',
    () async {
      final transport = await connection();
      final feature = FeatureBloc(transport);
      final started = Completer<void>();
      final reply = Completer<FeatureResult<void>>();
      try {
        final old = feature.read<void>('list', () {
          started.complete();
          return reply.future;
        }, replace: false);
        await started.future;
        await feature.mutate<void>(
          'save',
          () async => const FeatureResult(patch: {'value': 'saved'}),
        );
        await feature.read<void>(
          'list',
          () async => const FeatureResult(patch: {'value': 'new snapshot'}),
          replace: false,
        );
        expect(feature.state.values['value'], 'new snapshot');
        reply.complete(const FeatureResult(patch: {'value': 'old snapshot'}));
        await old;
        expect(feature.state.values['value'], 'new snapshot');
      } finally {
        if (!reply.isCompleted) reply.complete(const FeatureResult());
        await feature.close();
        await transport.close();
      }
    },
  );

  test(
    'superseded failures do not escape through a dialog result future',
    () async {
      final transport = await connection();
      final feature = FeatureBloc(transport);
      final reply = Completer<FeatureResult<int>>();
      final started = Completer<void>();
      try {
        final old = feature.read<int>('dialog', () {
          started.complete();
          return reply.future;
        }, rethrowErrors: true);
        await started.future;
        expect(
          await feature.read<int>(
            'dialog',
            () async => const FeatureResult(value: 2, patch: {'value': 'new'}),
          ),
          2,
        );
        reply.completeError(StateError('old failure'));
        expect(await old, isNull);
        expect(feature.state.error, isNull);
        expect(feature.state.values['value'], 'new');
      } finally {
        if (!reply.isCompleted) reply.complete(const FeatureResult());
        await feature.close();
        await transport.close();
      }
    },
  );

  test(
    'action failures survive unrelated reads and clear explicitly',
    () async {
      final transport = await connection();
      final feature = FeatureBloc(transport);
      final statuses = <StreamStatus<FeatureState>>[];
      final sub = feature.stream.listen(statuses.add);
      try {
        await feature.mutate<void>(
          'save',
          () async => throw StateError('save failed'),
        );
        expect(feature.state.busy, isFalse);
        expect(feature.state.errors['save'], contains('save failed'));
        expect(statuses.any((status) => status is FailureStatus), isTrue);
        await feature.read<void>(
          'list',
          () async => const FeatureResult(patch: {'fresh': true}),
        );
        expect(feature.state.errors['save'], contains('save failed'));
        await feature.clearError('save');
        expect(feature.state.error, isNull);
        await expectLater(
          feature.read<void>(
            'list',
            () async => throw StateError('field rejected'),
            rethrowErrors: true,
          ),
          throwsStateError,
        );
        expect(feature.state.reading, isEmpty);
      } finally {
        await sub.cancel();
        await feature.close();
        await transport.close();
      }
    },
  );

  test(
    'a retired session write cannot occupy the new session admission slot',
    () async {
      final oldReply = Completer<http.Response>();
      final started = Completer<void>();
      final writes = <String>[];
      final transport = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/example/write') {
            final account = request.headers['Authorization']!;
            writes.add(account);
            if (account == 'Bearer first') {
              started.complete();
              return oldReply.future;
            }
            return http.Response('{}', 200);
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      final connected = transport.stream.firstWhere(
        (_) => transport.state.refreshed != null,
      );
      transport.connect('first');
      await connected;
      final feature = FeatureBloc(transport);
      try {
        final old = feature.mutate<bool>('write', () async {
          await transport.request('example/write', {});
          return const FeatureResult(value: true, patch: {'account': 'first'});
        });
        await started.future;
        await transport.disconnect();
        final reconnected = transport.stream.firstWhere(
          (_) => transport.state.connected,
        );
        transport.connect('second');
        await reconnected;
        final result = await feature.mutate<bool>('write', () async {
          await transport.request('example/write', {});
          return const FeatureResult(value: true, patch: {'account': 'second'});
        });
        expect(result, isTrue);
        expect(writes, ['Bearer first', 'Bearer second']);
        expect(feature.state.values['account'], 'second');
        oldReply.complete(http.Response('{}', 200));
        expect(await old, isNull);
        expect(feature.state.values['account'], 'second');
      } finally {
        if (!oldReply.isCompleted) oldReply.complete(http.Response('{}', 200));
        await feature.close();
        await transport.close();
      }
    },
  );

  test('session changes reset observations and reject old responses', () async {
    final transport = await connection();
    final feature = FeatureBloc(transport, initial: const {'value': 'empty'});
    final started = Completer<void>();
    final reply = Completer<FeatureResult<void>>();
    try {
      await feature.patch({'value': 'first account'});
      final pending = feature.read<void>('list', () {
        started.complete();
        return reply.future;
      });
      await started.future;
      final reset = feature.stream.firstWhere(
        (_) => feature.state.values['value'] == 'empty',
      );
      await transport.disconnect();
      await reset;
      reply.complete(const FeatureResult(patch: {'value': 'old account'}));
      await pending;
      expect(feature.state.values['value'], 'empty');
      final loaded = transport.stream.firstWhere(
        (_) => transport.state.connected,
      );
      transport.connect('second');
      await loaded;
      await feature.read<void>(
        'list',
        () async => const FeatureResult(patch: {'value': 'second account'}),
      );
      expect(feature.state.values['value'], 'second account');
    } finally {
      if (!reply.isCompleted) reply.complete(const FeatureResult());
      await feature.close();
      await transport.close();
    }
  });

  test(
    'closing the feature cancels owned polls without closing the connection',
    () async {
      final transport = await connection();
      final timers = <TestTimer>[];
      try {
        await runZoned(
          () async {
            final feature = FeatureBloc(transport);
            try {
              feature.poll('list', const Duration(seconds: 5), () async {});
              expect(timers.single.isActive, isTrue);
              await feature.close();
              expect(timers.single.isActive, isFalse);
              expect(feature.isClosed, isTrue);
              expect(transport.isClosed, isFalse);
            } finally {
              await feature.close();
            }
          },
          zoneSpecification: ZoneSpecification(
            createPeriodicTimer: (self, parent, zone, duration, callback) {
              final timer = TestTimer();
              timers.add(timer);
              return timer;
            },
          ),
        );
      } finally {
        await transport.close();
      }
    },
  );
}
