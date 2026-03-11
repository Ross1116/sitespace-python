# PMP AI Integration Plan — SiteSpace Asset Booking Intelligence

## Overview

This document is a step-by-step implementation guide for integrating AI-powered Project Management Plan (PMP) parsing and asset booking suggestion into the SiteSpace platform.

**Goal**: Accept a PMP export from Microsoft Project (PDF, XML, XLSX, CSV), parse it with AI, and intelligently suggest which assets each subcontractor group should book — aligned with the project timeline.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Supported PMP Formats](#2-supported-pmp-formats)
3. [Data Models & Migrations](#3-data-models--migrations)
4. [AI Service Layer](#4-ai-service-layer)
5. [API Endpoints](#5-api-endpoints)
6. [Trade-to-Asset Mapping Knowledge Base](#6-trade-to-asset-mapping-knowledge-base)
7. [Implementation Steps (Ordered)](#7-implementation-steps-ordered)
8. [Frontend Integration Points](#8-frontend-integration-points)
9. [Dependencies to Add](#9-dependencies-to-add)
10. [Environment Variables](#10-environment-variables)
11. [Testing Strategy](#11-testing-strategy)
12. [Future Enhancements](#12-future-enhancements)

---

## 1. System Architecture Overview

```
User (Manager/Admin)
        │
        ▼
  Upload PMP File ──► POST /api/pmp/upload
        │
        ▼
 ┌─────────────────────────────────────┐
 │         PMP Parser Service           │
 │  ┌──────────────────────────────┐   │
 │  │ Format Detection              │   │
 │  │  PDF → LLM text extraction    │   │
 │  │  XML  → MSPDI parser          │   │
 │  │  XLSX → openpyxl parser       │   │
 │  │  CSV  → pandas/csv parser     │   │
 │  └──────────────────────────────┘   │
 │              │                       │
 │              ▼                       │
 │  Structured TaskList (JSON)          │
 │  {task, start_date, end_date,        │
 │   trade, duration, resources}        │
 └─────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────┐
 │         AI Analysis Engine           │
 │  (Claude API — claude-sonnet-4-6)    │
 │                                      │
 │  Input:  TaskList + Project Assets   │
 │           + Subcontractor Groups     │
 │  Output: BookingSuggestions[]        │
 │   {subcontractor_group, asset_type,  │
 │    start_date, end_date, priority,   │
 │    reasoning}                        │
 └─────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────┐
 │       Suggestion Matcher             │
 │  Maps AI suggestions to real         │
 │  Asset records in the DB for         │
 │  the given SiteProject               │
 └─────────────────────────────────────┘
        │
        ▼
  Suggestions stored in DB
  (pmp_suggestions table)
        │
        ▼
  Manager reviews suggestions
  via UI → Apply / Reject / Modify
        │
        ▼
  Approved → SlotBooking records created
```

---

## 2. Supported PMP Formats

| Format | Extension | How MS Project Exports It | Parse Strategy |
|---|---|---|---|
| **PDF** | `.pdf` | File → Export → Save as PDF | Extract text with `pypdf`, then send to Claude for structured extraction |
| **MSPDI XML** | `.xml` | File → Save As → XML | Parse directly with Python's `xml.etree.ElementTree` or `lxml` |
| **Excel** | `.xlsx` | File → Export → Excel Workbook | Parse with `openpyxl` — Gantt data usually in Sheet1 |
| **CSV** | `.csv` | File → Export → CSV | Parse with Python's `csv` module / pandas |
| **MPX** | `.mpx` | File → Save As → MPX (legacy) | Use `mpxj` (Java lib via subprocess) or manual parser — optional |

**Recommended starting formats**: PDF + MSPDI XML (most common exports from MS Project).

---

## 3. Data Models & Migrations

### 3.1 New Table: `pmp_documents`

```python
# app/models/pmp_document.py

import uuid
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.core.database import Base
import enum

class PMPStatus(str, enum.Enum):
    uploaded    = "uploaded"     # File stored, not yet parsed
    parsing     = "parsing"      # Currently being parsed
    parsed      = "parsed"       # TaskList extracted, ready for AI
    analyzing   = "analyzing"    # AI analysis in progress
    ready       = "ready"        # Suggestions generated
    failed      = "failed"       # Parse or AI error
    applied     = "applied"      # All suggestions acted on

class PMPDocument(Base):
    __tablename__ = "pmp_documents"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id     = Column(UUID(as_uuid=True), ForeignKey("site_projects.id", ondelete="CASCADE"), nullable=False)
    file_id        = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False)
    uploaded_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    original_filename = Column(String(500), nullable=False)
    file_format       = Column(String(20), nullable=False)   # pdf | xml | xlsx | csv
    status            = Column(SAEnum(PMPStatus), default=PMPStatus.uploaded, nullable=False)

    # Parsed output — stored as JSON
    parsed_tasks      = Column(JSON, nullable=True)  # List[ParsedTask]
    parse_error       = Column(Text, nullable=True)

    # AI analysis output
    analysis_summary  = Column(Text, nullable=True)
    analysis_metadata = Column(JSON, nullable=True)  # token counts, model used, etc.

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    project     = relationship("SiteProject", back_populates="pmp_documents")
    file        = relationship("StoredFile")
    suggestions = relationship("PMPSuggestion", back_populates="pmp_document", cascade="all, delete-orphan")
```

### 3.2 New Table: `pmp_suggestions`

```python
# app/models/pmp_suggestion.py

import uuid
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Text, Date, Enum as SAEnum, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.core.database import Base
import enum

class SuggestionStatus(str, enum.Enum):
    pending  = "pending"   # Awaiting manager review
    accepted = "accepted"  # Manager approved; booking will be created
    rejected = "rejected"  # Manager dismissed
    applied  = "applied"   # SlotBooking record(s) created
    expired  = "expired"   # Suggested date passed without action

class PMPSuggestion(Base):
    __tablename__ = "pmp_suggestions"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pmp_document_id = Column(UUID(as_uuid=True), ForeignKey("pmp_documents.id", ondelete="CASCADE"), nullable=False)
    project_id      = Column(UUID(as_uuid=True), ForeignKey("site_projects.id", ondelete="CASCADE"), nullable=False)

    # Subcontractor targeting
    subcontractor_id         = Column(UUID(as_uuid=True), ForeignKey("subcontractors.id", ondelete="SET NULL"), nullable=True)
    trade_specialty          = Column(String(100), nullable=False)  # "electrician", "crane_operator", etc.
    subcontractor_group_name = Column(String(200), nullable=True)   # Human-readable group label

    # Asset targeting
    asset_id   = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    asset_type = Column(String(200), nullable=False)   # Matched or suggested type
    asset_name = Column(String(200), nullable=True)    # Matched asset name from DB

    # Timing
    suggested_start_date = Column(Date, nullable=False)
    suggested_end_date   = Column(Date, nullable=False)
    suggested_start_time = Column(String(10), nullable=True)  # "08:00"
    suggested_end_time   = Column(String(10), nullable=True)  # "17:00"

    # AI reasoning
    priority    = Column(Integer, default=2)   # 1=high, 2=medium, 3=low
    confidence  = Column(Integer, default=70)  # 0-100 percentage
    reasoning   = Column(Text, nullable=True)  # Why AI suggested this
    source_task = Column(String(500), nullable=True)  # PMP task name that drove this

    # Manager action
    status          = Column(SAEnum(SuggestionStatus), default=SuggestionStatus.pending, nullable=False)
    manager_notes   = Column(Text, nullable=True)
    booking_id      = Column(UUID(as_uuid=True), ForeignKey("slot_bookings.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    pmp_document  = relationship("PMPDocument", back_populates="suggestions")
    project       = relationship("SiteProject")
    asset         = relationship("Asset")
    subcontractor = relationship("Subcontractor")
    booking       = relationship("SlotBooking")
```

### 3.3 Update Existing Models

**`SiteProject` model** — add back-reference:
```python
# In app/models/site_project.py, add:
pmp_documents = relationship("PMPDocument", back_populates="project", cascade="all, delete-orphan")
```

### 3.4 Alembic Migration

```bash
# After creating the model files and registering them in app/models/__init__.py:
alembic revision --autogenerate -m "add pmp_documents and pmp_suggestions tables"
alembic upgrade head
```

---

## 4. AI Service Layer

### 4.1 PMP Parser Service

Create `app/services/pmp_parser.py`:

```python
"""
Parse various PMP file formats into a unified TaskList structure.

ParsedTask schema (dict):
{
    "id": str,                    # Task ID from PMP
    "name": str,                  # Task name
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "duration_days": int,
    "trade_keywords": [str],      # Keywords hinting at trade: "electrical", "concrete", etc.
    "resources": [str],           # Resource names from PMP if available
    "level": int,                 # WBS level (1=phase, 2=task, 3=subtask)
    "parent_id": str | None,
    "is_milestone": bool,
    "notes": str | None,
}
"""

import io
import csv
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET

from pypdf import PdfReader
import openpyxl


def detect_format(filename: str, content_type: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return "pdf"
    elif ext == "xml":
        return "xml"
    elif ext in ("xlsx", "xls"):
        return "xlsx"
    elif ext == "csv":
        return "csv"
    else:
        raise ValueError(f"Unsupported format: {ext}")


def parse_pdf_to_text(file_bytes: bytes) -> str:
    """Extract all text from PDF for LLM processing."""
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n--- PAGE BREAK ---\n\n".join(pages)


def parse_mspdi_xml(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse Microsoft Project Data Interchange XML format."""
    root = ET.fromstring(file_bytes)
    ns = {"ms": "http://schemas.microsoft.com/project"}
    tasks = []

    for task_el in root.findall(".//ms:Task", ns):
        uid = task_el.findtext("ms:UID", namespaces=ns)
        name = task_el.findtext("ms:Name", namespaces=ns, default="")
        start = task_el.findtext("ms:Start", namespaces=ns)
        finish = task_el.findtext("ms:Finish", namespaces=ns)
        milestone = task_el.findtext("ms:Milestone", namespaces=ns, default="0")
        wbs = task_el.findtext("ms:WBS", namespaces=ns, default="")
        notes = task_el.findtext("ms:Notes", namespaces=ns)
        duration_str = task_el.findtext("ms:Duration", namespaces=ns, default="PT0H0M0S")

        # Parse ISO 8601 duration → days
        duration_days = _parse_iso_duration_days(duration_str)

        # Parse dates (MS Project uses ISO 8601)
        start_date = _parse_ms_date(start) if start else None
        end_date = _parse_ms_date(finish) if finish else None

        if not name or not start_date:
            continue

        level = len(wbs.split(".")) if wbs else 1

        tasks.append({
            "id": uid or str(len(tasks)),
            "name": name,
            "start_date": start_date.strftime("%Y-%m-%d") if start_date else None,
            "end_date": end_date.strftime("%Y-%m-%d") if end_date else None,
            "duration_days": duration_days,
            "trade_keywords": _extract_trade_keywords(name + " " + (notes or "")),
            "resources": [],
            "level": level,
            "parent_id": None,
            "is_milestone": milestone == "1",
            "notes": notes,
        })

    return tasks


def parse_xlsx(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse MS Project Excel export. Typical columns: ID, Name, Duration, Start, Finish, Resource."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    headers = [str(cell.value).strip().lower() if cell.value else "" for cell in ws[1]]

    col_map = {}
    for i, h in enumerate(headers):
        for key, variants in {
            "name": ["task name", "name", "task"],
            "start": ["start", "start date"],
            "finish": ["finish", "end", "end date", "finish date"],
            "duration": ["duration"],
            "resource": ["resource names", "resource", "resources"],
        }.items():
            if any(v in h for v in variants):
                col_map[key] = i

    tasks = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        name = str(row[col_map["name"]]).strip() if "name" in col_map and row[col_map["name"]] else ""
        if not name or name == "None":
            continue

        start_val = row[col_map["start"]] if "start" in col_map else None
        end_val = row[col_map["finish"]] if "finish" in col_map else None
        resource_val = str(row[col_map["resource"]]) if "resource" in col_map and row[col_map["resource"]] else ""

        start_date = _coerce_date(start_val)
        end_date = _coerce_date(end_val)
        if not start_date:
            continue

        tasks.append({
            "id": str(row_idx),
            "name": name,
            "start_date": start_date,
            "end_date": end_date or start_date,
            "duration_days": (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days + 1 if end_date else 1,
            "trade_keywords": _extract_trade_keywords(name + " " + resource_val),
            "resources": [r.strip() for r in resource_val.split(";") if r.strip()],
            "level": 2,
            "parent_id": None,
            "is_milestone": False,
            "notes": None,
        })

    return tasks


def parse_csv(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Generic CSV task list parser."""
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    tasks = []
    for i, row in enumerate(reader):
        name = (row.get("Task Name") or row.get("Name") or row.get("name") or "").strip()
        start_str = row.get("Start") or row.get("start") or row.get("Start Date") or ""
        end_str = row.get("Finish") or row.get("End") or row.get("end") or row.get("End Date") or ""

        start_date = _coerce_date(start_str)
        if not name or not start_date:
            continue

        end_date = _coerce_date(end_str) or start_date

        tasks.append({
            "id": str(i),
            "name": name,
            "start_date": start_date,
            "end_date": end_date,
            "duration_days": 1,
            "trade_keywords": _extract_trade_keywords(name),
            "resources": [],
            "level": 2,
            "parent_id": None,
            "is_milestone": False,
            "notes": None,
        })

    return tasks


# ──────────── Helpers ────────────

TRADE_KEYWORD_MAP = {
    "electrical": ["electrical", "wiring", "cabling", "switchboard", "conduit", "power", "mep"],
    "plumber": ["plumbing", "pipe", "drainage", "sanitary", "water supply", "hydraulic"],
    "concreter": ["concrete", "pour", "slab", "footing", "formwork", "reinforcement", "rebar", "rc"],
    "crane_operator": ["crane", "hoist", "lift", "erect", "steel frame", "precast", "rigging"],
    "carpenter": ["formwork", "timber", "framing", "joinery", "carpentry"],
    "excavation": ["excavation", "earthwork", "grading", "bulk dig", "cut and fill", "demolition"],
    "roofer": ["roofing", "roof", "waterproof", "membrane", "flashings"],
    "hvac": ["hvac", "mechanical", "ductwork", "air conditioning", "ventilation"],
    "mason": ["masonry", "brickwork", "blockwork", "facade", "render", "plaster"],
    "painter": ["painting", "coating", "render", "finish", "decorating"],
    "landscaper": ["landscaping", "garden", "paving", "site works", "soft landscaping"],
}

def _extract_trade_keywords(text: str) -> List[str]:
    text_lower = text.lower()
    found = []
    for trade, keywords in TRADE_KEYWORD_MAP.items():
        if any(kw in text_lower for kw in keywords):
            found.append(trade)
    return found


def _parse_ms_date(val: str) -> Optional[datetime]:
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val[:19], fmt)
        except ValueError:
            continue
    return None


def _parse_iso_duration_days(duration: str) -> int:
    """Convert PT8H0M0S style to days (approximate, 8h/day)."""
    import re
    days = re.search(r"(\d+)D", duration)
    hours = re.search(r"(\d+)H", duration)
    d = int(days.group(1)) if days else 0
    h = int(hours.group(1)) if hours else 0
    return d + (h // 8)


def _coerce_date(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    val = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
```

### 4.2 AI Analysis Service

Create `app/services/pmp_ai_service.py`:

```python
"""
Uses Claude API to analyze parsed PMP tasks against project assets
and generate asset booking suggestions.
"""

import json
from typing import List, Dict, Any
from anthropic import Anthropic

from app.core.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)

SYSTEM_PROMPT = """
You are an expert construction project manager AI assistant for SiteSpace,
a construction asset management platform. Your job is to analyze a Project
Management Plan (PMP) and recommend which assets each subcontractor trade
group should book, and when.

You understand:
- Construction sequencing and critical path logic
- Which trades use which heavy equipment and assets
- That tower cranes, excavators, concrete pumps, etc. have limited availability
- Booking conflicts and the importance of early reservation
- Australian construction practices (the platform operates in Australia)

You MUST output ONLY valid JSON. No prose, no markdown, just JSON.
"""

ANALYSIS_PROMPT_TEMPLATE = """
## Project: {project_name}
## Project Dates: {project_start} to {project_end}

## Available Assets in this Project:
{assets_json}

## Subcontractor Groups assigned to this Project:
{subcontractors_json}

## Parsed PMP Tasks:
{tasks_json}

---

Analyze the above and produce booking suggestions. For each suggestion, identify:
1. Which subcontractor trade group needs an asset
2. Which specific asset (from the available list) should be booked
3. The date range (must fall within the task dates + a small buffer)
4. Priority (1=critical path, 2=important, 3=nice-to-have)
5. Confidence score (0-100)
6. Brief reasoning (1-2 sentences max)

Rules:
- Only suggest assets that exist in the "Available Assets" list
- Prefer exact asset matches; if no exact match, suggest the closest type
- If a subcontractor is directly listed in the PMP resources, match them specifically
- If not, suggest by trade_specialty matching task keywords
- Group overlapping needs — if multiple tasks of same trade need same asset, merge into one booking period
- Consider dependencies: don't suggest an asset before it makes sense sequentially

Output a JSON object with this exact structure:
{{
  "summary": "2-3 sentence plain English summary of what the PMP shows",
  "project_phases": [
    {{"phase": "Foundation Works", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}
  ],
  "suggestions": [
    {{
      "trade_specialty": "crane_operator",
      "subcontractor_group_name": "Tower Crane & Rigging",
      "subcontractor_id": "<UUID or null if group suggestion>",
      "asset_id": "<UUID from available assets list or null if no match>",
      "asset_type": "Tower Crane",
      "asset_name": "TC-01",
      "suggested_start_date": "YYYY-MM-DD",
      "suggested_end_date": "YYYY-MM-DD",
      "suggested_start_time": "07:00",
      "suggested_end_time": "17:00",
      "priority": 1,
      "confidence": 85,
      "reasoning": "Structural steel erection phase runs weeks 4-12; tower crane required for all lifts above level 3.",
      "source_task": "Structural Steel Erection Level 3-8"
    }}
  ]
}}
"""


def analyze_pmp_with_ai(
    project_name: str,
    project_start: str,
    project_end: str,
    parsed_tasks: List[Dict],
    available_assets: List[Dict],
    subcontractors: List[Dict],
    pdf_text: str = None,  # Optional raw PDF text for context
) -> Dict[str, Any]:
    """
    Send PMP data to Claude for AI-powered booking suggestions.
    Returns parsed JSON with suggestions.
    """
    # Limit tasks to avoid huge prompts — summarize if too many
    tasks_to_send = parsed_tasks[:100] if len(parsed_tasks) > 100 else parsed_tasks

    # Build prompt
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        project_name=project_name,
        project_start=project_start,
        project_end=project_end,
        assets_json=json.dumps(available_assets, indent=2),
        subcontractors_json=json.dumps(subcontractors, indent=2),
        tasks_json=json.dumps(tasks_to_send, indent=2),
    )

    # If PDF text available, prepend for more context (truncated)
    if pdf_text:
        prompt = f"## Raw PMP Text (first 3000 chars):\n{pdf_text[:3000]}\n\n" + prompt

    messages = [{"role": "user", "content": prompt}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)

    # Attach token usage metadata
    result["_metadata"] = {
        "model": "claude-sonnet-4-6",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    return result


def extract_tasks_from_pdf_text(pdf_text: str, project_name: str) -> List[Dict]:
    """
    Use Claude to extract structured task list from raw PDF text
    (for PDFs that couldn't be parsed structurally).
    """
    prompt = f"""
Extract all project tasks from the following MS Project PDF export for project: "{project_name}".

PDF TEXT:
{pdf_text[:8000]}

Output a JSON array of tasks. Each task must have:
- "id": sequential string number
- "name": task name (string)
- "start_date": "YYYY-MM-DD" (null if not found)
- "end_date": "YYYY-MM-DD" (null if not found)
- "duration_days": integer (0 if unknown)
- "level": WBS level integer (1=phase/summary, 2=task, 3=subtask)
- "resources": array of resource/subcontractor names mentioned
- "is_milestone": boolean
- "notes": any notes (null if none)

Output ONLY valid JSON array. No markdown, no prose.
"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # Use cheaper model for extraction
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    tasks = json.loads(raw.strip())

    # Post-process: add trade keywords
    from app.services.pmp_parser import _extract_trade_keywords
    for task in tasks:
        task["trade_keywords"] = _extract_trade_keywords(task.get("name", "") + " " + " ".join(task.get("resources", [])))

    return tasks
```

---

## 5. API Endpoints

### 5.1 New Router: `app/api/v1/pmp.py`

**Endpoints to create:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/pmp/upload` | manager/admin | Upload PMP file + kick off parsing |
| `GET` | `/api/pmp/` | manager/admin | List PMP documents for accessible projects |
| `GET` | `/api/pmp/{pmp_id}` | manager/admin | Get PMP detail + status |
| `POST` | `/api/pmp/{pmp_id}/analyze` | manager/admin | Trigger AI analysis (async) |
| `GET` | `/api/pmp/{pmp_id}/suggestions` | manager/admin | List suggestions for this PMP |
| `PATCH` | `/api/pmp/suggestions/{suggestion_id}` | manager/admin | Accept / Reject a suggestion |
| `POST` | `/api/pmp/suggestions/{suggestion_id}/apply` | manager/admin | Create SlotBooking from suggestion |
| `POST` | `/api/pmp/{pmp_id}/apply-all` | manager/admin | Bulk apply all accepted suggestions |
| `DELETE` | `/api/pmp/{pmp_id}` | manager/admin | Delete PMP + suggestions |

### 5.2 Endpoint Implementation Sketch

```python
# app/api/v1/pmp.py (key endpoints)

@router.post("/upload", response_model=PMPDocumentResponse)
async def upload_pmp(
    project_id: UUID,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin),
):
    """
    1. Validate file type (pdf/xml/xlsx/csv)
    2. Save via StorageBackend → creates StoredFile record
    3. Create PMPDocument record (status=uploaded)
    4. Launch background task: parse + analyze
    5. Return PMPDocument immediately (client polls status)
    """
    ...
    background_tasks.add_task(process_pmp_document, pmp_doc.id, db_url)
    return pmp_doc


@router.post("/{pmp_id}/analyze", response_model=PMPDocumentResponse)
async def trigger_analysis(
    pmp_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin),
):
    """Re-run AI analysis on already-parsed PMP."""
    ...


@router.patch("/suggestions/{suggestion_id}", response_model=PMPSuggestionResponse)
async def update_suggestion(
    suggestion_id: UUID,
    payload: SuggestionUpdateRequest,  # {status, manager_notes, overrides}
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin),
):
    """Accept or reject a suggestion. Can also override dates/asset."""
    ...


@router.post("/suggestions/{suggestion_id}/apply", response_model=SlotBookingResponse)
async def apply_suggestion(
    suggestion_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin),
):
    """
    Convert an accepted PMPSuggestion into a SlotBooking.
    Uses existing booking creation logic from crud/slot_booking.py.
    Marks suggestion status = applied.
    """
    ...
```

### 5.3 Background Processing Function

```python
# app/services/pmp_processor.py

async def process_pmp_document(pmp_id: UUID, db_url: str):
    """
    Full pipeline run as a background task:
    1. Load PMP document from DB
    2. Fetch file bytes from storage
    3. Parse based on format → parsed_tasks
    4. Save parsed_tasks to DB (status=parsed)
    5. Load project assets + subcontractors
    6. Call AI analysis → suggestions
    7. Save suggestions to pmp_suggestions table (status=ready)
    """
    ...
```

---

## 6. Trade-to-Asset Mapping Knowledge Base

This is the static mapping used to pre-filter AI suggestions and for the extraction phase.
Store in `app/services/trade_asset_map.py`:

```python
TRADE_ASSET_MAP = {
    "crane_operator": {
        "asset_types": ["Tower Crane", "Mobile Crane", "Luffing Crane", "Pick and Carry Crane"],
        "asset_keywords": ["crane", "hoist"],
        "typical_phases": ["structure", "steel erection", "precast", "lift"],
        "typical_duration_days": (30, 180),
    },
    "excavation": {
        "asset_types": ["Excavator", "Backhoe", "Bulldozer", "Dump Truck", "Compactor"],
        "asset_keywords": ["excavator", "backhoe", "bulldozer"],
        "typical_phases": ["earthworks", "bulk excavation", "site preparation"],
        "typical_duration_days": (5, 60),
    },
    "concreter": {
        "asset_types": ["Concrete Pump", "Concrete Mixer", "Boom Pump", "Kibble"],
        "asset_keywords": ["pump", "mixer"],
        "typical_phases": ["footing", "slab", "pour", "column"],
        "typical_duration_days": (1, 14),
    },
    "electrician": {
        "asset_types": ["EWP", "Scissor Lift", "Boom Lift", "Cable Drum Trailer"],
        "asset_keywords": ["ewp", "scissor lift", "boom lift", "manlift"],
        "typical_phases": ["rough-in", "fit-off", "mep", "services"],
        "typical_duration_days": (3, 30),
    },
    "plumber": {
        "asset_types": ["Trencher", "Mini Excavator", "EWP"],
        "asset_keywords": ["trencher", "mini excavator"],
        "typical_phases": ["underground services", "drainage", "plumbing rough-in"],
        "typical_duration_days": (2, 20),
    },
    "hvac": {
        "asset_types": ["EWP", "Boom Lift", "Forklift", "Crane"],
        "asset_keywords": ["ewp", "boom lift", "forklift"],
        "typical_phases": ["mechanical services", "ductwork", "plant room"],
        "typical_duration_days": (5, 45),
    },
    "carpenter": {
        "asset_types": ["Formwork Crane", "EWP", "Forklift", "Compactor"],
        "asset_keywords": ["forklift", "ewp"],
        "typical_phases": ["formwork", "framing", "carpentry"],
        "typical_duration_days": (3, 30),
    },
    "roofer": {
        "asset_types": ["EWP", "Boom Lift", "Tower Crane"],
        "asset_keywords": ["ewp", "boom lift"],
        "typical_phases": ["roofing", "waterproofing"],
        "typical_duration_days": (5, 30),
    },
    "mason": {
        "asset_types": ["EWP", "Scaffold", "Tower Crane"],
        "asset_keywords": ["ewp", "scaffold"],
        "typical_phases": ["brickwork", "facade", "blockwork"],
        "typical_duration_days": (10, 90),
    },
    "landscaper": {
        "asset_types": ["Mini Excavator", "Bobcat", "Skid Steer", "Dump Truck"],
        "asset_keywords": ["bobcat", "skid steer", "mini excavator"],
        "typical_phases": ["external works", "landscaping", "site clean"],
        "typical_duration_days": (5, 30),
    },
    "general": {
        "asset_types": ["Forklift", "EWP", "Telehandler"],
        "asset_keywords": ["forklift", "telehandler", "ewp"],
        "typical_phases": ["general works"],
        "typical_duration_days": (1, 90),
    },
}
```

---

## 7. Implementation Steps (Ordered)

### Step 1 — Set Up Claude API Access
- [ ] Create Anthropic account at console.anthropic.com
- [ ] Generate API key
- [ ] Add `ANTHROPIC_API_KEY` to `.env` / Railway environment variables
- [ ] Add `anthropic>=0.40.0` to `requirements.txt`
- [ ] Test basic API call in a scratch script

### Step 2 — Create Model Files
- [ ] Create `app/models/pmp_document.py` (PMPDocument, PMPStatus)
- [ ] Create `app/models/pmp_suggestion.py` (PMPSuggestion, SuggestionStatus)
- [ ] Update `app/models/__init__.py` to import new models
- [ ] Add `pmp_documents` back-ref to `SiteProject` model

### Step 3 — Create Alembic Migration
- [ ] Run `alembic revision --autogenerate -m "add pmp tables"`
- [ ] Review generated migration for correctness
- [ ] Run `alembic upgrade head` on local DB
- [ ] Verify tables created correctly

### Step 4 — Create Pydantic Schemas
- [ ] Create `app/schemas/pmp.py`:
  - `PMPDocumentCreate`, `PMPDocumentResponse`
  - `PMPSuggestionResponse`, `SuggestionUpdateRequest`
  - `ParsedTask` (for internal use)
  - `PMPAnalysisResult`

### Step 5 — Build PMP Parser Service
- [ ] Create `app/services/pmp_parser.py` (from Section 4.1)
- [ ] Add `openpyxl>=3.1.0` and `lxml>=5.0` to `requirements.txt`
- [ ] Unit test each format parser with a sample file from MS Project

### Step 6 — Build AI Analysis Service
- [ ] Create `app/services/pmp_ai_service.py` (from Section 4.2)
- [ ] Create `app/services/trade_asset_map.py` (from Section 6)
- [ ] Test with a real PMP file and project

### Step 7 — Build Background Processor
- [ ] Create `app/services/pmp_processor.py`
- [ ] Implement full pipeline: parse → save → analyze → save suggestions
- [ ] Add proper error handling: set `status=failed` + `parse_error` message on exceptions

### Step 8 — Build CRUD Layer
- [ ] Create `app/crud/pmp.py`:
  - `create_pmp_document()`
  - `get_pmp_document()`
  - `list_pmp_documents()`
  - `update_pmp_status()`
  - `save_suggestions()`
  - `list_suggestions()`
  - `update_suggestion()`
  - `apply_suggestion_to_booking()`

### Step 9 — Build API Router
- [ ] Create `app/api/v1/pmp.py` (from Section 5)
- [ ] Register router in `app/main.py`:
  ```python
  from app.api.v1 import pmp
  app.include_router(pmp.router, prefix="/api/pmp", tags=["PMP AI"])
  ```

### Step 10 — Extend File Upload for PMP Formats
- [ ] Update `app/api/v1/files.py` allowed content types to include:
  - `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (xlsx)
  - `application/xml`, `text/xml`
  - `text/csv`
- [ ] Increase file size limit for PMP files to 50 MB

### Step 11 — Test End-to-End
- [ ] Upload a real MS Project PDF export
- [ ] Verify parsing produces reasonable task list
- [ ] Verify AI suggestions match expected assets
- [ ] Test suggestion accept/reject flow
- [ ] Test booking creation from suggestion

### Step 12 — Frontend Integration
- [ ] Build "Import PMP" button on Project detail page
- [ ] Build PMP status polling UI (show progress bar while parsing)
- [ ] Build "AI Suggestions" review panel with accept/reject per suggestion
- [ ] Add "Apply All Accepted" bulk action

---

## 8. Frontend Integration Points

The FE at `sitespace.com.au` should add:

### 8.1 Project Detail Page
```
┌─────────────────────────────────────────────────────────┐
│ Project: CBD Office Tower                               │
│ ─────────────────────────────────────────────────────  │
│ [Site Plans] [Assets] [Bookings] [📋 PMP Intelligence]  │
└─────────────────────────────────────────────────────────┘
```

### 8.2 PMP Intelligence Tab
```
┌─────────────────────────────────────────────────────────┐
│ PMP Intelligence                           [Import PMP] │
├─────────────────────────────────────────────────────────┤
│ ✅ MyProject.pdf                    Analyzed 2h ago     │
│    "Foundation works (wks 1-6), Structure (wks 7-18)..." │
│                                  [View Suggestions ›]   │
├─────────────────────────────────────────────────────────┤
│ AI Booking Suggestions (12 pending review)              │
│                                                         │
│ 🔴 HIGH  Tower Crane TC-01    Mar 15 → Jul 30          │
│          Structural Works · 90% confidence              │
│          [Accept] [Reject] [Modify]                     │
│                                                         │
│ 🟡 MED   Concrete Pump CP-02  Mar 1 → Mar 14           │
│          Footings & Slab · 75% confidence               │
│          [Accept] [Reject] [Modify]                     │
│                                                         │
│                        [Apply All Accepted] [Export]    │
└─────────────────────────────────────────────────────────┘
```

### 8.3 API Calls from FE
```javascript
// Upload PMP
POST /api/pmp/upload
FormData: { file: File, project_id: UUID }

// Poll status until "ready"
GET /api/pmp/{pmp_id}   → { status: "parsing" | "analyzing" | "ready" | "failed" }

// Load suggestions
GET /api/pmp/{pmp_id}/suggestions

// Accept suggestion
PATCH /api/pmp/suggestions/{id}
Body: { status: "accepted" }

// Create booking from suggestion
POST /api/pmp/suggestions/{id}/apply
```

---

## 9. Dependencies to Add

Add to `requirements.txt`:

```
# AI
anthropic>=0.40.0

# PMP parsing
openpyxl>=3.1.0
lxml>=5.0.0

# Already present (confirm):
pypdf>=3.0.0     # PDF text extraction
Pillow>=10.0.0   # Already in for site plans
```

Note: `pypdf` is already in the requirements for PDF utilities. `openpyxl` and `lxml` are new. `anthropic` is new.

---

## 10. Environment Variables

Add to `.env` and Railway project variables:

```bash
# Claude AI
ANTHROPIC_API_KEY=sk-ant-...

# PMP Processing
PMP_MAX_FILE_SIZE_MB=50          # Default: 50
PMP_MAX_TASKS_FOR_AI=150         # Truncate large PMPs before AI call
PMP_AI_MODEL=claude-sonnet-4-6   # Model to use for analysis
PMP_EXTRACTION_MODEL=claude-haiku-4-5-20251001  # Cheaper model for PDF extraction
```

Add to `app/core/config.py`:
```python
anthropic_api_key: str = ""
pmp_max_file_size_mb: int = 50
pmp_max_tasks_for_ai: int = 150
pmp_ai_model: str = "claude-sonnet-4-6"
pmp_extraction_model: str = "claude-haiku-4-5-20251001"
```

---

## 11. Testing Strategy

### Unit Tests
- `tests/test_pmp_parser.py`: Test each format parser with fixture files
- `tests/test_trade_asset_map.py`: Test keyword extraction

### Integration Tests
- `tests/test_pmp_api.py`: Upload → Parse → Analyze → Suggest → Apply flow
- Mock the Anthropic API client in tests to avoid real API calls and cost

### Test Fixtures
Create `tests/fixtures/`:
- `sample_project.pdf` — MS Project export as PDF
- `sample_project.xml` — MSPDI XML export
- `sample_project.xlsx` — Excel export
- `sample_project.csv` — CSV export

### Manual QA Checklist
- [ ] Upload a real MS Project PDF with 20+ tasks
- [ ] Verify parsed_tasks JSON looks correct
- [ ] Verify AI suggestions reference real assets from DB
- [ ] Accept 3 suggestions and create bookings
- [ ] Verify bookings appear in booking calendar
- [ ] Reject 2 suggestions and verify status updated
- [ ] Test conflict: suggest asset already booked → verify warning shown

---

## 12. Future Enhancements

| Enhancement | Priority | Notes |
|---|---|---|
| **MPX format support** | Low | Use Java MPXJ tool via subprocess. Complex setup. |
| **Revision tracking** | Medium | Re-upload updated PMP → diff against previous suggestions |
| **Booking conflict warnings** | High | Before applying suggestion, check against existing bookings |
| **Gantt chart visualization** | Medium | Render parsed PMP as Gantt on FE |
| **Subcontractor notification** | Medium | Email subcontractors when bookings are created from PMP |
| **AI feedback loop** | High | When managers reject suggestions, log reason → fine-tune prompt |
| **Cost estimation** | Low | Use asset daily rates × duration for budget estimate |
| **Multi-project PMP** | Low | Some PMPs span multiple construction zones/buildings |
| **WebSocket status updates** | Medium | Replace polling with WS push for parse/analyze progress |
| **S3 storage for PMP files** | High | Current local storage not suitable for production scale |
| **Rate limiting for AI calls** | High | Prevent runaway API costs; queue concurrent AI jobs |

---

## Appendix A: MS Project Export Instructions (User Guide)

Include these instructions in the FE when the user clicks "Import PMP":

**Option 1 — Export as PDF (Recommended for quick start)**
1. Open your MS Project file
2. File → Export → Create PDF/XPS Document
3. Choose "Entire Project" in print range
4. Save as `.pdf`

**Option 2 — Export as XML (Best structured data)**
1. File → Save As
2. Change "Save as type" to "XML Format (*.xml)"
3. Click Save → OK on the compatibility dialog

**Option 3 — Export as Excel**
1. File → Export → Save Project as File → Microsoft Excel Workbook
2. Follow the Export Wizard: select "Selected data" → Task data
3. Include columns: Task Name, Duration, Start, Finish, Resource Names
4. Save as `.xlsx`

---

## Appendix B: Cost Estimate for Claude API Usage

| Scenario | Tasks | Input Tokens | Output Tokens | Cost (est.) |
|---|---|---|---|---|
| Small project (20 tasks) | 20 | ~3,000 | ~1,000 | ~$0.02 |
| Medium project (80 tasks) | 80 | ~8,000 | ~2,000 | ~$0.04 |
| Large project (200 tasks, truncated to 150) | 150 | ~15,000 | ~3,000 | ~$0.07 |
| PDF extraction (haiku model) | — | ~5,000 | ~2,000 | ~$0.003 |

Using `claude-sonnet-4-6`: ~$3/M input tokens, ~$15/M output tokens.
Typical full pipeline (extraction + analysis) per PMP upload: **< $0.15 AUD**.

---

*Document version: 1.0 — Created for SiteSpace PMP AI Integration*
*Tech stack: FastAPI + PostgreSQL + Claude API (Anthropic)*
