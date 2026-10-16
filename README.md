# AI-Powered Land Record Management and Verification System

Prototype scaffold for the land record management system.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```sh
uv sync
```

## Local hackathon demo

This repository is a demonstration prototype, not a production-ready system for
government deployment. Demo accounts and seeded data are synthetic and for
local development only. Do not use the demo credentials, database, or sample
records in production.

Use a dedicated SQLite database for the demo. Set the same database URL and
private upload directory in every terminal used for initialization or either
portal. The following commands use PowerShell:

```powershell
uv sync
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
$env:LAND_RECORDS_UPLOAD_DIRECTORY = ".\data\demo_private_uploads"
uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
uv run python -m modules.demo verify
```

`init` is safe to rerun and creates missing tables plus audit-chain state; it is
not a schema-migration command. `seed-demo` can also be rerun: it creates only
missing demo accounts/applications and resumes a
partially created seed without duplicating records. It writes the OCR-readable
synthetic PDF to `data/demo_documents/synthetic_land_record.pdf` and stores
uploaded copies under the configured private upload directory. The seed
includes a draft for the walkthrough, an application awaiting review, an
application requiring corrections, an approved public record, and a rejected
application. The awaiting-decision record is assigned to the demo officer so it
is visible even when the review policy is `assigned_only`. The command verifies
the audit chain before reporting success.

The local-only demo logins are:

| Role | Email | Password |
|---|---|---|
| Landowner | `demo.landowner@example.test` | `LandDemo-2026!` |
| Officer | `demo.officer@example.test` | `OfficerDemo-2026!` |

These fixed credentials are intentionally limited to local SQLite demo
seeding. The seeder requires `LAND_RECORDS_ENVIRONMENT=development`, refuses
non-SQLite databases, and refuses to run in any other configured environment.
Never use these accounts or passwords for a deployment.

### Walkthrough

1. Start the landowner portal and sign in with the demo landowner account.
2. Open the `DEMO-2026-WALKTHROUGH` draft. Review the OCR-extracted values from
   the synthetic PDF, correct any mismatched values, and submit the application.
   If OCR is unavailable, use the manual-entry form; the upload is preserved.
3. In the officer portal, open the new application from the review queue, start
   review, and request corrections with an explanation.
4. Return to the landowner portal, open the correction notification, update the
   requested field(s), and resubmit.
5. In the officer portal, review the resubmission and approve it.
6. Open the newly approved public record from the registry/public verification
   page and run **Verify Audit Integrity**. The seeded approved record can be
   used to demonstrate public verification before completing the walkthrough.

The walkthrough intentionally requires human decisions in the portals:
review, correction, resubmission, and approval are not automatically performed
by the seeder.

### Run all portals

Run each command in a separate PowerShell terminal. Repeat the same database
and upload-directory settings in each terminal; all portals then share the same
database and private document store.

```powershell
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
$env:LAND_RECORDS_UPLOAD_DIRECTORY = ".\data\demo_private_uploads"
$env:PUBLIC_VERIFICATION_BASE_URL = "http://localhost:8501"
uv run streamlit run app.py --server.port 8501
```

```powershell
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
$env:LAND_RECORDS_UPLOAD_DIRECTORY = ".\data\demo_private_uploads"
$env:PUBLIC_VERIFICATION_BASE_URL = "http://localhost:8501"
uv run streamlit run landowner_app.py --server.port 8502
```

```powershell
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
$env:LAND_RECORDS_UPLOAD_DIRECTORY = ".\data\demo_private_uploads"
$env:PUBLIC_VERIFICATION_BASE_URL = "http://localhost:8501"
uv run streamlit run officer_app.py --server.port 8503
```

### Reset the demo database

Stop all portals before resetting. Reset is restricted to a local SQLite file
named `land_records_demo.db`; it refuses other database filenames. The command
prints a warning and does not delete anything unless the explicit confirmation
flag is supplied. **Reset permanently deletes all database contents** in that
file, including any non-demo data someone may have added to it. Uploaded files
in the private upload directory are not deleted.

```powershell
$env:LAND_RECORDS_ENVIRONMENT = "development"
$env:LAND_RECORDS_DATABASE_URL = "sqlite:///./data/land_records_demo.db"
uv run python -m modules.demo reset-demo --confirm-demo-reset
uv run python -m modules.demo init
uv run python -m modules.demo seed-demo
```

Review the warning before running the confirmed reset. Do not point this command
at a production or shared database.

### Troubleshooting

- **`uv` is not recognized:** install uv, reopen the terminal, and retry
  `uv sync`.
