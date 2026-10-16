from __future__ import annotations

from collections.abc import Iterator
from datetime import timezone
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import AuthService, AuthorizationError, hash_password
from modules.database import (
    Application,
    ApplicationVersion,
    AuditEvent,
    ApprovedRecordVersion,
    Base,
    Database,
    Document,
    LandRecord,
    Notification,
    ReviewEvent,
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
    assert detail.consistency_score["label"] == "Preliminary consistency indicator"
    assert detail.consistency_score["score"] is not None
    assert "not a legal" in detail.consistency_score["warning"]
    assert detail.consistency_score["components"]["audit_integrity"]["score"] == 0
    assert detail.consistency_score["components"]["reference_consistency"]["score"] is None
    duplicate_warning = next(
        warning
        for warning in detail.discrepancy_warnings
        if warning["code"] == "possible_duplicate_application"
    )
    assert duplicate_warning["audience"] == "officer"
    assert duplicate_warning["fields_compared"] == [
        "survey_or_plot_number",
        "district",
        "village",
    ]
    landowner_detail = service.get_landowner_application(
        tokens["owner"], ids["unassigned_application"]
    )
    assert not any(
        warning["code"] == "possible_duplicate_application"
        for warning in landowner_detail.discrepancy_warnings
    )
    assert len(detail.documents) == 1
    assert "stored_path" not in detail.documents[0].__dataclass_fields__
    original = service.get_officer_application_document(
        tokens["officer"],
        detail.id,
        detail.documents[0].id,
    )
    assert original.original_filename == "synthetic-review.png"
    assert original.content.startswith(b"\x89PNG")


def test_officer_review_details_include_optional_snapshot_geojson(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    synthetic_geojson = {
        "type": "Polygon",
        "coordinates": [
            [[-73.1, 40.1], [-73.0, 40.1], [-73.0, 40.2], [-73.1, 40.2], [-73.1, 40.1]]
        ],
    }
    with transaction(session_factory) as session:
        version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == ids["unassigned_application"],
                ApplicationVersion.version_number == 1,
            )
        )
        assert version is not None
        version.extracted_fields_json = {
            **version.extracted_fields_json,
            "parcel_geojson": synthetic_geojson,
            "parcel_geometry_validation_status": "warning",
        }

    detail = service.get_application_for_review(
        tokens["officer"], ids["unassigned_application"]
    )
    assert detail is not None
    assert detail.parcel_geojson == synthetic_geojson
    assert detail.parcel_geometry_validation_status == "warning"


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


def test_officer_approval_creates_approved_record_and_side_effects(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    application_id = ids["assigned_application"]

    result = service.decide_officer_application(
        tokens["officer"],
        application_id,
        "approve",
    )

    assert result.status == "approved"
    assert result.public_record_id is not None
    assert result.public_record_id.startswith("LR-REC-2026-")
    records = service.list_approved_registry(tokens["officer"])
    assert len(records) == 1
    assert records[0].public_record_id == result.public_record_id
    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == "approved"
        assert application.land_record is not None
        record = session.scalar(
            select(LandRecord).where(LandRecord.application_id == application_id)
        )
        assert record is not None
        assert record.current_version == application.current_version
        assert record.current_version == 1
        assert record.approved_by == ids["officer"]
        assert record.approved_at.replace(tzinfo=result.decided_at.tzinfo) == result.decided_at
        assert record.approved_fields_json["owner_name"] == "Synthetic Owner"
        assert len(record.current_record_hash) == 64
        version_snapshot = session.scalar(
            select(ApprovedRecordVersion).where(
                ApprovedRecordVersion.land_record_id == record.id,
                ApprovedRecordVersion.version_number == 1,
            )
        )
        assert version_snapshot is not None
        assert version_snapshot.version_hash == record.current_record_hash
        assert version_snapshot.authorized_by == ids["officer"]
        assert version_snapshot.authorized_at.replace(tzinfo=timezone.utc) == result.decided_at
        assert version_snapshot.audit_event.event_type == "approved_record_version_created"
        review_event = session.scalar(
            select(ReviewEvent).where(
                ReviewEvent.application_id == application_id,
                ReviewEvent.decision == "approve",
            )
        )
        assert review_event is not None
        assert review_event.officer_id == ids["officer"]
        assert review_event.created_at.replace(tzinfo=timezone.utc) == result.decided_at
        notification = session.scalar(
            select(Notification).where(Notification.application_id == application_id)
        )
        assert notification is not None
        assert notification.user_id == ids["owner"]
        assert result.public_record_id in notification.message
        audit_event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.event_type == "application_approve",
            )
        )
        assert audit_event is not None
        assert audit_event.land_record_id == record.id
        assert audit_event.actor_id == ids["officer"]
        assert audit_event.created_at.replace(tzinfo=timezone.utc) == result.decided_at

    audit_events = service.list_audit_events(
        tokens["officer"], application_id=application_id
    )
    assert any(
        event.event_type == "application_approve" and event.integrity_verified
        for event in audit_events
    )

    with pytest.raises(AuthorizationError, match="active review"):
        service.decide_officer_application(tokens["officer"], application_id, "approve")
    assert len(service.list_approved_registry(tokens["officer"])) == 1


