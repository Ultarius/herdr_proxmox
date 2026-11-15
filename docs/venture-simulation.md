# Venture simulation

Status, 2026-11-15: manual and opt-in daily ticks, the deterministic ledger and
survival score, role-aware agent meetings, qualification, outreach review,
charts, promotion to discovery, a confirmed four-agent team action,
multi-line costs, live ECB FX rates, a per-organization active-venture limit,
a per-tick score breakdown, a Flutter Ventures page and an installation-wide
module switch are implemented. The score formula is at version 2.
Scoring-weight tuning waits for observed ventures; see
[Implementation status](#implementation-status).

Venture simulation runs fictional B2B companies ("shadow companies") against
real daily news. Each day a group of agents reads the filtered headlines, decides
how the product, pricing and outlook change, and drafts cold outreach. The
gateway keeps the books. When a venture stays above its survival threshold, its
outreach drafts are released for an operator to send to real prospects.

It is [Product discovery](product-discovery.md) pointed at the outside world:
the same evidence pack, read-only group meeting, strict JSON result and operator
review, with news as the evidence instead of a repository snapshot.

## Goals

- Let a weak idea fail in simulation before engineering time is spent on it.
- Tie every product change to a cited news item from that day.
- Encode regional friction (currency, sales cycle, regulation, etiquette) as
  configuration, so an unfamiliar market can be tested.
- Hand the operator reviewed, ready-to-send outreach when a venture qualifies.
- Turn a qualified venture into an ordinary project that the existing discovery,
  task and validation loop can build.

## Non-goals

- Sending email. The gateway produces drafts and an export, nothing more.
- Predicting real outcomes. The survival score is an agent group's judgment of a
  fiction, constrained by rules. Only replies to real outreach are evidence.
- Multi-tenant hosting. This stays inside one installation's organizations and
  administrator permissions.
- Storing or republishing articles. Only headline, link, date, tags and a short
  excerpt are kept.

## What is reused, and what does not fit

| Need | Existing mechanism | Gap |
|---|---|---|
| Daily routine | `Discovery.advance()` on the `contributions.poll` loop: due time, one meeting per window, frozen prompt, fifteen-minute retry backoff | Discovery policy is one record per organization. Ventures need many records per organization, each with its own due time. |
| Evidence | `evidence_pack()`: bounded, redacted items with IDs, written atomically into the facilitator's checkout cache | Items come from feeds, not from Git. The 14 KB pack and 8000-character prompt limits apply unchanged. |
| Agent work | `store.action('discuss', …)` with a read-only group and a stable `request_id` | A venture team template; `mode: team` now creates one from an existing individual agent. |
| Result contract | `Discovery.parse()`: fenced JSON, bounded fields, citations must be delivered evidence IDs | A different schema (see [Result contract](#result-contract)). |
| Recovery | `needs_attention`, artifact recovery and reprocess without resending | None. |
| Budgets | `max_active_meetings`, `daily_session_cap`, organization `paused` | A per-organization cap on active ventures. |
| Memory | [Project knowledge](project-knowledge.md) selection in meeting context | None; ticks may report knowledge like any discussion. |
| Dashboard | `/api/discovery` snapshot plus one action endpoint; Jaspr page with a shared Juice bloc | Charts are inline SVG in the Ventures page. |
| Outbound HTTP | `urllib` openers with a redirect handler in `github_api.py` and `environment_artifacts.py` | No general content fetcher. Feed URLs are operator-supplied, so address checks are needed. |

Discovery requires a managed Git repository root, a worktree agent assigned to
it, and the facilitator's checkout to hold the evidence file
(`Discovery.validate`, `Discovery._dispatch`). Rather than loosen that, **each
venture owns a small managed repository** (the venture notebook). See
[Venture notebook](#venture-notebook).

## Data model

New tables in `tasks.sqlite3`, each row a JSON record like `discovery`:

- `ventures` — identity, organization, group, notebook repository, the four
  configuration layers, state (`draft`, `running`, `paused`, `qualified`,
  `failed`, `archived`), `next_at`, and the current ledger summary.
- `venture_feed_items` — one row per fetched item, keyed by a hash of the
  canonical link: source, title, link, published time, tags, excerpt (300
  characters at most), fetch time. Shared across ventures in an organization.
- `venture_ticks` — one row per venture per simulated day: state, the frozen
  evidence and prompt, meeting job ID, parsed result, ledger entry, error.
  Unique on (venture, simulated date), which makes a tick idempotent.
- `venture_outreach` — drafts: venture, tick, stage, persona, language, subject,
  body, cited evidence IDs, state (`draft`, `ready`, `exported`, `rejected`),
  decision actor and time.

### Configuration layers

All four are validated, bounded fields on the venture record.

1. **Sources** — feed URLs (ten at most), section and tag allowlist, trigger
   keywords, translation on or off with a list of terms to keep in the original
   language.
2. **Market** — geography, base currency, FX pair, sales-cycle friction (1–5),
   legacy-workflow bias (1–5), and a free-text regulatory baseline (1500
   characters at most).
3. **Simulation** — starting capital, monthly burn lines (each in the base
   currency or the other side of the FX pair), delivery architecture (`saas`,
   `api`, `chat_bot`), ideal customer profile (size and sector), Red Team
   strictness (1–10), survival threshold, the number of consecutive days
   required to qualify, `daily_ticks` (off by default) and `tick_hour_utc`.
4. **Outreach** — stage (`survey`, `waitlist`, `paid_pilot`), tone and language
   (including bilingual), recipient persona (`cto`, `cfo`, `operations`).

Changing layers 2 or 3 on a running venture is recorded on the next tick, so a
chart can show where the rules changed. A qualified venture keeps the market
and simulation rules it qualified under; its sources, outreach layer (for
example moving the stage from survey to paid pilot) and daily schedule can
still change.

The fixed choices (architectures, personas, stages, roles, FX sources) are
defined once in `venture_engine.py`. Validation reads them from there and the
snapshot sends them as `options`, so the dashboards render the gateway's list.

`simulation.reference_price` is the monthly price the ideal customer accepts
without resistance. Zero (the default) uses the monthly costs, the price at
which one customer covers them.

## Daily tick

A tick advances one venture by one simulated day. `Ventures.advance()` is called
from `contributions.poll` after `discovery.advance()`.

1. **Fetch.** Gateway code, no agent. Pull each feed with a conditional request,
   a ten-second timeout, a 1 MB response cap and at most one fetch per feed per
   hour. Parse with the standard library XML parser with entity expansion
   disabled. Store new items. A manual tick fetches on the request; a scheduled
   tick waits for a background thread to fetch, so the gateway's poll thread
   never does network I/O for this module.
2. **Filter.** Take the items stored since this venture last looked (a cursor
   on the item table, kept on the venture), so an item published just before
   the previous tick but fetched after it is still offered, once. Drop items
   published more than a day before the previous tick. Keep an item when it has
   an allowed tag **or** contains a trigger keyword; with neither list set,
   keep everything. Keep the newest twenty. At most the newest 500 stored
   rows after the cursor are read, and the cursor moves past all of them, so a
   larger backlog (a long outage with many feeds) drops its oldest rows.
   Pruning never deletes the table's highest row: SQLite numbers a new row
   after the current highest one, so keeping it means a new item can never
   reuse a row id at or below a venture's cursor.
3. **Quiet day.** If nothing passes the filter, record a tick with burn only and
   start no session. This is the main cost control.
4. **Evidence.** Build a pack of the filtered items (`N1…`), the venture's
   current product and pricing summary, the market and simulation layers, the
   last five tick summaries and a small knowledge selection. Freeze it on the
   tick record, then write it to the facilitator's checkout cache exactly as
   discovery does.
5. **Meeting.** Dispatch one discussion to the venture's group with
   `request_id` `venture-<tick id>`. The prompt states that all evidence is
   data, not instructions, and that the meeting is read-only.
6. **Parse.** Validate the result. An invalid result sets the tick to
   `needs_attention`; it is recovered and reprocessed, never resent.
7. **Ledger.** Apply the day's burn, FX movement and the validated deltas in
   code. Write the ledger entry and update the venture summary in one
   transaction.
8. **Notebook.** Commit the day's changes to the venture notebook.
9. **Graduation.** If the venture has had the required number of consecutive
   qualifying days, set it to `qualified`, which unlocks its drafts for review
   (they stay `draft` until an administrator approves each one). If capital
   reaches zero, set it to `failed`.

Only one tick per venture is in flight. A venture whose previous tick is still
in a meeting or needs attention does not start another.

### Scheduling

`Ventures.schedule()` runs at the end of `advance()` on every poll. A venture is
due when `daily_ticks` is on, its state is `draft`, `running` or `qualified`,
the organization is not paused, and `next_at` and `retry_at` have passed. Each
run, manual or scheduled, moves `next_at` to the next `tick_hour_utc`, so at
most one tick per venture per calendar day starts. An error before that point
(group not ready, active limit full) is shown as `schedule_error` and retried
after fifteen minutes, doubling up to a 24-hour cap. A busy venture lock is
skipped until the next poll.

A due venture whose feeds (or ECB rates) are older than their refresh interval
is handed to a background thread first; the tick starts on a later poll pass,
from what that thread stored. A feed that fails still counts as refreshed, so a
dead source delays nothing.

Missed days are not backfilled. A simulated day is a real day of news, so
inventing ticks for a day the gateway was down would only replay stale
headlines.

### Agent roles

The group is read-only and its members keep their own personas; the venture
adds a role brief to each meeting prompt. Four roles are mapped onto the
members:

| Role | Duty in the meeting |
|---|---|
| **Analyst** | Reads the evidence file, keeps only items that force a buying decision for the ideal customer, shares cited excerpts. |
| **Founder** | Proposes at most *N* product changes and a monthly price, preferring to change or remove a feature over adding one. |
| **Red Team** | Attacks the plan with the market layer (sales-cycle friction, legacy workflows, regulation, language support, scope creep), rating each objection 1–5. At strictness 7+ it must challenge every change. |
| **Outreach writer** | Drafts up to three messages for the configured persona, stage and language, each anchored to one cited event. |

Assignment, in order: explicit `role_overrides` (`{profile_id: role}`), then a
name hint (an agent called "Red Team" or "Skeptic" takes the Red Team role),
then the least-loaded member, so the facilitator is the Analyst in a small
group and a two-member group still covers all four roles. The resolved names
are stored on the venture and shown on the page.

The page's **Create team** action (`mode: team`) creates the four agents and a
read-only group on the notebook after a confirmation. Each role can copy
runtime, provider and model from a different existing individual worktree agent;
the gateway does not guess a model or account. The group's permission mode and
accessible paths come from the Analyst template. Creating the group starts the group
facilitator's session; the four members start when a meeting needs them. The
four agents are pinned to their roles through `role_overrides`.

The action is safe to retry. The copied settings and group name are frozen on
the venture before the first request, each agent is recorded as soon as it is
hired, and every store request carries a deterministic request ID. A retry
after a partial failure skips agents already hired and replays the group
request instead of creating duplicates.

The prompt fixes the speaking order (Analyst, Founder, Red Team, one Founder
rebuttal, Outreach writer, facilitator writes the JSON). It defines the sign of
each delta, so "competition +2" always means more pressure, and it requires
objections the Founder did not answer to show in the deltas. It also gives the
venture's standing (capital, runway, score, sub-scores, demand still in the
pipeline, qualification progress) and recorded replies to real outreach, so
agents judge each day against the venture's actual position. The brief also
carries the configured outreach tone and, when translation is on, tells the
Analyst to translate the selected items and which terms to keep in the
original language.

Outreach guidance is generated from layer 4:

- **Etiquette per language.** For example `ja`: keigo, a `{{company}}
  {{name}}様` address, a short courtesy opening and よろしくお願いいたします.
  Bilingual values such as `ja+en` produce the native version first and a
  short summary in the second language.
- **Persona.** A CTO hears about integration, security and data residency; a
  CFO about cost, payback and tax; an operations manager about steps removed
  and paper or fax fallbacks.
- **Stage call to action.** Survey: a fifteen-minute discovery conversation.
  Waitlist: a `{{waitlist_link}}` placeholder. Paid pilot: a concierge pilot at
  the current price.

### Result contract

One fenced JSON object:

```json
{
  "events": [{"evidence": ["N3"], "summary": "…", "direction": "tailwind"}],
  "product_changes": [{"kind": "add", "title": "…", "reason": "…", "evidence": ["N3"]}],
  "pricing": {"monthly": 48000, "reason": "…"},
  "deltas": {"demand": 2, "competition": -1, "regulatory": 0, "delivery_risk": -1},
  "red_team": [{"objection": "…", "severity": 3}],
  "outreach": [{"persona": "cfo", "language": "ja", "subject": "…", "body": "…", "evidence": ["N3"]}]
}
```

The gateway rejects:

- unknown evidence IDs, and events, product changes or drafts without one;
- more than five events or three drafts, a delta outside −3…3, unknown enum
  values and over-length text;
- more product changes than the strictness allows: 3 at strictness 1–3, 2 at
  4–7 and 1 at 8–10. This is the scope-creep guard;
- fewer Red Team objections than product changes at strictness 7 or higher;
- drafts in a persona or language other than the configured one, and drafts
  containing an email address (recipients stay placeholders).

`pricing` may be omitted to keep the current price.

### Ledger and survival score

Agents supply judgment; `venture_engine.py` does the arithmetic. It has no I/O,
so a venture can be replayed from its tick records and the formula is unit
tested. Formula version 2:

- **Burn.** Burn lines are monthly. Daily burn is their sum in the base
  currency divided by 30. `USD/JPY` at 150 means one USD buys 150 JPY, and
  conversion works in both directions.
- **Sub-scores.** Demand, competition, regulatory and delivery risk start at
  50 (range 0–100). Each delta moves them by 5 points.
- **Sales-cycle friction.** A demand judgment lands `sales_cycle − 1` ticks
  later, so at friction 5 (a ringi-sho consensus market) today's interest
  shows in demand four days from now. Pressure deltas apply immediately.
- **Price.** The gateway moves the price at most 50% a day and never above
  four times the reference price. That ceiling also bounds the first price,
  which has no previous value. A price above the reference costs demand: ten
  points for every doubling (`price_drag` on the ledger).
- **Revenue.** Without a price (none set yet, or 0) there are no paying
  customers, so an unpriced venture cannot qualify. Otherwise, demand minus the
  price drag below 60 means no paying customers; from 60 there is one, plus one
  per further 10 points, up to five. Revenue is price × customers ÷ 30 per day.
- **Runway.** Capital divided by net daily burn, or self-funding when revenue
  covers burn.
- **Survival score** (0–100). A weighted average of runway health (180 days =
  100) at 0.25, demand at 0.30, and the inverse of competition (0.15),
  regulatory pressure (0.15) and delivery risk (0.10 rising to 0.20 with
  legacy-workflow bias). It then subtracts a Red Team penalty of the summed
  objection severity × strictness ÷ 10, capped at 30.
- **Qualification.** A qualifying day is a judged day (one with a meeting) at
  or above the threshold with at least one paying customer. A venture
  qualifies after `consecutive_days` of them in a row and stays qualified
  across a pause. It fails when capital reaches zero.

Quiet days run the same step with zero deltas, so burn, revenue and the demand
pipeline still move. They neither count towards qualification nor reset it:
the untouched baseline scores above the default threshold, and surviving
silence proves nothing.

## Venture notebook

Each venture has a managed Git repository under the projects root, created with
the venture:

- `venture.md` — one-paragraph thesis, customer and architecture.
- `product.md` — current feature or endpoint list, each line with the date and
  evidence ID that added it.
- `ledger.csv` — one row per simulated day.
- `outreach/` — one file per released draft.

The gateway writes these files and commits once per tick with its own identity;
agents in the read-only group do not edit them. The repository satisfies
discovery's requirements (a Git root, a worktree agent, a facilitator checkout),
gives a readable history of how the product changed, and becomes the starting
point of a real project on graduation.

## Outreach and graduation

Drafts are always reviewed. Before qualification they are visible but locked.
Once the venture qualifies an administrator can:

- **Approve** a draft. It is marked `ready` and written to
  `outreach/<date>-<persona>-<id>.md` in the notebook and committed; that
  file is the export;
- **Reject** a draft or an approved message;
- **Mark sent / replied / meeting / declined** on a message they sent
  themselves.

The gateway never sends mail and stores no recipient addresses. Unsolicited
commercial email is regulated (for example Japan's specified electronic mail
law and EU rules), so choosing recipients and sending remain the operator's
responsibility, and the review screen says so.

**Promote to discovery** on a qualified venture (`mode: promote`) sets the
notebook repository as a discovery repository and seeds the product brief from
`venture.md` and `product.md`. Discovery must already have focus areas and a
read-only group, and an individual worktree agent must be assigned to the
notebook. An organization has one discovery configuration, so if discovery
already points at another repository the request must carry
`replace_discovery: true` (the page asks first). The previous repository and
brief are kept on the venture under `promotion.previous`. `mode: restore_promotion` restores them after a confirmation. From there the
supported loop is the existing one: direction → discovery → reviewed drafts →
queue → validation.

Recorded outcomes are counted on the venture, shown beside the survival score,
and included in every later evidence pack as `real_signals`. They are the only
real validation signal, so the agents see them; they do not change the score
formula.

## API and dashboard

- `GET /api/ventures` — administrator-only snapshot: ventures with their ledger
  summaries, the latest thirty ticks per selected venture, eligible groups,
  feed health, the fixed `options` and the `module` state. While the module is
  switched off it returns only `enabled: false` and `module`.
- `POST /api/ventures` — one action endpoint with `mode`: `create`,
  `configure`, `run`, `pause`, `resume`, `recover`, `outreach`, `team`,
  `promote`, `restore_promotion`, `archive`. Each runs under a `venture:<id>` operation lock.
  `limits` (organization-level, `max_active_ventures` 1–20) runs under the
  organization's `policy:` lock instead. `module` (`enabled: true|false`,
  installation-wide, no organization) is the only mode accepted while the
  module is off.

Dashboard: a **Ventures** sidebar destination backed by a `VenturesBloc` in
`dashboard_state`, following `planning_blocs.dart`.

- **Portfolio** — one row per venture: state, survival score with a seven-day
  trend, runway in days, last tick, next tick.
- **Venture detail** — score and capital charts; a timeline of ticks showing
  headline → judged impact → product or pricing change → Red Team objection; an
  outreach tab; a configuration tab with the four layers.
- **Feeds** — last fetch, item counts and errors per source.
- **Limits** — active ventures and the editable limit per organization, plus
  the live FX source's last error.

The Flutter reference app has a **Ventures** route (`/ventures`) on the same
`VenturesBloc`. It covers the daily loop (portfolio, run tick, pause and
resume, draft review); creating and configuring ventures stays on the Jaspr
dashboard.

### Live FX

`market.fx_source` is `manual` (the default) or `ecb`. With `ecb`, each tick
is priced at the European Central Bank's daily euro reference cross rate for
`fx_pair` (one keyless public source covering about thirty currencies). The URL
is a constant in `venture_fx.py`, not operator input, and the request goes
through the same SSRF-checked fetcher as news feeds, with a 64 KB cap. One
shared cache row is refreshed at most every twelve hours, and failures back
off the same way.

The rate used is frozen on the tick (`ledger.fx`: pair, rate, ECB date, stale
flag), so history shows margin swings and a venture stays replayable. If the
source is unreachable the last cached rates are used and marked stale. If
there are no rates, or the pair has a currency the ECB does not publish, the
operator's `fx_rate` is used and the tick says so (`fallback`).

### Tuning the score

Every tick stores `ledger.score_breakdown`: the points each factor (runway,
demand, competition, regulation, delivery, Red Team penalty) contributed. The
page shows it per tick. Compare it with outreach outcomes before changing
weights. Weights live in `venture_engine.py` under `FORMULA_VERSION`. A change
bumps the version, and older ticks keep the version they were scored with.

Ticks finishing publish to the existing notification feed so open pages update
without waiting for a poll.

## Safety and limits

- **Untrusted input.** Headlines are text written by third parties and reach
  agents. The group is read-only, the prompt marks evidence as data, the result
  is schema-validated, and no action in this module can send, publish or spend.
  As with discovery, read-only is an advisory policy, not an OS sandbox.
- **Feed fetching.** Only `https` URLs; resolved addresses in loopback, private
  and link-local ranges are refused, including after redirects, because the
  gateway usually sits on a private network. The connection is made only to
  addresses that were checked, tried in resolver order (so an IPv4-only host
  still connects when the resolver lists IPv6 first). The certificate is still
  verified against the host name, and a second DNS answer cannot redirect the
  request.
- **No proxies.** Environment proxies (`HTTPS_PROXY` and similar) are
  deliberately not used for feeds or FX, because a proxy would make its own
  DNS lookup outside these checks. An installation whose only route to the
  internet is a proxy cannot fetch feeds or live FX. Each feed then shows a
  request error, and live FX falls back to the manual rate.
- **Budgets.** Ticks count against `max_active_meetings` and
  `daily_session_cap`. `max_active_ventures` (default 3, editable 1–20 per
  organization) bounds running, paused and qualified ventures. The
  organization `paused` switch stops new ticks.
- **FX fetching.** One constant https URL through the same address checks as
  feeds, at most once per twelve hours.
- **Cost.** Worst case is one four-member meeting per running venture per day.
  Quiet days cost nothing.
- **Content.** Excerpts are capped and feed items older than ninety days are
  pruned, except those cited by a tick.
- **Access.** All reads and actions are administrator-only, like discovery.

## Delivery phases

1. **Manual tick.** Venture records and configuration, feed fetch and filter,
   evidence pack, notebook creation, **Run tick now**, result parsing, tick
   history page. No schedule, no ledger beyond burn.
2. **Simulation.** Daily schedule, quiet days, ledger and survival score,
   charts, portfolio view, budgets.
3. **Outreach.** Draft review, qualification, export, outcome recording,
   promote to discovery.

## Module boundary

| Piece | File | Owns |
|---|---|---|
| Orchestration | `web/gateway/ventures.py` | Tables, configuration, ticks, scheduling, parsing, outreach review |
| Engine | `web/gateway/venture_engine.py` | Ledger, score, qualification, role assignment and brief; pure functions |
| Feeds | `web/gateway/venture_feeds.py` | SSRF-safe fetch, XML parsing, UTC timestamps, filtering |
| FX | `web/gateway/venture_fx.py` | ECB reference-rate parsing and cross rates |
| Notebook | `web/gateway/venture_notebook.py` | Notebook repository, tick commits, approved outreach files |
| State | `web/dashboard_state/lib/ventures_bloc.dart` | Snapshot and actions |
| Page | `web/jaspr_dashboard/lib/pages/ventures_page.dart` | Portfolio, detail, form, outreach review, limits |
| Flutter page | `web/dashboard/lib/ventures_page.dart` | Portfolio, run tick, pause, draft review |

### How it plugs in

The gateway names the module once: `self.register_module(Ventures(self))` in
`contributions.py`. Everything else goes through the registry:

- **Switch.** `module_state` / `set_module` keep one installation-wide on/off
  record per module (on by default). Off stops `advance()`, schedules, feed
  and FX requests, and every action except `mode: module`; ventures and
  notebooks are kept. The Ventures page has the switch.
- **Poll loop.** `advance_modules()` calls each enabled module's `advance()`
  in its own `try`. Any exception is recorded as the module's `fault` (shown
  on the page) and never skips the core steps after it or ends the poll
  thread.
- **Routes.** `server.py` serves a registered module's `route` from the
  registry: `read(query)` for GET and `action(body, actor, role)` for POST,
  administrators only. A new module needs no server change.

What the module uses from the platform:

- the organization store: `snapshot`, `action('discuss' | 'hire' | 'group')`
  and `update_job`;
- the contributions service: `connect`, `operation` locks, `policy`,
  `patch_policy` (the one writer for module-owned policy keys),
  `knowledge.context` and the projects root;
- Product Discovery's public `policy` and `action`, for promotion only;
- shared helpers: `redact` (imported from `discovery`, as the rest of the
  gateway does), notifications, project and Git helpers.

## Implementation status

Delivered: bounded four-layer configuration, SSRF-safe feeds with UTC
normalisation, notebooks with per-tick commits, manual and opt-in daily ticks,
the formula-1 ledger and survival score, role-aware prompts with etiquette,
persona and stage guidance, strict result checks, qualification and failure,
outreach approve, reject and outcome actions, recovery without resending,
inline score and capital charts, promotion to Product Discovery with explicit
replacement, recorded previous values and one-click restore, a confirmed and
retry-safe four-agent team action with per-role templates, multi-line costs,
live ECB FX with fallback, the per-organization active-venture limit editor,
per-tick score breakdowns, and Ventures pages on both dashboards.

Remaining limits:

- Scoring weights are a first guess. Tune them with `score_breakdown` and
  recorded outreach outcomes once real ventures have run.
- Translation is done by the Analyst inside the meeting, on instruction from
  the brief; there is no separate pre-meeting step.
- The regulatory and tax baseline is text for the agents. It does not change
  the ledger arithmetic.
- The price model is deliberately simple (one reference price, a fixed drag
  per doubling). Tune it with the weights.
- The dashboards read the choice lists from the snapshot, but each choice's
  prompt text (etiquette, persona pitch, stage call to action) is still code
  in `venture_engine.py`.
- Live FX covers the ECB's currencies only, at daily granularity; other pairs
  use the operator's rate.
- The Flutter page does not create or configure ventures.

## Open questions

- Should several ventures share one group? Sharing serializes their ticks behind
  one meeting at a time; separate groups cost more resident sessions.
- Is a daily cadence right for every source, or should a venture tick only when
  enough new items have passed its filter?
- Translation: done by the Analyst inside the meeting (no new dependency, uses
  prompt budget) or by a separate pre-meeting step?
- Where FX rates come from, and whether a fixed rate entered by the operator is
  enough for the first phase.
- Whether simulated days should always equal calendar days, or whether replaying
  a past period from stored feed items is worth supporting.
