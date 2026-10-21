# Build capacity

Gateway validation uses one cooperative heavy-work lane per `herdr` account,
regardless of the toolchain or recipe. The durable gateway build queue remains
responsible for exact-commit requests and retry identity. Interactive capacity
wrappers currently support **Cargo only**, installed when the session's frozen
policy binds `rust-toolchain`. Other interactive commands do not automatically
join this lane; sessions without a bound policy receive no tool-specific shim.

All sessions and validation commands receive `HERDR_BUILD_JOBS` and
`HERDR_TEST_THREADS` as neutral budget signals. These do not impose OS limits or
automatically throttle an arbitrary tool. Adapter metadata maps the signals to
tool-specific variables where supported; Rust bindings receive
`CARGO_BUILD_JOBS` and `RUST_TEST_THREADS`. Other adapters keep their existing SDK
activation variables and prepared command bindings. Additional interactive
wrappers and parallelism mappings require separate tool-specific validation.

## Interactive Cargo commands

For a Rust-bound session, launch copies a credential-free Cargo wrapper into its
ignored scratch directory and prefixes its command directory to the prepared shell PATH.
The wrapper resolves the real Cargo behind itself, preserving an approved SDK
or component dispatcher. Version/help commands (including `cargo build --help`)
do not wait for a build slot. Flags after `--` belong to the child command and
do not bypass admission.
The recognized heavy subcommands are build, test, check, clippy, bench, run, doc,
rustc, rustdoc, install and fix. Custom Cargo aliases are not inferred.

Heavy commands receive a FIFO ticket and print **Herdr build queued** with the
reason, then **Herdr build running** with the compiler allowance. A queued command
must not be duplicated. Exit status and tool output are preserved. Explicit
`-j` / `--jobs` values are clamped to the allowance while narrower choices are
preserved. `RUST_TEST_THREADS=1` and explicit libtest thread flags limit standard
test-harness parallelism; custom harnesses can have their own behavior.

Both dashboards show the active lane, waiting count, current compiler allowance
and memory waiting reason in **System health**. Gateway validation exposes a
temporary **Build capacity** phase and queued message in its log. Waiting for
capacity does not consume the check execution timeout. A capacity-wait expiry
reports that no checks ran; it cannot produce passing evidence.

## Budget

Capacity reads CPU affinity and finite cgroup-v2 CPU quotas, plus physical
available memory and finite ancestor cgroup memory limits. It subtracts inactive
file cache from cgroup usage when available, preserving reclaimable cache rather
than treating all cache as unavailable RAM. Service leaf groups with `max` still
inherit the container's finite limits.

The initial policy reserves 20% of memory, bounded to 512 MiB–2 GiB, and aims to
leave a CPU available when the allocation permits. Compiler concurrency is
conservatively limited to one or two jobs, with roughly 2 GiB of headroom per job.
New heavy work waits when available memory is below the reserve plus 512 MiB,
or sustained memory PSI is high (`some avg10 >= 20%` or `full avg10 >= 5%`).
Occupied swap alone does not block work. Missing measurements fall back to one
compiler job rather than inferring available host resources. These values are
operational heuristics, not compiler peak-memory predictions.

The lane also checks the filesystem used by the interactive command's current
directory, or the gateway's validation worktree. New work waits below **2 GiB
available space** or **1,024 free inodes**. It rechecks while queued and can start
after space is restored. An inherited nested command refuses immediately under
disk pressure rather than waiting while holding its parent's build slot.
Unknown storage measurements are reported as unavailable. These are admission
checks, not a disk quota: a running compiler can still outgrow the remaining
space, and external target/cache filesystems require separate monitoring.

Both dashboards show project-filesystem disk space in **System health**, with a
warning on the **Tasks** page and task details when this headroom is exhausted.
The capacity and storage panels use the same project-filesystem observation.
Health polling does not create lock files, prune tickets or update lane metadata.
The warning comes from a fresh filesystem observation rather than the task
database, which may itself be unable to save progress when disk space runs out.

If a task stops after **No space left on device**, inspect and stop any remaining
build, then expand storage or remove only confirmed disposable build outputs.
Preserve task worktrees, source edits, Git metadata, revision requests and
completion receipts. Reopen the assigned agent and resume the original task's
checks after freeing space. An idle agent without a valid completion receipt
has not finished its task and is not released to its next assignment. Herdr does
not delete caches, rerun an ambiguous prompt, or manufacture a passing receipt.

Ordinary, proven pre-send **agent working** refusals now appear as a waiting
state; they are polled again automatically. A missing completion receipt after
the session settles becomes an actionable error instead. Blocked permissions,
unknown delivery and malformed or mismatching receipts continue to require
inspection. Existing valid receipts still trigger exact-SHA capture once.

## Lifetime and boundaries

The shared default is `~/.local/state/herdr/build-capacity`. Gateway, build worker
and sessions must use the same service account/root. Operators may set
`HERDR_BUILD_CAPACITY_ROOT` consistently on the gateway and worker; the launch
records that root in its wrapper. Tickets contain only PID/start identity, time,
kind and state—no commands, repository paths, prompts or credentials. At most 128
waiters are admitted, with a 30-minute capacity wait. Dead waiter tickets are
pruned without replaying work.

A kernel file lock owns the lane across processes. Gateway children and Cargo
inherit the lock descriptor on POSIX, so losing the parent does not release the
lane while its direct child is still executing. A nested cooperating wrapper
reuses that inherited lease. Existing validation process-group termination still
handles timeouts. Interrupted work is not automatically rerun by this lane.

This is coordination, not OS confinement or a hard RAM/CPU quota. Agents share
one Unix user; absolute Cargo paths, replaced PATHs, custom aliases, direct rustc
calls, arbitrary child concurrency and other unwrapped interactive tools can
bypass it. Guidance directs agents through the wrapper. Every gateway check
runner participates regardless of its tool, but SDK downloads/probes and
dependency materialization before checks are separate preparation operations.
Serializing all Linux validation recipes is deliberate: even a lightweight
recipe can launch heavy child tools. This favors bounded account-wide load over
parallel lightweight validations until measurements justify finer scheduling.

The production gateway is Linux. Native Windows validators skip the parent lane
claim because Windows cannot pass the inherited descriptor to check children;
holding it would deadlock a nested Cargo wrapper. They still export compiler
budgets and refuse low storage. Independently invoked Windows Cargo wrappers
coordinate their own commands, but arbitrary native Windows validation is not
covered by account-wide lane exclusion.

No root permissions, container settings, compiler profiles, test selection,
dependency graphs or evidence-admission rules are changed.

Apply the gateway update and start new sessions to get the wrapper and exports.
Already-running agents retain their previous PATH, budget variables and prompts;
new launches adopt their frozen binding's wrapper selection. Increasing swap is
not a substitute for controlling concurrent work, and this policy cannot guarantee a
large individual compiler/linker process will fit in a particular container.

## Verification

`python3 -m unittest discover -s tests -p test_build_capacity.py` exercises
container budgets/ancestor limits, pressure versus occupied swap, argument caps,
real cross-process FIFO exclusion, owner death, gateway/agent coordination,
queue status and timeout accounting. Native POSIX coverage additionally checks
nested wrapper descriptor inheritance and Cargo exit-code preservation.
The fixtures do not certify a real Bevy project's peak memory.