def test_public_record_qr_is_available_only_after_approval(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    assert service.get_public_approved_record_qr("LR-2026-UNASSIGNED") is None
    assert service.get_public_approved_record_qr("LR-2026-DRAFT") is None
    with transaction(session_factory) as session:
        rejected = session.get(Application, ids["no_document_application"])
        assert rejected is not None
        rejected.status = "rejected"
    assert service.get_public_approved_record_qr("LR-2026-NODOC") is None

    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None
    qr = service.get_public_approved_record_qr(approval.public_record_id)

    assert qr is not None
    assert qr.public_record_id == approval.public_record_id
    assert f"record_id={approval.public_record_id}" in qr.verification_url
    assert "application_id" not in qr.verification_url
    assert qr.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_public_approved_record_exposes_optional_geojson_to_map_only(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None
    synthetic_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-73.1, 40.1],
                    [-73.0, 40.1],
                    [-73.0, 40.2],
                    [-73.1, 40.2],
                    [-73.1, 40.1],
                ]
            ],
        },
        "properties": {"private_note": "must not reach map"},
    }
    with transaction(session_factory) as session:
        record = session.scalar(
            select(LandRecord).where(
                LandRecord.public_record_id == approval.public_record_id
            )
        )
        assert record is not None
        session.connection().execute(
            update(LandRecord)
            .where(LandRecord.id == record.id)
            .values(
                approved_fields_json={
                    **record.approved_fields_json,
                    "parcel_geojson": synthetic_geojson,
                    "parcel_geometry_validation_status": "verified",
                    "private_contact": "private@example.test",
                }
            )
        )

    public_record = service.get_public_approved_record(approval.public_record_id)
    assert public_record is not None
    assert public_record.parcel_geojson == synthetic_geojson
    assert public_record.parcel_geometry_validation_status == "verified"
    assert "private_contact" not in public_record.approved_fields


