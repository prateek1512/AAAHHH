"""Authentication, authorization, and development account seeding services."""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Mapping
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from modules.audit import AuditVerificationResult, GENESIS_HASH
from modules.consistency_score import calculate_consistency_score
from modules.database import (
    ApplicationData,
    ApplicationDecisionData,
    ApplicationSubmissionData,
    ApprovedRecordVersionChangeData,
    ApprovedRecordVersionData,
    ApprovedRegistryHistoryData,
    ApprovedRegistryRecordData,
    AuthUserData,
    Database,
    DemoAccountRoleConflict,
    DemoUserData,
    DocumentData,
    LandRecordSummaryData,
    LandownerApplicationDetailData,
    NotificationData,
    OfficerRegistryInternalDetailsData,
    create_database,
    initialize_database,
)
from modules.document_storage import DocumentStorage
from modules.discrepancy import detect_discrepancies
from modules.field_validation import FIELD_LABELS, validate_corrected_fields
from modules.ocr import process_document
from modules.public_qr import PublicRecordQRCode, generate_public_record_qr

Role = Literal["landowner", "officer"]
ReviewPolicy = Literal["assigned_only", "assigned_or_unassigned"]
SESSION_TOKEN_KEY = "land_records_auth_token"
SESSION_LIFETIME = timedelta(hours=12)
REVIEW_POLICY_ENV = "LAND_RECORDS_REVIEW_POLICY"
DEMO_ACCOUNT_ENV = {
    "landowner": {
        "email": "LAND_RECORDS_DEMO_LANDOWNER_EMAIL",
        "password": "LAND_RECORDS_DEMO_LANDOWNER_PASSWORD",
        "name": "LAND_RECORDS_DEMO_LANDOWNER_NAME",
        "default_name": "Demo Landowner",
    },
    "officer": {
        "email": "LAND_RECORDS_DEMO_OFFICER_EMAIL",
        "password": "LAND_RECORDS_DEMO_OFFICER_PASSWORD",
        "name": "LAND_RECORDS_DEMO_OFFICER_NAME",
        "default_name": "Demo Officer",
    },
}


class AuthenticationError(Exception):
    """Raised when credentials or an authenticated session are invalid."""


class AuthorizationError(Exception):
    """Raised when a valid user lacks permission to perform an operation."""


class AccountConfigurationError(Exception):
    """Raised when development account configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Identity resolved from a live database-backed session."""

    user_id: int
    full_name: str
    email: str
    role: Role


@dataclass(frozen=True, slots=True)
class ApplicationSummary:
    id: int
    application_number: str
    status: str
    submitted_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class LandownerApplicationDetail:
    id: int
    application_number: str
    status: str
    submitted_at: datetime | None
    updated_at: datetime
    current_version: int
    corrected_fields_json: dict[str, object]
    document_filenames: tuple[str, ...]
    document_ids: tuple[int, ...]
    review_events: tuple[LandownerReviewEvent, ...]
    public_record_id: str | None
    discrepancy_warnings: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class LandownerReviewEvent:
    id: int
    decision: str
    comments: str | None
    created_at: datetime
    officer_name: str


@dataclass(frozen=True, slots=True)
class ApplicationSubmission:
    id: int
    application_number: str
    status: str
    submitted_at: datetime
    current_version: int
    corrected_fields_json: dict[str, object]


@dataclass(frozen=True, slots=True)
class NotificationSummary:
    id: int
    application_id: int | None
    title: str
    message: str
    is_read: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LandRecordSummary:
    public_record_id: str
    approved_at: datetime
    current_version: int


@dataclass(frozen=True, slots=True)
class LandownerDocumentSummary:
    id: int
    application_id: int
    application_number: str
    original_filename: str
    mime_type: str
    uploaded_at: datetime
    ocr_result: dict[str, object] | None
    corrected_fields_json: dict[str, object]
    application_status: str


@dataclass(frozen=True, slots=True)
class LandownerDocumentDownload:
    original_filename: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class LandownerDashboardMetrics:
    total_applications: int
    submitted_applications: int
    under_review_applications: int
    corrections_required_applications: int
    approved_applications: int
    rejected_applications: int


@dataclass(frozen=True, slots=True)
class OfficerDashboardMetrics:
    total_submitted_applications: int
    pending_review_applications: int
    under_review_applications: int
    correction_requests: int
    approved_applications: int
    rejected_applications: int


@dataclass(frozen=True, slots=True)
class OfficerApplicationSummary:
    id: int
    application_number: str
    status: str
    submitted_at: datetime | None
    applicant_name: str
    applicant_email: str
    district: str | None
    village: str | None


@dataclass(frozen=True, slots=True)
class ReviewQueueFilter:
    status: str | None = None
    application_number: str | None = None
    district: str | None = None
    village: str | None = None


@dataclass(frozen=True, slots=True)
class ApplicationReviewDetail:
    id: int
    application_number: str
    status: str
    current_version: int
    submitted_at: datetime | None
    updated_at: datetime
    assigned_officer_id: int | None
    applicant_name: str
    applicant_email: str
    extracted_fields_json: dict[str, object]
    corrected_fields_json: dict[str, object]
    ocr_status: str
    ocr_warnings: tuple[str, ...]
    validation_warnings: tuple[str, ...]
    discrepancy_warnings: tuple[dict[str, object], ...]
    consistency_score: dict[str, object]
    documents: tuple[OfficerReviewDocument, ...]
    review_events: tuple[OfficerReviewEvent, ...]
    parcel_geojson: object | None = None
    parcel_geometry_validation_status: str | None = None


