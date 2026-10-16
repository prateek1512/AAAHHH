"""Local-only demo database and synthetic workflow data management."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import (
    AccountConfigurationError,
    AuthService,
    seed_demo_accounts,
)
from modules.database import (
    Application,
    ApplicationVersion,
    Database,
    Document,
    User,
    create_session_factory,
    get_database_url,
    initialize_database,
)
from modules.document_storage import DocumentStorage

DEMO_LANDOWNER_EMAIL = "demo.landowner@example.test"
DEMO_LANDOWNER_PASSWORD = "LandDemo-2026!"
DEMO_OFFICER_EMAIL = "demo.officer@example.test"
DEMO_OFFICER_PASSWORD = "OfficerDemo-2026!"
DEMO_DATABASE_FILENAME = "land_records_demo.db"
DEMO_APPLICATIONS = (
    ("DEMO-2026-WALKTHROUGH", "draft", "SY-DEMO-505"),
    ("DEMO-2026-REVIEW", "under_review", "SY-DEMO-101"),
    ("DEMO-2026-CORRECTIONS", "corrections_requested", "SY-DEMO-202"),
    ("DEMO-2026-APPROVED", "approved", "SY-DEMO-303"),
    ("DEMO-2026-REJECTED", "rejected", "SY-DEMO-404"),
)


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    application_statuses: tuple[tuple[str, str], ...]
    audit_valid: bool
    audit_event_count: int


def _require_local_sqlite(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or url.database in (None, "", ":memory:"):
        raise ValueError("Demo data commands are restricted to a local SQLite database.")
    if os.environ.get("LAND_RECORDS_ENVIRONMENT", "").strip().casefold() != "development":
        raise ValueError(
            "Demo data commands require LAND_RECORDS_ENVIRONMENT=development."
        )


def _make_synthetic_document(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = 612, 792
    content = (
        "SYNTHETIC LAND RECORD - DEMONSTRATION ONLY\n\n"
        "This document contains fabricated data for a local software demonstration.\n"
        "It is not an official document and does not establish ownership.\n\n"
        "Owner name: Demo Landowner\n"
        "District: Sample District\n"
        "Village: Sample Village\n"
        "Survey or plot number: SY-DEMO-101\n"
        "Land area: 2.5 acres\n"
        "Document type: Sale deed\n"
        "Circle or tehsil: Sample Tehsil\n"
        "Registration or document number: DEMO-REG-101\n"
        "Document date: 2026-01-15\n"
    )
    with fitz.open() as document:
        page = document.new_page(width=page_width, height=page_height)
        page.insert_textbox(
            fitz.Rect(54, 54, page_width - 54, page_height - 54),
            content,
            fontsize=15,
            fontname="helv",
            lineheight=1.4,
        )
        document.save(destination)
    return destination


def _record_status(
    session_factory: sessionmaker[Session],
    application_number: str,
) -> tuple[int, int | None, str, dict[str, object]] | None:
    with session_factory() as session:
        application = session.scalar(
            select(Application).where(
                Application.application_number == application_number
            )
        )
        if application is None:
            return None
        document = session.scalar(
            select(Document)
            .where(Document.application_id == application.id)
            .order_by(Document.id)
            .limit(1)
        )
        version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application.id,
                ApplicationVersion.version_number == application.current_version,
            )
        )
        return (
            application.id,
            document.id if document is not None else None,
            application.status,
            dict(version.corrected_fields_json) if version is not None else {},
        )


def _seed_application(
    *,
    service: AuthService,
    session_factory: sessionmaker[Session],
    owner_token: str,
    officer_token: str,
    application_number: str,
    target_status: str,
    survey_number: str,
    document_content: bytes,
) -> str:
    existing = _record_status(session_factory, application_number)
    corrected_fields: dict[str, object] = {}
    if existing is None:
        uploaded = service.upload_landowner_document(
            owner_token,
            "synthetic_land_record.pdf",
            "application/pdf",
            document_content,
            application_number=application_number,
        )
        document_id = uploaded.id
        status = "draft"
        application_id = uploaded.application_id
    else:
        application_id, document_id, status, corrected_fields = existing
        if document_id is None:
            raise RuntimeError(
                f"Demo application {application_number} is missing its synthetic document."
            )

    fields = {
        "owner_name": "Demo Landowner",
        "district": "Sample District",
        "village": "Sample Village",
        "survey_or_plot_number": survey_number,
        "land_area": "2.5 acres",
        "document_type": "Sale deed",
        "circle_or_tehsil": "Sample Tehsil",
        "registration_or_document_number": f"DEMO-REG-{survey_number[-3:]}",
        "document_date": "2026-01-15",
    }
    if status == "draft" and corrected_fields != fields:
        service.save_landowner_document_corrections(
            owner_token,
            document_id,
            fields,
        )
        corrected_fields = fields

    if status == "draft" and target_status != "draft":
        service.submit_landowner_application(owner_token, application_id)
        status = "submitted"

    if status == "submitted" and target_status != "submitted":
        service.start_officer_application_review(officer_token, application_id)
        status = "under_review"

    decisions = {
        "corrections_requested": (
            "request_corrections",
            "Please confirm the survey number against the supporting instrument.",
        ),
        "approved": ("approve", None),
        "rejected": (
            "reject",
            "The synthetic parcel reference does not match the supplied document.",
        ),
    }
    if status == "under_review" and target_status in decisions:
        decision, comments = decisions[target_status]
        result = service.decide_officer_application(
            officer_token,
            application_id,
            decision,
            comments,
        )
        status = result.status

    if status != target_status:
        raise RuntimeError(
            f"Demo application {application_number} has status {status!r}; "
            f"expected {target_status!r}. No existing record was overwritten."
        )
    return status


def seed_demo_data(
    database_url: str | None = None,
    *,
    document_path: str | Path | None = None,
    upload_directory: str | Path | None = None,
) -> DemoSeedResult:
    """Create resumable, idempotent synthetic workflow records in local SQLite."""
    selected_url = database_url or get_database_url()
    _require_local_sqlite(selected_url)
    initialize_database(selected_url)

    environment = {
        "LAND_RECORDS_DEMO_LANDOWNER_EMAIL": DEMO_LANDOWNER_EMAIL,
        "LAND_RECORDS_DEMO_LANDOWNER_PASSWORD": DEMO_LANDOWNER_PASSWORD,
        "LAND_RECORDS_DEMO_LANDOWNER_NAME": "Demo Landowner",
        "LAND_RECORDS_DEMO_OFFICER_EMAIL": DEMO_OFFICER_EMAIL,
        "LAND_RECORDS_DEMO_OFFICER_PASSWORD": DEMO_OFFICER_PASSWORD,
        "LAND_RECORDS_DEMO_OFFICER_NAME": "Demo Officer",
    }
    session_factory = create_session_factory(selected_url)
    database = Database(session_factory)
    try:
        try:
            seed_demo_accounts(database, environment)
        except AccountConfigurationError as error:
            raise RuntimeError(
                f"Could not prepare local demo accounts: {error}"
            ) from error
        with session_factory() as session:
            owner = session.scalar(
                select(User).where(User.email == DEMO_LANDOWNER_EMAIL)
            )
            officer = session.scalar(
                select(User).where(User.email == DEMO_OFFICER_EMAIL)
            )
            if owner is None or owner.role != "landowner":
                raise RuntimeError("The configured demo landowner account is unavailable.")
            if officer is None or officer.role != "officer":
                raise RuntimeError("The configured demo officer account is unavailable.")

        document_file = _make_synthetic_document(
            Path(document_path)
            if document_path is not None
            else Path("data/demo_documents/synthetic_land_record.pdf")
        )
        document_content = document_file.read_bytes()
        storage = DocumentStorage(upload_directory)
        service = AuthService(database, storage)
        owner_token = service.login(DEMO_LANDOWNER_EMAIL, DEMO_LANDOWNER_PASSWORD)
        officer_token = service.login(DEMO_OFFICER_EMAIL, DEMO_OFFICER_PASSWORD)

        statuses = tuple(
            (
                application_number,
                _seed_application(
                    service=service,
                    session_factory=session_factory,
                    owner_token=owner_token,
                    officer_token=officer_token,
                    application_number=application_number,
                    target_status=status,
                    survey_number=survey_number,
                    document_content=document_content,
                ),
            )
            for application_number, status, survey_number in DEMO_APPLICATIONS
        )
        verification = database.verify_audit_chain()
        if not verification.valid:
            raise RuntimeError(
                "Demo records were created, but audit verification failed: "
                + "; ".join(verification.errors)
            )
        return DemoSeedResult(
            application_statuses=statuses,
            audit_valid=verification.valid,
            audit_event_count=verification.event_count,
        )
    finally:
        session_factory.kw["bind"].dispose()


def reset_demo_database(database_url: str | None = None) -> Path:
    """Remove only the explicitly named SQLite demo database and its sidecars."""
    selected_url = database_url or get_database_url()
    _require_local_sqlite(selected_url)
    url = make_url(selected_url)
    database_path = Path(url.database or "").expanduser().resolve()
    if database_path.name.casefold() != DEMO_DATABASE_FILENAME:
        raise ValueError(
            f"Reset is limited to a SQLite file named {DEMO_DATABASE_FILENAME!r}; "
            f"refusing to remove {database_path}."
        )
    return database_path


def _run_reset(database_url: str | None, confirmed: bool) -> None:
    database_path = reset_demo_database(database_url)
    warning = (
        "WARNING: this deletes the complete SQLite demo database and all data in it. "
        "Stop both portals first. Private uploaded files are not removed."
    )
    print(warning)
    if not confirmed:
        raise ValueError("No data was deleted. Re-run with --confirm-demo-reset.")
    for target in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        if target.exists():
            target.unlink()
    print(f"Removed demo database: {database_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize or seed a local-only land-record demonstration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="create missing tables in the configured database")
    subparsers.add_parser("seed-demo", help="seed accounts and synthetic workflow records")
    subparsers.add_parser("verify", help="verify the configured database audit chain")
    reset_parser = subparsers.add_parser(
        "reset-demo",
        help="delete only data/demo/land_records_demo.db (requires explicit confirmation)",
    )
    reset_parser.add_argument(
        "--confirm-demo-reset",
        action="store_true",
        help="confirm deletion after reviewing the warning",
    )
    args = parser.parse_args()
    database_url = get_database_url()

    try:
        if args.command == "init":
            initialize_database(database_url)
            print(f"Database schema initialized at {database_url}.")
        elif args.command == "seed-demo":
            result = seed_demo_data(database_url)
            for application_number, status in result.application_statuses:
                print(f"{application_number}: {status}")
            print(
                f"Audit chain valid: {result.audit_valid} "
                f"({result.audit_event_count} events)."
            )
            print(
                "Local demo accounts: "
                f"{DEMO_LANDOWNER_EMAIL} / {DEMO_LANDOWNER_PASSWORD}; "
                f"{DEMO_OFFICER_EMAIL} / {DEMO_OFFICER_PASSWORD}"
            )
            if shutil.which("tesseract") is None:
                print(
                    "OCR engine unavailable: install native Tesseract or use the "
                    "manual-entry workflow for the synthetic PDF."
                )
        elif args.command == "verify":
            factory = create_session_factory(database_url)
            try:
                verification = Database(factory).verify_audit_chain()
            finally:
                factory.kw["bind"].dispose()
            print(
                f"Audit chain valid: {verification.valid} "
                f"({verification.event_count} events)."
            )
            for error in verification.errors:
                print(f"- {error}")
            if not verification.valid:
                raise RuntimeError("Audit verification failed.")
        elif args.command == "reset-demo":
            _run_reset(database_url, args.confirm_demo_reset)
    except (AccountConfigurationError, OSError, RuntimeError, ValueError) as error:
        print(f"Demo command failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
