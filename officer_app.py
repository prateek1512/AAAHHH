from __future__ import annotations

import logging

import logging

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from modules.auth import (
    ApplicationReviewDetail,
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
from modules.parcel_map import render_parcel_map
from modules.registry_ui import render_approved_registry

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


def _navigate_to(page: str) -> None:
    st.session_state["officer_page"] = page


def _open_application_for_review(application_id: int) -> None:
    st.session_state["selected_review_application_id"] = application_id
    st.session_state["officer_page"] = "Application Review"
    st.query_params["app_id"] = str(application_id)


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
        st.button(
            "Open application",
            key=f"open_review_{application.id}",
            type="primary",
            on_click=_open_application_for_review,
            args=(application.id,),
        )


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
        st.button(
            "📋 View Review Queue",
            type="primary",
            use_container_width=True,
            on_click=_navigate_to,
            args=("Review Queue",),
        )
    with col2:
        st.button(
            "📁 Approved Registry",
            use_container_width=True,
            on_click=_navigate_to,
            args=("Approved Registry",),
        )
    with col3:
        st.button(
            "🔍 Audit Explorer",
            use_container_width=True,
            on_click=_navigate_to,
            args=("Audit Explorer",),
        )


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
        "Review the submitted application and supporting evidence. Decisions are "
        "recorded in the review history and audit log."
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
    decision_notice_key = f"decision_notice_{detail.id}"
    if decision_notice_key in st.session_state:
        st.success(st.session_state.pop(decision_notice_key))

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
    officer = service.require_role(token, "officer")
    if detail.assigned_officer_id is None:
        if detail.status in {"submitted", "under_review", "corrections_requested"}:
            if st.button("Assign to me and start review", type="primary", key=f"claim_top_{detail.id}"):
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
            if detail.assigned_officer_id == officer.user_id
            else "This application is assigned to an officer."
        )
        if (
            detail.assigned_officer_id == officer.user_id
            and detail.status == "submitted"
            and st.button("Start review", type="primary", key=f"start_top_{detail.id}")
        ):
            try:
                review_started = service.start_officer_application_review(
                    token,
                    detail.id,
                )
            except (AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success(f"Current status: {_status_label(review_started.status)}.")
                st.rerun()

    st.divider()

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
        [
            "OCR & warnings",
            "Owner fields",
            "Original documents",
            "Review history",
            "Consistency indicator",
            "Discrepancy checks",
            "Parcel map",
        ]
    )

    with tab1:
        st.write(f"**OCR processing status:** {detail.ocr_status.capitalize()}")
        extracted_fields = detail.extracted_fields_json.get("fields")
        raw_confidence = detail.extracted_fields_json.get("field_confidence")
        field_confidence = raw_confidence if isinstance(raw_confidence, dict) else {}
        if isinstance(extracted_fields, dict) and extracted_fields:
            for field_name, value in extracted_fields.items():
                label = (
                    FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
                    if isinstance(field_name, str)
                    else str(field_name)
                )
                confidence = field_confidence.get(field_name)
                confidence_text = (
                    f" · OCR engine confidence {confidence}%"
                    if isinstance(confidence, (int, float))
                    else ""
                )
                st.write(
                    f"**{label}:** {value if value is not None else 'Not extracted'}"
                    f"{confidence_text}"
                )
        else:
            st.warning(
                "OCR fields are missing or unreadable. Use the original document to "
                "understand the submitted values."
            )
        with st.expander("Compare OCR results with landowner corrections"):
            extracted = extracted_fields if isinstance(extracted_fields, dict) else {}
            warning_values = detail.extracted_fields_json.get("field_warnings", [])
            warnings_by_field: dict[str, list[str]] = {}
            if isinstance(warning_values, list):
                for warning in warning_values:
                    if not isinstance(warning, dict):
                        continue
                    field_name = warning.get("field")
                    message = warning.get("message")
                    if isinstance(field_name, str) and isinstance(message, str):
                        warnings_by_field.setdefault(field_name, []).append(message)
            comparison_rows = []
            for field_name, label in FIELD_LABELS.items():
                confidence = field_confidence.get(field_name)
                comparison_rows.append(
                    {
                        "Field": label,
                        "OCR result": extracted.get(field_name) or "Not extracted",
                        "OCR engine confidence": (
                            f"{confidence}%"
                            if isinstance(confidence, (int, float))
                            else "Not provided"
                        ),
                        "Landowner-corrected value": (
                            detail.corrected_fields_json.get(field_name) or "Not provided"
                        ),
                        "Review warnings": " ".join(warnings_by_field.get(field_name, []))
                        or "None",
                    }
                )
            st.dataframe(comparison_rows, hide_index=True, use_container_width=True)
            st.caption(
                "OCR confidence estimates text recognition only; it does not establish "
                "whether the extracted or corrected field is factually or legally correct."
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

    with tab5:
        indicator = detail.consistency_score
        st.subheader("Preliminary consistency indicator")
        score = indicator.get("score")
        if isinstance(score, (int, float)):
            st.metric("Preliminary consistency indicator", f"{score:.1f} / 100")
        else:
            st.info("Indicator unavailable because no scoring components are available.")
        st.warning(indicator.get("warning", "For manual-review prioritization only."))
        st.caption(indicator.get("formula", ""))
        components = indicator.get("components", {})
        if isinstance(components, dict):
            component_labels = {
                "ocr_confidence": "OCR engine confidence",
                "required_completeness": "Required-field completeness",
                "reference_consistency": "Reference consistency",
                "audit_integrity": "Required audit events and chain integrity",
            }
            for component_name, component in components.items():
                if not isinstance(component, dict):
                    continue
                component_score = component.get("score")
                shown_score = (
                    f"{component_score:.1f} / 100"
                    if isinstance(component_score, (int, float))
                    else "Unavailable"
                )
                effective_weight = component.get("effective_weight_percent")
                weight_text = (
                    f" · Effective weight {effective_weight:g}%"
                    if isinstance(effective_weight, (int, float))
                    else ""
                )
                with st.expander(
                    f"{component_labels.get(component_name, component_name)}: "
                    f"{shown_score}{weight_text}"
                ):
                    st.write(component.get("explanation", ""))
                    affected = component.get("warnings_affecting_result", [])
                    if affected:
                        st.markdown("Warnings affecting this component:")
                        for warning in affected:
                            st.write(
                                f"**{warning.get('code', 'warning')} · "
                                f"{', '.join(warning.get('fields_compared', []))}**"
                            )
                            st.caption(warning.get("explanation", ""))
                            values = warning.get("compared_values", {})
                            if isinstance(values, dict):
                                for field_name, value in values.items():
                                    st.caption(f"{field_name}: {value}")
                    else:
                        st.caption("No warning affected this component.")
        st.caption(
            "A higher value only summarizes consistency in the checks that were "
            "available. Missing inputs are excluded and are not treated as passed."
        )

    with tab6:
        if detail.discrepancy_warnings:
            for warning in detail.discrepancy_warnings:
                severity = warning.get("severity")
                title = (
                    "Definite format or required-field issue"
                    if severity == "definite"
                    else "Possible discrepancy"
                    if severity == "possible"
                    else "Comparison unavailable"
                )
                fields_compared = warning.get("fields_compared", [])
                st.markdown(f"**{title} · {', '.join(fields_compared)}**")
                st.write(warning.get("rule", ""))
                compared_values = warning.get("compared_values", {})
                if isinstance(compared_values, dict):
                    for field, value in compared_values.items():
                        st.caption(f"{field}: {value}")
                st.divider()
        else:
            st.success("No discrepancies were identified by the available deterministic checks.")
        st.caption(
            "These checks identify values for human review. A mismatch is not proof of "
            "fraud, ownership, or a legal defect; no decision is made automatically."
        )

    with tab7:
        render_parcel_map(
            detail.parcel_geojson,
            record_identifier=detail.application_number,
            identifier_label="Application number",
            attributes={
                "district": detail.corrected_fields_json.get("district"),
                "village": detail.corrected_fields_json.get("village"),
                "survey_or_plot_number": detail.corrected_fields_json.get(
                    "survey_or_plot_number"
                ),
                "land_area": detail.corrected_fields_json.get("land_area"),
            },
            key=f"review_{detail.id}_{detail.current_version}",
            validation_status=detail.parcel_geometry_validation_status,
        )

    st.divider()
    if detail.status == "corrections_requested":
        st.subheader("Record a review decision")
        st.warning(
            "⏳ **Awaiting Landowner Resubmission**: Corrections were requested on this application. "
            "An official approval or rejection decision can only be recorded after the landowner revises their "
            "fields and resubmits the application from the Landowner Portal."
        )
        with st.expander("How to complete this workflow step in the demo"):
            st.markdown(
                "1. Switch to the **Landowner Portal** ([http://localhost:8502](http://localhost:8502)) and sign in as `demo.landowner@example.test`.\n"
                f"2. Navigate to **My Applications** and select **{detail.application_number}**.\n"
                "3. Review the officer's requested correction remarks, modify the land fields, and click **Save changes and resubmit for review**.\n"
                "4. Return to this Officer Portal: the application status will now be **Submitted**.\n"
                "5. Click **Start review** to unlock the Approve decision form."
            )
    elif detail.status == "submitted":
        st.subheader("Record a review decision")
        st.info(
            "This application has been submitted / resubmitted and is awaiting active review. "
            "Start active review to record an approval, rejection, or correction request."
        )
        claim_label = (
            "Start review"
            if detail.assigned_officer_id == officer.user_id
            else "Assign to me and start review"
        )
        if st.button(
            f"▶ {claim_label}",
            type="primary",
            key=f"start_review_decision_sec_{detail.id}",
        ):
            try:
                review_started = service.start_officer_application_review(token, detail.id)
            except (AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success(f"Review started. Current status: {_status_label(review_started.status)}.")
                st.rerun()
    elif detail.assigned_officer_id != officer.user_id:
        st.subheader("Record a review decision")
        st.info("Assign the application to yourself before making a decision.")
        if st.button(
            "▶ Assign to me and start review",
            type="primary",
            key=f"assign_decision_sec_{detail.id}",
        ):
            try:
                review_started = service.start_officer_application_review(token, detail.id)
            except (AuthorizationError, ValueError) as error:
                st.error(str(error))
            else:
                st.success(f"Assigned to you. Current status: {_status_label(review_started.status)}.")
                st.rerun()
    elif detail.status != "under_review":
        st.subheader("Record a review decision")
        st.info(f"A decision is not available for applications in {_status_label(detail.status)} status.")
    else:
        st.subheader("Record a review decision")
        st.caption(
            "Decisions are final for this application state. Approval depends on validated "
            "owner-reviewed fields and a supporting document, never OCR confidence or the "
            "preliminary consistency indicator alone."
        )
        with st.form(f"decision_form_{detail.id}"):
            decision_label = st.selectbox(
                "Decision",
                ("Approve", "Reject", "Request corrections"),
                key=f"decision_choice_{detail.id}",
            )
            decision = {
                "Approve": "approve",
                "Reject": "reject",
                "Request corrections": "request_corrections",
            }[decision_label]
            comments = st.text_area(
                "Reason or correction instructions",
                help=(
                    "Required for rejection and correction requests. Explain the basis "
                    "for the rejection or the specific changes needed."
                ),
                key=f"decision_comments_{detail.id}",
            )
            confirmed = st.checkbox(
                f"I confirm this {decision_label.lower()} decision for "
                f"{detail.application_number}.",
                key=f"decision_confirm_{detail.id}",
            )
            submit_decision = st.form_submit_button(
                "Confirm and save decision",
                type="primary",
            )
        if submit_decision:
            if not confirmed:
                st.error("Confirm the decision before saving it.")
            elif decision in {"reject", "request_corrections"} and len(comments.strip()) < 10:
                st.error(
                    "Enter a meaningful reason or correction message of at least 10 characters."
                )
            else:
                try:
                    result = service.decide_officer_application(
                        token,
                        detail.id,
                        decision,
                        comments,
                    )
                except (AuthorizationError, ValueError) as error:
                    st.error(str(error))
                except SQLAlchemyError:
                    logger.exception(
                        "Database transaction failed for decision on application %s",
                        detail.id,
                    )
                    st.error(
                        "The decision could not be saved because the database transaction "
                        "failed. No decision was confirmed."
                    )
                else:
                    decision_message = (
                        f"{result.application_number} was "
                        f"{'approved' if result.status == 'approved' else result.status.replace('_', ' ')}."
                    )
                    if result.public_record_id:
                        decision_message += f" Public record ID: {result.public_record_id}."
                    st.session_state[decision_notice_key] = decision_message
                    st.rerun()


def _approved_registry() -> None:
    render_approved_registry(service, token, "officer")


def _audit_explorer() -> None:
    st.markdown('<div class="portal-eyebrow">Audit log</div>', unsafe_allow_html=True)
    st.title("Audit Explorer")
    st.caption(
        "Inspect the append-only SHA-256 audit chain. Hash verification detects "
        "inconsistencies but does not prove legal ownership or factual accuracy."
    )

    with st.expander("Filters", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            app_id_filter = st.text_input("Application ID", placeholder="Filter by application...", key="audit_app_id")
        with col2:
            record_id_filter = st.text_input("Land Record ID", placeholder="Filter by land record...", key="audit_record_id")
        with col3:
            limit = st.number_input("Max results", min_value=10, max_value=500, value=100, step=10, key="audit_limit")

    if st.button("Verify complete audit chain", type="primary"):
        try:
            st.session_state["audit_chain_verification"] = service.verify_audit_chain(token)
        except AuthorizationError as error:
            st.error(str(error))
        except SQLAlchemyError:
            logger.exception("Database error verifying audit chain")
            st.error("The audit chain could not be verified because of a database error.")

    verification = st.session_state.get("audit_chain_verification")
    if verification is not None:
        st.caption("Showing the last requested verification result; run a fresh check when needed.")
        if verification.valid:
            st.success(
                f"Audit chain verified: all {verification.event_count} event(s) link "
                "from the deterministic genesis and their hashes are consistent."
            )
        else:
            st.error(
                f"Audit chain verification failed for {verification.event_count} event(s)."
            )
            for issue in verification.errors:
                st.write(f"- {issue}")
        st.caption(
            "Verification is tamper-evidence, not tamper-proof. A database administrator "
            "could alter events and recompute the chain; verification does not validate "
            "the truth of event data."
        )

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

    try:
        events = service.list_audit_events(
            token,
            application_id=app_id,
            land_record_id=record_id,
            limit=limit,
        )
    except AuthorizationError as error:
        st.error(str(error))
        return
    except SQLAlchemyError:
        logger.exception("Database error loading audit events")
        st.error("Audit events could not be loaded.")
        return

    st.divider()
    if not events:
        st.info("No audit events match the current filters.")
        return

    for event in events:
        with st.container(border=True):
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                st.markdown(f"**{event.event_type}**")
                st.caption(f"Event ID: {event.id}")
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
                st.code(f"Previous hash: {event.previous_event_hash}")


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