def test_shared_registry_includes_only_workflow_approved_public_fields(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"],
        ids["assigned_application"],
        "approve",
    )
    assert approval.public_record_id is not None

    with transaction(session_factory) as session:
        approved_record = session.scalar(
            select(LandRecord).where(
                LandRecord.public_record_id == approval.public_record_id
            )
        )
        assert approved_record is not None
        session.connection().execute(
            update(LandRecord)
            .where(LandRecord.id == approved_record.id)
            .values(
                approved_fields_json={
                    **approved_record.approved_fields_json,
                    "registration_or_document_number": "PRIVATE-DEED-123",
                    "document_date": "01/02/2020",
                    "personal_identification_number": "SYNTHETIC-PIN",
                    "private_contact_details": "private@example.test",
                    "internal_officer_notes": "Synthetic private note",
                }
            )
        )
        pending_application = session.get(Application, ids["unassigned_application"])
        rejected_application = session.get(Application, ids["draft_application"])
        officer = session.get(User, ids["officer"])
        assert pending_application is not None
        assert rejected_application is not None
        assert officer is not None
        rejected_application.status = "rejected"
        unverified_application = Application(
            application_number="SYNTHETIC-NO-APPROVAL-AUDIT",
            applicant_id=pending_application.applicant_id,
            status="approved",
            current_version=1,
        )
        session.add(unverified_application)
        session.flush()
        session.add_all(
            [
                LandRecord(
                    application=pending_application,
                    public_record_id="SYNTHETIC-PENDING",
                    current_version=1,
                    approved_fields_json={
                        "district": "North District",
                        "village": "Cedar Village",
                        "survey_or_plot_number": "SY-123",
                        "land_area": "2 acres",
                    },
                    current_record_hash="a" * 64,
                    approved_by=officer.id,
                    is_public=True,
                ),
                LandRecord(
                    application=rejected_application,
                    public_record_id="SYNTHETIC-REJECTED",
                    current_version=1,
                    approved_fields_json={
                        "district": "North District",
                        "village": "Cedar Village",
                        "survey_or_plot_number": "SY-123",
                        "land_area": "2 acres",
                    },
                    current_record_hash="b" * 64,
                    approved_by=officer.id,
                    is_public=True,
                ),
                LandRecord(
                    application=unverified_application,
                    public_record_id="SYNTHETIC-NO-APPROVAL-AUDIT",
                    current_version=1,
                    approved_fields_json={
                        "district": "North District",
                        "village": "Cedar Village",
                        "survey_or_plot_number": "SY-123",
                        "land_area": "2 acres",
                    },
                    current_record_hash="c" * 64,
                    approved_by=officer.id,
                    is_public=True,
                ),
            ]
        )

    officer_records = service.list_approved_registry(tokens["officer"])
    landowner_records = service.list_approved_registry(tokens["owner"])
    assert officer_records == landowner_records
    assert [record.public_record_id for record in officer_records] == [
        approval.public_record_id
    ]
    public_record = officer_records[0]
    assert public_record.district == "South District"
    assert public_record.village == "Pine Village"
    assert public_record.survey_or_plot_number == "SY-123"
    assert public_record.land_area == "2 acres"
    assert not hasattr(public_record, "applicant_name")
    assert not hasattr(public_record, "approved_fields_json")
    assert not hasattr(public_record, "application_number")
    assert "PRIVATE-DEED-123" not in repr(public_record)
    assert "SYNTHETIC-PIN" not in repr(public_record)
    assert "private@example.test" not in repr(public_record)
    assert "Synthetic private note" not in repr(public_record)

    with pytest.raises(AuthorizationError):
        service.get_officer_registry_internal_details(
            tokens["owner"], approval.public_record_id
        )


def test_shared_registry_search_filters_detail_and_audit_history(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"],
        ids["assigned_application"],
        "approve",
    )
    assert approval.public_record_id is not None

    by_public_id = service.list_approved_registry(
        tokens["owner"], search=approval.public_record_id.lower()
    )
    by_survey_number = service.list_approved_registry(tokens["owner"], search="sy-123")
    by_district_and_village = service.list_approved_registry(
        tokens["officer"],
        district="south district",
        village="PINE VILLAGE",
    )
    assert len(by_public_id) == len(by_survey_number) == len(by_district_and_village) == 1
    assert by_public_id[0].public_record_id == approval.public_record_id
    assert by_survey_number[0].public_record_id == approval.public_record_id
    assert by_district_and_village[0].public_record_id == approval.public_record_id

    detail = service.get_approved_registry_record(
        tokens["owner"], approval.public_record_id
    )
    assert detail is not None
    assert detail.record.current_version == 1
    assert detail.record.approved_at.replace(tzinfo=approval.decided_at.tzinfo) == approval.decided_at
    assert detail.audit_verified is True
    assert [event.event_type for event in detail.history] == [
        "application_approve",
        "approved_record_version_created",
    ]
    internal = service.get_officer_registry_internal_details(
        tokens["officer"], approval.public_record_id
    )
    assert internal is not None
    assert internal.applicant_name == "Synthetic Owner"
    assert internal.approved_fields_json["owner_name"] == "Synthetic Owner"
    with session_factory() as session:
        audit_events = session.scalars(
            select(AuditEvent).where(AuditEvent.application_id == ids["assigned_application"])
        ).all()
        assert len(audit_events) == 2
        assert all("approved_record_hash" in item.event_data_json for item in audit_events)
    verification = service.verify_audit_chain(tokens["officer"])
    assert verification.valid is True
    assert verification.event_count == 2