- **Login fails:** run `uv run python -m modules.demo seed-demo` against the
  same database URL configured for the portals; seeding does not reset existing
  demo account passwords.
- **The queue or registry looks empty:** check that all portal terminals use
  the same `LAND_RECORDS_DATABASE_URL`, then run
  `uv run python -m modules.demo verify`.
- **OCR reports that Tesseract is missing:** `pytesseract` is only the Python
  wrapper. Install the native Tesseract OCR engine separately (instructions
  below), ensure `tesseract --version` works in the portal terminal, then
  restart the portal. The demo PDF remains available for manual field entry.
- **Reset is refused:** verify the URL points to the dedicated SQLite file
  `data/land_records_demo.db`; reset intentionally refuses other database
  names and non-SQLite databases.

## Initialize the database

The schema uses SQLAlchemy and defaults to a local SQLite database at
`data/land_records.db`. Override the URL with `LAND_RECORDS_DATABASE_URL` when
using another supported SQLAlchemy database:

```sh
uv run python -m modules.database init
```

## Configure development demo accounts

There is no public officer-registration endpoint. Seed accounts only from a
trusted local development shell. Supply two distinct emails and passwords via
environment variables; the seed command requires passwords of at least 12
characters, hashes them with Argon2id, creates only missing accounts, and never
resets an existing account's password.

For example, in a local shell:

```sh
export LAND_RECORDS_DEMO_LANDOWNER_EMAIL=landowner@example.test
read -r -s LAND_RECORDS_DEMO_LANDOWNER_PASSWORD
export LAND_RECORDS_DEMO_LANDOWNER_PASSWORD
export LAND_RECORDS_DEMO_OFFICER_EMAIL=officer@example.test
read -r -s LAND_RECORDS_DEMO_OFFICER_PASSWORD
export LAND_RECORDS_DEMO_OFFICER_PASSWORD
uv run python -m modules.auth seed-demo
unset LAND_RECORDS_DEMO_LANDOWNER_PASSWORD LAND_RECORDS_DEMO_OFFICER_PASSWORD
```

The `read -s` prompts read the password without echoing it. Optionally set
`LAND_RECORDS_DEMO_LANDOWNER_NAME` and `LAND_RECORDS_DEMO_OFFICER_NAME`. Never
use demo credentials for production. Keep these variables in a trusted local
environment; do not commit them.

Review queue behavior is controlled by `LAND_RECORDS_REVIEW_POLICY`:
`assigned_only` limits an officer to explicitly assigned applications;
`assigned_or_unassigned` (the default) also permits unassigned applications
that are in a reviewable state.

## Landowner document uploads

The landowner portal accepts PDF, PNG, JPG, and JPEG files. Uploads are checked
against their extension, browser MIME type, file signature, and file parser.
The default maximum size is 10 MiB. Configure the limit in bytes with
`LAND_RECORDS_MAX_UPLOAD_BYTES` and the private storage location with
`LAND_RECORDS_UPLOAD_DIRECTORY`. By default, unmodified file bytes are stored in
`data/private_uploads`, outside Streamlit's public static directory, under a
random internal filename. The database stores the original display filename,
MIME type, SHA-256 digest, and application association; access is checked against
the authenticated landowner. Each upload creates a private draft application.

OCR runs after upload. PDF pages are rendered with PyMuPDF and images are
preprocessed with OpenCV before Tesseract recognition. The raw recognized text,
candidate fields, warnings, and confidence estimates are saved in the extracted
fields column; user edits are stored separately in corrected fields. Confidence
is an OCR engine recognition estimate, not a measure of field correctness or
legal ownership. Field-level warnings identify missing required values,
malformed formats, and values below the configurable review threshold.
`OCR_LOW_CONFIDENCE_THRESHOLD` sets that threshold on Tesseract's 0-100 scale
(default: `60`); the threshold and generated warnings are saved with each
application version for reproducible review. Low confidence never automatically
rejects an application, and high confidence never automatically approves one.
Users can enter fields manually if OCR is unavailable or unclear.

Application review also runs deterministic, local discrepancy rules. They check
required fields and formats, compare OCR and owner-entered values and prior
application versions, and compare parcel identifiers/location/area against
available active applications and public approved records. Each warning names
the compared fields, provides the available values, and explains its triggering
rule. Active-application duplicate details are officer-only. A missing
comparable reference is reported rather than filled with assumed data. These
warnings are for human review only: matching parcel numbers do not prove
ownership, and mismatches are not evidence of fraud or grounds for an automatic
decision.

