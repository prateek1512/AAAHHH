# Architecture

> System architecture documentation for the AI-Powered Land Record Management
> & Verification System. This file describes the actual implementation — not
> aspirational features.

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Portal Entrypoints](#portal-entrypoints)
3. [Module Dependency Graph](#module-dependency-graph)
4. [Data Model](#data-model)
5. [Authentication & Session Management](#authentication--session-management)
6. [Document Upload & OCR Pipeline](#document-upload--ocr-pipeline)
7. [Field Extraction & Validation](#field-extraction--validation)
8. [Discrepancy Detection Engine](#discrepancy-detection-engine)
9. [Consistency Score Calculation](#consistency-score-calculation)
10. [Application Lifecycle](#application-lifecycle)
11. [SHA-256 Audit Chain](#sha-256-audit-chain)
12. [Approved Record Registry](#approved-record-registry)
13. [Parcel Map Rendering](#parcel-map-rendering)
14. [QR Code & Public Verification](#qr-code--public-verification)
15. [Demo Data Management](#demo-data-management)
16. [Test Architecture](#test-architecture)
17. [Security Design Decisions](#security-design-decisions)

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Streamlit Frontend                          │
│                                                                     │
│   app.py              landowner_app.py          officer_app.py      │
│   (Public Lookup)     (Landowner Portal)        (Officer Portal)    │
│   Port 8501           Port 8502                 Port 8503           │
└────────┬──────────────────────┬──────────────────────┬──────────────┘
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   modules/auth_ui.py  │  Shared login / logout
                    │   (Streamlit helpers) │  components
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │   modules/auth.py     │  AuthService
                    │   (Business Logic)    │  Orchestrates all
                    │                       │  workflows
                    └───────┬───┬───┬───────┘
                            │   │   │
              ┌─────────────┘   │   └─────────────┐
              │                 │                 │
   ┌──────────▼──────┐  ┌──────▼──────┐  ┌───────▼──────────┐
   │ modules/         │  │ modules/     │  │ modules/          │
   │ database.py      │  │ document_    │  │ audit.py          │
   │ (ORM + Queries)  │  │ storage.py   │  │ (SHA-256 Chain)   │
   └──────────────────┘  │ (File I/O)   │  └──────────────────┘
              │          └──────────────┘
   ┌──────────▼──────┐
   │  SQLite / PG    │
   │  Database       │
   └─────────────────┘

          ┌─────────────────────────────────────┐
          │         Processing Modules           │
          │                                     │
          │  ocr.py → extraction.py             │
          │  field_validation.py                │
          │  discrepancy.py                     │
          │  consistency_score.py               │
          │  parcel_map.py                      │
          │  public_qr.py                       │
          │  registry_ui.py                     │
          └─────────────────────────────────────┘
```

---

## Portal Entrypoints

### `app.py` — Public Land Record Verification

- No authentication required
- Accepts `?record_id=<ID>` query parameter for deep-linking
- Displays only approved public-release fields (district, village, survey/plot
  number, land area)
- Shows immutable version history with SHA-256 hashes
- Provides explicit **Verify Audit Integrity** button
- Renders parcel map when GeoJSON geometry is available

### `landowner_app.py` — Landowner Portal

Five sidebar pages:

| Page | Functionality |
|---|---|
| Dashboard | Application metrics, recent applications, quick-submit button |
| Upload Land Document | File upload → OCR → field review → preview → save draft → submit |
| My Applications | Full application list, detail view, correction/resubmission workflow |
| Notifications | Unread/read notifications with mark-as-read |
| Approved Registry | Shared registry search, record details, version history, QR codes |

### `officer_app.py` — Government Officer Portal

Five sidebar pages:

| Page | Functionality |
|---|---|
| Department Dashboard | Department-wide metrics, quick-action buttons |
| Review Queue | Filterable queue (status, application #, district, village) |
| Application Review | 7-tab review workspace (OCR, fields, documents, history, consistency, discrepancies, map) + decision form |
| Approved Registry | Same shared registry with additional officer-only internal details and correction form |
| Audit Explorer | Filterable audit log viewer + full chain verification |

---

## Module Dependency Graph

```
auth_ui.py ──→ auth.py ──→ database.py ──→ SQLAlchemy
                  │  │
                  │  ├──→ audit.py (SHA-256 chain)
                  │  ├──→ document_storage.py (file I/O)
                  │  ├──→ ocr.py ──→ extraction.py
                  │  ├──→ field_validation.py
                  │  ├──→ discrepancy.py ──→ field_validation.py
                  │  ├──→ consistency_score.py ──→ field_validation.py
                  │  └──→ public_qr.py
                  │
registry_ui.py ──→ auth.py
                   parcel_map.py ──→ folium + streamlit-folium
```

Key design principles:

- **`auth.py` is the sole business-logic gateway.** All portals call
  `AuthService` methods; no portal directly queries the database.
- **`database.py` owns the ORM models and raw queries.** It exposes a
  `Database` class with repository methods.
- **Processing modules are stateless.** `ocr.py`, `extraction.py`,
  `field_validation.py`, `discrepancy.py`, and `consistency_score.py` are pure
  functions that take inputs and return dataclass results.

---

## Data Model

### Entity-Relationship Overview

```
User (1) ──────< Application (1) ──────< ApplicationVersion
  │                   │                         │
  │                   ├──────< Document          │ corrected_fields_json
  │                   │                         │ extracted_fields_json
  │                   ├──────< ReviewEvent
  │                   │
  │                   └──────< AuditEvent
  │                                │
  │              LandRecord (1) ──<┤
  │                   │            │
  │                   └──< ApprovedRecordVersion
  │
  ├──< AuthSession
  └──< Notification

AuditChainState (singleton, id=1)
```

### Core Tables

| Model | Purpose | Key Fields |
|---|---|---|
| `User` | Registered accounts | `email`, `password_hash` (Argon2id), `role` (landowner/officer), `full_name` |
| `AuthSession` | Server-side session tokens | `token` (256-bit hex), `user_id`, `expires_at` |
| `Application` | Land record application | `application_number` (LR-YYYY-XXXXXXXX), `status`, `current_version`, `assigned_officer_id` |
| `ApplicationVersion` | Immutable version snapshots | `version_number`, `corrected_fields_json`, `extracted_fields_json`, `created_at` |
| `Document` | Uploaded documents | `original_filename`, `stored_path` (opaque UUID), `document_sha256`, `mime_type`, `ocr_result_json` |
| `ReviewEvent` | Officer decisions | `decision` (approve/reject/request_corrections), `comments`, `officer_id` |
| `LandRecord` | Approved registry entries | `public_record_id`, `application_id`, `approved_fields_json`, `current_version`, `parcel_geojson` |
| `ApprovedRecordVersion` | Immutable approved snapshots | `version_number`, `approved_fields_json`, `version_hash` (SHA-256), `audit_event_id` |
| `Notification` | In-app notifications | `user_id`, `title`, `message`, `is_read` |
| `AuditEvent` | Audit log entries | `event_type`, `actor_id`, `event_data_json`, `event_hash`, `previous_event_hash` |
| `AuditChainState` | Singleton chain head | `id=1`, `head_hash` |

### Application Status State Machine

```
                 ┌──────────────────────────────────────────┐
                 │                                          │
  draft ──→ submitted ──→ under_review ──→ approved         │
                 ▲              │                            │
                 │              ├──→ rejected                │
                 │              │                            │
                 │              └──→ corrections_requested ──┘
                 │                            │
                 └────────────────────────────┘
                      (resubmission)
```

---

## Authentication & Session Management

**Module:** `modules/auth.py` — `AuthService` class

| Mechanism | Implementation |
|---|---|
| Password storage | Argon2id (via `argon2-cffi`), never plaintext |
| Session tokens | 256-bit cryptographic random hex strings |
| Session storage | Server-side `AuthSession` table with expiry |
| Role enforcement | `require_role(token, role)` raises `AuthorizationError` |
| Login | Validates email + password → creates session → returns token |
| Logout | Deletes session row from database |

There is no public registration endpoint. Accounts are seeded via CLI
commands with environment variable credentials.

---

## Document Upload & OCR Pipeline

**Modules:** `modules/document_storage.py` → `modules/ocr.py` → `modules/extraction.py`

### Upload Flow

```
Uploaded file
    │
    ▼
DocumentStorage.store()
    ├── Validate filename (no path traversal, control chars, length)
    ├── Validate extension (pdf/png/jpg/jpeg)
    ├── Validate MIME type matches extension
    ├── Check file size (≤ configurable max, default 10 MiB)
    ├── Validate file content (magic bytes + parser verification)
    │   ├── PDF: %PDF- header + PyMuPDF parse + not encrypted + page_count ≥ 1
    │   ├── PNG: \x89PNG header + PIL verify + load
    │   └── JPEG: \xFF\xD8\xFF header + PIL verify + load
    ├── Generate opaque UUID-hex storage key
    ├── Write with O_CREAT|O_EXCL (0600 permissions)
    └── Return StoredDocument(original_filename, storage_key, sha256, mime, size)
```

### OCR Pipeline

```
content bytes + filename + mime_type
    │
    ▼
process_document()
    │
    ├── PDF path:
    │   ├── PyMuPDF open → check encryption → iterate pages (max 100)
    │   ├── Render each page to RGB pixmap (scale=2.0, capped to 25M pixels)
    │   └── Convert to PIL Image
    │
    ├── Image path:
    │   └── PIL open → check dimensions → load
    │
    ▼
_preprocess_image()          (per page)
    ├── Convert to grayscale (OpenCV)
    ├── Downscale if > 25M pixels
    ├── Upscale if shortest edge < 1200px (up to 3x)
    ├── Median blur (kernel=3) for noise reduction
    └── Otsu threshold if bright + high contrast; else use denoised

    │
    ▼
_lines_from_tesseract()      (per page)
    ├── pytesseract.image_to_data(--psm 6)
    ├── Group words by (page, block, paragraph, line)
    └── Return: raw text + list[OCRLine(text, confidence)]

    │
    ▼
extract_fields()             (all lines combined)
    ├── For each of 9 field types, search for labeled patterns
    │   (e.g., "Owner name:", "District:", "Survey no:")
    ├── Post-process: area with unit regex, date format, registration number
    ├── Detect document type (sale deed, title deed, lease deed, etc.)
    └── Return FieldExtraction(fields, confidence, warnings)

    │
    ▼
DocumentOCRResult(status, ocr_text, fields, confidence, warnings, page_count)
```

### Extracted Fields

| Field Key | Example Labels Matched |
|---|---|
| `owner_name` | "Owner name", "Name of owner", "Holder name" |
| `district` | "District", "Zilla" |
| `circle_or_tehsil` | "Circle", "Tehsil", "Taluka" |
| `village` | "Village", "Mouza", "Gram" |
| `survey_or_plot_number` | "Survey no", "Plot number", "Khasra no", "Dag no" |
| `land_area` | "Land area", "Area", "Extent" |
| `registration_or_document_number` | "Registration no", "Document number", "Deed no" |
| `document_date` | "Document date", "Registration date", "Date" |
| `document_type` | Detected by keyword: sale deed, title deed, lease deed, etc. |

---

## Field Extraction & Validation

**Module:** `modules/field_validation.py`

### Required Fields

`owner_name`, `district`, `village`, `survey_or_plot_number`, `land_area`,
`document_type`

### Validation Rules

| Field | Rule |
|---|---|
| All fields | ≤ 500 characters, no control characters |
| `land_area` | Must be `<positive number> <explicit unit>` (e.g., "2.5 acres", "120 sq ft"); no unit inference or conversion |
| `document_date` | Must be DD/MM/YYYY or YYYY-MM-DD, must parse to a valid date |

### Field Warnings

`build_field_warnings()` generates reproducible warnings per application
version by comparing:

- OCR-extracted value vs. required-field completeness
- OCR confidence vs. configurable threshold (`OCR_LOW_CONFIDENCE_THRESHOLD`,
  default 60%)
- Landowner-corrected values vs. format rules

Warnings are stored in the version snapshot for reproducible officer review.

---

## Discrepancy Detection Engine

**Module:** `modules/discrepancy.py`

All rules are **deterministic** and **local** — no ML, no external API calls.
Each warning specifies severity, compared fields, triggering rule, and compared
values.

### Warning Severities

| Severity | Meaning |
|---|---|
| `definite` | Format or required-field validation failure |
| `possible` | Potential discrepancy requiring human review |
| `informational` | Missing comparison reference (no conclusion drawn) |

### Comparison Rules

| Code | Compares Against | Trigger |
|---|---|---|
| `missing_required_field` | Internal | Required field is empty |
| `invalid_field_format` | Internal | Field fails format validation |
| `ocr_corrected_difference` | OCR extracted | Owner-corrected value differs from OCR |
| `application_version_difference` | Previous version | Field changed between versions |
| `approved_location_mismatch` | Approved records | Same survey # but different district/village |
| `approved_area_mismatch` | Approved records | Same survey # + location but different area |
| `approved_survey_mismatch` | Approved records | Same location + area but different survey # |
| `possible_duplicate_approved_record` | Approved records | Same survey # + location exists |
| `possible_duplicate_application` | Active applications | Same survey # + location (officer-only) |
| `reference_comparison_unavailable` | None | No comparable reference record found |

> Matching parcels do **not** prove ownership. Mismatches are **not** evidence
> of fraud. No decision is ever made automatically.

---

## Consistency Score Calculation

**Module:** `modules/consistency_score.py`

A weighted 0–100 indicator for officer **prioritization**, not a legal score.

### Components

| Component | Default Weight | Calculation |
|---|---|---|
| `ocr_confidence` | 25% | Mean of valid Tesseract 0-100 field confidences |
| `required_completeness` | 25% | (Non-empty required fields / total required fields) × 100 |
| `reference_consistency` | 30% | 100 − (20 × applicable reference warnings), floor 0 |
| `audit_integrity` | 20% | 100 if audit events present and chain verifies, else 0 |

### Formula

```
score = Σ(component_score × weight) / Σ(weight)
        ─── only over available components ───
```

Unavailable components are excluded (not treated as passed). Remaining
weights are renormalized. Configurable via `RECORD_CONSISTENCY_WEIGHTS` JSON
environment variable.

---

## Application Lifecycle

### Landowner Workflow

1. **Upload** → Document stored privately → OCR runs → draft application created
2. **Review OCR** → Correct/enter fields → Preview → Save as draft version
3. **Submit** → Immutable version snapshot → audit event → notification → status = `submitted`
4. _(If corrections requested)_ → Revise fields → Save → Resubmit → new version + audit event

### Officer Workflow

1. **Review Queue** → Filter and select application
2. **Claim** → Assign to self → status = `under_review` → audit event
3. **Review** → 7-tab workspace: OCR, fields, documents, history, consistency, discrepancies, map
4. **Decide** → Approve / Reject / Request Corrections
   - **Approve** → Create `LandRecord` + `ApprovedRecordVersion` + audit event + notification
   - **Reject** → Record reason → audit event → notification → final
   - **Request corrections** → Record instructions → audit event → notification → landowner revises

### Post-Approval

- Approved records appear in the searchable public registry
- Officers can create authorized corrections (new immutable versions)
- Public verification available via QR code or direct URL

---

## SHA-256 Audit Chain

**Module:** `modules/audit.py`

### Design

| Property | Implementation |
|---|---|
| Hash algorithm | SHA-256 |
| Data serialization | Deterministic canonical JSON (sorted keys, no whitespace, no NaN) |
| Chain origin | Fixed genesis hash: `SHA-256("land-record-audit-chain-v1:genesis")` |
| Linking | Each event's `previous_event_hash` points to the preceding event's hash |
| Head tracking | `AuditChainState` singleton stores current chain head hash |
| Append guarantee | New events verify the chain before appending; invalid chains reject appends |

### Hash Payload

Each event hash covers:

```json
{
  "event_id": <int>,
  "event_type": "<string>",
  "actor_id": <int>,
  "application_id": <int|null>,
  "land_record_id": <int|null>,
  "event_data": { ... },
  "created_at": "<ISO 8601 UTC microseconds>",
  "previous_event_hash": "<hex>"
}
```

### Verification Checks

`verify_audit_chain()` detects:

- Duplicate event IDs or hashes
- Missing genesis link
- Forks (multiple events linking to same parent)
- Broken previous-hash links
- Tampered event content (recomputed hash ≠ stored hash)
- Disconnected events
- Event ID gaps
- Cycles

### Approved Record Version Hashes

Each `ApprovedRecordVersion` also has its own SHA-256 hash covering:

```json
{
  "public_record_id": "<string>",
  "application_id": <int>,
  "version_number": <int>,
  "approved_fields": { ... },
  "authorized_by": <int>,
  "authorized_at": "<ISO 8601 UTC>"
}
```

### Limitations

- **Tamper-evidence, not tamper-proof.** A database administrator can alter
  events and recompute the chain.
- **Not blockchain.** Uses a conventional relational database.
- **Does not establish ownership.** A valid chain proves internal consistency,
  not factual correctness.

---

## Approved Record Registry

**Module:** `modules/registry_ui.py` (UI) + `modules/auth.py` (data)

### Published Fields (Public)

- Public record ID
- District, village, survey/plot number, land area
- Approval version and date
- Non-sensitive audit history
- Parcel map (when GeoJSON available)

### Private Fields (Officer-Only)

- Application number
- Applicant name
- Approving officer name
- Full approved fields JSON
- Internal review notes
- Authorized correction form

### Features

- Full-text search by record ID or survey/plot number
- District and village filters
- Paginated results (25 per page)
- Immutable version history with field-level diffs
- QR code generation for public verification URLs
- Audit verification per record

---

## Parcel Map Rendering

**Module:** `modules/parcel_map.py`

### Supported Geometry

- GeoJSON `Polygon` and `MultiPolygon`
- WGS84 coordinates validated (longitude ±180, latitude ±90)
- Ring closure check (first point = last point, minimum 4 points per ring)
- Raw user properties stripped; only allowlisted attributes rendered

### Map Features

- **Basemaps:** OpenStreetMap streets + Esri satellite imagery (user selectable)
- **Styling:** Color-coded by validation status (green/amber/red/blue)
- **Tooltips:** Record ID + allowlisted parcel attributes
- **Fit bounds:** Auto-zooms to parcel extent
- **Synthetic demo:** Optional fabricated polygon for UI demonstration when no
  real geometry exists

> Map display is **separate from approval**. Satellite imagery does not
> establish ownership or cadastral boundaries.

---

## QR Code & Public Verification

**Module:** `modules/public_qr.py`

- Generates PNG QR codes encoding only the public verification URL
- URL format: `<base_url>/?record_id=<url-encoded-record-id>`
- Base URL configurable via `PUBLIC_VERIFICATION_BASE_URL`
- QR available only for currently approved registry records
- QR is a convenience link — not proof of ownership, not forgery-proof

---

## Demo Data Management

**Module:** `modules/demo.py`

### CLI Commands

| Command | Description |
|---|---|
| `init` | Create missing tables + audit chain state (safe to rerun) |
| `seed-demo` | Idempotent: create demo accounts + 5 synthetic applications at different lifecycle stages |
| `verify` | Verify the audit chain and report results |
| `reset-demo` | Delete `land_records_demo.db` (requires `--confirm-demo-reset`) |

### Safety Guards

- Requires `LAND_RECORDS_ENVIRONMENT=development`
- Refuses non-SQLite databases
- Reset restricted to filename `land_records_demo.db`
- Seed creates a synthetic PDF with PyMuPDF containing clearly labeled
  fabricated data
- Audit chain verified after seeding; seed fails if chain is invalid

---

## Test Architecture

13 pytest modules covering all core logic:

| Test Module | Coverage Area |
|---|---|
| `test_audit.py` | SHA-256 chain append, verify, canonical JSON |
| `test_auth.py` | Login, sessions, role enforcement |
| `test_consistency_score.py` | Weighted scoring, edge cases |
| `test_database.py` | ORM operations, constraints, transactions |
| `test_demo.py` | Demo seeding, reset guards |
| `test_discrepancy.py` | All discrepancy rules and severities |
| `test_document_upload.py` | Upload validation, storage, content checks |
| `test_field_validation.py` | Field format rules, required fields |
| `test_ocr.py` | OCR pipeline, preprocessing, extraction |
| `test_officer_review.py` | Full review workflow, decisions, notifications |
| `test_parcel_map.py` | GeoJSON validation, coordinate checks |
| `test_public_qr.py` | QR generation, URL building |
| `test_submission.py` | Submit, resubmit, version snapshots |

All tests use in-memory SQLite databases and synthetic data. No Tesseract
installation or external services required.

---

## Security Design Decisions

| Decision | Rationale |
|---|---|
| Argon2id for passwords | Current best-practice KDF; resistant to GPU/ASIC attacks |
| Server-side sessions | Tokens not stored client-side beyond Streamlit session state; no JWT attack surface |
| Opaque upload filenames | UUID-hex keys prevent path traversal; originals stored separately in DB |
| File content validation | Magic bytes + full parser load prevents extension spoofing |
| 0600 file permissions | Upload directory and files restricted to server process |
| Upload dir outside `static/` | Prevents accidental public serving of private documents |
| Canonical JSON for hashing | Deterministic serialization ensures hash reproducibility |
| No automatic decisions | OCR confidence and consistency scores never trigger approval or rejection |
| Audit chain pre-verification | Every append verifies the existing chain first, rejecting writes to corrupted chains |
| Role-scoped queries | `AuthService` enforces role checks before any data access |
