# Repository connections

Administrators can configure outbound GitHub access in **Clone a project →
Repository connections** in both dashboards. This is separate from incoming SSH
access keys, CLI-provider login, and GitHub PR publication settings.

Flutter loads this panel when **Add a project or workspace** is expanded;
Jaspr mounts it with the clone card. The collapsed Flutter setup intentionally
avoids fetching connection configuration. Both populate the clone dropdown when
their panel loads and refuse retries with an unavailable saved connection.

1. Give the connection a name and enter `owner/repository`, or use token discovery
   below to fill both fields from a repository selection.
2. Choose **SSH deploy key**, **Fine-grained GitHub token (HTTPS)**, or
   **Configured GitHub account (HTTPS)**.
   For a fine-grained token, use the link in this form, choose the correct resource
   owner and **Only select repositories**, then grant **Contents: Read-only**
   (Metadata read access is included). Organization approval may be required.
   Paste the token into the password field. **Discover repositories** lists
   a page of repositories visible to that token using GitHub's authenticated-user repository
   API (`https://api.github.com/user/repos`). Choose a **Discovered repository**
   to fill the repository and an empty connection name. **Load more repositories**
   fetches another page of up to 100 results.
   GitHub's `Link` header determines whether more pages exist, regardless of page
   length. The panel explicitly reports when more repositories are available.
   Listings stop after 1,000 pages (up to 100,000 repositories) and display a
   listing-limit message if GitHub still reports another page. Manual entry
   remains available. Pagination is not a snapshot: repository creation, renaming
   or deletion between requests can shift results and omit a repository. Restart
   discovery or enter its name manually when needed.
   Discovery uses the pasted token
   without saving it or changing the dashboard's publishing credentials. Editing
   the token clears the results; account changes also discard outstanding results.
   Metadata visibility does not prove Contents read permission, so test repository
   access after saving. Empty results suggest checking token repository selection
   or organization approval. Errors report invalid/expired credentials, denied or
   rate-limited access, or unavailable responses; manual entry remains available.
   Click **Save token connection** when the repository selection is ready.
   New connections accept PATs beginning with `github_pat_` (fine-grained) or
   `ghp_` (classic); a recognized prefix does not verify validity or permissions.
   OAuth/App credentials and refresh tokens are not accepted by this PAT form.
   The token field requests no browser autofill, spellcheck or autocorrection.
   It is cleared after a successful save; a rejected or failed save retains the
   masked paste for correction or retry while the dashboard session remains active.
   It is also cleared on account changes, dashboard disconnection, or when leaving
   the token authentication option. Browser extensions may
   override autofill preferences.
   This saves a separate credential for this connection without changing the
   dashboard's GitHub publishing configuration.
   Do not paste a token into the repository URL. Tokens expire or can be revoked;
   use **Rotate token** on its saved connection to replace it in place. Project
   assignments and the connection ID remain unchanged. Enter a replacement,
   click **Save replacement token**, then **Test repository access** again.
   Rotation refuses a connection currently used by a managed Git operation.
   A failed credential-file replacement retains the old token; the access verdict
   is reset before replacement so it cannot claim verification for a changed
   credential. Revoke the old token on GitHub separately.
3. For SSH, create the key, copy its public key (HTTP clipboard fallback downloads
   a public `.pub` file), and open the repository's GitHub deploy-key settings.
   Add it as read-only for clone/fetch/pull. Private key bytes never enter a response.
4. For SSH, click **Verify GitHub host key**. The gateway scans only github.com Ed25519 and
   compares the result to GitHub's published pinned fingerprint. An unexpected key
   is refused, never automatically trusted. No global known_hosts file is edited.
5. Click **Test repository access**. This runs a bounded, non-interactive
   `ls-remote HEAD`; the result records an observation time, not a future guarantee.
   HTTPS uses the connection's saved token, or the current primary GitHub credential
   when **Configured GitHub account** was selected. It does not create or expand
   token permissions or alter the publishing allowlist. Tokens are stored in
   separate mode-0600 files, never in connection listings, clone jobs, Git remotes,
   or command arguments. A push requires Contents write access; the access test
   verifies only repository reads.
   A successful query with no advertised `HEAD` (Git exit 2, for example an empty
   repository) also verifies access. It does not imply that any commits exist or
   that the credential can push.
6. Select the connection in the clone form. Its repository/protocol must match.
   Existing container credentials and public cloning remain available without a
   managed connection. Failed/interrupted jobs show their error and can populate
   a new folder for retry; old folders are never removed or reused automatically.

A successful managed clone records its connection. To configure an existing
checkout, enter its direct child folder name under **Existing project folder** and
click **Assign to existing project** on the connection. Assignment verifies the
origin identity and refuses linked worktrees, paths outside projects, or other
repositories. Local Git configuration records only SSH identity selection or an
askpass command with repository/configuration paths, never tokens or private key
bytes. Normal agent Git fetch/pull inherits this project-local configuration;
linked worktrees share the common Git directory. Gateway fetch also resolves the
recorded binding and rechecks origin against the connection before credentials
are provided. Global SSH and Git configuration are preserved.

Reassigning between HTTPS and SSH removes the old protocol's project-local
settings only when their values still exactly match those generated by Herdr.
Later operator overrides are preserved. A retry whose saved connection has not
loaded or is no longer available requires refreshing connections and choosing an
identity; it never silently submits the missing connection ID.

Managed connections currently support github.com only. Each connection names one
repository. Configure multiple connections for multiple repositories. This does
not authorize PR creation, publication, merging, deployment, submodule access or
Git LFS credentials. Those workflows keep their existing configuration.

