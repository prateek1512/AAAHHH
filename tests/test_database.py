from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from modules.database import (
    Application,
    ApplicationVersion,
    AuthSession,
    AuditEvent,
    ApprovedRecordVersion,
    Base,
    Document,
    LandRecord,
    Notification,
    ReviewEvent,
    User,
    create_database_engine,
    transaction,
    initialize_database,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


def test_initializes_all_expected_tables(session_factory: sessionmaker[Session]) -> None:
    engine = session_factory.kw["bind"]
    assert isinstance(engine, Engine)
    assert set(inspect(engine).get_table_names()) == {
        "users",
        "applications",
        "documents",
        "application_versions",
        "review_events",
        "land_records",
        "approved_record_versions",
        "notifications",
        "audit_events",
        "audit_chain_state",
        "auth_sessions",
    }


def test_models_persist_and_relationships_resolve(
    session_factory: sessionmaker[Session],
) -> None:
    with transaction(session_factory) as session:
        landowner = User(
            full_name="Synthetic Landowner",
            email="landowner@example.test",
            password_hash="test-hash-not-a-password",
            role="landowner",
        )
        officer = User(
            full_name="Synthetic Officer",
            email="officer@example.test",
            password_hash="test-hash-not-a-password",
            role="officer",
        )
        application = Application(
            application_number="DEMO-0001",
            applicant=landowner,
            assigned_officer=officer,
            status="under_review",
            current_version=1,
        )
        document = Document(
            application=application,
            original_filename="synthetic-record.pdf",
            stored_path="private/documents/synthetic-record.pdf",
            document_sha256="a" * 64,
            mime_type="application/pdf",
        )
        version = ApplicationVersion(
            application=application,
            version_number=1,
            extracted_fields_json={"parcel_reference": "DEMO-PARCEL-1"},
            corrected_fields_json={"parcel_reference": "DEMO-PARCEL-1"},
            creator=landowner,
        )
        review = ReviewEvent(
            application=application,
            officer=officer,
            decision="request_corrections",
            comments="Please review the synthetic parcel reference.",
        )
        record = LandRecord(
            application=application,
            public_record_id="DEMO-RECORD-0001",
            current_version=1,
            approved_fields_json={"parcel_reference": "DEMO-PARCEL-1"},
            current_record_hash="b" * 64,
            approver=officer,
            is_public=False,
        )
        notification = Notification(
            user=landowner,
            application=application,
            title="Application update",
            message="A synthetic update is available.",
        )
        audit_event = AuditEvent(
            event_type="application_submitted",
            actor=landowner,
            application=application,
            event_data_json={"application_number": "DEMO-0001"},
            event_hash="c" * 64,
        )
        session.add_all([document, version, review, record, notification, audit_event])
        session.flush()
        application_id = application.id

    with session_factory() as session:
        persisted = session.get(Application, application_id)
        assert persisted is not None
        assert persisted.applicant.email == "landowner@example.test"
        assert persisted.assigned_officer is not None
        assert persisted.assigned_officer.role == "officer"
        assert persisted.documents[0].stored_path.endswith("synthetic-record.pdf")
        assert persisted.versions[0].extracted_fields_json != {}
        assert persisted.versions[0].corrected_fields_json["parcel_reference"] == "DEMO-PARCEL-1"
        assert persisted.review_events[0].decision == "request_corrections"
        assert persisted.land_record is not None
        assert persisted.land_record.is_public is False
        assert persisted.notifications[0].user.email == "landowner@example.test"
        assert persisted.audit_events[0].actor.role == "landowner"


def test_audit_events_reject_orm_edits_and_deletes(
    session_factory: sessionmaker[Session],
) -> None:
    with transaction(session_factory) as session:
        actor = User(
            full_name="Synthetic Audit Actor",
            email="audit-actor@example.test",
            password_hash="test-hash-not-a-password",
            role="officer",
        )
        session.add(actor)
        session.flush()
        audit_event = AuditEvent(
            event_type="application_submitted",
            actor=actor,
            event_data_json={"application_number": "SYNTHETIC-1"},
            event_hash="d" * 64,
        )
        session.add(audit_event)
        session.flush()
        audit_event_id = audit_event.id

    with pytest.raises(ValueError, match="append-only"):
        with transaction(session_factory) as session:
            audit_event = session.get(AuditEvent, audit_event_id)
            assert audit_event is not None
            audit_event.event_data_json = {"application_number": "CHANGED"}

    with pytest.raises(ValueError, match="append-only"):
        with transaction(session_factory) as session:
            audit_event = session.get(AuditEvent, audit_event_id)
            assert audit_event is not None
            session.delete(audit_event)


def test_database_initialization_backfills_existing_approved_record_as_version_one(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'approved-record-migration.db'}"
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with transaction(session_factory) as session:
        owner = User(
            full_name="Synthetic Migration Owner",
            email="migration-owner@example.test",
            password_hash="test-hash-not-a-password",
            role="landowner",
        )
        officer = User(
            full_name="Synthetic Migration Officer",
            email="migration-officer@example.test",
            password_hash="test-hash-not-a-password",
            role="officer",
        )
        session.add_all([owner, officer])
        session.flush()
        application = Application(
            application_number="SYNTHETIC-MIGRATION-APP",
            applicant_id=owner.id,
            status="approved",
            current_version=4,
        )
        session.add(application)
        session.flush()
        record = LandRecord(
            application_id=application.id,
            public_record_id="SYNTHETIC-MIGRATION-RECORD",
            current_version=4,
            approved_fields_json={
                "owner_name": "Synthetic Migration Owner",
                "district": "Synthetic District",
                "village": "Synthetic Village",
                "survey_or_plot_number": "SYN-1",
                "land_area": "2 acres",
                "document_type": "Sale deed",
            },
            current_record_hash="a" * 64,
            approved_by=officer.id,
            is_public=True,
        )
        session.add(record)
        session.flush()
        event = AuditEvent(
            event_type="application_approve",
            actor_id=officer.id,
            application_id=application.id,
            land_record_id=record.id,
            event_data_json={"public_record_id": record.public_record_id},
            event_hash="b" * 64,
        )
        session.add(event)
        session.flush()
        record_id = record.id
        event_id = event.id
        original_fields = dict(record.approved_fields_json)
    engine.dispose()

    initialize_database(database_url)

    migrated_engine = create_database_engine(database_url)
    migrated_factory = sessionmaker(
        bind=migrated_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    with migrated_factory() as session:
        record = session.get(LandRecord, record_id)
        version = session.scalar(
            select(ApprovedRecordVersion).where(
                ApprovedRecordVersion.land_record_id == record_id
            )
        )
        assert record is not None
        assert version is not None
        assert record.current_version == version.version_number == 1
        assert version.approved_fields_json == original_fields
        assert version.audit_event_id == event_id
        assert version.authorized_by == record.approved_by
        assert version.version_hash == record.current_record_hash
    migrated_engine.dispose()


def test_foreign_keys_are_enforced(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError):
        with transaction(session_factory) as session:
            session.add(
                Application(
                    application_number="DEMO-ORPHAN",
                    applicant_id=9999,
                    status="draft",
                    current_version=1,
                )
            )
            session.flush()
