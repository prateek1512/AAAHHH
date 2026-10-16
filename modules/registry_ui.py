"""Shared approved land registry presentation for both portals."""

from __future__ import annotations

import logging
from io import BytesIO

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from modules.auth import AuthService, AuthorizationError
from modules.field_validation import FIELD_LABELS
from modules.parcel_map import render_parcel_map

logger = logging.getLogger(__name__)
PAGE_SIZE = 25


def _reset_page(page_key: str) -> None:
    st.session_state[page_key] = 0


def _event_label(event_type: str) -> str:
    labels = {
        "application_submitted": "Application submitted",
        "application_resubmitted": "Application resubmitted",
        "application_approve": "Application approved",
        "approved_record_version_created": "Approved record version created",
        "approved_record_correction_authorized": "Approved record correction authorized",
    }
    return labels.get(event_type, event_type.replace("_", " ").capitalize())


def _render_version_history(
    service: AuthService,
    token: str,
    record_id: str,
    portal_key: str,
) -> None:
    st.subheader("Approved version history")
    try:
        versions = service.list_approved_record_versions(token, record_id)
    except SQLAlchemyError:
        logger.exception("Database error loading versions for record %s", record_id)
        st.error("Approved record versions could not be loaded.")
        return

    if not versions:
        st.info("No immutable approved-version snapshots are available.")
        return

    for version in versions:
        with st.expander(f"Version {version.version_number}"):
            st.caption(
                f"Authorized {version.authorized_at.strftime('%d %b %Y, %H:%M UTC')} · "
                f"Audit event {version.audit_event_id}"
            )
            st.caption(f"Version hash: {version.version_hash}")
            if service.authenticate_session(token).role == "officer":
                st.caption(f"Authorized by {version.authorizer_name}")
            for name, value in version.approved_fields.items():
                st.write(f"**{FIELD_LABELS.get(name, name)}:** {value}")

    if len(versions) < 2:
        return

    st.markdown("**Compare versions**")
    version_numbers = [version.version_number for version in versions]
    from_column, to_column = st.columns(2)
    with from_column:
        from_version_number = st.selectbox(
            "Earlier version",
            version_numbers,
            index=0,
            key=f"{portal_key}_version_from_{record_id}",
        )
    with to_column:
        to_version_number = st.selectbox(
            "Compare with",
            version_numbers,
            index=len(version_numbers) - 1,
            key=f"{portal_key}_version_to_{record_id}",
        )
    previous = next(item for item in versions if item.version_number == from_version_number)
    current = next(item for item in versions if item.version_number == to_version_number)
    if previous.version_number == current.version_number:
        st.info("Choose two different versions to compare.")
        return

    changed_fields = sorted(
        name
        for name in set(previous.approved_fields) | set(current.approved_fields)
        if previous.approved_fields.get(name) != current.approved_fields.get(name)
    )
    if not changed_fields:
        st.info("No published fields differ between these versions.")
        return
    for name in changed_fields:
        old_value = previous.approved_fields.get(name, "Not present")
        new_value = current.approved_fields.get(name, "Not present")
        st.markdown(f"**{FIELD_LABELS.get(name, name)}**")
        before_column, after_column = st.columns(2)
        with before_column:
            st.caption(f"Version {previous.version_number}")
            st.write(old_value)
        with after_column:
            st.caption(f"Version {current.version_number}")
            st.write(new_value)


def _render_officer_correction_form(
    service: AuthService,
    token: str,
    record_id: str,
    fields: dict[str, object],
    portal_key: str,
) -> None:
    with st.expander("Authorized correction to approved record"):
        st.warning(
            "This creates a new official version; earlier approved versions remain unchanged. "
            "Do not include personal identification numbers, contact details, or deed contents "
            "in the correction reason."
        )
        with st.form(f"{portal_key}_correction_form_{record_id}"):
            field_name = st.selectbox(
                "Field to correct",
                tuple(FIELD_LABELS),
                format_func=lambda name: FIELD_LABELS[name],
            )
            current_value = fields.get(field_name, "")
            value = st.text_input(
                "Corrected value",
                value=current_value if isinstance(current_value, str) else str(current_value),
            )
            reason = st.text_area("Correction reason")
            confirmed = st.checkbox(
                "I authorize a new official approved-record version."
            )
            submitted = st.form_submit_button("Create approved version", type="primary")

        if not submitted:
            return
        if not confirmed:
            st.error("Confirm authorization before creating a new approved version.")
            return
        try:
            result = service.correct_approved_record(
                token,
                record_id,
                {field_name: value},
                reason,
            )
        except (AuthorizationError, ValueError) as error:
            st.error(str(error))
        except SQLAlchemyError:
            logger.exception("Database error correcting approved record %s", record_id)
            st.error("The approved record could not be corrected; no new version was confirmed.")
        else:
            st.session_state[f"{portal_key}_correction_notice_{record_id}"] = (
                f"Approved record version {result.version_number} was created."
            )
            st.rerun()


