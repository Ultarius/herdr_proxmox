# Venture simulation

Status, 2026-11-16: manual and opt-in daily ticks, guaranteed news-only or
every-day reviews, internal-evidence strategy days, per-day review records with
visible skips, the deterministic ledger and survival score, role-aware agent
meetings, qualification, outreach review, charts, promotion to discovery, a
confirmed four-agent team action, multi-line costs, live ECB FX rates, a
per-organization active-venture limit, a per-tick score breakdown, a Venture
Studio for portfolio reviews and new-venture candidates, candidate review and
approval, bounded studio policy and budgets, a self-starting studio (one-call
setup, readiness checklist, Herdr preflight, named failures, guided repair and
an explicit creation mode), a Flutter Ventures page and an installation-wide
module switch are implemented. The score formula is at
version 2. Scoring-weight tuning waits for observed ventures; see
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

Only one tick per venture is in flight. A tick still in a meeting holds the
next one back. A tick left needing attention is superseded when the next day's
tick starts; the same day can be run again after `dismiss_tick` (see below).

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

One bad day does not stop the days after it. A tick left needing attention
(an answer that does not validate, a meeting that stopped) is closed as
`superseded` when the next day's tick starts: its day stays unsettled, with no
costs and no result, it can no longer be recovered, and its review record says
why. Only a tick that is still meeting holds the next one back. On the same
day, `dismiss_tick` (with a reason) closes the stuck tick so the day can be run
again; **Recover artifact** still re-reads the saved answer.

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

### Review records and skipped days

Every due venture has exactly one review record per calendar day. The record is
separate from the tick ledger and has one of four kinds:

- `news` - a meeting with filtered external news;
- `quiet` - no news and `meeting_cadence=news_only`; only burn, revenue and the
  demand pipeline move;
- `strategy` - no news and `meeting_cadence=every_day`; the group meets using
  internal evidence only;
- `skipped` - no meeting, with a visible reason (paused, no group, active limit,
  previous tick needs inspection, or a missed gateway slot).

A skipped record can be replaced by a manual review on the same day. It never
counts as a simulated day. A strategy day does not count toward qualification:
its demand delta must be zero, and it may cite only `S` (standing), `T` (recent
ticks), `O` (recorded outreach outcomes) and `R` (open objections) evidence.
No strategy result may invent news.

## Venture Studio

