import logging

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from modules.auth import AuthService
from modules.auth_ui import get_auth_service
from modules.field_validation import FIELD_LABELS
from modules.parcel_map import render_parcel_map

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Public Land Record Verification",
    page_icon=":material/verified:",
    layout="wide",
)

service: AuthService = get_auth_service()
st.title("Public Land Record Verification")
st.write(
    "Look up an approved record by its public record ID. No account is required."
)
st.info(
    "Record and audit integrity checks are not legal ownership verification. "
    "A SHA-256 hash chain can indicate whether stored events are internally "
    "consistent; it does not establish ownership or prove the underlying facts."
)

with st.form("public_record_lookup"):
    public_record_id = st.text_input(
        "Public record ID",
        value=st.query_params.get("record_id", ""),
        placeholder="Enter the public record ID",
    )
    lookup_submitted = st.form_submit_button("Open approved record", type="primary")

if lookup_submitted:
    cleaned_id = public_record_id.strip()
    if cleaned_id:
        st.query_params["record_id"] = cleaned_id
        st.session_state["public_verification_record_id"] = cleaned_id
        st.session_state.pop("public_verification_result", None)
        st.rerun()
    st.error("Enter a public record ID.")

record_id = st.session_state.get(
    "public_verification_record_id",
    st.query_params.get("record_id", ""),
)
if not record_id:
    st.caption("Only approved records are available through this page.")
    st.stop()

try:
    record = service.get_public_approved_record(record_id)
except SQLAlchemyError:
    logger.exception("Database error loading public record %s", record_id)
    st.error("The approved record could not be loaded.")
    st.stop()

if record is None:
    st.warning("No publicly available approved record matches that ID.")
    st.stop()

st.divider()
st.subheader(record.public_record_id)
st.metric("Current approved version", record.current_version)
st.caption("Current approved-record SHA-256 hash")
st.code(record.current_version_hash, language=None)

st.subheader("Publicly released record fields")
columns = st.columns(2)
for index, field_name in enumerate(
    ("district", "village", "survey_or_plot_number", "land_area")
):
    with columns[index % 2]:
        st.caption(FIELD_LABELS[field_name])
        st.write(record.approved_fields.get(field_name) or "Not available")

st.subheader("Parcel map")
render_parcel_map(
    record.parcel_geojson,
    record_identifier=record.public_record_id,
    attributes=record.approved_fields,
    key=f"public_{record.public_record_id}",
    validation_status=record.parcel_geometry_validation_status,
)

st.subheader("Approval and version history")
for version in record.versions:
    with st.container(border=True):
        st.markdown(f"**Approved version {version.version_number}**")
        st.caption(version.authorized_at.strftime("%d %b %Y, %H:%M UTC"))
        st.caption("Version SHA-256 hash")
        st.code(version.version_hash, language=None)
        if version.version_number != record.current_version:
            st.caption("Earlier approved version")

st.markdown("[Go to public audit integrity check](#audit-verification)")
st.subheader("Audit verification", anchor="audit-verification")
if st.button("Verify Audit Integrity", type="primary"):
    try:
        result = service.verify_public_record_audit_integrity(record_id)
    except SQLAlchemyError:
        logger.exception("Database error verifying public record audit integrity %s", record_id)
        st.error("Audit integrity could not be checked because of a database error.")
    else:
        if result is None:
            st.warning("The approved record is no longer publicly available.")
            st.session_state.pop("public_verification_result", None)
        else:
            st.session_state["public_verification_result"] = (record_id, result)

verification_state = st.session_state.get("public_verification_result")
if verification_state is not None and verification_state[0] == record_id:
    result = verification_state[1]
    if result.passed:
        st.success(
            "Audit integrity check passed: the stored events are consistent with "
            "the current hash chain."
        )
    else:
        st.warning(
            "Audit history is inconsistent. One or more audit-chain or approved-version "
            "integrity checks failed."
        )
        for issue in result.errors:
            st.write(f"- {issue}")

st.caption(
    "Integrity is distinct from legal ownership verification. A consistent SHA-256 "
    "hash chain does not establish legal ownership, prove that record contents are "
    "factually correct, or guarantee that a record cannot be altered."
)
