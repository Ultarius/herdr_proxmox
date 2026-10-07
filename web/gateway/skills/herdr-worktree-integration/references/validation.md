## Validation failures and permission requests

A test failure is not permission to investigate or change container infrastructure.
Inspect the failing test and implementation inside the assigned checkout first.
If a test mocks root identity but performs a real privileged operation (for example
`os.chown`), report a test isolation defect with the command, traceback and file
evidence. Do not work around it with sudo, Docker, user namespaces, ownership
changes, or reads of `/etc/subuid` and `/etc/subgid`. Fix tests only when the task
authorizes edits; a validation-only request requires reporting the failure.

Do not request broad access such as `/etc/*` to make validation pass. Report the
exact required operation and path if an assigned check truly needs permission.
Preserve a successful merge and its recovery ref; return tests `failed` for a
check that ran and failed, or `not_run` for a check that could not run. Never
repeat the merge to retry validation. Stop and report instead of expanding scope.