@dataclass(frozen=True, slots=True)
class OfficerReviewDocument:
    id: int
    original_filename: str
    mime_type: str
    uploaded_at: datetime


@dataclass(frozen=True, slots=True)
class OfficerReviewEvent:
    id: int
    decision: str
    comments: str | None
    created_at: datetime
    officer_name: str


@dataclass(frozen=True, slots=True)
class OfficerApplicationDocumentDownload:
    original_filename: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class OfficerReviewStarted:
    id: int
    application_number: str
    status: str


@dataclass(frozen=True, slots=True)
class ApplicationDecision:
    application_id: int
    application_number: str
    decision: str
    status: str
    decided_at: datetime
    public_record_id: str | None


@dataclass(frozen=True, slots=True)
class ApprovedRegistryRecord:
    public_record_id: str
    approved_at: datetime
    current_version: int
    district: str | None
    village: str | None
    survey_or_plot_number: str | None
    land_area: str | None
    parcel_geojson: object | None = None
    parcel_geometry_validation_status: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovedRegistryHistoryEvent:
    event_type: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovedRegistryDetail:
    record: ApprovedRegistryRecord
    history: tuple[ApprovedRegistryHistoryEvent, ...]
    audit_verified: bool


@dataclass(frozen=True, slots=True)
class ApprovedRecordVersion:
    version_number: int
    approved_fields: dict[str, object]
    version_hash: str
    authorized_by: int
    authorizer_name: str
    authorized_at: datetime
    audit_event_id: int
    audit_event_type: str


@dataclass(frozen=True, slots=True)
class ApprovedRecordVersionChange:
    public_record_id: str
    version_number: int
    version_hash: str
    authorized_at: datetime
    audit_event_id: int


@dataclass(frozen=True, slots=True)
class PublicRecordVersion:
    version_number: int
    approved_fields: dict[str, object]
    version_hash: str
    authorized_at: datetime


@dataclass(frozen=True, slots=True)
class PublicApprovedRecord:
    public_record_id: str
    current_version: int
    current_version_hash: str
    approved_fields: dict[str, object]
    versions: tuple[PublicRecordVersion, ...]
    parcel_geojson: object | None = None
    parcel_geometry_validation_status: str | None = None


@dataclass(frozen=True, slots=True)
class PublicRecordQRData:
    public_record_id: str
    verification_url: str
    png_bytes: bytes


@dataclass(frozen=True, slots=True)
class PublicAuditIntegrityResult:
    passed: bool
    audit_chain_passed: bool
    record_versions_passed: bool
    event_count: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OfficerRegistryInternalDetails:
    application_number: str
    applicant_name: str
    approved_fields_json: dict[str, object]
    approver_name: str
    review_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditEventSummary:
    id: int
    event_type: str
    actor_name: str
    actor_email: str
    application_id: int | None
    land_record_id: int | None
    event_hash: str
    previous_event_hash: str | None
    integrity_verified: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditChainVerification:
    valid: bool
    event_count: int
    genesis_hash: str
    head_hash: str | None
    errors: tuple[str, ...]


def hash_password(password: str) -> str:
    """Hash a password with Argon2id; plaintext is never persisted."""
    if not password:
        raise ValueError("Password must not be empty.")
    return PasswordHasher().hash(password)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _principal(user: AuthUserData) -> Principal:
    if user.role == "landowner":
        role: Role = "landowner"
    elif user.role == "officer":
        role = "officer"
    else:
        raise AuthenticationError("The account is not authorized to sign in.")
    return Principal(
        user_id=user.user_id,
        full_name=user.full_name,
        email=user.email,
        role=role,
    )


def _get_review_policy() -> ReviewPolicy:
    policy = os.environ.get(REVIEW_POLICY_ENV, "assigned_or_unassigned")
    if policy == "assigned_only":
        return policy
    if policy == "assigned_or_unassigned":
        return policy
    raise ValueError(f"Unsupported review policy: {policy}")


def _application_summary(application: ApplicationData) -> ApplicationSummary:
    return ApplicationSummary(
        id=application.id,
        application_number=application.application_number,
        status=application.status,
        submitted_at=application.submitted_at,
        updated_at=application.updated_at,
    )


def _landowner_application_detail(
    application: LandownerApplicationDetailData,
    discrepancy_warnings: tuple[dict[str, object], ...],
) -> LandownerApplicationDetail:
    submitted_at = application.submitted_at
    if submitted_at is not None:
        submitted_at = (
            submitted_at.replace(tzinfo=UTC)
            if submitted_at.tzinfo is None
            else submitted_at.astimezone(UTC)
        )
    updated_at = (
        application.updated_at.replace(tzinfo=UTC)
        if application.updated_at.tzinfo is None
        else application.updated_at.astimezone(UTC)
    )
    return LandownerApplicationDetail(
        id=application.id,
        application_number=application.application_number,
        status=application.status,
        submitted_at=submitted_at,
        updated_at=updated_at,
        current_version=application.current_version,
        corrected_fields_json=application.corrected_fields_json,
        document_filenames=application.document_filenames,
        document_ids=application.document_ids,
        review_events=tuple(
            LandownerReviewEvent(
                id=event.id,
                decision=event.decision,
                comments=event.comments,
                created_at=(
                    event.created_at.replace(tzinfo=UTC)
                    if event.created_at.tzinfo is None
                    else event.created_at.astimezone(UTC)
                ),
                officer_name=event.officer_name,
            )
            for event in application.review_events
        ),
        public_record_id=application.public_record_id,
        discrepancy_warnings=discrepancy_warnings,
    )


