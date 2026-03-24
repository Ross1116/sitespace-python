# SiteSpace Programme Intelligence Platform
## Final Architecture Canon — Revised and Verified

**Version:** Consolidated final, revised and code-verified (updated 24 March 2026)
**Date:** 24 March 2026
**Audience:** Founders, engineers, future LLM/code agents  
**Purpose:** Single source of truth for architecture, implementation, schema, pipeline design, parser strategy, identity resolution, classification strategy, demand modeling, alerting, observability, failure handling, operational guidance, and delivery sequence.

---

# 1. Purpose of This Document
A
This is the single canonical architecture document for SiteSpace. It replaces all prior architecture documents.

It is written so that any engineer or LLM agent with zero prior context can:

- understand the full system end-to-end
- understand every architectural decision and why it exists
- build from this document without ambiguity
- reason about tradeoffs correctly
- handle operational edge cases safely

This revision explicitly addresses:

- the limits of exact normalized-name identity
- operational item merges without early over-engineering
- notification dedupe debuggability
- cold-start alert spam risk
- canonical asset taxonomy expansion
- mandatory Sentry-based observability
- richer AI audit logging and accuracy traceability
- cache versioning and invalidation rules
- stronger determinism guarantees
- confidence-driven behavior
- safe degraded-mode operation
- bounded AI control over total hours
- deterministic reduced-context fallback
- concrete anomaly thresholds
- strict separation between classification and work-profile generation
- fixed Monday-based workweek semantics
- exact unit-based calendar mapping and apportionment
- verified current-state corrections from the codebase and ARC Bowden PDF fixture
- per-day distribution bucket cap enforcement and `asset_types.max_hours_per_day`
- Bayesian evidence accumulation replacing naive observation counting
- profile maturity tiers: TENTATIVE → CONFIRMED → TRUSTED_BASELINE
- two-tier cache architecture: project-local and global cross-project knowledge base
- project lifecycle context and cross-project learning strategy

---

# 2. Executive Summary

The system already has the beginnings of a construction programme intelligence platform:

- CSV/XLSX/XLSM ingestion exists
- activities are parsed and stored with parent-child hierarchy
- AI-assisted classification exists with in-batch dedup
- lookahead exists in basic form
- bookings, assets, and subcontractor entities exist

However, several core product flows are structurally incomplete or fragile.

## Most important truths

- **The core architectural shift is from formula-based estimation to distribution-based work profiles.** Work is defined by its shape over time, not by average rates.
- **AI is not the system. AI is a decision helper called once per bounded problem; the system holds truth via cached, validated, reusable classifications and work profiles.**
- **Classification is a hard prerequisite for work-profile generation.** Asset type is stable memory; hours and distribution are separate reusable intelligence.
- **Work-profile AI does not infer asset type in the normal path.** Asset type is provided as input.
- **Identity cannot rely only on one exact normalized string.** Real task phrasing varies. Exact normalization is a conservative seed, not a complete semantic identity strategy.
- **Persistent item identity therefore requires aliasing plus manual merge operations.**
- **Notifications must be lookahead-driven, not activity-driven.**
- **Lookahead alerts need a cold-start ramp-up policy.** Without one, early projects with little booking history will spam subcontractors.
- **Notification dedupe must be explicit and debuggable.** Composite uniqueness beats opaque hashes for a small team.
- **Asset typing must be governed by a DB-backed taxonomy, not a frozen hardcoded list.** `other` must not become a permanent dumping ground.
- **`none` is required as a first-class asset type.** Many programme tasks consume no managed project asset and must not pollute `other`.
- **The parser remains the highest-risk subsystem.**
- **PDF is a first-class required input format for programme ingestion.** This is especially important because the ARC Bowden regression fixture is a PDF P6 export.
- **Partial success is preferable to total failure.**
- **Concurrency needs an explicit project-scoped guard.**
- **Observability is not optional.** Sentry and rich AI audit logs are mandatory for a 1–2 engineer team.
- **Cache semantics must be versioned and overrideable.** Manual truth must be able to replace stale AI truth.
- **Determinism requires versioned inference policy.** Prompt/model changes must not silently alter cache semantics.
- **Confidence is actionable, not decorative.** Low-confidence outputs must change system behavior.
- **Safe degraded mode must exist.** If AI or inference quality degrades, the system must fall back safely and suppress external risk.
- **AI must not have unbounded control over total hours.** Quantity proposals must be finalized by system rules and bounded by explicit hours policies.
- **Context fallback must be deterministic.** When context reuse is reduced, it must happen in a fixed, explainable order.
- **Anomaly detection must be concrete.** Vague anomaly logic is equivalent to no anomaly protection at all.
- **Calendar semantics must match real site calendars.** A 6-day week means Monday–Saturday, not “skip to next Monday early.”
- **Calendar-mapped demand must preserve exact totals.** Per-day values are apportioned in discrete scheduling units, not independently rounded.
- **Individual distribution buckets must be capped per day.** AI-generated distributions can produce single-day values exceeding physically possible hours. Per-day caps are asset-type-specific and stored in the taxonomy, not hardcoded.
- **The cache is a prior, not permanent truth.** The first AI answer is the worst answer. Subsequent observations must update the cached estimate. Bayesian precision accumulation is the correct mechanism.
- **Manual truth permanently outranks all machine estimates.** The posterior mean is always overridden by a manual correction.
- **Cross-project learning reduces AI cost trajectory.** High-confidence profiles confirmed across multiple projects seed new project caches, subject to asset-type presence guards.

---

# 3. Core System Identity

This system is:

> **A deterministic, learning system that converts construction programme activities into reusable work behavior profiles, derives demand from those profiles, and routes forward demand alerts to subcontractors so they can book project-managed assets before availability tightens.**

It is not a parser, not a scheduler, not an AI wrapper, not a rules engine.

## Core flow

```text
Activity
  → Item identity resolution
  → Classification resolution
  → Context (hierarchy + signals + asset type)
  → Work profile proposal (cached or AI-generated)
  → System finalization of quantity + shape
  → Calendar-mapped demand
  → Weekly lookahead aggregation
  → Gap analysis (demand vs booked)
  → Alert policy evaluation
  → Alert routing to subcontractors
  → Bookings / fulfillment
  → Feedback / corrections
  → Learning
```

---

# 4. Product Purpose and Business Semantics

SiteSpace is a construction programme intelligence platform for Australian commercial construction, initially focused on mid/high-rise building projects.

## Primary users

- Project managers and site teams
- Subcontractors

## Critical business clarification

Subcontractors do **not** own or manage assets in this system.  
Project managers own and manage the project asset pool.

Subcontractors are:

- consumers/requesters of project-managed assets
- recipients of forward demand alerts
- bookers of assets they need to carry out their work

The system routes **asset demand alerts**, not asset ownership.

---

# 5. Hard Constraints

These are binding architectural constraints:

- zero-budget startup
- Railway monolith deployment
- PostgreSQL (single database)
- FastAPI + SQLAlchemy
- additive migrations only
- no ML dependency right now
- no Redis until necessary
- under $5 Claude cost per programme upload
- 1–2 engineers
- future additions must fit additively
- no major or minor rewrites later if this document is followed

## Additional operational constraints

- **Sentry is mandatory** for API and background job exception capture
- **AI calls must be fully auditable** in database logs
- **Silent failure is unacceptable**
- **Debuggability is preferred over elegance** where tradeoffs exist

---

# 6. Assumed Scale

## Near-term

- **Rows per programme:** ~2,000–5,000
- **Projects at launch:** 1–5
- **Projects in first year:** 10–20
- **Programme versions per project:** multiple
- **Year-one total activity rows:** well under ~100,000

## Project lifecycle context

- **Uploads per project lifecycle:** 5–8 (PDF re-exports as programme revisions)
- **Unique contexts per project:** ~300–500 (many activities repeat across uploads)
- **Cross-project item reuse:** high — the same trade work appears across projects with similar phrasing
- **Global learning becomes meaningful:** from project 3–5 onwards for common items; near-complete coverage by project 15–20

## Consequences for learning strategy

- Within a single project lifecycle, the cache can accumulate 5–8 observations per context before the project ends
- Cross-project global knowledge compounds: what takes 3 uploads to confirm in project A is available instantly in project B
- AI cost per upload decreases monotonically as global knowledge matures
- Year-one target: first upload of project 10 costs materially less than first upload of project 1

## Consequences

- PostgreSQL is sufficient
- single monolith is correct
- no partitioning required yet
- JSONB concern is queryability, not scale
- identity, cache, taxonomy, and audit tables remain manageable in Postgres
- storing full AI request/response audit data in Postgres is acceptable at this scale

---

# 7. Current Architecture (Present State)

## 7.1 Current pipeline

```text
Upload file
  → store file metadata
  → parse rows
  → store programme_activities
  → classify each activity (keyword rules + AI/fallback batches)
  → store activity_asset_mappings
  → heuristic subcontractor assignment
  → compute lookahead (simplistic hour math)
  → write JSONB snapshot
  → bookings exist separately
```

## 7.2 Current core tables

Core operational tables that exist:

- `stored_files`
- `programme_uploads`
- `programme_activities`
- `activity_asset_mappings`
- `ai_suggestion_logs`
- `lookahead_snapshots`
- `notifications`
- `slot_bookings`
- `assets`
- `subcontractors`

Important supporting tables also exist:

- `users`
- `site_projects`
- `booking_audit_logs`

## 7.3 Important current facts

- hierarchy exists via `parent_id`, `level_name`, `zone_name`, `is_summary`
- no durable `item_id`
- no `% complete`
- no authoritative `activity_kind`
- no parser row confidence
- no project work week
- `assets.type` is free text
- no `assets.canonical_type`
- the programme upload API currently accepts CSV/XLSX/XLSM only
- PDF is **not** currently supported in the programme ingestion pipeline
- `pdfplumber` is present in dependencies but unused in programme parsing
- routing is heuristic
- lookahead is JSONB-first and formula-based
- lookahead demand is currently `overlap_days × 8`
- current lookahead counts all calendar days, including weekends
- current lookahead ignores `% complete`
- current gap is clamped non-negative
- notification semantics are incomplete
- notifications are not currently created from demand gaps
- `notifications.activity_id` is not nullable
- AI memory is per-upload only
- AI audit logging is not rich enough for later accuracy analysis
- Sentry exists for request-level exceptions, but background tasks and scheduled jobs are not comprehensively enriched or captured
- current upload status values are `processing`, `committed`, and `degraded`

## 7.4 Current strengths

- file upload/storage works
- upload records exist
- activity persistence works
- AI-assisted classification exists
- in-batch dedup reduces some AI calls
- bookings/assets exist
- nightly lookahead job exists
- request-level Sentry wiring already exists

## 7.5 Current limitations that matter immediately

- PDF programme parsing is not actually wired
- classification memory is not persistent across uploads
- no work-profile concept exists
- no distribution-based demand exists
- no lookahead-driven alert creation exists
- no project-scoped background processing guard exists

---

# 8. Critical Current Problems

## 8.1 No persistent identity layer
The same work across uploads, levels, and projects is relearned repeatedly.

## 8.2 Exact normalized-name identity is too naive
Even with normalization, real phrasing variants like:

- `Pour concrete columns`
- `Concrete column pour`
- `Column pour - concrete`

may refer to the same item. The system needs aliasing and operational merge control, not just a unique normalized string.

## 8.3 Wrong demand model
`duration × hours_per_day` does not reflect real work distribution.

## 8.4 No persistent classification memory in the right place
Known asset requirements are relearned repeatedly instead of being resolved once and reused everywhere.

## 8.5 Classification and work-profile generation are insufficiently separated
If work-profile inference also decides asset type, the system cannot accumulate stable asset memory cleanly.

## 8.6 Incorrect demand inputs
No structural support for project work week or `% complete`.

## 8.7 PDF fixture unsupported in real upload flow
The canonical ARC Bowden regression fixture is a PDF P6 export, but current programme ingestion does not support PDFs.

## 8.8 Asset canonical typing is too weak
A static hardcoded list with `other` as catchall will decay quickly without governance.

## 8.9 `none` asset type is missing from current runtime classification constraints
This forces truly non-asset tasks toward `other` or leaves them unresolved.

## 8.10 Routing is unsafe
Heuristic-only trade routing is not production-grade.

## 8.11 Lookahead storage is weak
JSONB is not a sufficient operational source.

## 8.12 Parser row typing is too weak
Summary rows, milestones, and noise need explicit treatment.

## 8.13 Notification model is underspecified
The real product is forecast-driven weekly asset alerts, not per-activity assignment.

## 8.14 Cold-start alert spam risk
If booked hours are near zero, every demand bucket becomes an alert unless thresholds and ramp-up controls exist.

## 8.15 Notification dedupe is opaque
A hash-only dedupe key is harder to debug than explicit composite uniqueness.

## 8.16 AI auditability is insufficient
The system must later answer:

- what prompt was sent
- what context was used
- what the model returned
- what failed validation
- what fallback was used
- whether the user later corrected it

## 8.17 Observability is too weak for a tiny team
Structured logs alone are insufficient. Exceptions and degraded states must surface automatically.

## 8.18 No cache versioning or invalidation contract
If context extraction changes, or manual truth supersedes AI truth, the cache behavior is undefined unless versioning and override rules exist.

## 8.19 Determinism is underspecified
If prompt version, model family, or inference policy changes, the same semantic context could otherwise produce different outputs over time with no explicit boundary.

## 8.20 Confidence has no operational meaning
If low-confidence outputs are stored but still behave exactly like trusted outputs, confidence is not improving safety.

## 8.21 No safe degraded mode
If AI is unavailable or unreliable, the system needs a defined reduced-function mode instead of ad hoc failure behavior.

## 8.22 AI quantity control is too broad
If AI freely determines total hours with weak bounds, bad quantity estimates directly pollute demand, gaps, and alerts.

## 8.23 No project-level alert cap
Per-recipient caps alone do not prevent overall project alert spam.

## 8.24 No demand anomaly guardrail
The system lacks an explicit way to detect impossible or implausible weekly demand spikes caused by parser duplication, bad AI outputs, or context fragmentation.

## 8.25 Context fallback behavior is ambiguous
If the system reduces context specificity under cost or explosion pressure but does not do so in a fixed order, determinism is broken operationally.

## 8.26 Calendar semantics are too loose
If a 6-day week can effectively skip Saturday, demand timing becomes wrong.

## 8.27 Per-day rounding corrupts demand
If mapped daily hours are rounded independently, total hours drift and distributions distort.

## 8.28 Upload status semantics are too coarse
Current `degraded` status conflates partial success and full failure.

## 8.29 No per-day cap on individual distribution buckets
The total-hours bounds clamp exists for the full profile, but individual daily distribution buckets are unchecked. AI can propose a shape like `[12, 0, 8, 0, 14]` for a 5-day crane task. All bucket values pass the current sum-to-total check while individual values exceed physically possible daily operation. This distorts demand timing and can cause impossible single-day peaks in the lookahead.

## 8.30 First-answer problem: no evidence accumulation on either AI pass
Both AI passes have the same root problem — the first answer sticks.

For **classification**: the first AI-assigned asset type is stored as the active classification and reused indefinitely. If wrong, it silently pollutes every subsequent work profile for that item.

For **work profiles**: the first AI-generated `total_hours` and distribution are cached and reused. If wrong, demand and gap calculations are wrong for every occurrence of that item in perpetuity.

Neither pass currently accumulates evidence, tracks confirmation rate, or has a mechanism to re-evaluate stale first answers. The mechanisms differ — classification needs categorical confirmation counting, work profiles need Bayesian posterior updating — but both need them.

## 8.31 No cross-project learning
Classification and work-profile knowledge is siloed per project. High-confidence profiles confirmed across multiple uploads within one project are not shared with new projects. Every new project's first upload incurs full AI cost even when the system has already resolved the same work elsewhere. Given 5–8 uploads per lifecycle and 10–20 projects in year one, the compounding learning opportunity is wasted.

---

# 9. Proposed-Current Architecture: Overview

## 9.1 Core shift

