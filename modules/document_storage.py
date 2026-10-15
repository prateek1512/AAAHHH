"""Private storage and validation for landowner-uploaded documents."""

from __future__ import annotations

import hashlib
import os
import re
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pymupdf as fitz
from PIL import Image, UnidentifiedImageError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIRECTORY_ENV = "LAND_RECORDS_UPLOAD_DIRECTORY"
MAX_UPLOAD_BYTES_ENV = "LAND_RECORDS_MAX_UPLOAD_BYTES"
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MEBIBYTE = 1024 * 1024

SUPPORTED_FORMATS = {
    ".pdf": ("application/pdf",),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
}
_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.(pdf|png|jpg|jpeg)$")


class DocumentStorageError(Exception):
    """Base class for upload validation and private storage errors."""


class UnsupportedDocumentError(DocumentStorageError):
    """Raised when the extension, MIME type, or content signature is unsupported."""


class InvalidDocumentError(DocumentStorageError):
    """Raised when a document is empty, malformed, or has an unsafe filename."""


class DocumentTooLargeError(DocumentStorageError):
    """Raised when a document exceeds the configured maximum size."""


@dataclass(frozen=True, slots=True)
class StoredDocument:
    original_filename: str
    stored_path: str
    document_sha256: str
    mime_type: str
    size_bytes: int


def _configured_max_upload_bytes() -> int:
    raw_value = os.environ.get(MAX_UPLOAD_BYTES_ENV)
    if raw_value is None:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{MAX_UPLOAD_BYTES_ENV} must be a positive integer.") from error
    if value <= 0:
        raise ValueError(f"{MAX_UPLOAD_BYTES_ENV} must be a positive integer.")
    return value


def _safe_original_filename(filename: str) -> str:
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(character in filename for character in '<>:"|?*')
        or filename.endswith((" ", "."))
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        or len(filename) > 255
    ):
        raise InvalidDocumentError("The uploaded filename is invalid.")
    return filename


def _validate_file_content(extension: str, content: bytes) -> None:
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise UnsupportedDocumentError("The file content does not match its extension.")
        try:
            with fitz.open(stream=content, filetype="pdf") as pdf:
                if pdf.is_encrypted or pdf.page_count < 1:
                    raise InvalidDocumentError("The PDF is empty or cannot be read.")
        except InvalidDocumentError:
            raise
        except (fitz.FileDataError, RuntimeError, ValueError) as error:
            raise InvalidDocumentError(
                "The PDF appears corrupted or cannot be read."
            ) from error
        return

    expected_signature = b"\x89PNG\r\n\x1a\n" if extension == ".png" else b"\xff\xd8\xff"
    if not content.startswith(expected_signature):
        raise UnsupportedDocumentError("The file content does not match its extension.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                expected_format = "PNG" if extension == ".png" else "JPEG"
                if image.format != expected_format:
                    raise UnsupportedDocumentError(
                        "The file content does not match its extension."
                    )
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
    except UnsupportedDocumentError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise InvalidDocumentError(
            "The image appears corrupted or cannot be read."
        ) from error


class DocumentStorage:
    """Store uploaded originals in a private directory using opaque internal names."""

    def __init__(
        self,
        directory: str | Path | None = None,
        max_file_size_bytes: int | None = None,
    ) -> None:
        configured_directory = directory or os.environ.get(UPLOAD_DIRECTORY_ENV)
        if configured_directory is None:
            configured_directory = PROJECT_ROOT / "data" / "private_uploads"
        self.upload_directory = Path(configured_directory).expanduser().resolve()
        public_static_directory = (PROJECT_ROOT / "static").resolve()
        if self.upload_directory == public_static_directory or public_static_directory in (
            self.upload_directory.parents
        ):
            raise ValueError("The upload directory must be outside the public static directory.")

        self.max_file_size_bytes = (
            _configured_max_upload_bytes()
            if max_file_size_bytes is None
            else max_file_size_bytes
        )
        if self.max_file_size_bytes <= 0:
            raise ValueError("The upload size limit must be a positive integer.")
        self.upload_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.upload_directory.chmod(0o700)

    @property
    def max_upload_size_mb(self) -> int:
        """Return the rounded-up size limit for Streamlit's uploader control."""
        return max(1, (self.max_file_size_bytes + MEBIBYTE - 1) // MEBIBYTE)

    def store(self, original_filename: str, mime_type: str, content: bytes) -> StoredDocument:
        """Validate and persist original bytes without deriving a storage path from the name."""
        safe_filename = _safe_original_filename(original_filename)
        extension = Path(safe_filename).suffix.lower()
        if extension not in SUPPORTED_FORMATS:
            raise UnsupportedDocumentError("Choose a PDF, PNG, JPG, or JPEG file.")

        normalized_mime_type = mime_type.split(";", maxsplit=1)[0].strip().lower()
        if normalized_mime_type not in SUPPORTED_FORMATS[extension]:
            raise UnsupportedDocumentError("The file MIME type does not match its extension.")
        if not content:
            raise InvalidDocumentError("The selected file is empty.")
        if len(content) > self.max_file_size_bytes:
            raise DocumentTooLargeError(
                f"The file exceeds the configured limit of "
                f"{self.max_file_size_bytes / MEBIBYTE:.1f} MB."
            )

        _validate_file_content(extension, content)
        storage_key = f"{uuid4().hex}{extension}"
        destination = self._path_for_key(storage_key)
        try:
            file_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(file_descriptor, "wb") as stored_file:
                stored_file.write(content)
                stored_file.flush()
                os.fsync(stored_file.fileno())
        except OSError as error:
            try:
                destination.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(f"Could not remove partial document: {cleanup_error}")
            raise DocumentStorageError("The document could not be stored.") from error

        return StoredDocument(
            original_filename=safe_filename,
            stored_path=storage_key,
            document_sha256=hashlib.sha256(content).hexdigest(),
            mime_type=normalized_mime_type,
            size_bytes=len(content),
        )

    def read(self, stored_path: str) -> bytes:
        """Read an opaque stored file key; arbitrary paths are never accepted."""
        path = self._path_for_key(stored_path)
        if path.is_symlink() or not path.is_file():
            raise DocumentStorageError("The requested document is unavailable.")
        try:
            return path.read_bytes()
        except OSError as error:
            raise DocumentStorageError("The requested document could not be read.") from error

    def delete(self, stored_path: str) -> None:
        """Remove an opaque stored file key, typically after a failed DB transaction."""
        path = self._path_for_key(stored_path)
        if path.is_symlink():
            raise DocumentStorageError("The stored document path is invalid.")
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise DocumentStorageError("The stored document could not be removed.") from error

    def _path_for_key(self, storage_key: str) -> Path:
        if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
            raise DocumentStorageError("The stored document path is invalid.")
        path = self.upload_directory / storage_key
        if path.resolve().parent != self.upload_directory:
            raise DocumentStorageError("The stored document path is invalid.")
        return path
