# Repository questions and operation measurements

Task commit creation is documented separately in [Managed task revisions](managed-task-revisions.md).

Herdr retains Git and linked worktrees. Repository questions should not require
another clone or an execution workspace. This layer supplies bounded read-only
answers and measures existing work before changing its coordination strategy.
It does not change validation, merge, publication or environment permissions.

## HTTP questions

Authenticated dashboard clients can GET `/api/projects/query` with `repository`
(a repository-root path relative to managed projects) and `kind`:

| Kind | Additional fields | Answer |
| --- | --- | --- |
| `diff` | `revision`, `base`, optional `file` | Base-relative committed patch, with explicit truncation |
| `ancestry` | `revision`, `base` | Ancestry in both directions and exclusive commit counts |
| `history` | `revision`, optional `file`, `limit` | Up to 50 commits, parents and subjects; not an exhaustive history |
| `requirements` | `revision` | Existing committed discovery report, excluding host measurement and operator overrides |
| `status` | `task_id` | Recorded task state, assignment state/reason and stored issues |
| `evidence` | `task_id`, optional `revision` | Recorded checks, their exact target and policy digest, and whether that target is still the candidate |
| `incorporation` | `task_id`, `release_id` | Selected SHA and recorded incorporation, separately from the current task candidate and main |

Git revisions must be complete 40- or 64-character hexadecimal commit IDs.
Branch names and abbreviated revisions are refused. File filters are literal
Git pathspecs. Queries neither fetch nor mutate refs nor run project commands.
Git query output exceeding 1 MB is refused; returned patches are capped at
65,536 characters and identify truncation. History defaults to 20 commits and
check summaries are capped at 100 with an explicit truncation flag.

The response includes repository identity, `observed_at`, `cached`, `measurement`
and `result`. The measurement reports elapsed milliseconds and instrumented Git
calls for this query, including repository and revision checks. JSON GET replies
also carry `X-Herdr-Git-Calls`, covering synchronous instrumented Git commands in
that request. This makes task-refresh costs observable without counting unrelated
requests. It does not count every external Git process spawned by project tools.

The existing operator authentication is checked before routing. Task/release
records must match the selected repository. There is no new repository permission
model: HTTP follows the gateway's existing operator access, and local workers use
their existing filesystem permissions.

The shared-operator access decision is deliberate: every authenticated operator
can read committed content and recorded evidence across managed repositories.
Private repository access therefore requires trusted operator accounts; this API
does not provide repository-by-repository ACLs. Performance diagnostics remain
administrator-only.

### Evidence is not admission

`recorded_checks_passed` states what the stored exact-target build reported under
its recorded policy digest. `current_candidate_matches` is separate. A historical
pass can remain true while the current candidate differs. `current_policy` is
explicitly `not_evaluated`: these reads do not replace fresh approval, validation,
release freeze or publication preflight. Status also does not claim live daemon
availability or diagnose every scheduler prerequisite.

## Cache boundaries

Only immutable Git/discovery answers are cached. Keys include the canonical
repository and common Git directory, full commit IDs, query parameters and analyzer
version. Every request revalidates the project boundary, repository root and
required commits before using a cache entry. Results are copied, so callers cannot
modify cached data. The in-memory LRU is bounded to 128 entries and 8 MB; an immutable answer
larger than 1 MB is refused. The existing discovery report cache remains its own
bounded layer.

Task evidence, recorded task status and release progress are never cached here.
Host assessment remains in the existing separately refreshable discovery path.
The cache is an optimization, not retained validation evidence or authorization.

## Local worker helper

New task prompts include a shell-quoted command using the deployed helper and
the exact projects root, repository, task identity and task database. For example:

```sh
python3 /opt/herdr-web/gateway/repository_queries.py \
  --projects /home/herdr/projects --repository example \
  --tasks-db /home/herdr/.config/herdr-web/tasks.sqlite3 \
  --task-id 0123456789abcdef0123456789abcdef --kind evidence
```

For committed questions, choose `--kind diff|ancestry|history|requirements` and
pass `--revision` and, where required, `--base`. A release question additionally
uses `--release-id`. SQLite opens with `mode=ro`; the helper never initializes or
migrates the database and receives no dashboard credential. Existing filesystem
permissions still apply; this is not a sandbox or a new grant. Failure is reported
instead of searching for credentials or broadening access. Each CLI invocation is
a separate process, so only the gateway provides a cache shared across requests.

## Performance observations

Administrators can use **Builds & deployments → Performance diagnostics → Refresh
performance diagnostics**, or GET `/api/diagnostics/performance`. It is an on-demand
view, not another polling source. Non-admin HTTP requests are refused.

The snapshot identifies its PID and start time. Fixed operation names record
count, failures, total/max time and p50/p95 over the latest 256 samples per
operation. Cache hit/miss counters are separate. No command arguments, paths,
prompts, environment variables or credentials become metric labels or samples.
Failures count raised exceptions; validation check execution also marks a
nonzero exit or timeout as failed. These counters are not HTTP error-rate or
release-readiness verdicts.

Instrumented operations include:

- Git subprocesses through `project_git` and repository identity resolution;
- outer repository-lock wait and hold times (reentrant helpers are not counted
  as separate lock claims);
- creation of agent, validation and release workspaces;
- execution-environment preparation and provider materialization (dependency
  restoration is measured separately from recipes that require no restore);
- validation check execution;
- repository queries and HTTP GET handling, including long polls/static reads.

Timings overlap and must not be added together as independent costs.
`repository.lock_wait` starts after repository identity resolution and measures
lock acquisition/contended waiting only. Its p95 is not total claim latency;
the preceding identity subprocess is included in `git.command` and enclosing
request timings. Reentrant identity reads are also Git operations.

Gateway aggregates reset on restart and do not include a separate durable build-worker
process. Validation `run.json` retains workspace/environment/check timings,
including failed phases, and includes them in delivered build evidence. The
dashboard displays those phases on individual builds. Agent workspace and
successful environment preparation timings are retained on their jobs; release
workspace creation timing is retained on the release run. SDK download time and
every arbitrary agent shell command are not comprehensively instrumented.

Measure cold and warm questions, actual workspace creation, restoration, checks
and lock contention on a real gateway before changing locks or checkout strategy.
This implementation makes no production speedup claim. It adds no sparse checkout,
virtual filesystem, cross-process telemetry collector, result cache for builds,
automatic deployment or replacement source-control system.

## Regression coverage

`test_repository_queries` exercises real Git, cache reuse/copying, immutable reads
despite uncommitted edits, path/revision/limit refusals, mutable evidence, read-only
SQLite CLI access, HTTP authentication and diagnostics roles. Seeded release tests
reorder task advances, old/new build evidence, candidate capture and reconstruction
of the release service, then prove frozen manifests remain unchanged. Existing
real-Git merge/checkpoint/ref and late-callback tests remain in place. Browser tests
cover on-demand diagnostics and non-admin controls.