**From**
```text
Formula-based estimation: duration × hours_per_day
```

**To**
```text
Distribution-based work profiles: cached or AI-generated, with system-finalized quantity controls
```

## 9.2 Key concepts

### Work Profile
Structured representation of asset demand over a task’s duration.

### Classification Memory
Persistent item-level resolution of asset type, reused across uploads and contexts.

### Item Identity
Persistent task identity across uploads, projects, and phrasing variants.

### Item Aliases
Normalized variants that map different textual forms to the same item.

### Context
Signals that affect work behavior.

### Context Cache
AI is called once per unique context and inference policy.

### Alert Policy
A project-level rule set that determines whether a demand gap becomes an external alert.

### Inference Version
A versioned bundle representing the inference policy used to generate a cached profile.

### Classification Maturity
The confidence tier of a persisted classification based on `confirmation_count` and `correction_count`. Ranges from TENTATIVE (first call, unconfirmed) to CONFIRMED (2+ confirmations, zero corrections) to STABLE (5+ confirmations, zero corrections) to PERMANENT (manual). Controls whether classification AI is re-queried on the next encounter.

### Work-Profile Maturity
The confidence tier of a cached work profile based on Bayesian posterior precision. Ranges from TENTATIVE to CONFIRMED to TRUSTED_BASELINE. Controls whether work-profile AI is called fresh, used as a hint, or skipped entirely. MANUAL overrides everything.

Both passes have maturity tiers. The mechanism differs because classification is categorical (confirmation counting) and work profiles are continuous (Bayesian posterior).

### Bayesian Cache Evidence
The mechanism by which cached work-profile `total_hours` estimates improve over time. Each new observation updates the posterior mean and precision using a Normal-Normal conjugate update. Actuals carry ~16× more precision weight than AI estimates. The coefficient of variation of the posterior determines whether AI is called again, used as a hint, or skipped entirely. Does not apply to classification — classification uses confirmation counting instead.

### Global Knowledge Base
A cross-project tier of high-confidence cached profiles available to seed new project caches. Promoted only after multi-upload confirmation across at least two projects. Only applied when the target project has the relevant asset type present in its asset pool.

## 9.3 Full pipeline visual

```text
Upload
  │
  ▼
Parse file
  ├─ detect row type
  ├─ assign row_confidence
  ├─ capture hierarchy
  ├─ capture pct_complete
  ├─ strip noise
  └─ capture dates/duration
  │
  ▼
programme_activities
  + pct_complete
  + activity_kind
  + row_confidence
  + item_id
  │
  ▼
identity resolution
  ├─ normalize name
  ├─ resolve alias → item
  ├─ create new item + alias if unknown
  └─ record match method
  │
  ▼
classification resolution
  ├─ active item_classification?
  ├─ keyword rules?
  └─ AI classification (standalone, only if needed)
       └─ persist item_classification
  │
  ▼
context_builder (classification included)
  │
  ▼
deterministic_context_key
  = hash(item + asset_type + duration + compressed_context + context_version + inference_version)
  │
  ▼
cache lookup (item_context_profiles)
  │
  ├─ HIT → reuse
  └─ MISS →
       ├─ if context cap hit: follow deterministic reduced-context fallback
       └─ AI generates work profile proposal
            ├─ asset_type provided as input
            ├─ validate structure
            ├─ finalize total_hours via system hours policy
            ├─ quantize total_hours to operational unit
            ├─ validate final profile
            ├─ store cache entry
            └─ write AI audit log
  │
  ▼
activity_work_profiles
activity_asset_mappings
  │
  ▼
demand engine
  ├─ apply pct_complete
  ├─ map to calendar using fixed weekly template
  ├─ apportion integer scheduling units across days
  ├─ aggregate weekly
  └─ run anomaly checks
  │
  ▼
lookahead_rows
lookahead_snapshots.data
  │
  ▼
alert signal derivation
  ├─ gap analysis
  ├─ confidence policy
  ├─ anomaly policy
  ├─ project alert policy
  ├─ explicit subcontractor routing
  ├─ alert severity ranking
  ├─ alert rate limiting
  └─ active-notification dedupe by composite uniqueness
  │
  ▼
notifications
  │
  ▼
delivery worker
  │
  ▼
bookings / fulfillment
  │
  ▼
corrections / merges / taxonomy updates / learning
```

---

# 10. Work Profile: The Center of the System

## 10.1 Definition

```json
{
  "item_id": "install_bd_reo",
  "asset_type": "crane",
  "duration_days": 2,
  "total_hours": 10,
  "distribution": [7, 3],
  "normalized_distribution": [0.7, 0.3],
  "confidence": 0.82,
  "source": "ai"
}
```

## 10.2 Rules

- `asset_type` must equal the resolved classification for the item in the current processing context
- `len(distribution) == duration_days`
- `sum(distribution) == total_hours`
- all values in `distribution >= 0`
- `len(normalized_distribution) == duration_days`
- if `total_hours > 0`, `sum(normalized_distribution) == 1.0` within tolerance
- if `total_hours == 0`, `normalized_distribution` must be all zeros
- `total_hours` used for operational mapping must be representable in the operational scheduling unit
- distribution is not assumed uniform
- same deterministic context key returns same profile after first resolution

## 10.3 Strict `none` asset rule

If:

```text
asset_type = 'none'
```

then enforce:

```text
total_hours = 0
distribution = [0, 0, ...]
normalized_distribution = [0, 0, ...]
```

This prevents ghost demand and false alerts.

## 10.4 What this replaces

- `hours_per_day` constants
- linear rate formulas
- simplistic embedded lookahead math

## 10.5 Why shape matters

Construction work is front-loaded, back-loaded, clustered, or ramped. Demand timing matters as much as demand quantity.

## 10.6 Responsibility split: shape vs quantity

### Current v1
On a cache miss, classification is resolved first.

Then AI may propose:

- `total_hours`
- `normalized_distribution`

The system then derives raw `distribution` from finalized `total_hours × normalized_distribution`, validates the result, and stores both forms.

### Directional rule
AI is strongest at **shape** and weaker at **quantity**.

Therefore:

- `normalized_distribution` is the preferred AI contribution
- `total_hours` is an AI proposal, not unquestioned truth
- `total_hours` must remain subject to validation, priors, manual correction, and future learned baselines
- over time, the system should increasingly own `total_hours` through historical actuals and learned patterns

## 10.7 V1 total-hours finalization policy

Final `total_hours` must be chosen by the system using this precedence:

```text
if manual truth exists:
    use manual
else if trusted historical baseline exists:
    use baseline
else:
    use ai_proposal_bounded
```

### Meaning

- **manual** = explicit corrected value for this item/context
- **trusted historical baseline** = system-owned quantity prior for this item/duration
- **ai_proposal_bounded** = AI proposal after clamp against explicit bounds

## 10.8 Trusted historical baseline rule

A trusted historical baseline exists for `(item_id, asset_type, duration_days)` when either:

1. there is a manual work profile for that item, asset type, and duration, or
2. there are at least 3 non-default, non-low-confidence cached profiles for that item, asset type, and duration

In that case:

```text
baseline_total_hours = median(trusted_total_hours for item_id + asset_type + duration_days)
```

The baseline is used directly as final `total_hours` in v1.

## 10.9 Concrete v1 hours bounds

When no trusted baseline exists, bound AI-proposed hours using explicit caps.

`max_hours_per_day` is stored in the `asset_types` table, not hardcoded. See Section 21.2 for the column definition and Section 21.3 for seed values.

### V1 seed values

| Group | Types | max_hours_per_day |
|---|---|---|
| Exclusive / high-control | `crane`, `hoist`, `loading_bay`, `concrete_pump` | 10 |
| Fungible / shared-use | `ewp`, `excavator`, `forklift`, `telehandler`, `compactor`, `other` | 16 |
| No asset | `none` | 0 |

### V1 clamp

For non-`none` asset types:

```text
min_hours = 0.5
max_hours = max_hours_per_day(asset_type) * duration_days
bounded_total_hours = clamp(ai_proposal, min_hours, max_hours)
```

For `none`:

```text
bounded_total_hours = 0
```

### Two enforcement points

Per-day caps are enforced in two separate places:

1. **Work-profile validation (Stage B)** — each individual `distribution[i]` bucket must not exceed `max_hours_per_day`. Enforced at generation time before cache storage.
2. **Demand engine** — after calendar mapping, each mapped daily value is validated against the per-day cap. Flags any day that exceeds the limit as anomalous even if the total profile passed validation.

This is not redundant. The second check catches edge cases from rounding or apportionment drift.

## 10.10 Operational total-hours unit rule

V1 operational mapping unit is:

```text
0.5 hours (30 minutes)
```

Therefore, before storage as operational work-profile truth:

```text
final_total_hours must be quantized to 0.5-hour increments
```

This is required so that calendar mapping can preserve exact totals.

### Future note
A future system may support:

```text
0.25 hours (15 minutes)
```

but v1 standard is 30-minute increments.

## 10.11 Per-day distribution bucket cap

Each individual bucket in `distribution` must satisfy:

```text
distribution[i] <= max_hours_per_day(asset_type)
```

For `none`:

```text
distribution[i] == 0  (enforced by the strict none rule)
```

### Enforcement at validation Stage B

Before cache storage, after AI proposal:

```text
for each bucket in distribution:
    if bucket > max_hours_per_day(asset_type):
        → reject proposal, retry with cap hint
        → if still invalid: redistribute excess to adjacent days
        → log violation in AI audit log
```

Redistribution rule: spill excess to adjacent work days proportionally, preserving `sum(distribution) == total_hours`. If redistribution would also violate adjacent-day caps, flag the profile as `low_confidence_flag = TRUE` and fall back to a uniform cap-compliant distribution.

### Enforcement in demand engine

After calendar mapping:

```text
for each mapped daily value:
    if daily_value > max_hours_per_day(asset_type):
        → mark day as anomalous
        → flag lookahead_row with daily_cap_exceeded flag
        → suppress external alerts for that bucket
```

## 10.12 Hours policy versioning

The total-hours finalization policy, including bounds, baseline rules, per-day caps, and operational-unit normalization, is versioned via `hours_policy_version` inside `inference_policies`.

Any material change to this logic requires a new `inference_version`.

---

# 11. Identity Layer

## 11.1 Purpose

The same task appearing across:

- different levels
- different zones
- different uploads
- different projects
- different phrasings

must converge to one persistent item when they are actually the same work.

## 11.2 Critical correction to prior design

A unique `normalised_name` on `items` is **not enough**.

It is safe for exact-text dedup. It is **not** a complete semantic identity strategy.

Exact normalization should be:

- deterministic
- conservative
- auditable

It should **not** aggressively auto-merge semantically similar phrases unless confidence is high.

## 11.3 Early-stage simplification

Implement now:

- `items`
- `item_aliases`
- manual merge
- merged-item redirect at runtime

Do **not** implement yet:

- split operations
- historical compaction jobs
- alias reassignment edge-case tooling between active items

These are deferred. Early over-engineering here slows delivery more than it helps.

## 11.4 Identity model

### `items`

```text
id: UUID PK
display_name: TEXT
identity_status: VARCHAR(20) DEFAULT 'active'   # 'active' | 'merged'
merged_into_item_id: UUID nullable FK → items.id
created_at: TIMESTAMP
updated_at: TIMESTAMP
```

### `item_aliases`

```text
id: UUID PK
item_id: UUID FK → items.id
alias_normalised_name: TEXT
normalizer_version: SMALLINT DEFAULT 1
alias_type: VARCHAR(20)         # 'exact' | 'variant' | 'manual'
confidence: VARCHAR(10)         # 'high' | 'medium' | 'low'
source: VARCHAR(20)             # 'parser' | 'manual' | 'reconciled'
created_at: TIMESTAMP
updated_at: TIMESTAMP

UNIQUE(alias_normalised_name, normalizer_version)
```

## 11.5 Resolution algorithm

During ingestion:

1. normalize raw activity name using current normalizer version
2. lookup exact match in `item_aliases`
3. if found:
   - resolve `item_id`
   - if item is merged, follow `merged_into_item_id` to active item
4. if not found:
   - create new `items` row
   - create new `item_aliases` row with `alias_type='exact'`

## 11.6 Operational rule

**Exact normalization is a seed identity mechanism, not final semantic truth.**

This is intentional. False merges are more dangerous than temporary duplicate items.

## 11.7 Normalization rules

The normalizer must:

- lowercase
- strip leading `Day N -` prefixes
- collapse whitespace
- strip configurable punctuation
- preserve semantic suffixes like `pour 1` vs `pour 2`
- remain deterministic

## 11.8 What about phrase-order variants?

Variants like:

- `pour concrete columns`
- `concrete column pour`
- `column pour concrete`

will often create separate aliases initially.

That is acceptable in v1.

The system resolves this via:

- manual alias addition
- manual merge operations
- future similarity suggestions

It does **not** rely on risky aggressive token sorting as authoritative identity.

## 11.9 Normalizer versioning

`normalizer_version` belongs on aliases, not as sole truth on items.

Why:

- the same item may accumulate aliases produced under different normalizer versions
- version changes should not silently rewrite history
- identity upgrades must be operationally controllable

## 11.10 Manual merge procedure

When two items are determined to be the same:

1. choose a **survivor** item
2. mark loser item:
   - `identity_status = 'merged'`
   - `merged_into_item_id = survivor.id`
3. move non-conflicting aliases to the survivor item where appropriate
4. merge classification memory
5. merge or overwrite cache entries according to cache override rules
6. record an identity audit event

### Current-stage rule

Do **not** repoint all historical rows immediately. Existing historical rows may continue to reference the merged item.

### Runtime rule

All runtime item lookups must resolve through the active canonical item by following `merged_into_item_id`.

## 11.11 `item_identity_events`

```text
id: UUID PK
event_type: VARCHAR(20)         # 'merge' | 'alias_add'
source_item_id: UUID nullable FK → items.id
target_item_id: UUID nullable FK → items.id
details_json: JSONB
created_by_user_id: UUID nullable
created_at: TIMESTAMP
```

This is required because identity changes must be explainable later.

## 11.12 `programme_activities.item_id`

```text
item_id: UUID nullable FK → items.id
```

Set after alias resolution.

---

# 12. Context Architecture

Context is everything that influences how work behaves.

## 12.1 Context builder

Inputs:

```json
{
  "canonical_item_id": "...",
  "asset_type": "crane",
  "activity_name": "Install BD reo",
  "duration_days": 2,
  "hierarchy": ["SUPERSTRUCTURE", "LEVEL 4", "Zone A", "Bathroom"]
}
```

Outputs:

```json
{
  "context_signature": "...",
  "compressed_context": {
    "phase": "structure",
    "spatial_type": "level",
    "zone_present": true,
    "area_type": "internal",
    "work_type": "column"
  },
  "unknown_context": ["Bathroom"],
  "ai_context": { ... }
}
```

## 12.2 Deterministic rule

Same inputs must always produce the same context signature.

## 12.3 Hierarchy handling

### Raw hierarchy
Preserved for audit and AI context.

### Compressed semantic context
Used in cache key.

### Unknown hierarchy
Passed to AI, excluded from cache key.

## 12.4 Fixed-schema rule

`compressed_context` must be:

- fixed-schema at its base
- enum-based where possible
- bounded-cardinality

### Initial base schema

```json
{
  "phase": "structure | facade | services | fitout | external | prelims | unknown",
  "spatial_type": "project | building | level | zone | room | area | unknown",
  "zone_present": true,
  "area_type": "internal | external | roof | basement | podium | unknown"
}
```

## 12.5 Bounded extension fields

The compressed context must support a small set of approved optional extension fields with bounded enums.

### Initial approved extension

```json
{
  "work_type": "slab | column | wall | core | facade | services | fitout | inspection | unknown"
}
```

Rules:

- extension fields must be explicitly approved
- extension values must be bounded enums
- unknown values stay in `unknown_context`, not in new ad hoc cache-key fields

This avoids false cache reuse without allowing context explosion.

## 12.6 Context versioning

