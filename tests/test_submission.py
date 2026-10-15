from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
import re

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import AuthService, AuthorizationError, hash_password
from modules.database import (
    Application,
    ApplicationVersion,
    AuditEvent,
    Base,
    Database,
    Notification,
    User,
    create_database_engine,
    transaction,
)
from modules.document_storage import DocumentStorage
from modules.ocr import DocumentOCRResult


@pytest.fixture
def submission_context(
    tmp_path,
) -> Iterator[tuple[sessionmaker[Session], AuthService, str, str, str]]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with transaction(session_factory) as session:
        session.add_all(
            [
                User(
                    full_name="Synthetic Owner",
                    email="owner@example.test",
                    password_hash=hash_password("synthetic-owner-password"),
                    role="landowner",
                ),
                User(
                    full_name="Other Synthetic Owner",
                    email="other-owner@example.test",
                    password_hash=hash_password("synthetic-other-owner-password"),
                    role="landowner",
                ),
                User(
                    full_name="Synthetic Officer",
                    email="officer@example.test",
                    password_hash=hash_password("synthetic-officer-password"),
                    role="officer",
                ),
            ]
        )
    service = AuthService(
        Database(session_factory),
        DocumentStorage(tmp_path / "private"),
    )
    owner_token = service.login("owner@example.test", "synthetic-owner-password")
    other_owner_token = service.login(
        "other-owner@example.test", "synthetic-other-owner-password"
    )
    officer_token = service.login("officer@example.test", "synthetic-officer-password")
    yield session_factory, service, owner_token, other_owner_token, officer_token
    engine.dispose()


def _valid_fields() -> dict[str, str]:
    return {
        "owner_name": "Synthetic Owner",
        "district": "Sample District",
        "village": "Sample Village",
        "survey_or_plot_number": "SY-123",
        "land_area": "2.5 acres",
        "document_type": "Sale deed",
        "circle_or_tehsil": "Sample Tehsil",
        "registration_or_document_number": "REG-123",
        "document_date": "2026-01-15",
    }


def _png_bytes() -> bytes:
    content = BytesIO()
    Image.new("RGB", (16, 16), "white").save(content, format="PNG")
    return content.getvalue()


def _install_controlled_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    result = DocumentOCRResult(
        status="complete",
        ocr_text="Owner name: OCR Owner\nDistrict: Sample District",
        fields={"owner_name": "OCR Owner", "district": "Sample District"},
        field_confidence={"owner_name": 91.0, "district": 88.0},
        warnings=(),
        page_count=1,
    )

    def fake_process(content: bytes, filename: str, mime_type: str) -> DocumentOCRResult:
        assert content.startswith(b"\x89PNG")
        assert filename == "synthetic-record.png"
        assert mime_type == "image/png"
        return result

    monkeypatch.setattr("modules.auth.process_document", fake_process)


def _upload_and_edit(
    service: AuthService,
    owner_token: str,
) -> int:
    uploaded = service.upload_landowner_document(
        owner_token,
        "synthetic-record.png",
        "image/png",
        _png_bytes(),
    )
    assert uploaded.application_number.startswith("LR-")
    service.save_landowner_document_corrections(
        owner_token,
        uploaded.id,
        _valid_fields(),
    )
    return uploaded.application_id


