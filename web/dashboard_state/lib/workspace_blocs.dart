import 'dart:convert';
import 'dart:typed_data';
import 'feature_bloc.dart';
import 'integration.dart';
import 'project_analysis.dart';

class ExplorerBloc extends FeatureBloc {
  ExplorerBloc(super.connection)
    : super(initial: const {'directory': null, 'preview': null, 'path': ''});

  Future<bool?> open(String path) => read<bool>('browse', () async {
    final result = Map<String, dynamic>.from(
      await connection.request('projects/browse', {'path': path}),
    );
    final folder = result['kind'] == 'folder';
    return FeatureResult(
      value: folder,
      patch: folder
          ? {'directory': result, 'path': path, 'preview': null}
          : {'preview': result},
    );
  });

  Future<void> closePreview() => patch({'preview': null});
}

class LogsBloc extends FeatureBloc {
  LogsBloc(super.connection)
    : super(
        initial: const {
          'logs': [],
          'total': 0,
          'preview': null,
          'loaded': false,
        },
      );

  Future<Map<String, dynamic>> _listing() async {
    final data = await connection.request('logs');
    return {
      'logs': List<Map<String, dynamic>>.from(data['logs']),
      'total': data['total_bytes'] as int,
      'loaded': true,
    };
  }

  Future<void> refresh() async => read<void>(
    'refresh',
    () async => FeatureResult(patch: await _listing()),
    replace: false,
  );

  Future<void> action(String name, Map<String, dynamic> body) async {
    await mutate<void>('action', () async {
      final data = await connection.request('logs/$name', body);
      if (name == 'preview') {
        return FeatureResult(
          patch: {
            'preview':
                '${data['truncated'] == true ? '[Last 8 KB of saved text]\n' : ''}${data['text']}',
          },
        );
      }
      return FeatureResult(patch: {'preview': null, ...await _listing()});
    });
  }

  Future<void> closePreview() => patch({'preview': null});

  Future<Map<String, dynamic>?> ticket(Map<String, dynamic> log, String mode) =>
      read<Map<String, dynamic>>(
        'ticket',
        () async => FeatureResult(
          value: Map<String, dynamic>.from(
            await connection.request('logs/ticket', {
              'id': log['id'],
              'mode': mode,
            }),
          ),
        ),
        rethrowErrors: true,
      );
}

class BuildDownload {
  const BuildDownload(this.filename, this.message, {this.bytes, this.text});
  final String filename, message;
  final Uint8List? bytes;
  final String? text;
}

class BuildsBloc extends FeatureBloc {
  BuildsBloc(super.connection)
    : super(
        initial: const {
          'runs': [],
          'identity': null,
          'deployment': null,
          'buildService': null,
          'executor': 'gateway',
          'queued': 0,
          'loaded': false,
          'performance': null,
        },
      ) {
    poll('builds', const Duration(seconds: 5), refresh);
  }

  Future<Map<String, dynamic>> _observations() async {
    final validation = await connection.request('validation');
    final identity = await connection.request('build');
    Map<String, dynamic>? installer, deployment;
    try {
      installer = Map<String, dynamic>.from(
        await connection.request('build-service'),
      );
    } catch (_) {}
    try {
      deployment = Map<String, dynamic>.from(
        await connection.request('updates'),
      );
    } catch (_) {}
    return {
      'runs': List<Map<String, dynamic>>.from(validation['runs'] ?? []),
      'executor': '${validation['executor'] ?? 'gateway'}',
      'queued': validation['queued'] as int? ?? 0,
      'identity': Map<String, dynamic>.from(identity),
      'deployment': deployment,
      'buildService': installer,
      'loaded': true,
    };
  }

  Future<void> refresh() async => read<void>(
    'refresh',
    () async => FeatureResult(patch: await _observations()),
    replace: false,
    quiet: state.values['loaded'] == true,
  );

  Future<void> loadPerformance() async {
    if (!admin) return;
    await read<void>(
      'performance',
      () async => FeatureResult(
        patch: {
          'performance': Map<String, dynamic>.from(
            await connection.request('diagnostics/performance'),
          ),
          'performanceEpoch': connection.generation,
        },
      ),
      replace: false,
    );
  }