Add:

```text
context_version: SMALLINT
```

to:

- `item_context_profiles`
- `activity_work_profiles`
- context key generation
- AI audit logs where relevant

This prevents silent cache drift when context extraction logic changes.

## 12.7 Inference versioning

Add:

```text
inference_version: SMALLINT
```

This represents the versioned generation policy bundle for a cached profile.

It must be bumped whenever a material change occurs in any of:

- prompt structure or prompt version
- model name or model family
- pattern library semantics
- validation rules that change accepted outputs
- hours-bounds policy
- other inference-time behavior that could materially change generated profiles

## 12.8 Inference-version immutability invariant

`inference_version` must uniquely and immutably map to:

- `model_name`
- `model_family`
- `prompt_version`
- `validation_rules_version`
- `pattern_library_version`
- `hours_policy_version`

These mappings must never change after creation.

This is what makes `inference_version` a valid determinism boundary.

## 12.9 Deterministic context key

```text
deterministic_context_key =
  hash(canonical_item_id + asset_type + duration_days + compressed_context + context_version + inference_version)
```

This is the key used for cache lookup and reuse.

## 12.10 Versioning rule

When context extraction logic changes in a way that could alter cache semantics:

- increment `context_version`
- old cache entries remain stored for audit/history
- new processing only reads cache entries for the current `context_version`

When inference-generation policy changes materially:

- increment `inference_version`
- old cache entries remain stored for audit/history
- new processing only reads cache entries for the current `inference_version`

## 12.11 Context explosion safeguard

Define an operational limit:

```text
max_new_contexts_per_upload
```

If the number of new unique deterministic context keys in one upload exceeds this threshold:

- stop creating increasingly specific contexts
- follow deterministic reduced-context fallback
- flag the upload with warnings
- surface the event in logs/Sentry if severe

This prevents cost spikes and runaway cache fragmentation.

## 12.12 Deterministic reduced-context fallback order

Reduced-context fallback is only allowed when one of the following is true:

- `max_new_contexts_per_upload` has been reached
- system degraded mode is active
- operator explicitly forces reduced-context operation

The fallback order must be fixed and deterministic:

1. **Exact full compressed context**
   - base schema + approved extension fields
2. **Base compressed context only**
   - drop all approved extension fields in a fixed order
   - in v1 this means dropping `work_type`
3. **No broader cache reuse than base context**
   - do not drop `phase`
   - do not drop `spatial_type`
   - do not collapse base fields to `unknown` for extra reuse
4. **If still unresolved**
   - do not keep broadening cache semantics
   - use default/profile fallback path with warnings, or AI if policy allows

This rule is required to preserve determinism and debuggability.

## 12.13 Adaptive context expansion (future-ready rule)

Formal rule:

```text
if same context signature repeatedly produces high-variance profiles:
    expand compressed_context
    bump context_version
```

Example:

```text
before:
  item + asset_type + duration + structure

after:
  item + asset_type + duration + structure + facade
```

This remains a future refinement. Current implementation uses a fixed compressed context extractor.

---

# 13. Deterministic AI Architecture

## 13.1 Core principle

> AI is used once per bounded problem and cached. Classification and work-profile generation are logically separate, even if implemented in the same service module.

## 13.2 Logical separation, physical simplicity

The architecture requires:

- **classification AI** = decide `asset_type` only
- **work-profile AI** = decide `total_hours` proposal + `normalized_distribution`

These are logically independent stages.

They may still be implemented inside the same FastAPI service and use the same provider client. No microservice split is required.

## 13.3 Hard prerequisite: classification first

Work-profile generation **must not occur** without a resolved `asset_type`.

This is a hard architectural rule.

Normal operation is:

1. resolve classification
2. freeze `asset_type`
3. build context with `asset_type`
4. generate or reuse work profile

## 13.4 Classification resolution pipeline

Classification must be resolved **before** work-profile generation.

### Resolution order

1. active `item_classifications`
2. keyword rules
3. standalone AI classification

### Standalone AI classification contract

#### Input

```json
{
  "activity_name": "...",
  "raw_hierarchy": ["..."],
  "allowed_asset_types": ["crane", "hoist", "loading_bay", "ewp", "concrete_pump", "excavator", "forklift", "telehandler", "compactor", "other", "none"]
}
```

#### Output

```json
{
  "asset_type": "crane",
  "confidence": 0.9
}
```

### Classification validation

Hard checks:

- `asset_type` is in `asset_types`
- confidence in `[0,1]`

If classification AI succeeds:

- persist `item_classifications`
- continue to work-profile stage using that asset type

## 13.5 Work-profile generation pipeline

Once classification is resolved:

1. build deterministic context using `item_id + asset_type + duration + context + versions`
2. lookup `item_context_profiles`
3. on cache hit, reuse
4. on miss, call work-profile AI with `asset_type` provided as input

### Work-profile AI contract

#### Input

```json
{
  "item_id": "...",
  "asset_type": "crane",
  "duration_days": 9,
  "context": {...},
  "pattern_library": {...}
}
```

#### Output

```json
{
  "total_hours": 54,
  "normalized_distribution": [...],
  "confidence": 0.84
}
```

### Normal-path rule

**Work-profile AI must not infer asset type in the normal path.**  
`asset_type` is supplied as input and must not be changed.

## 13.6 Optional transport-level combined provider fallback

A single provider roundtrip may still be used as a transport optimization or emergency fallback when:

- classification is unknown, and
- operator policy allows it, or
- provider constraints make one-shot generation materially simpler

But even then:

- classification and work-profile decisions remain logically separate
- the returned asset type must first be validated and frozen as classification
- the work-profile cache key must then be formed using that asset type
- the work-profile output must not override the frozen classification
- audit logs must clearly distinguish classification output from work-profile output

This is not a bypass of the classification-first invariant. It is only a transport-level exception.

## 13.7 Validation stages

### Stage A — Classification validation
Hard checks on resolved asset type:

- asset type exists in taxonomy
- confidence is valid

### Stage B — Work-profile AI proposal validation
Hard checks on the raw AI proposal:

- normalized distribution length matches duration
- normalized values are all `>= 0`
- normalized distribution sums to `1.0` within tolerance for non-zero-hour profiles
- confidence in `[0,1]`
- non-`none` asset types must have positive proposed hours
- `none` asset type must have zero hours and all-zero distributions
- no individual `distribution[i]` bucket exceeds `asset_types.max_hours_per_day` for the resolved asset type

### Stage C — System finalization
The system finalizes `total_hours` using the total-hours finalization policy, bounds rules, and operational-unit quantization.

### Stage D — Final profile validation
Hard checks on the finalized profile:

- derived raw distribution sums to finalized total hours within tolerance
- all derived raw values are `>= 0`
- finalized total hours is within allowed bounds
- finalized total hours is representable in the operational scheduling unit
- `none` asset rule still holds
- `asset_type` equals resolved classification

## 13.8 Soft checks

- compare total hours against historical profile range
- compare proposed hours against task priors where available
- detect implausible shapes for known task types
- flag low confidence
- flag suspicious mismatch between parser row confidence and strong AI certainty

## 13.9 Retry and fallback

- retry once with explicit validation hint
- if still invalid:
  - flag activity
  - fall back to default pattern or skip profile
  - never fail whole upload because of one bad AI response

## 13.10 Cold start pattern library

Provide seed **normalized** distributions by duration and task-type hint.

AI chooses or adapts from this library.

## 13.11 Cost control

- cache first
- batch misses
- classification AI only on true classification unknowns
- work-profile AI only on true work-profile cache misses
- budget guard near $5/upload

## 13.12 Explicit upload budget guard behavior

When nearing AI cost limit:

```text
skip remaining AI →
use:
  - active item classification memory if available
  - keyword rules if available
  - deterministic reduced-context fallback if allowed
  - default pattern library fallback
  - explicit warning flags on affected rows
```

This prevents upload failure and cost blowouts.

## 13.13 AI audit logging is mandatory

Every AI interaction must be logged richly enough to support:

- debugging
- cost analysis
- accuracy analysis
- correction backtesting
- prompt version comparisons
- AI proposal vs finalized output analysis

### Important logging rule

- **Full logs are required for actual AI calls**
- **Cache hits are not logged as full AI records**
- cache hit rates are tracked in upload metrics instead

This keeps database noise manageable and preserves signal quality.

### Required fields

Extend `ai_suggestion_logs` or add equivalent columns so each AI-generated decision records:

```text
id: UUID PK
upload_id: UUID nullable
activity_id: UUID nullable
item_id: UUID nullable
pipeline_stage: VARCHAR(30)       # 'classification' | 'work_profile'
context_hash: VARCHAR(64) nullable
context_version: SMALLINT nullable
inference_version: SMALLINT nullable
model_name: VARCHAR(100)
model_family: VARCHAR(50) nullable
prompt_version: VARCHAR(50)
request_json: JSONB
response_json: JSONB
final_output_json: JSONB nullable
validation_status: VARCHAR(20)    # 'valid' | 'invalid' | 'fallback'
validation_errors_json: JSONB nullable
retry_count: SMALLINT DEFAULT 0
hours_clamped: BOOLEAN DEFAULT FALSE
latency_ms: INTEGER nullable
input_tokens: INTEGER nullable
output_tokens: INTEGER nullable
cost_usd: NUMERIC nullable
accepted: BOOLEAN nullable
correction_json: JSONB nullable
created_at: TIMESTAMP
```

### Rules

- `pipeline_stage='classification'` logs contain classification decisions only
- `pipeline_stage='work_profile'` logs do not perform asset-type inference in the normal path
- store the raw structured request and response
- store the finalized post-policy output when AI proposal is transformed
- store validation failures, not just final result
- store token counts and cost when available
- link later manual corrections back to originating AI logs

---

# 14. Cache Semantics, Override Rules, and Invalidation

## 14.1 Cache source priority

When cached profiles conflict, source priority is:

```text
manual > learned > ai > default
```

Higher priority always overrides lower priority.

## 14.2 Why this exists

Without an explicit override contract:

- stale AI outputs become permanent truth
- weak default fallbacks can linger too long
- manual corrections fail to propagate meaningfully

## 14.3 Manual truth rule

Manual truth is global priority truth.

It must:

- never be overridden by AI
- never be downgraded by default/cache fallback
- persist across uploads
- only be replaced by a subsequent manual correction or a deliberate administrative action

## 14.4 Cache overwrite behavior

On manual correction:

```text
→ overwrite matching item_context_profiles entry
→ set source = 'manual'
→ set confidence = 1.0
```

## 14.5 Bayesian evidence accumulation

The cache must not treat the first AI answer as permanent truth. Each new observation — whether a subsequent AI call, an actual from a completed booking, or a manual correction — must update the cached estimate using a Normal-Normal conjugate update.

### What is stored

```text
posterior_mean:      NUMERIC   -- current best estimate (μ)
posterior_precision: NUMERIC   -- confidence (τ = 1/σ²; higher = more confident)
sample_count:        INTEGER   -- total observations informing this estimate
correction_count:    INTEGER   -- times manually corrected
actuals_count:       INTEGER   -- actual booking hours incorporated
actuals_median:      NUMERIC nullable  -- rolling median of captured actuals
```

### Update rule

```text
new_precision = current_precision + obs_precision
new_mean      = (current_precision × current_mean + obs_precision × observation) / new_precision
```

Two stored numbers, two lines. That is the entire update.

### Observation precision by source

| Source | Assumed error | obs_precision |
|---|---|---|
| AI estimate | ±20% of value | `1 / (0.20 × x)²` |
| Actual booking | ±5% of value | `1 / (0.05 × x)²` |
| Manual correction | perfect | `current_precision × 1000` (effectively overrides) |

Actuals carry approximately 16× more weight than AI estimates `(0.20/0.05)²`. This is correct: real hours from site are worth far more than AI guesses.

### Setting the initial prior

On the first AI call for a context:

```text
σ_prior       = ai_estimate × (1 - ai_confidence)
posterior_mean = ai_estimate
posterior_precision = 1 / σ_prior²
sample_count   = 1
```

### `observation_count` and `evidence_weight`

Retained for backward compatibility. `observation_count` continues to track raw reuse frequency. `evidence_weight` tracks confidence-weighted support. These are secondary signals; `posterior_precision` is the primary confidence measure.

Default entries do not increment any evidence field.

## 14.6 Conflict resolution within same priority

If two candidate cache entries have the same source priority, prefer in order:

1. higher `confidence`
2. higher `evidence_weight`
3. higher `observation_count`
4. newer `updated_at`
5. deterministic tie-break on `id`

This prevents weak same-tier entries from dominating stronger ones.

## 14.7 Classification/work-profile consistency invariant

For any cached or materialized work profile:

```text
work_profile.asset_type == resolved classification.asset_type
```

And for any cache entry:

```text
item_context_profiles.asset_type == asset_type used in deterministic_context_key
```

## 14.8 Cache invalidation triggers

The cache must update, supersede, or stop being used when any of the following occurs:

- manual correction
- item merge
- classification change that changes resolved asset type
- `context_version` bump
- `inference_version` bump

## 14.9 Behavior by trigger

### Manual correction
Overwrite or insert the exact cache entry with `source='manual'`.

### Item merge
Merge loser cache entries into survivor item using source priority rules, then same-tier conflict rules.

If two merged items produce cache entries with the same deterministic context key:

- keep the highest-priority entry
- within same priority, keep the higher-confidence entry
- merge `observation_count` and `evidence_weight` into the surviving entry when appropriate
- discard or supersede conflicting lower-priority entries

### Classification change
If classification changes the asset type for a context:

- old cache rows remain historical
- new processing uses the new asset type
- new deterministic context keys include the new asset type
- conflicting reuse across asset types is not allowed

### Context version bump
Do not mutate old entries. New processing ignores old-version cache rows.

### Inference version bump
Do not mutate old entries. New processing ignores old-version cache rows.

## 14.10 Default profiles in cache

Default fallback profiles may be stored in cache, but only with:

- `source='default'`
- low confidence
- lowest override priority

Additional rules:

- default entries do **not** increment `observation_count`
- default entries do **not** increment `evidence_weight`
- default entries are superseded by the first non-default signal for the same key
- default entries exist to avoid repeated failure loops, not to become durable truth

This prevents cache pollution.

## 14.11 Profile maturity tiers

A cached profile's maturity determines whether AI is called again, used only as a hint, or skipped entirely.

```text
cv = (1 / √posterior_precision) / posterior_mean   -- coefficient of variation
```

| Tier | Condition | Behaviour |
|---|---|---|
| TENTATIVE | `cv >= 0.20` | Call AI fresh; update posterior with result |
| CONFIRMED | `0.10 <= cv < 0.20` | Call AI with posterior as hint in prompt; update posterior |
| TRUSTED_BASELINE | `cv < 0.10` | Skip AI entirely; use `posterior_mean` directly |
| MANUAL | `source = 'manual'` | Skip AI entirely; posterior irrelevant; manual value is final |

Actuals accelerate promotion: a single actual observation typically moves a TENTATIVE profile to TRUSTED_BASELINE in one step because actual precision is ~16× AI precision.

## 14.12 Correction rate trigger

If a cache entry accumulates a high correction rate it should be re-evaluated:

```text
if correction_count / sample_count > 0.20 AND sample_count >= 3:
    → treat as TENTATIVE regardless of posterior_precision
    → call AI fresh on next encounter
    → if new AI answer differs by > 15%: update posterior, reset correction_count
```

This catches profiles that are quietly wrong without requiring explicit flagging.

## 14.13 Shape vs quantity independence

`posterior_mean` and `posterior_precision` apply to **total_hours only**.

`normalized_distribution` (shape) is updated separately:

- shape is more stable than quantity across occurrences
- actuals-informed shape updates are deferred to Stage 11 (Feature Learning)
- in v1, the shape from the highest-priority source (manual, then highest-confidence AI call) is retained
- `posterior_mean` overrides only the quantity; the stored shape is scaled to match the new total

This means actuals improve quantity accuracy immediately while shape improvement is deferred until enough data exists to be meaningful.

---

