from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from modules.database import (
    Application,
    ApplicationVersion,
    AuthSession,
    AuditEvent,
    Base,
    Document,
    LandRecord,
    Notification,
    ReviewEvent,
    User,
    create_database_engine,
    transaction,
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
        "notifications",
        "audit_events",
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
