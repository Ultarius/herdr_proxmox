# Task pages and evidence

The Tasks list is a compact index: title, repository, assigned identity, state, next step and last activity. Open task navigates to /tasks/<task-id>, which can be bookmarked or opened directly. The Tasks sidebar section stays selected.

## Task sections

- Overview shows the original task description, including acceptance criteria and required checks, the assigned identity, completion/blocker information and automation settings. A primary action offers implementation start, exact-commit validation or review when appropriate.
- Activity & agents shows recorded participants, their launch-time roles/runtime/model when available, associated sessions and prior restarted sessions. Worker completion reports remain separate from independent build results. Activity entries have readable labels; repeated adjacent identical entries are grouped. Up to 500 retained audit records remain expandable.
- Changes shows the branch and immutable base/candidate identities, candidate diff, PR/merge information and a vertical Git graph. Selecting a commit opens its bounded first-parent diff and SHA-specific build evidence; root commits are compared with the empty tree. Viewing history is read-only and does not recapture a candidate.
- Checks & builds shows current-target evidence, previous candidates separately, checks/reviews and authenticated log download. Artifacts and deployment actions remain on Builds & deployments.

## Git graph

The detail API queries at most 30 commits in the recorded base-to-candidate range and includes the base. Commit messages, authors and parent edges come from Git. The graph shows newest commits first, compact branch lanes at the left, and separate build badges. Missing history is an explicit empty state with a read-only refresh action; disconnected placeholder dots are not fabricated. Merge diff inspection uses the first parent and labels that comparison. All commit links are limited to the displayed task history.

## Attribution

New tasks preserve the assigned profile snapshot and each task launch preserves a participant identity. Legacy task details can recover identity from the saved launch profile, never from the current renamed profile or a Git author. Missing historical identity is stated explicitly. Sessions are linked by task ID or its recorded launch ID, not by every conversation involving the same agent. Session tokens and private launch parameters are excluded from detail responses. The view does not infer that every assigned session authored a commit.

## Tests

Tests exercise direct route parsing, list/detail/back navigation, four sections, participant/session history, grouped activity, read-only commit selection, stale build separation, missing task/history states, mobile and desktop layouts, both themes, exact merge parents, historical identity preservation, immutable storage during detail reads, authenticated commit inspection, invalid commit rejection and storage failure handling. Existing explicit publication and operator-role tests remain in place.

Task detail inspection and GitHub publication retain the existing access model: authenticated operators can inspect task evidence; mutation and publishing remain administrator-controlled. Deployment is still separate.