# 15. Confidence Policy

## 15.1 Purpose

Confidence must change system behavior. It is not just metadata.

## 15.2 Low-confidence definition

A work profile is considered low confidence when:

- AI returns confidence below configured threshold, or
- validation soft checks indicate material concern, or
- the source is `default`, or
- the originating parser row had `row_confidence = 'low'`

In these cases:

```text
low_confidence_flag = TRUE
```

## 15.3 Internal behavior

Low-confidence profiles:

- are included in internal lookahead
- are highlighted in UI for review/correction
- are prioritized in correction workflows
- are surfaced in upload warnings and observability

## 15.4 External alert behavior

Low-confidence demand must not, by itself, trigger external subcontractor alerts.

Rule:

- if an alert candidate’s positive gap is supported only by low-confidence profiles, suppress the external alert
- still show the demand internally to project users
- allow future override or manual review workflows later

This reduces external noise during early system trust-building.

## 15.5 Review prioritization

Correction/review queues should rank uncertain work by both:

- lowest confidence first
- highest operational impact second

Example impact signals:

- high demand hours
- high gap hours
- near-term schedule proximity
- large downstream alert effect

## 15.6 Learning behavior

In future learning systems, profile contribution must be confidence-weighted.

At minimum:

```text
learning_weight = confidence
```

Low-confidence data influences learning less than trusted data.

---

# 16. Work Profile Storage

## 16.1 `activity_work_profiles`

```text
id: UUID PK
activity_id: UUID FK → programme_activities.id
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
duration_days: SMALLINT
context_version: SMALLINT
inference_version: SMALLINT FK → inference_policies.version
total_hours: NUMERIC
distribution_json: JSONB
normalized_distribution_json: JSONB
confidence: NUMERIC
low_confidence_flag: BOOLEAN DEFAULT FALSE
source: VARCHAR(20)     # 'ai' | 'cache' | 'manual' | 'default'
context_hash: VARCHAR(64)
created_at: TIMESTAMP
```

## 16.2 Rules

- one profile per activity per upload
- `normalized_distribution_json` is the ratio form of `distribution_json`
- `low_confidence_flag = TRUE` if confidence is below configured threshold, source is `default`, or row confidence was low
- `asset_type` must match the resolved classification used for the activity
- `total_hours` must be representable in the operational scheduling unit

## 16.3 `item_context_profiles`

```text
id: UUID PK
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
duration_days: SMALLINT
context_version: SMALLINT
inference_version: SMALLINT FK → inference_policies.version
context_hash: VARCHAR(64)
total_hours: NUMERIC
distribution_json: JSONB
normalized_distribution_json: JSONB
confidence: NUMERIC
source: VARCHAR(20)         # 'manual' | 'learned' | 'ai' | 'default'
observation_count: INTEGER DEFAULT 0
evidence_weight: NUMERIC DEFAULT 0
posterior_mean: NUMERIC nullable         -- current Bayesian estimate of total_hours
posterior_precision: NUMERIC nullable    -- τ = 1/σ²; higher = more confident
sample_count: INTEGER DEFAULT 0          -- observations informing posterior
correction_count: INTEGER DEFAULT 0      -- times manually corrected
actuals_count: INTEGER DEFAULT 0         -- actual booking hours incorporated
actuals_median: NUMERIC nullable         -- rolling median of captured actuals
created_at: TIMESTAMP
updated_at: TIMESTAMP

UNIQUE(item_id, asset_type, duration_days, context_version, inference_version, context_hash)
```

## 16.4 Rules

- one cached profile per unique `(item, asset_type, duration, context_version, inference_version, context_hash)`
- cache entries are not deleted during normal operation
- lower-priority truth may be superseded by higher-priority truth
- for new non-default entries: initialize `observation_count = 1`, `evidence_weight = confidence`, `posterior_mean = total_hours`, `posterior_precision = 1 / (total_hours × (1 - confidence))²`, `sample_count = 1`
- for new default entries: initialize all evidence fields to zero or null
- on reuse of non-default entries: increment `observation_count` and `evidence_weight`; apply Bayesian update to `posterior_mean` and `posterior_precision` per Section 14.5
- on reuse of default entries: increment neither
- `total_hours` must be representable in the operational scheduling unit
- when `posterior_mean` exists and maturity tier is CONFIRMED or higher, use `posterior_mean` as operative `total_hours` for demand calculation (not the stored initial `total_hours`)

## 16.5 `item_knowledge_base` (global cache tier)

The global cache tier stores cross-project high-confidence profiles.

```text
id: UUID PK
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
duration_bucket: SMALLINT              -- bucketed duration (exact for 1-5 days; ranges for longer)
posterior_mean: NUMERIC                -- cross-project best estimate
posterior_precision: NUMERIC           -- cross-project precision
source_project_count: INTEGER          -- how many distinct projects contributed
sample_count: INTEGER                  -- total observations across all projects
correction_count: INTEGER DEFAULT 0    -- corrections from any project
normalized_shape_json: JSONB           -- consensus normalized distribution shape
confidence: VARCHAR(10)                -- 'high' | 'medium' (see promotion rules)
promoted_at: TIMESTAMP
last_updated_at: TIMESTAMP

UNIQUE(item_id, asset_type, duration_bucket)
```

### Duration bucketing rule

```text
1–5 days:   exact duration (bucket = duration_days)
6–10 days:  bucket = 7
11–20 days: bucket = 14
21+ days:   bucket = 28
```

### Promotion rules

A project-local cache entry is promoted to the global tier when **all** of the following hold:

- `sample_count >= 3`
- `correction_count == 0`
- `cv < 0.20` (posterior coefficient of variation is below TENTATIVE threshold)
- `source_project_count >= 1`
- `asset_type NOT IN ('other', 'none')`

Global entry is upgraded from `confidence='medium'` to `confidence='high'` when:

- `source_project_count >= 3`
- `sample_count >= 10`
- `correction_count == 0`
- `cv < 0.10` (TRUSTED_BASELINE tier)

### Asset-type presence guard

Before applying a global entry to a new project:

```text
SELECT 1 FROM assets
WHERE project_id = :target_project_id
AND canonical_type = :global_entry_asset_type
```

If no match: skip global lookup for that asset type. Do not apply demand knowledge for an asset type the project does not have.

### Global lookup behaviour by confidence tier

| Global confidence | Behaviour |
|---|---|
| `'medium'` | Pass `posterior_mean` as hint in AI work-profile prompt; still call AI; update local cache with result |
| `'high'` | Use `posterior_mean` directly; skip AI; seed local cache as CONFIRMED immediately |

### Global update on new project observations

When a local project-local profile is promoted or updated, also update the global entry:

```text
global.posterior_precision, global.posterior_mean = bayesian_update(
    global.posterior_precision, global.posterior_mean,
    local.posterior_mean, local.posterior_precision
)
global.source_project_count = max(existing, +1 for new project)
global.sample_count += local.sample_count
```

---

# 17. Classification Architecture

## 17.1 Purpose

Classification answers:

> **What project-managed asset type does this item require?**

This must be resolved before work-profile generation.

## 17.2 `item_classifications`

Persistent classification memory at the item level.

```text
id: UUID PK
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
confidence: VARCHAR(10)         # 'high' | 'medium' | 'low'
source: VARCHAR(20)             # 'ai' | 'keyword' | 'manual'
is_active: BOOLEAN DEFAULT TRUE
confirmation_count: INTEGER DEFAULT 0   -- times reused without correction
correction_count: INTEGER DEFAULT 0     -- times asset_type was changed
created_by_user_id: UUID nullable
created_at: TIMESTAMP
updated_at: TIMESTAMP
```

Partial unique index:

```sql
CREATE UNIQUE INDEX idx_item_classifications_active
  ON item_classifications (item_id)
  WHERE is_active = TRUE;
```

## 17.3 Classification maturity tiers

Classification is categorical, not continuous. The Normal-Normal Bayesian model used for work profiles does not apply. Instead, maturity is tracked via `confirmation_count` and `correction_count`.

| Tier | Condition | Behaviour |
|---|---|---|
| PERMANENT | `source = 'manual'` | Never re-query AI |
| STABLE | `confirmation_count >= 5`, `correction_count == 0` | Never re-query AI |
| CONFIRMED | `confirmation_count >= 2`, `correction_count == 0` | Use, no re-query |
| TENTATIVE | anything else | Re-run classification AI on next encounter |

On each upload where an item's classification is resolved from `item_classifications`:

```text
confirmation_count += 1
```

On each manual correction that changes `asset_type`:

```text
deactivate old active classification
insert new classification with confirmation_count = 0, correction_count = 0
also increment old entry's correction_count before deactivation for audit
```

On each AI re-query result (for TENTATIVE items):

```text
if ai_result.asset_type == existing.asset_type:
    confirmation_count += 1  (treated as confirmation)
else:
    insert new candidate with source='ai'
    do NOT auto-promote; flag for human review
```

This prevents silent asset-type drift while still catching items where the first AI call was wrong.

## 17.4 Cross-project classification memory

Classification memory is already cross-project by design.

`item_classifications` attaches to `items`, not to uploads or projects. When item abc-123 resolves in Project B via the same alias that was established in Project A, the STABLE or CONFIRMED classification from Project A is available immediately.

No separate global classification table is needed. The identity layer already handles this.

This is the key difference from work-profile caching:

- **Classification:** cross-project via identity layer (already)
- **Work profiles:** require explicit `item_knowledge_base` global tier (added in Stage 10)

## 17.5 Classification resolution is mandatory before work profile

Work-profile generation must not start until asset type is resolved.

### Resolution order

1. active `item_classifications`
2. keyword rules
3. standalone AI classification

## 17.6 Invariant: one active asset type per item in v1

For a given `item_id`:

```text
only ONE active asset_type exists in item_classifications
```

This is the v1 rule.

### Future caveat
Project-specific overrides may later supersede this, but they do not exist in the current architecture.

## 17.7 Invariant: work profile dependency

Work-profile generation must depend on:

```text
(item_id, asset_type, duration_days, context, context_version, inference_version)
```

## 17.8 Invariant: logical AI separation

Classification AI and Work Profile AI must be logically independent, even if implemented in the same service code.

Both passes have maturity tiers that control whether AI is called. But the maturity mechanism differs: classification uses `confirmation_count` / `correction_count` (categorical); work profiles use `posterior_mean` / `posterior_precision` (continuous).

## 17.9 Invariant: asset immutability during work profiling

Once classification is resolved for the current processing path:

```text
work-profile generation MUST NOT override asset_type
```

## 17.10 Relationship to aliases

Aliases determine item identity.
Classification memory attaches to the item, not the alias.

## 17.11 Relationship to work-profile cache

`item_context_profiles` answers:

> given this item, asset type, duration, and context, what is the reusable quantity+shape profile?

`item_classifications` answers:

> what asset type does this item require in v1?

## 17.12 Classification merge precedence

When merging items, classification precedence is:

```text
manual > high-confidence ai > keyword > low-confidence ai
```

Only one active classification survives per item.

Merge also reconciles `confirmation_count`: sum both entries' counts into the surviving classification.

## 17.13 `activity_asset_mappings` consistency rule

`activity_asset_mappings.asset_type` must equal:

- the resolved classification asset type, and
- `activity_work_profiles.asset_type`

for the same activity.

`activity_asset_mappings` is retained for backward compatibility and simpler queries.

---

# 18. Distribution-Based Demand Engine

This section replaces vague “skip non-work days” logic with fixed, site-realistic calendar semantics.

## 18.1 Process

For each activity with a work profile:

1. get distribution
2. apply `% complete`
3. convert `total_hours` into discrete scheduling units
4. apportion units across activity days using normalized distribution
5. map days to calendar dates using fixed weekly template and `work_days_per_week`
6. aggregate mapped daily units by ISO week and asset type
7. run anomaly checks on weekly buckets

## 18.2 Percentage complete handling

### v1
Apply remaining fraction uniformly before unit apportionment.

### future
Infer completed days more intelligently.

## 18.3 SQL warning

Use floating point semantics for percentage math.

## 18.4 Fixed Monday-based workweek semantics

`work_days_per_week` is a fixed Monday-based weekly template.

### V1 meaning

```text
1 = Monday only
2 = Monday–Tuesday
3 = Monday–Wednesday
4 = Monday–Thursday
5 = Monday–Friday
6 = Monday–Saturday
7 = Monday–Sunday
```

### Hard rules

- for a 6-day week, the 6th workday is **Saturday**
- for a 7-day week, the 7th workday is **Sunday**
- do not use a rolling “N days from start date” interpretation

## 18.5 Week-boundary spill rule

When mapping work across dates:

```text
Always exhaust the configured workdays remaining in the current ISO week
before spilling into the next week.
```

This means:

- a 5-day week skips Saturday and Sunday
- a 6-day week uses Saturday before moving to next Monday
- a 7-day week uses Sunday before moving to next Monday

### Important clarification

Activities may still spill into next week when they exceed the remaining configured workdays in the current week.

What is forbidden is:

```text
spilling into next week early
while valid workdays remain in the current week
```

## 18.6 Operational scheduling granularity

Calendar-mapped demand uses **discrete scheduling units**.

### V1 standard

```text
30-minute increments (0.5 hours)
```

### Future minimum supported precision

```text
15-minute increments (0.25 hours)
```

may be added later if needed.

## 18.7 Quantization boundary

Raw profile math may remain numeric internally during inference and validation.

But before calendar storage, lookahead, and alerting:

```text
total_hours must be quantized to the operational scheduling unit
```

In v1:

```text
total_hours must be a multiple of 0.5 hours
```

## 18.8 Hard invariant: exact total preservation

Calendar-mapped daily hours must:

- sum exactly to `final_total_hours`
- use only allowed scheduling increments
- be as close as possible to the raw normalized distribution

### Forbidden behavior

- independently rounding each day up/down
- arbitrarily “fixing” the last day to force totals
- recomputing weekly totals separately from mapped daily units

## 18.9 Deterministic unit apportionment

Calendar mapping must use deterministic unit apportionment.

### Rule

The system must:

1. quantize `final_total_hours` to the mapping unit first
2. convert total hours into integer scheduling units
3. allocate units across days using largest-remainder apportionment
4. preserve exact total units
5. minimize deviation from `normalized_distribution`
6. use deterministic tie-breaking

## 18.10 Largest-remainder apportionment method

Given:

- `final_total_hours`
- `unit_hours`
- `normalized_distribution`

### Step 1 — total units

```text
total_units = final_total_hours / unit_hours
```

This must be an integer.

### Step 2 — ideal units per day

```text
ideal_units[i] = normalized_distribution[i] * total_units
```

### Step 3 — base allocation

```text
base_units[i] = floor(ideal_units[i])
```

### Step 4 — remaining units

```text
remaining_units = total_units - sum(base_units)
```

### Step 5 — largest remainders

Assign remaining units to days with the largest fractional remainders:

```text
remainder[i] = ideal_units[i] - base_units[i]
```

### Step 6 — deterministic tie-break

If remainders tie:

```text
earlier day wins
```

### Step 7 — convert back to hours

```text
daily_hours[i] = allocated_units[i] * unit_hours
```

This guarantees:

- exact total preservation
- no arbitrary decimal daily values
- closest feasible match to the intended distribution

## 18.11 No arbitrary decimal hours in mapped demand

In v1, valid daily mapped values are:

```text
0.0
0.5
1.0
1.5
2.0
...
```

Invalid mapped daily values include:

```text
1.37
2.83
4.12
```

## 18.12 Weekly aggregation rule

Weekly lookahead demand must be the sum of already-quantized mapped daily units:

```text
weekly demand = sum(mapped daily unit-hours)
```

Do not recompute weekly demand from floating-point totals separately.

This keeps:

- daily mapping
- weekly lookahead
- bookings
- alert gaps

all aligned to the same operational scheduling granularity.

## 18.13 Per-day demand cap check

After calendar mapping, before aggregation, validate each mapped daily value:

