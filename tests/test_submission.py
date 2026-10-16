from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
import re
from uuid import uuid4

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


def test_correction_resubmission_preserves_versions_and_notifies_reviewers(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service, owner_token, _, officer_token = submission_context
    _install_controlled_ocr(monkeypatch)
    application_id = _upload_and_edit(service, owner_token)
    initial_submission = service.submit_landowner_application(owner_token, application_id)

    service.start_officer_application_review(officer_token, application_id)
    review_detail = service.get_application_for_review(officer_token, application_id)
    assert review_detail is not None
    assert review_detail.status == "under_review"
    assert review_detail.corrected_fields_json["owner_name"] == "Synthetic Owner"
    assert review_detail.extracted_fields_json["fields"]["owner_name"] == "OCR Owner"
    service.decide_officer_application(
        officer_token,
        application_id,
        "request_corrections",
        "Please correct the survey number against the attached instrument.",
    )

    correction_detail = service.get_landowner_application(owner_token, application_id)
    assert correction_detail.status == "corrections_requested"
    with session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        assert owner is not None
        correction_notification = session.scalar(
            select(Notification).where(
                Notification.application_id == application_id,
                Notification.user_id == owner.id,
                Notification.title == "Corrections requested",
            )
        )
        assert correction_notification is not None
        assert "correct the survey number" in correction_notification.message.lower()
    correction_event = correction_detail.review_events[-1]
    assert correction_event.decision == "request_corrections"
    assert "correct the survey number" in (correction_event.comments or "")
    assert correction_event.officer_name == "Synthetic Officer"

    revised_fields = _valid_fields()
    revised_fields["survey_or_plot_number"] = "SY-456"
    original_document = next(
        document
        for document in service.list_landowner_documents(owner_token)
        if document.application_id == application_id
    )
    added_document = service.upload_landowner_supporting_document(
        owner_token,
        application_id,
        "supporting-evidence.png",
        "image/png",
        _png_bytes(),
    )
    assert added_document.application_id == application_id
    assert added_document.original_filename == "supporting-evidence.png"

    resubmission = service.resubmit_landowner_application(
        owner_token,
        application_id,
        revised_fields,
    )
    assert resubmission.status == "submitted"
    assert resubmission.current_version == initial_submission.current_version + 2
    assert resubmission.corrected_fields_json["survey_or_plot_number"] == "SY-456"

    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == "submitted"
        assert application.current_version == resubmission.current_version
        old_submission = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application_id,
                ApplicationVersion.version_number == initial_submission.current_version,
            )
        )
        assert old_submission is not None
        assert old_submission.corrected_fields_json["survey_or_plot_number"] == "SY-123"
        revised_version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application_id,
                ApplicationVersion.version_number == initial_submission.current_version + 1,
            )
        )
        assert revised_version is not None
        assert revised_version.corrected_fields_json["survey_or_plot_number"] == "SY-456"
        current_snapshot = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application_id,
                ApplicationVersion.version_number == resubmission.current_version,
            )
        )
        assert current_snapshot is not None
        assert current_snapshot.corrected_fields_json == revised_fields
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.event_type == "application_resubmitted",
            )
        ) == 1
        officer = session.scalar(select(User).where(User.email == "officer@example.test"))
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        assert officer is not None and owner is not None
        assert session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.application_id == application_id,
                Notification.user_id == officer.id,
                Notification.title == "Application ready for review",
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.application_id == application_id,
                Notification.user_id == owner.id,
                Notification.title == "Application resubmitted",
            )
        ) == 1

    assert len(
        [
            item
            for item in service.list_landowner_documents(owner_token)
            if item.application_id == application_id
        ]
    ) == 2
    audit_events = service.list_audit_events(officer_token, application_id=application_id)
    assert any(
        item.event_type == "application_resubmitted" and item.integrity_verified
        for item in audit_events
    )
    with pytest.raises(ValueError, match="not awaiting corrections"):
        service.resubmit_landowner_application(owner_token, application_id)
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.event_type == "application_resubmitted",
            )
        ) == 1

    service.start_officer_application_review(officer_token, application_id)
    approval = service.decide_officer_application(officer_token, application_id, "approve")
    approved_detail = service.get_landowner_application(owner_token, application_id)
    assert approval.status == "approved"
    assert approved_detail.status == "approved"
    assert approved_detail.public_record_id == approval.public_record_id
    assert approved_detail.review_events[-1].decision == "approve"
    assert approval.public_record_id is not None
    registry = service.list_approved_registry(owner_token)
    assert len(registry) == 1
    assert registry[0].public_record_id == approval.public_record_id
    assert registry[0].current_version == 1
    public_record = service.get_public_approved_record(approval.public_record_id)
    assert public_record is not None
    assert public_record.current_version == 1
    assert len(public_record.current_version_hash) == 64
    assert public_record.approved_fields == {
        "district": "Sample District",
        "village": "Sample Village",
        "survey_or_plot_number": "SY-456",
        "land_area": "2.5 acres",
    }
    assert len(public_record.versions) == 1
    assert public_record.versions[0].version_number == 1
    verification = service.verify_public_record_audit_integrity(
        approval.public_record_id
    )
    assert verification is not None
    assert verification.passed is True
    assert service.verify_audit_chain(officer_token).valid is True
    qr = service.get_public_approved_record_qr(approval.public_record_id)
    assert qr is not None
    assert approval.public_record_id in qr.verification_url
    assert "application_id" not in qr.verification_url
    with session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        assert owner is not None
        approval_notification = session.scalar(
            select(Notification).where(
                Notification.application_id == application_id,
                Notification.user_id == owner.id,
                Notification.title == "Application approved",
            )
        )
        assert approval_notification is not None
    with pytest.raises(AuthorizationError, match="already been approved"):
        service.save_landowner_document_corrections(
            owner_token,
            original_document.id,
            revised_fields,
        )


