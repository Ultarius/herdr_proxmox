## Report

Return only the JSON schema requested by the gateway:

```json
{"outcome":"integrated|blocked","blocker":"missing_toolchain|missing_permissions|owner_restriction|read_only_role|task_conflict|state_conflict|unspecified","commit":"full HEAD SHA","tests":{"status":"passed|failed|not_run","summary":"commands, results and any skipped required checks"},"reason":"integration result or precise blocker"}
```

Use `integrated` only when the target is incorporated and conflicts/operations are
resolved. `passed` means every required check was actually run and passed. The
gateway independently checks Git state; the test evidence remains worker-reported.
Write the reply through the dashboard's provided delivery instructions. Include
recovery references in a blocker where useful; restore into a separate recovery
checkout only on an explicit recovery request, never by resetting the working branch.

Attempt a permitted task-local installation before reporting a missing tool.
If it cannot complete, report `missing_toolchain` with the tool, version and
actual failure. Administrator-approved SDK provisioning can resolve the pinned
Flutter/Dart toolchain; it does not guarantee resolution of arbitrary tools.