```text
for each (date, asset_type, daily_hours) in mapped demand:
    if daily_hours > asset_types.max_hours_per_day(asset_type):
        → mark day as daily_cap_exceeded
        → flag parent lookahead_row anomalous
        → suppress external alerts for that bucket
        → log in structured output with date, asset_type, daily_hours, cap
```

This is the second enforcement point for per-day caps (see Section 10.9).

Per-week soft cap is not separately configured. It is derived at display/reporting time as:

```text
max_week_hours = asset_types.max_hours_per_day(asset_type) × work_days_per_week
```

If weekly aggregated demand exceeds this value, surface a warning in upload metrics. Do not treat it as a hard block — multiple asset instances can legitimately produce demand above a single-asset daily cap at the project level.

## 18.15 Demand anomaly detection

After weekly aggregation, run anomaly checks on each `(project, week_start, asset_type)` bucket.

If `demand_hours` exceeds a realistic upper bound based on available priors, historical range, or conservative configured defaults:

- mark the row anomalous
- store anomaly flags
- surface upload warnings
- suppress external alerts for that bucket until reviewed

This catches:

- parser duplication bugs
- bad AI quantity outputs
- context explosion side effects
- broken upstream schedule data

## 18.16 Concrete v1 anomaly thresholds

A weekly bucket is anomalous if either of the following rules is met.

### Rule A — history-based anomaly
If there are at least 4 prior non-anomalous weekly buckets for the same `(project_id, asset_type)`:

```text
anomalous if demand_hours >
  max(
    3.0 * historical_median,
    2.0 * historical_p95,
    historical_median + 40
  )
```

### Rule B — cold-start anomaly
If there is insufficient history:

```text
anomalous if demand_hours >
  3 * max_hours_per_day(asset_type) * work_days_per_week
```

Where `max_hours_per_day(asset_type)` is the same explicit cap used in the v1 hours policy.

This gives the anomaly system a concrete, explainable starting behavior.

---

# 19. Queryable Lookahead

## 19.1 `lookahead_rows`

```text
id: UUID PK
snapshot_id: UUID FK → lookahead_snapshots.id
project_id: UUID FK → site_projects.id
week_start: DATE
asset_type: VARCHAR(50) FK → asset_types.code
demand_hours: NUMERIC
booked_hours: NUMERIC
gap_hours: NUMERIC
is_anomalous: BOOLEAN DEFAULT FALSE
anomaly_flags_json: JSONB nullable
created_at: TIMESTAMP
```

## 19.2 Rule

`gap_hours = demand_hours - booked_hours`

Store signed value.

## 19.3 JSONB consistency invariant

`lookahead_rows` and `lookahead_snapshots.data` are generated from the same computation in the same transaction.

`lookahead_rows` is operational truth.

---

# 20. Parser Strategy

The parser remains the highest-risk subsystem.

## 20.1 PDF is a first-class required input

Programme PDF ingestion is mandatory.

The canonical regression fixture, ARC Bowden V36.1, is a PDF P6 export. Therefore the parser strategy must treat machine-readable construction PDFs as a first-class supported source, not an optional later enhancement.

## 20.2 Extraction strategy

- deterministic extraction first
- machine-readable PDF parsing is required
- CSV/XLSX/XLSM remain supported
- no AI in core parsing
- partial parse with flags is better than total failure

## 20.3 PDF parsing rule

For programme PDFs:

- prefer deterministic text/table extraction
- preserve row order and hierarchy signals
- suppress headers, footers, legends, and timeline fragments
- do not use AI to recover row structure in the core parser path

## 20.4 Row typing

Explicitly classify rows as:

- `summary`
- `task`
- `milestone`

## 20.5 Parser row confidence

Each parsed row should also receive:

```text
row_confidence = 'high' | 'medium' | 'low'
```

### Meaning

- `high` = row structure strongly matches expected task/summary/milestone pattern
- `medium` = row is usable but some fields required inference or cleanup
- `low` = row is retained with uncertainty and warning flags

## 20.6 `activity_kind`

`activity_kind` is authoritative when present.  
`is_summary` remains for backward compatibility only.

## 20.7 Default pipeline rule

Only `task` rows generate work profiles and demand.

## 20.8 Confidence handling rule

`row_confidence` does **not** numerically scale demand.

Instead it affects trust:

- low-confidence rows are retained
- resulting work profiles are forced or biased toward `low_confidence_flag = TRUE`
- downstream external alerts should not depend solely on low-confidence rows

This preserves quantity integrity while controlling trust.

## 20.9 Noise suppression

Must suppress:

- page legends
- repeated headers/footers
- Gantt fragments
- duplicate spill text
- revision footer text
- PDF page-title repetition

## 20.10 Day-prefix normalization

Strip `Day N -`, preserve semantic suffixes.

## 20.11 ARC Bowden fixture

Must verify:

- repeated cycle tasks deduplicate appropriately
- summary rows do not generate demand
- 100% complete rows stop generating demand
- 6-day week schedules map correctly with Saturday as the 6th day
- noise does not become activity rows
- milestones type correctly
- PDF page headers/footers/legends are suppressed
- repeated floor-cycle tasks converge to shared identity items

## 20.12 Acceptance criteria

- no more than ~5% uncertain rows without flags
- malformed rows retained with flags
- no silent corruption
- uploads may complete with warnings
- PDF P6 exports like ARC Bowden must be treated as operationally supported, not best-effort edge cases

---

# 21. Canonical Asset Taxonomy

## 21.1 Correction to prior design

A hardcoded canonical list alone is too rigid.

The system needs a **DB-backed asset taxonomy** with explicit governance and additive expansion.

## 21.2 `asset_types`

```text
code: VARCHAR(50) PK
display_name: TEXT
parent_code: VARCHAR(50) nullable FK → asset_types.code
is_active: BOOLEAN DEFAULT TRUE
is_user_selectable: BOOLEAN DEFAULT TRUE
max_hours_per_day: NUMERIC(4,1) NOT NULL   -- per-asset-instance daily cap
introduced_at: TIMESTAMP
retired_at: TIMESTAMP nullable
taxonomy_version: INTEGER DEFAULT 1
```

## 21.3 Seed set

Initial seeded codes:

- `crane`
- `hoist`
- `loading_bay`
- `ewp`
- `concrete_pump`
- `excavator`
- `forklift`
- `telehandler`
- `compactor`
- `other`
- `none`

## 21.4 Expansion policy

The taxonomy will expand. That is expected.

Likely additions include things like:

- `scaffolding`
- `formwork_system`
- `tower_light`
- `boom_lift`
- `tower_crane` if the taxonomy later becomes more specific

## 21.5 Governance

A new asset type may be added only when:

- repeated real demand exists
- `other` is being used as a bucket for it
- downstream workflows benefit from separation

Approval should be by an internal product/engineering owner, not ad hoc in random code paths.

## 21.6 Rules

- all `asset_type` values stored across the system must reference `asset_types.code`
- AI prompts must receive active canonical codes
- `other` is temporary and reviewable, not a permanent destination
- `none` means no managed project asset demand
- current runtime constants and validation layers must include `none`, not omit it

## 21.7 Handling `other`

Every use of `other` should be observable.

At minimum, surface:

- total `other` counts per upload
- repeated `other` classifications by item
- percentage of demand represented by `other`

## 21.8 `other` monitoring threshold

If:

```text
percentage of 'other' classifications > 10%
```

for an upload or project trend window, flag for taxonomy review.

This prevents taxonomy degradation.

## 21.9 When taxonomy expands

When a new type is introduced:

- existing rows remain valid
- no destructive migration is required
- future ingestions may classify into the new type
- optional backfill/reclassification can be run later
- old `other` classifications can be corrected lazily or via targeted jobs

---

# 22. Complete Proposed-Current Data Model

## 22.1 `items`

```text
id: UUID PK
display_name: TEXT
identity_status: VARCHAR(20) DEFAULT 'active'
merged_into_item_id: UUID nullable FK → items.id
created_at: TIMESTAMP
updated_at: TIMESTAMP
```

## 22.2 `item_aliases`

```text
id: UUID PK
item_id: UUID FK → items.id
alias_normalised_name: TEXT
normalizer_version: SMALLINT DEFAULT 1
alias_type: VARCHAR(20)         # 'exact' | 'variant' | 'manual'
confidence: VARCHAR(10)         # 'high' | 'medium' | 'low'
source: VARCHAR(20)             # 'parser' | 'manual' | 'reconciled'
created_at: TIMESTAMP
updated_at: TIMESTAMP

UNIQUE(alias_normalised_name, normalizer_version)
```

## 22.3 `item_identity_events`

```text
id: UUID PK
event_type: VARCHAR(20)         # 'merge' | 'alias_add'
source_item_id: UUID nullable FK → items.id
target_item_id: UUID nullable FK → items.id
details_json: JSONB
created_by_user_id: UUID nullable
created_at: TIMESTAMP
```

## 22.4 `programme_uploads` additive columns

```text
work_days_per_week: SMALLINT DEFAULT 5
```

## 22.5 `programme_activities` additive columns

```text
item_id: UUID nullable FK → items.id
pct_complete: SMALLINT nullable
activity_kind: VARCHAR(20) nullable    # 'summary' | 'task' | 'milestone'
row_confidence: VARCHAR(10) nullable   # 'high' | 'medium' | 'low'
```

## 22.6 `asset_types`

```text
code: VARCHAR(50) PK
display_name: TEXT
parent_code: VARCHAR(50) nullable FK → asset_types.code
is_active: BOOLEAN DEFAULT TRUE
is_user_selectable: BOOLEAN DEFAULT TRUE
max_hours_per_day: NUMERIC(4,1) NOT NULL   -- per-asset-instance daily cap
introduced_at: TIMESTAMP
retired_at: TIMESTAMP nullable
taxonomy_version: INTEGER DEFAULT 1
```

## 22.7 `inference_policies`

```text
version: SMALLINT PK
model_name: VARCHAR(100)
model_family: VARCHAR(50)
prompt_version: VARCHAR(50)
validation_rules_version: VARCHAR(50)
pattern_library_version: VARCHAR(50)
hours_policy_version: VARCHAR(50)
created_at: TIMESTAMP
```

### Rules

- rows are immutable once created
- exactly one version is active in normal operation at a time
- `inference_version` in profile/cache rows must correspond to one immutable policy row

## 22.8 `item_classifications`

```text
id: UUID PK
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
confidence: VARCHAR(10)          # 'high' | 'medium' | 'low'
source: VARCHAR(20)              # 'ai' | 'keyword' | 'manual'
is_active: BOOLEAN DEFAULT TRUE
confirmation_count: INTEGER DEFAULT 0   -- times reused without correction
correction_count: INTEGER DEFAULT 0     -- times asset_type was changed
created_by_user_id: UUID nullable
created_at: TIMESTAMP
updated_at: TIMESTAMP
```

## 22.9 `item_context_profiles`

```text
id: UUID PK
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
duration_days: SMALLINT
context_version: SMALLINT
inference_version: SMALLINT FK → inference_policies.version
context_hash: VARCHAR(64)
total_hours: NUMERIC
distribution_json: JSONB
normalized_distribution_json: JSONB
confidence: NUMERIC
source: VARCHAR(20)              # 'manual' | 'learned' | 'ai' | 'default'
observation_count: INTEGER DEFAULT 0
evidence_weight: NUMERIC DEFAULT 0
posterior_mean: NUMERIC nullable
posterior_precision: NUMERIC nullable
sample_count: INTEGER DEFAULT 0
correction_count: INTEGER DEFAULT 0
actuals_count: INTEGER DEFAULT 0
actuals_median: NUMERIC nullable
created_at: TIMESTAMP
updated_at: TIMESTAMP

UNIQUE(item_id, asset_type, duration_days, context_version, inference_version, context_hash)
```

## 22.10 `activity_work_profiles`

```text
id: UUID PK
activity_id: UUID FK → programme_activities.id
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
duration_days: SMALLINT
context_version: SMALLINT
inference_version: SMALLINT FK → inference_policies.version
total_hours: NUMERIC
distribution_json: JSONB
normalized_distribution_json: JSONB
confidence: NUMERIC
low_confidence_flag: BOOLEAN DEFAULT FALSE
source: VARCHAR(20)
context_hash: VARCHAR(64)
created_at: TIMESTAMP
```

## 22.11 `assets` additive column

```text
canonical_type: VARCHAR(50) nullable FK → asset_types.code
```

Retain:

```text
type: VARCHAR(100)
```

## 22.12 `subcontractor_asset_type_assignments`

```text
id: UUID PK
project_id: UUID FK → site_projects.id
subcontractor_id: UUID FK → subcontractors.id
asset_type: VARCHAR(50) FK → asset_types.code
is_active: BOOLEAN DEFAULT TRUE
created_at: TIMESTAMP

UNIQUE(project_id, subcontractor_id, asset_type)
```

## 22.13 `project_alert_policies`

```text
project_id: UUID PK FK → site_projects.id
mode: VARCHAR(20) DEFAULT 'observe_only'     # 'observe_only' | 'thresholded' | 'active'
external_enabled: BOOLEAN DEFAULT FALSE
min_demand_hours: NUMERIC DEFAULT 8
min_gap_hours: NUMERIC DEFAULT 8
min_gap_ratio: NUMERIC DEFAULT 0.25
min_lead_weeks: SMALLINT DEFAULT 1
max_alerts_per_subcontractor_per_week: SMALLINT DEFAULT 3
max_alerts_per_project_per_week: SMALLINT DEFAULT 20
updated_at: TIMESTAMP
```

### Trust-state interpretation

These modes map to trust state:

- `observe_only` = cold
- `thresholded` = warming
- `active` = trusted

A separate trust-state column is not required yet.

## 22.14 `lookahead_rows`

```text
id: UUID PK
snapshot_id: UUID FK → lookahead_snapshots.id
project_id: UUID FK → site_projects.id
week_start: DATE
asset_type: VARCHAR(50) FK → asset_types.code
demand_hours: NUMERIC
booked_hours: NUMERIC
gap_hours: NUMERIC
is_anomalous: BOOLEAN DEFAULT FALSE
anomaly_flags_json: JSONB nullable
created_at: TIMESTAMP
```

## 22.15 `notifications` additive changes

Preferred additive columns:

```text
project_id: UUID nullable FK → site_projects.id
week_start: DATE nullable
severity_score: NUMERIC nullable
```

### `activity_id`
Preferred: nullable.

### Dedupe model

Do **not** rely on an opaque hash as the primary uniqueness mechanism.

Use an explicit partial unique index for active lookahead notifications:

```sql
CREATE UNIQUE INDEX idx_notifications_active_lookahead
  ON notifications (project_id, subcontractor_id, asset_type, week_start)
  WHERE trigger_type = 'lookahead'
    AND status IN ('pending', 'sent');
```

Application rule:

- for `trigger_type='lookahead'`, `project_id`, `subcontractor_id`, `asset_type`, and `week_start` must all be present

## 22.16 `ai_suggestion_logs` additive columns

The table must be expanded to support full AI auditability as described in Section 13.13.

## 22.17 `item_knowledge_base`

```text
id: UUID PK
item_id: UUID FK → items.id
asset_type: VARCHAR(50) FK → asset_types.code
duration_bucket: SMALLINT
posterior_mean: NUMERIC
posterior_precision: NUMERIC
source_project_count: INTEGER DEFAULT 1
sample_count: INTEGER DEFAULT 0
correction_count: INTEGER DEFAULT 0
normalized_shape_json: JSONB
confidence: VARCHAR(10)      # 'medium' | 'high'
promoted_at: TIMESTAMP
last_updated_at: TIMESTAMP

UNIQUE(item_id, asset_type, duration_bucket)
```

## 22.19 Existing tables retained

- `stored_files`
- `programme_uploads`
- `programme_activities`
- `activity_asset_mappings`
- `ai_suggestion_logs`
- `lookahead_snapshots`
- `notifications`
- `slot_bookings`
- `assets`
- `subcontractors`

No tables are dropped.

---

# 23. Complete Ingestion Pipeline

