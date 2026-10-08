# Automatic task capture and validation

## What

Tasks have an administrator-controlled, opt-in automatic capture and validation policy. It is disabled by default and requires the durable build service. The Tasks page shows a collapsible Git timeline with captured commit-parent edges, the recorded base, current candidate and SHA-specific build colors. Previous candidate builds remain available separately and never count as evidence for a newer candidate.

## Why

Agent idle status is not completion evidence. A worker can pause for permission, fail, or leave uncommitted work. Likewise, a successful build for an older SHA cannot validate a later merge commit. Automatic publication, merging and deployment are separate decisions.

## How

1. Create and launch a task. Its prompt asks the agent to commit changes and write a completion receipt last, inside its ignored local guidance directory.
2. An administrator enables Automatically capture and validate. Installation of the durable build service is required; the gateway does not install tools or silently use its in-process runner.
3. Every ten seconds the task reconciler checks the original live session identity and idle status, outstanding agent jobs, the assigned branch, and the receipt. The receipt must match the current HEAD, launch ID and fresh session token. Candidate capture also requires a clean checkout with no Git operation in progress.
4. Capture records the exact candidate SHA and up to twelve commits with real parent edges. The reconciler queues one build per task and SHA using the existing durable build identity. An old completion file is invalid after a session restart because the token changes. Restart provides the new receipt protocol without replaying the prior task.
5. Build results are attached to their SHA. Failed builds are not retried automatically; inspect and use Validate candidate to retry explicitly. A new committed result needs a matching new receipt. Waiting reasons are persisted and visible.
6. Review, publishing a branch or draft PR, GitHub merge, base update and deployment remain separate actions. Existing manually captured candidates can be queued under the enabled policy without requiring an agent to recreate their receipt.

The timeline is a bounded view of actual Git history, not a pipeline diagram. Green requires a completed build with required checks verified. Red indicates build failure. Other nodes have no verified pass. Missing edges outside the captured history are not invented. Legacy candidates need recapture to load parent data. Tree equality with the last fetched configured base is informational: it does not prove publication, deployment or independent validation.

## Completion receipt

The launch prompt supplies the exact path, run ID and token. The worker writes JSON with outcome set to complete, commit set to the full committed HEAD, run_id, token and tests as an array of actual check results. Worker-reported tests are separate from runner evidence; they do not grant a verified pass.

## Verification

Regression coverage exercises matching completion, stale session tokens, wrong commits, uncommitted work, service prerequisites, build deduplication, no automatic failure retries, real merge graph rendering, historical evidence separation, mobile scrolling and light/dark themes. Deployment and live LXC validation must be performed after installing this batch.