The API is `GET /api/projects/connections` and `POST /api/projects/connections`
with actions `discover`, `create`, `rotate`, `verify_host`, `test`, `bind`, `unbind`, and `remove`. Listing, configuration and
managed cloning are administrator operations. `projects/clone` accepts optional
`connection_id`; durable job records retain that identifier, the credential-free
URL and folder so retries can be reviewed. Requests have existing same-origin
session checks. The shared client allows time for bounded host/access probes.

`discover` is an administrator-only **POST to the existing connections endpoint**,
with JSON body `{"action":"discover","token":"<PAT>","page":1}`. The raw PAT
is sent from the browser to the gateway in this request body, then used by the
gateway to authenticate a bounded request to `https://api.github.com/user/repos`.
Use HTTPS for the dashboard connection when sending credentials over a network.
Discovery does not save the PAT or echo it in its response. It returns
`repositories` (repository names, private/archive flags), `next_page` (integer or
null), and `limit_reached` (boolean). Page numbers must be between 1 and 1,000.
Subsequent pages require resubmitting the PAT. It does not create a connection or
change GitHub publication settings. This repository listing is unrelated to the
product-discovery workflow.

`create` accepts `label`, `repository` (`owner/repository`), `kind` (`ssh`,
`github`, or `token`), and a required `token` only for `kind: token`. Supplying a
token for another kind is rejected. `rotate` accepts `id` and `token` and applies
only to connection-specific token credentials. Neither action echoes token bytes.

Keys and metadata live under the gateway configuration directory's
`repository-connections/` (directory 0700, private files 0600). Metadata records
public keys, repository identities, bindings and test outcomes.
The configured account token stays in the existing `github.json`; connection-specific
tokens stay in `repository-connections/<connection-id>/token.json`. Askpass releases a token only for
username/password prompts naming the exact selected GitHub repository. Redirects
and credential helpers are disabled for managed gateway operations. Error output
from Git/SSH is not copied into test results. The gateway and agents share the
`herdr` Unix account: file modes protect against other users, not same-user agents.
Repository connections, private keys and tokens are not part of configuration export/import;
reconfigure them on a replacement container. Dashboard upgrades preserve the
configuration directory. No live keys or remote grants are created by tests.

## Removing old clones

Administrators can use **Remove clone** beside a finished, failed, or interrupted
entry in **Clone a project**. **Remove history entry** clears only that entry;
the project folder and saved repository connection remain available.

To delete the folder too, select **Also permanently delete the cloned folder**,
type its exact folder name, then choose **Delete folder and history**. This
permanently deletes all files in that folder, including local changes. Running
clones cannot be removed. Folder deletion also refuses projects used by
workspaces, managed jobs, or configured teams/agents, assigned repository
connections, linked worktrees, and unsafe paths. Unassign a repository connection
and close or reconfigure the relevant workspaces and project assignments first.
The remote repository and GitHub credentials are not deleted or revoked.
Older clone entries without a recorded original folder identity support
history-only removal. The dashboard disables their folder-deletion option, and
the gateway also refuses deletion. Inspect those folders before deleting them
manually; their current contents may belong to a replacement project.

The administrator-only endpoint is `POST /api/projects/jobs/remove` with `id`,
optional boolean `delete_folder` (defaults to `false`), and `confirm_folder`
matching the recorded folder name when deletion is requested. The gateway selects
the folder from its stored clone job; requests cannot supply an arbitrary path.

## Reusing a token for publishing

To reuse a saved PAT for task publication, open **Tasks → GitHub publishing** and
select its connection under **Publishing token source**. The selected connection
must match the branch destination. Only its ID goes through the browser; the
gateway validates and copies the token into publishing configuration. Saving a
clone connection alone does not enable publishing. A token with Contents read
access is insufficient for publishing. A saved connection in direct mode allows
only its named repository in the publishing allowlist. For fork publication,
the allowlist names the upstream and the branch destination must match the
connection's fork. Every allowlist entry must target that same destination;
enter a token manually to publish to multiple destinations (up to 20
allowlisted repositories).
Publishing needs Contents write and, when used for pull requests, Pull requests
write. SSH deploy keys cannot authorize the GitHub API.
Publishing retains its own copy: rotating or removing a connection does not
change that copy. Reconfigure publishing explicitly to adopt a new token.

## Retiring a connection

In either dashboard, click **Unassign project** beside every assigned checkout.
This removes matching Herdr-generated values from that checkout's local Git
configuration and clears its binding. Current operator overrides are preserved,
including other values for the same setting. It cannot reconstruct settings
overwritten when the connection was originally assigned; restore those from your
own configuration backup if needed. Stale requests cannot unassign a replacement
connection, and linked worktrees must be unassigned through their main checkout.

Once unassigned, click **Remove connection**, then **Confirm removal**. Removal
refuses bound connections and credentials in active use by a gateway Git command.
It deletes the metadata record and that connection's generated key/trust files,
including its saved `token.json` for token connections.
It does not delete checkouts, global configuration or the primary GitHub token.
Unexpected key-directory contents are refused for inspection rather than deleted
recursively. Failed key generation and failed connection creation clean up partial
credential files, including `token.json`. A failed retirement can be retried after
resolving its reported error.

Revoke the deploy key or token in GitHub separately: deleting local configuration does not
revoke remote authorization. The limits remain 64 connections and 128 checkout
bindings; retired entries release their slots. API callers use `unbind` with
`id` and direct-child `folder`, then `remove` with `id`; both require an admin.

The published host fingerprint is documented at
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints.
Changing it requires a reviewed source update, not accepting a key returned by a
failed scan. Tests cover rejected origins, wrong host keys, credential prompt
scope, durable bindings/worktree inheritance, clone reservation and UI retry.
