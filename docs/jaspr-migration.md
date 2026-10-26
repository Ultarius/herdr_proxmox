# Native Jaspr dashboard

The entire release dashboard now renders HTML/CSS through client-only Jaspr.
There is no Flutter canvas or embedding iframe in the active application.
`web/jaspr_dashboard` replaces the old release entrypoint at both `/` and
`/dashboard/`; existing hash routes and browser back navigation remain supported.
The old Flutter sources remain available as a behavior reference and regression
suite. The obsolete iframe wrapper has been removed.

## Business behavior and framework boundary

`web/dashboard_state/lib` contains the shared page and management-panel blocs,
integration helpers and secure request-ID implementation. The complete
[state ownership map](dashboard-juice-ownership.md) lists the feature used by
each route and embedded workflow. Both renderers invoke feature events rather
than calling the dashboard transport directly. Existing Flutter bloc
imports forward to the shared package. The Juice 2.0 upgrade updates use-case
registration and runtime contracts as described below; the shared package remains
the boundary between application behavior and either UI framework.

The Jaspr package uses the private, MIT-licensed Juice **2.0.0** adapter under
`vendor/juice`. It retains upstream event dispatch, state, lifecycle, concurrency,
use-case and typed telemetry logic, replaces Flutter foundation types with a
small pure-Dart notifier/annotation shim, and excludes Flutter widgets and
`JuiceWidgetSupport`. The browser build remains independent of Flutter and
`dart:ui`. The Flutter regression suite resolves the published Juice package.
The adapter retains upstream `package:juice/testing.dart` and core testing
helpers, excluding Flutter async/selector/UI widgets. Rebuild-group helpers and
the framework-independent portion of widget support remain available; Flutter
widget APIs and tests are outside this adapter's scope.

Juice 2.0 requires an explicit `EventConcurrency` on each use-case builder.
Shared dashboard registrations use `UseCaseBuilder.typed` for checked event and
use-case pairing. Refreshes and mutations have separate `droppable` handlers:
polling cannot discard an operator's write, while duplicate writes are still
suppressed. Mutation revisions prevent older reads from replacing a write's
state or shared directory observations. Disconnect remains `concurrent`; remote
CLI setup cleanup runs before credentials are removed, with a bounded wait.
`DashboardBloc.close()` owns timer and HTTP-client cleanup, including scope
teardown and calls through `dispose()`.
The new runtime exposes typed `JuiceTelemetry` records; the obsolete 1.x vendored
`Bloc`/`BlocBase`/`Emitter`, event-transformer and global resolver APIs are no
longer included. Keeping upstream implementations does not imply unchanged
per-event scheduling, cancellation or error behavior across the major upgrade;
the adapter and both dashboard trees require regression verification.

