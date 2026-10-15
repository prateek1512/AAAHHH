from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

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
    Document,
    User,
    create_database_engine,
    transaction,
)
from modules.document_storage import DocumentStorage


@pytest.fixture
def review_context(
    tmp_path,
) -> Iterator[tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]]]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = DocumentStorage(tmp_path / "private")
    tokens: dict[str, str] = {}
    user_ids: dict[str, int] = {}
    service = AuthService(Database(session_factory), storage)
    image_bytes = BytesIO()
    Image.new("RGB", (16, 16), "white").save(image_bytes, format="PNG")
    stored = storage.store(
        "synthetic-review.png",
        "image/png",
        image_bytes.getvalue(),
    )
    with transaction(session_factory) as session:
        owner = User(
            full_name="Synthetic Owner",
            email="owner@example.test",
            password_hash=hash_password("synthetic-owner-password"),
            role="landowner",
        )
        officer = User(
            full_name="Assigned Synthetic Officer",
            email="officer@example.test",
            password_hash=hash_password("synthetic-officer-password"),
            role="officer",
        )
        other_officer = User(
            full_name="Other Synthetic Officer",
            email="other-officer@example.test",
            password_hash=hash_password("synthetic-other-officer-password"),
            role="officer",
        )
        session.add_all([owner, officer, other_officer])
        session.flush()
        user_ids.update(
            owner=owner.id,
            officer=officer.id,
            other_officer=other_officer.id,
        )

        def add_application(
            number: str,
            status: str,
            district: str,
            village: str,
            assigned_officer_id: int | None = None,
            with_document: bool = True,
        ) -> int:
            application = Application(
                application_number=number,
                applicant_id=owner.id,
                assigned_officer_id=assigned_officer_id,
                status=status,
                current_version=1,
            )
            session.add(application)
            session.flush()
            session.add(
                ApplicationVersion(
                    application_id=application.id,
                    version_number=1,
                    extracted_fields_json={
                        "status": "complete",
                        "ocr_text": f"Owner name: Synthetic Owner\nDistrict: {district}",
                        "fields": {"owner_name": "Synthetic Owner", "district": district},
                        "warnings": ["Confidence is a recognition estimate."],
                        "field_confidence": {"owner_name": 89.0},
                    },
                    corrected_fields_json={
                        "owner_name": "Synthetic Owner",
                        "district": district,
                        "village": village,
                        "survey_or_plot_number": "SY-123",
                        "land_area": "2 acres",
                        "document_type": "Sale deed",
                    },
                    created_by=owner.id,
                )
            )
            if with_document:
                session.add(
                    Document(
                        application_id=application.id,
                        original_filename=stored.original_filename,
                        stored_path=stored.stored_path,
                        document_sha256=stored.document_sha256,
                        mime_type=stored.mime_type,
                    )
                )
            return application.id

        user_ids["unassigned_application"] = add_application(
            "LR-2026-UNASSIGNED", "submitted", "North District", "Cedar Village"
        )
        user_ids["assigned_application"] = add_application(
            "LR-2026-ASSIGNED",
            "under_review",
            "South District",
            "Pine Village",
            assigned_officer_id=user_ids["officer"],
        )
        user_ids["other_assigned_application"] = add_application(
            "LR-2026-OTHER", "submitted", "North District", "Cedar Village",
            assigned_officer_id=user_ids["other_officer"],
        )
        user_ids["draft_application"] = add_application(
            "LR-2026-DRAFT", "draft", "North District", "Cedar Village"
        )
        user_ids["no_document_application"] = add_application(
            "LR-2026-NODOC", "submitted", "North District", "Cedar Village",
            with_document=False,
        )

    tokens["officer"] = service.login("officer@example.test", "synthetic-officer-password")
    tokens["other_officer"] = service.login(
        "other-officer@example.test", "synthetic-other-officer-password"
    )
    tokens["owner"] = service.login("owner@example.test", "synthetic-owner-password")
    yield session_factory, service, tokens, user_ids
    engine.dispose()


