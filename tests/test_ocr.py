from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

import numpy as np
import pytest
import pytesseract
import pymupdf as fitz
from PIL import Image
from pytesseract import Output
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from modules.auth import AuthService, AuthorizationError, hash_password
from modules.database import (
    Application,
    ApplicationVersion,
    Base,
    Database,
    User,
    create_database_engine,
    transaction,
)
from modules.document_storage import DocumentStorage
from modules.extraction import OCRLine, extract_fields
from modules.ocr import DocumentOCRResult, _preprocess_image, process_document


@pytest.fixture
def ocr_context(
    tmp_path,
) -> Iterator[tuple[sessionmaker[Session], AuthService]]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with transaction(session_factory) as session:
        session.add(
            User(
                full_name="Synthetic OCR Owner",
                email="ocr-owner@example.test",
                password_hash=hash_password("synthetic-ocr-password"),
                role="landowner",
            )
        )
    service = AuthService(
        Database(session_factory),
        DocumentStorage(tmp_path / "private", max_file_size_bytes=1024 * 1024),
    )
    yield session_factory, service
    engine.dispose()


def _pdf_with_pages(*page_texts: str) -> bytes:
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_text((48, 72), text, fontsize=14)
    content = document.tobytes()
    document.close()
    return content


def _tesseract_data(text: str, confidence: str = "91") -> dict[str, list[str]]:
    words = text.split()
    return {
        "text": words,
        "conf": [confidence] * len(words),
        "page_num": ["1"] * len(words),
        "block_num": ["1"] * len(words),
        "par_num": ["1"] * len(words),
        "line_num": ["1"] * len(words),
    }


def test_field_extraction_is_labeled_conservative_and_preserves_uncertainty() -> None:
    result = extract_fields(
        [
            OCRLine("Owner name: Synthetic Owner", confidence=88.5),
            OCRLine("District: Sample District", confidence=84),
            OCRLine("Land area: 2.5 acres", confidence=82),
            OCRLine("Unlabeled value that looks like a village", confidence=99),
            OCRLine("Sale deed", confidence=93),
        ]
    )

    assert result.fields["owner_name"] == "Synthetic Owner"
    assert result.fields["district"] == "Sample District"
    assert result.fields["land_area"] == "2.5 acres"
    assert result.fields["village"] is None
    assert result.fields["document_type"] == "sale deed"
    assert result.confidence["owner_name"] == 88.5
    assert any("do not indicate" in warning for warning in result.warnings)


def test_malformed_extracted_area_is_preserved_for_field_warning() -> None:
    result = extract_fields(
        [OCRLine("Land area: approximately two acres", confidence=73.5)]
    )

    assert result.fields["land_area"] == "approximately two acres"
    assert result.confidence["land_area"] == 73.5


def test_pdf_pages_are_rendered_and_ocr_text_is_combined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_texts = iter(
        [
            "Owner name: Synthetic Owner",
            "District: Sample District",
        ]
    )
    def fake_ocr(
        image: np.ndarray,
        output_type: object,
        config: str,
    ) -> dict[str, list[str]]:
        assert image.ndim == 2
        assert output_type is Output.DICT
        assert config == "--psm 6"
        return _tesseract_data(next(page_texts))

    monkeypatch.setattr("modules.ocr.pytesseract.image_to_data", fake_ocr)

    result = process_document(
        _pdf_with_pages(
            "Owner name: Synthetic Owner",
            "District: Sample District",
        ),
        "synthetic-land-record.pdf",
        "application/pdf",
    )

    assert result.status == "complete"
    assert result.page_count == 2
    assert "Owner name: Synthetic Owner" in result.ocr_text
    assert "District: Sample District" in result.ocr_text
    assert result.fields["owner_name"] == "Synthetic Owner"
    assert result.fields["district"] == "Sample District"
    assert result.field_confidence["owner_name"] == 91


def test_image_is_preprocessed_and_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    image_bytes = BytesIO()
    Image.new("RGB", (400, 200), "white").save(image_bytes, format="PNG")
    captured: list[np.ndarray] = []

    def fake_ocr(
        image: np.ndarray,
        output_type: object,
        config: str,
    ) -> dict[str, list[str]]:
        assert output_type is Output.DICT
        assert config == "--psm 6"
        captured.append(image)
        return _tesseract_data("Village: Sample Village")

    monkeypatch.setattr("modules.ocr.pytesseract.image_to_data", fake_ocr)
    result = process_document(image_bytes.getvalue(), "record.png", "image/png")

    assert result.status == "complete"
    assert result.page_count == 1
    assert result.fields["village"] == "Sample Village"
    assert len(captured) == 1
    assert captured[0].ndim == 2

    processed = _preprocess_image(Image.new("RGB", (80, 40), "white"))
    assert processed.ndim == 2
    assert min(processed.shape) > 40


def test_missing_tesseract_returns_manual_entry_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_tesseract(
        image: np.ndarray,
        output_type: object,
        config: str,
    ) -> dict[str, list[str]]:
        assert image.size > 0 and output_type is Output.DICT and config
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr("modules.ocr.pytesseract.image_to_data", missing_tesseract)
    result = process_document(
        _pdf_with_pages("Synthetic land record"),
        "record.pdf",
        "application/pdf",
    )

    assert result.status == "failed"
    assert any("Install Tesseract" in warning for warning in result.warnings)
    assert result.ocr_text == ""


