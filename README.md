# AI-Powered Land Record Management & Verification System

> **Hackathon Prototype** — A full-stack digitization pipeline for land records:
> document upload → OCR extraction → human review → officer approval → public
> verification with SHA-256 audit chain tamper-evidence.

---

## ✨ Key Features

| Capability | Description |
|---|---|
| **Document OCR Pipeline** | Upload PDF / PNG / JPG → PyMuPDF rendering → OpenCV preprocessing → Tesseract OCR → regex-based field extraction with per-field confidence |
| **Three-Portal Architecture** | Landowner portal, Government Officer portal, Public Verification page — each with role-scoped access |
| **Deterministic Discrepancy Engine** | Compares OCR vs. owner-corrected values, cross-references active applications and approved records by survey number / location / area |
| **Consistency Indicator** | Weighted 0–100 score (OCR confidence, required completeness, reference consistency, audit integrity) for officer review prioritization |
| **SHA-256 Audit Chain** | Append-only, hash-linked event log with deterministic canonical JSON — verifiable from genesis through every approval |
| **Approved Record Registry** | Searchable, paginated, with immutable version history, field comparisons, QR-coded public verification links, and optional GeoJSON parcel maps |
| **Parcel Map Visualization** | Folium maps with OpenStreetMap and Esri satellite basemaps; GeoJSON Polygon / MultiPolygon validation |

---

## 📋 Requirements