The officer workspace also shows an optional **Preliminary consistency indicator**
for manual-review prioritization. It is a weighted mean on a 0-100 scale, using
only available components: OCR engine confidence (mean of valid 0-100 field
values), required-field completeness (non-empty required fields / required
fields), reference consistency (100 minus 20 points per applicable approved or
active-application discrepancy warning, floored at 0), and required audit
events/hash-chain integrity (100 if present and verified, otherwise 0). Default
relative weights are 25%, 25%, 30%, and 20%, respectively. Unavailable components
are excluded and the remaining weights are renormalized; unavailable reference
data is never counted as a match. Configure relative weights with the
`RECORD_CONSISTENCY_WEIGHTS` environment variable as a JSON object containing
exactly `ocr_confidence`, `required_completeness`, `reference_consistency`, and
`audit_integrity`, each set to a finite non-negative number with at least one
positive weight. No geospatial validation is implemented, so it is not scored.
The indicator is not a legal ownership score or scientifically validated
measure, does not establish factual correctness, and must never be the sole
basis for an approval or rejection.

After upload, landowners can preview the original document, review OCR suggestions,
and manually enter missing values. Required fields are validated before a corrected
data preview is shown. Land area must include a positive value and explicit unit;
units are never inferred or converted. Saving requires confirming the preview and
creates a new draft application version without submitting it. Approved records
are read-only in the landowner portal.

To submit, save a valid reviewed draft first, then use **Submit application for
verification**. Submission atomically records an immutable version snapshot, marks
the application as submitted, appends a SHA-256-linked audit event, and creates a
landowner notification. The application number follows the `LR-YYYY-XXXXXXXX`
format and remains the same through review. A submitted application is read-only
to the landowner and does not appear in the approved registry until an officer
approves it.

`pytesseract` is a Python wrapper; install the native Tesseract executable
separately if it is not already installed:

```sh
# macOS
brew install tesseract

# Debian or Ubuntu
sudo apt-get update && sudo apt-get install tesseract-ocr
```

