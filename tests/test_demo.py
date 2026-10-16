from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import pymupdf as fitz
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import AuthService
from modules.database import (
    Application,
    AuditEvent,
    Database,
    Document,
    User,
    create_session_factory,
)
from modules.demo import (
    DEMO_APPLICATIONS,
    DEMO_LANDOWNER_EMAIL,
    DEMO_LANDOWNER_PASSWORD,
    DEMO_OFFICER_EMAIL,
    DEMO_OFFICER_PASSWORD,
    _run_reset,
    reset_demo_database,
    seed_demo_data,
)
from modules.document_storage import DocumentStorage
from modules.ocr import DocumentOCRResult


@pytest.fixture
def demo_database(tmp_path: Path) -> Iterator[tuple[str, Path, Path]]:
    database_path = tmp_path / "land_records_demo.db"
    upload_directory = tmp_path / "demo_private_uploads"
    document_path = tmp_path / "synthetic_land_record.pdf"
    yield f"sqlite:///{database_path.as_posix()}", upload_directory, document_path


def test_demo_seed_is_idempotent_and_audit_chain_verifies(
    demo_database: tuple[str, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, upload_directory, document_path = demo_database
    monkeypatch.setenv("LAND_RECORDS_ENVIRONMENT", "development")
    ocr_calls = 0

    def synthetic_ocr(
        content: bytes, filename: str, mime_type: str
    ) -> DocumentOCRResult:
        nonlocal ocr_calls
        ocr_calls += 1
        assert content.startswith(b"%PDF-")
        assert filename == "synthetic_land_record.pdf"
        assert mime_type == "application/pdf"
        return DocumentOCRResult(
            status="complete",
            ocr_text="Owner name: Demo Landowner\nSurvey or plot number: SY-DEMO-101",
            fields={
                "owner_name": "Demo Landowner",
                "survey_or_plot_number": "SY-DEMO-101",
            },
            field_confidence={"owner_name": 98.0, "survey_or_plot_number": 91.0},
            warnings=(),
            page_count=1,
        )

    monkeypatch.setattr("modules.auth.process_document", synthetic_ocr)
    first = seed_demo_data(
        database_url,
        document_path=document_path,
        upload_directory=upload_directory,
    )
    second = seed_demo_data(
        database_url,
        document_path=document_path,
        upload_directory=upload_directory,
    )

    expected_statuses = tuple(
        (application_number, status)
        for application_number, status, _ in DEMO_APPLICATIONS
    )
    assert first.application_statuses == expected_statuses
    assert second.application_statuses == expected_statuses
    assert first.audit_valid is second.audit_valid is True
    assert second.audit_event_count == first.audit_event_count
    assert ocr_calls == len(DEMO_APPLICATIONS)
    assert document_path.is_file()
    assert document_path.read_bytes().startswith(b"%PDF-")
    with fitz.open(document_path) as document:
        assert "SYNTHETIC LAND RECORD" in document[0].get_text()
        assert "Survey or plot number: SY-DEMO-101" in document[0].get_text()

    factory = create_session_factory(database_url)
    try:
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(User)) == 2
            assert session.scalar(select(func.count()).select_from(Application)) == 5
            assert session.scalar(select(func.count()).select_from(Document)) == 5
            assert session.scalar(select(func.count()).select_from(AuditEvent)) == first.audit_event_count
            assert set(
                session.scalars(select(Application.status)).all()
            ) == {
                "draft",
                "under_review",
                "corrections_requested",
                "approved",
                "rejected",
            }
            assert session.scalar(
                select(User.role).where(User.email == DEMO_LANDOWNER_EMAIL)
            ) == "landowner"
            assert session.scalar(
                select(User.role).where(User.email == DEMO_OFFICER_EMAIL)
            ) == "officer"
        service = AuthService(Database(factory), DocumentStorage(upload_directory))
        assert service.login(DEMO_LANDOWNER_EMAIL, DEMO_LANDOWNER_PASSWORD)
        assert service.login(DEMO_OFFICER_EMAIL, DEMO_OFFICER_PASSWORD)
        assert Database(factory).verify_audit_chain().valid
    finally:
        factory.kw["bind"].dispose()


def test_demo_commands_refuse_non_sqlite_production_or_unsafe_reset_paths(
    demo_database: tuple[str, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, _, _ = demo_database
    monkeypatch.setenv("LAND_RECORDS_ENVIRONMENT", "development")

    with pytest.raises(ValueError, match="local SQLite"):
        seed_demo_data("postgresql://localhost/land_records")

    monkeypatch.setenv("LAND_RECORDS_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="require"):
        seed_demo_data(database_url)
    monkeypatch.setenv("LAND_RECORDS_ENVIRONMENT", "development")

    with pytest.raises(ValueError, match="limited"):
        reset_demo_database("sqlite:///./data/land_records.db")
    assert reset_demo_database(database_url).name == "land_records_demo.db"


def test_demo_reset_warns_and_requires_explicit_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LAND_RECORDS_ENVIRONMENT", "development")
    database_path = tmp_path / "land_records_demo.db"
    database_path.write_bytes(b"synthetic demo database")
    wal_path = Path(f"{database_path}-wal")
    wal_path.write_bytes(b"synthetic wal")
    database_url = f"sqlite:///{database_path.as_posix()}"

    with pytest.raises(ValueError, match="No data was deleted"):
        _run_reset(database_url, confirmed=False)
    assert database_path.exists()
    assert "WARNING" in capsys.readouterr().out

    _run_reset(database_url, confirmed=True)
    assert not database_path.exists()
    assert not wal_path.exists()
