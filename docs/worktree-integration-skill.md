# Worker integration skill

The assigned worker merges an exact supplied commit in its existing worktree.
The gateway pins the recovery snapshot before delivery, serializes work per agent
and verifies incorporation and conflict state. The coordinator summarizes supplied
evidence. It does not perform merges or require access to workers' checkouts.

The canonical procedure is
`web/gateway/skills/herdr-worktree-integration/SKILL.md`. The gateway includes it in
merge requests so agents on older commits receive the current procedure without
depending on skill discovery or reading files outside their checkout. Dashboard
updates copy the entire gateway directory, including the skill. No relaunch is
needed to receive the procedure in a newly delivered merge request. Already
delivered prompts retain their original instructions.

For native project discovery, copy the canonical `SKILL.md` (not this guide) to
`.agents/skills/herdr-worktree-integration/SKILL.md` in the target project and
commit it. OpenCode supports this location, including inside Git worktrees.
Existing worktrees receive it when they incorporate that commit. Avoid multiple
different copies under the same skill name. Runtime discovery support varies;
the dashboard's inline delivery is the fallback, not a permission bypass.

Keep Herdr's terminal-control skill separate. Export the release-matched official
skill with `herdr --skill` and install it in a location supported by the chosen
agent (for OpenCode, `.agents/skills/herdr/SKILL.md` locally or
`~/.config/opencode/skills/herdr/SKILL.md` globally). Review existing files before
replacing them. The `HERDR_ENV=1` guard applies before Herdr control commands;
it is a workflow guard, not a sandbox or proof of Git authorization.

Sources: [Herdr agent skill documentation](https://herdr.dev/docs/agent-skill/)
and [OpenCode skill discovery](https://opencode.ai/docs/skills/).
