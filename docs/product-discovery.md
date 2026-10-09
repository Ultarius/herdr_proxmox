# Product discovery

Product discovery lets a group assess the product without creating a host task first. It produces proposals for operator review. Approved task proposals become ordinary drafts; starting or queuing them uses the existing task workflow.

## First setup

1. Create a read-only **Product** group in the organization. Include the people/agents needed to assess product gaps, such as a reviewer and an implementer. Start its members and facilitator from the group page and ensure they are ready for input.
2. Assign an individual worktree agent to the managed Git repository. Discovery requires a valid assignee for eventual drafts.
3. Open **Tasks → Product discovery** as an administrator. Choose the organization and **Configure discovery**.
4. Select the repository and group. Write the product brief, one to six focus areas, and exclusions. For example: “A new user should reach their first validated task in ten minutes”; focus: onboarding, documentation, reliability; exclude pricing and credential changes.
5. Leave scheduling off for the first run. Save, then choose **Run discovery now**.
6. Inspect the meeting's rubric, cited evidence and proposals. **Create draft** approves a task proposal; **Reject** records a rejection. Open approved drafts to inspect their acceptance criteria and required checks, then launch or queue them normally.

Scheduling is off by default. If enabled, the default interval is 168 hours and the cap is three proposals. Intervals range from 24 to 720 hours and caps from one to five. Enabling scheduling dispatches the first meeting at the next poll if no window is open. Manual and scheduled runs share one root meeting per window. Pausing discovery, or the organization's existing automation policy, stops new dispatch and approvals. It does not interrupt a meeting already sent to an agent. A pending submission is retried using its reserved identity and frozen prompt; scheduled preparation errors have a fifteen-minute backoff.

## Evidence and result contract

The gateway reads a bounded snapshot of the selected repository's committed HEAD: top-level layout, Markdown document inventory, README excerpts, known dashboard/API surfaces and selected empty-state strings. It adds bounded task history, build failures, validation-executor status and the running deployment identity. Missing operational facts and individual test counts remain unknown.

The pack is capped at 14 KB. Hidden paths and credential-named files are excluded, and URLs, provider tokens, JWTs, Basic/Bearer authorization values, secret assignments (including block-style values and private-key blocks) are redacted. Oversized blobs are skipped before being read. This is allowlisting and redaction, not a guarantee that arbitrary sensitive prose can always be detected. Raw logs, environment files and authentication stores are not included. Task evidence is limited to the latest sixty records; missing evidence does not prove a feature is absent throughout the product.

The evidence pack is stored atomically inside the facilitator's ignored checkout cache. The facilitator reads it and shares cited excerpts with members, who retain their own checkout boundaries. Evidence copies that differ during preparation are rejected. These files are writable by the same Unix user as the agents; the checks do not provide isolation against an agent modifying its own files after preparation.

The group returns structured JSON: one adequate/thin/missing assessment for every focus area and zero or more task/initiative proposals. Each proposal needs a problem, impact, description, assignee, acceptance criteria, required checks and citations to supplied evidence IDs. The gateway rejects missing fields, unknown citations, excess proposals and nested initiatives. Valid citations prove provenance, not the correctness of an agent's interpretation; operators still review the proposal.

## Approval, duplicates and planning

All discovery proposals require review. Approval creates a draft, never an automatic launch, push, pull request, merge or deployment. The gateway rechecks organization/repository/assignee eligibility and compares normalized title tokens against a bounded backlog of open and recently completed tasks. A Jaccard overlap of at least 0.65 with at least two shared meaningful words (or an exact normalized title) records a duplicate and links the existing task. Similar wording can produce false positives, and differently worded equivalent tasks can escape this heuristic; the operator can override a duplicate with **Create draft anyway**, which records the overridden task on the draft's source.

Approving an initiative starts one planning meeting using the same evidence and the approved initiative. It may propose at most five flat tasks; the planning meeting returns task proposals only and may omit the rubric. Those proposals also require approval before drafts are created. Nested initiatives are rejected. Meeting, planning and task identities are durable, so repeated approval does not create another copy.

Discovery history shows the rubric, evidence, proposals, decisions, related drafts and group links. Invalid or failed results become `needs_attention`. Inspect and recover the existing group artifact first, then use **Reprocess recovered result**; this revalidates the existing artifact and does not resend a prompt.

## Boundaries

Read-only is the existing group advisory policy, not an OS sandbox. Existing group round limits and timeouts apply. Discovery adds a cadence/proposal budget, not a provider-level token or monetary cap. It runs on the gateway poll loop, so the gateway must remain running for scheduling.

Product direction and discovery management are administrator-only. Ordinary task reads retain their existing access policy, but do not expose the new product direction fields.

Adaptive scoring based on merged/deployed outcomes, automatic discovery-proposal queueing and queue-slack-triggered early meetings are not implemented. The supported loop is direction → discovery → reviewed drafts → existing queue/handoff → validation. Existing task reviews also have an optional improvement lens for grounded documentation, tooling and usability follow-ups.
