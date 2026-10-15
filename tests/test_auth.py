from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import (
    AuthService,
    AuthenticationError,
    AuthorizationError,
    hash_password,
    seed_demo_accounts,
)
from modules.database import (
    Application,
    AuthSession,
    Base,
    Database,
    LandRecord,
    Notification,
    User,
    create_database_engine,
    transaction,
)


@pytest.fixture
def auth_context() -> Iterator[tuple[sessionmaker[Session], AuthService]]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with transaction(session_factory) as session:
        session.add_all(
            [
                User(
                    full_name="Synthetic Landowner",
                    email="landowner@example.test",
                    password_hash=hash_password("owner-demo-password"),
                    role="landowner",
                ),
                User(
                    full_name="Synthetic Officer",
                    email="officer@example.test",
                    password_hash=hash_password("officer-demo-password"),
                    role="officer",
                ),
                User(
                    full_name="Other Synthetic Officer",
                    email="other-officer@example.test",
                    password_hash=hash_password("other-officer-password"),
                    role="officer",
                ),
                User(
                    full_name="Inactive Synthetic User",
                    email="inactive@example.test",
                    password_hash=hash_password("inactive-demo-password"),
                    role="landowner",
                    is_active=False,
                ),
            ]
        )
    yield session_factory, AuthService(Database(session_factory))
    engine.dispose()


def _application(
    session_factory: sessionmaker[Session],
    *,
    assigned_officer_id: int | None = None,
) -> int:
    with transaction(session_factory) as session:
        applicant = session.scalar(select(User).where(User.email == "landowner@example.test"))
        assert applicant is not None
        application = Application(
            application_number=f"DEMO-AUTH-{uuid4().hex[:12]}",
            applicant=applicant,
            assigned_officer_id=assigned_officer_id,
            status="submitted",
            submitted_at=datetime.now(UTC),
            current_version=1,
        )
        session.add(application)
        session.flush()
        return application.id


