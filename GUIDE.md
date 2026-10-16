# Operations & Live Demonstration Guide

> **Operational manual and judge demonstration script** for the AI-Powered Land Record
> Management & Verification System. This guide provides exact commands, step-by-step
> setup procedures, pre-demo checklists, a timed 5–7 minute judging walkthrough,
> troubleshooting solutions, and reset instructions.

---

## Table of Contents

- [Section A — Before You Start](#section-a--before-you-start)
- [Section B — First-Time Setup](#section-b--first-time-setup)
- [Section C — Starting the Application](#section-c--starting-the-application)
- [Section D — Demo Preparation Checklist](#section-d--demo-preparation-checklist)
- [Section E — Judge Demonstration Script (5–7 Minutes)](#section-e--judge-demonstration-script-57-minutes)
- [Section F — Sample Data & Demo Credentials](#section-f--sample-data--demo-credentials)
- [Section G — Automated Testing & Verification](#section-g--automated-testing--verification)
- [Section H — Troubleshooting & Diagnostics](#section-h--troubleshooting--diagnostics)
- [Section I — Reset and Cleanup Procedures](#section-i--reset-and-cleanup-procedures)
- [Section J — Final Quick-Start Card](#section-j--final-quick-start-card)

---

## Section A — Before You Start

### 1. Prerequisites

The application is built in Python 3.12 and uses modern Python tooling. Verify that your host machine has the following tools installed before beginning:

| Dependency | Required Version | Role in System | Mandatory? |
|---|---|---|---|
| **Python** | `3.12.x` (enforced via `.python-version`) | Runtime environment | **Yes** |
| **uv** | Latest release (`≥ 0.4.0`) | Project packaging & virtual environment | **Yes** |
| **Git** | Any modern release | Version control & repository synchronization | **Yes** |
| **Tesseract OCR** | `≥ 5.0.0` (native binary) | Native optical character recognition engine | *Optional* (manual fallback exists) |
| **SQLite** | Bundled with Python 3.12 | Local relational storage & audit ledger | **Yes** (standard library) |

> [!NOTE]
> If native Tesseract is not installed on your system, the application remains fully functional: uploaded documents are stored privately and the landowner is presented with the manual-entry and review form to input record attributes.

---

### 2. Opening the Project Terminal

Open a terminal or command prompt and navigate directly to the root of the cloned repository:

```sh
# macOS / Linux
cd /Users/prateek/Desktop/AAAHHH

# Windows (PowerShell)
cd C:\path\to\AAAHHH
```

Ensure you remain in this root directory for all subsequent setup and execution commands.

---

### 3. Verifying Local Tooling

Run the diagnostic commands below for your operating system to confirm that all prerequisites are installed and discoverable on your `PATH`.

#### macOS Verification

```sh
python3 --version
# Expected output: Python 3.12.x

uv --version
# Expected output: uv 0.x.x

git --version
# Expected output: git version 2.x.x

tesseract --version
# Expected output: tesseract 5.x.x (or command not found if optional OCR is omitted)
```

#### Linux (Debian / Ubuntu) Verification

```sh
python3 --version
uv --version
git --version
tesseract --version
```

#### Windows (PowerShell) Verification

```powershell
python --version
uv --version
git --version
tesseract --version
```

---

### 4. Installing Missing Dependencies

If any tool is missing from your environment, install it using the standard package manager for your platform:

#### Installing `uv` (Fast Python Package Manager)

```sh
# macOS / Linux (curl installer)
curl -LsSf https://astral.sh/uv/install.sh | sh

# macOS via Homebrew
brew install uv

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

#### Installing Native Tesseract OCR (Optional for Scanning)

```sh
# macOS (Homebrew)
brew install tesseract

# Debian / Ubuntu Linux
sudo apt-get update && sudo apt-get install -y tesseract-ocr

# Windows
# Download and install UB-Mannheim's installer:
# https://github.com/UB-Mannheim/tesseract/wiki
# Ensure C:\Program Files\Tesseract-OCR is added to your system PATH
```

---

## Section B — First-Time Setup

Follow these exact steps in order. Every command is executed from the repository root directory.

### Step 1: Synchronize Project Dependencies

Run `uv sync` to create an isolated Python 3.12 virtual environment in `.venv/` and install all pinned packages from `pyproject.toml` and `uv.lock`:

```sh
uv sync
```

**What this command does:**
- Resolves all 13 core runtime and testing libraries (Streamlit, SQLAlchemy, PyMuPDF, OpenCV, pytesseract, Argon2-cffi, Folium, Pydantic, pytest, etc.).
- Compiles native C-extensions (such as Argon2 password hashing and PyMuPDF bindings) cleanly inside `.venv`.
- Ensures zero pollution of your global Python installation.

---

### Step 2: Configure Environment Variables

The application relies on explicit environment variables for database location, private upload storage, and environment mode.

#### macOS / Linux (Bash / Zsh)

Add these exports to your active shell session (or save them to a `.env` file):

```sh
export LAND_RECORDS_ENVIRONMENT="development"
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"
export LAND_RECORDS_UPLOAD_DIRECTORY="./data/demo_private_uploads"
export PUBLIC_VERIFICATION_BASE_URL="http://localhost:8501"
```

#### Windows (PowerShell)

```powershell
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
$env:LAND_RECORDS_UPLOAD_DIRECTORY = ".\data\demo_private_uploads"
$env:PUBLIC_VERIFICATION_BASE_URL = "http://localhost:8501"
```

**Variable Reference & Safe Local Defaults:**

| Variable | Recommended Demo Value | Description |
|---|---|---|
| `LAND_RECORDS_ENVIRONMENT` | `development` | Enforces safety gates; prevents running demo seeder in production |
| `LAND_RECORDS_DATABASE_URL` | `sqlite:///./data/land_records_demo.db` | Path to the demo SQLite relational database |
| `LAND_RECORDS_UPLOAD_DIRECTORY` | `./data/demo_private_uploads` | Secure local directory for uploaded deeds and PDFs |
| `PUBLIC_VERIFICATION_BASE_URL` | `http://localhost:8501` | Base URL encoded into public verification QR codes |
| `OCR_LOW_CONFIDENCE_THRESHOLD` | `60` | Percentage threshold below which OCR fields are flagged |

> [!IMPORTANT]
> Never set production secrets or real personal credentials. The application operates entirely offline with local SQLite storage and synthetic demo records.

---

### Step 3: Initialize the Database Schema

Create all required database tables, indexes, and constraints:

```sh
uv run python -m modules.demo init
```

**Expected output:**
```text
Database schema initialized at sqlite:///./data/land_records_demo.db.
```

**What this command does:**
- Inspects the SQLAlchemy metadata across all ORM models.
- Generates tables: `users`, `applications`, `application_versions`, `documents`, `audit_events`, `land_records`, `land_record_versions`, `notifications`.
- Configures SQLite WAL (Write-Ahead Logging) mode and foreign key enforcement.

---

### Step 4: Seed Demo Accounts and Synthetic Applications

Populate the database with predictable synthetic test accounts and realistic land-record applications:

```sh
uv run python -m modules.demo seed-demo
```

**Expected output:**
```text
DEMO-2026-WALKTHROUGH: draft
DEMO-2026-REVIEW: under_review
DEMO-2026-CORRECTIONS: corrections_requested
DEMO-2026-APPROVED: approved
DEMO-2026-REJECTED: rejected
Audit chain valid: True (7 events).
Local demo accounts: demo.landowner@example.test / LandDemo-2026!; demo.officer@example.test / OfficerDemo-2026!
```

**What this command does:**
1. Generates a synthetic sample deed PDF at `data/demo_documents/synthetic_land_record.pdf` with clean vector text.
2. Creates two demo user accounts with Argon2id password hashing:
   - Citizen Landowner: `demo.landowner@example.test` / `LandDemo-2026!`
   - Government Officer: `demo.officer@example.test` / `OfficerDemo-2026!`
3. Creates five applications representing all distinct stages of the administrative lifecycle.
4. Generates an append-only SHA-256 audit chain starting from the canonical genesis block.
5. Operates **idempotently**: rerunning the command checks existing application numbers and does not duplicate data.

---

### Step 5: Verify Audit Chain Integrity

Confirm that the seeded audit chain is cryptographically continuous from genesis to head:

```sh
uv run python -m modules.demo verify
```

**Expected output:**
```text
Audit chain valid: True (7 events).
```

---

## Section C — Starting the Application

The system uses three specialized Streamlit web portals. To demonstrate the complete human-in-the-loop workflow, launch each portal in a separate terminal window with the environment variables configured.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        SHARED SQLITE LEDGER                            │
│                 (sqlite:///./data/land_records_demo.db)                │
└───────▲────────────────────────▲───────────────────────────────▲───────┘
        │                        │                               │
        │                        │                               │
┌───────┴──────────────┐ ┌───────┴──────────────┐ ┌──────────────┴───────┐
│  LANDOWNER PORTAL    │ │  OFFICER PORTAL      │ │  PUBLIC VERIFICATION │
│  landowner_app.py    │ │  officer_app.py      │ │  app.py              │
│  Port: 8502          │ │  Port: 8503          │ │  Port: 8501          │
│  Citizen Ingestion   │ │  Gov Review & Audit  │ │  Public Transparency │
└──────────────────────┘ └──────────────────────┘ └──────────────────────┘
```

### Multi-Terminal Launch Commands

#### Terminal 1: Public Verification Portal (Port 8501)

```sh
# Terminal 1
export LAND_RECORDS_ENVIRONMENT="development"
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"
export LAND_RECORDS_UPLOAD_DIRECTORY="./data/demo_private_uploads"
export PUBLIC_VERIFICATION_BASE_URL="http://localhost:8501"

uv run streamlit run app.py --server.port 8501
```

- **Target URL:** [http://localhost:8501](http://localhost:8501)
- **Role:** Citizen and public verification of approved land titles, QR verification targets, and cryptographic integrity checks.

---

#### Terminal 2: Landowner Citizen Portal (Port 8502)

```sh
# Terminal 2
export LAND_RECORDS_ENVIRONMENT="development"
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"
export LAND_RECORDS_UPLOAD_DIRECTORY="./data/demo_private_uploads"
export PUBLIC_VERIFICATION_BASE_URL="http://localhost:8501"

uv run streamlit run landowner_app.py --server.port 8502
```

- **Target URL:** [http://localhost:8502](http://localhost:8502)
- **Role:** Citizen login, document upload, OCR review, correction preview, application tracking, and resubmission.

---

#### Terminal 3: Government Officer Portal (Port 8503)

```sh
# Terminal 3
export LAND_RECORDS_ENVIRONMENT="development"
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"
export LAND_RECORDS_UPLOAD_DIRECTORY="./data/demo_private_uploads"
export PUBLIC_VERIFICATION_BASE_URL="http://localhost:8501"

uv run streamlit run officer_app.py --server.port 8503
```

- **Target URL:** [http://localhost:8503](http://localhost:8503)
- **Role:** Department dashboard, review queue, discrepancy engine inspection, consistency score, approval/rejection/correction decisions, and full audit ledger explorer.

---

### Operational Tips for Running Portals

1. **How to tell if a portal started successfully:**
   Streamlit prints the following banner in the terminal:
   ```text
   You can now view your Streamlit app in your browser.
   Local URL: http://localhost:850X
   ```
2. **Stopping a server:**
   Press `Ctrl + C` in the respective terminal window.
3. **Diagnosing Port Conflicts:**
   If a port is already occupied (e.g., error `Port 8502 is already in use`), identify and terminate the old process:
   ```sh
   # macOS / Linux
   lsof -i :8502
   kill -9 <PID>

   # Windows (PowerShell)
   netstat -ano | findstr :8502
   Stop-Process -Id <PID> -Force
   ```
   Alternatively, run the app on an alternative port using `--server.port 8512`.
4. **Why Shared Database Configuration is Critical:**
   Streamlit runs each entrypoint as an independent Python process. All three processes read and write to the same SQLite database file specified by `LAND_RECORDS_DATABASE_URL`. If one terminal defaults to `./data/land_records.db` while another uses `./data/land_records_demo.db`, applications submitted in the Landowner portal will **not** appear in the Officer portal!

---

## Section D — Demo Preparation Checklist

Complete this checklist 15 minutes before the evaluation session with the judges:

- [ ] **1. Code Repository:** Confirmed on `main` branch with clean git working tree (`git status`).
- [ ] **2. Dependencies Synced:** Ran `uv sync` to ensure virtual environment packages are up-to-date.
- [ ] **3. OCR Engine Ready:** Checked `tesseract --version` (or prepared the fallback manual workflow).
- [ ] **4. Database Seeded:** Ran `uv run python -m modules.demo init` followed by `seed-demo`.
- [ ] **5. Audit Chain Validated:** Ran `uv run python -m modules.demo verify` and confirmed `Audit chain valid: True`.
- [ ] **6. Portals Active:** Started all three terminals (ports 8501, 8502, 8503).
- [ ] **7. Browser Tabs Arranged:** Prepared three adjacent browser tabs:
  - Tab 1: `http://localhost:8502` (Landowner Citizen Portal)
  - Tab 2: `http://localhost:8503` (Government Officer Portal)
  - Tab 3: `http://localhost:8501` (Public Verification Page)
- [ ] **8. Demo Credentials Stored:** Verified login credentials work in both portals.
- [ ] **9. Sample Document Available:** Confirmed `data/demo_documents/synthetic_land_record.pdf` exists and is accessible.
- [ ] **10. Privacy Compliance:** Confirmed that NO real deeds, real citizen names, or actual Aadhaar numbers exist in the database.
- [ ] **11. Map Layer Verification:** Opened an approved record with GeoJSON to confirm Folium map tiles render properly.
- [ ] **12. Display & Zoom:** Set browser zoom to 90% or 100% so tables, metrics, and split-screen viewers display without clipping.
- [ ] **13. Test Suite Pass:** Verified that all 124 tests pass via `uv run pytest`.
- [ ] **14. Offline Verification:** Confirmed that the database, OCR, and audit verification function completely offline without internet access.
- [ ] **15. Backup Demonstration Record:** Kept pre-seeded record `DEMO-2026-APPROVED` ready for instant lookup if time runs short during judging.

---

## Section E — Judge Demonstration Script (5–7 Minutes)

A structured, timed presentation script designed specifically for Smart India Hackathon (SIH) judges.

```
TIMELINE (5-7 MINUTES):
0:00 ─── 0:30 ─── Problem & Architecture Pitch
0:30 ─── 1:30 ─── Landowner Upload, OCR & Citizen Validation
1:30 ─── 3:00 ─── Officer Review, Consistency Score & Discrepancy Checks
3:00 ─── 4:15 ─── Dynamic Correction Cycle & Resubmission
4:15 ─── 5:30 ─── Officer Approval, Public Registry & QR Verification
5:30 ─── 6:30 ─── SHA-256 Audit Chain Verification & Q&A
```

---

### Step 1: The Problem & Architecture (0:00 – 0:30)

- **Actor:** Presenter
- **Visual:** README / Architecture Diagram or Home Dashboard of Landowner Portal ([http://localhost:8502](http://localhost:8502))
- **What to say:**
  > "Honorable judges, land record administration across India suffers from three critical bottlenecks: manual data entry errors, lack of citizen transparency during review, and vulnerability of historical records to silent tampering.
  >
  > We have engineered an AI-powered, human-in-the-loop land record verification system. It features an automated OCR extraction pipeline, a deterministic discrepancy engine that cross-references government registries, role-scoped portals for citizens and officers, and an append-only SHA-256 cryptographic audit chain that provides mathematical tamper-evidence for every single administrative action."
- **Technical capability demonstrated:** Full-stack system topology, human-in-the-loop design principle.

---

### Step 2: Citizen Document Upload & OCR Ingestion (0:30 – 1:30)

- **Actor:** Citizen Landowner
- **Portal:** Landowner Portal ([http://localhost:8502](http://localhost:8502))
- **Actions:**
  1. Sign in with `demo.landowner@example.test` and `LandDemo-2026!`.
  2. The Overview dashboard displays active application metrics (Drafts, In review, Approved).
  3. Click **Upload Land Document** on the sidebar.
  4. Upload `data/demo_documents/synthetic_land_record.pdf` (or open existing draft `DEMO-2026-WALKTHROUGH`).
- **What appears on screen:**
  - Original document preview rendered cleanly via PyMuPDF.
  - OCR extraction status: **Completed**.
  - Recognized fields with individual engine confidence percentages:
    - *Owner name:* Demo Landowner (`92%`)
    - *District:* Sample District (`88%`)
    - *Village:* Sample Village (`85%`)
    - *Survey or plot number:* SY-DEMO-101 / SY-DEMO-505 (`91%`)
    - *Land area:* 2.5 acres (`89%`)
- **What to say to judges:**
  > "Notice our design philosophy: AI and OCR suggest data, but never dictate official records. The citizen reviews extracted fields in a separate version draft. They can correct any transcription issue, add missing details, and preview the normalized data before submission."
- **Actions to submit:**
  1. Click **Preview corrected information** (runs format validation on area units and dates).
  2. Click **Save as draft version**.
  3. Click **Submit application for verification**.
- **What appears:** Application status updates to **Submitted** with an immutable version 1 snapshot.
- **Fallback if OCR is slow:** Open existing draft `DEMO-2026-WALKTHROUGH` where fields are already extracted.

---

### Step 3: Government Officer Review & Discrepancy Engine (1:30 – 3:00)

- **Actor:** Government Revenue Officer
- **Portal:** Government Officer Portal ([http://localhost:8503](http://localhost:8503))
- **Actions:**
  1. Sign in with `demo.officer@example.test` and `OfficerDemo-2026!`.
  2. Navigate to **Review Queue**.
  3. Locate the submitted application (or pre-seeded `DEMO-2026-REVIEW`).
  4. Click **Assign to me and start review**.
  5. The portal transitions to the **Application Review** workspace with 7 detailed inspection tabs.
- **Walk through the Key Tabs:**
  - **Tab 1 (OCR & warnings):** Displays a comparison table showing *OCR result*, *OCR engine confidence*, *Landowner-corrected value*, and *Review warnings*.
  - **Tab 3 (Original documents):** Side-by-side read-only document viewer showing the original PDF deed.
  - **Tab 5 (Consistency indicator):** Shows the explainable 0–100 score (e.g., `85.4 / 100`). Expand the breakdown to show:
    - OCR confidence component (`25%` weight)
    - Required-field completeness (`25%` weight)
    - Reference consistency with existing registry (`30%` weight)
    - Audit ledger integrity (`20%` weight)
  - **Tab 6 (Discrepancy checks):** Highlights deterministic rules. If a survey number overlaps with another active application or an approved record, a clear warning appears (e.g., `possible_duplicate_application`).
  - **Tab 7 (Parcel map):** Renders an interactive Folium map showing the boundary polygon with satellite and street layers.
- **What to say to judges:**
  > "Instead of forcing officers to read through pages of unstructured text, our discrepancy engine automatically checks for duplicate survey filings, area format discrepancies, and registry conflicts. Furthermore, the 0-100 consistency indicator provides an explainable heuristic to prioritize suspicious or incomplete applications. Every rule is transparent and deterministic."

---

### Step 4: The Dynamic Correction Cycle (3:00 – 4:15)

- **Actor:** Officer & Landowner
- **Actions in Officer Portal:**
  1. Scroll down to **Record a review decision**.
  2. Select **Request corrections**.
  3. In the remarks box, enter: `Please verify the exact parcel land area unit and specify the North boundary landmark.` (minimum 10 characters required).
  4. Check the confirmation checkbox: *"I confirm this request corrections decision"*.
  5. Click **Confirm and save decision**.
- **Actions in Landowner Portal ([http://localhost:8502](http://localhost:8502)):**
  1. Switch to the Landowner tab and click **Notifications**.
  2. Notice the instant alert: *"Officer requested corrections on your application"*.
  3. Navigate to **My Applications**, select the application, and inspect the officer's remarks.
  4. Edit the land area field to `2.75 acres`.
  5. Check *"I confirm that application is ready for review again"* and click **Save changes and resubmit for review**.
- **What to say to judges:**
  > "This demonstrates a real-world administrative workflow. Rather than outright rejecting an application for a minor typo, the officer initiates a structured correction cycle. A new version is created, preserving the previous version in the audit history for complete legal accountability."

---

### Step 5: Officer Approval & Public Registry (4:15 – 5:30)

- **Actor:** Officer & Citizen
- **Actions in Officer Portal:**
  1. Return to the Officer portal and refresh the application.
  2. Version 2 is now under review with updated owner fields.
  3. Select **Approve**, enter approval notes (`All boundary references verified against village cadastral map.`), check confirmation, and click **Confirm and save decision**.
  4. Application status updates to **Approved**. A new public record is officially minted.
- **Actions in Public Verification Portal ([http://localhost:8501](http://localhost:8501)):**
  1. Switch to the Public Portal.
  2. Enter the public record ID (or use `DEMO-2026-APPROVED`).
  3. Click **Open approved record**.
- **What appears on screen:**
  - Approved record card with current version number and official SHA-256 record hash.
  - Publicly released fields: District, Village, Survey Number, Land Area (owner personal details are protected).
  - Interactive parcel map with GeoJSON boundaries.
  - Immutable version history showing timestamps and authorization officers.
- **What to say to judges:**
  > "Once approved, the record is immediately accessible on the public verification portal. Anyone—banks, buyers, or courts—can verify land details without needing an account. Privacy is built-in: sensitive personal information is redacted, while parcel coordinates and survey parameters remain completely transparent."

---

### Step 6: SHA-256 Audit Ledger & Cryptographic Verification (5:30 – 6:30)

- **Actor:** Presenter
- **Actions in Public Portal:**
  1. Scroll to the bottom of the public record page.
  2. Click **Verify Audit Integrity**.
  3. The system dynamically validates the hash chain and displays:
     > ✅ *"Audit integrity check passed: the stored events are consistent with the current hash chain."*
- **Actions in Officer Portal:**
  1. Navigate to **Audit Explorer** on the sidebar.
  2. Click **Verify Audit Chain**.
  3. The explorer reports: `Audit chain verified: all 7+ event(s) link from the deterministic genesis and their hashes are consistent.`
  4. Expand any event to inspect its cryptographic payload:
     - `event_type`: `application_approved`
     - `event_hash`: `a7f9...`
     - `previous_event_hash`: `3c1b...`
     - Deterministic canonical JSON payload.
- **What to say to judges (Closing Pitch):**
  > "To conclude, our system does not rely on buzzwords. We implemented a cryptographically linked SHA-256 audit ledger directly on top of our relational database. Every event—upload, review start, correction request, resubmission, and approval—is hashed with canonical JSON and linked to its parent hash.
  >
  > If a corrupt database administrator attempts to quietly alter an applicant's survey number or date in SQL, the parent hash chain immediately breaks, and the public verification portal alerts the citizen. This delivers mathematical tamper-evidence for modern e-governance."

---

## Section F — Sample Data & Demo Credentials

All test accounts and sample records are strictly **synthetic** and configured for local demonstration purposes only.

### 1. Demo User Accounts

| Role | Email | Password | Display Name | Permissions & Scope |
|---|---|---|---|---|
| **Landowner** (Citizen) | `demo.landowner@example.test` | `LandDemo-2026!` | Demo Landowner | Can upload deeds, edit draft fields, submit applications, view notifications, and browse approved registry. |
| **Officer** (Government) | `demo.officer@example.test` | `OfficerDemo-2026!` | Demo Officer | Can inspect review queue, claim applications, request corrections, approve/reject titles, and view audit ledger. |

> [!CAUTION]
> These credentials are hardcoded into the demo seeder (`modules/demo.py`) with Argon2id hashing. Never use these credentials or accounts in any staging or production deployment.

---

### 2. Pre-Seeded Applications Lifecycle Matrix

The idempotent seeder creates five applications designed to demonstrate different lifecycle stages without requiring manual data entry:

| Application Number | Lifecycle Status | Survey Number | Purpose in Judge Demo |
|---|---|---|---|
| `DEMO-2026-WALKTHROUGH` | `draft` | `SY-DEMO-505` | Ready for live upload, OCR review, correction, and submission. |
| `DEMO-2026-REVIEW` | `under_review` | `SY-DEMO-101` | Assigned to officer queue; ready for immediate discrepancy inspection. |
| `DEMO-2026-CORRECTIONS` | `corrections_requested` | `SY-DEMO-202` | Shows citizen notification and resubmission workflow. |
| `DEMO-2026-APPROVED` | `approved` | `SY-DEMO-303` | Instant public record lookup, QR resolution, and audit integrity check. |
| `DEMO-2026-REJECTED` | `rejected` | `SY-DEMO-404` | Demonstrates audit trail preservation when an application is denied. |

---

### 3. Synthetic Document Assets

- **Generated File Path:** `data/demo_documents/synthetic_land_record.pdf`
- **Document Properties:** Single-page PDF generated via PyMuPDF containing high-contrast vector text.
- **Synthetic Content Fields:**
  - Owner: Demo Landowner
  - District: Sample District
  - Village: Sample Village
  - Survey Number: SY-DEMO-101
  - Land Area: 2.5 acres
  - Document Type: Sale deed
  - Document Date: 2026-01-15
- **Privacy Assurance:** Contains no real Aadhaar numbers, real survey deed numbers, or genuine personal identifiable information (PII).

---

## Section G — Automated Testing & Verification

The repository includes a comprehensive automated test suite with **124 unit and integration tests** across 13 test modules.

### Running the Full Test Suite

Execute pytest via `uv`:

```sh
uv run pytest
```

**Expected output:**
```text
============================== test session starts ==============================
platform darwin -- Python 3.12.x, pytest-9.x.x
rootdir: /Users/prateek/Desktop/AAAHHH
configfile: pyproject.toml
collected 124 items

tests/test_audit.py .....                                                [  4%]
tests/test_auth.py .............                                         [ 14%]
tests/test_consistency_score.py ..........                               [ 22%]
tests/test_database.py .....                                             [ 26%]
tests/test_demo.py ...                                                   [ 29%]
tests/test_discrepancy.py ............                                   [ 38%]
tests/test_document_upload.py ........                                   [ 45%]
tests/test_field_validation.py ...............                           [ 57%]
tests/test_ocr.py ........                                               [ 63%]
tests/test_officer_review.py ......................                      [ 81%]
tests/test_parcel_map.py .......                                         [ 87%]
tests/test_public_qr.py ........                                         [ 93%]
tests/test_submission.py ........                                        [100%]

================== 124 passed, 5 warnings in 61.48s (0:01:01) ==================
```

> [!NOTE]
> All tests execute against an in-memory SQLite database (`sqlite:///:memory:`) and use synthetic fixtures. The test suite does not require native Tesseract or external network connectivity.

---

### Targeted Test Commands

Run individual test suites to demonstrate specific subsystem reliability to judges:

```sh
# 1. SHA-256 Audit Chain & Canonical JSON Verification (5 tests)
uv run pytest tests/test_audit.py -v

# 2. Deterministic Discrepancy Engine & Duplicate Detection (12 tests)
uv run pytest tests/test_discrepancy.py -v

# 3. 0-100 Consistency Indicator Heuristic (10 tests)
uv run pytest tests/test_consistency_score.py -v

# 4. Officer Review Workflow, RBAC & State Transitions (22 tests)
uv run pytest tests/test_officer_review.py -v

# 5. Landowner Document Upload & MIME Validation (8 tests)
uv run pytest tests/test_document_upload.py -v

# 6. GeoJSON Polygon & MultiPolygon Geometry Validation (7 tests)
uv run pytest tests/test_parcel_map.py -v

# 7. QR Code Generation for Public Records (8 tests)
uv run pytest tests/test_public_qr.py -v
```

---

## Section H — Troubleshooting & Diagnostics

Quick-reference diagnostic table for common operational and demo issues:

| # | Problem Symptom | Likely Cause | Diagnostic Command | Solution / Fallback |
|---|---|---|---|---|
| **1** | `uv: command not found` | `uv` is not installed or not in current shell `PATH`. | `which uv` | Re-run `curl -LsSf https://astral.sh/uv/install.sh \| sh` and restart your shell. |
| **2** | Python version mismatch | System Python is older than 3.12. | `uv run python --version` | `uv` automatically downloads Python 3.12 if specified in `.python-version`. Run `uv python install 3.12`. |
| **3** | Tesseract missing or not found | Native binary not installed. | `tesseract --version` | Upload still works! The portal automatically presents the manual-entry review form for the synthetic document. |
| **4** | OCR text is blank or unreadable | Scanned image quality is too low or font is unsupported. | Check `document.ocr_result["status"]` | Use the clean synthetic PDF (`data/demo_documents/synthetic_land_record.pdf`) which yields high confidence. |
| **5** | Port conflict (`address already in use`) | Another Streamlit process is occupying port 8501, 8502, or 8503. | `lsof -i :8502` | Kill the stale process: `kill -9 <PID>` or start on another port: `--server.port 8512`. |
| **6** | Portals show different data | Portals were started with different database environment variables. | `echo $LAND_RECORDS_DATABASE_URL` | Ensure both terminals export `LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"`. |
| **7** | Database missing (`no such table`) | Schema was not initialized before starting the app. | `ls -la data/land_records_demo.db` | Run `uv run python -m modules.demo init` followed by `seed-demo`. |
| **8** | Login fails (`Invalid email or password`) | Typo in email/password or demo database was overwritten. | Re-check credentials | Use `demo.landowner@example.test` / `LandDemo-2026!`. Re-run `seed-demo` if needed. |
| **9** | Document upload rejected | File exceeds 10 MB limit or is not an approved MIME type. | Check file size & extension | Ensure file is a valid PDF, PNG, or JPG under 10 MiB. |
| **10** | Application not in Officer Review Queue | Application was saved as draft but not submitted. | Check Landowner dashboard | Citizen must click **Submit application for verification** in the Landowner portal. |
| **11** | "Save as draft" button disabled | User did not preview corrected information first. | Inspect form buttons | Click **Preview corrected information** first to validate fields; the save button will activate immediately. |
| **12** | Officer decision rejected | Remarks comment is shorter than 10 characters or checkbox unticked. | Review decision error banner | Enter at least 10 characters explaining the decision and tick the confirmation checkbox. |
| **13** | Audit verification fails | Database records were manually edited outside the application ORM. | `uv run python -m modules.demo verify` | Re-seed the clean demo database using the reset procedure in Section I. |
| **14** | Parcel map displays grey box | Host machine has no internet connection to download OpenStreetMap tiles. | Check internet connectivity | The GeoJSON polygon coordinates and validation status remain fully visible and verified offline. |

---

## Section I — Reset and Cleanup Procedures

### 1. Stopping the Application Servers

In each of the three terminal windows, press:
```text
Ctrl + C
```
Confirm the processes terminate cleanly.

---

### 2. Resetting the Demo Database

To return the database to a pristine pre-demo state, use the dedicated reset tool:

```sh
export LAND_RECORDS_ENVIRONMENT="development"
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"

# Reset the SQLite demo file (requires explicit confirmation flag)
uv run python -m modules.demo reset-demo --confirm-demo-reset

# Recreate fresh schema and seed records
uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
uv run python -m modules.demo verify
```

> [!WARNING]
> `reset-demo` deletes `data/land_records_demo.db` and its `-wal` and `-shm` sidecars. It is strictly guarded in code: it refuses to delete any database not named `land_records_demo.db` and rejects non-SQLite database URLs.

---

### 3. Cleaning Temporary Files & Uploads

If you wish to remove uploaded demo deeds and Python bytecode caches:

```sh
# Clean uploaded files created during the demonstration
rm -rf data/demo_private_uploads/*

# Remove Python bytecode cache directories
find . -type d -name "__pycache__" -exec rm -rf {} +
rm -rf .pytest_cache
```

---

## Section J — Final Quick-Start Card

Keep this compact reference sheet open during the hackathon evaluation:

```sh
# ==============================================================================
# AI-POWERED LAND RECORD MANAGEMENT & VERIFICATION SYSTEM — QUICK-START CARD
# ==============================================================================

# 1. SETUP ENVIRONMENT & DEPENDENCIES
cd /Users/prateek/Desktop/AAAHHH
uv sync

# 2. CONFIGURE ENVIRONMENT (Bash / Zsh)
export LAND_RECORDS_ENVIRONMENT="development"
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"
export LAND_RECORDS_UPLOAD_DIRECTORY="./data/demo_private_uploads"
export PUBLIC_VERIFICATION_BASE_URL="http://localhost:8501"

# 3. INITIALIZE & SEED DEMO DATABASE
uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
uv run python -m modules.demo verify

# 4. RUN AUTOMATED TEST SUITE (124 tests)
uv run pytest

# 5. START THREE PORTALS (Separate Terminals)
# Terminal 1: Public Verification Portal
uv run streamlit run app.py --server.port 8501
# URL: http://localhost:8501

# Terminal 2: Landowner Citizen Portal
uv run streamlit run landowner_app.py --server.port 8502
# URL: http://localhost:8502

# Terminal 3: Government Officer Portal
uv run streamlit run officer_app.py --server.port 8503
# URL: http://localhost:8503

# 6. DEMO CREDENTIALS
# Citizen Landowner:  demo.landowner@example.test / LandDemo-2026!
# Government Officer: demo.officer@example.test   / OfficerDemo-2026!

# 7. BACKUP PUBLIC LOOKUP RECORD
# Public Record ID:   DEMO-2026-APPROVED
# ==============================================================================
```