def _application_submission(
    submission: ApplicationSubmissionData,
) -> ApplicationSubmission:
    return ApplicationSubmission(
        id=submission.id,
        application_number=submission.application_number,
        status=submission.status,
        submitted_at=submission.submitted_at,
        current_version=submission.current_version,
        corrected_fields_json=submission.corrected_fields_json,
    )


def _notification_summary(notification: NotificationData) -> NotificationSummary:
    return NotificationSummary(
        id=notification.id,
        application_id=notification.application_id,
        title=notification.title,
        message=notification.message,
        is_read=notification.is_read,
        created_at=(
            notification.created_at.replace(tzinfo=UTC)
            if notification.created_at.tzinfo is None
            else notification.created_at.astimezone(UTC)
        ),
    )


def _land_record_summary(record: LandRecordSummaryData) -> LandRecordSummary:
    return LandRecordSummary(
        public_record_id=record.public_record_id,
        approved_at=record.approved_at,
        current_version=record.current_version,
    )


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _approved_registry_record(
    record: ApprovedRegistryRecordData,
) -> ApprovedRegistryRecord:
    return ApprovedRegistryRecord(
        public_record_id=record.public_record_id,
        approved_at=_utc_datetime(record.approved_at),
        current_version=record.current_version,
        district=record.district,
        village=record.village,
        survey_or_plot_number=record.survey_or_plot_number,
        land_area=record.land_area,
        parcel_geojson=record.parcel_geojson,
        parcel_geometry_validation_status=record.parcel_geometry_validation_status,
    )


def _landowner_document_summary(document: DocumentData) -> LandownerDocumentSummary:
    return LandownerDocumentSummary(
        id=document.id,
        application_id=document.application_id,
        application_number=document.application_number,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        uploaded_at=document.uploaded_at,
        ocr_result=document.ocr_result_json,
        corrected_fields_json=document.corrected_fields_json or {},
        application_status=document.application_status,
    )


