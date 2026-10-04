# Saved terminal text

Herdr preserves live pane contents while its server runs. Its documented default
scrollback limit is 10,000,000 bytes per pane (**10 MB**, about **9.54 MiB**).
Optional `[experimental] pane_history = true` saves recent screen history across
server restarts, but is off by default. This is not a durable, complete transcript
for every agent run. Herdr's rotating diagnostic logs are also separate from
terminal output. See [session state](https://herdr.dev/docs/session-state/),
[config reference](https://herdr.dev/docs/config-reference/) and
[diagnostic logs](https://herdr.dev/docs/configuration/#logs).

The application adds **Saved logs** at `/dashboard/#/logs`. Choose a live agent
or enter a terminal pane ID such as `w1:p1`, choose the source and save a text
snapshot before closing the pane. The visible-screen source is passive. Recent
history requests up to 2,000 rendered rows through `herdr pane read`; supported
full-screen agents may need to be idle while Herdr reads their history. The
result is only the text Herdr can expose at that moment, not all past output.
See [Herdr output reads](https://herdr.dev/docs/cli-reference/#panes) and
[alternate-screen history](https://herdr.dev/docs/agent-automation/).

Saved snapshots remain on the LXC in
`~/.config/herdr-web/run-logs/` after a gateway restart or pane closure. Include
this directory in backups. Files are private to the `herdr` user. No automatic
terminal recording is enabled and login/setup sessions are not archived.

The Flutter page initially requests metadata only. **Preview last 8 KB** reads
at most 8,192 bytes on demand. **Open text page** launches a separate HTML/JS
viewer that loads no Flutter runtime and shows about 65,536 bytes per page,
with previous/next controls. UTF-8 boundaries are preserved. **Download .txt**
is available for files up to 10,000,000 bytes. The download is fetched by this
separate page, not loaded into Flutter's widget state.

File sizes come from the actual saved file byte count. The UI displays decimal
MB and exact bytes: **1 MB = 1,000,000 bytes**. For example, 1,048,576 bytes is
about **1.049 MB**, or exactly **1 MiB**. Small files retain six decimal places
so a nonempty file is not misleadingly displayed as zero. Future run sizes
cannot be predicted from agent count: output volume, duration and retained
history determine them. As an illustration, 2,000 rows averaging 100 UTF-8
bytes each would be about **0.2 MB**; actual Unicode and row lengths vary.

Exports are capped at 10 MB each, with at most 200 saved snapshots and 100 MB
of saved text. When storage is full, saving fails with a clear message; older
snapshots are not silently deleted. Delete snapshots explicitly from the page.
The displayed storage total counts `.txt` bytes, not metadata or filesystem
allocation overhead. The directory can therefore occupy slightly more than
100 MB physically. Existing oversized files can still be paged, but cannot be
downloaded through the application.

Log APIs require the dashboard token. New tabs receive a five-minute capability
limited to one saved file and operation; it is carried in a URL fragment and
removed from the address bar, never placed in a request URL. Download links are
single-use; viewer links permit paged reads until expiry. Reopen the link if it
expires. The viewer renders output as text, never HTML, and enforces the origin
policy. Terminal text can contain sensitive project data; access follows the
same shared dashboard credentials as other controls.

Deploy the complete gateway directory, including `run_logs.py` and
`log_view.html`, together with rebuilt Flutter assets. No additional Python
dependencies are needed. Unit and HTTP tests cover persistence, exact size,
bounded previews, UTF-8 pages, link expiry, deletion and authentication. Flutter
tests cover on-demand preview, MB formatting and narrow layouts. Provisioning CI
also saves and reads a snapshot from a real Herdr pane.