def test_approved_record_correction_creates_new_version_and_preserves_previous_snapshot(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None
    before = service.list_approved_record_versions(
        tokens["owner"], approval.public_record_id
    )
    before_internal = service.list_approved_record_versions(
        tokens["officer"], approval.public_record_id
    )
    assert len(before) == 1
    assert len(before_internal) == 1
    original_version = before[0]
    original_snapshot = dict(before_internal[0].approved_fields)

    with pytest.raises(AuthorizationError):
        service.correct_approved_record(
            tokens["owner"],
            approval.public_record_id,
            {"survey_or_plot_number": "SY-999"},
            "Authorized correction reason",
        )
    with pytest.raises(ValueError, match="at least 10 characters"):
        service.correct_approved_record(
            tokens["officer"],
            approval.public_record_id,
            {"survey_or_plot_number": "SY-999"},
            "typo",
        )

    correction = service.correct_approved_record(
        tokens["officer"],
        approval.public_record_id,
        {"survey_or_plot_number": "SY-999"},
        "Correct survey number against official register.",
    )
    assert correction.version_number == 2
    assert len(correction.version_hash) == 64

    after = service.list_approved_record_versions(
        tokens["owner"], approval.public_record_id
    )
    after_internal = service.list_approved_record_versions(
        tokens["officer"], approval.public_record_id
    )
    assert [version.version_number for version in after] == [1, 2]
    assert after_internal[0].approved_fields == original_snapshot
    assert after[0].version_hash == original_version.version_hash
    assert after[0].audit_event_id == original_version.audit_event_id
    assert after[1].approved_fields["survey_or_plot_number"] == "SY-999"
    assert after[1].version_hash == correction.version_hash
    assert after[1].audit_event_id == correction.audit_event_id

    current_record = service.list_approved_registry(tokens["owner"])[0]
    assert current_record.current_version == 2
    assert current_record.survey_or_plot_number == "SY-999"

    with session_factory() as session:
        version_one = session.scalar(
            select(ApprovedRecordVersion).where(
                ApprovedRecordVersion.version_number == 1,
            )
        )
        assert version_one is not None
        assert version_one.approved_fields_json == original_snapshot
        assert version_one.version_hash == original_version.version_hash
        assert version_one.audit_event.event_type == "approved_record_version_created"
        correction_event = session.get(AuditEvent, correction.audit_event_id)
        assert correction_event is not None
        assert correction_event.event_type == "approved_record_version_created"
        authorization_event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "approved_record_correction_authorized"
            )
        )
        assert authorization_event is not None
        assert authorization_event.event_data_json["changed_fields"] == [
            "survey_or_plot_number"
        ]
        assert authorization_event.event_data_json["approved_record_hash"] == correction.version_hash

    with pytest.raises(ValueError, match="immutable"):
        with transaction(session_factory) as session:
            version_one = session.scalar(
                select(ApprovedRecordVersion).where(
                    ApprovedRecordVersion.version_number == 1
                )
            )
            assert version_one is not None
            version_one.approved_fields_json = {"survey_or_plot_number": "OVERWRITTEN"}

    assert service.verify_audit_chain(tokens["officer"]).valid is True