  Future<bool?> installService() {
    if (state.values['buildService']?['supported'] != true ||
        ['queued', 'running'].contains(state.values['buildService']?['state']))
      return Future.value(null);
    return mutate<bool>('action', () async {
      final result = await connection.request(
        'build-service/install',
        const {},
      );
      return FeatureResult(
        value: true,
        patch: {
          'buildService': <String, dynamic>{'supported': true, ...result},
        },
        message:
            'Build-service installation queued. The gateway will restart; automation settings remain unchanged.',
      );
    }, admin: true);
  }

  Future<bool?> promote(Map<String, dynamic> run) => _deploymentAction(
    'updates/promote',
    {'run_id': run['id']},
    'Deployment queued. The gateway restarts when deployment finishes.',
  );
  Future<bool?> rollback() =>
      _deploymentAction('updates/rollback', const {}, 'Rollback queued.');
  Future<bool?> repairProvenance() => _deploymentAction(
    'updates/provenance',
    const {},
    'Source identity resolution queued.',
  );
  Future<bool?> _deploymentAction(
    String path,
    Map<String, dynamic> body,
    String message,
  ) {
    if (['queued', 'running'].contains(state.values['deployment']?['state']))
      return Future.value(null);
    return mutate<bool>('action', () async {
      await connection.request(path, body);
      return FeatureResult(
        value: true,
        patch: await _observations(),
        message: message,
      );
    }, admin: true);
  }

  Future<BuildDownload?> download(Map<String, dynamic> run, String kind) {
    if (state.reading.contains('download')) return Future.value(null);
    return read<BuildDownload>('download', () async {
      BuildDownload result;
      if (kind == 'artifact' || kind == 'deployment') {
        final bytes = await connection.bytes(
          'validation/artifact?id=${run['id']}&kind=${kind == 'deployment' ? 'deployment' : 'static'}',
        );
        final manifest =
            run[kind == 'deployment' ? 'deployment_package' : 'artifact']
                as Map;
        final expected = manifest['bytes'];
        if (expected is! int || expected < 0 || bytes.length != expected) {
          throw Exception(
            'Downloaded size ${bytes.length} does not match the manifest ($expected bytes).',
          );
        }
        result = BuildDownload(
          '$kind-${run['id']}.tar.gz',
          '$kind package fetched and handed to the browser · ${bytes.length} bytes verified.',
          bytes: bytes,
        );
      } else if (kind == 'manifest') {
        final text = const JsonEncoder.withIndent(
          '  ',
        ).convert(run['artifact']);
        result = BuildDownload(
          'build-${run['id']}.json',
          'Manifest fetched · ${utf8.encode(text).length} bytes.',
          text: text,
        );
      } else {
        final text = await connection.text('validation/log?id=${run['id']}');
        result = BuildDownload(
          'validation-${run['id']}.log',
          'Log fetched · ${utf8.encode(text).length} bytes.',
          text: text,
        );
      }
      return FeatureResult(value: result);
    }, start: {'downloadId': '${run['id']}:$kind'});
  }
}

class GitBloc extends FeatureBloc {
  GitBloc(super.connection, this.path)
    : super(initial: const {'data': null, 'showProgress': true}) {
    poll('git', const Duration(seconds: 10), () => load(quiet: true));
  }
  final String path;

  Future<void> load({bool quiet = false, bool fetch = false}) async {
    if (fetch) {
      await mutate<void>(
        'fetch',
        () async => FeatureResult(
          patch: {
            'data': Map<String, dynamic>.from(
              await connection.request('projects/git', {
                'path': path,
                'action': 'fetch',
              }),
            ),
          },
        ),
        start: {'showProgress': true},
      );
    } else {
      await read<void>(
        'status',
        () async => FeatureResult(
          patch: {
            'data': Map<String, dynamic>.from(
              await connection.request('projects/git', {
                'path': path,
                'action': 'status',
              }),
            ),
          },
        ),
        quiet: quiet,
        replace: false,
        start: {'showProgress': !quiet},
      );
    }
  }

