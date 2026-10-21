# Dashboard updates and storage recovery

Dashboard updates need disk space for the downloaded archive, extracted package,
the current installation/configuration backup and the replacement installation.
Increasing container RAM, CPU or swap does not increase this space.

The root update worker checks temporary storage before downloading and extracting.
Downloads use Content-Length when supplied, falling back to the bounded download
limit when it is absent; streamed size limits remain enforced even if the header
is wrong. Before stopping the gateway, it budgets the backup and replacement,
retaining 256 MiB and 1,024 inodes of headroom. An upgrade backs up CONFIG but does
not replace the live configuration. Rollback also budgets the configuration
replacement and current task records/credentials that must survive restoration.
Allocations on a shared device are summed and availability is sampled at every
destination, rather than trusting the first path's report. These are conservative
free-space checks, not a guarantee against quotas that filesystem APIs do not
report, such as some Btrfs qgroup limits. Both ENOSPC and EDQUOT errors receive
storage recovery guidance. Symbolic links (including dangling links) and special
files in copied trees are refused with a path-specific error, so estimates cannot
silently omit linked directory contents. If a file disappears during measurement,
the worker asks to retry instead of assuming its size is zero.

Backups are copied into `backup-<ns>.partial`. A `manifest.json` records relative
file paths, sizes, SHA-256 hashes, empty directories and file count. Files and
the manifest are synced before the directory is atomically renamed; on POSIX,
the backup directories and parent directory are synced as well. Failed copies
are cleaned up where possible. Rollback ignores `.partial` directories even
when cleanup fails or the worker crashes.

Before any service stop or removal, restore re-verifies the manifest. Legacy
backups without a manifest need a parsable BUILD.json identity (the historical
last-copy witness), required gateway/UI/version files and valid configuration
databases. This is completion evidence, not proof against earlier corruption.
If a legacy backup lacks installer/toolchain metadata present in the live
deployment, it is refused rather than leaving a mixture of old and current
components. Rollback selects the newest verified candidate and reports skipped
unverified candidates; if none pass, it stops without changing the live service.
There is no automatic override for unverifiable backups.

SQLite checks use scratch copies of each database and its WAL, with a read-only
connection and `PRAGMA quick_check`; saved backups are never opened for SQLite
sidecar writes. See [SQLite's WAL read-only requirements](https://www.sqlite.org/wal.html#read_only_databases).
Verification evidence is stored separately in root-owned `STATE/backups.json`,
including method, content digest, path, mtime and verification time. Legacy
content changes after certification are refused. Certification never replaces
the fresh content check and does not write markers into old backups.

Existing backups are retained;
the worker does not delete projects, worktrees, build outputs or backups to make
room. Other processes can still exhaust space after a check. If installation and
recovery both fail, the worker attempts to save the backup path and recovery
error; `rolled_back` is true only when restoration succeeded. Failure-status writes,
request cleanup and audit appends are best-effort. If disk pressure prevents them,
the original failure and recovery details are sent to stderr (the service journal)
without masking the primary exception. The dashboard status may remain stale;
check `journalctl -u herdr-update.service` in that case. Failure to write the initial
running status aborts before any installation changes and is diagnosed there too.

## Recover a container reporting ENOSPC

`Errno 28: No space left on device` can indicate exhausted bytes, inodes or
filesystem metadata. `Disk quota exceeded` (EDQUOT) specifically identifies a
quota failure; the message tells the operator to inspect the container allowance.
ENOSPC with free bytes remaining is not proof of a quota: backing-pool exhaustion
and metadata constraints are also possible.
From the Proxmox host, inspect the affected container (replace 106 as needed):

```sh
pct config 106
pct exec 106 -- df -h / /tmp /opt/herdr-web /var/lib/herdr-updater
pct exec 106 -- df -i / /tmp /opt/herdr-web /var/lib/herdr-updater
pvesm status
```

If its root disk is full and the backing Proxmox storage has sufficient capacity,
grow that disk. This example **adds** 32 GiB; it does not set the total to 32 GiB:

```sh
pct resize 106 rootfs +32G
pct exec 106 -- df -h / /tmp /opt/herdr-web
```

See the [Proxmox pct reference](https://pve.proxmox.com/pve-docs/pct.1.html#cli_pct_resize).
For an LVM-backed ext4 root disk, such as CT 106's `local-lvm` volume, the filesystem
check above should reflect the expanded disk. With a quota-limited Btrfs subvolume,
`df` can describe the host filesystem instead of the container allowance. Confirm
the configured `rootfs` size with `pct config 106`; on the Proxmox host, inspect the
actual backing subvolume using `btrfs qgroup show -p -r -e -F --raw <subvolume>`.
This includes ancestor groups and both referenced/exclusive limits; substitute the
real host subvolume path. See the [Btrfs qgroup reference](https://btrfs.readthedocs.io/en/latest/btrfs-qgroup.html).
Other storage backends also need their own pool/quota checks when guest `df` and
the allocation failure disagree. No qgroup parser or large allocation probe is
run automatically by the updater; its preflight remains best-effort.
If `/tmp` or another destination is a separate filesystem, expanding rootfs alone
may not increase its capacity. Do not delete worktrees or configuration to repair
an update. Inspect build outputs before choosing any cleanup.

After sufficient space is available, retry **Install update**. With this fix,
a failed stable update for that version permits retrying the same latest version;
a successful installation still prevents same-version installs and downgrades.
Legacy failures without a recorded version remain retryable. Alpha retries use
**Browse alpha builds** and **Install selected alpha**, independently of the stable
version check.
Older gateways may disable the button when Installed and Latest match. In that
case, after freeing space, use **Roll back last deployment**, then check for
updates and install again. Do not edit VERSION to bypass the check.

If the service is unhealthy, inspect `journalctl -u herdr-update.service` and
`/var/lib/herdr-updater/status.json`; restore a retained backup if needed after
freeing space. Seeing the dashboard alone does not prove the failed copy restored
every file successfully.

## Refreshing the privileged worker

These checks run in `/usr/local/sbin/herdr-dashboard-update`, separately from the
gateway. A gateway restart alone does not refresh it. After successfully deploying
a complete matching package that contains this fix, run as root inside the
container:

```sh
install -o root -g root -m 0755 /opt/herdr-web/install/dashboard-update.py /usr/local/sbin/herdr-dashboard-update
```

This replaces only the worker executable; existing service/path units remain in
place. If those units need installation or revision, `bash install/web-install.sh`
from `/opt/herdr-web` performs a full setup: it also configures the build service,
installs OS packages with apt, rewrites service/access policy and restarts the
gateway. Use the full setup deliberately, with sufficient disk space.

The new checks prevent unsafe attempts; an already full disk still needs capacity
before the older installed updater can install the fix.
