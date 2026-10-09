# Feature branch alpha builds

Alpha builds test Herdr Proxmox (gateway, dashboard and matching installer).
They do not replace the separate herdr.dev CLI distribution.

## Publishing

Create `alpha/<feature>` and push a HEAD commit whose subject starts exactly with
`alpha:` (case sensitive):

```sh
git switch -c alpha/new-login-screen
git commit -m "alpha: test the new login screen"
git push origin alpha/new-login-screen
```

Ordinary commits run CI but publish no alpha. PRs, manual runs and stable branches
cannot invoke alpha publication. A prefix on an earlier commit in a multi-commit
push is insufficient: the pushed HEAD must carry it.

After installer/gateway checks and the pinned Flutter/Jaspr build pass, the same
run publishes an installable GitHub prerelease. It does not depend on a tag push
starting another workflow. Tags contain a feature slug, 12-character SHA-256 branch
identifier and workflow run number: `alpha-new-login-screen-<branch-hash>-b42`.
Slash/dash and case collisions stay distinct. Rerunning a run keeps its tag and
never overwrites assets. BUILD.json records the exact commit, branch and channel.

## Installing

In Dashboard updates, choose **Browse alpha builds**, select the build in the
dropdown, then **Install selected alpha** and confirm. The catalog checks the
latest 100 GitHub releases and requires both archive and checksum assets.
Installation rechecks availability; removed builds are rejected.

For a fresh LXC using the remote Proxmox installer, set `HERDR_RELEASE` to the exact
published alpha tag. `latest` remains stable-only. Local source checkouts still use
their own built assets.

On existing containers, after deploying this version, explicitly refresh the root
worker as root before installing alphas:

```sh
cd /opt/herdr-web
bash install/web-install.sh
```

The worker verifies the checksum, archive/version identity, branch/tag identity
and GitHub tag's exact commit before replacing anything. Alpha packages require
BUILD.json. Stable legacy compatibility, backup, authenticated startup checks and
rollback remain in place. Local promoted builds keep their separate workflow.
Choose the available stable update to leave an alpha installation.

Alpha releases and tags are retained after merge. Automatic destructive cleanup
is not enabled. This preserves reproducible downloads and avoids deleting testing
artifacts without a separate retention policy. Alphas are intended for testing
containers; stable releases remain the default.