def _render_record_detail(
    service: AuthService,
    token: str,
    record_id: str,
    portal_key: str,
) -> None:
    if st.button("Back to registry", key=f"{portal_key}_registry_back"):
        st.query_params.pop("registry_record", None)
        st.rerun()

    try:
        detail = service.get_approved_registry_record(token, record_id)
    except SQLAlchemyError:
        logger.exception("Database error loading approved registry record %s", record_id)
        st.error("The approved record could not be loaded.")
        return

    if detail is None:
        st.info("This record is unavailable or is not in the approved registry.")
        return

    record = detail.record
    st.title(record.public_record_id)
    st.caption(
        f"Approved version {record.current_version} · "
        f"Approved {record.approved_at.strftime('%d %b %Y')}"
    )
    notice_key = f"{portal_key}_correction_notice_{record_id}"
    if notice_key in st.session_state:
        st.success(st.session_state.pop(notice_key))
    st.markdown("[Audit verification](#audit-verification)")
    if st.button(
        "Show public verification QR code",
        key=f"{portal_key}_public_qr_{record.public_record_id}",
    ):
        try:
            qr_data = service.get_public_approved_record_qr(record.public_record_id)
        except ValueError as error:
            st.error(f"QR code could not be generated: {error}")
        except SQLAlchemyError:
            logger.exception(
                "Database error validating public QR record %s",
                record.public_record_id,
            )
            st.error("QR code could not be generated because the record could not be checked.")
        else:
            if qr_data is None:
                st.warning("A public QR code is available only for approved registry records.")
            else:
                st.image(
                    BytesIO(qr_data.png_bytes),
                    caption="Public record verification QR code",
                    width=240,
                )
                st.link_button(
                    "Open public verification page",
                    qr_data.verification_url,
                )
                st.caption(
                    "The QR code contains only the public verification URL and public record "
                    "ID. It is a convenient link, not proof of ownership, and does not prevent "
                    "copying or forgery."
                )
                st.link_button(
                    "Open public audit integrity check",
                    f"{qr_data.verification_url}#audit-verification",
                    key=f"{portal_key}_public_audit_link_{record.public_record_id}",
                )

    st.subheader("Published parcel details")
    columns = st.columns(2)
    public_fields = (
        ("District", record.district),
        ("Village", record.village),
        ("Survey or plot number", record.survey_or_plot_number),
        ("Land area", record.land_area),
    )
    for index, (label, value) in enumerate(public_fields):
        with columns[index % 2]:
            st.caption(label)
            st.write(value or "Not available")

    st.subheader("Parcel map")
    render_parcel_map(
        record.parcel_geojson,
        record_identifier=record.public_record_id,
        attributes={
            "district": record.district,
            "village": record.village,
            "survey_or_plot_number": record.survey_or_plot_number,
            "land_area": record.land_area,
        },
        key=f"{portal_key}_{record.public_record_id}",
        validation_status=record.parcel_geometry_validation_status,
    )

    st.subheader("Record history")
    if detail.history:
        for item in detail.history:
            st.markdown(f"**{_event_label(item.event_type)}**")
            st.caption(item.created_at.strftime("%d %b %Y, %H:%M UTC"))
    else:
        st.info("No audit history is available for this record.")

    _render_version_history(service, token, record_id, portal_key)

    st.subheader("Audit verification", anchor="audit-verification")
    if detail.audit_verified:
        st.success("The available audit events pass their hash-integrity checks.")
    else:
        st.error("The audit history could not be fully verified.")
    st.caption(
        "Audit hashes provide a tamper-evidence check for recorded events; "
        "they do not establish legal ownership or verify the truth of a land claim."
    )

    principal = service.authenticate_session(token)
    if principal.role == "officer":
        with st.expander("Additional internal details"):
            try:
                internal = service.get_officer_registry_internal_details(token, record_id)
            except SQLAlchemyError:
                logger.exception(
                    "Database error loading internal registry details for %s", record_id
                )
                st.error("Internal record details could not be loaded.")
                return
            if internal is None:
                st.info("Internal details are unavailable.")
                return
            st.write(f"**Application number:** {internal.application_number}")
            st.write(f"**Applicant:** {internal.applicant_name}")
            st.write(f"**Approving officer:** {internal.approver_name}")
            st.markdown("**Approved application fields**")
            for name, value in internal.approved_fields_json.items():
                st.write(f"- {name.replace('_', ' ').capitalize()}: {value}")
            st.markdown("**Internal review notes**")
            if internal.review_notes:
                for note in internal.review_notes:
                    st.write(f"- {note}")
            else:
                st.caption("No internal review notes.")
            _render_officer_correction_form(
                service,
                token,
                record_id,
                internal.approved_fields_json,
                portal_key,
            )