The studio is a separate read-only group with its own managed notebook. Setup
creates four studio agents (Portfolio Manager, Venture Scout, Skeptic and
Research Analyst) and one read-only group; see
[Getting started](#getting-started). The studio has two run kinds:

- **Portfolio review** returns a summary and exactly one ranked recommendation
  (`continue`, `pause`, `kill` or `investigate`) for every active venture. The
  recommendation is inert; the operator may apply pause or archive.
- **Scout** returns at most the configured number of venture candidates. A
  candidate includes a name, thesis, product, pricing, a full normalized
  configuration, assumptions, kill criteria, a confidence opinion and evidence
  citations. Candidates are stored as proposals, never as ventures. A candidate
  that fails validation is refused on its own and listed on the run under
  `refused_candidates`; the valid ones are kept.

A scheduled portfolio review with no active ventures is recorded as `skipped`
and starts no meeting; a manual one is refused.

Studio policy is organization-scoped: portfolio cadence (`off`, `daily`,
`weekly`), scout cadence, candidate cap, candidate backlog cap, focus areas,
permanent exclusions, an allowed source allowlist, and `studio_paused`. Stored
values are read tolerantly: an out-of-range value (a hand edit, an older limit)
falls back to its default and is reported as `invalid_policy` on the page
instead of breaking the snapshot or the scheduler; saving the studio settings
repairs it. Operator input is validated strictly. Only studio keys leave the
gateway through this path. The evidence file carries just the focus areas,
exclusions, candidate cap and sources, never the rest of the organization
policy.

### Getting started

A new operator needs a connected agent CLI and nothing else: no organization,
no existing agent and no project folder.

`studio_start` (the **Set up Venture Studio** form) does all of it in one call.
It is refused while Herdr is stopped, before anything is created: creating a
group starts its facilitator, and that launch would fail and leave a stuck
session. The same check guards a venture's own team.

1. **Organization.** The named one, the only one, or a new one called after the
   studio. An organization is only the container for the studio's agents,
   limits and audit history; the operator never has to create it first.
2. **Notebook, four agents and the group**, as before, but the agents' CLI,
   provider and model no longer have to be copied from an existing agent. A
   role uses, in order: the individual agent named for it; the operator's
   explicit CLI, provider and model; the first individual agent of the
   organization; and otherwise a detected choice. Detection uses the account
   catalog and selection routine of the team presets: among the models the
   ready accounts actually offer, it picks one that meets the capability floor
   of an independent reviewer, free or cheaper first, and checks it like a
   hand-made choice (`settings_source` is `detected`). When the catalog is
   unavailable or offers no suitable model, the first ready CLI runs on its
   own default model (`automatic`). The setup form uses the agent form's model
   picker, so provider, model and reasoning are chosen from what the account
   offers, not typed. The Studio tab shows what the agents run on and how it
   was chosen. An explicit choice is checked (CLI ready, account signed in, model
   offered) before any agent is hired. With no ready CLI the setup stops with
   each CLI's reason and hires nobody; a later attempt continues from the
   notebook it already created. The same order applies to a venture's own
   team, which additionally falls back to the studio's settings.
3. **Starter policy.** Sources, focus areas, cadences and the creation mode.
   For a new studio a cadence that is not given is **weekly**: "manual only"
   is a choice the operator makes, not what silence means. Session limits of
   an existing organization that are too small for one studio meeting are
   raised and reported as `limits_raised`. Calling it again on an existing
   studio changes only what the call names: it never re-enables a cadence the
   operator turned off and never raises a limit again.
   The group is named **Venture Studio** (a group belongs to one
   organization, so it needs no identifier) and the notebook folder is
   `ventures/studio-<organization name>-<short id>`. A studio set up earlier,
   whose group is still called `Venture Studio <organization id>`, is renamed
   once by the next setup, team start, repair or manual run; a name the
   operator chose is left alone. The notebook folder of an existing studio is
   not moved.
4. **First scout**, when asked. If it cannot start (Herdr stopped, no sources)
   the studio still exists and the answer says why.

### Readiness

Every studio summary carries `readiness`: a checklist the Studio tab shows
with one action per failing item.

| Check | Blocks | Action |
| --- | --- | --- |
| Herdr is running | every run | Start Herdr |
| The agent CLI is ready | every run | Open CLI accounts |
| The studio team exists | every run | Set up, or Repair |
| No agent session is stuck | every run | Repair studio |
| The organization limits allow a studio meeting (five sessions) | every run | Raise limits for the studio |
| Scouting sources | the scout, unless web research is on (then shown as **Optional**) | Configure studio |
| Focus areas | the scout | Configure studio |

The same checks run as a **preflight** before a run is recorded, so the studio
never starts a meeting that cannot work:

- A manual run is refused with the reason; nothing is recorded.
- A scheduled run waits. The reason is shown as `waiting` on the schedule
  line, it is not an error, it adds no failure and no backoff, and the same
  due window runs on the first pass after the cause clears.
- A scout needs at least one source and one focus area. A manual scout may
  run without sources only with an explicit override (**Run scout without
  sources**); a scheduled one never does. When web research would run, the
  override cannot skip the focus area: the scout would not know where to look,
  so that run is refused and the button is only offered when a focus is set
  (`can_scout_override`).
- Session limits of 0 mean no limit and are shown that way.
- A venture's own meeting tick has the Herdr check too: it is skipped with a
  review record, and the news items it was offered are offered again.

Enabling a cadence is refused while the session limits are below what one
studio meeting needs. `studio_limits` raises `max_active_sessions` and
`daily_session_cap` to that minimum; it never lowers a limit and never touches
one that is off (0).

### Web research and suggested sources

A scout whose sources give no items (none configured, or none that is a
readable feed) no longer returns nothing. With `studio_web_research` on (the
default) it runs as a **web-research** scout: the prompt tells the agents to
search the web themselves for the focus areas, read-only, and to report every
page they rely on under `research` (`W1`...`W12`: URL, title, date, claim).
Candidates cite those ids; a page that was not reported cannot be cited. The
pages are stored on the run and on the candidate as `evidence_links` and are
always labelled agent-reported: the gateway does not fetch them. A focus area
is still required. A scout that does have source items may not cite the web.

A reported page that cannot be used (no `W` id, not https, no title or claim)
is left out rather than failing the run, so one bad page costs only the ideas
that cite it, and the refusal names the id. Long titles and claims are
shortened. A legacy numeric confidence is still validated for older clients,
but it cannot override the summary derived from the dimensions.

A settled scout whose ideas were refused can be read again with
`studio_recover` (**Read the answer again** on the Ideas tab). The saved answer
is parsed under the current rules, ideas that now pass become candidates,
ideas already accepted from that run keep their stored state, and no prompt is
sent. The Ideas tab also says when the latest scout is still meeting, has
stopped, or proposed nothing, instead of showing an empty queue.

### Candidate diligence and economics

A scout candidate now carries the case against itself as well as the case for
it. The gateway refuses a candidate that omits any of these fields:

- `prior_state`: what was required before and what is new, with citations,
  and its `driver`: `regulation`, `technology`, `infrastructure`, `incumbent`
  (a weakening leader), `behaviour` or `other`;
- `alternatives`: 3-6 alternatives, including software, a service and either
  doing nothing or in-house work, with a public price where available. A
  software or service alternative needs a cited page; doing nothing or
  in-house work does not;
- `buyer`: the role that signs and the budget it comes from;
- `reachable_accounts`: a cited count and the segment it covers;
- `business_model`: `saas`, `service`, `productised_service` or `hybrid`, with
  one-off, recurring, per-unit, take-rate and credit revenue lines. A
  recurring line states its `period` (`month` or `year`). A `per_unit` line is
  a price per `unit` the customer has (room, seat, site) per period, times
  `units` per customer. A `take_rate` line is `percent` of a cited monthly
  `volume` per customer (payments, for instance), minus `cost_percent` passed
  on to banks or networks. Per-unit and take-rate revenue counts as recurring;
  a credit reduces the first year of that revenue, so it needs one of them;
- `delivery_effort`: expert days and a day rate;
- `evidence_against`: at least one cited page or reason that weakens the idea;
- `dissent`: the Skeptic's structured `advance`, `hold` or `reject` verdict;
- `confidence_dimensions`: timing driver (15%), paid demand (30%), technical
  feasibility (10%), differentiation (15%), recurring potential (15%) and
  defensibility (15%), each rated weak, medium or strong and cited. An older
  answer's `regulatory_driver` is read as the timing driver, and a missing
  defensibility rating counts as weak (shown as not rated), so leaving it out
  never raises the summary;
- `first_offer` and `time_to_first_payment_days`: the smallest sellable first
  offer and how long the first payment takes.

Optional, cited fields describe where the idea comes from and how big it can
get. They are shown on the card and in the brief as upside, never counted
toward break-even or the warnings:

- `analogue`: a company that proved the model in another industry, and why it
  transfers;
- `incumbent`: the leader buyers use today and 1-5 cited complaints from its
  own customers (reviews, pricing pages, contract terms);
- `market_ceiling`: every account the product could serve, at least the
  reachable count; the gateway reports the yearly revenue if all of them
  bought at these prices;
- `expansion`: up to four later modules with a price and the share of
  customers expected to buy each; the gateway reports the average extra
  revenue per customer.

The prompt tells the scout how ideas are rated and where promising ones come
from (an analogue, an incumbent's complaints, or a timing shift beyond
regulation), asks it to weigh three times as many ideas as it may return and
keep the strongest, and warns it that ratings are checked against citations.
The Ideas tab lists open ideas by confidence, highest first.

Every scout run also gets a review checklist, `review.md`, written by the
gateway beside its evidence file, and the prompt tells the Skeptic and the
Research Analyst to work through it for every idea. It asks how the price
compares with the cheapest cited alternative, whether the business is mostly
consulting, where the market grows, whether customers with no system yet are a
better start, what revenue grows with the customer's activity, whether the
leader could copy the idea within a quarter, how big it realistically gets,
and which reframe was weighed; the Skeptic advances only when price, delivery
and the copy question hold. Held ideas are returned too, with their dissent,
so the stakeholder panel and the operator see them (autonomous mode never
creates one). The scout omits rejected ideas and returns up to the round cap.
Ideas already in the pack's
`existing` list are not audited again: the scout looks elsewhere or returns a
revision with `revises`. The checklist lives beside the evidence rather
than in the prompt, so it reaches existing studio teams and costs little of
the prompt budget; unlike the evidence it is rewritten when a newer gateway
changes it. Alternative prices may state a `period`.

The questions the gateway can check become `review_notes` on the candidate,
shown on the card and in the brief. Like kill-criteria notes they never refuse
or hold an idea: a price more than 10% above the cheapest cited software
alternative with a period, delivery costing more than half of a customer's
first-year revenue, and no per-unit or take-rate revenue. For service models,
the delivery note compares the cost per project with the project price.
Services with recurring revenue also get the price and usage-based revenue checks.
The gateway recomputes these notes when the operator edits the idea.

The gateway, not the agent, derives the summary confidence from the dimensions
(weak 0, medium 50, strong 100, weighted) and computes the unit economics, per
month in the market currency:

- runway: capital over the monthly burn;
- the first offer's margin: one-off price minus expert days at the day rate,
  and the day count at which it starts losing money;
- for a service model, break-even in projects a month (and about twelve times
  that a year, compared with the cited reachable accounts on the working
  assumption of one project per account a year);
- for SaaS and hybrid models, break-even in paying customers at the monthly
  recurring price (an annual fee counts as a twelfth), the share of the cited
  reachable accounts that is, and roughly how many new customers a month are
  needed to get there between the first payment and the end of the runway;
- first-year revenue and contribution per customer: one-off fees plus twelve
  months of recurring revenue, minus credits, minus the delivery effort.

Variable costs beyond the stated delivery effort are not modelled, so the
break-even is a floor. These figures are stored on the candidate, shown on the
Ideas card and in the approval dialog, and recomputed if the operator edits the
configuration. Warnings (a losing first offer, a service with no one-off
revenue to fund delivery, a break-even above the reachable accounts, a first
payment after the money runs out) cannot be overridden by the scout. Autonomous mode creates only an idea the Skeptic would `advance` and
whose economics carry no warning; the operator can still approve the others.

Kill criteria are refused only on what the gateway can check: 1-8 items, at
least one of them a paid signal (money, a signed agreement or a paid project),
and every date (ISO, `31 Mar 2027`, `31 March 2027` or `March 2027`) in the
future, within the first 120-day gate and inside the computed runway. Wording
problems that are a matter of judgement (effort without the customer's current
baseline, a proxy such as stated interest, guidance or a template without
saying why it would kill the business, unless a payment such as a signed or paid
pilot decides the item; a bare amount, such as a competitor's price, does not)
are stored as `kill_criteria_notes` and
shown on the card as criteria to tighten, so they do not cost an otherwise
sound idea. Operator edits are checked the same way.

Because those pages are unverified, **autonomous mode never creates a venture
from web research alone**; such a candidate waits for the operator.

Every scout may also return `suggested_sources`: up to eight feed URLs for the
focus. They are advisory, an unusable entry is dropped, and nothing is added
by itself. **Add source** (`studio_source_add`) fetches the URL first and adds
it only when it is a feed that parses and has items, so a page that is not a
feed (a search engine's home page, for example) cannot become a source that
way. The readiness checklist names each configured source whose last fetch
failed, with the error.

A web-researched candidate names no feeds, and a venture without feeds only
has quiet days: it spends money and never holds a judged review. Approval
therefore accepts `attach_sources`: when the candidate has no feeds, the
studio's current sources become the venture's feeds and the choice is recorded
on the candidate as `feeds_attached`. A candidate that names its own feeds is
left alone. The approval dialog offers this by default and warns when the
venture would still have no feeds. The Ideas card shows the market, outreach
settings and feeds, the research pages as links, and the scout's own note.

Whether the agents can search depends on the agent CLI and its permissions;
a CLI without a web tool is told to return no candidates and say so in
`research_note`.

### Printable brief

**Export brief** on an idea card opens a printable brief of that candidate in
a new tab and starts the print dialog, so the idea can be presented on paper
or saved as a PDF. With pop-ups blocked the dashboard downloads the brief as an
HTML file instead. The gateway renders it (`candidate_brief`,
`venture_brief.py`) on A4: the key figures, the idea, why now, who buys, the
business model and gateway economics with their warnings, the alternatives,
the case against and the Skeptic, the confidence dimensions, assumptions, kill
criteria with the notes to tighten them, and every cited id with its page or
news item from the scout run. Every page says that the narrative is agent
opinion and that the numbers come from the gateway.

Agent text is escaped, and the brief carries its own Content-Security-Policy:
no network requests and only the hashed print script. The brief is shown from
a blob of the dashboard's origin, so this policy, not the escaping alone, is
what keeps a hostile scout answer from running in it. Exporting changes
nothing and takes no lock; it is available for decided candidates too.

### Stakeholder panel

The studio's four agents share one model, and a model judging its own ideas
tends to agree with them. The stakeholder panel is a second, separate
read-only group (`venture_panel.py`): two to four stakeholders chosen from an
investor, the target customer, an industry operator and the market leader,
each on its own CLI and model. **Automatic** picks a ready CLI the studio and
the other stakeholders do not use, then a model nobody uses on it; the Studio
tab shows every stakeholder's model and warns when all of them run on the
studio's own model. A changed model hires a new stakeholder agent. The group's
chair runs on the first stakeholder's model.

After every scout round, the gateway pitches up to six ideas, starting with
new ideas and filling the remaining slots with open ideas that have no usable
review and no unresolved pitch. A round that proposes nothing can still
trigger a pitch. When the candidate backlog is full, the Studio can pitch
those open ideas without starting a scout meeting. If a pack is too large,
ideas at the end of the batch can go to the next pitch. The maximum daily
load on the Studio tab counts the panel meeting, and panel setup checks it
against the session cap. A pitch includes each idea's case, gateway economics,
warnings, review notes, and citations from the idea's originating scout round.
The prompt sets the
method. In round 1 each stakeholder reviews every idea alone, without seeing
the others; in an optional round 2 it hears their objections without names and
may change its verdict. Each review is `back`, `hold` or `pass` with 1-5
objections, up to 3 questions and the evidence that would change its mind.

The gateway accepts a review only from a stakeholder that spoke in the meeting
transcript, so the chair cannot write one for a stakeholder; reviews in the
name of a silent stakeholder are dropped and listed as not counted, and
missing reviews are listed too. Accepted reviews are stored on the idea
(`stakeholder_reviews`, with each stakeholder's model) with a round summary
(`panel`), shown on the Ideas card and in the brief. When a pitch returns no
usable reviews for an idea, both show “Pitched, no reviews.” A result with
no usable reviews for any idea still needs attention.

The next scout round's evidence carries `stakeholder_feedback`: the latest
round on up to six recent ideas. The scout is told to answer it: an idea it
improves comes back with `revises` (the earlier idea's name) and a
`revision_note`. The revision is not refused as a duplicate of the idea it
revises; it carries the next round number and the previous objections
(`previous_feedback`), the earlier idea becomes `superseded`, and the panel
sees in the next pitch whether each objection was answered.

One meeting runs at a time, so the next scout waits for the pitch: each round
is scout, then panel. In autonomous mode with a panel, a scout round creates
nothing; the pitch decides, and an idea is created only when most stakeholders
assigned to that pitch back it, on top of the other caps. Missing reviews
count toward the panel size. A pitch that could not start is prepared
when it is recovered: it goes to the panel as it is set up then, with the
ideas that are still open, as edited. `studio_panel` with `enabled: false`
switches pitching off and keeps the agents; without `stakeholders` it switches
the existing panel back on.

#### Talking to the stakeholders during the scout

While the panel is on, the stakeholders also attend each scout meeting as
advisors. The `discuss` request names them in `advisors`. They are prepared,
reserved and listed in the roster like members for that one meeting, and the
group itself does not change. A meeting takes up to four advisors who are not
already in the group. The scout prompt points the chair to `stakeholders.md`,
written beside the evidence file like the review checklist, which sets the
method:

1. **Explore first.** Before choosing ideas, the chair asks every stakeholder
   on its own: where buyers already spend money, which segments feel the
   pain most, which tools or workarounds they use today, and one segment the
   studio may be overlooking.
2. **Screen a rough shortlist.** For each idea, every stakeholder gives its
   biggest objection and one piece of evidence that would change its view.
   The studio spends its remaining research on that evidence.
3. **Pivot.** A stronger segment is investigated in the same session. A
   rating rises only with a cited page, never because a stakeholder liked
   the idea. A promising idea with a named evidence gap can come back held.

The answer returns `stakeholder_exploration` (one entry per stakeholder) and
each candidate's `stakeholder_screen` (objection, `would_change_view`, and the
studio's `response`). Both are advisory: an unusable entry is dropped, never
a reason to refuse an idea. As with pitch reviews, an entry from a stakeholder
that did not speak in the transcript is dropped. The run keeps the
exploration and the roles that spoke (`consulted`). The Studio tab shows the
latest completed exploration and how many invited advisors spoke. The Ideas
card shows the screen. A pitch labels an idea pre-screened when a member of
that pitch panel also screened it. The chair shares the final case and
sources for the first review, then can share anonymized earlier objections
in the optional second round.

Each scout meeting needs one more session per stakeholder. Panel setup and
the scout preflight check that against the organization limits. The daily
projection counts both the advisor sessions and the follow-up pitch. A stuck
stakeholder session holds up a scout that talks
to it: repair it, or keep the stakeholders to the pitch. Panel setup lets
you choose either participation mode. `studio_panel` with
`scout_consult: false` does that (**Keep stakeholders to the pitch** on the
Studio tab), and `true` turns it back on. The scout prompt is close to its
8,000-character budget, which is why the method lives in the file.

### Where the evidence file goes

The facilitator runs in its own worktree, so a file written into the source
notebook is outside its checkout and every run stopped on a permission prompt.
The gateway now prepares the facilitator session first and writes the file
inside that worktree, next to its guidance bundle
(`.ci-cache/herdr-guidance/studio-<run id>/evidence.json`, already ignored by
Git). Product Discovery does the same. Venture ticks use
`venture-<tick id>` in their own facilitator's worktree. If a prompt was never
accepted and its file is gone (the facilitator has a new worktree), the file
and the prompt are written again.

### Failures name their cause

A run that stops carries `failure`: a category, the phase it stopped in
(`dispatch`, `preparation`, `meeting` or `validation`), the discussion's own
error text, the agent it names and one recovery action.

| Category | Meaning | Action |
| --- | --- | --- |
| `herdr` | Herdr is stopped | Start Herdr |
| `permission` | An agent waits for a decision | Open the studio group |
| `provider` | The CLI account is not ready | Open CLI accounts |
| `model` | The model is not offered | Open CLI accounts |
| `team` | An agent session did not start | Repair studio |
| `limits` | An organization limit blocks the meeting | Raise limits |
| `evidence` | The evidence file could not be prepared | Repair studio |
| `result` | The answer did not validate | Dismiss run |
| `meeting` | The meeting stopped for another reason | Open the studio group |

The category is derived from the error text, so an unusual message falls back
to `meeting`. A meeting that is still running but waits (a member blocked on a
decision, a session budget) shows that as `waiting` on the run instead of
looking like progress.

### Repair

`studio_repair` is one guided action. Without `confirmed` it returns the plan,
which the dashboard shows first. With it, the steps run in order and each
reports what happened:

1. Start Herdr when it is stopped.
2. Release agent session bindings that failed or were left uncertain.
3. Start the studio facilitator.
4. For each run needing attention, newest first: read a saved result again;
   send a prompt that never arrived; retry a meeting that stopped before its
   prompt was sent; recover the saved output of one that stopped after; or
   close a run that cannot be used (a result that does not validate, an older
   run superseded by the retried one).

A step that fails stops the steps that depend on it. `studio_team` starts the
facilitator and every agent that has no session yet, for an operator who wants
the team live before a run.

### Creation mode

`studio_mode` is explicit and shown on the Studio tab.

- **`proposal`** (default): the scout proposes, the operator approves.
- **`autonomous`**: after a scout, the studio creates ventures itself within
  hard caps: at most one per run; only a candidate at or above
  `studio_auto_min_confidence` (default 70); only one that cites at least one
  outside source item, not just internal history; never above the active
  venture limit; never an excluded or duplicate idea. Creating means hiring
  that venture's four agents and starting its news-only daily reviews, under
  the same budget check as a manual approval.

Every decision is recorded: `auto` on the candidate (created or not, the
reason, the run and the minimum used), `auto` on the run, and the approval is
signed `venture_studio_auto`. A candidate that was not created stays a
proposal. The scout's bar is still fixed by the gateway, and nothing in either
mode sends mail, spends or publishes.

### Run lifecycle and recovery

Both run kinds share one group, so only a run still **meeting** blocks a new
one. When both cadences are due, the first starts and the other waits for the
next poll: waiting is not a failure, so it records no error and no backoff.
A success resets the kind's failure count and records `last_at`. A `pending`
run left for more than ten minutes (an interrupted dispatch) is moved to
needs-attention, so it cannot block the studio.

A member that is still busy when the meeting is assembled, typically still
answering its startup prompt, makes the meeting **wait**, not fail. Members
receive that prompt without the gateway waiting for the reply, so one can turn
busy between preparation and assembly. Nothing has been sent at that point,
so Herdr puts the meeting back to `waiting_for_members` and the poll loop
retries it; the studio shows it as "Waiting to start". It turns into a
failure only after 24 hours, like any other wait.

A run needing attention is listed on the page but never blocks the studio. Its
recover button takes the same step repair would take for that run, and its
label says which (`recover` in the studio summary carries the action, the
label and a sentence):

- **The prompt never reached the group** (a meeting limit, a restart):
  **Re-send prompt** sends the frozen prompt again under the same request id
  `studio-<run id>`. A first attempt that did reach the group therefore
  returns the same discussion instead of a second meeting.
- **The meeting stopped before its prompt was sent** (an agent that was busy
  or not ready): **Retry preparation** prepares the same meeting again. It
  never finalizes a discussion that has no output.
- **The meeting stopped after its prompt was sent**: **Recover result**
  recovers the group's saved output, then reads it.
- **The agents' result does not validate**: **Read result again** re-parses the
  saved artifact; if it still fails, **Dismiss run** closes it with a required
  reason. The discussion and its artifact are left for inspection.

A busy agent is named as such ("A studio agent was still busy"), not as an
agent that did not start.

Scheduler errors, failure counts and the last and next run for each kind are
shown on both dashboards.

### Evidence budget

The studio pack has its own 32 KB file budget (the 8,000-character prompt limit
is unchanged), because it must cover up to twenty active ventures. It shrinks
in this order: closed history, lessons, news down to five items, then each
venture's detail in two steps (shorter thesis and product, one tick; then a
compact entry of about 700 bytes without product or ticks), then the remaining
news and open names. Every active venture always stays in, so the portfolio
review can cover it. The `existing` list is a bounded reminder only: open
ventures and candidates first, then the newest twenty closed ones, each with a
120-character thesis. Duplicate detection reads every recorded venture and
candidate from storage, not this list.

### What a scout may and may not set

A scout proposes the market, product, outreach and costs. It does not set its
own bar or schedule. Whatever it proposes, a stored candidate gets:

- the module defaults for survival threshold (60), qualifying days (3), Red
  Team strictness (5), meeting cadence and review hour;
- the reference price reset to 0, so prices are anchored to its monthly costs
  (a scout that named a high reference could charge four times it for free);
- daily reviews **off**;
- starting capital capped at twelve months of its proposed monthly costs (the
  runway factor is already at its maximum by then);
- a refusal if it proposes no monthly costs at all.

The prompt states the configuration schema and limits outright: the keys a
venture has, `market.geography` as one line, monthly costs under
`simulation.burn`, 1–8 assumptions and 1–8 kill criteria of at most 300
characters, and 1–12 cited evidence ids (the research list holds at most
twelve pages). Keys the schema does not have, such as planning notes, are
dropped instead of stored, and a list of places is joined into the geography
line. A candidate that still breaks a rule is refused whole, and its reason
names every problem at once. The Ideas tab lists the newest scout run's
refused ideas with those reasons, so an empty queue is not mistaken for a
scout that found nothing.

Every reset value is kept under `scout_adjusted` (`proposed` and `used`) and
shown on the Ideas card and in the approval dialog. The operator may change
the bar with Edit. Editing never turns on daily reviews: only approval's
**Start daily reviews** does, and only together with **Create team** or for a
venture that already has a group.

### Exclusions

Exclusions are enforced, not just written into the prompt. A candidate whose
name, thesis or product contains an excluded term as whole words is stored as
`excluded` with `excluded_by`: "crypto" excludes "Crypto Treasury Desk" but
not "Cryptography Audit Desk". Approval checks again against the current list.
The list holds up to forty terms and is permanent: rejecting with **exclude**
when it is full is refused, rather than dropping the oldest term.

### Budget

The projection uses the real groups: each meeting counts its members plus the
facilitator, and a venture without a group counts nothing. `every_day`
ventures and daily studio runs are guaranteed meetings. `news_only` ventures
count towards the maximum only, since they meet at most once a day. Weekly
studio runs count as one seventh of a meeting a day. The page shows the
guaranteed and maximum meetings and sessions per day and the studio runs per
week. With a `daily_session_cap` set, a change that would raise the projected
maximum above the cap is refused: a studio cadence change, an approval with
**Start daily reviews**, or turning on daily ticks for a venture with a group. A change that lowers it is always allowed, so an organization
already over its cap can still turn things off.

### Candidate approval

An operator edits a candidate, then explicitly approves it. Approval is a
resumable sequence: create the venture, optionally create its four-agent team,
then optionally enable daily reviews. A deterministic `candidate-<id>` request
identity makes retries idempotent. The candidate records its `venture_id`; the
venture records `source.candidate_id` and `source.studio_run_id` plus the
candidate's assumptions and kill criteria. Rejection records a reason and may
add the direction to permanent exclusions. A candidate linked to a venture
cannot be rejected; archive the venture instead.

### Lessons

The studio evidence pack includes a bounded lessons list built from completed,
failed and archived ventures, repeated high-severity objections, dropped
product changes, outreach outcomes by market and persona, repeated market
friction, disproved assumptions recorded by the operator, and permanent
exclusions. Lessons affect recommendations only; they never change the
deterministic score formula.

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
  feed health, the fixed `options`, the Herdr state, each agent CLI's
  readiness and the `module` state. While the module is
  switched off it returns only `enabled: false` and `module`.
- `POST /api/ventures` — one action endpoint with `mode`: `create`,
  `configure`, `run`, `pause`, `resume`, `recover`, `dismiss_tick`, `outreach`, `team`,
  `promote`, `restore_promotion`, `archive`. Each runs under a `venture:<id>` operation lock.
  Studio modes run under the organization's `studio:` lock: `studio_setup`,
  `studio_run` (`kind`, optional `override`), `studio_recover`,
  `studio_dismiss`, `studio_repair` (optional `confirmed`) and `studio_team`.
  `studio_panel` (`stakeholders`: 2-4 `{role, settings}`, or `enabled`) runs
  under the `studio:` lock and sets up the stakeholder panel.
  `candidate_update` and `candidate_decide` run under the candidate's lock;
  a `configuration` in `candidate_update` or `configure` is partial: each
  layer changes only the fields it names, and a list (feeds, burn lines) is
  replaced whole, so a market-only edit keeps the burn and capital;
  `candidate_brief` (`candidate_id`) is read-only and returns the printable
  brief as `html` with a `filename`.
  `studio_policy`, `studio_limits` and `studio_source_add` (`url`) run under
  the `policy:` lock.
  `studio_start` (the one-call setup) and `herdr_start` need no organization.
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
  organization) bounds running, paused and qualified ventures. Drafts may
  still be created. The cap applies when a venture becomes active, by Resume,
  Pause or its first run, under one organization lock, so two drafts cannot
  take the last place at once. The organization `paused` switch stops new
  ticks.
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
| Studio | `web/gateway/venture_studio.py` | Studio readiness, repair, setup and autonomous creation |
| Panel | `web/gateway/venture_panel.py` | Stakeholder panel setup, pitch runs and feedback for the next round |
| Brief | `web/gateway/venture_brief.py` | The printable brief of one idea |
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
news-only and every-day review cadence, internal-evidence strategy reviews,
one review record per day with visible skips, the deterministic ledger and survival
score, role-aware prompts with etiquette, persona and stage guidance, strict
result checks, qualification and failure, outreach approve, reject and outcome
actions, recovery without resending, inline score and capital charts, promotion
to Product Discovery with explicit replacement, recorded previous values and
one-click restore, a confirmed and retry-safe four-agent team action with
per-role templates, multi-line costs, live ECB FX with fallback, the
per-organization active-venture limit editor, per-tick score breakdowns, a
Venture Studio with setup, policy, portfolio reviews, scouting, candidate
review, approval-to-create, lessons and a budget projection, a one-call studio
setup that needs no organization or template agent, a readiness checklist and
preflight, evidence written into the facilitator's worktree, named failure
categories, a guided repair, proposal and autonomous creation modes, and
Ventures pages on both dashboards.

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
- The first-run path is covered by gateway tests against a stand-in store.
  The facilitator preparation, session release and discussion retry it calls
  are the organization store's own actions and have not been exercised
  against a live Herdr from these tests.
- Failure categories are matched from error text. A message the rules do not
  know is filed as `meeting`.
- A permission prompt is shown on the run and linked to the group; it is
  answered in the group discussion, not from the Studio tab.
- A scheduled run that waits on a stuck agent session does not repair it by
  itself; the operator runs Repair.
- The studio still lives inside an organization. A first-class studio object
  without one is not implemented.
- Automatic CLI selection uses the platform's account catalog: Codex and
  Claude must be signed in and OpenCode's configured providers must return a
  model. A detected model comes from the account's own list, but listing a
  model does not prove the account may use it: only a real meeting does.
  Antigravity's sign-in cannot be verified from outside, so it is chosen last.
- Repair starts the facilitator only. The other agents start when the next
  meeting needs them, so repair does not spend session starts on them.
- The Flutter page does not create, configure or approve ventures.
- Dismissing a studio run closes it in the studio only. The group discussion
  itself stays in the organization history for inspection.
- The budget check estimates a new venture team as five sessions (four agents
  and a facilitator) until the team exists.
- Assumption outcomes and dropped product changes are recorded only when the
  agents or the operator report them; the gateway does not infer them.

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
