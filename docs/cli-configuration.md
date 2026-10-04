# CLI configuration in the browser

Connect the dashboard, then open **CLI configuration**. Its Flutter route is
`/dashboard/#/configuration`. This page works without a running Herdr session.
Select **Configure** to launch the selected CLI as the LXC's `herdr` user in a
real Linux pseudoterminal. The Flutter terminal renders ANSI output and supports
interactive menus, keyboard input, resizing, copy/paste and Ctrl+C.

Open the authorization URL printed by the CLI in your local browser, complete
the provider's sign-in, and return to the terminal. Where the CLI asks for a
code or API key, use the masked paste field and **Send to terminal**, or type
directly into the terminal. The CLI controls whether input is echoed. Closing
the terminal or leaving the page stops the setup process; refresh configuration
after completing login. An exited command is not itself proof of successful login.

| CLI | Setup command | Status detection |
| --- | --- | --- |
| Codex | `codex login --device-auth` | Parses `codex login status`; unrecognized output stays unknown |
| Claude Code | `claude auth login` | Uses `loggedIn` from `claude auth status` JSON |
| OpenCode | `opencode auth login` | Detects nonempty stored API/OAuth provider credentials, without returning them |
| Antigravity | `agy` in the remote connection environment | Unknown: settings-file existence cannot prove secure-keyring authentication |

Codex device-code login may need enabling in your ChatGPT account or workspace
settings. See [Codex authentication](https://learn.chatgpt.com/docs/auth).
Claude's login and status commands are documented in its
[CLI reference](https://code.claude.com/docs/en/cli-reference).
OpenCode stores provider credentials through its
[auth commands](https://opencode.ai/docs/cli/#auth).
Antigravity documents a remote authorization URL and pasted code in its
[installation and authentication guide](https://www.antigravity.google/docs/cli/install/).

**Configured** means the CLI reports a local signed-in state. **Credentials
detected** means saved credentials exist. Neither verifies expiry, billing,
model access or a successful model request. Environment-provided credentials
and alternative provider configuration may not appear in file-based detection.
Antigravity may require additional keyring configuration on a headless host;
the page displays the CLI's own prompts and errors instead of fabricating status.

The gateway permits only these four setup commands and never accepts a command
string from the browser. Antigravity's interactive CLI remains interactive after
login; close it when setup is complete. These processes have the normal `herdr`
user's capabilities. The installer enables LAN access on the LXC's IPv4
interfaces; the dashboard access token is still required. Loopback-only access
with an SSH tunnel is available through `HERDR_WEB_BIND=127.0.0.1` when reinstalling
the web service.

**SSH access (optional)** on this page accepts one public SSH key for the
`herdr` account, validates it with `ssh-keygen`, preserves existing keys and
ignores duplicates. SSH is not needed to open the dashboard on your LAN. New
installations prepare the account for key login even when no key is supplied
during provisioning. Older containers installed without a key may need the
updated `/root/herdr-install.sh` rerun as root to unlock that account for SSH.

**Dashboard access** controls LAN versus SSH-only binding. Add a public key,
test the tunnel command shown in the dialog, and confirm that the tunneled URL
works before applying SSH-only mode. Five seconds after applying, the gateway
rebinds to loopback. LAN mode can be restored from the tunneled dashboard; both
modes still require the dashboard token. Mode changes do not change password
or root SSH authentication. The selection persists in
`~/.config/herdr-web/dashboard-access.json` and survives normal reinstall.
For console recovery, run the web installer as root with `HERDR_WEB_BIND=0.0.0.0`.

Every API operation requires the dashboard token and the same-origin policy.
Status responses contain no credential values. Terminal input is not written
to gateway logs; output is held in a bounded memory buffer and cleared on close.
The vendor CLI itself persists credentials in its usual user configuration or
keyring. Up to four sessions are allowed, with one per CLI; abandoned sessions
expire after ten minutes without polling/input. A lost browser response may
leave a session until expiry. Sessions do not survive gateway restart.

Rebuild the web assets, copy the complete gateway directory and reinstall the
web service using the existing deployment instructions. `pty_exec.py` and
`cli_setup.py` must be deployed alongside `server.py`; no new Python packages
are required. Flutter uses the pinned `xterm` 4.0.0 package for terminal rendering.

Verification includes status parsing, credential exclusion, endpoint auth and
origin checks, command allowlisting, input validation and Flutter prompt/input/
cleanup interaction tests. Linux CI additionally executes a real PTY test for
controlling-terminal input, output, resize and process termination. Provider
OAuth success requires an actual account and is not exercised by credential-free CI.