def render_approved_registry(
    service: AuthService,
    token: str,
    portal_key: str,
) -> None:
    """Render registry search, filters, pagination, details, and audit history."""
    st.markdown('<div class="portal-eyebrow">Approved records</div>', unsafe_allow_html=True)

    record_id = st.query_params.get("registry_record")
    if record_id:
        _render_record_detail(service, token, record_id, portal_key)
        return

    st.title("Approved Land Registry")
    st.caption(
        "This shared registry contains only records approved by an authorized officer. "
        "Personal details and original documents are not published."
    )

    page_key = f"{portal_key}_registry_page"
    search_key = f"{portal_key}_registry_search"
    district_key = f"{portal_key}_registry_district"
    village_key = f"{portal_key}_registry_village"
    with st.expander("Search and filters", expanded=True):
        search = st.text_input(
            "Record ID or survey/plot number",
            placeholder="Search by public record ID or parcel number...",
            key=search_key,
            on_change=_reset_page,
            args=(page_key,),
        )
        district_column, village_column = st.columns(2)
        with district_column:
            district = st.text_input(
                "District",
                placeholder="Filter by district...",
                key=district_key,
                on_change=_reset_page,
                args=(page_key,),
            )
        with village_column:
            village = st.text_input(
                "Village",
                placeholder="Filter by village...",
                key=village_key,
                on_change=_reset_page,
                args=(page_key,),
            )

    page = max(0, int(st.session_state.get(page_key, 0)))
    try:
        records = service.list_approved_registry(
            token,
            search=search or None,
            district=district or None,
            village=village or None,
            limit=PAGE_SIZE + 1,
            offset=page * PAGE_SIZE,
        )
    except SQLAlchemyError:
        logger.exception("Database error searching the approved registry")
        st.error("The approved registry could not be loaded.")
        return

    has_next_page = len(records) > PAGE_SIZE
    records = records[:PAGE_SIZE]
    if not records:
        st.info("No approved records match the current search and filters.")
    else:
        for record in records:
            with st.container(border=True):
                record_column, approval_column, action_column = st.columns([2.5, 1.3, 1])
                with record_column:
                    st.markdown(f"**{record.public_record_id}**")
                    location = " · ".join(
                        value for value in (record.district, record.village) if value
                    )
                    if location:
                        st.caption(location)
                    if record.survey_or_plot_number:
                        st.caption(f"Survey/plot: {record.survey_or_plot_number}")
                with approval_column:
                    st.caption(f"Approved version {record.current_version}")
                    st.write(record.approved_at.strftime("%d %b %Y"))
                with action_column:
                    if st.button(
                        "View details",
                        key=f"{portal_key}_registry_open_{record.public_record_id}",
                    ):
                        st.query_params["registry_record"] = record.public_record_id
                        st.rerun()

    previous_column, page_column, next_column = st.columns([1, 2, 1])
    with previous_column:
        if page > 0 and st.button("Previous", key=f"{portal_key}_registry_previous"):
            st.session_state[page_key] = page - 1
            st.rerun()
    with page_column:
        st.caption(f"Page {page + 1}")
    with next_column:
        if has_next_page and st.button("Next", key=f"{portal_key}_registry_next"):
            st.session_state[page_key] = page + 1
            st.rerun()