On Windows, install the native Tesseract OCR package and make sure its install
directory is on `PATH` (for example, using the Windows installer linked from
[UB Mannheim's Tesseract distribution](https://github.com/UB-Mannheim/tesseract/wiki)).
Open a new terminal and confirm `tesseract --version` succeeds before starting
the portal. Restart the portal after installing it. If Tesseract is unavailable,
the upload is still preserved and the portal displays a warning with the
manual-entry form.

## Run a portal

```sh
uv run streamlit run app.py
```

The public verification page does not require an account. Visitors can enter
or open a record with `?record_id=<public-record-id>`, see only approved
public-release fields and version history, and explicitly run an audit integrity
check. The check recomputes the audit chain and validates the approved version
snapshots against their hashes and linked audit events. A passing integrity
check is not legal ownership verification.

The shared approved-record detail view can display a QR code linking to this
public verification page. Its URL contains only the public record ID; the QR
does not encode private fields or document contents. To configure the URL
embedded in QR codes, set `PUBLIC_VERIFICATION_BASE_URL` in the environment
used to run the portal. For local development, the default is
`http://localhost:8501`, matching `uv run streamlit run app.py`; if using a
different port, local network hostname, or public deployment URL, set it before
starting the portal. For example in PowerShell:

```powershell
$env:PUBLIC_VERIFICATION_BASE_URL = "http://localhost:8501"
uv run streamlit run app.py
```

A QR code is available only for a currently approved, publicly releasable
registry record. Possession of the QR code is not proof of ownership, and QR
codes do not prevent copying or forgery.

Run the authenticated landowner portal separately:

```sh
uv run streamlit run landowner_app.py
```

In another terminal, use `uv run streamlit run officer_app.py` for the officer
portal. Both portals use the same configured database and authentication
service. For a non-SQLite database, install its SQLAlchemy driver and set
`LAND_RECORDS_DATABASE_URL` in both portal environments.

The landowner portal includes database-derived application metrics, private
application and notification views, private document upload, and the shared
approved land registry. The officer portal uses the same registry service and
database. Registry searches support public record IDs and survey/plot numbers,
with district/village filters and bounded pagination. Record details show the
approved version, approval date, published parcel fields, audit history, and
audit verification. Only records with an approved application and the associated
authorized-officer approval audit event are eligible for publication; pending
and rejected applications are excluded.

Approved record details and the officer review workspace can show an optional
parcel map when a `parcel_geojson` Polygon or MultiPolygon is already present
in the relevant approved-record or application-version snapshot. Geometry is
checked for polygon structure and WGS84 coordinate bounds before rendering;
invalid geometry is not mapped. The map offers OpenStreetMap streets and Esri
satellite imagery with provider attribution, and only displays allowlisted
non-sensitive parcel attributes. It is a visualization only, separate from
validation, scoring, and approval. Satellite imagery does not prove ownership
or cadastral boundaries. No authoritative GeoJSON source is currently
configured, so ordinary records without supplied geometry show an explanatory
message. An explicit opt-in **synthetic map example** is available from that
message for UI demonstration only; its fabricated polygon and location are not
connected to any record and are not official boundaries. Synthetic geometry is
also used in tests and is never assigned to real records.

The public registry publishes only record ID, district, village, survey/plot
number, land area, approval version/date, and non-sensitive audit history. It
does not publish applicant identities, personal identification numbers, contact
details, deed files, or internal review notes. Authorized officers can view
additional internal fields and review notes from the record detail page. Audit
hash verification is a tamper-evidence check only and does not establish legal
ownership. Tests use synthetic records. Synthetic demo records are added only
when an operator explicitly runs `uv run python -m modules.demo seed-demo`
against the dedicated local demo database.

The officer review queue is scoped to applications assigned to the authenticated
officer or available under `LAND_RECORDS_REVIEW_POLICY`. Officers can filter the
queue by status, application number, district, and village; open private
application details and original documents; and claim an unassigned application
to begin review. Claiming changes a submitted application to `under_review` and
adds an audit event.

An assigned officer can approve, reject, or request corrections from the
application review page after explicitly confirming the action. Rejection and
correction requests require a meaningful explanation. Approval requires valid
owner-reviewed fields and an attached supporting document; OCR confidence is
never an approval criterion. Each decision transaction updates application
status, adds review history, creates a hash-linked audit event, and notifies the
landowner. Approval additionally snapshots the approved fields into a
`LandRecord` with a record reference and SHA-256 hash. Rejected applications
remain preserved and do not enter the approved registry. The officer assigned
to a completed application may reopen its read-only details and history, but
cannot make another decision.

When corrections are requested, the landowner can open the application under
**My Applications**, review the officer's instructions and decision history,
save revised fields as a new version, and append private supporting documents
without replacing the original upload. Resubmission is an explicit confirmed
action. It creates another immutable version snapshot, returns the application
to `submitted`, appends an `application_resubmitted` audit event, and creates
in-app notifications for the landowner and assigned officer (or active officers
when unassigned). Landowners can mark their own notifications as read; the
notification history is retained. Final approval or rejection and its review
history remain visible in the landowner application details. Approved records
cannot be edited through this correction workflow.

## Run tests

```sh
uv run pytest
```

## Audit chain

Important application and review transitions append audit events in the same
database transaction as the corresponding status change, approved record, and
notifications. `modules/audit.py` stores event data as deterministic canonical
JSON, hashes the event ID, actor, relationships, data, UTC timestamp, and
previous hash with SHA-256, and links the first event to a fixed genesis hash.
Approved-record approval and version events include the approved record hash;
officer comments and document contents are not copied into audit event data.
The officer Audit Explorer can recompute and verify the full chain.

Every new approval creates immutable official version 1, independent of the
application's pre-approval draft version. Officers can create another official
version only through the explicit **Authorized correction to approved record**
workflow, with confirmation and a required reason. Each snapshot records the
authorized officer, UTC time, canonical SHA-256 version hash, and linked audit
event. The registry shows the current version and provides read-only history and
field comparisons. OCR never updates approved versions. On startup, pre-existing
approved records are backfilled as version 1 only when a matching approval audit
event exists; their approved fields are preserved.

Verification can detect altered event contents, inconsistent event hashes,
broken links, disconnected events, forks, and event-ID gaps where detectable.
The audit chain head is also retained in the database to detect removal of its
final event while that head marker remains intact. This is tamper-evidence, not
tamper-proof: a database administrator able to edit the database could change
events and recompute the chain and head marker. A valid chain does not prove
that event data is factually correct and does not establish legal ownership.
This system uses a conventional relational database and SHA-256 hash chaining;
it does not use blockchain or a distributed ledger.

Audit rows written by earlier prototype versions use a different hash format.
Initialization does not rewrite or reseal those rows; a database containing
them will report verification failure and reject further audit appends until an
explicitly reviewed migration is performed.

Dependencies are managed in `pyproject.toml` and locked in `uv.lock`.# Land-Record-Digitization-v2