```text
Upload received
  │
  ▼
Validate file
  │
  ▼
Acquire project-scoped processing guard
  │
  ▼
Create StoredFile
Create ProgrammeUpload(status='processing')
  │
  ▼
Background task starts
  │
  ▼
Parse file
  ├─ detect row type
  ├─ assign row_confidence
  ├─ capture hierarchy
  ├─ capture pct_complete
  ├─ capture dates / duration
  ├─ suppress noise
  └─ flag uncertain rows
  │
  ▼
Insert programme_activities
  │
  ▼
For each task row:
  ├─ normalize name
  ├─ resolve alias → item
  ├─ create new item + alias if needed
  ├─ follow merged item redirect if needed
  ├─ set programme_activities.item_id
  │
  ├─ resolve classification
  │   ├─ active item_classification?
  │   │   ├─ PERMANENT or STABLE → use, skip AI
  │   │   ├─ CONFIRMED → use, skip AI
  │   │   └─ TENTATIVE → use, but re-run AI as check
  │   │       ├─ AI agrees → confirmation_count += 1
  │   │       └─ AI disagrees → flag for review, do not auto-change
  │   ├─ keyword rules? → persist item_classification (TENTATIVE)
  │   └─ AI classification if still unknown
  │       └─ persist item_classification (TENTATIVE, confirmation_count = 0)
  │
  ├─ build context using resolved asset_type
  ├─ compute deterministic_context_key using
  │     item_id + asset_type + duration + context_version + inference_version
  │
  ├─ cache lookup (two-tier)
  │   ├─ Tier 1: project-local item_context_profiles
  │   │   ├─ HIT →
  │   │   │   ├─ check profile maturity tier (TENTATIVE/CONFIRMED/TRUSTED_BASELINE/MANUAL)
  │   │   │   ├─ TRUSTED_BASELINE or MANUAL → reuse posterior_mean, skip AI
  │   │   │   ├─ CONFIRMED → call AI with posterior as hint, apply Bayesian update
  │   │   │   └─ TENTATIVE → call AI fresh, apply Bayesian update
  │   │   └─ MISS →
  │   │       ├─ Tier 2: item_knowledge_base (global)
  │   │       │   ├─ asset_type present in project? (guard check)
  │   │       │   ├─ confidence='high' → seed local cache, skip AI
  │   │       │   ├─ confidence='medium' → pass posterior_mean as hint, call AI
  │   │       │   └─ no global match → continue to full AI call
  │   │       ├─ if context cap exceeded or degraded mode:
  │   │       │    follow deterministic reduced-context fallback
  │   │       └─ AI work-profile candidate (full call)
  │
  ▼
Process AI work-profile batch
  ├─ send structured prompt with asset_type provided
  ├─ validate AI proposal structure
  ├─ finalize total_hours via system hours policy
  ├─ quantize final_total_hours to operational unit
  ├─ derive raw distribution from finalized total_hours × normalized_distribution
  ├─ validate final profile
  ├─ retry invalid once
  ├─ fallback if still invalid
  ├─ write rich AI audit logs
  └─ track cost
  │
  ▼
For each resolved profile:
  ├─ force low_confidence_flag if parser row_confidence was low
  ├─ write activity_work_profiles
  ├─ write activity_asset_mappings
  ├─ write / update item_context_profiles
  └─ ensure cache asset_type matches resolved classification
  │
  ▼
Demand engine
  ├─ apply pct_complete
  ├─ convert hours to scheduling units
  ├─ apportion units across days deterministically
  ├─ map to fixed weekly work template
  ├─ validate each mapped daily value against max_hours_per_day
  │   └─ flag anomalous if exceeded, suppress external alerts for that bucket
  ├─ aggregate by project/week/asset_type
  ├─ run anomaly detection on weekly buckets
  ├─ write lookahead_snapshot JSONB
  └─ write lookahead_rows
  │
  ▼
Alert signal derivation
  ├─ fetch project_alert_policies
  ├─ apply confidence policy
  ├─ suppress anomalous buckets
  ├─ evaluate alert thresholds
  ├─ resolve subcontractor recipients
  ├─ compute severity_score
  ├─ enforce per-subcontractor weekly cap
  ├─ enforce per-project weekly cap
  ├─ insert notifications
  │    using active composite uniqueness
  └─ skip or suppress external alerts if project still in observe_only mode
  │
  ▼
Cancel stale unresolved lookahead alerts from older uploads
  │
  ▼
Mark upload:
  ├─ committed
  ├─ completed_with_warnings
  └─ failed
  │
  ▼
Backward-compat status read:
  └─ treat existing historical 'degraded' as equivalent to 'completed_with_warnings'
  │
  ▼
Release project guard
```

---

# 24. Notification Architecture

## 24.1 What the notification means

> "Predicted demand for [asset type] is upcoming in week of [date]. Book now before availability tightens."

## 24.2 Base trigger rule

A weekly asset bucket becomes an **alert candidate** only if:

```text
gap_hours > 0
```

## 24.3 Cold-start and anti-spam policy

Base trigger alone is not enough.

Without controls, early projects with sparse booking data will generate alerts for nearly every demand bucket.

Therefore, external subcontractor alerts require **all** of the following:

1. `gap_hours > 0`
2. `demand_hours >= min_demand_hours`
3. either:
   - `gap_hours >= min_gap_hours`, or
   - `gap_hours / demand_hours >= min_gap_ratio`
4. `week_start` is at least `min_lead_weeks` ahead where applicable
5. project alert policy is not `observe_only`
6. `external_enabled = TRUE`
7. the candidate is not driven solely by low-confidence demand
8. the candidate bucket is not anomalous

## 24.4 Alert modes

### `observe_only`
Default for new projects.

- compute lookahead normally
- show demand gaps internally
- do **not** send subcontractor notifications

### `thresholded`
Send only when thresholds are exceeded.

### `active`
Normal operation, still subject to configured thresholds.

## 24.5 Trust-state semantics

Alert-policy mode also represents project trust state:

- `observe_only` = cold
- `thresholded` = warming
- `active` = trusted

This avoids introducing a redundant trust-state column.

## 24.6 Onboarding trust policy

New projects must begin in `observe_only`.

Operational expectation for the early uploads:

- high correction rate is normal
- alias merging will be needed
- taxonomy refinement will be needed
- routing configuration will likely still be incomplete

Promotion from `observe_only` should only occur after human review of:

- parser quality
- identity quality
- classification quality
- routing completeness
- alert relevance

The system should earn external alerting trust rather than assume it.

## 24.7 Routing

1. `subcontractor_asset_type_assignments`
2. heuristic trade fallback only if explicit routing is missing

## 24.8 Dedupe / uniqueness

Use explicit active uniqueness:

- `project_id`
- `subcontractor_id`
- `asset_type`
- `week_start`

for unresolved lookahead notifications.

This is easier to inspect than hashes.

## 24.9 Alert severity

A candidate alert must have a deterministic severity ranking for:

- rate limiting
- UI prioritization
- future learning and evaluation

### Severity score

```text
severity_score =
  (w1 * gap_hours) +
  (w2 * gap_ratio) +
  (w3 * demand_hours)
```

Where:

- `gap_ratio = gap_hours / demand_hours`
- `w1`, `w2`, `w3` are configurable weights

The precise defaults are an implementation detail, but the existence of a stored deterministic severity score is required.

## 24.10 Alert rate limiting

Each project has:

```text
max_alerts_per_subcontractor_per_week
max_alerts_per_project_per_week
```

### Application order

1. rank candidate alerts by `severity_score`
2. enforce per-subcontractor weekly cap
3. enforce project-wide weekly cap across the remaining candidates

This prevents both recipient-level flooding and project-level spam.

## 24.11 Lifecycle

- `pending`
- `sent`
- `acted`
- `cancelled`
- `failed`

## 24.12 Delivery

At launch: email.

DB-backed worker is sufficient.

---

# 25. Re-upload Lifecycle

## 25.1 On new upload version

1. create new upload
2. parse new activities
3. resolve aliases/items
4. resolve classifications
5. reuse work-profile cache where keys match
6. generate new work profiles and lookahead
7. cancel unresolved old lookahead notifications
8. preserve prior uploads and history

## 25.2 Historical policy

Keep:

- old uploads
- old activities
- old work profiles
- old mappings
- old snapshots
- old notifications

Do not rewrite old uploads destructively.

## 25.3 Stale alert policy

Minimum safe rule:

- cancel all unresolved `pending`/`sent` lookahead notifications for that project when a new upload is committed

This remains acceptable for the first production version.

---

# 26. Correction Propagation Architecture

## 26.1 Goal

Manual corrections must feed back into persistent memory.

## 26.2 Flow

### Step 1: update occurrence-level rows

Update:

- `activity_asset_mappings`
- `activity_work_profiles`

Set manual provenance.

### Step 2: update AI audit log

Mark original AI suggestion as corrected.

### Step 3: update item-level classification memory

If classification changed:

- deactivate old active classification
- insert new active classification

### Step 4: update cache entry

Update or create `item_context_profiles` for that exact `(item_id, asset_type, duration, context)`.

### Step 5: enforce override priority

Set cache `source='manual'` so future lower-priority AI/default results cannot overwrite it.

## 26.3 Transactional requirement

Occurrence updates, classification memory, and cache updates should happen in one transaction.

## 26.4 Lazy propagation rule

Do not mass-update every historical occurrence immediately.

Future uploads and reprocesses should benefit from corrected memory automatically.

---

# 27. Identity Reconciliation Operations

## 27.1 Current scope

Implement now:

- manual item merge
- alias addition

Do **not** implement now:

- split operations
- historical compaction jobs
- alias reassignment edge-case tooling between active items

## 27.2 Why this matters

Normalization improvements, phrase variants, and human review will create cases where:

- two items should merge
- aliases should be added
- historical references lag behind current identity truth

This is normal, not exceptional.

## 27.3 Operational posture

- be conservative at ingestion time
- allow duplicates initially rather than risky false merges
- provide auditable manual merge tools
- resolve canonical active item at runtime

## 27.4 Merge safety rules

- manual merges only in v1
- no bulk auto-merge from fuzzy matching
- all merges produce `item_identity_events`
- all runtime lookups resolve through canonical active item
- merged classification memory must be reconciled immediately
- merged cache entries must be reconciled immediately using cache source priority and same-tier conflict rules

## 27.5 Historical row policy after merge

Do not repoint all historical rows immediately.

Instead:

- keep old historical references as-is
- ensure new processing resolves to the active survivor item
- defer historical cleanup until clearly needed

## 27.6 Future improvement

Later, the system may support:

- split operations
- merge suggestions using similarity
- background historical compaction

These are deferred, not required now.

---

# 28. Concurrency Guard

Only one upload per project may process at a time.

## Recommended

Use PostgreSQL advisory lock.

Fallback:

- status guard on `programme_uploads`

Without this, uploads can race and corrupt planning state.

---

# 29. Partial Failure Strategy

## Principle

**Partial success is preferable to total failure.**

## Parser

- retain parseable rows
- flag bad ones
- do not silently drop

## AI

- keep cache hits even if AI partially fails
- keep classification memory even if work-profile generation later fails
- keep default patterns if budget exceeded
- log failures richly
- do not roll back whole upload unnecessarily

## Upload status model

### Current status values in code
- `processing`
- `committed`
- `degraded`

### Proposed-current target model
- `processing`
- `committed`
- `completed_with_warnings`
- `failed`

### Migration rule
Treat existing historical `degraded` as semantically equivalent to `completed_with_warnings` unless and until explicit failure splitting is introduced.

---

# 30. System Degraded Mode

## 30.1 Purpose

The system needs an explicit safe mode for cases where AI or inference quality is unavailable, too expensive, or clearly degraded.

## 30.2 Triggers

Safe degraded mode may be entered when any of the following occurs:

- AI provider unavailable
- upload budget guard reached
- severe validation failure rate in current batch
- `max_new_contexts_per_upload` exceeded materially
- operator/admin forces degraded mode

## 30.3 Behavior

In degraded mode:

- do not make new AI calls unless an operator explicitly overrides
- use existing classification memory if available
- use keyword rules if available
- use deterministic reduced-context fallback where safe
- use default normalized pattern library for unresolved work profiles
- mark affected activities with warnings / low-confidence flags
- continue internal lookahead generation where possible
- disable or suppress external subcontractor alerts
- surface degraded-mode status in logs and Sentry

## 30.4 System health state

The runtime system should expose a unified operational health state:

- `healthy`
- `degraded`
- `recovery`

### Meaning

- `healthy` = normal operation
- `degraded` = safe reduced-function mode, external alerting suppressed
- `recovery` = system stabilizing after degraded mode; internal processing restored, external alerts remain suppressed until health checks pass

A dedicated DB table is not required yet. This can be surfaced via configuration, health endpoint, and observability.

## 30.5 Exit rules

Transition rules must be explicit:

### `degraded → recovery`
Only when:

- degraded trigger has cleared, and
- at least one successful inference-capable batch or one successful full upload completes without re-triggering degraded mode

### `recovery → healthy`
Only when:

- one additional full upload completes without degraded triggers, and
- validation failure rates are below threshold for that run

External alerts remain suppressed during `recovery`.

## 30.6 Goal

The goal of degraded mode is:

- preserve internal operability
- avoid silent corruption
- avoid bad external notifications
- fail safely rather than fail hard

---

# 31. Observability Requirements

For a 1–2 engineer team, observability must be cheap, high-signal, and immediate.

## 31.1 Mandatory tooling

### Sentry
Required for:

- FastAPI request exceptions
- background upload job exceptions
- parser failures
- AI validation spikes
- notification delivery failures
- merge-operation errors

### Structured logs
Still required for detailed flow tracing.

### Database audit
Required for AI calls and identity events.

## 31.2 Current Sentry reality

Sentry is already initialized for the FastAPI app.

What must be added is:

- background task exception capture
- nightly/scheduled job exception capture
- upload/project/stage tagging
- AI-specific contexts and tags
- system health state tagging

## 31.3 Sentry requirements

Sentry events must include tags/contexts for:

- `project_id`
- `upload_id`
- `stage`
- `activity_id` where available
- `item_id` where available
- `context_hash` where available
- `context_version` where available
- `inference_version` where available
- `model_name` for AI stages
- `system_health_state`

Background tasks must report exceptions to Sentry, not just write stdout logs.

## 31.4 Must log / surface

- upload processing time by stage
- parser counts: parsed / flagged / suppressed
- parser row confidence distribution
- AI calls per upload by stage:
  - classification
  - work_profile
- classification source breakdown:
  - item memory
  - keyword
  - AI
- work-profile cache hit rate
- AI cost per upload
- work profile source breakdown
- item reuse rate
- `other` asset-type rate
- low-confidence profile rate
- anomalous lookahead row rate
- warning count per upload
- failed notification deliveries
- total demand hours per upload
- context cache growth
- identity merge event counts
- degraded-mode activations
- project-level and recipient-level alert suppression counts

## 31.5 AI observability

For every AI-driven decision, retain:

- prompt version
- model
- request context
- output
- validation result
- retry count
- fallback used
- latency
- token usage
- cost
- eventual human correction outcome
- whether hours were clamped before finalization

This is essential for later measuring real accuracy instead of imagined accuracy.

## 31.6 Principle

The goal is not “perfectly error free.”  
The goal is:

- no silent corruption
- rapid detection
- easy triage
- explainable decisions
- recoverable failures

---

# 32. API Surface Direction

Likely endpoints:

```text
POST /uploads
GET  /projects/{project_id}/lookahead
GET  /projects/{project_id}/lookahead?asset_type=crane&week_start=...
GET  /subcontractors/{sub_id}/notifications
POST /activities/{activity_id}/corrections
POST /bookings
GET  /items?search=...
GET  /items/{item_id}/work-profiles
POST /items/merge
GET  /projects/{project_id}/subcontractor-asset-assignments
POST /projects/{project_id}/subcontractor-asset-assignments
DELETE /projects/{project_id}/subcontractor-asset-assignments/{id}
GET  /projects/{project_id}/alert-policy
POST /projects/{project_id}/alert-policy
GET  /asset-types
POST /asset-types              # internal/admin only
GET  /system/health
```