def test_valid_login_creates_database_backed_session(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    token = service.login("LANDOWNER@example.test", "owner-demo-password")

    principal = service.authenticate_session(token)
    assert principal.email == "landowner@example.test"
    assert principal.role == "landowner"
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == principal.email))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != "owner-demo-password"
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_invalid_password_is_rejected(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    _, service = auth_context
    with pytest.raises(AuthenticationError):
        service.login("landowner@example.test", "wrong-password")


def test_inactive_user_cannot_login(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    _, service = auth_context
    with pytest.raises(AuthenticationError):
        service.login("inactive@example.test", "inactive-demo-password")


def test_landowner_cannot_access_officer_review_functions(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    _, service = auth_context
    token = service.login("landowner@example.test", "owner-demo-password")
    with pytest.raises(AuthorizationError):
        service.list_officer_review_queue(token)


def test_officer_cannot_review_application_assigned_to_another_officer(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    with session_factory() as session:
        other_officer = session.scalar(
            select(User).where(User.email == "other-officer@example.test")
        )
        assert other_officer is not None
        other_officer_id = other_officer.id
    application_id = _application(session_factory, assigned_officer_id=other_officer_id)
    token = service.login("officer@example.test", "officer-demo-password")

    with pytest.raises(AuthorizationError):
        service.authorize_review_decision(token, application_id)


def test_officer_review_queue_obeys_configured_policy(
    auth_context: tuple[sessionmaker[Session], AuthService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service = auth_context
    with session_factory() as session:
        officer = session.scalar(select(User).where(User.email == "officer@example.test"))
        assert officer is not None
        officer_id = officer.id
    assigned_id = _application(session_factory, assigned_officer_id=officer_id)
    unassigned_id = _application(session_factory)
    token = service.login("officer@example.test", "officer-demo-password")

    assert {item.id for item in service.list_officer_review_queue(token)} == {
        assigned_id,
        unassigned_id,
    }
    monkeypatch.setenv("LAND_RECORDS_REVIEW_POLICY", "assigned_only")
    assert {item.id for item in service.list_officer_review_queue(token)} == {assigned_id}
    with pytest.raises(AuthorizationError):
        service.authorize_review_decision(token, unassigned_id)
    monkeypatch.setenv("LAND_RECORDS_REVIEW_POLICY", "")
    with pytest.raises(ValueError, match="Unsupported review policy"):
        service.list_officer_review_queue(token)


def test_inactive_account_revokes_existing_session(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    token = service.login("landowner@example.test", "owner-demo-password")
    with transaction(session_factory) as session:
        user = session.scalar(select(User).where(User.email == "landowner@example.test"))
        assert user is not None
        user.is_active = False

    with pytest.raises(AuthenticationError):
        service.authenticate_session(token)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_landowner_reads_only_own_applications_and_notifications(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    owner_token = service.login("landowner@example.test", "owner-demo-password")
    application_id = _application(session_factory)
    with transaction(session_factory) as session:
        owner = session.scalar(select(User).where(User.email == "landowner@example.test"))
        officer = session.scalar(select(User).where(User.email == "officer@example.test"))
        assert owner is not None and officer is not None
        session.add_all(
            [
                Notification(
                    user=owner,
                    application_id=application_id,
                    title="Owner update",
                    message="Private owner notification.",
                ),
                Notification(
                    user=officer,
                    application_id=application_id,
                    title="Officer update",
                    message="Private officer notification.",
                ),
            ]
        )

    applications = service.list_landowner_applications(owner_token)
    notifications = service.list_landowner_notifications(owner_token)
    assert [item.id for item in applications] == [application_id]
    assert [item.title for item in notifications] == ["Owner update"]


def test_officer_cannot_access_landowner_private_data(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    _, service = auth_context
    officer_token = service.login("officer@example.test", "officer-demo-password")

    with pytest.raises(AuthorizationError):
        service.list_landowner_applications(officer_token)
    with pytest.raises(AuthorizationError):
        service.list_landowner_notifications(officer_token)


def test_logout_revokes_session_and_cleans_database(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    token = service.login("landowner@example.test", "owner-demo-password")
    service.logout(token)
    service.logout(token)

    with pytest.raises(AuthenticationError):
        service.authenticate_session(token)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_demo_seeding_is_idempotent_and_does_not_reset_passwords(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    configured = {
        "LAND_RECORDS_DEMO_LANDOWNER_EMAIL": "seed-owner@example.test",
        "LAND_RECORDS_DEMO_LANDOWNER_PASSWORD": "initial-owner-password",
        "LAND_RECORDS_DEMO_OFFICER_EMAIL": "seed-officer@example.test",
        "LAND_RECORDS_DEMO_OFFICER_PASSWORD": "initial-officer-password",
    }
    database = Database(session_factory)
    assert seed_demo_accounts(database, configured) == ("landowner", "officer")
    changed_config = {
        **configured,
        "LAND_RECORDS_DEMO_LANDOWNER_PASSWORD": "replacement-owner-password",
        "LAND_RECORDS_DEMO_OFFICER_PASSWORD": "replacement-officer-password",
    }
    assert seed_demo_accounts(database, changed_config) == ()
    token = service.login("seed-owner@example.test", "initial-owner-password")
    assert service.authenticate_session(token).role == "landowner"
    with pytest.raises(AuthenticationError):
        service.login("seed-owner@example.test", "replacement-owner-password")


def test_landowner_dashboard_metrics_are_database_derived_and_scoped(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    token = service.login("landowner@example.test", "owner-demo-password")
    statuses = ("draft", "submitted", "under_review", "corrections_requested", "approved", "rejected")
    for status in statuses:
        with transaction(session_factory) as session:
            owner = session.scalar(select(User).where(User.email == "landowner@example.test"))
            assert owner is not None
            session.add(
                Application(
                    application_number=f"DEMO-METRIC-{uuid4().hex[:12]}",
                    applicant=owner,
                    status=status,
                    current_version=1,
                )
            )
    with transaction(session_factory) as session:
        other_owner = User(
            full_name="Another Synthetic Landowner",
            email="another-landowner@example.test",
            password_hash=hash_password("another-owner-password"),
            role="landowner",
        )
        session.add(other_owner)
        session.flush()
        session.add(
            Application(
                application_number=f"DEMO-OTHER-{uuid4().hex[:12]}",
                applicant=other_owner,
                status="approved",
                current_version=1,
            )
        )

    metrics = service.get_landowner_dashboard_metrics(token)
    assert metrics.total_applications == 6
    assert metrics.submitted_applications == 1
    assert metrics.under_review_applications == 1
    assert metrics.corrections_required_applications == 1
    assert metrics.approved_applications == 1
    assert metrics.rejected_applications == 1


def test_approved_record_page_returns_only_minimal_owner_scoped_data(
    auth_context: tuple[sessionmaker[Session], AuthService],
) -> None:
    session_factory, service = auth_context
    token = service.login("landowner@example.test", "owner-demo-password")
    with transaction(session_factory) as session:
        owner = session.scalar(select(User).where(User.email == "landowner@example.test"))
        officer = session.scalar(select(User).where(User.email == "officer@example.test"))
        other_owner = User(
            full_name="Registry Test Landowner",
            email="registry-owner@example.test",
            password_hash=hash_password("registry-owner-password"),
            role="landowner",
        )
        assert owner is not None and officer is not None
        session.add(other_owner)
        session.flush()
        own_application = Application(
            application_number=f"DEMO-RECORD-{uuid4().hex[:12]}",
            applicant=owner,
            status="approved",
            current_version=1,
        )
        other_application = Application(
            application_number=f"DEMO-RECORD-{uuid4().hex[:12]}",
            applicant=other_owner,
            status="approved",
            current_version=1,
        )
        session.add_all([own_application, other_application])
        session.flush()
        own_record = LandRecord(
            application=own_application,
            public_record_id="SYNTHETIC-RECORD-001",
            current_version=1,
            approved_fields_json={
                "personal_data": "must not be included in the summary",
                "internal_officer_note": "must not be included in the summary",
            },
            current_record_hash="d" * 64,
            approver=officer,
            is_public=True,
        )
        other_record = LandRecord(
            application=other_application,
            public_record_id="SYNTHETIC-RECORD-002",
            current_version=1,
            approved_fields_json={"personal_data": "another user's data"},
            current_record_hash="e" * 64,
            approver=officer,
            is_public=True,
        )
        session.add_all([own_record, other_record])

    records = service.list_landowner_approved_records(token)
    assert len(records) == 1
    assert records[0].public_record_id == "SYNTHETIC-RECORD-001"
    assert not hasattr(records[0], "approved_fields_json")
    assert not hasattr(records[0], "document")
