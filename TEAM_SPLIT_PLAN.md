# Team Split Plan — Lookahead Asset Planning Feature
## Backend Dev (BE) + AI Dev (AI) · Parallel Workstreams

> **How to read this doc**
> - **[BE]** = Backend Dev leads, AI Dev can assist
> - **[AI]** = AI Dev leads, Backend Dev can assist
> - **[BOTH]** = Must be done together / agree first
> - Ownership is not a wall — it's a default. Cross over freely.

---

## 1. One-Page Ownership Summary

| Stream | What it is | Lead | Support | Days est. |
|---|---|---|---|---|
| **DB Models + Migrations** | New tables, Alembic | BE | — | 1 |
| **Config + Env Vars** | AI keys, scheduler, feature flags | BOTH | — | 0.5 |
| **AI Service Layer** | `ai_service.py`, prompts, provider | AI | — | 2 |
| **Programme Upload Route** | `POST /api/programmes/upload`, 202 flow | BE | AI (test) | 1.5 |
| **Background Orchestrator** | Pipeline runner, status polling | BE | AI (AI calls) | 1.5 |
| **Fallback Chain** | Regex date/column detection, graceful degrades | AI | BE (DB writes) | 1 |
| **Asset Classification** | AI classifier + keyword boost | AI | — | 1.5 |
| **Learning Loop** | `ai_suggestion_logs`, few-shot injection | AI | BE (schema) | 1.5 |
| **Header Hash Cache** | Skip AI on repeat uploads | BE | AI (aware of it) | 0.5 |
| **Lookahead Engine** | Demand calc, anomaly detection, nightly job | BE | — | 3 |
| **APScheduler Setup** | Startup/shutdown hooks in `main.py` | BE | — | 0.5 |
| **Stream 3 — Sub Scope** | Existing sub management extension | BE | — | 1.5 |
| **Stream 5 — Notifications** | Nightly alerts, suppress logic, email templates | BE | — | 3 |
| **Stream 6 — Book Now Link** | Query params, source column, status update | BE | — | 1 |
| **Stream 7 — Frontend** | PM dashboard, sub view, TV heatmap | AI (structure) | BE (API contract) | 4–5 |

---

## 2. The Interface Contract (Agree Day 1)

This is the most important section. Both people need to agree on this **before writing any code**. It defines where AI Dev's output ends and BE Dev's storage begins.

### 2.1 AI Structure Detection Output (Step 1)

AI Dev's `detect_structure()` must return this exact shape:

```json
{
  "column_mapping": {
    "name": "Task Name",
    "start_date": "Start",
    "end_date": "Finish",
    "duration": "Duration",
    "wbs_code": "WBS",
    "resource": "Resource Names",
    "level_indicator": "Outline Level"
  },
  "completeness_score": 94,
  "missing_fields": ["zone_name"],
  "notes": "P6 export detected. Outline Level column used for hierarchy."
}
```

BE Dev's `programme_activities` table is built around this contract. If `column_mapping` changes shape, both sides need updating — agree once, freeze it.

### 2.2 AI Classification Output (Step 2)

AI Dev's `classify_assets()` must return this exact shape per batch:

```json
{
  "classifications": [
    {
      "activity_id": "uuid-string",
      "asset_type": "crane",
      "confidence": "high",
      "source": "ai",
      "reasoning": "Steel erection keyword + structural phase context"
    },
    {
      "activity_id": "uuid-string",
      "asset_type": "hoist",
      "confidence": "medium",
      "source": "keyword_boost",
      "reasoning": null
    }
  ],
  "skipped": ["uuid-of-low-confidence-item"],
  "batch_tokens_used": 1240
}
```

BE Dev writes these directly into `activity_asset_mappings`. The `asset_type` values must match a validated set — BE Dev defines the allowed set, AI Dev conforms to it.

### 2.3 Allowed `asset_type` Values

Agree and lock this list. BE Dev validates on write. AI Dev uses only these values in prompts:

```text
crane | hoist | loading_bay | ewp | concrete_pump
excavator | forklift | telehandler | compactor | other
```

---

## 3. Stream-by-Stream Breakdown

---

### Stream 1 — Programme Upload (BE leads, AI assists on AI calls)

**BE Dev owns:**
- `programme_uploads` table + Alembic migration
  - id, project_id, uploaded_by, file_name, file_id (FK → stored_files), column_mapping (JSONB), version_number, completeness_score, status, created_at
- `programme_activities` table + Alembic migration
  - id, programme_upload_id, parent_id (self-ref, deferred FK), name, start_date, end_date, duration_days, level_name, zone_name, is_summary, wbs_code, sort_order
