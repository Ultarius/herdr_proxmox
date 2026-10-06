# Moving organization configuration to a clean LXC

In **Org chart → Migration & backup**, select **Export configuration**. Keep the downloaded `herdr-configuration.json` outside the old container before replacing it.

On the new dashboard, connect and open **Org chart → Migration & backup → Import configuration**. Choose the JSON file and review the counts before confirming. Organizations, agents and groups retain their IDs and all stored fields, including archived entries, persona versions, runtime/provider/model/reasoning choices, permissions, accessible paths, project paths, worktree preferences, reporting relationships and membership. Fields added in future versions are retained too.

Import is atomic and does not overwrite different-content records with existing IDs. Re-importing identical records succeeds, so a lost response can be retried safely. It does not start agents or replay jobs. Review project paths and permission settings, clone the repositories and reconnect CLI accounts before launching agents. Missing project directories are preserved in configuration so they can be recreated on the new container.

This is a configuration migration, not a complete container backup. Repository files, worktrees, Git recovery refs, discussion artifacts and history, live sessions, launch/job bindings, CLI credentials and dashboard authentication are not transferred. Back those up separately if needed. Integration coordination is not automatically enabled on the destination.

Imports support format `herdr-configuration`, version 1, up to 2 MB and 1,000 records per collection. Invalid references, reporting cycles and duplicate IDs are rejected before any records are written. Imported prompt fields use the normal creation limits; generated facilitator personas have a larger allowance for their instruction prefix. Exports remain complete even above the import size limit, and the dashboard warns that migration capacity must be increased before replacing the container. Keep the original export until the imported settings have been checked on the new server.
