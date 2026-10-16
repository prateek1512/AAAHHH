"""Deterministic, explainable discrepancy rules for application review."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from modules.field_validation import FIELD_LABELS, REQUIRED_FIELDS, validate_corrected_fields


def _normalized(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _display(value: object) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "Not provided"
    return str(value)


def _warning(
    code: str,
    severity: str,
    field_names: Sequence[str],
    rule: str,
    compared_values: Mapping[str, object],
    *,
    audience: str = "shared",
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "fields_compared": list(field_names),
        "rule": rule,
        "compared_values": {
            field: _display(value) for field, value in compared_values.items()
        },
        "audience": audience,
    }


def _comparison_key(fields: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        _normalized(fields.get("survey_or_plot_number")),
        _normalized(fields.get("district")),
        _normalized(fields.get("village")),
    )


def detect_discrepancies(
    *,
    corrected_fields: Mapping[str, object],
    extracted_fields: Mapping[str, object] | None = None,
    previous_fields: Mapping[str, object] | None = None,
    other_applications: Sequence[Mapping[str, object]] = (),
    approved_records: Sequence[Mapping[str, object]] = (),
) -> tuple[dict[str, object], ...]:
    """Apply explicit format and comparison rules; warnings never make decisions."""
    warnings: list[dict[str, object]] = []
    fields = dict(corrected_fields)

    validation_errors = validate_corrected_fields(fields)
    for field_name in REQUIRED_FIELDS:
        label = FIELD_LABELS[field_name]
        if not _normalized(fields.get(field_name)):
            warnings.append(
                _warning(
                    "missing_required_field",
                    "definite",
                    (field_name,),
                    f"{label} is required but has no value in this application.",
                    {field_name: fields.get(field_name)},
                )
            )
    for error in validation_errors:
        if " is required." in error:
            continue
        field_name = next(
            (
                name
                for name, label in FIELD_LABELS.items()
                if error.casefold().startswith(label.split(" and ")[0].casefold())
            ),
            None,
        )
        warnings.append(
            _warning(
                "invalid_field_format",
                "definite",
                (field_name,) if field_name else ("unsupported_field",),
                error,
                {field_name: fields.get(field_name)}
                if field_name
                else {"unsupported_field": error},
            )
        )

    extraction = extracted_fields or {}
    for error in validate_corrected_fields(extraction):
        if " is required." in error:
            continue
        field_name = next(
            (
                name
                for name, label in FIELD_LABELS.items()
                if error.casefold().startswith(label.split(" and ")[0].casefold())
            ),
            None,
        )
        warnings.append(
            _warning(
                "invalid_ocr_field_format",
                "definite",
                (field_name,) if field_name else ("unsupported_field",),
                f"The OCR-extracted value fails format validation: {error}",
                {field_name: extraction.get(field_name)}
                if field_name
                else {"unsupported_field": error},
            )
        )
    for field_name, ocr_value in extraction.items():
        if field_name not in FIELD_LABELS or not _normalized(ocr_value):
            continue
        corrected_value = fields.get(field_name)
        if _normalized(ocr_value) != _normalized(corrected_value):
            warnings.append(
                _warning(
                    "ocr_corrected_difference",
                    "possible",
                    (field_name,),
                    "The OCR-extracted value differs from the landowner-corrected value. "
                    "This can be a legitimate correction; check the source document.",
                    {
                        "OCR extracted value": ocr_value,
                        "Landowner-corrected value": corrected_value,
                    },
                )
            )

    if previous_fields is not None:
        for field_name in FIELD_LABELS:
            earlier = previous_fields.get(field_name)
            current = fields.get(field_name)
            if _normalized(earlier) != _normalized(current):
                warnings.append(
                    _warning(
                        "application_version_difference",
                        "possible",
                        (field_name,),
                        "This value differs from the preceding application version. "
                        "Changes can be legitimate; review the version history.",
                        {
                            "Previous version": earlier,
                            "Current version": current,
                        },
                    )
                )

    survey, district, village = _comparison_key(fields)
    area = _normalized(fields.get("land_area"))
    matching_approved: list[Mapping[str, object]] = []
    appropriate_reference_exists = False
    for record in approved_records:
        ref_fields = record.get("fields")
        if not isinstance(ref_fields, Mapping):
            continue
        ref_survey,         ref_district, ref_village = _comparison_key(ref_fields)
        ref_area = _normalized(ref_fields.get("land_area"))
        same_location = bool(district and village) and (
            district == ref_district and village == ref_village
        )
        same_survey = bool(survey) and survey == ref_survey
        same_location_area = same_location and bool(area) and area == ref_area
        if same_survey or same_location_area:
            appropriate_reference_exists = True
        if same_survey:
            for field_name, current, prior in (
                ("district", fields.get("district"), ref_fields.get("district")),
                ("village", fields.get("village"), ref_fields.get("village")),
            ):
                if _normalized(current) != _normalized(prior):
                    warnings.append(
                        _warning(
                            "approved_location_mismatch",
                            "possible",
                            ("survey_or_plot_number", field_name),
                            "The survey/plot number matches an approved record, but the "
                            "location differs. The same parcel number may be reused; verify "
                            "the source records.",
                            {
                                "survey_or_plot_number": fields.get("survey_or_plot_number"),
                                f"Application {field_name}": current,
                                f"Approved record {field_name}": prior,
                            },
                        )
                    )
            if same_location:
                matching_approved.append(record)
            prior_area = ref_fields.get("land_area")
            if same_location and _normalized(prior_area) != area:
                warnings.append(
                    _warning(
                        "approved_area_mismatch",
                        "possible",
                        ("survey_or_plot_number", "district", "village", "land_area"),
                        "The survey/plot number and location match an approved record, "
                        "but the stated area differs as written. Unit equivalence is not "
                        "inferred; confirm against authoritative records.",
                        {
                            "survey_or_plot_number": fields.get("survey_or_plot_number"),
                            "district": fields.get("district"),
                            "village": fields.get("village"),
                            "Application land_area": fields.get("land_area"),
                            "Approved record land_area": prior_area,
                        },
                    )
                )
        elif same_location_area and survey and ref_survey and survey != ref_survey:
            warnings.append(
                _warning(
                    "approved_survey_mismatch",
                    "possible",
                    ("district", "village", "land_area", "survey_or_plot_number"),
                    "An approved record has the same district, village, and stated area "
                    "but a different survey/plot number. This may be unrelated land; verify "
                    "the reference before drawing conclusions.",
                    {
                        "district": fields.get("district"),
                        "village": fields.get("village"),
                        "land_area": fields.get("land_area"),
                        "Application survey_or_plot_number": fields.get(
                            "survey_or_plot_number"
                        ),
                        "Approved record survey_or_plot_number": ref_fields.get(
                            "survey_or_plot_number"
                        ),
                    },
                )
            )

    if not appropriate_reference_exists:
        warnings.append(
            _warning(
                "reference_comparison_unavailable",
                "informational",
                ("survey_or_plot_number", "district", "village", "land_area"),
                "No approved reference record with a comparable survey/plot number or "
                "matching location and stated area is available. No reference comparison "
                "was made.",
                {
                    "survey_or_plot_number": fields.get("survey_or_plot_number"),
                    "district": fields.get("district"),
                    "village": fields.get("village"),
                    "land_area": fields.get("land_area"),
                },
            )
        )

    for record in matching_approved:
        ref_fields = record["fields"]
        warnings.append(
            _warning(
                "possible_duplicate_approved_record",
                "possible",
                ("survey_or_plot_number", "district", "village"),
                "An approved record has the same survey/plot number and location. This is "
                "a possible duplicate, not proof of ownership or wrongdoing.",
                {
                    "survey_or_plot_number": fields.get("survey_or_plot_number"),
                    "district": fields.get("district"),
                    "village": fields.get("village"),
                    "Approved public record ID": record.get("public_record_id"),
                },
            )
        )

    current_key = survey, district, village
    for application in other_applications:
        other_fields = application.get("fields")
        if not isinstance(other_fields, Mapping):
            continue
        other_key = _comparison_key(other_fields)
        if not all(current_key) or other_key != current_key:
            continue
        warnings.append(
            _warning(
                "possible_duplicate_application",
                "possible",
                ("survey_or_plot_number", "district", "village"),
                "Another active application has the same survey/plot number and location. "
                "Names are not used for this comparison; verify whether these are duplicate "
                "filings or legitimate records.",
                {
                    "Other application number": application.get("application_number"),
                    "survey_or_plot_number": fields.get("survey_or_plot_number"),
                    "district": fields.get("district"),
                    "village": fields.get("village"),
                    "Other application status": application.get("status"),
                    "Other application survey_or_plot_number": other_fields.get(
                        "survey_or_plot_number"
                    ),
                    "Other application district": other_fields.get("district"),
                    "Other application village": other_fields.get("village"),
                },
                audience="officer",
            )
        )

    return tuple(warnings)