def test_public_record_verification_is_anonymous_and_only_returns_public_fields(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    _, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None

    public_record = service.get_public_approved_record(approval.public_record_id)
    assert public_record is not None
    assert public_record.current_version == 1
    assert len(public_record.current_version_hash) == 64
    assert set(public_record.approved_fields) == {
        "district",
        "village",
        "survey_or_plot_number",
        "land_area",
    }
    assert len(public_record.versions) == 1
    version = public_record.versions[0]
    assert version.version_number == 1
    assert set(version.approved_fields) == set(public_record.approved_fields)
    assert version.version_hash == public_record.current_version_hash
    assert not hasattr(public_record, "applicant_name")
    assert not hasattr(public_record, "application_number")
    assert not hasattr(version, "authorizer_name")
    assert not hasattr(version, "audit_event_id")

    result = service.verify_public_record_audit_integrity(approval.public_record_id)
    assert result is not None
    assert result.passed is True
    assert result.audit_chain_passed is True
    assert result.record_versions_passed is True

    assert service.get_public_approved_record("UNKNOWN-PUBLIC-ID") is None
    assert service.verify_public_record_audit_integrity("UNKNOWN-PUBLIC-ID") is None


def test_public_record_verification_warns_when_audit_event_is_modified(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None
    before = service.verify_public_record_audit_integrity(approval.public_record_id)
    assert before is not None and before.passed is True

    with transaction(session_factory) as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "approved_record_version_created"
            )
        )
        assert event is not None
        session.connection().execute(
            update(AuditEvent)
            .where(AuditEvent.id == event.id)
            .values(
                event_data_json={
                    **event.event_data_json,
                    "approved_record_hash": "0" * 64,
                }
            )
        )

    result = service.verify_public_record_audit_integrity(approval.public_record_id)
    assert result is not None
    assert result.passed is False
    assert result.audit_chain_passed is False
    assert any("hash mismatch" in error.lower() for error in result.errors)


def test_public_record_integrity_detects_a_modified_version_snapshot(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None

    with transaction(session_factory) as session:
        version = session.scalar(select(ApprovedRecordVersion))
        assert version is not None
        session.connection().execute(
            update(ApprovedRecordVersion)
            .where(ApprovedRecordVersion.id == version.id)
            .values(
                approved_fields_json={
                    **version.approved_fields_json,
                    "district": "Modified District",
                }
            )
        )

    result = service.verify_public_record_audit_integrity(approval.public_record_id)
    assert result is not None
    assert result.passed is False
    assert result.audit_chain_passed is True
    assert result.record_versions_passed is False
    assert any("version 1 hash mismatch" in error.lower() for error in result.errors)


def test_persisted_chain_head_detects_removed_final_event(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    approval = service.decide_officer_application(
        tokens["officer"], ids["assigned_application"], "approve"
    )
    assert approval.public_record_id is not None
    review_started = service.start_officer_application_review(
        tokens["officer"], ids["unassigned_application"]
    )
    assert service.verify_audit_chain(tokens["officer"]).valid is True

    with transaction(session_factory) as session:
        session.execute(
            delete(AuditEvent).where(
                AuditEvent.event_type == "application_review_started",
                AuditEvent.application_id == review_started.id,
            )
        )

    verification = service.verify_audit_chain(tokens["officer"])
    assert verification.valid is False
    assert any("stored audit chain head" in error.lower() for error in verification.errors)


@pytest.mark.parametrize(
    ("decision", "comments", "expected_status", "expected_title"),
    [
        (
            "reject",
            "Survey number does not match the attached instrument.",
            "rejected",
            "Application rejected",
        ),
        (
            "request_corrections",
            "Please provide the corrected survey number and area unit.",
            "corrections_requested",
            "Corrections requested",
        ),
    ],
)
def test_rejection_and_correction_request_persist_history_and_notification(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
    decision: str,
    comments: str,
    expected_status: str,
    expected_title: str,
) -> None:
    session_factory, service, tokens, ids = review_context
    application_id = ids["assigned_application"]

    result = service.decide_officer_application(
        tokens["officer"],
        application_id,
        decision,
        comments,
    )

    assert result.status == expected_status
    assert result.public_record_id is None
    assert service.list_approved_registry(tokens["officer"]) == ()
    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == expected_status
        assert application.land_record is None
        if decision == "reject":
            assert application.rejection_reason == comments
        review_event = session.scalar(
            select(ReviewEvent).where(
                ReviewEvent.application_id == application_id,
                ReviewEvent.decision == decision,
            )
        )
        assert review_event is not None
        assert review_event.comments == comments
        assert review_event.officer_id == ids["officer"]
        notification = session.scalar(
            select(Notification).where(Notification.application_id == application_id)
        )
        assert notification is not None
        assert notification.user_id == ids["owner"]
        assert notification.title == expected_title
        assert comments in notification.message
        audit_event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.application_id == application_id,
                AuditEvent.event_type == f"application_{decision}",
            )
        )
        assert audit_event is not None
        assert audit_event.actor_id == ids["officer"]
        assert audit_event.event_data_json["decision"] == decision

    detail = service.get_application_for_review(tokens["officer"], application_id)
    assert detail is not None
    assert any(event.decision == decision and event.comments == comments for event in detail.review_events)
    if decision == "reject":
        owner_detail = service.get_landowner_application(tokens["owner"], application_id)
        assert owner_detail.status == "rejected"
        assert owner_detail.review_events[-1].decision == "reject"
        assert owner_detail.review_events[-1].comments == comments


