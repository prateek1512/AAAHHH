"""Create public verification URLs and QR images for approved records."""

from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote, urlsplit

import qrcode

DEFAULT_PUBLIC_VERIFICATION_BASE_URL = "http://localhost:8501"
PUBLIC_VERIFICATION_BASE_URL_ENV = "PUBLIC_VERIFICATION_BASE_URL"


@dataclass(frozen=True, slots=True)
class PublicRecordQRCode:
    verification_url: str
    png_bytes: bytes


def get_public_verification_base_url() -> str:
    """Return a validated configured page base URL, defaulting to local Streamlit."""
    configured = os.environ.get(
        PUBLIC_VERIFICATION_BASE_URL_ENV,
        DEFAULT_PUBLIC_VERIFICATION_BASE_URL,
    ).strip()
    parsed = urlsplit(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{PUBLIC_VERIFICATION_BASE_URL_ENV} must be an HTTP(S) base URL "
            "without credentials, query, or fragment."
        )
    return configured.rstrip("/")


def build_public_verification_url(
    public_record_id: str,
    *,
    base_url: str | None = None,
) -> str:
    """Build a URL carrying only the URL-encoded public record ID."""
    record_id = public_record_id.strip()
    if not record_id:
        raise ValueError("A public record ID is required to build a verification URL.")
    root = (
        get_public_verification_base_url()
        if base_url is None
        else _validated_base_url(base_url)
    )
    return f"{root}/?record_id={quote(record_id, safe='')}"


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Public verification base URL must be HTTP(S) without credentials, "
            "query, or fragment."
        )
    return value.strip().rstrip("/")


def generate_public_record_qr(
    public_record_id: str,
    *,
    base_url: str | None = None,
) -> PublicRecordQRCode:
    """Generate a PNG QR encoding only the public verification URL."""
    url = build_public_verification_url(public_record_id, base_url=base_url)
    image = qrcode.make(url)
    output = BytesIO()
    image.save(output, format="PNG")
    return PublicRecordQRCode(verification_url=url, png_bytes=output.getvalue())
