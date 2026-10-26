# Dashboard state ownership

Both dashboard renderers use the feature implementations in
`web/dashboard_state/lib`. The Jaspr application uses the vendored Juice 2.0
core; the Flutter reference application uses published Juice 2.0. Backend reads,
mutations, operation status, server observations and polling belong to these
shared blocs. Renderers subscribe to feature state and close owned features on
disposal.

| Page or surface | Shared Juice owner |
| --- | --- |
| Authentication, session and workspace actions | `DashboardBloc` |
| Shell directory and integration notices | `ShellBloc` |
| Overview and agent activity | `DashboardBloc`, `ResourcesBloc`, `CloneJobsBloc` |
| Organization and org chart | `OrganizationBloc`, `OrganizationToolsBloc` |
| Agent console | `AgentConsoleBloc` |
| Discussion group | `GroupBloc` |
| Tasks, task detail and release workflows | `TasksBloc` |
| Project knowledge | `KnowledgeBloc` |
| Builds and deployments | `BuildsBloc` |
| Project explorer and Git changes | `ExplorerBloc`, `GitBloc` |
| Saved terminal logs | `LogsBloc` |
| CLI accounts and setup terminal | `CliSetupBloc`, `CliSetupSession` |
| SSH, access controls, SDKs and updates | `SshAccessBloc`, `DashboardAccessBloc`, `ToolchainBloc`, `DashboardUpdatesBloc` |
| Discovery, automation and collaboration panels | `ProductDiscoveryBloc`, `OrganizationAutomationBloc`, `CollaborationBloc` |
| Integration board and project requirements | `IntegrationBloc`, `ProjectAnalysisBloc`, `ProjectEnvironmentController` |
| Repository connections, build storage and task exports | `RepositoryConnectionsController`, `StorageController`, `TaskBackupBloc` |
| Model discovery and configuration transfer | `ModelDiscoveryBloc`, `OrganizationToolsBloc` |

The historical `Controller` names retain their public APIs for both renderers;
their server state and operations now run through Juice. The task export's
legacy notifier is a Listenable projection of `TaskBackupBloc`, not a second
operation implementation.

## Presentation boundary

Form drafts, filters, tab selection, expanded sections, dialog confirmations,
focus, scroll position, native browser activation and terminal rendering remain
local UI concerns. These may use `setState` or presentation controllers. File
pickers and download/save handoffs remain in the renderer; their backend evidence
is obtained by a feature event. Confirmation and inspection gates remain in the
workflow before its mutation is dispatched.

Features use explicit Juice concurrency. Read and write admission is separate;
stale reads cannot overwrite newer observations or mutation results. Mutations
are never made restartable to simulate cancellation of an already sent write.
Session and privilege changes invalidate pending observations, and feature close
releases owned polling and subscriptions. Cleanup capabilities remain scoped to
the session that owns the remote CLI terminal.

## Verification

The Flutter, VM and Chrome suites exercise the same shared feature code, with
renderer tests covering the actual buttons, dialogs and terminal components.
`tests/test_dashboard_juice_boundary.py` also rejects direct dashboard transport
calls in either renderer. Keep backend operations in a shared feature when adding
new pages or embedded management panels.