| Requirement | Version |
|---|---|
| Python | ≥ 3.12 |
| [uv](https://docs.astral.sh/uv/) | Latest |
| Tesseract OCR (native) | Any recent release — _optional; manual entry works without it_ |

---

## 🚀 Quick Start (Local Demo)

### 1. Install dependencies

```sh
uv sync
```

### 2. Install Tesseract OCR (optional, for document scanning)

```sh
# macOS
brew install tesseract

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install tesseract-ocr
```

On Windows, install from [UB Mannheim's Tesseract distribution](https://github.com/UB-Mannheim/tesseract/wiki)
and ensure `tesseract --version` works in your terminal.

If Tesseract is unavailable, uploads are still preserved and the manual-entry
form is shown.

### 3. Seed the demo database

**macOS / Linux (Bash/Zsh):**

```sh
export LAND_RECORDS_ENVIRONMENT=development
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"
export LAND_RECORDS_UPLOAD_DIRECTORY="./data/demo_private_uploads"

uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
uv run python -m modules.demo verify
```

**Windows (PowerShell):**

```powershell
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
$env:LAND_RECORDS_UPLOAD_DIRECTORY = ".\data\demo_private_uploads"

uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
uv run python -m modules.demo verify
```

The seeder is **idempotent** — rerunning it creates only missing records and
resumes a partially completed seed without duplication.

### 4. Start the three portals

Run each in a **separate terminal** with the same environment variables set above.

| Portal | Command | Default URL |
|---|---|---|
| Public Verification | `uv run streamlit run app.py --server.port 8501` | http://localhost:8501 |
| Landowner Portal | `uv run streamlit run landowner_app.py --server.port 8502` | http://localhost:8502 |
| Officer Portal | `uv run streamlit run officer_app.py --server.port 8503` | http://localhost:8503 |

Add `export PUBLIC_VERIFICATION_BASE_URL="http://localhost:8501"` (or the
PowerShell equivalent) before starting if you want QR codes to resolve correctly.

### 5. Demo login credentials

| Role | Email | Password |
|---|---|---|
| Landowner | `demo.landowner@example.test` | `LandDemo-2026!` |
| Officer | `demo.officer@example.test` | `OfficerDemo-2026!` |

> **⚠️ These are synthetic, local-only demo accounts.** Never use them in a deployment.

---

## 🎬 Hackathon Demo Walkthrough

The seeder creates five applications at different lifecycle stages:

| Application Number | Status | Purpose |
|---|---|---|
| `DEMO-2026-WALKTHROUGH` | Draft | Interactive walkthrough starting point |
| `DEMO-2026-REVIEW` | Under review | Shows officer review queue |
| `DEMO-2026-CORRECTIONS` | Corrections requested | Demonstrates correction cycle |
| `DEMO-2026-APPROVED` | Approved | Public registry + audit verification |
| `DEMO-2026-REJECTED` | Rejected | Shows rejection workflow |

### Recommended demo flow

1. **Landowner Portal** → Sign in → Open `DEMO-2026-WALKTHROUGH` → Review OCR
   results → Correct fields → Submit.
2. **Officer Portal** → Sign in → Open the newly submitted application from the
   Review Queue → Start Review → Request corrections with an explanation.
3. **Landowner Portal** → Check notifications → Open the application → Revise
   fields per officer instructions → Resubmit.
4. **Officer Portal** → Review the resubmission → Approve.
5. **Public Verification** → Enter the new public record ID → View approved
   fields and version history → Click **Verify Audit Integrity**.
6. **Officer Portal** → Open the Audit Explorer → Verify the complete chain.

---

## 🗂️ Project Structure

```
├── app.py                      # Public verification Streamlit entrypoint
├── landowner_app.py            # Landowner portal Streamlit entrypoint
├── officer_app.py              # Officer portal Streamlit entrypoint
├── modules/
│   ├── database.py             # SQLAlchemy ORM models, DB init, repository
│   ├── auth.py                 # AuthService — sessions, workflows, business logic
│   ├── auth_ui.py              # Shared login/logout Streamlit components
│   ├── audit.py                # SHA-256 hash chain: append, verify, CanonicalJSON
│   ├── ocr.py                  # PDF/image → Tesseract OCR pipeline
│   ├── extraction.py           # Regex field extraction from OCR lines
│   ├── field_validation.py     # Field format and required-field validation
│   ├── discrepancy.py          # Deterministic discrepancy detection rules
│   ├── consistency_score.py    # Weighted 0–100 consistency indicator
│   ├── document_storage.py     # Private document storage with validation
│   ├── public_qr.py            # QR code generation for public verification
│   ├── parcel_map.py           # GeoJSON validation + Folium map rendering
│   ├── registry_ui.py          # Shared approved registry UI components
│   └── demo.py                 # Demo data seeding and management CLI
├── tests/                      # 13 pytest test modules
├── pyproject.toml              # Project metadata, dependencies, pytest config
├── uv.lock                     # Locked dependency versions
├── ARCHITECTURE.md             # Detailed architecture documentation
└── GUIDE.md                    # Comprehensive demo & usage guide
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LAND_RECORDS_DATABASE_URL` | `sqlite:///./data/land_records.db` | SQLAlchemy database URL |
| `LAND_RECORDS_ENVIRONMENT` | _(none)_ | Set to `development` for demo commands |
| `LAND_RECORDS_UPLOAD_DIRECTORY` | `./data/private_uploads` | Private document storage path |
| `LAND_RECORDS_MAX_UPLOAD_BYTES` | `10485760` (10 MiB) | Maximum upload file size |
| `LAND_RECORDS_REVIEW_POLICY` | `assigned_or_unassigned` | Officer queue scope (`assigned_only` or `assigned_or_unassigned`) |
| `OCR_LOW_CONFIDENCE_THRESHOLD` | `60` | Tesseract confidence % below which fields are flagged |
| `RECORD_CONSISTENCY_WEIGHTS` | `{"ocr_confidence":0.25,...}` | JSON object of scoring weights |
| `PUBLIC_VERIFICATION_BASE_URL` | `http://localhost:8501` | Base URL embedded in QR codes |

---

## 🧪 Run Tests

```sh
uv run pytest
```

All 13 test modules use in-memory SQLite databases and synthetic data — no
Tesseract installation or external services required.

---

## 🔄 Reset the Demo Database

Stop all portals first, then:

```sh
export LAND_RECORDS_ENVIRONMENT=development
export LAND_RECORDS_DATABASE_URL="sqlite:///./data/land_records_demo.db"

uv run python -m modules.demo reset-demo --confirm-demo-reset
uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
```

Reset is restricted to a file named `land_records_demo.db` and refuses other
database names and non-SQLite databases.

---

## 🔐 Security & Limitations

| Property | Implementation |
|---|---|
| **Password hashing** | Argon2id via `argon2-cffi` |
| **Session tokens** | Cryptographic random tokens stored server-side |
| **Document storage** | Opaque UUID filenames, 0600 permissions, outside `static/` |
| **Upload validation** | Extension + MIME + magic-byte + parser verification |
| **Audit chain** | SHA-256 hash-linked, append-only — **tamper-evidence, not tamper-proof** |
| **No blockchain** | Uses a conventional relational database; a DBA can recompute the chain |
| **OCR limitations** | Tesseract accuracy depends on document quality; confidence is not correctness |
| **Human decisions** | Every approval/rejection requires an explicit officer action — nothing is automated |

> This is a **prototype for demonstration**. It is not a production system for
> government deployment and does not establish legal ownership.

---

## 🔗 Further Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Detailed system architecture, data
  model, module responsibilities, and data flow diagrams.
- **[GUIDE.md](GUIDE.md)** — Step-by-step demo guide with troubleshooting,
  feature deep-dives, and judge-ready talking points.

---

## 📦 Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Package Manager | uv |
| Web Framework | Streamlit |
| ORM / Database | SQLAlchemy + SQLite (PostgreSQL-compatible) |
| OCR | Tesseract (via pytesseract) + OpenCV + PyMuPDF |
| Authentication | Argon2id hashing, server-side session tokens |
| Maps | Folium + streamlit-folium |
| QR Codes | qrcode (PIL backend) |
| Validation | Pydantic, regex-based extraction |
| Testing | pytest |