def test_upload_persists_ocr_output_separately_from_manual_corrections(
    ocr_context: tuple[sessionmaker[Session], AuthService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service = ocr_context
    synthetic_result = DocumentOCRResult(
        status="complete",
        ocr_text="Owner name: OCR Owner",
        fields={"owner_name": "OCR Owner", "district": None},
        field_confidence={"owner_name": 90.0, "district": None},
        warnings=("Confidence estimates OCR recognition only.",),
        page_count=1,
    )
    def fake_process(content: bytes, filename: str, mime_type: str) -> DocumentOCRResult:
        assert content and filename and mime_type
        return synthetic_result

    monkeypatch.setattr("modules.auth.process_document", fake_process)
    token = service.login("ocr-owner@example.test", "synthetic-ocr-password")
    uploaded = service.upload_landowner_document(
        token,
        "synthetic-record.pdf",
        "application/pdf",
        _pdf_with_pages("Synthetic land record"),
    )

    assert uploaded.ocr_result is not None
    assert uploaded.ocr_result["ocr_text"] == "Owner name: OCR Owner"
    assert uploaded.ocr_result["fields"] == {"owner_name": "OCR Owner", "district": None}
    service.save_landowner_document_corrections(
        token,
        uploaded.id,
        {
            "owner_name": "Corrected Owner",
            "district": "Manually entered district",
            "village": "Sample Village",
            "survey_or_plot_number": "SY-123",
            "land_area": "2.5 acres",
            "document_type": "sale deed",
        },
    )

    documents = service.list_landowner_documents(token)
    assert len(documents) == 1
    assert documents[0].ocr_result is not None
    stored_fields = documents[0].ocr_result.get("fields")
    assert isinstance(stored_fields, dict)
    assert stored_fields["owner_name"] == "OCR Owner"
    assert documents[0].corrected_fields_json["owner_name"] == "Corrected Owner"
    with session_factory() as session:
        version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == uploaded.application_id,
                ApplicationVersion.version_number == 2,
            )
        )
        assert version is not None
        assert version.extracted_fields_json["ocr_text"] == "Owner name: OCR Owner"
        assert version.extracted_fields_json["warning_threshold"] == 60
        assert any(
            warning["field"] == "district" and warning["code"] == "missing_required"
            for warning in version.extracted_fields_json["field_warnings"]
        )
        assert version.corrected_fields_json["owner_name"] == "Corrected Owner"
        original_version = session.scalar(
            select(ApplicationVersion).where(
                ApplicationVersion.application_id == uploaded.application_id,
                ApplicationVersion.version_number == 1,
            )
        )
        assert original_version is not None
        assert original_version.corrected_fields_json == {}
        assert any(
            warning["field"] == "district" and warning["source"] == "ocr"
            for warning in original_version.extracted_fields_json["field_warnings"]
        )


def test_missing_ocr_output_can_be_completed_manually(
    ocr_context: tuple[sessionmaker[Session], AuthService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service = ocr_context
    failed_result = DocumentOCRResult(
        status="failed",
        ocr_text="",
        fields={},
        field_confidence={},
        warnings=("OCR output unavailable; enter the fields manually.",),
        page_count=0,
    )

    def fake_process(content: bytes, filename: str, mime_type: str) -> DocumentOCRResult:
        assert content and filename and mime_type
        return failed_result

    monkeypatch.setattr("modules.auth.process_document", fake_process)
    token = service.login("ocr-owner@example.test", "synthetic-ocr-password")
    uploaded = service.upload_landowner_document(
        token,
        "synthetic-no-ocr.pdf",
        "application/pdf",
        _pdf_with_pages("Synthetic land record"),
    )

    assert uploaded.ocr_result is not None
    assert uploaded.ocr_result["status"] == "failed"
    assert uploaded.ocr_result["ocr_text"] == ""
    service.save_landowner_document_corrections(
        token,
        uploaded.id,
        {
            "owner_name": "Manually Entered Owner",
            "district": "Sample District",
            "village": "Sample Village",
            "survey_or_plot_number": "SY-123",
            "land_area": "2.5 acres",
            "document_type": "sale deed",
        },
    )

    document = service.list_landowner_documents(token)[0]
    assert document.ocr_result is not None
    assert document.ocr_result["status"] == "failed"
    assert document.corrected_fields_json["owner_name"] == "Manually Entered Owner"


def test_approved_application_fields_cannot_be_edited(
    ocr_context: tuple[sessionmaker[Session], AuthService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, service = ocr_context
    synthetic_result = DocumentOCRResult(
        status="complete",
        ocr_text="Owner name: OCR Owner",
        fields={"owner_name": "OCR Owner"},
        field_confidence={"owner_name": 90.0},
        warnings=(),
        page_count=1,
    )

    def fake_process(content: bytes, filename: str, mime_type: str) -> DocumentOCRResult:
        assert content and filename and mime_type
        return synthetic_result

    monkeypatch.setattr("modules.auth.process_document", fake_process)
    token = service.login("ocr-owner@example.test", "synthetic-ocr-password")
    uploaded = service.upload_landowner_document(
        token,
        "approved-record.pdf",
        "application/pdf",
        _pdf_with_pages("Synthetic record"),
    )
    with transaction(session_factory) as session:
        application = session.get(Application, uploaded.application_id)
        assert application is not None
        application.status = "approved"

    with pytest.raises(AuthorizationError, match="approved"):
        service.save_landowner_document_corrections(
            token,
            uploaded.id,
            {
                "owner_name": "Corrected Owner",
                "district": "Sample District",
                "village": "Sample Village",
                "survey_or_plot_number": "SY-123",
                "land_area": "2 acres",
                "document_type": "sale deed",
            },
        )
    with session_factory() as session:
        application = session.get(Application, uploaded.application_id)
        assert application is not None
        assert application.current_version == 1
