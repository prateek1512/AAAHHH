from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from io import BytesIO

import pytest
import pymupdf as fitz
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import AuthService, AuthenticationError, AuthorizationError, hash_password
from modules.database import (
    Application,
    Base,
    Database,
    Document,
    User,
    create_database_engine,
    transaction,
)
from modules.document_storage import (
    DocumentStorage,
    DocumentTooLargeError,
    InvalidDocumentError,
    StoredDocument,
    UnsupportedDocumentError,
)


@pytest.fixture
def upload_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage]]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with transaction(session_factory) as session:
        session.add_all(
            [
                User(
                    full_name="Synthetic Landowner",
                    email="upload-owner@example.test",
                    password_hash=hash_password("synthetic-owner-password"),
                    role="landowner",
                ),
                User(
                    full_name="Other Synthetic Landowner",
                    email="other-upload-owner@example.test",
                    password_hash=hash_password("synthetic-other-owner-password"),
                    role="landowner",
                ),
                User(
                    full_name="Synthetic Officer",
                    email="upload-officer@example.test",
                    password_hash=hash_password("synthetic-officer-password"),
                    role="officer",
                ),
            ]
        )
    database = Database(session_factory)
    storage = DocumentStorage(tmp_path / "private", max_file_size_bytes=1024 * 1024)
    from modules.ocr import DocumentOCRResult

    monkeypatch.setattr(
        "modules.auth.process_document",
        lambda *_args: DocumentOCRResult(
            status="complete",
            ocr_text="Synthetic OCR text",
            fields={},
            field_confidence={},
            warnings=(),
            page_count=1,
        ),
    )
    owner_service = AuthService(database, storage)
    other_service = AuthService(database, storage)
    yield session_factory, owner_service, other_service, storage
    engine.dispose()


def _valid_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Synthetic land record")
    content = document.tobytes()
    document.close()
    return content


def _valid_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(output, format="PNG")
    return output.getvalue()


def _token(service: AuthService, email: str, password: str) -> str:
    return service.login(email, password)


def test_valid_upload_preserves_original_metadata_and_creates_draft(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    session_factory, owner_service, _, storage = upload_context
    content = _valid_pdf()
    token = _token(owner_service, "upload-owner@example.test", "synthetic-owner-password")

    summary = owner_service.upload_landowner_document(
        token,
        "synthetic-record.pdf",
        "application/pdf",
        content,
    )

    files = list(storage.upload_directory.iterdir())
    assert len(files) == 1
    assert files[0].name != "synthetic-record.pdf"
    assert files[0].read_bytes() == content
    assert summary.original_filename == "synthetic-record.pdf"
    assert summary.mime_type == "application/pdf"
    assert summary.application_number.startswith("LR-")
    with session_factory() as session:
        document = session.scalar(select(Document).where(Document.id == summary.id))
        assert document is not None
        assert document.document_sha256 == sha256(content).hexdigest()
        assert document.stored_path == files[0].name
        application = session.get(Application, summary.application_id)
        assert application is not None
        assert application.status == "draft"
        assert application.applicant.email == "upload-owner@example.test"


def test_unsupported_extension_and_mime_are_rejected(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    _, owner_service, _, _ = upload_context
    token = _token(owner_service, "upload-owner@example.test", "synthetic-owner-password")
    with pytest.raises(UnsupportedDocumentError):
        owner_service.upload_landowner_document(token, "notes.txt", "text/plain", b"hello")
    with pytest.raises(UnsupportedDocumentError):
        owner_service.upload_landowner_document(
            token,
            "record.pdf",
            "image/png",
            _valid_pdf(),
        )
    with pytest.raises(UnsupportedDocumentError):
        owner_service.upload_landowner_document(
            token,
            "mismatched-signature.pdf",
            "application/pdf",
            b"not a PDF",
        )


def test_oversized_file_is_rejected(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    _, owner_service, _, _ = upload_context
    token = _token(owner_service, "upload-owner@example.test", "synthetic-owner-password")
    with pytest.raises(DocumentTooLargeError):
        owner_service.upload_landowner_document(
            token,
            "large.pdf",
            "application/pdf",
            b"x" * (1024 * 1024 + 1),
        )


def test_corrupted_file_is_rejected(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    _, owner_service, _, _ = upload_context
    token = _token(owner_service, "upload-owner@example.test", "synthetic-owner-password")
    with pytest.raises(InvalidDocumentError):
        owner_service.upload_landowner_document(
            token,
            "corrupted.pdf",
            "application/pdf",
            b"%PDF-1.7\nnot a valid PDF",
        )


def test_internal_filename_is_unique_and_does_not_trust_original_name(
    tmp_path,
) -> None:
    storage = DocumentStorage(tmp_path / "private", max_file_size_bytes=1024 * 1024)
    content = _valid_png()
    first: StoredDocument = storage.store("same-name.png", "image/png", content)
    second = storage.store("same-name.png", "image/png", content)

    assert first.stored_path != second.stored_path
    assert first.original_filename == "same-name.png"
    assert (storage.upload_directory / first.stored_path).read_bytes() == content
    with pytest.raises(InvalidDocumentError):
        storage.store("../unsafe.png", "image/png", content)


def test_landowner_cannot_list_or_retrieve_another_owners_document(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    _, owner_service, other_service, _ = upload_context
    owner_token = _token(
        owner_service,
        "upload-owner@example.test",
        "synthetic-owner-password",
    )
    other_token = _token(
        other_service,
        "other-upload-owner@example.test",
        "synthetic-other-owner-password",
    )
    document = owner_service.upload_landowner_document(
        owner_token,
        "owner-record.png",
        "image/png",
        _valid_png(),
    )

    assert other_service.list_landowner_documents(other_token) == ()
    with pytest.raises(AuthorizationError):
        other_service.get_landowner_document(other_token, document.id)
    officer_token = _token(
        other_service,
        "upload-officer@example.test",
        "synthetic-officer-password",
    )
    with pytest.raises(AuthorizationError):
        other_service.get_landowner_document(officer_token, document.id)


def test_unauthenticated_upload_is_rejected(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    _, owner_service, _, _ = upload_context
    with pytest.raises(AuthenticationError):
        owner_service.upload_landowner_document(
            None,
            "record.pdf",
            "application/pdf",
            _valid_pdf(),
        )


def test_empty_document_is_rejected(
    upload_context: tuple[sessionmaker[Session], AuthService, AuthService, DocumentStorage],
) -> None:
    _, _, _, storage = upload_context
    with pytest.raises(InvalidDocumentError):
        storage.store("empty.pdf", "application/pdf", b"")
