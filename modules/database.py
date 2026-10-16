"""SQLAlchemy models and database setup for the land records application."""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    exists,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    aliased,
    joinedload,
    mapped_column,
    relationship,
    sessionmaker,
)

from modules.audit import (
    AuditVerificationResult,
    CanonicalJSON,
    GENESIS_HASH,
    append_audit_event,
    calculate_approved_record_version_hash,
    verify_audit_chain as verify_audit_event_chain,
)
from modules.field_validation import (
    FIELD_LABELS,
    snapshot_field_warnings,
    validate_corrected_fields,
)

DEFAULT_DATABASE_URL = "sqlite:///./data/land_records.db"
DATABASE_URL_ENV = "LAND_RECORDS_DATABASE_URL"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base class for all persisted models."""


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('landowner', 'officer')",
            name="ck_users_role",
        ),
        Index("ix_users_role_active", "role", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    applicant_applications: Mapped[list[Application]] = relationship(
        back_populates="applicant",
        foreign_keys="Application.applicant_id",
        passive_deletes="all",
    )
    assigned_applications: Mapped[list[Application]] = relationship(
        back_populates="assigned_officer",
        foreign_keys="Application.assigned_officer_id",
        passive_deletes="all",
    )
    created_versions: Mapped[list[ApplicationVersion]] = relationship(
        back_populates="creator",
        foreign_keys="ApplicationVersion.created_by",
        passive_deletes="all",
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(
        back_populates="officer",
        foreign_keys="ReviewEvent.officer_id",
        passive_deletes="all",
    )
    approved_records: Mapped[list[LandRecord]] = relationship(
        back_populates="approver",
        foreign_keys="LandRecord.approved_by",
        passive_deletes="all",
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user",
        passive_deletes="all",
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user",
        passive_deletes="all",
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="actor",
        foreign_keys="AuditEvent.actor_id",
        passive_deletes="all",
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'submitted', 'under_review', "
            "'corrections_requested', 'approved', 'rejected')",
            name="ck_applications_status",
        ),
        CheckConstraint("current_version >= 1", name="ck_applications_current_version"),
        Index("ix_applications_status_submitted_at", "status", "submitted_at"),
        Index("ix_applications_assigned_officer_status", "assigned_officer_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    assigned_officer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    applicant: Mapped[User] = relationship(
        back_populates="applicant_applications",
        foreign_keys=[applicant_id],
    )
    assigned_officer: Mapped[User | None] = relationship(
        back_populates="assigned_applications",
        foreign_keys=[assigned_officer_id],
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="application",
        passive_deletes="all",
    )
    versions: Mapped[list[ApplicationVersion]] = relationship(
        back_populates="application",
        passive_deletes="all",
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(
        back_populates="application",
        passive_deletes="all",
    )
    land_record: Mapped[LandRecord | None] = relationship(
        back_populates="application",
        uselist=False,
        passive_deletes="all",
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="application",
        passive_deletes="all",
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="application",
        foreign_keys="AuditEvent.application_id",
        passive_deletes="all",
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(document_sha256) = 64", name="ck_documents_sha256_length"),
        Index("ix_documents_application_uploaded_at", "application_id", "uploaded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    application: Mapped[Application] = relationship(back_populates="documents")


class ApplicationVersion(Base):
    __tablename__ = "application_versions"
    __table_args__ = (
        UniqueConstraint("application_id", "version_number", name="uq_application_version_number"),
        CheckConstraint("version_number >= 1", name="ck_application_versions_number"),
        Index("ix_application_versions_created_at", "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_fields_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    corrected_fields_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    application: Mapped[Application] = relationship(back_populates="versions")
    creator: Mapped[User] = relationship(
        back_populates="created_versions",
        foreign_keys=[created_by],
    )


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'reject', 'request_corrections')",
            name="ck_review_events_decision",
        ),
        Index("ix_review_events_application_created_at", "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    officer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    application: Mapped[Application] = relationship(back_populates="review_events")
    officer: Mapped[User] = relationship(
        back_populates="review_events",
        foreign_keys=[officer_id],
    )


class LandRecord(Base):
    __tablename__ = "land_records"
    __table_args__ = (
        CheckConstraint("current_version >= 1", name="ck_land_records_current_version"),
        CheckConstraint("length(current_record_hash) = 64", name="ck_land_records_hash_length"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    public_record_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_fields_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    current_record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    application: Mapped[Application] = relationship(back_populates="land_record")
    approver: Mapped[User] = relationship(
        back_populates="approved_records",
        foreign_keys=[approved_by],
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="land_record",
        foreign_keys="AuditEvent.land_record_id",
        passive_deletes="all",
    )
    versions: Mapped[list[ApprovedRecordVersion]] = relationship(
        back_populates="land_record",
        order_by="ApprovedRecordVersion.version_number",
        passive_deletes="all",
    )


class ApprovedRecordVersion(Base):
    __tablename__ = "approved_record_versions"
    __table_args__ = (
        UniqueConstraint(
            "land_record_id",
            "version_number",
            name="uq_approved_record_version_number",
        ),
        UniqueConstraint("audit_event_id", name="uq_approved_record_version_audit_event"),
        CheckConstraint("version_number >= 1", name="ck_approved_record_version_number"),
        CheckConstraint("length(version_hash) = 64", name="ck_approved_record_version_hash"),
        Index("ix_approved_record_versions_record_version", "land_record_id", "version_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    land_record_id: Mapped[int] = mapped_column(
        ForeignKey("land_records.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_fields_json: Mapped[dict[str, object]] = mapped_column(
        CanonicalJSON(), nullable=False
    )
    version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    audit_event_id: Mapped[int] = mapped_column(
        ForeignKey("audit_events.id", ondelete="RESTRICT"), nullable=False
    )

    land_record: Mapped[LandRecord] = relationship(back_populates="versions")
    authorizer: Mapped[User] = relationship(foreign_keys=[authorized_by])
    audit_event: Mapped[AuditEvent] = relationship(foreign_keys=[audit_event_id])


@event.listens_for(ApprovedRecordVersion, "before_update")
def _reject_approved_version_update(
    mapper: object, connection: object, target: ApprovedRecordVersion
) -> None:
    raise ValueError(
        f"Approved record version {target.id} is immutable and cannot be edited."
    )


@event.listens_for(ApprovedRecordVersion, "before_delete")
def _reject_approved_version_delete(
    mapper: object, connection: object, target: ApprovedRecordVersion
) -> None:
    raise ValueError(
        f"Approved record version {target.id} is immutable and cannot be deleted."
    )


@event.listens_for(LandRecord, "before_update")
def _reject_land_record_update(
    mapper: object, connection: object, target: LandRecord
) -> None:
    raise ValueError(
        f"Approved record {target.public_record_id} can only be changed through "
        "the authorized correction workflow."
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read_created_at", "user_id", "is_read", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="notifications")
    application: Mapped[Application | None] = relationship(back_populates="notifications")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("length(event_hash) = 64", name="ck_audit_events_hash_length"),
        CheckConstraint(
            "length(previous_event_hash) = 64",
            name="ck_audit_events_previous_hash_length",
        ),
        Index("ix_audit_events_application_created_at", "application_id", "created_at"),
        Index("ix_audit_events_land_record_created_at", "land_record_id", "created_at"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT")
    )
    land_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("land_records.id", ondelete="RESTRICT")
    )
    event_data_json: Mapped[dict[str, object]] = mapped_column(
        CanonicalJSON(), nullable=False, default=dict
    )
    previous_event_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=GENESIS_HASH
    )
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    actor: Mapped[User] = relationship(
        back_populates="audit_events",
        foreign_keys=[actor_id],
    )
    application: Mapped[Application | None] = relationship(
        back_populates="audit_events",
        foreign_keys=[application_id],
    )
    land_record: Mapped[LandRecord | None] = relationship(
        back_populates="audit_events",
        foreign_keys=[land_record_id],
    )


class AuditChainState(Base):
    __tablename__ = "audit_chain_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    head_hash: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(AuditEvent, "before_update")
def _reject_audit_event_update(
    mapper: object, connection: object, target: AuditEvent
) -> None:
    raise ValueError(f"Audit event {target.id} is append-only and cannot be edited.")


@event.listens_for(AuditEvent, "before_delete")
def _reject_audit_event_delete(
    mapper: object, connection: object, target: AuditEvent
) -> None:
    raise ValueError(f"Audit event {target.id} is append-only and cannot be deleted.")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship(back_populates="auth_sessions")


@dataclass(frozen=True, slots=True)
class AuthUserData:
    user_id: int
    full_name: str
    email: str
    password_hash: str
    role: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class ApplicationData:
    id: int
    application_number: str
    status: str
    submitted_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OfficerQueueApplicationData:
    id: int
    application_number: str
    status: str
    submitted_at: datetime | None
    updated_at: datetime
    applicant_name: str
    applicant_email: str
    district: str | None
    village: str | None


@dataclass(frozen=True, slots=True)
class LandownerApplicationDetailData:
    id: int
    application_number: str
    status: str
    submitted_at: datetime | None
    updated_at: datetime
    current_version: int
    corrected_fields_json: dict[str, object]
    document_filenames: tuple[str, ...]
    document_ids: tuple[int, ...]
    review_events: tuple[LandownerReviewEventData, ...]
    public_record_id: str | None


@dataclass(frozen=True, slots=True)
class LandownerReviewEventData:
    id: int
    decision: str
    comments: str | None
    created_at: datetime
    officer_name: str


@dataclass(frozen=True, slots=True)
class ApplicationSubmissionData:
    id: int
    application_number: str
    status: str
    submitted_at: datetime
    current_version: int
    corrected_fields_json: dict[str, object]


@dataclass(frozen=True, slots=True)
class OfficerReviewStartedData:
    id: int
    application_number: str
    status: str
    assigned_officer_id: int


@dataclass(frozen=True, slots=True)
class OfficerDocumentData:
    id: int
    application_id: int
    original_filename: str
    stored_path: str
    mime_type: str
    uploaded_at: datetime


@dataclass(frozen=True, slots=True)
class ApplicationDecisionData:
    application_id: int
    application_number: str
    decision: str
    status: str
    decided_at: datetime
    public_record_id: str | None


@dataclass(frozen=True, slots=True)
class NotificationData:
    id: int
    application_id: int | None
    title: str
    message: str
    is_read: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LandRecordSummaryData:
    public_record_id: str
    approved_at: datetime
    current_version: int


@dataclass(frozen=True, slots=True)
class ApprovedRegistryRecordData:
    public_record_id: str
    approved_at: datetime
    current_version: int
    current_version_hash: str
    district: str | None
    village: str | None
    survey_or_plot_number: str | None
    land_area: str | None
    parcel_geojson: object | None = None
    parcel_geometry_validation_status: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovedRegistryHistoryData:
    event_type: str
    created_at: datetime
    integrity_verified: bool


@dataclass(frozen=True, slots=True)
class ApprovedRecordVersionData:
    version_number: int
    approved_fields_json: dict[str, object]
    version_hash: str
    authorized_by: int
    authorizer_name: str
    authorized_at: datetime
    audit_event_id: int
    audit_event_type: str


@dataclass(frozen=True, slots=True)
class ApprovedRecordVersionChangeData:
    public_record_id: str
    version_number: int
    version_hash: str
    authorized_at: datetime
    audit_event_id: int


@dataclass(frozen=True, slots=True)
class OfficerRegistryInternalDetailsData:
    application_number: str
    applicant_name: str
    approved_fields_json: dict[str, object]
    approver_name: str
    review_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentData:
    id: int
    application_id: int
    application_number: str
    original_filename: str
    stored_path: str
    document_sha256: str
    mime_type: str
    uploaded_at: datetime
    ocr_result_json: dict[str, object] | None = None
    corrected_fields_json: dict[str, object] | None = None
    application_status: str = "draft"


def _document_data(document: Document) -> DocumentData:
    application = document.application
    version = next(
        (
            candidate
            for candidate in application.versions
            if candidate.version_number == application.current_version
        ),
        None,
    )
    return DocumentData(
        id=document.id,
        application_id=document.application_id,
        application_number=application.application_number,
        original_filename=document.original_filename,
        stored_path=document.stored_path,
        document_sha256=document.document_sha256,
        mime_type=document.mime_type,
        uploaded_at=document.uploaded_at,
        ocr_result_json=version.extracted_fields_json if version is not None else None,
        corrected_fields_json=version.corrected_fields_json if version is not None else None,
        application_status=application.status,
    )


def _case_insensitive_pattern(value: str) -> str:
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _approved_registry_record_data(record: LandRecord) -> ApprovedRegistryRecordData:
    fields = record.approved_fields_json

    def published_value(key: str) -> str | None:
        value = fields.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return ApprovedRegistryRecordData(
        public_record_id=record.public_record_id,
        approved_at=record.approved_at,
        current_version=record.current_version,
        current_version_hash=record.current_record_hash,
        district=published_value("district"),
        village=published_value("village"),
        survey_or_plot_number=published_value("survey_or_plot_number"),
        land_area=published_value("land_area"),
        parcel_geojson=fields.get("parcel_geojson"),
        parcel_geometry_validation_status=(
            fields.get("parcel_geometry_validation_status")
            if fields.get("parcel_geometry_validation_status")
            in {"valid", "verified", "warning", "needs_review", "invalid", "unknown"}
            else None
        ),
    )


def _canonical_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_json_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class DemoUserData:
    full_name: str
    email: str
    password_hash: str
    role: str


class DemoAccountRoleConflict(Exception):
    """Raised when a configured demo email already has a different role."""


class Database:
    """Database operations used by services; ORM instances stay inside this module."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def find_auth_user(self, email: str) -> AuthUserData | None:
        with self._session_factory() as session:
            user = session.scalar(select(User).where(func.lower(User.email) == email))
            if user is None:
                return None
            return AuthUserData(
                user_id=user.id,
                full_name=user.full_name,
                email=user.email,
                password_hash=user.password_hash,
                role=user.role,
                is_active=user.is_active,
            )

    def create_auth_session(
        self,
        *,
        user_id: int,
        expected_password_hash: str,
        token_hash: str,
        expires_at: datetime,
        expected_role: str | None = None,
        replacement_password_hash: str | None = None,
    ) -> bool:
        with transaction(self._session_factory) as session:
            user = session.get(User, user_id)
            if (
                user is None
                or not user.is_active
                or user.password_hash != expected_password_hash
                or (expected_role is not None and user.role != expected_role)
            ):
                return False
            if replacement_password_hash is not None:
                user.password_hash = replacement_password_hash
            session.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )
            return True

    def find_session_user(self, token_hash: str, now: datetime) -> AuthUserData | None:
        with transaction(self._session_factory) as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.token_hash == token_hash)
            )
            if auth_session is None:
                return None
            user = auth_session.user
            expires_at = auth_session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now or not user.is_active:
                session.delete(auth_session)
                return None
            return AuthUserData(
                user_id=user.id,
                full_name=user.full_name,
                email=user.email,
                password_hash=user.password_hash,
                role=user.role,
                is_active=user.is_active,
            )

    def revoke_auth_session(self, token_hash: str) -> None:
        with transaction(self._session_factory) as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.token_hash == token_hash)
            )
            if auth_session is not None:
                session.delete(auth_session)

    def list_landowner_applications(self, user_id: int) -> tuple[ApplicationData, ...]:
        with self._session_factory() as session:
            applications = session.scalars(
                select(Application)
                .where(Application.applicant_id == user_id)
                .order_by(Application.updated_at.desc(), Application.id.desc())
            )
            return tuple(
                ApplicationData(
                    id=application.id,
                    application_number=application.application_number,
                    status=application.status,
                    submitted_at=application.submitted_at,
                    updated_at=application.updated_at,
                )
                for application in applications
            )

    def get_landowner_application(
        self, user_id: int, application_id: int
    ) -> LandownerApplicationDetailData | None:
        with self._session_factory() as session:
            application = session.scalar(
                select(Application).where(
                    Application.id == application_id,
                    Application.applicant_id == user_id,
                )
            )
            if application is None:
                return None
            version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application.id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            documents = session.execute(
                select(Document.id, Document.original_filename)
                .where(Document.application_id == application.id)
                .order_by(Document.uploaded_at, Document.id)
            ).all()
            review_events = session.execute(
                select(ReviewEvent, User.full_name)
                .join(User, ReviewEvent.officer_id == User.id)
                .where(ReviewEvent.application_id == application.id)
                .order_by(ReviewEvent.created_at, ReviewEvent.id)
            ).all()
            return LandownerApplicationDetailData(
                id=application.id,
                application_number=application.application_number,
                status=application.status,
                submitted_at=application.submitted_at,
                updated_at=application.updated_at,
                current_version=application.current_version,
                corrected_fields_json=(
                    dict(version.corrected_fields_json) if version is not None else {}
                ),
                document_filenames=tuple(filename for _, filename in documents),
                document_ids=tuple(document_id for document_id, _ in documents),
                review_events=tuple(
                    LandownerReviewEventData(
                        id=event.id,
                        decision=event.decision,
                        comments=event.comments,
                        created_at=event.created_at,
                        officer_name=officer_name,
                    )
                    for event, officer_name in review_events
                ),
                public_record_id=(
                    application.land_record.public_record_id
                    if application.land_record is not None
                    else None
                ),
            )

    def get_application_discrepancy_context(
        self, application_id: int
    ) -> dict[str, object]:
        """Load only comparison fields needed by deterministic discrepancy rules."""
        with self._session_factory() as session:
            application = session.get(Application, application_id)
            if application is None:
                return {
                    "extracted_fields": {},
                    "previous_fields": None,
                    "other_applications": (),
                    "approved_records": (),
                }
            current = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application_id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            previous = (
                session.scalar(
                    select(ApplicationVersion).where(
                        ApplicationVersion.application_id == application_id,
                        ApplicationVersion.version_number == application.current_version - 1,
                    )
                )
                if application.current_version > 1
                else None
            )
            active_applications = session.execute(
                select(
                    Application.application_number,
                    Application.status,
                    ApplicationVersion.corrected_fields_json,
                )
                .join(
                    ApplicationVersion,
                    (ApplicationVersion.application_id == Application.id)
                    & (ApplicationVersion.version_number == Application.current_version),
                )
                .where(
                    Application.id != application_id,
                    Application.status.in_(
                        ("submitted", "under_review", "corrections_requested")
                    ),
                )
            ).all()
            approved_records = session.scalars(
                select(LandRecord).where(
                    LandRecord.application_id != application_id,
                    LandRecord.is_public.is_(True),
                )
            ).all()
            audit_event_types = tuple(
                session.scalars(
                    select(AuditEvent.event_type).where(
                        AuditEvent.application_id == application_id
                    )
                ).all()
            )

            extracted_json = current.extracted_fields_json if current else {}
            extracted_fields = extracted_json.get("fields", {})
            return {
                "extracted_fields": (
                    dict(extracted_fields) if isinstance(extracted_fields, dict) else {}
                ),
                "previous_fields": (
                    dict(previous.corrected_fields_json) if previous is not None else None
                ),
                "other_applications": tuple(
                    {
                        "application_number": application_number,
                        "status": status,
                        "fields": dict(corrected_fields),
                    }
                    for application_number, status, corrected_fields in active_applications
                ),
                "approved_records": tuple(
                    {
                        "public_record_id": record.public_record_id,
                        "fields": {
                            field_name: record.approved_fields_json.get(field_name)
                            for field_name in (
                                "district",
                                "village",
                                "survey_or_plot_number",
                                "land_area",
                            )
                        },
                    }
                    for record in approved_records
                ),
                "audit_event_types": audit_event_types,
            }

    def create_landowner_application_document(
        self,
        *,
        user_id: int,
        application_number: str,
        original_filename: str,
        stored_path: str,
        document_sha256: str,
        mime_type: str,
    ) -> DocumentData:
        with transaction(self._session_factory) as session:
            applicant = session.get(User, user_id)
            if applicant is None or applicant.role != "landowner" or not applicant.is_active:
                raise PermissionError("An active landowner is required to upload a document.")
            application = Application(
                application_number=application_number,
                applicant=applicant,
                status="draft",
                current_version=1,
            )
            document = Document(
                application=application,
                original_filename=original_filename,
                stored_path=stored_path,
                document_sha256=document_sha256,
                mime_type=mime_type,
            )
            session.add(document)
            session.flush()
            return _document_data(document)

    def save_landowner_ocr_result(
        self,
        *,
        user_id: int,
        document_id: int,
        ocr_result_json: dict[str, object],
    ) -> DocumentData:
        with transaction(self._session_factory) as session:
            document = session.scalar(
                select(Document)
                .join(Application, Document.application_id == Application.id)
                .where(
                    Document.id == document_id,
                    Application.applicant_id == user_id,
                )
            )
            if document is None:
                raise PermissionError("The document does not belong to this landowner.")
            application = document.application
            version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application.id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            if version is None:
                version = ApplicationVersion(
                    application=application,
                    version_number=application.current_version,
                    extracted_fields_json=snapshot_field_warnings(
                        ocr_result_json,
                        {},
                    ),
                    corrected_fields_json={},
                    created_by=user_id,
                )
                session.add(version)
            else:
                version.extracted_fields_json = snapshot_field_warnings(
                    ocr_result_json,
                    version.corrected_fields_json,
                )
            session.flush()
            return _document_data(document)

    def save_landowner_document_corrections(
        self,
        *,
        user_id: int,
        document_id: int,
        corrected_fields_json: dict[str, object],
    ) -> DocumentData:
        with transaction(self._session_factory) as session:
            document = session.scalar(
                select(Document)
                .join(Application, Document.application_id == Application.id)
                .where(
                    Document.id == document_id,
                    Application.applicant_id == user_id,
                )
            )
            if document is None:
                raise PermissionError("The document does not belong to this landowner.")
            application = document.application
            if application.status not in ("draft", "corrections_requested"):
                raise PermissionError(
                    "Only draft applications or applications requiring corrections can be edited."
                )
            current_version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application.id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            if (
                current_version is not None
                and current_version.corrected_fields_json == corrected_fields_json
            ):
                return _document_data(document)
            extracted_fields_json = snapshot_field_warnings(
                current_version.extracted_fields_json if current_version is not None else {},
                corrected_fields_json,
            )
            version = ApplicationVersion(
                application=application,
                version_number=application.current_version + 1,
                extracted_fields_json=extracted_fields_json,
                corrected_fields_json=corrected_fields_json,
                created_by=user_id,
            )
            application.current_version = version.version_number
            session.add(version)
            session.flush()
            return _document_data(document)

    def add_landowner_supporting_document(
        self,
        *,
        user_id: int,
        application_id: int,
        original_filename: str,
        stored_path: str,
        document_sha256: str,
        mime_type: str,
    ) -> DocumentData:
        with transaction(self._session_factory) as session:
            application = session.scalar(
                select(Application).where(
                    Application.id == application_id,
                    Application.applicant_id == user_id,
                )
            )
            if application is None:
                raise PermissionError("The application does not belong to this landowner.")
            if application.status != "corrections_requested":
                raise PermissionError(
                    "Supporting documents can only be added when corrections are requested."
                )
            document = Document(
                application_id=application.id,
                original_filename=original_filename,
                stored_path=stored_path,
                document_sha256=document_sha256,
                mime_type=mime_type,
            )
            session.add(document)
            session.flush()
            return _document_data(document)

    def submit_landowner_application(
        self, *, user_id: int, application_id: int
    ) -> ApplicationSubmissionData:
        """Atomically snapshot and submit a draft with its audit and notification."""
        with transaction(self._session_factory) as session:
            application = session.scalar(
                select(Application).where(
                    Application.id == application_id,
                    Application.applicant_id == user_id,
                )
            )
            if application is None:
                raise PermissionError("The application does not belong to this landowner.")
            if application.status != "draft":
                raise ValueError("This application is no longer a draft and cannot be submitted again.")

            documents = session.scalars(
                select(Document.id).where(Document.application_id == application.id).limit(1)
            ).all()
            if not documents:
                raise ValueError("Upload a supporting document before submitting this application.")

            current_version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application.id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            if current_version is None:
                raise ValueError("Review the application fields before submitting.")

            submitted_at = utc_now()
            version_number = application.current_version + 1
            snapshot = ApplicationVersion(
                application_id=application.id,
                version_number=version_number,
                extracted_fields_json=dict(current_version.extracted_fields_json),
                corrected_fields_json=dict(current_version.corrected_fields_json),
                created_at=submitted_at,
                created_by=user_id,
            )
            session.add(snapshot)

            submitted_application_id = session.scalar(
                update(Application)
                .where(
                    Application.id == application.id,
                    Application.applicant_id == user_id,
                    Application.status == "draft",
                )
                .values(
                    status="submitted",
                    submitted_at=submitted_at,
                    updated_at=submitted_at,
                    current_version=version_number,
                )
                .returning(Application.id)
            )
            if submitted_application_id is None:
                raise ValueError(
                    "This application has already been submitted or is no longer a draft."
                )

            append_audit_event(
                session,
                event_type="application_submitted",
                actor_id=user_id,
                application_id=application.id,
                land_record_id=None,
                event_data={
                    "application_number": application.application_number,
                    "version_number": version_number,
                },
                created_at=submitted_at,
            )
            session.add(
                Notification(
                    user_id=user_id,
                    application_id=application.id,
                    title="Application submitted",
                    message=(
                        f"Application {application.application_number} was submitted "
                        "successfully and is pending verification."
                    ),
                    created_at=submitted_at,
                )
            )
            session.flush()
            return ApplicationSubmissionData(
                id=application.id,
                application_number=application.application_number,
                status="submitted",
                submitted_at=submitted_at,
                current_version=version_number,
                corrected_fields_json=dict(snapshot.corrected_fields_json),
            )

    def resubmit_landowner_application(
        self,
        *,
        user_id: int,
        application_id: int,
        corrected_fields_json: dict[str, object] | None = None,
    ) -> ApplicationSubmissionData:
        """Create a new immutable submission snapshot after requested corrections."""
        with transaction(self._session_factory) as session:
            application = session.scalar(
                select(Application).where(
                    Application.id == application_id,
                    Application.applicant_id == user_id,
                )
            )
            if application is None:
                raise PermissionError("The application does not belong to this landowner.")
            if application.status != "corrections_requested":
                raise ValueError(
                    "This application is not awaiting corrections and cannot be resubmitted."
                )

            current_version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application.id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            if current_version is None:
                raise ValueError("Save the revised application fields before resubmitting.")
            if not session.scalar(
                select(Document.id)
                .where(Document.application_id == application.id)
                .limit(1)
            ):
                raise ValueError("A supporting document is required before resubmission.")

            submitted_at = utc_now()
            source_version_number = current_version.version_number
            revised_fields = dict(current_version.corrected_fields_json)
            if (
                corrected_fields_json is not None
                and corrected_fields_json != current_version.corrected_fields_json
            ):
                source_version_number += 1
                revised_fields = dict(corrected_fields_json)
                session.add(
                    ApplicationVersion(
                        application_id=application.id,
                        version_number=source_version_number,
                        extracted_fields_json=snapshot_field_warnings(
                            current_version.extracted_fields_json,
                            revised_fields,
                        ),
                        corrected_fields_json=revised_fields,
                        created_at=submitted_at,
                        created_by=user_id,
                    )
                )
            version_number = source_version_number + 1
            snapshot = ApplicationVersion(
                application_id=application.id,
                version_number=version_number,
                extracted_fields_json=snapshot_field_warnings(
                    current_version.extracted_fields_json,
                    revised_fields,
                ),
                corrected_fields_json=revised_fields,
                created_at=submitted_at,
                created_by=user_id,
            )
            session.add(snapshot)
            updated_application_id = session.scalar(
                update(Application)
                .where(
                    Application.id == application.id,
                    Application.applicant_id == user_id,
                    Application.status == "corrections_requested",
                    Application.current_version == current_version.version_number,
                )
                .values(
                    status="submitted",
                    submitted_at=submitted_at,
                    updated_at=submitted_at,
                    current_version=version_number,
                )
                .returning(Application.id)
            )
            if updated_application_id is None:
                raise ValueError(
                    "This application was already resubmitted or has changed. Refresh and try again."
                )

            append_audit_event(
                session,
                event_type="application_resubmitted",
                actor_id=user_id,
                application_id=application.id,
                land_record_id=None,
                event_data={
                    "application_number": application.application_number,
                    "version_number": version_number,
                },
                created_at=submitted_at,
            )
            session.add(
                Notification(
                    user_id=user_id,
                    application_id=application.id,
                    title="Application resubmitted",
                    message=(
                        f"Application {application.application_number} was resubmitted "
                        "and is ready for review."
                    ),
                    created_at=submitted_at,
                )
            )
            officers = session.scalars(
                select(User).where(User.role == "officer", User.is_active.is_(True))
            ).all()
            assigned_officer = next(
                (
                    officer
                    for officer in officers
                    if officer.id == application.assigned_officer_id
                ),
                None,
            )
            recipients = (
                (assigned_officer,)
                if assigned_officer is not None
                else tuple(officers)
            )
            for officer in recipients:
                session.add(
                    Notification(
                        user_id=officer.id,
                        application_id=application.id,
                        title="Application ready for review",
                        message=(
                            f"Application {application.application_number} was resubmitted "
                            "after corrections and is ready for review."
                        ),
                        created_at=submitted_at,
                    )
                )
            session.flush()
            return ApplicationSubmissionData(
                id=application.id,
                application_number=application.application_number,
                status="submitted",
                submitted_at=submitted_at,
                current_version=version_number,
                corrected_fields_json=dict(snapshot.corrected_fields_json),
            )

    def list_landowner_documents(self, user_id: int) -> tuple[DocumentData, ...]:
        with self._session_factory() as session:
            documents = session.scalars(
                select(Document)
                .join(Application, Document.application_id == Application.id)
                .where(Application.applicant_id == user_id)
                .order_by(Document.uploaded_at.desc(), Document.id.desc())
            )
            return tuple(_document_data(document) for document in documents)

    def get_landowner_document(self, user_id: int, document_id: int) -> DocumentData | None:
        with self._session_factory() as session:
            document = session.scalar(
                select(Document)
                .join(Application, Document.application_id == Application.id)
                .where(
                    Document.id == document_id,
                    Application.applicant_id == user_id,
                )
            )
            if document is None:
                return None
            return _document_data(document)

    def list_landowner_notifications(self, user_id: int) -> tuple[NotificationData, ...]:
        with self._session_factory() as session:
            notifications = session.scalars(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
            )
            return tuple(
                NotificationData(
                    id=notification.id,
                    application_id=notification.application_id,
                    title=notification.title,
                    message=notification.message,
                    is_read=notification.is_read,
                    created_at=notification.created_at,
                )
                for notification in notifications
            )

    def mark_landowner_notification_read(
        self, *, user_id: int, notification_id: int
    ) -> bool:
        with transaction(self._session_factory) as session:
            updated_id = session.scalar(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
                .values(is_read=True)
                .returning(Notification.id)
            )
            if updated_id is not None:
                return True
            return session.scalar(
                select(Notification.id).where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                )
            ) is not None

    def count_landowner_applications_by_status(self, user_id: int) -> dict[str, int]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Application.status, func.count(Application.id))
                .where(Application.applicant_id == user_id)
                .group_by(Application.status)
            )
            return {status: count for status, count in rows}

    def list_landowner_approved_records(
        self, user_id: int
    ) -> tuple[LandRecordSummaryData, ...]:
        with self._session_factory() as session:
            records = session.scalars(
                select(LandRecord)
                .join(Application, LandRecord.application_id == Application.id)
                .where(
                    Application.applicant_id == user_id,
                    Application.status == "approved",
                )
                .order_by(LandRecord.approved_at.desc(), LandRecord.id.desc())
            )
            return tuple(
                LandRecordSummaryData(
                    public_record_id=record.public_record_id,
                    approved_at=record.approved_at,
                    current_version=record.current_version,
                )
                for record in records
            )

    def list_officer_review_queue(
        self, officer_id: int, policy: str
    ) -> tuple[ApplicationData, ...]:
        if policy not in ("assigned_only", "assigned_or_unassigned"):
            raise ValueError(f"Unsupported review policy: {policy}")
        with self._session_factory() as session:
            query = select(Application).where(
                Application.status.in_(("submitted", "under_review", "corrections_requested"))
            )
            if policy == "assigned_only":
                query = query.where(Application.assigned_officer_id == officer_id)
            else:
                query = query.where(
                    (Application.assigned_officer_id == officer_id)
                    | (Application.assigned_officer_id.is_(None))
                )
            applications = session.scalars(
                query.order_by(Application.submitted_at, Application.id)
            )
            return tuple(
                ApplicationData(
                    id=application.id,
                    application_number=application.application_number,
                    status=application.status,
                    submitted_at=application.submitted_at,
                    updated_at=application.updated_at,
                )
                for application in applications
            )

    def officer_can_review(
        self, officer_id: int, application_id: int, policy: str
    ) -> bool:
        if policy not in ("assigned_only", "assigned_or_unassigned"):
            raise ValueError(f"Unsupported review policy: {policy}")
        with self._session_factory() as session:
            application = session.get(Application, application_id)
            if application is None or application.status not in (
                "submitted",
                "under_review",
                "corrections_requested",
            ):
                return False
            return application.assigned_officer_id == officer_id or (
                policy == "assigned_or_unassigned"
                and application.assigned_officer_id is None
            )

    def officer_can_view_application(
        self, officer_id: int, application_id: int, policy: str
    ) -> bool:
        if self.officer_can_review(officer_id, application_id, policy):
            return True
        with self._session_factory() as session:
            application = session.get(Application, application_id)
            return bool(
                application is not None
                and application.assigned_officer_id == officer_id
                and application.status in ("approved", "rejected")
            )

    def count_officer_dashboard_metrics(self, officer_id: int, policy: str) -> dict[str, int]:
        """Return database-derived counts for officer dashboard."""
        if policy not in ("assigned_only", "assigned_or_unassigned"):
            raise ValueError(f"Unsupported review policy: {policy}")
        with self._session_factory() as session:
            # Base query for applications in reviewable states
            base_query = select(Application).where(
                Application.status.in_(("submitted", "under_review", "corrections_requested"))
            )
            if policy == "assigned_only":
                base_query = base_query.where(Application.assigned_officer_id == officer_id)
            else:
                base_query = base_query.where(
                    (Application.assigned_officer_id == officer_id)
                    | (Application.assigned_officer_id.is_(None))
                )

            # Total submitted (all applications that have been submitted)
            total_submitted = session.scalar(
                select(func.count(Application.id)).where(
                    Application.status.in_(("submitted", "under_review", "corrections_requested", "approved", "rejected"))
                )
            ) or 0

            # Pending review (submitted, assigned to officer or unassigned)
            pending_review = session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "submitted",
                    (Application.assigned_officer_id == officer_id) | (Application.assigned_officer_id.is_(None))
                )
            ) or 0

            # Under review (assigned to officer, status under_review)
            under_review = session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "under_review",
                    Application.assigned_officer_id == officer_id
                )
            ) or 0

            # Correction requests (assigned to officer, status corrections_requested)
            correction_requests = session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "corrections_requested",
                    Application.assigned_officer_id == officer_id
                )
            ) or 0

            # Approved (officer approved them)
            approved = session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "approved"
                )
            ) or 0

            # Rejected (officer rejected them)
            rejected = session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "rejected"
                )
            ) or 0

            return {
                "total_submitted": total_submitted,
                "pending_review": pending_review,
                "under_review": under_review,
                "correction_requests": correction_requests,
                "approved": approved,
                "rejected": rejected,
            }

    def list_officer_review_queue_filtered(
        self,
        officer_id: int,
        policy: str,
        status: str | None = None,
        application_number: str | None = None,
        district: str | None = None,
        village: str | None = None,
    ) -> tuple[OfficerQueueApplicationData, ...]:
        """Return applications with optional filters for the review queue."""
        if policy not in ("assigned_only", "assigned_or_unassigned"):
            raise ValueError(f"Unsupported review policy: {policy}")
        with self._session_factory() as session:
            query = (
                select(Application, User, ApplicationVersion)
                .join(User, User.id == Application.applicant_id)
                .outerjoin(
                    ApplicationVersion,
                    (ApplicationVersion.application_id == Application.id)
                    & (ApplicationVersion.version_number == Application.current_version),
                )
                .where(
                    Application.status.in_(
                        ("submitted", "under_review", "corrections_requested")
                    )
                )
            )
            if policy == "assigned_only":
                query = query.where(Application.assigned_officer_id == officer_id)
            else:
                query = query.where(
                    (Application.assigned_officer_id == officer_id)
                    | (Application.assigned_officer_id.is_(None))
                )
            
            if status:
                query = query.where(Application.status == status)
            if application_number:
                query = query.where(Application.application_number.ilike(f"%{application_number}%"))
            if district:
                query = query.where(
                    ApplicationVersion.corrected_fields_json["district"]
                    .as_string()
                    .ilike(f"%{district}%")
                )
            if village:
                query = query.where(
                    ApplicationVersion.corrected_fields_json["village"]
                    .as_string()
                    .ilike(f"%{village}%")
                )
            
            applications = session.execute(
                query.order_by(Application.submitted_at, Application.id)
            ).all()
            return tuple(
                OfficerQueueApplicationData(
                    id=application.id,
                    application_number=application.application_number,
                    status=application.status,
                    submitted_at=application.submitted_at,
                    updated_at=application.updated_at,
                    applicant_name=applicant.full_name,
                    applicant_email=applicant.email,
                    district=_optional_json_string(
                        version.corrected_fields_json, "district"
                    )
                    if version is not None
                    else None,
                    village=_optional_json_string(
                        version.corrected_fields_json, "village"
                    )
                    if version is not None
                    else None,
                )
                for application, applicant, version in applications
            )

    def get_application_for_review(self, officer_id: int, application_id: int, policy: str):
        """Get detailed application data for review, if officer is authorized."""
        if not self.officer_can_view_application(officer_id, application_id, policy):
            return None
        with self._session_factory() as session:
            application = session.get(Application, application_id)
            if application is None:
                return None
            
            # Get applicant info
            applicant = session.get(User, application.applicant_id)
            
            # Get latest version
            version = session.scalar(
                select(ApplicationVersion)
                .where(
                    ApplicationVersion.application_id == application_id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            
            # Get documents
            documents = session.scalars(
                select(Document)
                .where(Document.application_id == application_id)
                .order_by(Document.uploaded_at.desc())
            ).all()
            
            # Get review events
            review_events = session.scalars(
                select(ReviewEvent)
                .options(joinedload(ReviewEvent.officer))
                .where(ReviewEvent.application_id == application_id)
                .order_by(ReviewEvent.created_at.desc())
            ).all()
            
            return {
                "application": application,
                "applicant": applicant,
                "version": version,
                "documents": documents,
                "review_events": review_events,
            }

    def get_officer_application_document(
        self,
        *,
        officer_id: int,
        application_id: int,
        document_id: int,
        policy: str,
    ) -> OfficerDocumentData | None:
        if not self.officer_can_view_application(officer_id, application_id, policy):
            return None
        with self._session_factory() as session:
            document = session.scalar(
                select(Document).where(
                    Document.id == document_id,
                    Document.application_id == application_id,
                )
            )
            if document is None:
                return None
            return OfficerDocumentData(
                id=document.id,
                application_id=document.application_id,
                original_filename=document.original_filename,
                stored_path=document.stored_path,
                mime_type=document.mime_type,
                uploaded_at=document.uploaded_at,
            )

    def start_officer_application_review(
        self,
        *,
        officer_id: int,
        application_id: int,
        policy: str,
    ) -> OfficerReviewStartedData:
        if policy not in ("assigned_only", "assigned_or_unassigned"):
            raise ValueError(f"Unsupported review policy: {policy}")
        with transaction(self._session_factory) as session:
            application = session.get(Application, application_id)
            if application is None or application.status not in (
                "submitted",
                "under_review",
                "corrections_requested",
            ):
                raise ValueError("The application is no longer available for review.")
            already_assigned = application.assigned_officer_id == officer_id
            if already_assigned and application.status != "submitted":
                return OfficerReviewStartedData(
                    id=application.id,
                    application_number=application.application_number,
                    status=application.status,
                    assigned_officer_id=officer_id,
                )
            if application.assigned_officer_id is not None:
                if not already_assigned:
                    raise PermissionError("This application is assigned to another officer.")
            elif policy == "assigned_only":
                raise PermissionError("You are not authorized to claim this application.")

            previous_status = application.status
            review_status = "under_review" if previous_status == "submitted" else previous_status
            now = utc_now()
            assignment_filter = (
                Application.assigned_officer_id == officer_id
                if already_assigned
                else Application.assigned_officer_id.is_(None)
            )
            update_values: dict[str, object] = {
                "status": review_status,
                "updated_at": now,
            }
            if not already_assigned:
                update_values["assigned_officer_id"] = officer_id
            claimed_id = session.scalar(
                update(Application)
                .where(
                    Application.id == application_id,
                    assignment_filter,
                    Application.status == previous_status,
                )
                .values(**update_values)
                .returning(Application.id)
            )
            if claimed_id is None:
                raise PermissionError(
                    "The application changed before review could begin. Refresh and try again."
                )

            append_audit_event(
                session,
                event_type="application_review_started",
                actor_id=officer_id,
                application_id=application.id,
                land_record_id=None,
                event_data={
                    "application_number": application.application_number,
                    "previous_status": previous_status,
                    "status": review_status,
                },
                created_at=now,
            )
            session.flush()
            return OfficerReviewStartedData(
                id=application.id,
                application_number=application.application_number,
                status=review_status,
                assigned_officer_id=officer_id,
            )

    def decide_officer_application(
        self,
        *,
        officer_id: int,
        application_id: int,
        decision: str,
        comments: str | None,
    ) -> ApplicationDecisionData:
        if decision not in {"approve", "reject", "request_corrections"}:
            raise ValueError("Unsupported application decision.")
        clean_comments = comments.strip() if comments else ""
        if decision in {"reject", "request_corrections"} and len(clean_comments) < 10:
            requirement = "rejection reason" if decision == "reject" else "correction instructions"
            raise ValueError(f"Provide a meaningful {requirement} of at least 10 characters.")

        with transaction(self._session_factory) as session:
            application = session.get(Application, application_id)
            if (
                application is None
                or application.status != "under_review"
                or application.assigned_officer_id != officer_id
            ):
                raise PermissionError(
                    "The application is no longer assigned to you for an active review."
                )
            if application.land_record is not None:
                raise ValueError("An approved land record already exists; it cannot be overwritten.")

            version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application_id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            if version is None:
                raise ValueError("The current application version is unavailable.")
            documents = session.scalar(
                select(func.count(Document.id)).where(
                    Document.application_id == application_id
                )
            ) or 0

            if decision == "approve":
                validation_errors = validate_corrected_fields(version.corrected_fields_json)
                if validation_errors:
                    raise ValueError(
                        "Approval requires valid owner-reviewed fields: "
                        + " ".join(validation_errors)
                    )
                if documents < 1:
                    raise ValueError("Approval requires at least one supporting document.")

            decided_at = utc_now()
            status_by_decision = {
                "approve": "approved",
                "reject": "rejected",
                "request_corrections": "corrections_requested",
            }
            new_status = status_by_decision[decision]
            result_id = session.scalar(
                update(Application)
                .where(
                    Application.id == application_id,
                    Application.status == "under_review",
                    Application.assigned_officer_id == officer_id,
                )
                .values(
                    status=new_status,
                    rejection_reason=clean_comments if decision == "reject" else None,
                    updated_at=decided_at,
                )
                .returning(Application.id)
            )
            if result_id is None:
                raise PermissionError(
                    "The application status changed before your decision was saved. "
                    "Refresh and review the current application."
                )

            public_record_id: str | None = None
            land_record: LandRecord | None = None
            approved_fields: dict[str, object] | None = None
            approved_version_hash: str | None = None
            if decision == "approve":
                public_record_id = f"LR-REC-{decided_at:%Y}-{uuid4().hex[:8].upper()}"
                approved_fields = dict(version.corrected_fields_json)
                approved_version_hash = calculate_approved_record_version_hash(
                    public_record_id=public_record_id,
                    application_id=application_id,
                    version_number=1,
                    approved_fields=approved_fields,
                    authorized_by=officer_id,
                    authorized_at=decided_at,
                )
                land_record = LandRecord(
                    application_id=application_id,
                    public_record_id=public_record_id,
                    current_version=1,
                    approved_fields_json=approved_fields,
                    current_record_hash=approved_version_hash,
                    approved_at=decided_at,
                    approved_by=officer_id,
                    is_public=True,
                )
                session.add(land_record)
                session.flush()

            review_event = ReviewEvent(
                application_id=application_id,
                officer_id=officer_id,
                decision=decision,
                comments=clean_comments or None,
                created_at=decided_at,
            )
            session.add(review_event)

            audit_data: dict[str, object] = {
                "application_number": application.application_number,
                "decision": decision,
                "previous_status": "under_review",
                "status": new_status,
                "public_record_id": public_record_id,
                "version_number": version.version_number,
            }
            if land_record is not None:
                audit_data["approved_record_hash"] = approved_version_hash
                audit_data["approved_version_number"] = 1
            append_audit_event(
                session,
                event_type=(
                    "application_request_corrections"
                    if decision == "request_corrections"
                    else f"application_{decision}"
                ),
                actor_id=officer_id,
                application_id=application_id,
                land_record_id=land_record.id if land_record else None,
                event_data=audit_data,
                created_at=decided_at,
            )

            if land_record is not None:
                version_event = append_audit_event(
                    session,
                    event_type="approved_record_version_created",
                    actor_id=officer_id,
                    application_id=application_id,
                    land_record_id=land_record.id,
                    event_data={
                        "public_record_id": land_record.public_record_id,
                        "version_number": 1,
                        "approved_record_hash": approved_version_hash,
                    },
                    created_at=decided_at,
                )
                session.add(
                    ApprovedRecordVersion(
                        land_record_id=land_record.id,
                        version_number=1,
                        approved_fields_json=approved_fields or {},
                        version_hash=approved_version_hash or "",
                        authorized_by=officer_id,
                        authorized_at=decided_at,
                        audit_event_id=version_event.id,
                    )
                )

            if decision == "approve":
                title = "Application approved"
                message = (
                    f"Application {application.application_number} was approved. "
                    f"Your record reference is {public_record_id}."
                )
            elif decision == "reject":
                title = "Application rejected"
                message = (
                    f"Application {application.application_number} was rejected. "
                    f"Reason: {clean_comments}"
                )
            else:
                title = "Corrections requested"
                message = (
                    f"Application {application.application_number} requires corrections. "
                    f"Instructions: {clean_comments}"
                )
            session.add(
                Notification(
                    user_id=application.applicant_id,
                    application_id=application_id,
                    title=title,
                    message=message,
                    created_at=decided_at,
                )
            )
            session.flush()
            return ApplicationDecisionData(
                application_id=application_id,
                application_number=application.application_number,
                decision=decision,
                status=new_status,
                decided_at=decided_at,
                public_record_id=public_record_id,
            )

    def list_approved_registry(
        self,
        search: str | None = None,
        district: str | None = None,
        village: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[ApprovedRegistryRecordData, ...]:
        """Return published snapshots approved through the officer workflow."""
        limit = min(max(limit, 1), 50)
        offset = max(offset, 0)
        with self._session_factory() as session:
            approved_by_officer = exists(
                select(AuditEvent.id)
                .join(User, AuditEvent.actor_id == User.id)
                .where(
                    AuditEvent.land_record_id == LandRecord.id,
                    AuditEvent.application_id == Application.id,
                    AuditEvent.event_type == "application_approve",
                    User.role == "officer",
                )
            )
            query = (
                select(LandRecord)
                .join(Application, LandRecord.application_id == Application.id)
                .where(
                    Application.status == "approved",
                    LandRecord.is_public.is_(True),
                    approved_by_officer,
                )
            )

            if search and search.strip():
                search_pattern = _case_insensitive_pattern(search)
                query = query.where(
                    LandRecord.public_record_id.ilike(search_pattern, escape="\\")
                    | LandRecord.approved_fields_json["survey_or_plot_number"]
                    .as_string()
                    .ilike(search_pattern, escape="\\")
                )

            for field_name, value in (("district", district), ("village", village)):
                if value and value.strip():
                    query = query.where(
                        LandRecord.approved_fields_json[field_name]
                        .as_string()
                        .ilike(_case_insensitive_pattern(value), escape="\\")
                    )

            records = session.scalars(
                query.order_by(LandRecord.approved_at.desc(), LandRecord.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(
                _approved_registry_record_data(record)
                for record in records
            )

    def get_approved_registry_record(
        self, public_record_id: str
    ) -> tuple[ApprovedRegistryRecordData, tuple[ApprovedRegistryHistoryData, ...]] | None:
        with self._session_factory() as session:
            approved_by_officer = exists(
                select(AuditEvent.id)
                .join(User, AuditEvent.actor_id == User.id)
                .where(
                    AuditEvent.land_record_id == LandRecord.id,
                    AuditEvent.application_id == Application.id,
                    AuditEvent.event_type == "application_approve",
                    User.role == "officer",
                )
            )
            record = session.scalar(
                select(LandRecord)
                .join(Application, LandRecord.application_id == Application.id)
                .where(
                    LandRecord.public_record_id == public_record_id,
                    LandRecord.is_public.is_(True),
                    Application.status == "approved",
                    approved_by_officer,
                )
            )
            if record is None:
                return None

            events = session.scalars(
                select(AuditEvent)
                .where(
                    (AuditEvent.land_record_id == record.id)
                    | (AuditEvent.application_id == record.application_id)
                )
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
            chain_result = _verify_audit_chain_in_session(session)
            history = tuple(
                ApprovedRegistryHistoryData(
                    event_type=audit_event.event_type,
                    created_at=audit_event.created_at,
                    integrity_verified=chain_result.valid,
                )
                for audit_event in events
            )
            return _approved_registry_record_data(record), history

    def list_approved_record_versions(
        self, public_record_id: str
    ) -> tuple[ApprovedRecordVersionData, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(ApprovedRecordVersion, User, AuditEvent)
                .join(LandRecord, ApprovedRecordVersion.land_record_id == LandRecord.id)
                .join(Application, LandRecord.application_id == Application.id)
                .join(User, ApprovedRecordVersion.authorized_by == User.id)
                .join(AuditEvent, ApprovedRecordVersion.audit_event_id == AuditEvent.id)
                .where(
                    LandRecord.public_record_id == public_record_id,
                    LandRecord.is_public.is_(True),
                    Application.status == "approved",
                )
                .order_by(ApprovedRecordVersion.version_number)
            ).all()
            return tuple(
                ApprovedRecordVersionData(
                    version_number=version.version_number,
                    approved_fields_json=dict(version.approved_fields_json),
                    version_hash=version.version_hash,
                    authorized_by=version.authorized_by,
                    authorizer_name=authorizer.full_name,
                    authorized_at=version.authorized_at,
                    audit_event_id=audit_event.id,
                    audit_event_type=audit_event.event_type,
                )
                for version, authorizer, audit_event in rows
            )

    def get_public_approved_record(
        self, public_record_id: str
    ) -> tuple[ApprovedRegistryRecordData, tuple[ApprovedRecordVersionData, ...]] | None:
        with self._session_factory() as session:
            authorized_approval = exists(
                select(AuditEvent.id)
                .join(User, AuditEvent.actor_id == User.id)
                .where(
                    AuditEvent.land_record_id == LandRecord.id,
                    AuditEvent.application_id == Application.id,
                    AuditEvent.event_type == "application_approve",
                    User.role == "officer",
                )
            )
            record = session.scalar(
                select(LandRecord)
                .join(Application, LandRecord.application_id == Application.id)
                .where(
                    LandRecord.public_record_id == public_record_id,
                    LandRecord.is_public.is_(True),
                    Application.status == "approved",
                    authorized_approval,
                )
            )
            if record is None:
                return None
            rows = session.execute(
                select(ApprovedRecordVersion, User, AuditEvent)
                .join(User, ApprovedRecordVersion.authorized_by == User.id)
                .join(AuditEvent, ApprovedRecordVersion.audit_event_id == AuditEvent.id)
                .where(ApprovedRecordVersion.land_record_id == record.id)
                .order_by(ApprovedRecordVersion.version_number)
            ).all()
            versions = tuple(
                ApprovedRecordVersionData(
                    version_number=version.version_number,
                    approved_fields_json=dict(version.approved_fields_json),
                    version_hash=version.version_hash,
                    authorized_by=version.authorized_by,
                    authorizer_name=authorizer.full_name,
                    authorized_at=version.authorized_at,
                    audit_event_id=audit_event.id,
                    audit_event_type=audit_event.event_type,
                )
                for version, authorizer, audit_event in rows
            )
            return _approved_registry_record_data(record), versions

    def verify_approved_record_versions(self, public_record_id: str) -> tuple[str, ...]:
        with self._session_factory() as session:
            record = session.scalar(
                select(LandRecord)
                .join(Application, LandRecord.application_id == Application.id)
                .where(
                    LandRecord.public_record_id == public_record_id,
                    LandRecord.is_public.is_(True),
                    Application.status == "approved",
                )
            )
            if record is None:
                return ("Approved record is unavailable.",)
            versions = session.scalars(
                select(ApprovedRecordVersion)
                .where(ApprovedRecordVersion.land_record_id == record.id)
                .order_by(ApprovedRecordVersion.version_number)
            ).all()
            errors: list[str] = []
            expected_number = 1
            for version in versions:
                if version.version_number != expected_number:
                    errors.append(
                        f"Approved record version gap before version {version.version_number}."
                    )
                expected_number = version.version_number + 1
                expected_hash = calculate_approved_record_version_hash(
                    public_record_id=record.public_record_id,
                    application_id=record.application_id,
                    version_number=version.version_number,
                    approved_fields=version.approved_fields_json,
                    authorized_by=version.authorized_by,
                    authorized_at=version.authorized_at,
                )
                if version.version_hash != expected_hash:
                    errors.append(
                        f"Approved record version {version.version_number} hash mismatch."
                    )
                event = session.get(AuditEvent, version.audit_event_id)
                if event is None:
                    errors.append(
                        f"Approved record version {version.version_number} audit event is missing."
                    )
                elif (
                    event.event_type != "approved_record_version_created"
                    or event.land_record_id != record.id
                    or event.application_id != record.application_id
                    or event.actor_id != version.authorized_by
                    or event.event_data_json.get("version_number") != version.version_number
                    or event.event_data_json.get("approved_record_hash") != version.version_hash
                ):
                    errors.append(
                        f"Approved record version {version.version_number} does not match "
                        "its linked audit event."
                    )
            if not versions:
                errors.append("No approved record version snapshots exist.")
            elif (
                versions[-1].version_number != record.current_version
                or versions[-1].version_hash != record.current_record_hash
                or versions[-1].approved_fields_json != record.approved_fields_json
                or versions[-1].authorized_by != record.approved_by
                or versions[-1].authorized_at != record.approved_at
            ):
                errors.append("Current approved record does not match its latest version.")
            return tuple(errors)

    def create_approved_record_version(
        self,
        *,
        officer_id: int,
        public_record_id: str,
        field_changes: Mapping[str, object],
        correction_reason: str,
    ) -> ApprovedRecordVersionChangeData:
        reason = correction_reason.strip()
        if len(reason) < 10:
            raise ValueError("Provide a meaningful correction reason of at least 10 characters.")
        if not field_changes:
            raise ValueError("Provide at least one field to correct.")
        unknown_fields = set(field_changes) - FIELD_LABELS.keys()
        if unknown_fields:
            raise ValueError("The correction contains unsupported approved-record fields.")

        with transaction(self._session_factory) as session:
            record = session.scalar(
                select(LandRecord)
                .join(Application, LandRecord.application_id == Application.id)
                .where(
                    LandRecord.public_record_id == public_record_id,
                    LandRecord.is_public.is_(True),
                    Application.status == "approved",
                )
                .with_for_update()
            )
            if record is None:
                raise PermissionError("This approved record is not available for correction.")

            latest_version = session.scalar(
                select(ApprovedRecordVersion)
                .where(ApprovedRecordVersion.land_record_id == record.id)
                .order_by(ApprovedRecordVersion.version_number.desc())
                .limit(1)
            )
            if (
                latest_version is None
                or latest_version.version_number != record.current_version
                or latest_version.version_hash != record.current_record_hash
                or latest_version.approved_fields_json != record.approved_fields_json
            ):
                raise ValueError(
                    "The current approved record does not match its latest immutable version."
                )

            new_fields = dict(latest_version.approved_fields_json)
            new_fields.update(field_changes)
            validation_errors = validate_corrected_fields(new_fields)
            if validation_errors:
                raise ValueError(
                    "The corrected approved record is invalid: "
                    + " ".join(validation_errors)
                )
            changed_fields = sorted(
                field_name
                for field_name in set(latest_version.approved_fields_json) | set(new_fields)
                if latest_version.approved_fields_json.get(field_name) != new_fields.get(field_name)
            )
            if not changed_fields:
                raise ValueError("The correction does not change any approved fields.")

            authorized_at = utc_now()
            version_number = latest_version.version_number + 1
            version_hash = calculate_approved_record_version_hash(
                public_record_id=record.public_record_id,
                application_id=record.application_id,
                version_number=version_number,
                approved_fields=new_fields,
                authorized_by=officer_id,
                authorized_at=authorized_at,
            )
            correction_event = append_audit_event(
                session,
                event_type="approved_record_correction_authorized",
                actor_id=officer_id,
                application_id=record.application_id,
                land_record_id=record.id,
                event_data={
                    "public_record_id": record.public_record_id,
                    "previous_version": latest_version.version_number,
                    "version_number": version_number,
                    "previous_approved_record_hash": latest_version.version_hash,
                    "approved_record_hash": version_hash,
                    "changed_fields": changed_fields,
                    "correction_reason": reason,
                },
                created_at=authorized_at,
            )
            version_event = append_audit_event(
                session,
                event_type="approved_record_version_created",
                actor_id=officer_id,
                application_id=record.application_id,
                land_record_id=record.id,
                event_data={
                    "public_record_id": record.public_record_id,
                    "version_number": version_number,
                    "approved_record_hash": version_hash,
                    "authorization_event_id": correction_event.id,
                },
                created_at=authorized_at,
            )
            session.add(
                ApprovedRecordVersion(
                    land_record_id=record.id,
                    version_number=version_number,
                    approved_fields_json=new_fields,
                    version_hash=version_hash,
                    authorized_by=officer_id,
                    authorized_at=authorized_at,
                    audit_event_id=version_event.id,
                )
            )
            session.flush()
            session.connection().execute(
                update(LandRecord)
                .where(
                    LandRecord.id == record.id,
                    LandRecord.current_version == latest_version.version_number,
                )
                .values(
                    current_version=version_number,
                    approved_fields_json=new_fields,
                    current_record_hash=version_hash,
                    approved_at=authorized_at,
                    approved_by=officer_id,
                )
            )
            return ApprovedRecordVersionChangeData(
                public_record_id=record.public_record_id,
                version_number=version_number,
                version_hash=version_hash,
                authorized_at=authorized_at,
                audit_event_id=version_event.id,
            )

    def get_officer_registry_internal_details(
        self, public_record_id: str
    ) -> OfficerRegistryInternalDetailsData | None:
        applicant_user = aliased(User)
        approver_user = aliased(User)
        audit_actor = aliased(User)
        with self._session_factory() as session:
            result = session.execute(
                select(LandRecord, Application, applicant_user, approver_user)
                .join(Application, LandRecord.application_id == Application.id)
                .join(applicant_user, Application.applicant_id == applicant_user.id)
                .join(approver_user, LandRecord.approved_by == approver_user.id)
                .where(
                    LandRecord.public_record_id == public_record_id,
                    LandRecord.is_public.is_(True),
                    Application.status == "approved",
                    exists(
                        select(AuditEvent.id)
                        .join(audit_actor, AuditEvent.actor_id == audit_actor.id)
                        .where(
                            AuditEvent.land_record_id == LandRecord.id,
                            AuditEvent.application_id == Application.id,
                            AuditEvent.event_type == "application_approve",
                            audit_actor.role == "officer",
                        )
                    ),
                )
            ).first()
            if result is None:
                return None
            record, application, applicant, approver = result
            notes = session.scalars(
                select(ReviewEvent.comments)
                .where(
                    ReviewEvent.application_id == application.id,
                    ReviewEvent.comments.is_not(None),
                )
                .order_by(ReviewEvent.created_at, ReviewEvent.id)
            )
            return OfficerRegistryInternalDetailsData(
                application_number=application.application_number,
                applicant_name=applicant.full_name,
                approved_fields_json=dict(record.approved_fields_json),
                approver_name=approver.full_name,
                review_notes=tuple(note for note in notes if note),
            )

    def verify_audit_chain(self) -> AuditVerificationResult:
        with self._session_factory() as session:
            return _verify_audit_chain_in_session(session)

    def list_audit_events(
        self,
        application_id: int | None = None,
        land_record_id: int | None = None,
        limit: int = 100,
    ) -> tuple[dict, ...]:
        """Return audit events for the audit explorer."""
        with self._session_factory() as session:
            query = (
                select(AuditEvent, User)
                .join(User, AuditEvent.actor_id == User.id)
            )
            
            if application_id:
                query = query.where(AuditEvent.application_id == application_id)
            if land_record_id:
                query = query.where(AuditEvent.land_record_id == land_record_id)
            
            results = session.execute(
                query.order_by(AuditEvent.created_at.desc()).limit(limit)
            ).all()
            
            return tuple(
                {
                    "event": event,
                    "actor": actor,
                }
                for event, actor in results
            )

    def create_missing_demo_users(self, users: Sequence[DemoUserData]) -> tuple[str, ...]:
        created_roles: list[str] = []
        with transaction(self._session_factory) as session:
            for user_data in users:
                existing = session.scalar(
                    select(User).where(User.email == user_data.email)
                )
                if existing is not None:
                    if existing.role != user_data.role:
                        raise DemoAccountRoleConflict(
                            "A configured demo email belongs to a different role."
                        )
                    continue
                session.add(
                    User(
                        full_name=user_data.full_name,
                        email=user_data.email,
                        password_hash=user_data.password_hash,
                        role=user_data.role,
                    )
                )
                created_roles.append(user_data.role)
        return tuple(created_roles)


def get_database_url() -> str:
    """Return the configured database URL, defaulting to a local SQLite file."""
    return os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL)


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create an engine and enable SQLite foreign-key enforcement."""
    url = make_url(database_url or get_database_url())
    if url.drivername.startswith("sqlite") and url.database not in (None, "", ":memory:"):
        if not url.database.startswith("file:"):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url)
    if url.drivername.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(
            dbapi_connection: sqlite3.Connection, _connection_record: object
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """Create sessions bound to the configured database."""
    return sessionmaker(
        bind=create_database_engine(database_url),
        autoflush=False,
        expire_on_commit=False,
    )


def create_database(database_url: str | None = None) -> Database:
    """Create the service-facing database access object."""
    return Database(create_session_factory(database_url))


@contextmanager
def transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit a unit of database work, or roll it back if it fails."""
    with session_factory() as session:
        with session.begin():
            yield session


def _verify_audit_chain_in_session(session: Session) -> AuditVerificationResult:
    result = verify_audit_event_chain(session.scalars(select(AuditEvent)).all())
    state = session.get(AuditChainState, 1)
    if state is None:
        if result.event_count == 0:
            return result
        message = "Audit chain head state is missing."
    elif state.head_hash != (result.head_hash or GENESIS_HASH):
        message = "Stored audit chain head does not match the verified event chain."
    else:
        return result
    return replace(
        result,
        valid=False,
        errors=(*result.errors, message),
    )


def initialize_database(database_url: str | None = None) -> None:
    """Create all database tables."""
    engine = create_database_engine(database_url)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            dialect_name = connection.dialect.name
            if dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert

                statement = dialect_insert(AuditChainState).values(
                    id=1, head_hash=GENESIS_HASH
                )
                connection.execute(
                    statement.on_conflict_do_nothing(index_elements=["id"])
                )
            elif dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert

                statement = dialect_insert(AuditChainState).values(
                    id=1, head_hash=GENESIS_HASH
                )
                connection.execute(
                    statement.on_conflict_do_nothing(index_elements=["id"])
                )
            elif connection.execute(
                select(AuditChainState.id).where(AuditChainState.id == 1)
            ).scalar_one_or_none() is None:
                connection.execute(
                    insert(AuditChainState).values(id=1, head_hash=GENESIS_HASH)
                )
        _backfill_initial_approved_record_versions(engine)
    finally:
        engine.dispose()


def _backfill_initial_approved_record_versions(engine: Engine) -> None:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with transaction(factory) as session:
        records = session.scalars(
            select(LandRecord)
            .join(Application, LandRecord.application_id == Application.id)
            .where(
                Application.status == "approved",
                ~exists(
                    select(ApprovedRecordVersion.id).where(
                        ApprovedRecordVersion.land_record_id == LandRecord.id
                    )
                ),
            )
            .order_by(LandRecord.id)
        ).all()
        for record in records:
            approval_event = session.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.application_id == record.application_id,
                    AuditEvent.land_record_id == record.id,
                    AuditEvent.event_type == "application_approve",
                )
                .order_by(AuditEvent.id)
                .limit(1)
            )
            if approval_event is None:
                raise RuntimeError(
                    f"Approved record {record.public_record_id} has no approval audit "
                    "event; its initial version cannot be migrated safely."
                )
            version_hash = calculate_approved_record_version_hash(
                public_record_id=record.public_record_id,
                application_id=record.application_id,
                version_number=1,
                approved_fields=record.approved_fields_json,
                authorized_by=record.approved_by,
                authorized_at=record.approved_at,
            )
            session.add(
                ApprovedRecordVersion(
                    land_record_id=record.id,
                    version_number=1,
                    approved_fields_json=dict(record.approved_fields_json),
                    version_hash=version_hash,
                    authorized_by=record.approved_by,
                    authorized_at=record.approved_at,
                    audit_event_id=approval_event.id,
                )
            )
            session.connection().execute(
                update(LandRecord)
                .where(LandRecord.id == record.id)
                .values(
                    current_version=1,
                    current_record_hash=version_hash,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the land records database.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="create database tables")
    args = parser.parse_args()

    if args.command == "init":
        initialize_database()
        print(f"Database initialized at {get_database_url()}")


if __name__ == "__main__":
    main()