def test_complete_upload_ocr_edit_submit_workflow(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service, owner_token, _, officer_token = submission_context
    _install_controlled_ocr(monkeypatch)

    application_id = _upload_and_edit(service, owner_token)
    submission = service.submit_landowner_application(owner_token, application_id)

    assert re.fullmatch(r"LR-\d{4}-[A-F0-9]{8}", submission.application_number)
    assert submission.status == "submitted"
    assert submission.submitted_at is not None
    assert submission.current_version == 3
    assert submission.corrected_fields_json["owner_name"] == "Synthetic Owner"
    document = service.list_landowner_documents(owner_token)[0]
    with pytest.raises(AuthorizationError, match="not authorized"):
        service.save_landowner_document_corrections(
            owner_token,
            document.id,
            _valid_fields(),
        )

    detail = service.get_landowner_application(owner_token, application_id)
    assert detail.application_number == submission.application_number
    assert detail.status == "submitted"
    assert detail.submitted_at == submission.submitted_at
    assert detail.current_version == 3
    assert detail.corrected_fields_json == submission.corrected_fields_json
    assert detail.document_filenames == ("synthetic-record.png",)
    assert any(
        item.id == application_id and item.status == "submitted"
        for item in service.list_landowner_applications(owner_token)
    )
    assert service.list_landowner_approved_records(owner_token) == ()
    audit_events = service.list_audit_events(officer_token, application_id=application_id)
    assert len(audit_events) == 1
    assert audit_events[0].event_type == "application_submitted"
    assert audit_events[0].integrity_verified is True

    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == "submitted"
        assert application.submitted_at is not None
        snapshot = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application_id,
                ApplicationVersion.version_number == 3,
            )
        )
        assert snapshot is not None
        assert snapshot.corrected_fields_json == _valid_fields()
        ocr_text = snapshot.extracted_fields_json["ocr_text"]
        assert isinstance(ocr_text, str)
        assert ocr_text.startswith("Owner name: OCR Owner")
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.application_id == application_id)
        )
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        assert owner is not None
        assert audit is not None
        assert audit.event_type == "application_submitted"
        assert audit.event_data_json["application_number"] == submission.application_number
        assert session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.application_id == application_id,
                Notification.user_id == owner.id,
            )
        ) == 1


def test_invalid_submission_does_not_change_status_or_emit_success_events(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service, owner_token, _, _ = submission_context
    _install_controlled_ocr(monkeypatch)
    uploaded = service.upload_landowner_document(
        owner_token,
        "synthetic-record.png",
        "image/png",
        _png_bytes(),
    )

    with pytest.raises(ValueError, match="Owner name is required"):
        service.submit_landowner_application(owner_token, uploaded.application_id)

    with session_factory() as session:
        application = session.get(Application, uploaded.application_id)
        assert application is not None
        assert application.status == "draft"
        assert application.submitted_at is None
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == uploaded.application_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.application_id == uploaded.application_id
            )
        ) == 0


def test_submission_requires_supporting_document(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
) -> None:
    session_factory, service, owner_token, _, _ = submission_context
    with transaction(session_factory) as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        assert owner is not None
        application = Application(
            application_number="LR-2026-NODOC123",
            applicant_id=owner.id,
            status="draft",
            current_version=1,
        )
        session.add(application)
        session.flush()
        session.add(
            ApplicationVersion(
                application_id=application.id,
                version_number=1,
                extracted_fields_json={},
                corrected_fields_json=_valid_fields(),
                created_by=owner.id,
            )
        )
        application_id = application.id

    with pytest.raises(ValueError, match="supporting document"):
        service.submit_landowner_application(owner_token, application_id)


def test_application_numbers_are_unique_and_human_readable(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, owner_token, _, _ = submission_context
    _install_controlled_ocr(monkeypatch)

    first = service.upload_landowner_document(
        owner_token,
        "synthetic-record.png",
        "image/png",
        _png_bytes(),
    )
    second = service.upload_landowner_document(
        owner_token,
        "synthetic-record.png",
        "image/png",
        _png_bytes(),
    )

    assert first.application_number != second.application_number
    assert re.fullmatch(r"LR-\d{4}-[A-F0-9]{8}", first.application_number)
    assert re.fullmatch(r"LR-\d{4}-[A-F0-9]{8}", second.application_number)


def test_duplicate_submission_is_rejected_without_duplicate_events(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service, owner_token, _, _ = submission_context
    _install_controlled_ocr(monkeypatch)
    application_id = _upload_and_edit(service, owner_token)
    service.submit_landowner_application(owner_token, application_id)

    with pytest.raises(ValueError, match="already been submitted"):
        service.submit_landowner_application(owner_token, application_id)

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == application_id
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.application_id == application_id
            )
        ) == 1


def test_other_landowner_cannot_open_or_submit_application(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, owner_token, other_owner_token, _ = submission_context
    _install_controlled_ocr(monkeypatch)
    application_id = _upload_and_edit(service, owner_token)

    with pytest.raises(AuthorizationError):
        service.get_landowner_application(other_owner_token, application_id)
    with pytest.raises(AuthorizationError):
        service.submit_landowner_application(other_owner_token, application_id)
