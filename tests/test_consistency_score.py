from __future__ import annotations

import json

import pytest

from modules.consistency_score import (
    DEFAULT_WEIGHTS,
    WEIGHTS_ENV,
    calculate_consistency_score,
    get_consistency_weights,
)


def _fields(**updates: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "owner_name": "Synthetic Owner",
        "district": "Sample District",
        "village": "Sample Village",
        "survey_or_plot_number": "SY-101",
        "land_area": "2.5 acres",
        "document_type": "Sale deed",
    }
    fields.update(updates)
    return fields


def test_missing_ocr_and_reference_inputs_are_unavailable_not_successful() -> None:
    result = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence={},
        reference_available=False,
        audit_check_available=False,
        audit_integrity_passed=False,
    )

    assert result["label"] == "Preliminary consistency indicator"
    assert result["score"] == 100
    components = result["components"]
    assert components["ocr_confidence"]["score"] is None
    assert components["ocr_confidence"]["available"] is False
    assert components["reference_consistency"]["score"] is None
    assert components["reference_consistency"]["available"] is False
    assert components["audit_integrity"]["score"] is None
    assert components["required_completeness"]["score"] == 100
    assert components["required_completeness"]["effective_weight_percent"] == 100


def test_all_component_boundaries_are_normalized_to_zero_to_one_hundred() -> None:
    result = calculate_consistency_score(
        corrected_fields={},
        ocr_confidence={"field_a": 0, "field_b": 0},
        reference_available=True,
        audit_check_available=True,
        audit_integrity_passed=False,
    )

    components = result["components"]
    assert components["ocr_confidence"]["score"] == 0
    assert components["required_completeness"]["score"] == 0
    assert components["reference_consistency"]["score"] == 100
    assert components["audit_integrity"]["score"] == 0
    assert result["score"] == 30


def test_reference_warnings_reduce_score_and_are_explained() -> None:
    warnings = [
        {
            "code": "approved_area_mismatch",
            "severity": "possible",
            "fields_compared": ["survey_or_plot_number", "land_area"],
            "rule": "Synthetic area differs from approved record.",
            "compared_values": {"Application land_area": "3 acres"},
        },
        {
            "code": "possible_duplicate_application",
            "severity": "possible",
            "fields_compared": ["survey_or_plot_number", "district", "village"],
            "rule": "Synthetic active application matches.",
            "compared_values": {"Other application number": "LR-SYNTHETIC"},
        },
    ]
    result = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence={"owner_name": 80},
        discrepancy_warnings=warnings,
        reference_available=True,
        audit_check_available=True,
        audit_integrity_passed=True,
    )

    component = result["components"]["reference_consistency"]
    assert component["score"] == 60
    assert len(component["warnings_affecting_result"]) == 2
    assert component["warnings_affecting_result"][0]["compared_values"] == {
        "Application land_area": "3 acres"
    }


def test_low_confidence_warning_is_linked_to_ocr_score() -> None:
    result = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence={"survey_or_plot_number": 40},
        field_warnings=(
            {
                "code": "low_ocr_confidence",
                "field": "survey_or_plot_number",
                "message": "OCR confidence is below the review threshold.",
            },
        ),
        reference_available=False,
        audit_check_available=False,
        audit_integrity_passed=False,
    )

    affected = result["components"]["ocr_confidence"]["warnings_affecting_result"]
    assert affected[0]["fields_compared"] == ["survey_or_plot_number"]
    assert "review threshold" in affected[0]["explanation"]


def test_available_component_weights_renormalize_when_reference_is_unavailable() -> None:
    result = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence={"owner_name": 80},
        reference_available=False,
        audit_check_available=True,
        audit_integrity_passed=False,
    )

    assert result["components"]["reference_consistency"]["score"] is None
    assert result["score"] == pytest.approx(round((80 * 0.25 + 100 * 0.25) / 0.7, 1))
    effective_weights = [
        component["effective_weight_percent"]
        for component in result["components"].values()
        if component["available"]
    ]
    assert sum(effective_weights) == pytest.approx(100)


def test_invalid_confidence_values_are_ignored_and_missing_component_reported() -> None:
    result = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence={
            "valid": 75,
            "negative": -1,
            "too_high": 101,
            "boolean": True,
            "nan": float("nan"),
        },
        reference_available=False,
        audit_check_available=False,
        audit_integrity_passed=False,
    )

    assert result["components"]["ocr_confidence"]["score"] == 75


def test_required_field_completeness_has_exact_zero_and_full_boundaries() -> None:
    none_present = calculate_consistency_score(
        corrected_fields={},
        ocr_confidence=None,
        reference_available=False,
        audit_check_available=False,
        audit_integrity_passed=False,
    )
    complete = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence=None,
        reference_available=False,
        audit_check_available=False,
        audit_integrity_passed=False,
    )

    assert none_present["components"]["required_completeness"]["score"] == 0
    assert complete["components"]["required_completeness"]["score"] == 100


def test_configurable_weights_are_loaded_and_invalid_configuration_rejected() -> None:
    configured = {**DEFAULT_WEIGHTS, "ocr_confidence": 0.4}
    weights = get_consistency_weights({WEIGHTS_ENV: json.dumps(configured)})
    assert weights["ocr_confidence"] == 0.4

    with pytest.raises(ValueError, match="exactly these components"):
        get_consistency_weights({WEIGHTS_ENV: '{"ocr_confidence": 1}'})
    with pytest.raises(ValueError, match="finite non-negative"):
        get_consistency_weights(
            {WEIGHTS_ENV: json.dumps({**DEFAULT_WEIGHTS, "audit_integrity": -1})}
        )


def test_zero_weight_components_do_not_affect_score() -> None:
    weights = {
        "ocr_confidence": 1,
        "required_completeness": 0,
        "reference_consistency": 0,
        "audit_integrity": 0,
    }
    result = calculate_consistency_score(
        corrected_fields={},
        ocr_confidence={"field": 35},
        reference_available=False,
        audit_check_available=False,
        audit_integrity_passed=False,
        weights=weights,
    )

    assert result["score"] == 35
    assert result["components"]["required_completeness"]["effective_weight_percent"] == 0


def test_reference_unavailability_and_audit_failure_are_shown_as_score_warnings() -> None:
    result = calculate_consistency_score(
        corrected_fields=_fields(),
        ocr_confidence=None,
        discrepancy_warnings=(
            {
                "code": "reference_comparison_unavailable",
                "fields_compared": ["survey_or_plot_number"],
                "rule": "No suitable synthetic reference.",
                "compared_values": {"survey_or_plot_number": "SY-101"},
            },
        ),
        reference_available=False,
        audit_check_available=True,
        audit_integrity_passed=False,
        audit_integrity_explanation="Synthetic audit mismatch.",
    )

    ref_component = result["components"]["reference_consistency"]
    audit_component = result["components"]["audit_integrity"]
    assert ref_component["score"] is None
    assert ref_component["warnings_affecting_result"][0]["code"] == (
        "reference_comparison_unavailable"
    )
    assert audit_component["score"] == 0
    assert audit_component["warnings_affecting_result"][0]["explanation"] == (
        "Synthetic audit mismatch."
    )
