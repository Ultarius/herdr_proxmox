import 'build_timeline.dart';
import 'operations_support.dart';

class UpdateCard extends StatefulComponent {
  const UpdateCard({super.key});
  @override
  State<UpdateCard> createState() => _UpdateState();
}

class _UpdateState extends OperationsState<UpdateCard> {
  Map<String, dynamic>? info;
  List<Map<String, dynamic>> alphas = [];
  String? alphaVersion;
  Timer? timer;
  bool get queued => ['queued', 'running'].contains(info?['state']);
  @override
  void initState() {
    super.initState();
    check();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> check({bool force = false}) => perform((epoch) async {
    final result = await connection.request(
      force ? 'updates/check' : 'updates',
      force ? <String, dynamic>{} : null,
    );
    if (valid(epoch))
      setState(() {
        info = result;
        if (!queued) {
          timer?.cancel();
          timer = null;
        } else {
          timer ??= Timer.periodic(const Duration(seconds: 5), (_) => check());
        }
      });
  });
  Future<void> install({bool alpha = false}) async {
    final version = alpha ? alphaVersion : info?['latest'];
    if (version == null || busy || queued || info?['supported'] != true) return;
    final epoch = connection.generation;
    if (!await confirm(
          'Install $version?',
          '${alpha ? 'This is an experimental feature-branch build for testing. ' : ''}The dashboard briefly disconnects. Your projects, profiles and credentials are preserved. A backup is kept for recovery. Reload after completion.',
          confirmLabel: 'Install update',
        ) ||
        !valid(epoch))
      return;
    await perform((epoch) async {
      await connection.request('updates/install', {
        'version': version,
        if (alpha) 'channel': 'alpha',
        if (alpha) 'confirm_alpha': true,
      });
      if (valid(epoch))
        setState(() {
          info = {...?info, 'state': 'queued'};
          timer ??= Timer.periodic(const Duration(seconds: 5), (_) => check());
        });
    });
  }

  Future<void> loadAlphas() => perform((epoch) async {
    final result = await connection.request(
      'updates/alphas',
      <String, dynamic>{},
    );
    if (valid(epoch))
      setState(() {
        alphas = (result['releases'] as List).cast<Map<String, dynamic>>();
        alphaVersion = null;
        if (alphas.isEmpty)
          error = 'No installable alpha builds are available.';
      });
  });

  /// Lookup, completion and capability messages as template callouts.
  List<Component> _messages(Map<String, dynamic> info) => [
    if (info['check_diagnostic'] != null || info['check_error'] != null)
      callout('warning', 'triangle-alert', [
        text(
          info['check_diagnostic'] ??
              'Release lookup unavailable: ${info['check_error']}',
        ),
        if (info['check_error'] != null && info['check_failures'] != null)
          text(
            'Release lookup failed ${info['check_failures']} times since ${info['check_failed_since'] ?? 'the first failed check'}. Counts reset when the gateway restarts.',
          ),
      ]),
    if (info['state'] == 'complete')
      info['check_error'] == null
          ? callout('success', 'circle-check', [
              text('Update complete. Reload to use the new dashboard.'),
            ])
          : callout('info', 'info', [
              text(
                'The previous installation completed. The current release check failed.',
              ),
            ]),
    if (info['error'] != null)
      callout('danger', 'octagon-alert', [text(info['error'])]),
    if (info['recovery_error'] != null)
      callout('danger', 'octagon-alert', [
        text('Recovery failed: ${info['recovery_error']}'),
      ]),
    if (info['backup_verification'] is Map)
      callout('info', 'info', [
        text(
          info['backup_verification']['method'] == 'manifest'
              ? 'Backup contents verified against its manifest.'
              : 'Legacy backup checked using build identity, required files and database integrity.',
        ),
      ]),
    if (info['deployment_mode'] == 'local')
      callout('info', 'info', [
        text(
          'Release installation is disabled while a local build is deployed.',
        ),
      ])
    else if (info['supported'] != true)
      callout('warning', 'triangle-alert', [
        text('Install the updater service before using dashboard updates.'),
      ]),
  ];

  /// Alpha channel: browse, a compact list of the builds, pick and install.
  /// The <details> has no attributes, so refreshes never close it.
  Component _alphaBuilds() => el('details', [
    el('summary', [
      icon('flask-conical'),
      text('Alpha builds · feature branch testing'),
    ], classes: 'updates-alpha__summary'),
    el('div', [
      actions([
        button(
          'Browse alpha builds',
          loadAlphas,
          disabled: busy,
          iconName: 'search',
          small: true,
        ),
      ]),
      if (alphas.isNotEmpty) ...[
        el('p', [
          text(
            '${alphas.length} alpha build${alphas.length == 1 ? '' : 's'} available',
          ),
        ], classes: 'overline'),
        el(
          'ol',
          [
            for (final release in alphas)
              el(
                'li',
                [
                  release['version'] == alphaVersion
                      ? dot('accent', 'filled', size: 'md')
                      : dot('neutral', 'hollow', size: 'md'),
                  el('span', [
                    mono(release['version']),
                    if (release['version'] == alphaVersion)
                      pill('Selected', 'accent'),
                  ], classes: 'updates-alpha__head'),
                  if ('${release['notes'] ?? ''}'.isNotEmpty)
                    el('span', [
                      text(release['notes']),
                    ], classes: 'updates-alpha__notes'),
                ],
                classes: 'updates-alpha__item',
                attributes: {
                  'data-selected': '${release['version'] == alphaVersion}',
                },
              ),
          ],
          classes: 'updates-alpha__list',
          attributes: {'role': 'list', 'aria-label': 'Alpha builds'},
        ),
        el('div', [
          select(
            'Feature branch alpha build',
            alphaVersion ?? '',
            {
              '': 'Select build',
              for (final release in alphas)
                '${release['version']}': '${release['version']}',
            },
            (value) =>
                setState(() => alphaVersion = value.isEmpty ? null : value),
          ),
          button(
            'Install selected alpha',
            () => install(alpha: true),
            variant: 'primary',
            iconName: 'download',
            disabled:
                busy ||
                alphaVersion == null ||
                info?['supported'] != true ||
                queued,
          ),
        ], classes: 'updates-alpha__pick'),
        if (alphaVersion != null)
          el('p', [
            text(
              alphas
                  .where((r) => r['version'] == alphaVersion)
                  .firstOrNull?['notes'],
            ),
          ], classes: 'updates-card__notes'),
      ],
    ], classes: 'updates-alpha__body'),
  ], classes: 'updates-alpha');

  @override
  Component build(BuildContext context) => card(
    'Dashboard updates',
    [
      if (info != null) ...[
        facts([
          ('Installed', mono(info!['installed'])),
          (
            'Latest',
            Component.fragment([
              mono(info!['latest'] ?? 'Unavailable'),
              if (info!['available'] == true)
                pill('Update available', 'accent'),
            ]),
          ),
        ], classes: 'updates-card__versions'),
        ..._messages(info!),
        if ('${info!['notes'] ?? ''}'.isNotEmpty) ...[
          el('p', [text('Release notes')], classes: 'overline'),
          el('p', [text(info!['notes'])], classes: 'updates-card__notes'),
        ],
      ],
      actions([
        button(
          'Check for updates',
          () => check(force: true),
          disabled: busy,
          iconName: 'refresh-cw',
        ),
        button(
          'Install update',
          install,
          variant: 'primary',
          iconName: 'download',
          disabled:
              busy ||
              info?['available'] != true ||
              info?['supported'] != true ||
              queued,
        ),
      ]),
      _alphaBuilds(),
      feedback(),
    ],
    iconName: 'package',
    subtitle:
        'Stable releases from Ultarius/herdr_proxmox. Updates are installed when you choose.',
    actions: [if (info != null) statusPill(info!['state'])],
    classes: 'updates-card',
  );
}
