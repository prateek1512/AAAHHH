"""Canonical SHA-256 audit event creation and chain verification."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from sqlalchemy import select, update
from sqlalchemy.orm import Session, attributes
from sqlalchemy.types import Text, TypeDecorator

if TYPE_CHECKING:
    from modules.database import AuditEvent

GENESIS_HASH = hashlib.sha256(b"land-record-audit-chain-v1:genesis").hexdigest()


class AuditChainError(RuntimeError):
    """Raised when an audit event cannot safely be appended."""


def canonical_json(value: object) -> str:
    """Serialize JSON data deterministically; reject non-JSON and non-finite values."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class CanonicalJSON(TypeDecorator[dict[str, object]]):
    """Persist JSON objects as canonical JSON text and decode them on read."""

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: dict[str, object] | None, dialect: object
    ) -> str:
        if not isinstance(value, dict):
            raise TypeError("Audit event data must be a JSON object.")
        return canonical_json(value)

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> dict[str, object]:
        if value is None:
            return {"_invalid_audit_data": None}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"_invalid_canonical_json": value}
        if not isinstance(decoded, dict):
            return {"_invalid_audit_data": decoded}
        return decoded


@dataclass(frozen=True, slots=True)
class AuditVerificationResult:
    valid: bool
    event_count: int
    verified_event_ids: tuple[int, ...]
    head_hash: str | None
    errors: tuple[str, ...]


def canonical_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat(timespec="microseconds")


def event_hash_payload(event: Any) -> dict[str, object]:
    event_data = event.event_data_json
    if isinstance(event_data, str):
        event_data = json.loads(event_data)
    if not isinstance(event_data, dict):
        raise ValueError("Audit event data must be a JSON object.")
    return {
        "event_id": event.id,
        "event_type": event.event_type,
        "actor_id": event.actor_id,
        "application_id": event.application_id,
        "land_record_id": event.land_record_id,
        "event_data": event_data,
        "created_at": canonical_utc_timestamp(event.created_at),
        "previous_event_hash": event.previous_event_hash,
    }


def calculate_event_hash(event: Any) -> str:
    payload = canonical_json(event_hash_payload(event))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calculate_approved_record_version_hash(
    *,
    public_record_id: str,
    application_id: int,
    version_number: int,
    approved_fields: Mapping[str, object],
    authorized_by: int,
    authorized_at: datetime,
) -> str:
    payload = canonical_json(
        {
            "public_record_id": public_record_id,
            "application_id": application_id,
            "version_number": version_number,
            "approved_fields": dict(approved_fields),
            "authorized_by": authorized_by,
            "authorized_at": canonical_utc_timestamp(authorized_at),
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_audit_event(
    session: Session,
    *,
    event_type: str,
    actor_id: int,
    application_id: int | None,
    land_record_id: int | None = None,
    event_data: Mapping[str, object] | None = None,
    created_at: datetime,
) -> AuditEvent:
    """Append and flush an event in the caller's business transaction."""
    from modules.database import (
        AuditChainState,
        AuditEvent,
        _verify_audit_chain_in_session,
    )

    if not event_type.strip():
        raise ValueError("Audit event type must not be empty.")
    data = dict(event_data or {})
    canonical_json(data)

    state = session.scalar(
        select(AuditChainState)
        .where(AuditChainState.id == 1)
        .with_for_update()
    )
    if state is None:
        if session.scalar(select(AuditEvent.id).limit(1)) is not None:
            raise AuditChainError("Cannot append: audit chain head state is missing.")
        state = AuditChainState(id=1, head_hash=GENESIS_HASH)
        session.add(state)
        session.flush()

    verification = _verify_audit_chain_in_session(session)
    if not verification.valid:
        detail = "; ".join(verification.errors)
        raise AuditChainError(f"Cannot append to an invalid audit chain: {detail}")

    previous_event_hash = verification.head_hash or GENESIS_HASH
    event = AuditEvent(
        event_type=event_type,
        actor_id=actor_id,
        application_id=application_id,
        land_record_id=land_record_id,
        event_data_json=data,
        previous_event_hash=previous_event_hash,
        event_hash=f"{secrets.token_hex(8)}{secrets.token_hex(24)}",
        created_at=created_at,
    )
    session.add(event)
    session.flush()
    calculated_hash = calculate_event_hash(event)
    session.connection().execute(
        update(AuditEvent)
        .where(AuditEvent.id == event.id)
        .values(event_hash=calculated_hash)
    )
    attributes.set_committed_value(event, "event_hash", calculated_hash)
    state.head_hash = calculated_hash
    return event


def verify_audit_chain(events: Iterable[Any]) -> AuditVerificationResult:
    """Verify links, event contents, and coverage from the deterministic genesis."""
    event_list = tuple(events)
    if not event_list:
        return AuditVerificationResult(True, 0, (), None, ())

    errors: list[str] = []
    id_counts: dict[int, int] = {}
    hash_map: dict[str, Any] = {}
    children: dict[str, list[Any]] = {}

    for item in event_list:
        id_counts[item.id] = id_counts.get(item.id, 0) + 1
        if item.event_hash in hash_map:
            errors.append(f"Duplicate event hash found for event ID {item.id}.")
        else:
            hash_map[item.event_hash] = item
        children.setdefault(item.previous_event_hash, []).append(item)

    for event_id, count in id_counts.items():
        if count > 1:
            errors.append(f"Duplicate event ID found: {event_id}.")

    genesis_events = children.get(GENESIS_HASH, [])
    if not genesis_events:
        errors.append(
            "No event links to the genesis hash; the first event may be missing or disconnected."
        )
        return AuditVerificationResult(
            False, len(event_list), (), None, tuple(errors)
        )
    if len(genesis_events) > 1:
        errors.append("Multiple events link directly to genesis; the chain has a fork.")

    current = genesis_events[0]
    expected_previous_hash = GENESIS_HASH
    visited_ids: set[int] = set()
    verified_ids: list[int] = []

    while current.id not in visited_ids:
        visited_ids.add(current.id)
        verified_ids.append(current.id)

        if current.previous_event_hash != expected_previous_hash:
            errors.append(f"Broken previous-hash link at event ID {current.id}.")
        try:
            recomputed_hash = calculate_event_hash(current)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"Event ID {current.id} has invalid canonical data: {error}.")
            recomputed_hash = ""
        if recomputed_hash != current.event_hash:
            errors.append(f"Event hash mismatch at event ID {current.id}.")

        expected_previous_hash = current.event_hash
        next_events = children.get(current.event_hash, [])
        if len(next_events) > 1:
            errors.append(
                f"Multiple events link to event ID {current.id}; the chain has a fork."
            )
        if not next_events:
            break
        current = next_events[0]

    if current.id in visited_ids and children.get(current.event_hash):
        errors.append(f"Cycle detected after event ID {current.id}.")

    if len(visited_ids) != len(event_list):
        missing = len(event_list) - len(visited_ids)
        errors.append(
            f"{missing} event(s) are disconnected from genesis; an event may be missing "
            "or a previous-hash link may be broken."
        )

    ordered_ids = sorted(item.id for item in event_list)
    if ordered_ids:
        for previous_id, current_id in zip(ordered_ids, ordered_ids[1:]):
            if current_id != previous_id + 1:
                errors.append(
                    f"Audit event ID gap detected between {previous_id} and {current_id}."
                )

    head_hash = expected_previous_hash if len(visited_ids) else None
    return AuditVerificationResult(
        valid=not errors,
        event_count=len(event_list),
        verified_event_ids=tuple(verified_ids),
        head_hash=head_hash,
        errors=tuple(dict.fromkeys(errors)),
    )