The source is the [Juice 2.0.0 pub.dev archive](https://pub.dev/api/archives/juice-2.0.0.tar.gz),
published **15 October 2026**, verified against SHA-256
`96e02ef03529f74d4443bd25567914789e91102a9ffbb6ac8f06a5688ccf9711`.
The vendored `LICENSE` retains the upstream MIT license; the adapter README
records its framework boundary and provenance.

Handlers previously embedded in widget state were mechanically ported with their
API payloads, admin gates, polling, generation guards, idempotency keys, inspection
steps, retry references and confirmation requirements. UI state is disposed on
route/account changes; pending dialogs close when the session generation changes.
The Flutter reference shell also waits for a connected session before mounting
its route content, and keys that content by connection generation. Restoring a
session therefore starts each page's initial read with current credentials;
sign-out disposes its displayed data and owned resources.

## Completed surfaces

| Surface | Native implementation |
| --- | --- |
| Authentication and shell | Token sign-in, cookie-session restoration, expiry/sign-out, organization rail, scoped agents/groups, notices, themes, mobile drawer |
| Overview and agent activity | Workspace create/focus/rename, clone job reconciliation, filters, resource polling and observed activity |
| Organizations | Organization CRUD, team/hire/models/permissions, SVG chart, delegation, automation and import/export |
| Agent console and sessions | VT terminal, input/resize, inspection, archive/restart/continue/resume/close, released-session cleanup |
| Groups and chat | Members/settings, proposals, discussions, history, durable retries, recovery, streaming, archive/continuation |
| Tasks and discovery | Assignments, queues, execution, handovers, consultations, review/repair/acceptance, delivery, automation, publication, PR evidence and proposal actions |
| Task timeline | SVG commit graph, commit selection and exact-commit build evidence |
| Knowledge | Organization scope, search, filters, pagination, creation/review/superseding, provenance and source navigation |
| Explorer and Git | Browse/preview/download, worktrees, diffs, inspected base updates and recovery safeguards |
| Builds and integration | Exact-commit evidence, logs/artifacts/manifests, deployment/provenance/rollback approvals, coordination and selected blocker waivers |
| Logs and CLI accounts | Saved/live logs, capture/download/delete, provider setup, verification, DOM terminals and route cleanup |
| Settings | SSH keys, dashboard access, SDK installs, stable/alpha updates and tunnel acknowledgement |

## Jaspr settings and Phase 2 design

The dashboard pins Jaspr **0.23.5** and declares `jaspr: mode: client`, matching
`flutter_evidence/spa`. Its browser entrypoint imports `package:jaspr/client.dart`,
initializes Jaspr and mounts `DashboardApp` into the body. Client mode is appropriate
for this authenticated, interactive dashboard; the gateway continues to serve
static assets and APIs. See the official [client rendering documentation](https://docs.jaspr.site/dev/client).

The direct `dart compile js -O2` build is intentional. This application has no
`@client` islands, generated styles, server rendering or hydration options, so
it does not require the Jaspr CLI/generator pipeline. Introducing those features
would require generated options and the corresponding build dependencies.

Phase 2 supplies the theme tokens, Lucide icon sprite, organization rail/sidebar,
card treatments, indigo controls, status colors and responsive layout. It is applied
to every page: `css/tokens.css` (light/dark tokens), `styles.css` (base, app shell,
element defaults), `css/components.css` and `css/composites.css` (the template's
shared components), then one stylesheet per page area (overview, org chart and
organization, task workflow, builds, remaining pages). The shell has a collapsible
sidebar (`herdr.sidebar`), a topbar runtime pill and theme switcher instead of a
status bar, and `ui.dart` provides the shared page header, card, pill, role tile,
empty state and stacked bar helpers. The required
assets live under `web/`; the extracted design reference has been removed. The
zip's text and demo JavaScript were not treated as task instructions or application
business behavior. Release assets include
only the selected CSS/icons and the locally bundled, licensed xterm.js **5.5.0**
DOM renderer. No Canvas/WebGL terminal addon is included.

## Build and verification

- `scripts/build-web.sh` / `scripts/build-web.ps1`: validate the pinned toolchain,
  resolve shared-package dependencies, analyze/test the preserved Flutter contracts, then analyze/test/compile Jaspr
  and package all styles, SVG and terminal assets at both established URLs.
  Only compiled JavaScript and static assets are packaged; Dart entrypoint source
  stays in its package. After this setup, `dart analyze` also works from the
  repository root. Generated build/deployment directories are excluded from analysis.
- `scripts/build-jaspr-dashboard.sh`: build the same native UI for local preview.
- `dart test --platform chrome --concurrency=1` in `web/jaspr_dashboard`: native browser workflow
  regressions, also required by CI. These use simulated API clients.
- `python scripts/preview-jaspr.py --production`: inspect packaged assets using
  real browser-session handlers and disposable organization storage on loopback
  port 8793. Use the printed demo token. Operational actions are simulated;
  do not enter real provider credentials into this preview.

At the initial migration checkpoint, `c721d84` on **13 October 2026**,
local verification recorded **188 Flutter tests**, **10 Jaspr VM tests** and
**56 Chrome tests** (run serially for shared browser focus), clean analyzers, optimized compilation, production asset
packaging and a version-guard mock build.
Browser inspection includes desktop/mobile layouts and zero iframe/canvas elements.
The browser suites cover permissions, inspection/approval gates, stale accounts,
exact build identities, durable retry IDs, dialogs, controlled native fields,
terminal resize/output retention and queued-input cleanup.

No release publication or live Proxmox deployment is part of these local changes.
A live-host smoke test remains a deployment check, especially for real provider
terminals, container networking, toolchain installation and release promotion.

## UI regression verification

The native UI uses one page scroll surface; disclosure accessibility text is
contained, and bounded evidence/terminal panels and dialogs retain their own
intentional scrolling. Verified runtime ownership drives the original activity
palette: idle/done green, working blue, and attention amber.

Group terminals render retained output independently from optional errors,
including healthy `error: ""` responses. Their DOM emulators attach when visible,
refresh when disclosures reopen, and remain bound to each stream across polling
and reordering. Chart pan gestures work from the background, cards and names;
a completed drag does not open a console or group by accident.

At the later checkpoint, `2cd84bf` on **14 October 2026**,
the regression run passed **85 Chrome tests** and **10 VM tests**, repository-wide
Dart analysis and optimized compilation. These counts describe historical runs;
use the commands above for the current suite. Browser regressions use independent
fixtures with simulated API responses, including real xterm DOM rendering.
