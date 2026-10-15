"""Validation for landowner-reviewed extracted field values."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime


FIELD_LABELS: dict[str, str] = {
    "owner_name": "Owner name",
    "district": "District",
    "circle_or_tehsil": "Circle or tehsil",
    "village": "Village",
    "survey_or_plot_number": "Survey or plot number",
    "land_area": "Land area and unit",
    "document_type": "Document type",
    "registration_or_document_number": "Registration or document number",
    "document_date": "Document date",
}

REQUIRED_FIELDS = (
    "owner_name",
    "district",
    "village",
    "survey_or_plot_number",
    "land_area",
    "document_type",
)

_AREA_PATTERN = re.compile(
    r"^\s*(?P<amount>\d+(?:\.\d+)?)\s+(?P<unit>"
    r"acres?|hectares?|ha|sq\.?\s*ft|sq\.?\s*m|square\s+feet|"
    r"square\s+meters?|square\s+metres?|cents?|bighas?|kanals?|marla?s?|"
    r"gunthas?|grounds?)\s*$",
    re.IGNORECASE,
)
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d")


def validate_corrected_fields(
    fields: Mapping[str, object],
) -> tuple[str, ...]:
    """Return field-specific errors; values are never normalized or unit-converted."""
    errors: list[str] = []
    unknown_fields = set(fields) - FIELD_LABELS.keys()
    for field_name in sorted(unknown_fields):
        errors.append(f"Unsupported field: {field_name}.")

    normalized_values: dict[str, str] = {}
    for field_name, label in FIELD_LABELS.items():
        value = fields.get(field_name)
        if value is None:
            normalized_values[field_name] = ""
            continue
        if not isinstance(value, str):
            errors.append(f"{label} must be entered as text.")
            normalized_values[field_name] = ""
            continue
        if len(value) > 500:
            errors.append(f"{label} must be 500 characters or fewer.")
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            errors.append(f"{label} contains unsupported control characters.")
        normalized_values[field_name] = value.strip()

    for field_name in REQUIRED_FIELDS:
        if not normalized_values[field_name]:
            errors.append(f"{FIELD_LABELS[field_name]} is required.")

    area = normalized_values["land_area"]
    if area:
        match = _AREA_PATTERN.fullmatch(area)
        if match is None:
            errors.append(
                "Land area must be a positive number followed by one explicit unit "
                "(for example, 2.5 acres or 120 sq ft). Do not use an ambiguous unit."
            )
        elif float(match.group("amount")) <= 0:
            errors.append("Land area must be greater than zero.")

    document_date = normalized_values["document_date"]
    if document_date:
        valid_date = False
        for date_format in _DATE_FORMATS:
            try:
                datetime.strptime(document_date, date_format)
            except ValueError:
                continue
            valid_date = True
            break
        if not valid_date:
            errors.append(
                "Document date must be a real date in DD/MM/YYYY or YYYY-MM-DD format."
            )

    return tuple(errors)
