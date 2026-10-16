from __future__ import annotations

import pytest

from modules.field_validation import (
    build_field_warnings,
    snapshot_field_warnings,
    validate_corrected_fields,
)


def _required_fields(**updates: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "owner_name": "Synthetic Owner",
        "district": "Sample District",
        "village": "Sample Village",
        "survey_or_plot_number": "SY-123",
        "land_area": "2.5 acres",
        "document_type": "Sale deed",
    }
    fields.update(updates)
    return fields


def test_required_fields_must_be_present() -> None:
    fields = _required_fields(owner_name="", village=None)

    errors = validate_corrected_fields(fields)

    assert any("Owner name is required" in error for error in errors)
    assert any("Village is required" in error for error in errors)


@pytest.mark.parametrize(
    "area",
    ["0 acres", "-1 acres", "1", "two acres", "2 unknown-units", "1/2 acres"],
)
def test_area_requires_a_positive_number_and_explicit_unit(area: str) -> None:
    errors = validate_corrected_fields(_required_fields(land_area=area))

    assert any("Land area" in error for error in errors)


@pytest.mark.parametrize("area", ["0.5 hectares", "120 sq ft", "2 bighas"])
def test_area_accepts_positive_explicit_units_without_conversion(area: str) -> None:
    errors = validate_corrected_fields(_required_fields(land_area=area))

    assert errors == ()


def test_invalid_date_and_non_text_values_are_rejected() -> None:
    errors = validate_corrected_fields(
        _required_fields(document_date="31/02/2024", district=123)
    )

    assert any("date" in error.lower() for error in errors)
    assert any("District must be entered as text" in error for error in errors)


def test_high_ocr_confidence_is_reported_without_a_low_confidence_warning() -> None:
    warnings = build_field_warnings(
        {
            "fields": {"owner_name": "Synthetic Owner"},
            "field_confidence": {"owner_name": 96.5},
        },
        {},
    )

    assert not any(warning["code"] == "low_ocr_confidence" for warning in warnings)


def test_low_confidence_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_LOW_CONFIDENCE_THRESHOLD", "85")
    warnings = build_field_warnings(
        {
            "fields": {"owner_name": "Synthetic Owner"},
            "field_confidence": {"owner_name": 84.9},
        },
        {},
    )

    low_confidence_warning = next(
        warning for warning in warnings if warning["code"] == "low_ocr_confidence"
    )
    assert low_confidence_warning["field"] == "owner_name"
    assert "84.9%" in str(low_confidence_warning["message"])
    assert "85%" in str(low_confidence_warning["message"])
    assert "does not measure field correctness" in str(low_confidence_warning["message"])


def test_missing_required_ocr_fields_have_field_level_explanations() -> None:
    warnings = build_field_warnings({"fields": {}, "field_confidence": {}}, {})

    missing_fields = {
        warning["field"]
        for warning in warnings
        if warning["code"] == "missing_required" and warning["source"] == "ocr"
    }
    assert {
        "owner_name",
        "district",
        "village",
        "survey_or_plot_number",
        "land_area",
        "document_type",
    } <= missing_fields


def test_malformed_ocr_fields_are_flagged_and_warning_snapshot_keeps_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_LOW_CONFIDENCE_THRESHOLD", "72")
    snapshot = snapshot_field_warnings(
        {
            "ocr_text": "Land area: an unknown amount",
            "fields": {"land_area": "an unknown amount"},
            "field_confidence": {"land_area": 92},
        },
        {},
    )

    malformed_warning = next(
        warning
        for warning in snapshot["field_warnings"]
        if warning["code"] == "invalid_ocr_value"
    )
    assert malformed_warning["field"] == "land_area"
    assert snapshot["warning_threshold"] == 72
    assert snapshot["ocr_text"] == "Land area: an unknown amount"
