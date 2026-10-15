"""SQLAlchemy models and database setup for the land records application."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Sequence

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
    func,
    select,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

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
            "previous_event_hash IS NULL OR length(previous_event_hash) = 64",
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
    event_data_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
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


def _canonical_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_json_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _audit_event_hash(
    *,
    event_type: str,
    actor_id: int,
    application_id: int | None,
    land_record_id: int | None,
    event_data_json: dict[str, object],
    previous_event_hash: str | None,
    created_at: datetime,
) -> str:
    event_data = {
        "event_type": event_type,
        "actor_id": actor_id,
        "application_id": application_id,
        "land_record_id": land_record_id,
        "event_data_json": event_data_json,
        "previous_event_hash": previous_event_hash,
        "created_at": _canonical_utc(created_at).isoformat(),
    }
    payload = json.dumps(event_data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
            documents = session.scalars(
                select(Document.original_filename)
                .where(Document.application_id == application.id)
                .order_by(Document.uploaded_at, Document.id)
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
                document_filenames=tuple(documents),
            )

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
                    extracted_fields_json=ocr_result_json,
                    corrected_fields_json={},
                    created_by=user_id,
                )
                session.add(version)
            else:
                version.extracted_fields_json = ocr_result_json
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
            if application.status != "draft":
                raise PermissionError(
                    "Only draft applications can be edited; submitted and approved "
                    "applications are immutable."
                )
            current_version = session.scalar(
                select(ApplicationVersion).where(
                    ApplicationVersion.application_id == application.id,
                    ApplicationVersion.version_number == application.current_version,
                )
            )
            extracted_fields_json = (
                current_version.extracted_fields_json if current_version is not None else {}
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

            previous_event = session.scalar(
                select(AuditEvent)
                .order_by(AuditEvent.id.desc())
                .limit(1)
            )
            previous_event_hash = previous_event.event_hash if previous_event else None
            audit_data: dict[str, object] = {
                "application_number": application.application_number,
                "version_number": version_number,
            }
            event_hash = _audit_event_hash(
                event_type="application_submitted",
                actor_id=user_id,
                application_id=application.id,
                land_record_id=None,
                event_data_json=audit_data,
                previous_event_hash=previous_event_hash,
                created_at=submitted_at,
            )
            session.add(
                AuditEvent(
                    event_type="application_submitted",
                    actor_id=user_id,
                    application_id=application.id,
                    event_data_json=audit_data,
                    previous_event_hash=previous_event_hash,
                    event_hash=event_hash,
                    created_at=submitted_at,
                )
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
        if not self.officer_can_review(officer_id, application_id, policy):
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
        if not self.officer_can_review(officer_id, application_id, policy):
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
            if application.assigned_officer_id == officer_id:
                return OfficerReviewStartedData(
                    id=application.id,
                    application_number=application.application_number,
                    status=application.status,
                    assigned_officer_id=officer_id,
                )
            if application.assigned_officer_id is not None:
                raise PermissionError("This application is assigned to another officer.")
            if policy == "assigned_only":
                raise PermissionError("You are not authorized to claim this application.")

            previous_status = application.status
            review_status = "under_review" if previous_status == "submitted" else previous_status
            now = utc_now()
            claimed_id = session.scalar(
                update(Application)
                .where(
                    Application.id == application_id,
                    Application.assigned_officer_id.is_(None),
                    Application.status == previous_status,
                )
                .values(
                    assigned_officer_id=officer_id,
                    status=review_status,
                    updated_at=now,
                )
                .returning(Application.id)
            )
            if claimed_id is None:
                raise PermissionError(
                    "Another officer claimed this application before your assignment completed."
                )

            previous_event = session.scalar(
                select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
            )
            previous_event_hash = previous_event.event_hash if previous_event else None
            audit_data: dict[str, object] = {
                "application_number": application.application_number,
                "previous_status": previous_status,
                "status": review_status,
            }
            event_type = "application_review_started"
            event_hash = _audit_event_hash(
                event_type=event_type,
                actor_id=officer_id,
                application_id=application.id,
                land_record_id=None,
                event_data_json=audit_data,
                previous_event_hash=previous_event_hash,
                created_at=now,
            )
            session.add(
                AuditEvent(
                    event_type=event_type,
                    actor_id=officer_id,
                    application_id=application.id,
                    event_data_json=audit_data,
                    previous_event_hash=previous_event_hash,
                    event_hash=event_hash,
                    created_at=now,
                )
            )
            session.flush()
            return OfficerReviewStartedData(
                id=application.id,
                application_number=application.application_number,
                status=review_status,
                assigned_officer_id=officer_id,
            )

    def list_approved_registry(
        self,
        search: str | None = None,
        district: str | None = None,
        village: str | None = None,
    ) -> tuple[dict, ...]:
        """Return approved land records with optional filters."""
        with self._session_factory() as session:
            query = (
                select(LandRecord, Application, User)
                .join(Application, LandRecord.application_id == Application.id)
                .join(User, Application.applicant_id == User.id)
                .where(Application.status == "approved")
            )
            
            if search:
                query = query.where(
                    LandRecord.public_record_id.ilike(f"%{search}%") |
                    Application.application_number.ilike(f"%{search}%")
                )
            # District/village filters would require joining with ApplicationVersion
            # For now, we return the basic records
            
            results = session.execute(
                query.order_by(LandRecord.approved_at.desc(), LandRecord.id.desc())
            ).all()
            
            return tuple(
                {
                    "record": record,
                    "application": application,
                    "applicant": applicant,
                }
                for record, application, applicant in results
            )

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


def initialize_database(database_url: str | None = None) -> None:
    """Create all database tables."""
    engine = create_database_engine(database_url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


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