- `POST /api/programmes/upload` route — receive file, save via existing `StorageBackend`, create `programme_uploads` record, return **202 immediately**
- `GET /api/programmes/{upload_id}/status` — polling endpoint
- Background task runner (`process_programme.py`) — orchestrates the full pipeline, writes results to DB, handles all DB writes from AI output
- Header hash cache — SHA-256 of file headers, skip AI if same hash seen before
- Auth: project-scoped via existing `has_project_access` check

**AI Dev owns:**
- `app/services/ai_service.py` — `detect_structure(rows: list[dict]) -> StructureResult`
  - Takes first 50–100 rows as JSON
  - System prompt explains P6/MS Project
  - Returns `column_mapping` + completeness_score
  - Hard 8s timeout via `httpx` async
  - Validates output against agreed JSON schema before returning
- `app/services/prompts/structure_detection.txt` — versioned prompt file (not hardcoded)
- The fallback chain logic (called by BE's orchestrator when AI fails):
  - Regex date detection across all columns
  - First string column with most unique values as name fallback
  - Flatten orphaned children, tag `unstructured`
- Validate with real Bowden CSV during dev — ensure `completeness_score ≥ 90` on known file

**Together:**
- Agree on the `column_mapping` JSON shape (Section 2.1) before either writes code
- BE Dev writes the orchestrator stub first (calls a mock `detect_structure`), AI Dev replaces mock with real implementation

---

### Stream 2 — Asset Classification (AI leads, BE assists on schema)

**AI Dev owns:**
- `app/services/ai_service.py` — `classify_assets(activities: list[dict]) -> ClassificationResult`
  - Batch 100 activities per call
  - Parallel calls for 500+ activities
  - Keyword boost layer (pure Python, no AI call needed for obvious matches)
  - Confidence tier assignment: `high` = AI + keyword agree, `medium` = AI confident alone, `low` = uncertain
  - Hard 3s timeout per batch
  - `ai_suggestion_logs` table writes — log every suggestion + outcome for learning loop
- `app/services/prompts/asset_classification.txt` — versioned prompt, 3–5 few-shot from Bowden data
- Learning loop — after 50+ corrections per builder, inject corrected examples as few-shot
- Auto-commit logic — high + medium classifications auto-written, low skipped (not a queue for PM to clear)

**BE Dev owns:**
- `activity_asset_mappings` table + Alembic migration
  - id, programme_activity_id, asset_type (validated against allowed set), confidence, source (ai/keyword/manual), is_confirmed, confirmed_by, confirmed_at, subcontractor_id (nullable)
- `ai_suggestion_logs` table + Alembic migration
  - id, activity_id, suggested_asset_type, confidence, accepted (bool), correction (string, nullable), created_at
- `GET /api/programmes/{upload_id}/mappings` — returns classified activities with confidence badges
- `PATCH /api/programmes/mappings/{mapping_id}` — PM inline correction endpoint
- Validation: on write to `activity_asset_mappings`, enforce `asset_type` is in allowed set (return 422 otherwise)

**Together:**
- Agree on allowed `asset_type` values (Section 2.3) before AI Dev writes prompts
- BE Dev exposes a `/mappings` endpoint that AI Dev can call manually to verify classification output looks right in context

---

### Stream 3 — Subcontractor Scope Assignment (BE only)

This stream is ~80% existing code. AI Dev does not need to touch this.

**BE Dev owns (all of it):**
- Add `subcontractor_id` (nullable FK) to `activity_asset_mappings`
- `GET /api/programmes/{upload_id}/activities?subcontractor_id={sub_id}` — filtered view for subs
- Wire into existing `POST /{project_id}/subcontractors` and sub management
- Verify existing `trade_specialty` column on `subcontractors` table is populated (it's a plain String, not enum — add validation if needed)
- Auth: sub-facing endpoint uses `get_current_subcontractor` dependency (verify this works here)

**AI Dev:** Nothing needed here. Move straight to Stream 2 classification work in parallel.

---

### Stream 4 — Lookahead Engine + Anomaly Detection (BE only)

Fully deterministic. No AI. BE Dev owns this entirely.

**BE Dev owns:**
- `app/services/lookahead_engine.py`
  - Join `programme_activities` + `activity_asset_mappings` + `slot_bookings`
  - Compute booked hours from `end_time - start_time` per `booking_date` row (not DateTime ranges — explicit in the guide)
  - Bucket by week, by asset_type
  - Output per week: `{asset_type, demand_hours, booked_hours, demand_level, gap_hours}`
  - Demand thresholds: Low <8h, Med 8–20h, High 20–40h, Critical 40+h/wk
  - Anomaly detection: compare new snapshot vs previous — flag if demand doubles, 40%+ classifications changed, activity count shifts 30%+. Write to `anomaly_flags` JSONB on `lookahead_snapshots`
- `lookahead_snapshots` table + Alembic migration
  - id, project_id, programme_upload_id, snapshot_date, data (JSONB), anomaly_flags (JSONB)
- `GET /api/lookahead/{project_id}` — returns latest snapshot + anomaly flags
- `GET /api/lookahead/{project_id}/history` — previous snapshots
- APScheduler nightly job — add startup/shutdown lifecycle hooks in `app/main.py`. Note: Railway is stateless — validate APScheduler survives redeploys or consider Railway cron as an alternative
- Timezone: bucket by project timezone (store TZ on `site_projects` or default ACST)

**AI Dev:** Can review the anomaly thresholds and suggest adjustments based on Bowden data patterns, but does not write this code.

---

### Stream 5 — Notifications (BE leads, AI Dev reviews templates)

**BE Dev owns:**
- `notifications` table + Alembic migration
  - id, sub_id, activity_id, asset_type, trigger_type (6wk/3wk/1wk), status (pending/sent/acted), sent_at, acted_at, booking_id (nullable)
- Nightly APScheduler job — query lookahead for gaps, check suppression, send if due
- Suppression logic: if `booked_hours >= demand_hours` for that asset type in that week → skip
- Wire into existing `app/core/email.py` (Mailtrap) — three new templates: 6wk, 3wk, 1wk
- `GET /api/notifications/my` — sub-facing, uses `get_current_subcontractor` auth
- `PATCH /api/notifications/{id}/dismiss` — sub dismisses notification

**AI Dev can assist:**
- Draft the 3 email template copy (6wk/3wk/1wk) — these are plain text/HTML templates, AI Dev can write the copy since they have the most context on what the AI has classified
- Review final templates before BE Dev wires them into Mailtrap

---

### Stream 6 — Book Now Deep Link (BE only)

Small extension. ~1 day.

**BE Dev owns:**
- Add nullable `source` column to `slot_bookings` (Alembic migration)
- Ensure `POST /api/bookings/` accepts `source=lookahead` query param / body field
- Verify `POST /api/bookings/` works with `get_current_subcontractor` JWT — this is flagged as a caveat in the guide, verify it explicitly
- On successful booking: update `notifications` row `status → acted`, set `acted_at` and `booking_id`
- Frontend receives pre-filled form via `?asset_type=crane&date_from=...&date_to=...&source=lookahead`

---

### Stream 7 — Frontend (AI Dev structures, BE Dev owns API contract)

**AI Dev leads:**
- PM Dashboard — upload widget, progress bar (polling `/status`), completeness banner, heatmap (weeks × assets, colour-coded), collapsible mapping panel (two tabs: Unclassified / Auto-classified)
- Sub View — filtered activity list, notification feed, Book Now buttons, calendar. **Must work on mobile.**
- TV View — read-only heatmap (existing `tv` role via `TvReadOnlyMiddleware`, no changes needed to auth)
- Recharts / CSS grid for heatmap. No Gantt.

**BE Dev assists:**
- Define exact API response shapes for all `/api/programmes/`, `/api/lookahead/`, `/api/notifications/` endpoints — AI Dev builds FE against these contracts
- If API shape needs to change based on FE feedback, BE Dev makes the change

**Recommended approach:** BE Dev writes the API contract as TypeScript type stubs or a short OpenAPI snippet first, AI Dev builds FE against that. Prevents FE work needing to be redone when API shape crystallises.

---

## 4. Week 1 Schedule (Side by Side)

| Day | BE Dev | AI Dev |
|---|---|---|
| **Mon** | Add `ANTHROPIC_API_KEY`, `AI_PROVIDER`, `AI_MODEL`, `AI_ENABLED`, `AI_TIMEOUT_*` to `config.py`. Install `openpyxl`, `apscheduler`, `anthropic`. Write Alembic migration for `programme_uploads` + `programme_activities`. | Install `anthropic`. Build `ai_service.py` skeleton with `detect_structure()` stub. Write structure detection prompt (`prompts/structure_detection.txt`). Test against Bowden CSV in a standalone script. |
| **Tue** | `POST /api/programmes/upload` — file receive, StorageBackend save, 202 return, background task stub. `GET /status` endpoint. | Iterate structure detection prompt. Validate output JSON schema. Implement fallback chain (regex date, name column heuristic). Wire `detect_structure` into BE's background task stub. |
| **Wed** | Alembic migration for `activity_asset_mappings` + `ai_suggestion_logs`. `GET /api/programmes/{id}/mappings`. `PATCH /mappings/{id}` correction endpoint. Validate `asset_type` on write. | Build `classify_assets()`. Write classification prompt (`prompts/asset_classification.txt`) with 3–5 Bowden few-shot examples. Keyword boost layer. Auto-commit high + medium, skip low. |
| **Thu** | APScheduler lifecycle hooks in `main.py`. `lookahead_snapshots` + `notifications` tables migration. Lookahead engine skeleton (joins, demand calc). | Mapping review panel in PM Dashboard (FE). Two tabs: Unclassified / Auto-classified with confidence badges. Wire to `GET /mappings`. |
| **Fri** | **Joint E2E test**: Upload Bowden CSV → BE orchestrator runs → AI classifies → mappings in DB. Fix anything broken. Verify sub auth works on `/api/lookahead`. | **Joint E2E test**: same session. Validate completeness score, classification confidence distribution. Demo mapping panel. Start Book Now UI on Sub View. |

---

## 5. Collaboration Checkpoints

These are moments where both people must sync — don't skip them.

| When | What to align on |
|---|---|
| **Day 1 AM** | Lock the interface contract (Sections 2.1, 2.2, 2.3). Write it down. Don't start coding until both agree. |
| **Day 2 EOD** | AI Dev shows BE Dev the structure detection output on real Bowden CSV. BE Dev confirms it maps cleanly to DB schema. |
| **Day 3 AM** | Lock the allowed `asset_type` values. AI Dev updates prompts. BE Dev adds validation. |
| **Day 3 EOD** | AI Dev shows first batch of classifications. BE Dev confirms they can be written to `activity_asset_mappings` without schema errors. |
| **Day 5** | Joint E2E run. Full pipeline upload → classify → lookahead → notification. Both present. |
| **Weekly** | Review AI accuracy on real uploads. AI Dev adjusts prompts/few-shot. BE Dev reviews any schema changes needed. |

---

## 6. Caveats and Shared Responsibilities

These are flagged in the guide as needing attention. Assign before starting:

| Caveat | Who handles it |
|---|---|
| `column_mapping` must use JSONB (PostgreSQL dialect), not generic JSON | BE Dev — in model definition |
| `parent_id` on `programme_activities` needs deferred FK constraint | BE Dev — in Alembic migration |
| `assets.type` is free-text in production — validate against allowed set before lookahead joins | BE Dev — validation step in lookahead engine |
| Compute booked hours from `end_time - start_time` per `booking_date`, not DateTime ranges | BE Dev — explicit in lookahead engine |
| APScheduler on Railway — verify it survives redeploys | BE Dev — test in staging |
| `get_current_subcontractor` auth on `/api/bookings/` — verify it works | BE Dev — integration test |
| Timezone handling — bucket by project TZ, not server TZ | BE Dev — in lookahead engine |
| AI timeout on every call — 8s structure, 3s classify. Never block the request | AI Dev — httpx async with timeout |
| `AI_ENABLED=false` must fully disable all AI calls (feature flag) | AI Dev — guard at `ai_service.py` entry |
| Never commit `AI_API_KEY` | Both — `.env` only, check `.gitignore` |

---

## 7. What Each Person Needs from the Other

**What BE Dev needs from AI Dev:**
- The locked JSON shape for `detect_structure()` output (Day 1)
- The locked JSON shape for `classify_assets()` output (Day 1)
- Confirmation that the fallback chain is implemented and tested (Day 2)
- Notification that AI service is ready to be called from background orchestrator (Day 2–3)
- AI accuracy metrics after first real upload (Day 5+)

**What AI Dev needs from BE Dev:**
- `programme_activities` DB schema so AI output maps cleanly (Day 1)
- `activity_asset_mappings` DB schema so classifications can be written (Day 2)
- The allowed `asset_type` values to use in prompts (Day 3)
- A running `/api/programmes/upload` endpoint to test against (Day 2)
- API response shapes for all new endpoints to build FE against (Day 3)

---

## 8. Do Not Cross Without Talking

These are areas where uncoordinated changes will break the other person's work:

- Changing `column_mapping` JSON shape — affects both DB write (BE) and prompt output (AI)
- Changing the allowed `asset_type` values — affects both DB validation (BE) and prompt instructions (AI)
- Changing the background orchestrator call signature to `ai_service.py` — affects both sides
- Adding new tables that reference `programme_activities` or `activity_asset_mappings` — discuss schema first

---

*Document version: 1.0 — Mapped to Lookahead_Asset_Planning_Dev_Guide.docx + Lookahead_System_Flow.html*
