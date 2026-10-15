from __future__ import annotations

import logging

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from modules.auth import (
    ApplicationSummary,
    AuthService,
    AuthorizationError,
    LandownerDashboardMetrics,
    NotificationSummary,
)
from modules.auth_ui import get_auth_service, render_login
from modules.document_storage import DocumentStorageError
from modules.field_validation import REQUIRED_FIELDS, validate_corrected_fields
from modules.ocr import render_document_preview

st.set_page_config(
    page_title="Landowner Portal",
    page_icon=":material/description:",
    layout="wide",
)

logger = logging.getLogger(__name__)

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    [data-testid="stMetric"] {
        border: 1px solid #e6eaf0;
        border-radius: 12px;
        padding: 1rem 1.1rem;
        background: #ffffff;
    }
    .portal-eyebrow {
        color: #52647a;
        font-size: 0.88rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        margin-bottom: 0.25rem;
    }
    .portal-note {
        border-left: 4px solid #d99b26;
        border-radius: 4px;
        background: #fff8e8;
        color: #493711;
        padding: 0.9rem 1rem;
        margin: 0.75rem 0 1.25rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

token = render_login("landowner", "Landowner Portal")
if token is None:
    st.stop()

service: AuthService = get_auth_service()

PAGES = (
    "Dashboard",
    "Upload Land Document",
    "My Applications",
    "Notifications",
    "Approved Land Records",
)

with st.sidebar:
    st.divider()
    st.subheader("Landowner services")
    current_page = st.radio(
        "Navigate",
        PAGES,
        key="landowner_page",
        label_visibility="collapsed",
    )


def _navigate_to_upload() -> None:
    st.session_state["landowner_page"] = "Upload Land Document"


def _navigate_to_applications() -> None:
    st.session_state["landowner_page"] = "My Applications"


def _status_label(status: str) -> str:
    return {
        "draft": "Draft",
        "submitted": "Submitted",
        "under_review": "Under review",
        "corrections_requested": "Corrections required",
        "approved": "Approved",
        "rejected": "Rejected",
    }.get(status, "Status unavailable")


def _application_row(application: ApplicationSummary) -> None:
    with st.container(border=True):
        number_column, status_column, date_column = st.columns([2.2, 1.5, 1.4])
        with number_column:
            st.markdown(f"**{application.application_number}**")
            st.caption("Application details will be available in a future update.")
        with status_column:
            st.markdown(f"**{_status_label(application.status)}**")
        with date_column:
            st.caption("Last updated")
            st.write(application.updated_at.strftime("%d %b %Y"))


def _render_applications(applications: tuple[ApplicationSummary, ...]) -> None:
    if not applications:
        st.info("You don't have any applications yet.")
        return
    for application in applications:
        _application_row(application)


def _render_notifications(notifications: tuple[NotificationSummary, ...]) -> None:
    if not notifications:
        st.info("You don't have any notifications yet.")
        return
    for notification in notifications:
        with st.container(border=True):
            st.markdown(f"**{notification.title}**")
            st.write(notification.message)
            st.caption(
                f"{notification.created_at.strftime('%d %b %Y, %H:%M UTC')} · "
                f"{'Read' if notification.is_read else 'Unread'}"
            )


def _render_metrics(metrics: LandownerDashboardMetrics) -> None:
    metric_specs = (
        ("Total applications", metrics.total_applications),
        ("Submitted", metrics.submitted_applications),
        ("Under review", metrics.under_review_applications),
        ("Corrections required", metrics.corrections_required_applications),
        ("Approved", metrics.approved_applications),
        ("Rejected", metrics.rejected_applications),
    )
    for start in range(0, len(metric_specs), 3):
        columns = st.columns(3)
        for column, (label, value) in zip(columns, metric_specs[start : start + 3]):
            with column:
                st.metric(label, value)


def _show_pending_notice() -> None:
    st.markdown(
        '<div class="portal-note"><strong>Verification is pending.</strong> '
        "A submitted record remains pending government verification until an "
        "authorized officer approves it. This prototype does not establish legal ownership."
        "</div>",
        unsafe_allow_html=True,
    )


def _dashboard() -> None:
    st.markdown('<div class="portal-eyebrow">Overview</div>', unsafe_allow_html=True)
    st.title("Your land record dashboard")
    st.write("Track applications and notifications from one place.")

    st.button(
        "＋  Submit a new land record",
        type="primary",
        key="new_record_dashboard",
        on_click=_navigate_to_upload,
    )

    _show_pending_notice()
    metrics = service.get_landowner_dashboard_metrics(token)
    _render_metrics(metrics)

    st.subheader("Recent applications")
    applications = service.list_landowner_applications(token)
    if not applications:
        st.info("No applications yet. Start by submitting a new land record.")
    else:
        for application in applications[:5]:
            _application_row(application)
        if len(applications) > 5:
            st.button("View all applications", on_click=_navigate_to_applications)


def _upload_document() -> None:
    st.markdown('<div class="portal-eyebrow">New application</div>', unsafe_allow_html=True)
    st.title("Upload Land Document")
    _show_pending_notice()
    st.write(
        "Upload a synthetic land document to create a private draft application. "
        "The original file is preserved and OCR results remain separate from your edits."
    )
    with st.form("landowner_document_upload"):
        uploaded_file = st.file_uploader(
            "Choose a land document",
            type=["pdf", "png", "jpg", "jpeg"],
            max_upload_size=service.max_upload_size_mb,
            help=(
                "Supported formats: PDF, PNG, JPG, JPEG. "
                f"Maximum size: {service.max_upload_size_mb} MB."
            ),
        )
        submitted = st.form_submit_button("Upload document", type="primary")

    if submitted:
        if uploaded_file is None:
            st.error("Choose a document before uploading.")
        else:
            try:
                document = service.upload_landowner_document(
                    token,
                    uploaded_file.name,
                    uploaded_file.type or "",
                    uploaded_file.getvalue(),
                )
            except DocumentStorageError as error:
                st.error(str(error))
            else:
                st.success(
                    f"Uploaded **{document.original_filename}** to draft "
                    f"application **{document.application_number}**."
                )
                ocr_status = (
                    document.ocr_result.get("status", "unavailable")
                    if document.ocr_result
                    else "unavailable"
                )
                st.info(f"OCR processing status: {ocr_status}")

    st.subheader("Your uploaded documents")
    documents = service.list_landowner_documents(token)
    if not documents:
        st.info("No documents uploaded yet.")
        return
    for document in documents:
        with st.container(border=True):
            st.text(document.original_filename)
            st.caption(
                f"Application {document.application_number} · "
                f"Uploaded {document.uploaded_at.strftime('%d %b %Y, %H:%M UTC')} · "
                f"Status: {_status_label(document.application_status)}"
            )
            submission_notice_key = f"submitted_notice_{document.application_id}"
            if submission_notice_key in st.session_state:
                st.success(st.session_state.pop(submission_notice_key))
            try:
                original_document = service.get_landowner_document(token, document.id)
                preview = render_document_preview(
                    original_document.content,
                    original_document.original_filename,
                    original_document.mime_type,
                )
            except DocumentStorageError as error:
                preview = None
                st.warning(f"Original document preview is unavailable: {error}")
            if preview is not None:
                st.image(preview, caption="Original document preview", use_container_width=True)
            else:
                st.caption("Preview is unavailable; the original document remains stored privately.")

            ocr_result = document.ocr_result
            if not ocr_result:
                st.warning("OCR output is not available. You can still enter fields manually.")
                extracted_fields: dict[str, object] = {}
                confidence: dict[str, object] = {}
            else:
                status = str(ocr_result.get("status", "unavailable"))
                st.write(f"OCR processing status: **{status.capitalize()}**")
                raw_extracted_fields = ocr_result.get("fields")
                extracted_fields: dict[str, object] = {}
                if isinstance(raw_extracted_fields, dict):
                    extracted_fields = {
                        key: value
                        for key, value in raw_extracted_fields.items()
                        if isinstance(key, str)
                    }
                with st.expander("OCR results and original recognized text"):
                    st.caption(
                        "OCR field confidence is only an estimate of text recognition. "
                        "It does not establish the correctness of a legal field."
                    )
                    raw_text = ocr_result.get("ocr_text")
                    st.text_area(
                        "Raw OCR text",
                        value=raw_text if isinstance(raw_text, str) else "",
                        height=180,
                        disabled=True,
                        key=f"ocr_text_{document.id}",
                    )
                    raw_confidence = ocr_result.get("field_confidence", {})
                    confidence = (
                        {key: value for key, value in raw_confidence.items() if isinstance(key, str)}
                        if isinstance(raw_confidence, dict)
                        else {}
                    )
                    if isinstance(extracted_fields, dict):
                        for field_name, value in extracted_fields.items():
                            field_confidence = (
                                confidence.get(field_name)
                                if isinstance(confidence, dict)
                                else None
                            )
                            if value:
                                confidence_text = (
                                    f" · OCR token confidence {field_confidence}%"
                                    if isinstance(field_confidence, (int, float))
                                    else ""
                                )
                                st.write(f"**{field_name.replace('_', ' ').title()}:** {value}{confidence_text}")
                    warnings = ocr_result.get("warnings", [])
                    if isinstance(warnings, list):
                        for warning in warnings:
                            st.warning(f"OCR warning: {warning}")

            corrected_fields = document.corrected_fields_json
            field_labels = (
                ("owner_name", "Owner name"),
                ("district", "District"),
                ("circle_or_tehsil", "Circle or tehsil"),
                ("village", "Village"),
                ("survey_or_plot_number", "Survey or plot number"),
                ("land_area", "Land area and unit"),
                ("document_type", "Document type"),
                ("registration_or_document_number", "Registration or document number"),
                ("document_date", "Document date"),
            )
            if document.application_status == "approved":
                st.info(
                    "This application has an approved official record. It is read-only here; "
                    "contact the department if an official correction is needed."
                )
                st.subheader("Approved version fields")
                for field_name, label in field_labels:
                    value = corrected_fields.get(field_name)
                    st.write(f"**{label}:** {value if isinstance(value, str) and value else 'Not provided'}")
                continue
            if document.application_status != "draft":
                st.info(
                    "This application has been submitted and is pending verification. "
                    "Its submitted field snapshot is read-only."
                )
                for field_name, label in field_labels:
                    value = corrected_fields.get(field_name)
                    st.write(f"**{label}:** {value if isinstance(value, str) and value else 'Not provided'}")
                continue

            st.subheader("Review extracted fields")
            st.caption(
                "Fields populated from OCR are suggestions. Entries saved here are "
                "owner-reviewed values in a separate application version."
            )
            preview_key = f"correction_preview_{document.id}"
            with st.form(f"manual_document_fields_{document.id}"):
                edited_fields: dict[str, str] = {}
                for field_name, label in field_labels:
                    existing_correction = corrected_fields.get(field_name)
                    ocr_value = extracted_fields.get(field_name)
                    initial_value = (
                        existing_correction
                        if isinstance(existing_correction, str)
                        else ocr_value if isinstance(ocr_value, str) else ""
                    )
                    field_confidence = confidence.get(field_name)
                    source_missing = not isinstance(ocr_value, str) or not ocr_value.strip()
                    low_confidence = (
                        isinstance(field_confidence, (int, float))
                        and field_confidence < 60
                    )
                    if source_missing:
                        required_note = (
                            " This field is required before you can save."
                            if field_name in REQUIRED_FIELDS
                            else ""
                        )
                        st.warning(
                            f"{label}: OCR did not find a labeled value. Enter it from the "
                            f"original document.{required_note}"
                        )
                    elif low_confidence:
                        st.warning(
                            f"{label}: OCR text confidence is low "
                            f"({field_confidence}%). Check the value against the original; "
                            "this score does not measure legal correctness."
                        )
                    source_text = (
                        "Owner-entered/reviewed value"
                        if isinstance(existing_correction, str) and existing_correction
                        else "OCR suggestion" if isinstance(ocr_value, str) and ocr_value
                        else "Owner-entered value"
                    )
                    st.caption(f"{label} source: {source_text}")
                    help_text = (
                        "Enter a positive number and one explicit unit, for example "
                        "'2.5 acres' or '120 sq ft'. Units are not converted."
                        if field_name == "land_area"
                        else "Enter the date as DD/MM/YYYY or YYYY-MM-DD."
                        if field_name == "document_date"
                        else f"Required field: {label}."
                        if field_name in REQUIRED_FIELDS
                        else "Optional field. Leave blank if it is not shown in the document."
                    )
                    edited_fields[field_name] = st.text_input(
                        label,
                        value=initial_value,
                        key=f"corrected_{document.id}_{field_name}",
                        help=help_text,
                    )
                preview_requested = st.form_submit_button("Preview corrected information")
                save_corrections = st.form_submit_button(
                    "Save as draft version",
                    type="primary",
                    disabled=preview_key not in st.session_state,
                )

            if preview_requested:
                validation_errors = validate_corrected_fields(edited_fields)
                if validation_errors:
                    st.session_state.pop(preview_key, None)
                    st.error("Correct these fields before previewing:")
                    for error in validation_errors:
                        st.warning(error)
                else:
                    st.session_state[preview_key] = dict(edited_fields)
                    st.rerun()

            if preview_key in st.session_state:
                st.subheader("Preview of owner-reviewed information")
                st.caption(
                    "These values are owner-entered/reviewed draft data, not OCR text "
                    "and not an approved land record."
                )
                preview_fields = st.session_state[preview_key]
                for field_name, label in field_labels:
                    preview_value = preview_fields.get(field_name, "")
                    st.write(f"**{label}:** {preview_value or 'Not provided'}")

            if save_corrections:
                validation_errors = validate_corrected_fields(edited_fields)
                if validation_errors:
                    st.error("Correct these fields before saving:")
                    for error in validation_errors:
                        st.warning(error)
                elif st.session_state.get(preview_key) != edited_fields:
                    st.warning("The values changed since preview. Preview them again before saving.")
                    st.session_state.pop(preview_key, None)
                else:
                    try:
                        service.save_landowner_document_corrections(
                            token,
                            document.id,
                            edited_fields,
                        )
                    except (ValueError, AuthorizationError) as error:
                        st.error(str(error))
                    else:
                        st.session_state.pop(preview_key, None)
                        st.success("Owner-reviewed fields saved as a new draft version.")
                        st.rerun()

            saved_fields_match_form = all(
                corrected_fields.get(field_name) == (value.strip() or None)
                for field_name, value in edited_fields.items()
            )
            saved_field_errors = validate_corrected_fields(corrected_fields)
            if not saved_field_errors and not saved_fields_match_form:
                st.warning("Save your latest reviewed field changes before submitting.")
            elif saved_field_errors:
                st.caption("Complete and save all required fields before submitting.")

            if st.button(
                "Submit application for verification",
                type="primary",
                key=f"submit_application_{document.application_id}",
                disabled=bool(saved_field_errors) or not saved_fields_match_form,
            ):
                try:
                    submission = service.submit_landowner_application(
                        token,
                        document.application_id,
                    )
                except (ValueError, AuthorizationError) as error:
                    st.error(str(error))
                except SQLAlchemyError:
                    logger.exception(
                        "Database error submitting landowner application %s",
                        document.application_id,
                    )
                    st.error(
                        "The application could not be submitted because the database "
                        "transaction failed. No successful submission was confirmed."
                    )
                else:
                    st.session_state[submission_notice_key] = (
                        f"Application **{submission.application_number}** was submitted "
                        f"successfully. Current status: **{_status_label(submission.status)}**."
                    )
                    st.rerun()


def _my_applications() -> None:
    st.markdown('<div class="portal-eyebrow">Your submissions</div>', unsafe_allow_html=True)
    st.title("My Applications")
    _show_pending_notice()
    applications = service.list_landowner_applications(token)
    _render_applications(applications)
    if not applications:
        return

    selected_id = st.selectbox(
        "Open an application",
        options=[application.id for application in applications],
        format_func=lambda application_id: next(
            f"{application.application_number} · {_status_label(application.status)}"
            for application in applications
            if application.id == application_id
        ),
        key="landowner_open_application",
    )
    try:
        detail = service.get_landowner_application(token, selected_id)
    except AuthorizationError as error:
        st.error(str(error))
        return
    st.subheader(f"Application {detail.application_number}")
    st.write(f"**Current status:** {_status_label(detail.status)}")
    st.write(f"**Current version:** {detail.current_version}")
    st.write(
        "**Submitted:** "
        + (
            detail.submitted_at.strftime("%d %b %Y, %H:%M UTC")
            if detail.submitted_at is not None
            else "Not submitted"
        )
    )
    st.write(
        "**Supporting documents:** "
        + (", ".join(detail.document_filenames) if detail.document_filenames else "None")
    )
    st.markdown("**Application field snapshot**")
    for field_name, value in detail.corrected_fields_json.items():
        label = field_name.replace("_", " ").title()
        st.write(f"**{label}:** {value if isinstance(value, str) and value else 'Not provided'}")
    if detail.status == "submitted":
        _show_pending_notice()


def _notifications() -> None:
    st.markdown('<div class="portal-eyebrow">Updates</div>', unsafe_allow_html=True)
    st.title("Notifications")
    _render_notifications(service.list_landowner_notifications(token))


def _approved_records() -> None:
    st.markdown('<div class="portal-eyebrow">Verified records</div>', unsafe_allow_html=True)
    st.title("Approved Land Records")
    st.caption(
        "Only record references and approval dates are shown here. Private documents, "
        "personal field data, and officer notes are not displayed."
    )
    records = service.list_landowner_approved_records(token)
    if not records:
        st.info("You don't have any approved land records yet.")
        return

    for record in records:
        with st.container(border=True):
            record_column, date_column = st.columns([2, 1])
            with record_column:
                st.markdown(f"**{record.public_record_id}**")
                st.caption(f"Approved version {record.current_version}")
            with date_column:
                st.caption("Approved")
                st.write(record.approved_at.strftime("%d %b %Y"))


if current_page == "Dashboard":
    _dashboard()
elif current_page == "Upload Land Document":
    _upload_document()
elif current_page == "My Applications":
    _my_applications()
elif current_page == "Notifications":
    _notifications()
elif current_page == "Approved Land Records":
    _approved_records()