class AuthService:
    """Authenticate users and enforce role and record-level access rules."""

    def __init__(
        self,
        database: Database,
        document_storage: DocumentStorage | None = None,
    ) -> None:
        self._database = database
        self._document_storage = document_storage or DocumentStorage()
        self._password_hasher = PasswordHasher()
        self._dummy_password_hash = self._password_hasher.hash(secrets.token_urlsafe(32))

    def login(
        self,
        email: str,
        password: str,
        *,
        expected_role: Role | None = None,
    ) -> str:
        """Verify credentials and return a random opaque token for the session."""
        normalized_email = email.strip().casefold()
        user = self._database.find_auth_user(normalized_email)
        stored_hash = user.password_hash if user is not None else self._dummy_password_hash
        try:
            password_matches = self._password_hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            password_matches = False

        if (
            user is None
            or not password_matches
            or not user.is_active
            or (expected_role is not None and user.role != expected_role)
        ):
            raise AuthenticationError("Invalid email or password.")

        replacement_password_hash = None
        if self._password_hasher.check_needs_rehash(user.password_hash):
            replacement_password_hash = self._password_hasher.hash(password)
        raw_token = secrets.token_urlsafe(32)
        if not self._database.create_auth_session(
            user_id=user.user_id,
            expected_password_hash=user.password_hash,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + SESSION_LIFETIME,
            expected_role=expected_role,
            replacement_password_hash=replacement_password_hash,
        ):
            raise AuthenticationError("Invalid email or password.")
        return raw_token

    def authenticate_session(self, token: str | None) -> Principal:
        """Resolve a token to the current active identity using database state."""
        if not token:
            raise AuthenticationError("Sign in to continue.")

        user = self._database.find_session_user(_hash_token(token), datetime.now(UTC))
        if user is None:
            raise AuthenticationError("Sign in to continue.")
        return _principal(user)

    def require_role(self, token: str | None, *roles: Role) -> Principal:
        """Resolve the session and enforce the required role in the backend."""
        principal = self.authenticate_session(token)
        if principal.role not in roles:
            raise AuthorizationError("You are not authorized to perform this action.")
        return principal

    def logout(self, token: str | None) -> None:
        """Revoke a session token; repeated logout calls are safe."""
        if not token:
            return
        self._database.revoke_auth_session(_hash_token(token))

    def list_landowner_applications(self, token: str | None) -> tuple[ApplicationSummary, ...]:
        """Return summaries only for the authenticated landowner's applications."""
        principal = self.require_role(token, "landowner")
        return tuple(
            _application_summary(application)
            for application in self._database.list_landowner_applications(principal.user_id)
        )

    def get_landowner_application(
        self, token: str | None, application_id: int
    ) -> LandownerApplicationDetail:
        principal = self.require_role(token, "landowner")
        application = self._database.get_landowner_application(
            principal.user_id, application_id
        )
        if application is None:
            raise AuthorizationError("You are not authorized to access this application.")
        context = self._database.get_application_discrepancy_context(application_id)
        discrepancy_warnings = detect_discrepancies(
            corrected_fields=application.corrected_fields_json,
            extracted_fields=context["extracted_fields"],
            previous_fields=context["previous_fields"],
            other_applications=context["other_applications"],
            approved_records=context["approved_records"],
        )
        return _landowner_application_detail(
            application,
            tuple(
                warning
                for warning in discrepancy_warnings
                if warning.get("audience") != "officer"
            ),
        )

    @property
    def max_upload_size_mb(self) -> int:
        """Return the rounded-up size limit for Streamlit's uploader control."""
        return self._document_storage.max_upload_size_mb

    def upload_landowner_document(
        self,
        token: str | None,
        original_filename: str,
        mime_type: str,
        content: bytes,
        *,
        application_number: str | None = None,
    ) -> LandownerDocumentSummary:
        """Store an original document and create its owner-scoped draft application."""
        principal = self.require_role(token, "landowner")
        stored_document = self._document_storage.store(original_filename, mime_type, content)
        try:
            document = self._database.create_landowner_application_document(
                user_id=principal.user_id,
                application_number=(
                    application_number
                    or f"LR-{datetime.now(UTC):%Y}-{uuid4().hex[:8].upper()}"
                ),
                original_filename=stored_document.original_filename,
                stored_path=stored_document.stored_path,
                document_sha256=stored_document.document_sha256,
                mime_type=stored_document.mime_type,
            )
        except Exception as error:
            try:
                self._document_storage.delete(stored_document.stored_path)
            except Exception as cleanup_error:
                error.add_note(
                    "Could not remove uploaded file after database failure: "
                    f"{cleanup_error}"
                )
            raise
        ocr_result = process_document(content, stored_document.original_filename, stored_document.mime_type)
        persisted_document = self._database.save_landowner_ocr_result(
            user_id=principal.user_id,
            document_id=document.id,
            ocr_result_json=ocr_result.to_storage(),
        )
        return _landowner_document_summary(persisted_document)

    def save_landowner_document_corrections(
        self,
        token: str | None,
        document_id: int,
        corrected_fields: Mapping[str, object],
    ) -> LandownerDocumentSummary:
        """Save landowner-reviewed values separately from original OCR output."""
        principal = self.require_role(token, "landowner")
        errors = validate_corrected_fields(corrected_fields)
        if errors:
            raise ValueError("\n".join(errors))
        normalized_fields: dict[str, object] = {}
        for field_name in FIELD_LABELS:
            raw_value = corrected_fields.get(field_name)
            value = raw_value.strip() if isinstance(raw_value, str) else ""
            normalized_fields[field_name] = value or None
        try:
            document = self._database.save_landowner_document_corrections(
                user_id=principal.user_id,
                document_id=document_id,
                corrected_fields_json=normalized_fields,
            )
        except PermissionError as error:
            raise AuthorizationError(
                "You are not authorized to edit this document, or its official record "
                "has already been approved."
            ) from error
        return _landowner_document_summary(document)

    def upload_landowner_supporting_document(
        self,
        token: str | None,
        application_id: int,
        original_filename: str,
        mime_type: str,
        content: bytes,
    ) -> LandownerDocumentSummary:
        """Append a private supporting document to an application awaiting corrections."""
        principal = self.require_role(token, "landowner")
        stored_document = self._document_storage.store(original_filename, mime_type, content)
        try:
            document = self._database.add_landowner_supporting_document(
                user_id=principal.user_id,
                application_id=application_id,
                original_filename=stored_document.original_filename,
                stored_path=stored_document.stored_path,
                document_sha256=stored_document.document_sha256,
                mime_type=stored_document.mime_type,
            )
        except Exception as error:
            try:
                self._document_storage.delete(stored_document.stored_path)
            except Exception as cleanup_error:
                error.add_note(
                    "Could not remove supporting file after database failure: "
                    f"{cleanup_error}"
                )
            raise
        return _landowner_document_summary(document)

    def submit_landowner_application(
        self, token: str | None, application_id: int
    ) -> ApplicationSubmission:
        principal = self.require_role(token, "landowner")
        detail = self._database.get_landowner_application(
            principal.user_id, application_id
        )
        if detail is None:
            raise AuthorizationError("You are not authorized to submit this application.")
        if detail.status != "draft":
            raise ValueError("This application has already been submitted or is no longer a draft.")
        errors = validate_corrected_fields(detail.corrected_fields_json)
        if errors:
            raise ValueError("\n".join(errors))
        try:
            submission = self._database.submit_landowner_application(
                user_id=principal.user_id,
                application_id=application_id,
            )
        except PermissionError as error:
            raise AuthorizationError(
                "You are not authorized to submit this application."
            ) from error
        return _application_submission(submission)

    def resubmit_landowner_application(
        self,
        token: str | None,
        application_id: int,
        corrected_fields: Mapping[str, object] | None = None,
    ) -> ApplicationSubmission:
        principal = self.require_role(token, "landowner")
        detail = self._database.get_landowner_application(
            principal.user_id, application_id
        )
        if detail is None:
            raise AuthorizationError("You are not authorized to resubmit this application.")
        if detail.status != "corrections_requested":
            raise ValueError(
                "This application is not awaiting corrections and cannot be resubmitted."
            )
        corrected_values = (
            detail.corrected_fields_json
            if corrected_fields is None
            else {
                field_name: (
                    value.strip() or None
                    if isinstance(value, str)
                    else value
                )
                for field_name, value in corrected_fields.items()
            }
        )
        errors = validate_corrected_fields(corrected_values)
        if errors:
            raise ValueError("\n".join(errors))
        try:
            submission = self._database.resubmit_landowner_application(
                user_id=principal.user_id,
                application_id=application_id,
                corrected_fields_json=corrected_values,
            )
        except PermissionError as error:
            raise AuthorizationError(
                "You are not authorized to resubmit this application."
            ) from error
        return _application_submission(submission)

    def list_landowner_documents(
        self, token: str | None
    ) -> tuple[LandownerDocumentSummary, ...]:
        """Return uploaded document metadata for the authenticated landowner only."""
        principal = self.require_role(token, "landowner")
        return tuple(
            _landowner_document_summary(document)
            for document in self._database.list_landowner_documents(principal.user_id)
        )

    def get_landowner_document(
        self, token: str | None, document_id: int
    ) -> LandownerDocumentDownload:
        """Retrieve original bytes only after checking the authenticated owner's scope."""
        principal = self.require_role(token, "landowner")
        document = self._database.get_landowner_document(principal.user_id, document_id)
        if document is None:
            raise AuthorizationError("You are not authorized to access this document.")
        return LandownerDocumentDownload(
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            content=self._document_storage.read(document.stored_path),
        )

    def get_landowner_dashboard_metrics(
        self, token: str | None
    ) -> LandownerDashboardMetrics:
        """Return database-derived counts for only the authenticated landowner."""
        principal = self.require_role(token, "landowner")
        counts = self._database.count_landowner_applications_by_status(principal.user_id)
        return LandownerDashboardMetrics(
            total_applications=sum(counts.values()),
            submitted_applications=counts.get("submitted", 0),
            under_review_applications=counts.get("under_review", 0),
            corrections_required_applications=counts.get("corrections_requested", 0),
            approved_applications=counts.get("approved", 0),
            rejected_applications=counts.get("rejected", 0),
        )

    def list_landowner_notifications(
        self, token: str | None
    ) -> tuple[NotificationSummary, ...]:
        """Return notifications only for the authenticated landowner."""
        principal = self.require_role(token, "landowner")
        return tuple(
            _notification_summary(notification)
            for notification in self._database.list_landowner_notifications(principal.user_id)
        )

    def mark_landowner_notification_read(
        self, token: str | None, notification_id: int
    ) -> None:
        principal = self.require_role(token, "landowner")
        if not self._database.mark_landowner_notification_read(
            user_id=principal.user_id,
            notification_id=notification_id,
        ):
            raise AuthorizationError("You are not authorized to access this notification.")

    def list_landowner_approved_records(
        self, token: str | None
    ) -> tuple[LandRecordSummary, ...]:
        """Return minimal approved-record references owned by the authenticated user."""
        principal = self.require_role(token, "landowner")
        return tuple(
            _land_record_summary(record)
            for record in self._database.list_landowner_approved_records(principal.user_id)
        )

    def list_officer_review_queue(
        self,
        token: str | None,
    ) -> tuple[ApplicationSummary, ...]:
        """Return only applications assigned to the officer or in their allowed queue."""
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        return tuple(
            _application_summary(application)
            for application in self._database.list_officer_review_queue(
                principal.user_id, policy
            )
        )

    def authorize_review_decision(self, token: str | None, application_id: int) -> Principal:
        """Authorize an officer for a decision without performing a workflow transition."""
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        if not self._database.officer_can_review(principal.user_id, application_id, policy):
            raise AuthorizationError("You are not authorized to review this application.")
        return principal

    def get_officer_dashboard_metrics(self, token: str | None) -> OfficerDashboardMetrics:
        """Return database-derived counts for the officer dashboard."""
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        counts = self._database.count_officer_dashboard_metrics(principal.user_id, policy)
        return OfficerDashboardMetrics(
            total_submitted_applications=counts.get("total_submitted", 0),
            pending_review_applications=counts.get("pending_review", 0),
            under_review_applications=counts.get("under_review", 0),
            correction_requests=counts.get("correction_requests", 0),
            approved_applications=counts.get("approved", 0),
            rejected_applications=counts.get("rejected", 0),
        )

    def list_officer_review_queue_filtered(
        self,
        token: str | None,
        status: str | None = None,
        application_number: str | None = None,
        district: str | None = None,
        village: str | None = None,
    ) -> tuple[OfficerApplicationSummary, ...]:
        """Return filtered review queue for the officer."""
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        return tuple(
            OfficerApplicationSummary(
                id=application.id,
                application_number=application.application_number,
                status=application.status,
                submitted_at=application.submitted_at,
                applicant_name=application.applicant_name,
                applicant_email=application.applicant_email,
                district=application.district,
                village=application.village,
            )
            for application in self._database.list_officer_review_queue_filtered(
                principal.user_id, policy, status, application_number, district, village
            )
        )

    def get_application_for_review(
        self, token: str | None, application_id: int
    ) -> ApplicationReviewDetail | None:
        """Get detailed application data for review, if officer is authorized."""
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        data = self._database.get_application_for_review(principal.user_id, application_id, policy)
        if data is None:
            return None
        
        application = data["application"]
        applicant = data["applicant"]
        version = data["version"]
        documents = data["documents"]
        review_events = data["review_events"]
        extracted_fields = version.extracted_fields_json if version else {}
        corrected_fields = version.corrected_fields_json if version else {}
        discrepancy_context = self._database.get_application_discrepancy_context(
            application_id
        )
        discrepancy_warnings = detect_discrepancies(
            corrected_fields=corrected_fields,
            extracted_fields=discrepancy_context["extracted_fields"],
            previous_fields=discrepancy_context["previous_fields"],
            other_applications=discrepancy_context["other_applications"],
            approved_records=discrepancy_context["approved_records"],
        )
        confidence = extracted_fields.get("field_confidence", {})
        confidence_values = confidence if isinstance(confidence, Mapping) else {}
        field_warnings = extracted_fields.get("field_warnings", [])
        if not isinstance(field_warnings, list):
            field_warnings = []
        required_audit_events_present = any(
            event_type in {"application_submitted", "application_resubmitted"}
            for event_type in discrepancy_context["audit_event_types"]
        )
        audit_verification = self._database.verify_audit_chain()
        audit_integrity_explanation = (
            "; ".join(audit_verification.errors)
            if not audit_verification.valid
            else "No application_submitted or application_resubmitted audit event "
            "is associated with this application."
            if not required_audit_events_present
            else None
        )
        consistency_score = calculate_consistency_score(
            corrected_fields=corrected_fields,
            ocr_confidence=confidence_values,
            field_warnings=tuple(
                warning for warning in field_warnings if isinstance(warning, Mapping)
            ),
            discrepancy_warnings=discrepancy_warnings,
            reference_available=not any(
                warning.get("code") == "reference_comparison_unavailable"
                for warning in discrepancy_warnings
            ),
            audit_check_available=True,
            audit_integrity_passed=(
                audit_verification.valid and required_audit_events_present
            ),
            audit_integrity_explanation=audit_integrity_explanation,
        )
        ocr_status = extracted_fields.get("status", "unavailable")
        ocr_warnings = extracted_fields.get("warnings", ())
        if not isinstance(ocr_warnings, (list, tuple)):
            ocr_warnings = ()
        ocr_warnings = tuple(item for item in ocr_warnings if isinstance(item, str))
        validation_warnings = (
            validate_corrected_fields(corrected_fields) if corrected_fields else ()
        )
        geometry_source = extracted_fields.get("parcel_geojson")
        extracted_values = extracted_fields.get("fields")
        if geometry_source is None and isinstance(extracted_values, Mapping):
            geometry_source = extracted_values.get("parcel_geojson")
        if geometry_source is None:
            geometry_source = corrected_fields.get("parcel_geojson")
        geometry_status = extracted_fields.get("parcel_geometry_validation_status")
        if geometry_status is None and isinstance(extracted_values, Mapping):
            geometry_status = extracted_values.get("parcel_geometry_validation_status")
        if geometry_status is None:
            geometry_status = corrected_fields.get("parcel_geometry_validation_status")
        
        return ApplicationReviewDetail(
            id=application.id,
            application_number=application.application_number,
            status=application.status,
            current_version=application.current_version,
            submitted_at=application.submitted_at,
            updated_at=application.updated_at,
            assigned_officer_id=application.assigned_officer_id,
            applicant_name=applicant.full_name if applicant else "Unknown",
            applicant_email=applicant.email if applicant else "Unknown",
            extracted_fields_json=extracted_fields,
            corrected_fields_json=corrected_fields,
            ocr_status=ocr_status if isinstance(ocr_status, str) else "unavailable",
            ocr_warnings=ocr_warnings,
            validation_warnings=validation_warnings,
            discrepancy_warnings=discrepancy_warnings,
            consistency_score=consistency_score,
            parcel_geojson=geometry_source,
            parcel_geometry_validation_status=(
                geometry_status if isinstance(geometry_status, str) else None
            ),
            documents=tuple(
                OfficerReviewDocument(
                    id=doc.id,
                    original_filename=doc.original_filename,
                    mime_type=doc.mime_type,
                    uploaded_at=doc.uploaded_at,
                )
                for doc in documents
            ),
            review_events=tuple(
                OfficerReviewEvent(
                    id=event.id,
                    decision=event.decision,
                    comments=event.comments,
                    created_at=event.created_at,
                    officer_name=event.officer.full_name if event.officer else "Unknown",
                )
                for event in review_events
            ),
        )

    def get_officer_application_document(
        self,
        token: str | None,
        application_id: int,
        document_id: int,
    ) -> OfficerApplicationDocumentDownload:
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        document = self._database.get_officer_application_document(
            officer_id=principal.user_id,
            application_id=application_id,
            document_id=document_id,
            policy=policy,
        )
        if document is None:
            raise AuthorizationError("You are not authorized to access this document.")
        return OfficerApplicationDocumentDownload(
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            content=self._document_storage.read(document.stored_path),
        )

    def start_officer_application_review(
        self, token: str | None, application_id: int
    ) -> OfficerReviewStarted:
        principal = self.require_role(token, "officer")
        policy = _get_review_policy()
        if not self._database.officer_can_review(principal.user_id, application_id, policy):
            raise AuthorizationError("You are not authorized to review this application.")
        try:
            result = self._database.start_officer_application_review(
                officer_id=principal.user_id,
                application_id=application_id,
                policy=policy,
            )
        except PermissionError as error:
            raise AuthorizationError(str(error)) from error
        return OfficerReviewStarted(
            id=result.id,
            application_number=result.application_number,
            status=result.status,
        )

    def decide_officer_application(
        self,
        token: str | None,
        application_id: int,
        decision: str,
        comments: str | None = None,
    ) -> ApplicationDecision:
        principal = self.require_role(token, "officer")
        try:
            result: ApplicationDecisionData = self._database.decide_officer_application(
                officer_id=principal.user_id,
                application_id=application_id,
                decision=decision,
                comments=comments,
            )
        except PermissionError as error:
            raise AuthorizationError(str(error)) from error
        return ApplicationDecision(
            application_id=result.application_id,
            application_number=result.application_number,
            decision=result.decision,
            status=result.status,
            decided_at=result.decided_at,
            public_record_id=result.public_record_id,
        )

    def list_approved_registry(
        self,
        token: str | None,
        search: str | None = None,
        district: str | None = None,
        village: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[ApprovedRegistryRecord, ...]:
        """Search the shared published registry for authenticated portal users."""
        self.require_role(token, "officer", "landowner")
        results = self._database.list_approved_registry(
            search,
            district,
            village,
            limit=limit,
            offset=offset,
        )
        return tuple(
            ApprovedRegistryRecord(
                public_record_id=record.public_record_id,
                approved_at=record.approved_at,
                current_version=record.current_version,
                district=record.district,
                village=record.village,
                survey_or_plot_number=record.survey_or_plot_number,
                land_area=record.land_area,
            )
            for record in results
        )

    def get_approved_registry_record(
        self, token: str | None, public_record_id: str
    ) -> ApprovedRegistryDetail | None:
        self.require_role(token, "officer", "landowner")
        result = self._database.get_approved_registry_record(public_record_id)
        if result is None:
            return None
        record_data, history_data = result
        record = _approved_registry_record(record_data)
        history = tuple(
            ApprovedRegistryHistoryEvent(
                event_type=event.event_type,
                created_at=_utc_datetime(event.created_at),
            )
            for event in history_data
        )
        return ApprovedRegistryDetail(
            record=record,
            history=history,
            audit_verified=bool(history_data)
            and all(event.integrity_verified for event in history_data),
        )

    def list_approved_record_versions(
        self, token: str | None, public_record_id: str
    ) -> tuple[ApprovedRecordVersion, ...]:
        principal = self.require_role(token, "officer", "landowner")
        results: tuple[ApprovedRecordVersionData, ...] = (
            self._database.list_approved_record_versions(public_record_id)
        )
        public_fields = {
            "district",
            "village",
            "survey_or_plot_number",
            "land_area",
        }
        return tuple(
            ApprovedRecordVersion(
                version_number=version.version_number,
                approved_fields=(
                    dict(version.approved_fields_json)
                    if principal.role == "officer"
                    else {
                        name: value
                        for name, value in version.approved_fields_json.items()
                        if name in public_fields
                    }
                ),
                version_hash=version.version_hash,
                authorized_by=version.authorized_by,
                authorizer_name=version.authorizer_name,
                authorized_at=_utc_datetime(version.authorized_at),
                audit_event_id=version.audit_event_id,
                audit_event_type=version.audit_event_type,
            )
            for version in results
        )

    def get_public_approved_record(
        self, public_record_id: str
    ) -> PublicApprovedRecord | None:
        result = self._database.get_public_approved_record(public_record_id)
        if result is None:
            return None
        record, version_data = result
        public_fields = {
            "district",
            "village",
            "survey_or_plot_number",
            "land_area",
        }

        def published(fields: Mapping[str, object]) -> dict[str, object]:
            return {
                name: value
                for name, value in fields.items()
                if name in public_fields
            }

        versions = tuple(
            PublicRecordVersion(
                version_number=version.version_number,
                approved_fields=published(version.approved_fields_json),
                version_hash=version.version_hash,
                authorized_at=_utc_datetime(version.authorized_at),
            )
            for version in version_data
        )
        current = next(
            (version for version in versions if version.version_number == record.current_version),
            None,
        )
        if current is None:
            return None
        return PublicApprovedRecord(
            public_record_id=record.public_record_id,
            current_version=record.current_version,
            current_version_hash=record.current_version_hash,
            approved_fields=current.approved_fields,
            versions=versions,
            parcel_geojson=record.parcel_geojson,
            parcel_geometry_validation_status=record.parcel_geometry_validation_status,
        )

    def get_public_approved_record_qr(
        self, public_record_id: str
    ) -> PublicRecordQRData | None:
        """Return a public-only QR code only when this ID resolves to an approved record."""
        record = self.get_public_approved_record(public_record_id)
        if record is None:
            return None
        qr: PublicRecordQRCode = generate_public_record_qr(record.public_record_id)
        return PublicRecordQRData(
            public_record_id=record.public_record_id,
            verification_url=qr.verification_url,
            png_bytes=qr.png_bytes,
        )

    def verify_public_record_audit_integrity(
        self, public_record_id: str
    ) -> PublicAuditIntegrityResult | None:
        if self._database.get_public_approved_record(public_record_id) is None:
            return None
        chain = self._database.verify_audit_chain()
        version_errors = self._database.verify_approved_record_versions(public_record_id)
        errors = (*chain.errors, *version_errors)
        return PublicAuditIntegrityResult(
            passed=chain.valid and not version_errors,
            audit_chain_passed=chain.valid,
            record_versions_passed=not version_errors,
            event_count=chain.event_count,
            errors=errors,
        )

    def correct_approved_record(
        self,
        token: str | None,
        public_record_id: str,
        field_changes: Mapping[str, object],
        correction_reason: str,
    ) -> ApprovedRecordVersionChange:
        principal = self.require_role(token, "officer")
        try:
            result: ApprovedRecordVersionChangeData = (
                self._database.create_approved_record_version(
                    officer_id=principal.user_id,
                    public_record_id=public_record_id,
                    field_changes=field_changes,
                    correction_reason=correction_reason,
                )
            )
        except PermissionError as error:
            raise AuthorizationError(str(error)) from error
        return ApprovedRecordVersionChange(
            public_record_id=result.public_record_id,
            version_number=result.version_number,
            version_hash=result.version_hash,
            authorized_at=_utc_datetime(result.authorized_at),
            audit_event_id=result.audit_event_id,
        )

    def get_officer_registry_internal_details(
        self, token: str | None, public_record_id: str
    ) -> OfficerRegistryInternalDetails | None:
        self.require_role(token, "officer")
        details: OfficerRegistryInternalDetailsData | None = (
            self._database.get_officer_registry_internal_details(public_record_id)
        )
        if details is None:
            return None
        return OfficerRegistryInternalDetails(
            application_number=details.application_number,
            applicant_name=details.applicant_name,
            approved_fields_json=details.approved_fields_json,
            approver_name=details.approver_name,
            review_notes=details.review_notes,
        )

    def list_audit_events(
        self,
        token: str | None,
        application_id: int | None = None,
        land_record_id: int | None = None,
        limit: int = 100,
    ) -> tuple[AuditEventSummary, ...]:
        """Return audit events for the audit explorer."""
        self.require_role(token, "officer")
        results = self._database.list_audit_events(application_id, land_record_id, limit)
        chain = self._database.verify_audit_chain()
        return tuple(
            AuditEventSummary(
                id=r["event"].id,
                event_type=r["event"].event_type,
                actor_name=r["actor"].full_name,
                actor_email=r["actor"].email,
                application_id=r["event"].application_id,
                land_record_id=r["event"].land_record_id,
                event_hash=r["event"].event_hash,
                previous_event_hash=r["event"].previous_event_hash,
                integrity_verified=(
                    chain.valid and r["event"].id in chain.verified_event_ids
                ),
                created_at=_utc_datetime(r["event"].created_at),
            )
            for r in results
        )

    def verify_audit_chain(self, token: str | None) -> AuditChainVerification:
        """Verify the complete audit chain for an authorized officer."""
        self.require_role(token, "officer")
        result: AuditVerificationResult = self._database.verify_audit_chain()
        return AuditChainVerification(
            valid=result.valid,
            event_count=result.event_count,
            genesis_hash=GENESIS_HASH,
            head_hash=result.head_hash,
            errors=result.errors,
        )


def seed_demo_accounts(
    database: Database,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Create configured demo accounts if absent; never modify existing accounts."""
    env = os.environ if environment is None else environment
    configured: dict[str, tuple[str, str, str]] = {}
    for role, keys in DEMO_ACCOUNT_ENV.items():
        email = env.get(keys["email"], "").strip().casefold()
        password = env.get(keys["password"], "")
        name = env.get(keys["name"], keys["default_name"]).strip()
        if not email or "@" not in email:
            raise AccountConfigurationError(f"Set {keys['email']} to a valid demo email.")
        if len(password) < 12:
            raise AccountConfigurationError(
                f"Set {keys['password']} to a development password of at least 12 characters."
            )
        if not name:
            raise AccountConfigurationError(f"Set {keys['name']} to a non-empty display name.")
        configured[role] = (email, password, name)

    if configured["landowner"][0] == configured["officer"][0]:
        raise AccountConfigurationError("Demo landowner and officer emails must be different.")

    user_data = tuple(
        DemoUserData(
            full_name=name,
            email=email,
            password_hash=hash_password(password),
            role=role,
        )
        for role, (email, password, name) in configured.items()
    )
    try:
        return database.create_missing_demo_users(user_data)
    except DemoAccountRoleConflict as error:
        raise AccountConfigurationError(str(error)) from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local land-record authentication.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed-demo", help="create configured development demo accounts")
    args = parser.parse_args()

    if args.command == "seed-demo":
        initialize_database()
        created = seed_demo_accounts(create_database())
        if created:
            print(f"Created configured development account(s): {', '.join(created)}.")
        else:
            print("Configured development accounts already exist; no accounts were changed.")


if __name__ == "__main__":
    main()
