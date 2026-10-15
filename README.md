# AI-Powered Land Record Management and Verification System

Prototype scaffold for the land record management system.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```sh
uv sync
```

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
is an OCR recognition estimate, not a legal correctness score. Users can enter
fields manually if OCR is unavailable or unclear.

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
directory is on `PATH`. Restart the portal after installing it. If Tesseract is
unavailable, the upload is still preserved and the portal displays a warning
with the manual-entry form.

## Run a portal

```sh
uv run streamlit run landowner_app.py
```

In another terminal, use `uv run streamlit run officer_app.py` for the officer
portal. Both portals use the same configured database and authentication
service. For a non-SQLite database, install its SQLAlchemy driver and set
`LAND_RECORDS_DATABASE_URL` in both portal environments.

The landowner portal includes database-derived application metrics, private
application and notification views, a minimal approved-record summary, and
private document upload. It contains no
synthetic dashboard records by default; seed data only through a trusted
development setup.

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

Dependencies are managed in `pyproject.toml` and locked in `uv.lock`.# Land-Record-Digitization-v2