def test_decisions_require_explanations_and_approval_checks_fields_and_documents(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    application_id = ids["assigned_application"]

    with pytest.raises(ValueError, match="meaningful rejection reason"):
        service.decide_officer_application(tokens["officer"], application_id, "reject", "no")
    with pytest.raises(ValueError, match="meaningful correction instructions"):
        service.decide_officer_application(
            tokens["officer"], application_id, "request_corrections", "fix"
        )
    with transaction(session_factory) as session:
        version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application_id,
                ApplicationVersion.version_number == 1,
            )
        )
        assert version is not None
        version.corrected_fields_json = {
            **version.corrected_fields_json,
            "land_area": "unknown",
        }
    with pytest.raises(ValueError, match="[Ll]and area"):
        service.decide_officer_application(tokens["officer"], application_id, "approve")

    with transaction(session_factory) as session:
        version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == application_id,
                ApplicationVersion.version_number == 1,
            )
        )
        assert version is not None
        version.corrected_fields_json = {
            **version.corrected_fields_json,
            "land_area": "2 acres",
        }
        session.query(Document).filter(Document.application_id == application_id).delete()
    with pytest.raises(ValueError, match="supporting document"):
        service.decide_officer_application(tokens["officer"], application_id, "approve")

    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == "under_review"
        assert application.land_record is None
        assert session.scalar(
            select(func.count()).select_from(ReviewEvent).where(
                ReviewEvent.application_id == application_id
            )
        ) == 0


def test_landowner_cannot_approve_even_by_calling_decision_service(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context

    with pytest.raises(AuthorizationError):
        service.decide_officer_application(
            tokens["owner"],
            ids["assigned_application"],
            "approve",
        )

    with session_factory() as session:
        application = session.get(Application, ids["assigned_application"])
        assert application is not None
        assert application.status == "under_review"
        assert application.land_record is None
        assert session.scalar(
            select(func.count()).select_from(ReviewEvent).where(
                ReviewEvent.application_id == ids["assigned_application"]
            )
        ) == 0


def test_approval_database_error_rolls_back_decision_and_record(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service, tokens, ids = review_context

    def fail_audit_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected audit persistence failure")

    monkeypatch.setattr("modules.database.append_audit_event", fail_audit_write)
    with pytest.raises(RuntimeError, match="injected audit persistence failure"):
        service.decide_officer_application(
            tokens["officer"],
            ids["assigned_application"],
            "approve",
        )

    with session_factory() as session:
        application = session.get(Application, ids["assigned_application"])
        assert application is not None
        assert application.status == "under_review"
        assert application.land_record is None
        assert session.scalar(
            select(func.count()).select_from(ReviewEvent).where(
                ReviewEvent.application_id == ids["assigned_application"]
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.application_id == ids["assigned_application"]
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.application_id == ids["assigned_application"]
            )
        ) == 0


def test_stale_decision_cannot_overwrite_first_review_decision(
    review_context: tuple[sessionmaker[Session], AuthService, dict[str, str], dict[str, int]],
) -> None:
    session_factory, service, tokens, ids = review_context
    application_id = ids["assigned_application"]

    first_decision = service.decide_officer_application(
        tokens["officer"],
        application_id,
        "request_corrections",
        "Please provide a corrected survey number.",
    )
    assert first_decision.status == "corrections_requested"

    with pytest.raises(AuthorizationError, match="no longer assigned"):
        service.decide_officer_application(
            tokens["officer"],
            application_id,
            "reject",
            "The submitted survey number does not match.",
        )

    with session_factory() as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.status == "corrections_requested"
        assert session.scalar(
            select(func.count()).select_from(ReviewEvent).where(
                ReviewEvent.application_id == application_id
            )
        ) == 1
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
