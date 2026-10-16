from __future__ import annotations

import pytest
from urllib.parse import parse_qs, urlsplit

from modules.public_qr import (
    DEFAULT_PUBLIC_VERIFICATION_BASE_URL,
    PUBLIC_VERIFICATION_BASE_URL_ENV,
    build_public_verification_url,
    generate_public_record_qr,
    get_public_verification_base_url,
)


def test_url_uses_public_record_id_and_configured_base_url() -> None:
    url = build_public_verification_url(
        "LR-REC-2026-AB 12",
        base_url="https://records.example.test/verify",
    )

    assert url == "https://records.example.test/verify/?record_id=LR-REC-2026-AB%2012"
    assert "record_id=" in url
    assert "database" not in url


def test_local_development_base_url_defaults_and_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PUBLIC_VERIFICATION_BASE_URL_ENV, raising=False)
    assert get_public_verification_base_url() == DEFAULT_PUBLIC_VERIFICATION_BASE_URL
    monkeypatch.setenv(
        PUBLIC_VERIFICATION_BASE_URL_ENV,
        "http://localhost:8502",
    )
    assert build_public_verification_url("PUBLIC-123") == (
        "http://localhost:8502/?record_id=PUBLIC-123"
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "javascript:alert(1)",
        "https://user:secret@example.test",
        "https://records.example.test?token=private",
        "https://records.example.test#private",
        "missing-scheme.example.test",
    ],
)
def test_invalid_base_urls_are_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        build_public_verification_url("PUBLIC-123", base_url=base_url)


def test_qr_png_contains_a_public_verification_url_only() -> None:
    qr = generate_public_record_qr(
        "PUBLIC-RECORD-123",
        base_url="https://records.example.test",
    )

    assert qr.verification_url == (
        "https://records.example.test/?record_id=PUBLIC-RECORD-123"
    )
    parsed = urlsplit(qr.verification_url)
    assert parse_qs(parsed.query) == {"record_id": ["PUBLIC-RECORD-123"]}
    assert "private" not in qr.verification_url
    assert "deed" not in qr.verification_url
    assert qr.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"deed contents" not in qr.png_bytes
    assert b"private contact" not in qr.png_bytes
