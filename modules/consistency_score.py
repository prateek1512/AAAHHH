"""Explainable preliminary consistency indicator for officer prioritization."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence

from modules.field_validation import REQUIRED_FIELDS

DEFAULT_WEIGHTS: dict[str, float] = {
    "ocr_confidence": 0.25,
    "required_completeness": 0.25,
    "reference_consistency": 0.30,
    "audit_integrity": 0.20,
}
WEIGHTS_ENV = "RECORD_CONSISTENCY_WEIGHTS"
REFERENCE_WARNING_CODES = frozenset(
    {
        "approved_survey_mismatch",
        "approved_location_mismatch",
        "approved_area_mismatch",
        "possible_duplicate_approved_record",
        "possible_duplicate_application",
    }
)


def get_consistency_weights(
    environment: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """Read normalized relative component weights from JSON environment config."""
    values = os.environ if environment is None else environment
    raw = values.get(WEIGHTS_ENV)
    if raw is None:
        return dict(DEFAULT_WEIGHTS)
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{WEIGHTS_ENV} must be a JSON object of component weights.") from error
    if not isinstance(configured, dict) or set(configured) != set(DEFAULT_WEIGHTS):
        expected = ", ".join(sorted(DEFAULT_WEIGHTS))
        raise ValueError(f"{WEIGHTS_ENV} must define exactly these components: {expected}.")
    weights: dict[str, float] = {}
    for component, value in configured.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{WEIGHTS_ENV} values must be finite non-negative numbers.")
        weights[component] = float(value)
    if not any(weights.values()):
        raise ValueError(f"At least one {WEIGHTS_ENV} weight must be greater than zero.")
    return weights


def _warning_effect(warning: Mapping[str, object]) -> dict[str, object]:
    fields = warning.get("fields_compared")
    if not isinstance(fields, list):
        field_name = warning.get("field")
        fields = [field_name] if isinstance(field_name, str) else []
    return {
        "code": warning.get("code", "unspecified"),
        "fields_compared": fields,
        "explanation": warning.get("rule", warning.get("message", "")),
        "compared_values": dict(warning.get("compared_values", {}))
        if isinstance(warning.get("compared_values", {}), Mapping)
        else {},
    }


def calculate_consistency_score(
    *,
    corrected_fields: Mapping[str, object],
    ocr_confidence: Mapping[str, object] | None,
    field_warnings: Sequence[Mapping[str, object]] = (),
    discrepancy_warnings: Sequence[Mapping[str, object]] = (),
    reference_available: bool,
    audit_check_available: bool,
    audit_integrity_passed: bool,
    audit_integrity_explanation: str | None = None,
    weights: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Calculate a weighted 0-100 indicator, renormalizing over available inputs.

    Formula: sum(component_score * configured_weight) / sum(configured_weight)
    across available components only. Unavailable inputs are excluded, never
    treated as successful matches. This is a review aid, not a legal score.
    """
    configured_weights = dict(
        get_consistency_weights() if weights is None else weights
    )
    if set(configured_weights) != set(DEFAULT_WEIGHTS):
        raise ValueError("Weights must define every supported consistency component.")
    for component, weight in configured_weights.items():
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
        ):
            raise ValueError("Consistency weights must be finite non-negative numbers.")
    if not any(configured_weights.values()):
        raise ValueError("At least one consistency weight must be greater than zero.")

    ocr_values = [
        float(value)
        for value in (ocr_confidence or {}).values()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 100
    ]
    ocr_warnings = [
        warning
        for warning in field_warnings
        if warning.get("code") in {"low_ocr_confidence", "invalid_ocr_value"}
    ]
    completeness_warnings = [
        warning
        for warning in discrepancy_warnings
        if warning.get("code") == "missing_required_field"
    ]
    completeness_warnings.extend(
        warning
        for warning in field_warnings
        if warning.get("code") == "missing_required"
        and warning.get("source") in {"ocr", "reviewed"}
    )
    reference_warnings = [
        warning
        for warning in discrepancy_warnings
        if warning.get("code") in REFERENCE_WARNING_CODES
    ]
    reference_unavailable_warnings = [
        warning
        for warning in discrepancy_warnings
        if warning.get("code") == "reference_comparison_unavailable"
    ]
    audit_warnings = (
        [
            {
                "code": "audit_integrity_failed",
                "fields_compared": [],
                "rule": audit_integrity_explanation
                or "Required audit events were missing or the stored chain did not verify.",
                "compared_values": {},
            }
        ]
        if audit_check_available and not audit_integrity_passed
        else []
    )

    present_required = sum(
        isinstance(corrected_fields.get(field), str)
        and bool(corrected_fields[field].strip())
        for field in REQUIRED_FIELDS
    )
    completeness_score = 100.0 * present_required / len(REQUIRED_FIELDS)
    reference_score = (
        max(0.0, 100.0 - 20.0 * len(reference_warnings))
        if reference_available
        else None
    )
    audit_score = (
        100.0 if audit_integrity_passed else 0.0
    ) if audit_check_available else None

    component_values: dict[str, tuple[float | None, str, list[dict[str, object]]]] = {
        "ocr_confidence": (
            sum(ocr_values) / len(ocr_values) if ocr_values else None,
            (
                f"Arithmetic mean of {len(ocr_values)} valid OCR engine confidence "
                "values (0-100); this measures text recognition only."
                if ocr_values
                else "Unavailable: no valid OCR engine confidence values were supplied."
            ),
            [_warning_effect(warning) for warning in ocr_warnings],
        ),
        "required_completeness": (
            completeness_score,
            f"{present_required} of {len(REQUIRED_FIELDS)} required fields are non-empty.",
            [_warning_effect(warning) for warning in completeness_warnings],
        ),
        "reference_consistency": (
            reference_score,
            (
                f"Starts at 100; subtracts 20 points per applicable approved-record "
                f"or active-application discrepancy warning ({len(reference_warnings)} "
                "warning(s)); minimum 0. This is not an ownership test."
                if reference_available
                else "Unavailable: no suitable comparison reference record was found."
            ),
            [
                _warning_effect(warning)
                for warning in (*reference_warnings, *reference_unavailable_warnings)
            ],
        ),
        "audit_integrity": (
            audit_score,
            (
                "100 when required application audit events are present and the full "
                "stored hash chain verifies; otherwise 0."
                if audit_check_available and audit_integrity_passed
                else "0 because a required application audit event is missing or the "
                "stored hash chain did not verify."
                if audit_check_available
                else "Unavailable: audit integrity was not checked."
            ),
            [_warning_effect(warning) for warning in audit_warnings],
        ),
    }

    available_weight = sum(
        configured_weights[name]
        for name, (score, _, _) in component_values.items()
        if score is not None
    )
    total = (
        sum(
            score * configured_weights[name]
            for name, (score, _, _) in component_values.items()
            if score is not None
        )
        / available_weight
        if available_weight > 0
        else None
    )
    components = {
        name: {
            "score": round(score, 1) if score is not None else None,
            "configured_weight": configured_weights[name],
            "effective_weight_percent": (
                round(configured_weights[name] / available_weight * 100, 1)
                if score is not None and available_weight > 0
                else None
            ),
            "available": score is not None,
            "explanation": explanation,
            "warnings_affecting_result": affected_warnings,
        }
        for name, (score, explanation, affected_warnings) in component_values.items()
    }
    return {
        "label": "Preliminary consistency indicator",
        "score": round(total, 1) if total is not None else None,
        "scale": "0-100; higher means fewer inconsistencies in available checks",
        "formula": (
            "Weighted mean of available component scores only; unavailable components "
            "are excluded and remaining weights are renormalized."
        ),
        "components": components,
        "warning": (
            "This indicator is for manual-review prioritization only. It is not a legal "
            "ownership score, does not establish factual correctness, and must not be used "
            "as the sole basis for approval or rejection."
        ),
    }
