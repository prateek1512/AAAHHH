from __future__ import annotations

from modules.discrepancy import detect_discrepancies


def _fields(**updates: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "owner_name": "Synthetic Owner",
        "district": "Sample District",
        "village": "Sample Village",
        "survey_or_plot_number": "SY-100",
        "land_area": "2.5 acres",
        "document_type": "Sale deed",
        "document_date": "2024-01-15",
    }
    fields.update(updates)
    return fields


def _warnings_for(warnings: tuple[dict[str, object], ...], code: str):
    return [warning for warning in warnings if warning["code"] == code]


def test_missing_required_field_is_a_definite_warning_with_compared_value() -> None:
    warnings = detect_discrepancies(corrected_fields=_fields(village=""))

    warning = _warnings_for(warnings, "missing_required_field")[0]
    assert warning["severity"] == "definite"
    assert warning["fields_compared"] == ["village"]
    assert warning["compared_values"] == {"village": "Not provided"}
    assert "required" in warning["rule"]


def test_malformed_area_is_a_definite_format_error() -> None:
    warnings = detect_discrepancies(corrected_fields=_fields(land_area="about two acres"))

    warning = _warnings_for(warnings, "invalid_field_format")[0]
    assert warning["severity"] == "definite"
    assert warning["fields_compared"] == ["land_area"]
    assert warning["compared_values"] == {"land_area": "about two acres"}


def test_survey_mismatch_compares_same_location_and_area_reference() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(survey_or_plot_number="SY-101"),
        approved_records=(
            {
                "public_record_id": "PUB-SYNTHETIC-1",
                "fields": _fields(survey_or_plot_number="SY-100"),
            },
        ),
    )

    warning = _warnings_for(warnings, "approved_survey_mismatch")[0]
    assert warning["severity"] == "possible"
    assert set(warning["fields_compared"]) == {
        "district",
        "village",
        "land_area",
        "survey_or_plot_number",
    }
    assert warning["compared_values"]["Application survey_or_plot_number"] == "SY-101"
    assert warning["compared_values"]["Approved record survey_or_plot_number"] == "SY-100"
    assert "may be unrelated land" in warning["rule"]


def test_district_and_village_mismatches_use_matching_survey_number() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(district="Other District", village="Other Village"),
        approved_records=(
            {
                "public_record_id": "PUB-SYNTHETIC-2",
                "fields": _fields(),
            },
        ),
    )

    mismatches = _warnings_for(warnings, "approved_location_mismatch")
    assert {warning["fields_compared"][1] for warning in mismatches} == {
        "district",
        "village",
    }
    assert all(warning["severity"] == "possible" for warning in mismatches)


def test_same_approved_survey_and_location_flags_area_difference_and_possible_duplicate() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(land_area="3 acres"),
        approved_records=(
            {
                "public_record_id": "PUB-SYNTHETIC-3",
                "fields": _fields(),
            },
        ),
    )

    area_warning = _warnings_for(warnings, "approved_area_mismatch")[0]
    duplicate_warning = _warnings_for(
        warnings, "possible_duplicate_approved_record"
    )[0]
    assert area_warning["compared_values"]["Application land_area"] == "3 acres"
    assert area_warning["compared_values"]["Approved record land_area"] == "2.5 acres"
    assert duplicate_warning["compared_values"]["Approved public record ID"] == "PUB-SYNTHETIC-3"
    assert duplicate_warning["severity"] == "possible"


def test_same_survey_location_and_area_is_possible_approved_duplicate() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(),
        approved_records=(
            {"public_record_id": "PUB-SYNTHETIC-4", "fields": _fields()},
        ),
    )

    assert len(_warnings_for(warnings, "possible_duplicate_approved_record")) == 1


def test_possible_duplicate_application_uses_survey_and_location_not_owner_name() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(owner_name="Synthetic Applicant A"),
        other_applications=(
            {
                "status": "submitted",
                "fields": _fields(owner_name="Synthetic Applicant B"),
            },
        ),
    )

    warning = _warnings_for(warnings, "possible_duplicate_application")[0]
    assert warning["severity"] == "possible"
    assert warning["audience"] == "officer"
    assert set(warning["fields_compared"]) == {
        "survey_or_plot_number",
        "district",
        "village",
    }
    assert "owner_name" not in warning["compared_values"]


def test_same_owner_name_without_matching_parcel_fields_is_not_duplicate() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(owner_name="Same Synthetic Name"),
        other_applications=(
            {
                "status": "submitted",
                "fields": _fields(
                    owner_name="Same Synthetic Name",
                    survey_or_plot_number="DIFFERENT",
                ),
            },
        ),
    )

    assert not _warnings_for(warnings, "possible_duplicate_application")


def test_ocr_corrected_difference_is_explained_and_shows_both_values() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(survey_or_plot_number="SY-101"),
        extracted_fields={"survey_or_plot_number": "SY-100"},
    )

    warning = _warnings_for(warnings, "ocr_corrected_difference")[0]
    assert warning["fields_compared"] == ["survey_or_plot_number"]
    assert warning["compared_values"] == {
        "OCR extracted value": "SY-100",
        "Landowner-corrected value": "SY-101",
    }
    assert "legitimate correction" in warning["rule"]


def test_previous_version_difference_includes_prior_and_current_values() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(survey_or_plot_number="SY-101"),
        previous_fields=_fields(survey_or_plot_number="SY-100"),
    )

    warning = _warnings_for(warnings, "application_version_difference")[0]
    assert warning["fields_compared"] == ["survey_or_plot_number"]
    assert warning["compared_values"] == {
        "Previous version": "SY-100",
        "Current version": "SY-101",
    }


def test_missing_reference_is_reported_and_case_differences_are_normalized() -> None:
    unavailable = detect_discrepancies(corrected_fields=_fields())
    assert _warnings_for(unavailable, "reference_comparison_unavailable")

    equivalent = detect_discrepancies(
        corrected_fields=_fields(
            district="sample district",
            village="SAMPLE VILLAGE",
            survey_or_plot_number="sy-100",
        ),
        approved_records=({"fields": _fields()},),
    )
    assert not _warnings_for(equivalent, "approved_location_mismatch")
    assert not _warnings_for(equivalent, "approved_survey_mismatch")


def test_area_difference_for_matching_approved_record_is_possible_not_fraud_claim() -> None:
    warnings = detect_discrepancies(
        corrected_fields=_fields(land_area="3 acres"),
        approved_records=({"fields": _fields()},),
    )

    warning = _warnings_for(warnings, "approved_area_mismatch")[0]
    assert warning["severity"] == "possible"
    assert "fraud" not in warning["rule"].casefold()