---

# 33. Auth / Access Direction

## Project managers / internal users

Can:

- upload programmes
- view lookahead
- manage routing
- manage alert policy
- correct classifications/work profiles
- merge items
- manage taxonomy
- manage assets
- view all project notifications and bookings

## Subcontractors

Can:

- view only their own notifications
- create bookings/requests
- view their own booking state

Cannot:

- manage routing
- manage taxonomy
- merge items
- view unrelated project data

---

# 34. Proposed-Future Architecture

The proposed-current architecture supports the following additively.

## 34.1 Identity candidate suggestions

Future system may suggest possible merges using:

- token similarity
- alias overlap
- shared classification
- shared profile behavior
- repeated manual corrections

Suggestions only. No auto-merge.

## 34.2 Feature learning

Learn which hierarchy tokens affect hours/distribution.

## 34.3 Feature confidence weighting

When the feature system exists, use:

```text
effective_weight = learned_weight * confidence
```

This prevents low-sample noise from dominating behavior.

## 34.4 Delay feature computation until data exists

### Now
- collect data only

### Later
- compute feature weights once sufficient validated observations exist

Do not attempt feature learning before enough real data exists.

## 34.5 Adaptive context expansion

Promote previously ignored hierarchy signals when they repeatedly explain divergence.

## 34.6 Actuals-driven total-hours learning

The long-term system must learn from predicted vs actual usage.

### Future data shape

Add a future concept such as:

```text
asset_usage_actuals
- id: UUID PK
- project_id: UUID FK
- booking_id: UUID nullable FK
- asset_type: VARCHAR(50)
- week_start: DATE
- actual_hours_used: NUMERIC
- created_at: TIMESTAMP
```

### Future rule

When actuals arrive, apply Bayesian update to `item_context_profiles.posterior_mean` with `obs_precision = 1 / (0.05 × actual)²`.

Actuals carry ~16× more weight than AI estimates. A single actual observation typically moves a TENTATIVE profile to TRUSTED_BASELINE in one step.

This is the path from AI-proposed quantity to system-learned quantity.

## 34.13 Global knowledge base maturity

As the global tier accumulates data across projects, AI becomes a fallback for novel situations only:

```text
Project 1,  Upload 1: $3.50 (no global data)
Project 1,  Upload 5: $0.40 (local cache handles ~90%)
Project 5,  Upload 1: $1.20 (global covers ~65% of items)
Project 10, Upload 1: $0.30 (global covers ~90% of items)
Project 20, Upload 1: $0.05 (global covers ~98% of items)
```

At maturity, AI is reserved for:

- items never seen in any project (genuinely novel)
- contexts that differ materially from all cached entries
- CONFIRMED entries re-queried due to high correction rate

Shape consensus (normalized distribution) also stabilizes globally over time, eventually making AI's shape contribution redundant for well-known items.

## 34.14 Similarity-based alias suggestions (enhancement)

Later, the identity system may suggest possible merges using token similarity scoring across item display names and aliases. This is a UI workflow — the system proposes, a human approves. No auto-merge.

## 34.7 Project-specific classification overrides

Future architecture may add project-scoped classification overrides when the same item requires a different asset type on a particular project.

This does not change the current rule that classification is still resolved before work-profile generation.

## 34.8 Multi-pass AI

Use critique/refine only for low-confidence cases.

## 34.9 Fine-tuning / ML

Only after enough validated profiles exist.

## 34.10 RAG

Retrieve similar cached profiles, classification examples, and taxonomy examples before AI generation.

## 34.11 Smarter asset matching

Use richer asset attributes and item requirements.

## 34.12 Finer operational granularity

If later operational need justifies it, move from:

```text
30-minute units
```

to:

```text
15-minute units
```

This must be a versioned hours-policy change, not an ad hoc runtime toggle.

---

# 35. System Evolution

## Stage 1 — Present

```text
Rules + formula estimation + weak memory
```

## Stage 2 — Proposed current

```text
Identity-aware ingestion
Alias-based item resolution
Persistent classification memory
Standalone classification AI only when needed
Work-profile cache with versioning
Bayesian evidence accumulation (posterior_mean, posterior_precision)
Profile maturity tiers (TENTATIVE → CONFIRMED → TRUSTED_BASELINE)
Per-day distribution bucket cap enforcement
Inference-policy versioning
Bounded AI quantity control
Distribution demand
Per-day demand cap check in demand engine
Queryable lookahead
Thresholded alerting
Explicit routing
Deterministic after cold start
```

## Stage 3 — Learning system

```text
Global knowledge base (item_knowledge_base)
Cross-project posterior sharing
Asset-type presence guard on global lookup
Actuals-informed Bayesian updates
Item stats
Lower AI usage — decreasing with each project
```

## Stage 4 — Feature-driven system

```text
Learned hierarchy effects
Feature confidence weighting
Actuals-informed shape learning
Less AI
More deterministic
```

## Stage 5 — Mature system

```text
AI only for genuinely novel items and novel contexts
Global knowledge base covers ~98% of common work
Taxonomy stabilized
Identity largely reconciled
Near-zero-cost inference on common work
```

---

# 36. Build Stage Plan

## Stage 0 — Tests + observability foundation

Add targeted tests for:

- parser
- normalization
- identity alias resolution
- classification resolution
- demand calculation
- alert policy thresholds
- confidence-policy behavior
- anomaly suppression behavior
- deterministic reduced-context fallback behavior
- fixed workweek mapping behavior
- exact unit apportionment behavior

Also add:

- enrich existing Sentry integration
- background task exception capture
- upload/job tags in Sentry
- AI audit log enrichment

## Stage 1 — Parser hardening + correctness columns

Add:

- PDF programme upload support
- `work_days_per_week`
- `pct_complete`
- `activity_kind`
- `row_confidence`
- row typing
- noise suppression
- import flags

## Stage 2 — Identity layer

Add:

- `items`
- `item_aliases`
- `item_identity_events`
- `programme_activities.item_id`
- normalization
- alias resolution
- canonical item redirect logic
- manual merge flow

Do **not** add split operations or historical compaction now.

## Stage 3 — Asset taxonomy foundation

Add:

- `asset_types` with `max_hours_per_day` column
- `assets.canonical_type`
- seed initial taxonomy including `none` with concrete `max_hours_per_day` values
- constrain AI and code paths to taxonomy
- surface `max_hours_per_day` in hours-bounds clamping and distribution validation

## Stage 4 — Classification layer

Add:

- `item_classifications`
- active-classification invariant
- classification resolution order
- keyword rules
- standalone AI classification
- classification audit logging
- `activity_asset_mappings` consistency rule

## Stage 5 — Work profile infrastructure

Add:

- `inference_policies`
- `item_context_profiles` with Bayesian columns (`posterior_mean`, `posterior_precision`, `sample_count`, `correction_count`, `actuals_count`, `actuals_median`)
- `activity_work_profiles`
- `context_version`
- `inference_version`
- context builder
- deterministic context key including asset type
- fixed compressed-context schema + bounded extension fields
- deterministic reduced-context fallback order
- two-tier cache lookup (project-local then global)
- profile maturity tier evaluation (TENTATIVE / CONFIRMED / TRUSTED_BASELINE / MANUAL)
- Bayesian posterior update on each cache encounter
- cache override rules
- confidence-weighted evidence
- cache invalidation triggers
- correction rate trigger (>20% → re-evaluate)
- work-profile AI generation with asset type provided
- per-day distribution bucket cap validation (Stage B)
- total-hours finalization policy
- concrete hours-bounds clamping (sourced from `asset_types.max_hours_per_day`)
- operational total-hours unit normalization
- pattern library
- AI cost tracking
- rich AI logging

## Stage 6 — Distribution demand + queryable lookahead

Add:

- distribution-to-calendar mapping
- fixed Monday-based workweek semantics
- exact unit apportionment
- pct_complete reduction
- per-day demand cap check in demand engine (second enforcement point)
- weekly aggregation
- concrete anomaly detection thresholds
- `lookahead_rows`

## Stage 7 — Notification architecture + alert policy

Add:

- `project_alert_policies`
- `subcontractor_asset_type_assignments`
- confidence-aware external alert suppression
- anomaly-aware external alert suppression
- thresholded lookahead-driven alerts
- severity scoring
- per-subcontractor rate limiting
- per-project rate limiting
- active composite uniqueness on notifications
- resolve `activity_id` nullability
- cancel stale alerts
- email worker

## Stage 8 — Corrections + identity maintenance

Add:

- correction endpoint
- transactional propagation
- manual merge admin endpoint
- cache overwrite on manual truth

## Stage 9 — Re-upload refinement

Add:

- refined stale-alert cancellation
- active-version semantics
- historical reporting improvements
- upload status split from historical `degraded` to explicit warning/failure semantics

## Stage 10 — Learning loop + global knowledge base

Add:

- `item_knowledge_base` table
- promotion logic: project-local → global (multi-upload, multi-project, zero corrections)
- global cache lookup in cache-miss path
- global confidence tier evaluation (medium/high → different AI behaviour)
- asset-type presence guard on global lookup
- global posterior update when local projects contribute new observations
- item statistics
- Bayesian posterior update using actuals (`actuals_count`, `actuals_median`)
- `other` review reporting
- actuals capture foundation (`asset_usage_actuals`)

## Stage 11 — Feature learning

Add:

- hierarchy feature effects
- adaptive context expansion
- confidence weighting for learned features
- actuals-informed total-hours learning

## Stage 12 — Requirements / smarter matching

Add richer asset and item requirements.

## Stage 13 — Fine-tuning / RAG / ML

Only if earned.

---

# 37. Stage Plan Table

| Stage | What | Why now | Risk | Rewrite risk later? |
|---|---|---|---|---|
| 0 | Tests + enrich existing Sentry + AI audit logs | Prevent blind failures | Low | Prevents breakage |
| 1 | PDF parser + correctness columns | Data quality foundation | Medium | No rewrite |
| 2 | Identity + aliases + manual merge | Required for real memory | Medium | No rewrite |
| 3 | Asset taxonomy registry + `max_hours_per_day` | Prevent drift, `other` collapse, daily cap enforcement | Low | No rewrite |
| 4 | Classification layer | Stable asset memory before work profiling | Medium | No rewrite |
| 5 | Work profile infra + Bayesian evidence + context/inference versioning | Core product shift, cache self-improves | High | Prevents formula rewrite later |
| 6 | Demand engine + per-day cap check + anomaly checks + lookahead_rows | Correct demand timing and catch spikes | Medium | No rewrite |
| 7 | Notifications + alert policy + rate limits | Business value without spam | Medium | No rewrite |
| 8 | Corrections + cache override + correction rate trigger | Feedback loop completion | Medium | No rewrite |
| 9 | Re-upload lifecycle refinement | Production safety | Medium | No rewrite |
| 10 | Learning loop + global knowledge base + actuals Bayesian updates | Compound cross-project learning, near-zero AI cost trajectory | Medium | No rewrite |
| 11 | Feature learning | Reduce AI dependency | Medium | No rewrite |
| 12 | Requirements / matching | Operational depth | Medium | No rewrite |
| 13 | Fine-tuning / RAG / ML | Optimization | Medium | No rewrite |

---

# 38. Priority Order

## Highest priority

1. tests
2. enrich existing Sentry + background job reporting
3. parser hardening
4. PDF programme ingestion support
5. `work_days_per_week`, `pct_complete`, `activity_kind`, `row_confidence`
6. identity layer with aliases
7. asset taxonomy table
8. classification layer with persistent `item_classifications`
9. deterministic context key with `context_version` + immutable `inference_version`
10. deterministic reduced-context fallback order
11. cache override/invalidation rules
12. confidence-weighted cache evidence
13. bounded total-hours finalization policy
14. concrete hours-bounds clamping
15. operational total-hours unit normalization
16. work-profile infrastructure
17. fixed Monday-based calendar mapping
18. exact unit apportionment
19. anomaly detection with explicit thresholds
20. `lookahead_rows`
21. confidence policy
22. alert policy + explicit routing
23. notification write path with active composite dedupe
24. alert severity and rate limiting
25. safe degraded mode

## Next

26. correction propagation
27. manual item merge operations

## Later

28. global knowledge base + actuals Bayesian updates
29. learning loop
30. feature learning
31. actuals-informed shape learning
32. richer requirements
33. fine-tuning / RAG / ML

---

# 39. Key Decisions Summary

## Keep

- monolith
- current activity hierarchy model
- `activity_asset_mappings`
- JSONB snapshot for compatibility/debug
- in-batch dedup as a local optimization

## Add now

- `items`
- `item_aliases`
- `item_identity_events`
- alias-based identity resolution
- manual merge operations
- merged-item redirect logic
- `asset_types` with `max_hours_per_day`
- `assets.canonical_type`
- `item_classifications`
- standalone classification resolution before work profiling
- `inference_policies`
- work profiles
- normalized distribution storage
- context cache with Bayesian columns (`posterior_mean`, `posterior_precision`, `sample_count`, `correction_count`, `actuals_count`, `actuals_median`)
- profile maturity tiers and Bayesian update logic
- correction rate trigger (>20% → re-evaluate)
- per-day distribution bucket cap validation (Stage B, sourced from `asset_types.max_hours_per_day`)
- `context_version`
- `inference_version`
- deterministic context key including asset type
- deterministic reduced-context fallback order
- two-tier cache lookup (project-local then global)
- cache override rules
- confidence-weighted cache evidence
- cache invalidation triggers
- bounded total-hours finalization (sourced from `asset_types.max_hours_per_day`)
- concrete hours-bounds clamping
- operational total-hours unit normalization
- fixed Monday-based calendar semantics
- exact unit apportionment
- per-day demand cap check in demand engine
- distribution demand engine
- anomaly detection with explicit thresholds
- `lookahead_rows`
- `project_alert_policies`
- explicit routing
- alert severity
- alert rate limiting
- active composite unique dedupe on notifications
- confidence-driven external alert suppression
- anomaly-driven external alert suppression
- enrich existing Sentry wiring
- rich AI audit logs
- safe degraded mode

## Add next

- correction propagation

## Add next (after corrections)

- global knowledge base (`item_knowledge_base`)
- actuals Bayesian updates

## Defer

- split operations
- historical compaction
- similarity-based merge suggestions
- actuals-informed shape learning
- feature learning
- item requirements
- richer asset attributes
- ML/fine-tuning/RAG
- project-specific classification overrides
- 15-minute operational granularity

## Reject for now

- opaque hash-only notification dedupe as primary mechanism
- aggressive auto-merge of semantically similar item names
- work-profile AI inferring asset type in the normal path
- shipping external alerts without threshold/ramp-up controls
- letting `other` grow without governance
- numerically discounting demand based on parser uncertainty rather than controlling trust/alerting
- independently rounding per-day mapped hours

---

# 40. Risks and Red Flags

