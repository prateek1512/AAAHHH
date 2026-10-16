"""Validation for landowner-reviewed extracted field values."""

from __future__ import annotations

import math
import os
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
DEFAULT_OCR_LOW_CONFIDENCE_THRESHOLD = 60.0


def get_ocr_low_confidence_threshold() -> float:
    """Read the configurable OCR review threshold, constrained to the engine's scale."""
    configured = os.environ.get(
        "OCR_LOW_CONFIDENCE_THRESHOLD",
        str(DEFAULT_OCR_LOW_CONFIDENCE_THRESHOLD),
    )
    try:
        threshold = float(configured)
    except ValueError as error:
        raise ValueError("OCR_LOW_CONFIDENCE_THRESHOLD must be a number from 0 to 100.") from error
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("OCR_LOW_CONFIDENCE_THRESHOLD must be a number from 0 to 100.")
    return threshold


def _field_value_errors(field_name: str, value: object) -> tuple[str, ...]:
    label = FIELD_LABELS[field_name]
    if value is None:
        return ()
    if not isinstance(value, str):
        return (f"{label} must be entered as text.",)

    errors: list[str] = []
    if len(value) > 500:
        errors.append(f"{label} must be 500 characters or fewer.")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        errors.append(f"{label} contains unsupported control characters.")
    normalized = value.strip()
    if field_name == "land_area" and normalized:
        match = _AREA_PATTERN.fullmatch(normalized)
        if match is None:
            errors.append(
                "Land area must be a positive number followed by one explicit unit "
                "(for example, 2.5 acres or 120 sq ft). Do not use an ambiguous unit."
            )
        elif float(match.group("amount")) <= 0:
            errors.append("Land area must be greater than zero.")
    if field_name == "document_date" and normalized:
        valid_date = False
        for date_format in _DATE_FORMATS:
            try:
                datetime.strptime(normalized, date_format)
            except ValueError:
                continue
            valid_date = True
            break
        if not valid_date:
            errors.append(
                "Document date must be a real date in DD/MM/YYYY or YYYY-MM-DD format."
            )
    return tuple(errors)


def validate_corrected_fields(
    fields: Mapping[str, object],
) -> tuple[str, ...]:
    """Return field-specific errors; values are never normalized or unit-converted."""
    errors: list[str] = []
    for field_name in sorted(set(fields) - FIELD_LABELS.keys()):
        errors.append(f"Unsupported field: {field_name}.")

    for field_name, label in FIELD_LABELS.items():
        value = fields.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if field_name in REQUIRED_FIELDS:
                errors.append(f"{label} is required.")
            continue
        errors.extend(_field_value_errors(field_name, value))
    return tuple(errors)


def build_field_warnings(
    extracted_fields_json: Mapping[str, object],
    corrected_fields: Mapping[str, object],
    *,
    confidence_threshold: float | None = None,
) -> list[dict[str, str | float]]:
    """Create reproducible field warnings from raw OCR and the reviewed snapshot."""
    threshold = (
        get_ocr_low_confidence_threshold()
        if confidence_threshold is None
        else confidence_threshold
    )
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("OCR confidence threshold must be between 0 and 100.")

    raw_fields = extracted_fields_json.get("fields", {})
    raw_confidence = extracted_fields_json.get("field_confidence", {})
    extracted = raw_fields if isinstance(raw_fields, Mapping) else {}
    confidence = raw_confidence if isinstance(raw_confidence, Mapping) else {}
    warnings: list[dict[str, str | float]] = []

    def add_warning(
        code: str, field_name: str, source: str, message: str
    ) -> None:
        warnings.append(
            {
                "code": code,
                "field": field_name,
                "source": source,
                "message": message,
            }
        )

    for field_name in FIELD_LABELS:
        ocr_value = extracted.get(field_name)
        if ocr_value is None or (isinstance(ocr_value, str) and not ocr_value.strip()):
            if field_name in REQUIRED_FIELDS:
                add_warning(
                    "missing_required",
                    field_name,
                    "ocr",
                    "OCR did not identify this required field; it must be entered and "
                    "checked against the original document.",
                )
        else:
            for message in _field_value_errors(field_name, ocr_value):
                add_warning("invalid_ocr_value", field_name, "ocr", message)

        field_confidence = confidence.get(field_name)
        if (
            isinstance(field_confidence, (int, float))
            and not isinstance(field_confidence, bool)
            and math.isfinite(field_confidence)
            and 0 <= field_confidence < threshold
        ):
            add_warning(
                "low_ocr_confidence",
                field_name,
                "ocr",
                f"OCR engine confidence is {field_confidence:g}%, below the configured "
                f"{threshold:g}% manual-review threshold. Verify the text against the "
                "original; this score does not measure field correctness.",
            )

        corrected_value = corrected_fields.get(field_name)
        if corrected_value is None or (
            isinstance(corrected_value, str) and not corrected_value.strip()
        ):
            if field_name in REQUIRED_FIELDS:
                add_warning(
                    "missing_required",
                    field_name,
                    "reviewed",
                    "This required field has not been supplied in the landowner-reviewed values.",
                )
        else:
            for message in _field_value_errors(field_name, corrected_value):
                add_warning("invalid_reviewed_value", field_name, "reviewed", message)

    return warnings


def snapshot_field_warnings(
    extracted_fields_json: Mapping[str, object],
    corrected_fields: Mapping[str, object],
) -> dict[str, object]:
    """Copy an OCR snapshot and persist warnings plus their threshold per application version."""
    threshold = get_ocr_low_confidence_threshold()
    snapshot = dict(extracted_fields_json)
    snapshot["field_warnings"] = build_field_warnings(
        snapshot,
        corrected_fields,
        confidence_threshold=threshold,
    )
    snapshot["warning_threshold"] = threshold
    return snapshot
