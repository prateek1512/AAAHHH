"""Conservative extraction of labeled land-record fields from OCR lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence


FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "owner_name": (r"owner(?:'s)? name", r"name of owner", r"holder name"),
    "district": (r"district", r"zilla"),
    "circle_or_tehsil": (r"circle", r"tehsil", r"taluka", r"taluk"),
    "village": (r"village", r"mouza", r"gram"),
    "survey_or_plot_number": (
        r"survey(?:\s*(?:no|number))?",
        r"plot(?:\s*(?:no|number))?",
        r"khasra(?:\s*(?:no|number))?",
        r"dag(?:\s*(?:no|number))?",
    ),
    "land_area": (r"land area", r"area", r"extent"),
    "registration_or_document_number": (
        r"registration(?:\s*(?:no|number))?",
        r"document(?:\s*(?:no|number))?",
        r"deed(?:\s*(?:no|number))?",
        r"record(?:\s*(?:no|number))?",
    ),
    "document_date": (
        r"document date",
        r"registration date",
        r"date of registration",
        r"date",
    ),
}

_DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"
)
_AREA_PATTERN = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:acres?|hectares?|hect(?:ares?)?|"
    r"sq\.?\s*(?:ft|m|met(?:er|re)s?)|square\s+(?:feet|meters?|metres?)|"
    r"cents?|bighas?|kanals?|marla?s?)\b",
    re.IGNORECASE,
)
_REGISTRATION_NUMBER_PATTERN = re.compile(
    r"\b(?:[A-Z0-9][A-Z0-9/-]*\d[A-Z0-9/-]*|\d[A-Z0-9/-]*)\b",
    re.IGNORECASE,
)
_DOCUMENT_TYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sale deed", re.compile(r"\bsale\s+deed\b", re.IGNORECASE)),
    ("title deed", re.compile(r"\btitle\s+deed\b", re.IGNORECASE)),
    ("lease deed", re.compile(r"\blease\s+deed\b", re.IGNORECASE)),
    ("property card", re.compile(r"\bproperty\s+card\b", re.IGNORECASE)),
    ("land record extract", re.compile(r"\b(?:land\s+record|record\s+of\s+rights)\b", re.IGNORECASE)),
    ("mutation record", re.compile(r"\bmutation\b", re.IGNORECASE)),
)


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class FieldExtraction:
    fields: dict[str, str | None]
    confidence: dict[str, float | None]
    warnings: tuple[str, ...]


def _compile_label_pattern(labels: Sequence[str]) -> re.Pattern[str]:
    label_pattern = "|".join(f"(?:{label})" for label in labels)
    return re.compile(
        rf"^\s*(?:{label_pattern})\s*(?:[:#=-]\s*|\s+)(?P<value>.*?)\s*$",
        re.IGNORECASE,
    )


def _clean_value(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip(" \t:;,#-")
    return cleaned or None


def _extract_labeled_value(
    lines: Sequence[OCRLine], labels: Sequence[str]
) -> tuple[str | None, float | None]:
    pattern = _compile_label_pattern(labels)
    for line in lines:
        match = pattern.match(line.text)
        if match is None:
            continue
        value = _clean_value(match.group("value"))
        if value:
            confidence = line.confidence
            if confidence is not None and 0 <= confidence <= 100:
                return value, round(confidence, 1)
            return value, None
    return None, None


def extract_fields(lines: Sequence[OCRLine]) -> FieldExtraction:
    """Extract only explicitly labeled candidates; missing fields remain None."""
    fields: dict[str, str | None] = {}
    confidence: dict[str, float | None] = {}
    for field_name, labels in FIELD_LABELS.items():
        fields[field_name], confidence[field_name] = _extract_labeled_value(lines, labels)

    if fields["land_area"] is not None:
        area_match = _AREA_PATTERN.search(fields["land_area"] or "")
        if area_match:
            fields["land_area"] = area_match.group(0)
        else:
            fields["land_area"] = None
            confidence["land_area"] = None

    if fields["registration_or_document_number"] is not None:
        number_match = _REGISTRATION_NUMBER_PATTERN.search(
            fields["registration_or_document_number"] or ""
        )
        if number_match:
            fields["registration_or_document_number"] = number_match.group(0)
        else:
            fields["registration_or_document_number"] = None
            confidence["registration_or_document_number"] = None

    if fields["document_date"] is not None:
        date_match = _DATE_PATTERN.search(fields["document_date"] or "")
        if date_match:
            fields["document_date"] = date_match.group(0)
        else:
            fields["document_date"] = None
            confidence["document_date"] = None

    combined_text = "\n".join(line.text for line in lines)
    for document_type, pattern in _DOCUMENT_TYPES:
        match = pattern.search(combined_text)
        if match:
            fields["document_type"] = document_type
            candidate_lines = [
                line.confidence
                for line in lines
                if pattern.search(line.text) and line.confidence is not None
            ]
            confidence["document_type"] = (
                round(sum(candidate_lines) / len(candidate_lines), 1)
                if candidate_lines
                else None
            )
            break
    else:
        fields["document_type"] = None
        confidence["document_type"] = None

    warnings: list[str] = []
    if not any(fields.values()):
        warnings.append(
            "No confidently labeled fields were recognized. Review the original document "
            "and enter fields manually."
        )
    if any(value is not None for value in confidence.values()):
        warnings.append(
            "Confidence values estimate OCR text recognition only; they do not indicate "
            "whether a legal field is accurate."
        )
    return FieldExtraction(
        fields=fields,
        confidence=confidence,
        warnings=tuple(warnings),
    )


def as_json_value(extraction: FieldExtraction) -> dict[str, object]:
    """Serialize extracted fields without including any owner-corrected values."""
    return {
        "fields": dict(extraction.fields),
        "field_confidence": dict(extraction.confidence),
        "warnings": list(extraction.warnings),
    }


def fields_from_json(value: Mapping[str, object]) -> dict[str, str | None]:
    """Return only the fields mapping from stored OCR JSON."""
    stored_fields = value.get("fields")
    if not isinstance(stored_fields, dict):
        return {}
    return {
        str(key): item if isinstance(item, str) else None
        for key, item in stored_fields.items()
    }