| Risk | Severity | Mitigation |
|---|---|---|
| AI validation fails frequently | Critical | pattern priors, retry once, fallback, audit logs |
| AI cost exceeds $5/upload | Critical | cache, batching, budget guard |
| Parser misreads summary rows as tasks | High | row typing tests + fixture |
| Footer/legend pollution | High | parser cleanup |
| PDF parsing remains unsupported | High | Stage 1 PDF parser implementation |
| Work week ignored | High | explicit column + fixed Monday-based template |
| 6-day week skips Saturday incorrectly | High | fixed workweek semantics |
| pct_complete ignored | High | store and apply |
| Identity duplicates proliferate | High | aliases + manual merges + review |
| False item merges | High | conservative auto-resolution, manual merge only |
| Normalizer change causes identity drift | High | aliases store version, no silent rewrites, manual merge |
| `other` becomes dumping ground | High | asset taxonomy governance + observability + 10% threshold |
| `none` omitted from runtime classification | High | include `none` in taxonomy and prompt constraints |
| Alert spam on cold start | High | observe_only default + thresholds + rate limits |
| Notification duplicates | High | composite partial unique index |
| `notifications.activity_id` blocks lookahead alerts | High | make nullable |
| Stale alerts after re-upload | High | cancel unresolved on new commit |
| Silent background job failures | High | background Sentry capture + structured logs |
| AI decisions not explainable later | High | rich AI audit logs |
| Concurrent uploads race | High | advisory lock |
| Cache key too specific | High | compressed context only + context cap |
| Cache key too broad | High | bounded extension fields + future adaptive expansion |
| Cache drift after context logic changes | High | `context_version` |
| Cache drift after prompt/model changes | High | immutable `inference_version` |
| Manual corrections fail to override stale AI | High | source-priority cache rules |
| `none` asset generates ghost demand | High | strict zero-hours rule |
| Default fallback pollutes cache | High | no evidence increments + supersede on first real signal |
| Low-confidence demand causes bad external alerts | High | confidence-action policy |
| AI quantity proposal is too large/small | High | bounded total-hours finalization |
| Parser low-confidence rows drive false alerts | High | row confidence → low_confidence_flag |
| Demand spike from duplication or bad inference | High | weekly anomaly detection + alert suppression |
| Reduced-context reuse becomes inconsistent | High | deterministic fallback order |
| Classification drifts across uploads | High | item-level classification memory + standalone classification stage |
| Work-profile cache reused across wrong asset type | High | asset_type included in deterministic context key |
| Per-day mapped hours drift from total | High | integer-unit apportionment |
| Floating-point daily demand mismatches bookings | High | 30-minute unit mapping |
| Current `degraded` status hides failure semantics | Medium | explicit target status model + migration |
| No safe behavior during AI outage | High | degraded mode |
| Too many alerts across a project | Medium | project-wide alert cap |
| Distribution bucket exceeds daily possible hours | High | per-day bucket cap in Stage B validation and demand engine |
| Single-day demand appears as 12+ hours in lookahead | High | `max_hours_per_day` from `asset_types` enforced at two points |
| First AI answer permanently wrong (cache first-answer problem) | High | Bayesian evidence accumulation; TENTATIVE profiles continue calling AI |
| Cache quietly wrong for months without correction signal | High | correction rate trigger; CONFIRMED re-evaluates if correction_count/sample_count > 0.20 |
| New project always pays full AI cost | Medium | global knowledge base seeds new projects at Stage 10 |
| Global knowledge base applied to wrong project type | Medium | asset-type presence guard required before global lookup |
| Global entry promoted with bad data from one unusual project | Medium | require `source_project_count >= 2` for HIGH confidence; medium confidence only informs AI |
| First AI classification wrong, sticks silently | High | classification maturity tiers; TENTATIVE items re-query AI on each encounter |
| AI re-query disagrees with stored classification silently | High | disagreement never auto-changes; always flags for human review |
| confirmation_count never increments (code omission) | Medium | test: classification reuse must increment confirmation_count |

---

# 41. Design Principles

## 1. Determinism
Same deterministic context key → same output.

## 2. AI is not the system
Cached truth is the system.

## 3. Classification first, work profile second
Stable asset memory must be resolved before quantity/shape inference.

## 4. Work is shape, not rate
Distribution is the atomic unit.

## 5. Quantity must be bounded by system policy
AI proposals do not become total-hours truth without finalization and bounds checks.

## 6. Cache is everything
It reduces cost, variance, and latency.

## 7. Identity is conservative first, reconcilable later
Prefer temporary duplicates over dangerous false merges.

## 8. Hierarchy is unbounded
Compress known semantics; preserve raw unknowns.

## 9. Calendar semantics must match site reality
5-day, 6-day, and 7-day schedules must map exactly as sites operate.

## 10. Exact totals beat naive rounding
Mapped daily demand must preserve the exact quantized total and stay as close as possible to the intended distribution.

## 11. Partial success beats total failure
Flag, continue, and surface warnings.

## 12. Observability is mandatory
For a tiny team, every silent error is expensive.

## 13. Debuggability beats cleverness
Composite keys and explicit tables are better than opaque elegance when on-call reality matters.

## 14. Manual truth outranks machine truth
Corrections must be able to override cached AI decisions.

## 15. Confidence must change behavior
Uncertain outputs must be treated differently from trusted ones.

## 16. Safe degraded mode is part of the architecture
The system must fail safely, not optimistically.

## 17. Additive evolution
Future capabilities attach to today’s foundation.

## 18. Both AI passes have a first-answer problem; both need maturity tiers
Classification and work-profile generation both suffer from the same root issue: the first answer sticks. The mechanisms differ — classification uses categorical confirmation counting, work profiles use Bayesian posterior updating — but both passes must accumulate evidence and control re-query behaviour based on that evidence.

## 19. Per-day caps belong in the taxonomy, not in hardcoded logic
`max_hours_per_day` is an asset-type property. It lives in the database alongside the taxonomy, not in conditionals scattered through the codebase.

## 20. Cross-project learning is an architectural property, not an afterthought
Project lifecycle is 5–8 uploads. The system should compound learning across projects from the start. Global knowledge structures must be planned before the first project ships, even if populated only from project 3 onwards.

---

# 42. Architecture in Three States

## Present

```text
Per-upload parsing + per-occurrence classification + formula demand + weak alerting
```

## Proposed current

```text
Identity-aware ingestion
Alias resolution
Manual merge support
Persistent classification memory
Standalone classification AI when needed
Work-profile cache with Bayesian evidence accumulation
Profile maturity tiers (TENTATIVE → CONFIRMED → TRUSTED_BASELINE)
Per-day distribution bucket cap (asset_types.max_hours_per_day)
Context versioning
Inference-policy versioning
Bounded AI quantity control
Two-tier cache lookup (project-local then global)
Deterministic reduced-context fallback
Fixed weekly calendar semantics
Per-day demand cap check in demand engine
Exact unit apportionment
Distribution demand
Queryable lookahead
Thresholded external alerts
Confidence-aware suppression
Anomaly-aware suppression
Explicit routing
Alert severity + rate limiting
Rich observability
Safe degraded mode
```

## Proposed future

```text
Global knowledge base (cross-project posterior sharing)
Feature learning (hierarchy effects on hours)
Actuals-informed Bayesian updates (collapses uncertainty fast)
Actuals-informed shape learning
Near-zero AI cost for known work by project 20
Similarity-based merge suggestions
Smarter asset matching
Near-deterministic system
```

---

# 43. Parser Rewrite Caveat

The no-rewrite commitment applies to:

- data model foundation
- identity/context/profile pipeline
- classification/work-profile separation
- demand/alert architecture
- observability and audit strategy

It does **not** apply to parser internals. Parser internals will evolve materially. That is expected.

---

# 44. Final Summary

## What the system is now

A working ingestion/classification pipeline with basic planning state, but without durable identity, realistic demand timing, safe notification policy, or deep observability.

## What it becomes next

A deterministic, identity-aware system where:

- activities resolve to persistent items through aliases
- items can be manually merged safely when identity truth improves
- classification is resolved first and persisted as reusable memory
- work-profile generation depends on known asset type rather than rediscovering it repeatedly
- each item+asset_type+context resolves to a reusable work profile
- context extraction is versioned so cache semantics remain safe
- inference policy is versioned so prompt/model changes do not silently corrupt determinism
- total hours are finalized by system rules, not blindly trusted from AI
- total hours are clamped by explicit v1 bounds when no trusted baseline exists
- total hours are normalized to the operational scheduling unit
- demand is derived from distributions over time
- calendar mapping respects real 5/6/7-day site weeks
- mapped daily demand preserves exact totals through deterministic unit apportionment
- lookahead is queryable
- anomalous weekly demand spikes are flagged before they become external noise
- alerts are weekly, forecast-driven, thresholded, confidence-aware, anomaly-aware, rate-limited, and debuggable
- subcontractor routing is explicit
- AI is fully auditable
- Sentry catches failures before they become mysteries
- taxonomy evolves cleanly without code drift
- `none` is available for non-asset tasks instead of polluting `other`
- manual corrections override stale AI outputs
- degraded mode preserves safe internal operation when inference is unavailable or unreliable
- individual distribution buckets are capped at `asset_types.max_hours_per_day` at generation time and again in the demand engine
- the work-profile cache improves itself with each observation via Bayesian posterior updating
- profile maturity tiers control whether AI is called again, used as a hint, or skipped
- cross-project global knowledge compounds learning so new projects benefit from prior projects' confirmed profiles

## Why this avoids rewrites

Because:

- identity is upgraded now to support real-world textual variance
- classification becomes durable memory before work-profile reuse depends on it
- notification dedupe is aligned with business semantics
- cold-start alert spam is handled before launch
- taxonomy growth is planned structurally
- observability is built in from the start
- cache versioning and override rules are defined before scale
- prompt/model drift is bounded by inference versioning
- AI quantity control is bounded before external trust depends on it
- anomaly thresholds are explicit from the start
- calendar semantics are fixed before operational lookahead depends on them
- PDF ingestion is treated as required, not deferred fantasy
- future learning attaches additively to the same pipeline

---

# 45. Final Checklist

## Immediately

- [x] Add parser, normalization, identity, classification, demand, and alert-policy tests
- [ ] Add ARC Bowden PDF regression fixture
- [x] Enrich existing Sentry integration in FastAPI and background jobs
- [x] Tag Sentry events with project/upload/stage context
- [x] Expand `ai_suggestion_logs` for full AI auditability
- [x] Harden parser row typing and noise suppression
- [x] Add PDF support to programme upload validation and parsing
- [x] Add `programme_uploads.work_days_per_week`
- [x] Add `programme_activities.pct_complete`
- [x] Add `programme_activities.activity_kind`
- [x] Add `programme_activities.row_confidence`
- [x] Add `items`
- [x] Add `item_aliases`
- [x] Add `item_identity_events`
- [x] Add `programme_activities.item_id`
- [x] Implement normalization + alias resolution
- [x] Implement active-item redirect logic for merged items
- [ ] Add `asset_types`
- [ ] Add `assets.canonical_type`
- [ ] Seed initial taxonomy including `none`
- [ ] Add `item_classifications`
- [ ] Implement classification resolution order
- [ ] Implement standalone AI classification
- [ ] Add `inference_policies`
- [ ] Add `item_context_profiles`
- [ ] Add `activity_work_profiles`
- [ ] Add `context_version`
- [ ] Add `inference_version`
- [ ] Implement context builder + fixed compressed-context schema
- [ ] Implement bounded extension fields in compressed context
- [ ] Implement deterministic context key including asset type
- [ ] Implement deterministic reduced-context fallback order
- [ ] Implement cache override rules
- [ ] Implement confidence-weighted cache evidence
- [ ] Implement cache invalidation triggers
- [ ] Implement work-profile AI generation with asset type provided
- [ ] Implement total-hours finalization policy
- [ ] Implement concrete hours-bounds clamping
- [ ] Implement operational total-hours unit normalization
- [ ] Implement cold-start normalized pattern library
- [ ] Implement cache lookup/reuse pipeline
- [ ] Implement normalized distribution storage
- [ ] Implement low-confidence flagging
- [ ] Implement confidence-aware alert suppression
- [x] Implement fixed Monday-based workweek mapping
- [ ] Implement exact unit apportionment for mapped daily demand
- [ ] Implement distribution-based demand engine
- [x] Implement anomaly detection on weekly demand with explicit thresholds
- [ ] Add `lookahead_rows`
- [ ] Add `project_alert_policies`
- [ ] Add `subcontractor_asset_type_assignments`
- [x] Resolve `notifications.activity_id` nullability
- [ ] Add `severity_score` to notifications
- [ ] Add composite partial unique index for active lookahead notifications
- [ ] Implement thresholded lookahead-driven notification write path
- [ ] Implement alert severity scoring
- [ ] Implement alert rate limiting per subcontractor/week
- [ ] Implement project-wide alert cap
- [ ] Implement stale-alert cancellation on new upload commit
- [ ] Add project-scoped processing guard
- [x] Add AI cost tracking per upload
- [ ] Add reporting on `other` asset-type usage
- [ ] Add threshold alert when `other` exceeds 10%
- [x] Implement safe degraded mode behavior
- [ ] Surface system health state
- [ ] Introduce explicit upload-status migration plan from current `degraded` semantics
- [ ] Add `max_hours_per_day` to `asset_types` table with seed values
- [ ] Implement per-day distribution bucket cap in work-profile Stage B validation
- [ ] Implement per-day demand cap check in demand engine (second enforcement point)
- [ ] Add `confirmation_count` and `correction_count` to `item_classifications`
- [ ] Implement classification maturity tier evaluation (TENTATIVE / CONFIRMED / STABLE / PERMANENT)
- [ ] Implement re-query logic for TENTATIVE classifications; flag disagreement for review, never auto-change
- [ ] Increment `confirmation_count` on every classification reuse
- [ ] Add Bayesian columns to `item_context_profiles` (`posterior_mean`, `posterior_precision`, `sample_count`, `correction_count`, `actuals_count`, `actuals_median`)
- [ ] Implement Bayesian posterior update on each work-profile cache encounter
- [ ] Implement work-profile maturity tier evaluation (TENTATIVE / CONFIRMED / TRUSTED_BASELINE / MANUAL)
- [ ] Implement correction rate trigger (correction_count / sample_count > 0.20 → re-evaluate work profile)

## Next

- [ ] Implement correction propagation flow
- [x] Implement manual merge operations for items

## Later

- [ ] Add `item_knowledge_base` table (global cross-project cache tier)
- [ ] Implement project-local → global promotion rules
- [ ] Implement global cache lookup with asset-type presence guard
- [ ] Implement actuals Bayesian updates (`actuals_count`, `actuals_median`)
- [ ] Add similarity-based merge suggestions
- [ ] Refine stale-alert cancellation by exact signal scope
- [ ] Add item stats / learning loop
- [ ] Add actual-hours capture foundation (`asset_usage_actuals`)
- [ ] Add hierarchy feature learning
- [ ] Add confidence weighting for learned features
- [ ] Add actuals-informed shape learning
- [ ] Add item requirements
- [ ] Evaluate fine-tuning / RAG / ML only after baseline matures

---

# 46. Final Architecture Verdict

**The correct next implementation target is still the distribution-based, identity-aware, context-cached architecture — with these critical revisions made explicit:**

1. **identity uses aliases plus manual merge operations, not just unique normalized names**
2. **classification is resolved first and reused as stable memory**
3. **work-profile generation depends on known asset type and does not infer classification in the normal path**
4. **notification dedupe uses explicit composite uniqueness, not opaque hashes**
5. **external alerting is gated by project alert policy, thresholds, confidence rules, anomaly suppression, and rate limits to avoid cold-start spam**
6. **asset typing is governed by a DB-backed taxonomy, with mandatory observability on `other` and explicit support for `none`**
7. **Sentry and rich AI audit logging are mandatory, not optional**
8. **cache behavior is versioned, invalidation-triggered, and governed by clear source priority rules**
9. **determinism is protected by both `context_version` and immutable `inference_version`**
10. **manual truth permanently outranks machine truth**
11. **AI quantity proposals are bounded by concrete system rules before they affect alerts**
12. **context fallback order is explicit and deterministic**
13. **anomaly detection has concrete starting thresholds**
14. **calendar mapping uses fixed weekly templates and exact unit apportionment**
15. **PDF programme ingestion is a required first-class capability**
16. **safe degraded mode is part of the architecture, not an afterthought**
17. **per-day distribution bucket caps are enforced at two points:** Stage B validation (generation time) and the demand engine (mapping time). `max_hours_per_day` is stored in `asset_types`, not hardcoded
18. **both AI passes must accumulate evidence and improve over time.** Classification uses categorical confirmation counting (`confirmation_count`, `correction_count`). Work profiles use Bayesian posterior updating (`posterior_mean`, `posterior_precision`). Both have maturity tiers that control AI re-query behaviour. Neither should treat the first answer as permanent truth
19. **cross-project learning is a first-class architectural property.** The `item_knowledge_base` global tier compounds confidence across projects. New projects benefit from prior projects' confirmed profiles. AI cost decreases monotonically as the system matures

**Build this. In this order. With these safeguards.**