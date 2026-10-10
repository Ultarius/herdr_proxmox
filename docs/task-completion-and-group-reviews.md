# Completion and group reviews

## Task completion

Done is an explicit task state, separate from build validation and deployment. A task can be satisfied through upstream work or superseded, even if its task branch was never published. The administrator records a completion reason and confirms inspection; the request pins the candidate and fetched base SHA. Dirty/changed checkouts, stale base decisions and open PRs are rejected. Completion is audited and disables task automation. A missing or unverified check remains not_verified, never a pass.

Every minute the reconciler can finish review-ready tasks automatically only when the candidate file tree exactly matches the fetched configured base, the exact candidate required checks are independently verified, no conflicting job is active and the assigned checkout is clean and unchanged. Divergent or more advanced upstream trees do not prove equivalence automatically. Use reviewed completion for those cases; do not infer acceptance from an old patch or an idle agent.

## Groups and open work

Use Discuss with group on a task to select an active group in the same organization. The group receives the task description, current candidate, reported blocker and up to twenty other open tasks. It is asked to discuss remaining acceptance criteria, evidence, blockers and duplicate work without editing, committing, publishing or deploying.

Automatic group review is opt-in. The reconciler requests one meeting per changed candidate, task state, reported blocker or build state. Existing busy-agent checks remain enforced. An unchanged state does not trigger repeated meetings; interrupted discussions require recovery in the existing group UI, not automatic resend.

Discussion artifacts and their status appear in Activity & agents, with links to the original group. Group artifacts link back to their task. A finalized action plan can contain a fenced JSON object with task_proposals, an array of up to ten proposals. Each proposal contains title, description (including acceptance criteria and required checks), and the `profile_id` of an existing individual worktree agent for the same repository. The assignee does not need to have attended the discussion: the group prompt lists every eligible repository agent and marks who attended, so a reviewer can hand work to an implementer who was not in the conversation.

## Drafts first, queueing on request

A finalized discussion materializes its proposals as draft tasks automatically, bounded by the follow-up limits; no proposal executes work by itself. Automatic queueing is the opt-in: with the organization's follow-up automation or the group's task creation enabled, qualifying proposals are created and queued through the ordinary assignment scheduler. Otherwise, and for any `needs_review=true` proposal, the task stays a draft that an operator starts. Each proposal produces one deterministic task identity, so repeated evaluation or acceptance cannot duplicate it. An unfinished or malformed meeting creates nothing.

This closes the task-to-discussion-to-draft loop. It does not enable publication, GitHub merge, base updates or deployment; those remain separate operator actions. GitHub publishing configuration is not required for local discussions or local drafts.