  Future<Map<String, dynamic>?> inspectBase(
    String path,
    String base,
    String branch,
  ) => read<Map<String, dynamic>>('basePlan', () async {
    final inspected = await connection.request('projects/git', {
      'path': path,
      'base': base,
    });
    final plan = inspected['base_update'];
    if (plan is! Map || plan['branch'] != branch)
      throw Exception('Destination branch changed. Refresh before updating.');
    return FeatureResult(
      value: Map<String, dynamic>.from(plan),
      patch: {'basePlan': Map<String, dynamic>.from(plan)},
    );
  }, rethrowErrors: true);

  Future<bool?> updateBase(
    Map<String, dynamic> plan,
    String requestId,
  ) => mutate<bool>(
    'baseUpdate',
    () async {
      final result = await connection.request('projects/git', {
        ...plan,
        'action': 'update_base',
        'request_id': requestId,
      });
      if (result['state'] != 'complete')
        throw Exception(result['reason'] ?? 'Base update requires review.');
      final data = Map<String, dynamic>.from(
        await connection.request('projects/git', {
          'path': path,
          'action': 'status',
        }),
      );
      return FeatureResult(
        value: true,
        patch: {'data': data},
        message:
            'Base checkout updated. Agent worktrees continue independently.',
      );
    },
    admin: true,
    rethrowErrors: true,
  );

  Future<Map<String, dynamic>?> diff(Map tree, Map file) =>
      read<Map<String, dynamic>>(
        'diff',
        () async => FeatureResult(
          value: Map<String, dynamic>.from(
            await connection.request('projects/git', {
              'path': tree['path'],
              'file': file['path'],
            }),
          ),
        ),
        rethrowErrors: true,
      );

  Future<bool?> notify(IntegrationNotice notice) =>
      mutate<bool>('notify', () async {
        if (!agentCanIntegrate(connection, notice.profileId))
          return const FeatureResult(value: false);
        await requestIntegration(connection, notice);
        return const FeatureResult(
          value: true,
          message:
              'Merge request sent. Follow progress in the organization chat.',
        );
      }, start: {'notifying': notice.profileId});
}

class IntegrationBloc extends FeatureBloc {
  IntegrationBloc(super.connection)
    : super(
        initial: const {
          'data': <String, dynamic>{},
          'validation': <String, dynamic>{},
          'checked': null,
        },
      ) {
    poll('integration', const Duration(seconds: 15), refresh);
  }

  Future<Map<String, dynamic>> _observations() async {
    final result = await connection.request('integration');
    Map<String, dynamic> validation;
    try {
      validation = Map<String, dynamic>.from(
        await connection.request('validation'),
      );
    } catch (error) {
      validation = {'error': '$error'};
    }
    return {
      'data': Map<String, dynamic>.from(result['coordination'] ?? {}),
      'validation': validation,
      'checked': result['checked'] as String?,
    };
  }

  Future<void> refresh() async => read<void>(
    'refresh',
    () async => FeatureResult(patch: await _observations()),
    replace: false,
  );

  Future<bool?> action(String endpoint, Map<String, dynamic> body) async {
    final result = await mutate<bool>('action', () async {
      await connection.request(endpoint, body);
      await connection.request('organizations/directory');
      return FeatureResult(value: true, patch: await _observations());
    }, admin: true);
    if (result == null && available) await refresh();
    return result;
  }

  Future<String?> log(Map run) => read<String>(
    'log',
    () async => FeatureResult(
      value: await connection.text('validation/log?id=${run['run_id']}'),
    ),
  );
}

class ProjectAnalysisBloc extends FeatureBloc {
  ProjectAnalysisBloc(super.connection, this.path)
    : super(initial: const {'loaded': null});
  final String path;

  Future<void> refresh({bool force = false}) async => read<void>(
    'analysis',
    () async => FeatureResult(
      patch: {
        'loaded': await loadProjectAnalysis(
          connection.request,
          path,
          force: force,
        ),
      },
    ),
    replace: false,
  );

  Future<bool?> saveOverride(Map<String, dynamic> values) => mutate<bool>(
    'override',
    () async {
      await connection.request('projects/toolchain', {
        'repository': path,
        ...values,
      });
      final report = await loadProjectAnalysis(
        connection.request,
        path,
        force: true,
      );
      return FeatureResult(value: true, patch: {'loaded': report});
    },
    admin: true,
    rethrowErrors: true,
  );
}
