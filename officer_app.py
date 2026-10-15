from __future__ import annotations

import logging

import streamlit as st

from modules.auth import (
    ApplicationReviewDetail,
    ApprovedRegistryRecord,
    AuditEventSummary,
    AuthorizationError,
    AuthService,
    OfficerApplicationSummary,
    OfficerDashboardMetrics,
)
from modules.auth_ui import get_auth_service, render_login
from modules.document_storage import DocumentStorageError
from modules.ocr import render_document_preview
from modules.field_validation import FIELD_LABELS

st.set_page_config(
    page_title="Government Officer Portal",
    page_icon=":material/verified_user:",
    layout="wide",
)

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
    .audit-verified {
        color: #1a7f37;
        font-weight: 600;
    }
    .audit-unverified {
        color: #c93838;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

token = render_login("officer", "Government Officer Portal")
if token is None:
    st.stop()

service: AuthService = get_auth_service()
logger = logging.getLogger(__name__)

PAGES = (
    "Department Dashboard",
    "Review Queue",
    "Application Review",
    "Approved Registry",
    "Audit Explorer",
)

with st.sidebar:
    st.divider()
    st.subheader("Officer services")
    current_page = st.radio(
        "Navigate",
        PAGES,
        key="officer_page",
        label_visibility="collapsed",
    )


def _status_label(status: str) -> str:
    return {
        "draft": "Draft",
        "submitted": "Submitted",
        "under_review": "Under review",
        "corrections_requested": "Corrections required",
        "approved": "Approved",
        "rejected": "Rejected",
    }.get(status, "Status unavailable")


def _status_badge(status: str) -> str:
    """Return HTML for a colored status badge."""
    colors = {
        "draft": "#9ca3af",
        "submitted": "#3b82f6",
        "under_review": "#f59e0b",
        "corrections_requested": "#ef4444",
        "approved": "#22c55e",
        "rejected": "#ef4444",
    }
    color = colors.get(status, "#6b7280")
    label = _status_label(status)
    return f'<span style="background:{color};color:white;padding:0.25rem 0.75rem;border-radius:9999px;font-size:0.75rem;font-weight:600;">{label}</span>'


def _render_officer_metrics(metrics: OfficerDashboardMetrics) -> None:
    metric_specs = (
        ("Total submitted", metrics.total_submitted_applications),
        ("Pending review", metrics.pending_review_applications),
        ("Under review", metrics.under_review_applications),
        ("Correction requests", metrics.correction_requests),
        ("Approved", metrics.approved_applications),
        ("Rejected", metrics.rejected_applications),
    )
    for start in range(0, len(metric_specs), 3):
        columns = st.columns(3)
        for column, (label, value) in zip(columns, metric_specs[start : start + 3]):
            with column:
                st.metric(label, value)


def _application_row(application: OfficerApplicationSummary) -> None:
    with st.container(border=True):
        number_column, status_column, date_column = st.columns([2.2, 1.5, 1.4])
        with number_column:
            st.markdown(f"**{application.application_number}**")
            st.caption(
                f"Applicant: {application.applicant_name} · "
                f"District: {application.district or 'Not provided'} · "
                f"Village: {application.village or 'Not provided'}"
            )
        with status_column:
            st.markdown(_status_badge(application.status), unsafe_allow_html=True)
        with date_column:
            st.caption("Submitted")
            st.write(
                application.submitted_at.strftime("%d %b %Y")
                if application.submitted_at
                else "—"
            )
        if st.button(
            "Open application",
            key=f"open_review_{application.id}",
            type="primary",
        ):
            st.session_state["selected_review_application_id"] = application.id
            st.session_state["officer_page"] = "Application Review"
            st.query_params["app_id"] = str(application.id)
            st.rerun()


def _render_applications(applications: tuple[OfficerApplicationSummary, ...]) -> None:
    if not applications:
        st.info("No applications match the current filters.")
        return
    for application in applications:
        _application_row(application)


def _dashboard() -> None:
    st.markdown('<div class="portal-eyebrow">Department overview</div>', unsafe_allow_html=True)
    st.title("Department Dashboard")
    st.write("Track all land record applications across the department.")

    _render_officer_metrics(service.get_officer_dashboard_metrics(token))

    st.subheader("Quick actions")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📋 View Review Queue", type="primary", use_container_width=True):
            st.session_state["officer_page"] = "Review Queue"
            st.rerun()
    with col2:
        if st.button("📁 Approved Registry", use_container_width=True):
            st.session_state["officer_page"] = "Approved Registry"
            st.rerun()
    with col3:
        if st.button("🔍 Audit Explorer", use_container_width=True):
            st.session_state["officer_page"] = "Audit Explorer"
            st.rerun()


def _review_queue() -> None:
    st.markdown('<div class="portal-eyebrow">Review queue</div>', unsafe_allow_html=True)
    st.title("Review Queue")
    st.caption("Applications assigned to you or available for review based on department policy.")

    with st.expander("Filters", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            status_filter = st.selectbox(
                "Status",
                ["All", "submitted", "under_review", "corrections_requested"],
                key="review_status_filter",
            )
        with col2:
            app_number_filter = st.text_input(
                "Application number",
                placeholder="Search by number...",
                key="review_app_number_filter",
            )
        with col3:
            district_filter = st.text_input(
                "District",
                placeholder="Filter by district...",
                key="review_district_filter",
            )
        with col4:
            village_filter = st.text_input(
                "Village",
                placeholder="Filter by village...",
                key="review_village_filter",
            )

    status = None if status_filter == "All" else status_filter
    applications = service.list_officer_review_queue_filtered(
        token,
        status=status,
        application_number=app_number_filter if app_number_filter else None,
        district=district_filter if district_filter else None,
        village=village_filter if village_filter else None,
    )

    st.divider()
    _render_applications(applications)


def _application_review() -> None:
    st.markdown('<div class="portal-eyebrow">Application review</div>', unsafe_allow_html=True)
    st.title("Application Review")
    st.caption(
        "Review the submitted application and supporting evidence. Final approval, "
        "rejection, and correction decisions are not available here."
    )

    app_id = st.session_state.get("selected_review_application_id")
    query_app_id = st.query_params.get("app_id")
    if query_app_id:
        try:
            app_id = int(query_app_id)
        except ValueError:
            app_id = None

    if not isinstance(app_id, int) or app_id < 1:
        st.info("Select an application from the Review Queue to view its details.")
        return

    detail: ApplicationReviewDetail | None = service.get_application_for_review(token, app_id)
    if detail is None:
        st.error("Application not found or you are not authorized to review it.")
        return

    # Header
    col1, col2 = st.columns([3, 1])
    with col1:
        st.subheader(f"{detail.application_number}")
        st.caption(f"Applicant: {detail.applicant_name} ({detail.applicant_email})")
    with col2:
        st.markdown(_status_badge(detail.status), unsafe_allow_html=True)

    st.caption(
        f"Version {detail.current_version} · Submitted "
        f"{detail.submitted_at.strftime('%d %b %Y, %H:%M UTC') if detail.submitted_at else 'date unavailable'}"
    )
    if detail.assigned_officer_id is None:
        if detail.status in {"submitted", "under_review", "corrections_requested"}:
            if st.button("Assign to me and start review", type="primary"):
                try:
                    review_started = service.start_officer_application_review(token, detail.id)
                except (AuthorizationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success(
                        f"Assigned to you. Current status: {_status_label(review_started.status)}."
                    )
                    st.rerun()
    else:
        st.info(
            "This application is assigned to you."
            if detail.assigned_officer_id
            == service.require_role(token, "officer").user_id
            else "This application is assigned to an officer."
        )

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(
        ["OCR & warnings", "Owner fields", "Original documents", "Review history"]
    )

    with tab1:
        st.write(f"**OCR processing status:** {detail.ocr_status.capitalize()}")
        extracted_fields = detail.extracted_fields_json.get("fields")
        if isinstance(extracted_fields, dict) and extracted_fields:
            for field_name, value in extracted_fields.items():
                label = (
                    FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
                    if isinstance(field_name, str)
                    else str(field_name)
                )
                st.write(f"**{label}:** {value if value is not None else 'Not extracted'}")
        else:
            st.warning(
                "OCR fields are missing or unreadable. Use the original document to "
                "understand the submitted values."
            )
        ocr_text = detail.extracted_fields_json.get("ocr_text")
        if isinstance(ocr_text, str) and ocr_text.strip():
            with st.expander("Raw OCR text"):
                st.text_area(
                    "Recognized text",
                    value=ocr_text,
                    height=200,
                    disabled=True,
                    key=f"review_ocr_text_{detail.id}",
                )
        else:
            st.warning("No readable OCR text was saved for this application.")
        if detail.ocr_warnings:
            st.markdown("**OCR warnings**")
            for warning in detail.ocr_warnings:
                st.warning(warning)
        if detail.validation_warnings:
            st.markdown("**Existing field validation warnings**")
            for warning in detail.validation_warnings:
                st.warning(warning)
        elif detail.corrected_fields_json:
            st.success("The saved owner-reviewed fields pass the current field validation checks.")

    with tab2:
        st.write("**Landowner-Corrected Fields**")
        if detail.corrected_fields_json:
            for key, value in detail.corrected_fields_json.items():
                label = FIELD_LABELS.get(key, key.replace("_", " ").title())
                st.write(f"**{label}:** {value if value is not None else 'Not provided'}")
        else:
            st.info("No corrected fields submitted by landowner.")

    with tab3:
        st.write("**Uploaded Documents**")
        if detail.documents:
            for doc in detail.documents:
                with st.container(border=True):
                    st.write(f"**{doc.original_filename}**")
                    st.caption(f"Type: {doc.mime_type} · Uploaded: {doc.uploaded_at.strftime('%d %b %Y, %H:%M UTC')}")
                    try:
                        original = service.get_officer_application_document(
                            token,
                            detail.id,
                            doc.id,
                        )
                        preview = render_document_preview(
                            original.content,
                            original.original_filename,
                            original.mime_type,
                        )
                    except (AuthorizationError, DocumentStorageError) as error:
                        st.error(f"Original document is unavailable: {error}")
                    else:
                        if preview is None:
                            st.warning("The original document could not be previewed.")
                        else:
                            st.image(
                                preview,
                                caption="Original document (read-only)",
                                use_container_width=True,
                            )
        else:
            st.error("No supporting document is attached to this application.")

    with tab4:
        st.write("**Review History**")
        if detail.review_events:
            for event in detail.review_events:
                with st.container(border=True):
                    decision_colors = {
                        "approve": "#22c55e",
                        "reject": "#ef4444",
                        "request_corrections": "#f59e0b",
                    }
                    color = decision_colors.get(event.decision, "#6b7280")
                    st.markdown(
                        f'<span style="background:{color};color:white;padding:0.2rem 0.5rem;border-radius:4px;font-size:0.75rem;font-weight:600;">{event.decision}</span>',
                        unsafe_allow_html=True,
                    )
                    if event.comments:
                        st.write(event.comments)
                    st.caption(f"By {event.officer_name} · {event.created_at.strftime('%d %b %Y, %H:%M UTC')}")
        else:
            st.info("No review events yet.")

    st.divider()
    st.warning(
        "Review decision workflows (approve, reject, request corrections) are not implemented in this prototype. "
        "This view is read-only."
    )


def _approved_registry() -> None:
    st.markdown('<div class="portal-eyebrow">Approved records</div>', unsafe_allow_html=True)
    st.title("Approved Registry")
    st.caption("Search and filter approved land records. Private documents and personal field data are not displayed.")

    with st.expander("Search & Filters", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            search = st.text_input("Search", placeholder="Record ID or application number...", key="registry_search")
        with col2:
            district = st.text_input("District", placeholder="Filter by district...", key="registry_district")
        with col3:
            village = st.text_input("Village", placeholder="Filter by village...", key="registry_village")

    records = service.list_approved_registry(
        token,
        search=search if search else None,
        district=district if district else None,
        village=village if village else None,
    )

    st.divider()
    if not records:
        st.info("No approved records match the current filters.")
        return

    for record in records:
        with st.container(border=True):
            record_col, date_col = st.columns([2.5, 1])
            with record_col:
                st.markdown(f"**{record.public_record_id}**")
                st.caption(f"Application: {record.application_number} · Applicant: {record.applicant_name}")
                st.caption(f"Approved version {record.current_version}")
            with date_col:
                st.caption("Approved")
                st.write(record.approved_at.strftime("%d %b %Y"))

            if st.button("View details", key=f"registry_detail_{record.public_record_id}"):
                # Could navigate to a detail view
                st.toast("Detail view coming soon")


def _audit_explorer() -> None:
    st.markdown('<div class="portal-eyebrow">Audit log</div>', unsafe_allow_html=True)
    st.title("Audit Explorer")
    st.caption("View the SHA-256 hash-linked audit trail for all important actions.")

    with st.expander("Filters", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            app_id_filter = st.text_input("Application ID", placeholder="Filter by application...", key="audit_app_id")
        with col2:
            record_id_filter = st.text_input("Land Record ID", placeholder="Filter by land record...", key="audit_record_id")
        with col3:
            limit = st.number_input("Max results", min_value=10, max_value=500, value=100, step=10, key="audit_limit")

    try:
        app_id = int(app_id_filter) if app_id_filter else None
    except ValueError:
        app_id = None
        st.error("Invalid Application ID")

    try:
        record_id = int(record_id_filter) if record_id_filter else None
    except ValueError:
        record_id = None
        st.error("Invalid Land Record ID")

    events = service.list_audit_events(token, application_id=app_id, land_record_id=record_id, limit=limit)

    st.divider()
    if not events:
        st.info("No audit events match the current filters.")
        return

    for event in events:
        with st.container(border=True):
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                st.markdown(f"**{event.event_type}**")
                st.caption(f"Actor: {event.actor_name} ({event.actor_email})")
            with col2:
                if event.application_id:
                    st.caption(f"Application: {event.application_id}")
                if event.land_record_id:
                    st.caption(f"Land Record: {event.land_record_id}")
                st.caption(event.created_at.strftime("%d %b %Y, %H:%M UTC"))
            with col3:
                if event.integrity_verified:
                    st.markdown('<span class="audit-verified">✓ Integrity verified</span>', unsafe_allow_html=True)
                else:
                    st.markdown('<span class="audit-unverified">✗ Integrity FAILED</span>', unsafe_allow_html=True)

            # Show hash chain info
            with st.expander("Hash chain details"):
                st.code(f"Event hash: {event.event_hash}")
                if event.previous_event_hash:
                    st.code(f"Previous hash: {event.previous_event_hash}")
                else:
                    st.caption("Genesis event (no previous hash)")


if current_page == "Department Dashboard":
    _dashboard()
elif current_page == "Review Queue":
    _review_queue()
elif current_page == "Application Review":
    _application_review()
elif current_page == "Approved Registry":
    _approved_registry()
elif current_page == "Audit Explorer":
    _audit_explorer()