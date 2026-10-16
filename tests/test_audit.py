from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from modules.audit import (
    GENESIS_HASH,
    calculate_event_hash,
    canonical_json,
    verify_audit_chain,
)


@dataclass
class Event:
    id: int
    event_type: str
    actor_id: int
    application_id: int | None
    land_record_id: int | None
    event_data_json: dict[str, object]
    previous_event_hash: str
    event_hash: str
    created_at: datetime


def _chain(length: int = 3) -> list[Event]:
    events: list[Event] = []
    previous_hash = GENESIS_HASH
    for event_id in range(1, length + 1):
        item = Event(
            id=event_id,
            event_type="application_submitted",
            actor_id=17,
            application_id=23,
            land_record_id=None,
            event_data_json={"version_number": event_id, "application_number": "SYNTHETIC-1"},
            previous_event_hash=previous_hash,
            event_hash="",
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=event_id),
        )
        item.event_hash = calculate_event_hash(item)
        events.append(item)
        previous_hash = item.event_hash
    return events


def test_valid_chain_verifies_from_deterministic_genesis() -> None:
    events = _chain()

    result = verify_audit_chain(events)

    assert result.valid is True
    assert result.event_count == 3
    assert result.verified_event_ids == (1, 2, 3)
    assert result.head_hash == events[-1].event_hash
    assert result.errors == ()


def test_canonical_json_sorts_keys_and_removes_formatting_variance() -> None:
    assert canonical_json({"b": 2, "a": {"y": 1, "x": 0}}) == (
        '{"a":{"x":0,"y":1},"b":2}'
    )


def test_modified_event_data_is_detected() -> None:
    events = _chain()
    events[1].event_data_json["version_number"] = 999

    result = verify_audit_chain(events)

    assert result.valid is False
    assert any("Event hash mismatch at event ID 2" in error for error in result.errors)


def test_broken_previous_hash_link_and_missing_middle_event_are_detected() -> None:
    events = _chain()
    events[1].previous_event_hash = "a" * 64

    result = verify_audit_chain(events)

    assert result.valid is False
    assert any("disconnected from genesis" in error for error in result.errors)

    missing_middle = _chain()
    del missing_middle[1]
    missing_result = verify_audit_chain(missing_middle)
    assert missing_result.valid is False
    assert any("disconnected from genesis" in error for error in missing_result.errors)


def test_inconsistent_event_hash_is_detected() -> None:
    events = _chain()
    events[0].event_hash = "f" * 64

    result = verify_audit_chain(events)

    assert result.valid is False
    assert any("Event hash mismatch at event ID 1" in error for error in result.errors)