def test_officer_queue_filters_status_number_district_and_village(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    _, service, tokens, ids = review_context
    token = tokens["officer"]

    all_reviewable = service.list_officer_review_queue_filtered(token)
    assert {application.id for application in all_reviewable} == {
        ids["unassigned_application"],
        ids["assigned_application"],
        ids["no_document_application"],
    }
    north = service.list_officer_review_queue_filtered(token, district="north")
    assert {application.id for application in north} == {
        ids["unassigned_application"],
        ids["no_document_application"],
    }
    village = service.list_officer_review_queue_filtered(token, village="pine")
    assert [application.id for application in village] == [ids["assigned_application"]]
    submitted = service.list_officer_review_queue_filtered(token, status="submitted")
    assert {application.id for application in submitted} == {
        ids["unassigned_application"],
        ids["no_document_application"],
    }
    by_number = service.list_officer_review_queue_filtered(
        token, application_number="unassigned"
    )
    assert [application.application_number for application in by_number] == [
        "LR-2026-UNASSIGNED"
    ]


def test_review_details_include_fields_warnings_version_and_private_document(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    _, service, tokens, ids = review_context
    detail = service.get_application_for_review(
        tokens["officer"], ids["unassigned_application"]
    )

    assert detail is not None
    assert detail.current_version == 1
    assert detail.ocr_status == "complete"
    assert detail.ocr_warnings == ("Confidence is a recognition estimate.",)
    assert detail.corrected_fields_json["district"] == "North District"
    assert detail.validation_warnings == ()
    assert len(detail.documents) == 1
    assert "stored_path" not in detail.documents[0].__dataclass_fields__
    original = service.get_officer_application_document(
        tokens["officer"],
        detail.id,
        detail.documents[0].id,
    )
    assert original.original_filename == "synthetic-review.png"
    assert original.content.startswith(b"\x89PNG")


def test_private_review_access_is_role_and_assignment_scoped(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    _, service, tokens, ids = review_context

    with pytest.raises(AuthorizationError):
        service.list_officer_review_queue_filtered(tokens["owner"])
    with pytest.raises(AuthorizationError):
        service.get_application_for_review(tokens["owner"], ids["unassigned_application"])
    assert (
        service.get_application_for_review(
            tokens["officer"], ids["other_assigned_application"]
        )
        is None
    )
    with pytest.raises(AuthorizationError):
        service.get_officer_application_document(
            tokens["officer"],
            ids["unassigned_application"],
            99999,
        )


def test_officer_can_claim_review_once_and_status_is_rechecked(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    application_id = ids["unassigned_application"]

    started = service.start_officer_application_review(tokens["officer"], application_id)
    assert started.status == "under_review"
    assert started.application_number == "LR-2026-UNASSIGNED"
    with pytest.raises(AuthorizationError):
        service.start_officer_application_review(tokens["other_officer"], application_id)

    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == "under_review"
        assert application.assigned_officer_id == ids["officer"]
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.event_type == "application_review_started",
            )
        ) == 1

    repeat = service.start_officer_application_review(tokens["officer"], application_id)
    assert repeat.status == "under_review"
    audit_events = service.list_audit_events(
        tokens["officer"],
        application_id=application_id,
    )
    assert len(audit_events) == 1
    assert audit_events[0].event_type == "application_review_started"
    assert audit_events[0].integrity_verified is True
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.event_type == "application_review_started",
            )
        ) == 1


def test_stale_or_ineligible_application_cannot_be_claimed(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    draft_id = ids["draft_application"]
    with pytest.raises(AuthorizationError, match="not authorized"):
        service.start_officer_application_review(tokens["officer"], draft_id)

    with transaction(session_factory) as session:
        application = session.get(Application, ids["unassigned_application"])
        assert application is not None
        application.status = "approved"
    with pytest.raises(AuthorizationError):
        service.start_officer_application_review(
            tokens["officer"], ids["unassigned_application"]
        )
