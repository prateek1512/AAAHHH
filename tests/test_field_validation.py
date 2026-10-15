from __future__ import annotations

import pytest

from modules.field_validation import validate_corrected_fields


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