def test_landowner_notification_read_is_persistent_and_owner_scoped(
    submission_context: tuple[sessionmaker[Session], AuthService, str, str, str],
) -> None:
    session_factory, service, owner_token, other_owner_token, officer_token = submission_context
    application_id = _application_for_notification_test(session_factory)
    with transaction(session_factory) as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        other_owner = session.scalar(
            select(User).where(User.email == "other-owner@example.test")
        )
        assert owner is not None and other_owner is not None
        owner_notification = Notification(
            user_id=owner.id,
            application_id=application_id,
            title="Unread update",
            message="Your application needs attention.",
        )
        other_notification = Notification(
            user_id=other_owner.id,
            application_id=application_id,
            title="Private update",
            message="This notification belongs to another owner.",
        )
        session.add_all([owner_notification, other_notification])
        session.flush()
        owner_notification_id = owner_notification.id
        other_notification_id = other_notification.id

    owner_notifications = service.list_landowner_notifications(owner_token)
    assert [item.id for item in owner_notifications] == [owner_notification_id]
    assert owner_notifications[0].is_read is False
    service.mark_landowner_notification_read(owner_token, owner_notification_id)
    assert service.list_landowner_notifications(owner_token)[0].is_read is True
    with pytest.raises(AuthorizationError):
        service.mark_landowner_notification_read(owner_token, other_notification_id)
    with pytest.raises(AuthorizationError):
        service.mark_landowner_notification_read(officer_token, owner_notification_id)
    assert [item.id for item in service.list_landowner_notifications(other_owner_token)] == [
        other_notification_id
    ]
    with session_factory() as session:
        persisted_notification = session.get(Notification, owner_notification_id)
        assert persisted_notification is not None
        assert persisted_notification.is_read is True


def _application_for_notification_test(
    session_factory: sessionmaker[Session],
) -> int:
    with transaction(session_factory) as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.test"))
        assert owner is not None
        application = Application(
            application_number=f"LR-NOTIFY-{uuid4().hex[:10]}",
            applicant_id=owner.id,
            status="submitted",
        )
        session.add(application)
        session.flush()
        return application.id


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
