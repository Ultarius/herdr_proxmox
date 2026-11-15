# Juice DOM framework adapter

This private package adapts the bloc runtime from Juice **2.1.0** for a
client-only Jaspr build. The existing Flutter dashboard continues to resolve the
published Juice package. Only `web/jaspr_dashboard` overrides it with this package.

The upstream bloc, use-case, concurrency, lifecycle, status-emission and typed
telemetry implementations are retained. Flutter foundation imports resolve to a
small pure-Dart notifier/annotation shim; Flutter widgets and
`JuiceWidgetSupport` are omitted. Logger and rebuild-group exports remain
available. This preserves upstream core logic while keeping the browser build
independent of Flutter and `dart:ui`; it is not a replacement for every Juice
Flutter widget or extension.

The adapter retains the upstream `package:juice/testing.dart` API and
`src/testing` helpers. It excludes the Flutter async builder, state selector and
UI barrel/widgets, keeping `rebuild_group.dart` and the framework-independent
parts of `widget_support.dart`. Its main barrel exports the pure-Dart subset.
Upstream source is otherwise retained, apart from redirecting Flutter foundation
imports to the shim, removing its redundant annotation import from
`cancellable_event.dart`, and removing Flutter-only exports. The retained testing API
supports core bloc contracts; it does not supply Flutter widget APIs or tests.

Use-case builders require an explicit concurrency mode in 2.0. Dashboard
registrations use `UseCaseBuilder.typed` so the event/use-case pairing is checked
by Dart. The 2.0 runtime also includes typed `JuiceTelemetry` records and removes
the former vendored `Bloc`/`BlocBase`/`Emitter`, event-transformer and global
resolver APIs. Those 1.x compatibility files are not carried forward. Juice 2.1
adds bloc-to-bloc `Reaction`s, owned `sendAfter` timers, `EventConcurrency.singleflight`
and multi-bloc `juiceFlowTest`; the retained testing barrel includes all three.

Source: the [Juice 2.1.0 pub.dev archive](https://pub.dev/api/archives/juice-2.1.0.tar.gz),
published **13 November 2026**. Verified archive SHA-256:

```text
83b23be59f15648a8e07cfcbde9f2a9a6658cce4edf395d33dd50c4d8e2ac737
```

The source retains the upstream MIT license in `LICENSE`.

The DOM `ValueNotifier` must be covered by adapter tests and by shared bloc
contract tests. Framework upgrades require analyzer, adapter and dashboard
regression checks: retaining upstream logic does not establish that every
event's scheduling or failure behavior is unchanged across major versions.
