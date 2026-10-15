"""PDF/image preprocessing and Tesseract OCR orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytesseract
import pymupdf as fitz
from PIL import Image, UnidentifiedImageError
from pytesseract import Output
from pytesseract.pytesseract import TesseractError, TesseractNotFoundError

from modules.extraction import FieldExtraction, OCRLine, as_json_value, extract_fields

MAX_RENDERED_IMAGE_PIXELS = 25_000_000
PDF_RENDER_SCALE = 2.0
MIN_OCR_IMAGE_EDGE = 1200
MAX_PDF_PAGES = 100


@dataclass(frozen=True, slots=True)
class DocumentOCRResult:
    status: str
    ocr_text: str
    fields: dict[str, str | None]
    field_confidence: dict[str, float | None]
    warnings: tuple[str, ...]
    page_count: int

    def to_storage(self) -> dict[str, object]:
        return {
            "ocr_text": self.ocr_text,
            **as_json_value(
                FieldExtraction(
                    fields=self.fields,
                    confidence=self.field_confidence,
                    warnings=self.warnings,
                )
            ),
            "status": self.status,
            "page_count": self.page_count,
        }


def render_document_preview(content: bytes, filename: str, mime_type: str) -> bytes | None:
    """Render the original first page as PNG for a private authenticated UI preview."""
    extension = Path(filename).suffix.lower()
    if extension in {".png", ".jpg", ".jpeg"} and mime_type.startswith("image/"):
        return content
    if extension != ".pdf" and mime_type.lower() != "application/pdf":
        return None
    document: fitz.Document | None = None
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.is_encrypted or document.page_count < 1:
            return None
        page = document[0]
        scale = min(
            1.5,
            1400 / max(page.rect.width, page.rect.height),
            (MAX_RENDERED_IMAGE_PIXELS / (page.rect.width * page.rect.height)) ** 0.5,
        )
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")
    except (fitz.FileDataError, RuntimeError, ValueError):
        return None
    finally:
        if document is not None:
            document.close()


def _preprocess_image(image: Image.Image) -> np.ndarray:
    rgb_image = np.asarray(image.convert("RGB"))
    grayscale = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    if grayscale.shape[0] * grayscale.shape[1] > MAX_RENDERED_IMAGE_PIXELS:
        scale = (MAX_RENDERED_IMAGE_PIXELS / (grayscale.shape[0] * grayscale.shape[1])) ** 0.5
        grayscale = cv2.resize(
            grayscale,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )
    if min(grayscale.shape[:2]) < MIN_OCR_IMAGE_EDGE:
        scale = min(3.0, MIN_OCR_IMAGE_EDGE / min(grayscale.shape[:2]))
        grayscale = cv2.resize(
            grayscale,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )

    denoised = cv2.medianBlur(grayscale, 3)
    mean_brightness = float(np.mean(denoised))
    contrast = float(np.std(denoised))
    if mean_brightness > 125 and contrast > 25:
        return cv2.threshold(
            denoised,
            0,
            255,
            cv2.THRESH_BINARY | cv2.THRESH_OTSU,
        )[1]
    return denoised


def _lines_from_tesseract(image: np.ndarray) -> tuple[str, list[OCRLine]]:
    data = pytesseract.image_to_data(
        image,
        output_type=Output.DICT,
        config="--psm 6",
    )
    grouped: dict[tuple[str, ...], list[tuple[str, float]]] = {}
    for index, word in enumerate(data["text"]):
        text = str(word).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError, IndexError):
            confidence = -1
        if confidence < 0:
            confidence_value = None
        else:
            confidence_value = min(100.0, confidence)
        key = tuple(
            str(data[name][index])
            for name in ("page_num", "block_num", "par_num", "line_num")
        )
        grouped.setdefault(key, []).append(
            (text, confidence_value if confidence_value is not None else -1.0)
        )

    lines: list[OCRLine] = []
    for values in grouped.values():
        line_text = " ".join(word for word, _ in values)
        valid_confidences = [confidence for _, confidence in values if confidence >= 0]
        line_confidence = (
            sum(valid_confidences) / len(valid_confidences) if valid_confidences else None
        )
        lines.append(OCRLine(text=line_text, confidence=line_confidence))
    return "\n".join(line.text for line in lines), lines


def _failed_result(warning: str, page_count: int = 0) -> DocumentOCRResult:
    fields = extract_fields(())
    return DocumentOCRResult(
        status="failed",
        ocr_text="",
        fields=fields.fields,
        field_confidence=fields.confidence,
        warnings=(warning,),
        page_count=page_count,
    )


def process_document(content: bytes, filename: str, mime_type: str) -> DocumentOCRResult:
    """OCR every supported page, retaining raw recognized text and known warnings."""
    extension = Path(filename).suffix.lower()
    is_pdf = extension == ".pdf" or mime_type.lower() == "application/pdf"
    if extension not in {".pdf", ".png", ".jpg", ".jpeg"}:
        return _failed_result("OCR does not support this document type.")
    if not content:
        return _failed_result("The document contains no data to process.")

    pages: list[Image.Image] = []
    page_warnings: list[str] = []
    total_pages = 0
    document: fitz.Document | None = None
    try:
        if is_pdf:
            document = fitz.open(stream=content, filetype="pdf")
            if document.is_encrypted:
                return _failed_result("The PDF is encrypted and could not be processed.")
            if document.page_count == 0:
                return _failed_result("The PDF has no pages to process.")
            total_pages = document.page_count
            for page_index in range(min(document.page_count, MAX_PDF_PAGES)):
                try:
                    page = document[page_index]
                    scale = PDF_RENDER_SCALE
                    estimated_pixels = page.rect.width * page.rect.height * scale**2
                    if estimated_pixels > MAX_RENDERED_IMAGE_PIXELS:
                        scale = min(
                            scale,
                            (MAX_RENDERED_IMAGE_PIXELS / (page.rect.width * page.rect.height)) ** 0.5,
                        )
                    pixmap = document[page_index].get_pixmap(
                        matrix=fitz.Matrix(scale, scale),
                        alpha=False,
                    )
                    pages.append(
                        Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    )
                except (fitz.FileDataError, RuntimeError, ValueError) as error:
                    pages.append(Image.new("RGB", (1, 1), color="white"))
                    page_warnings.append(
                        f"Page {page_index + 1} could not be rendered: {error}"
                    )
            if document.page_count > MAX_PDF_PAGES:
                page_warnings.append(
                    f"Only the first {MAX_PDF_PAGES} pages were processed."
                )
        else:
            with Image.open(BytesIO(content)) as image:
                total_pages = 1
                if image.width * image.height > MAX_RENDERED_IMAGE_PIXELS:
                    return _failed_result(
                        "The image dimensions are too large for OCR processing. "
                        "The original remains available for manual entry.",
                        page_count=total_pages,
                    )
                image.load()
                pages = [image.copy()]
    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        return _failed_result(f"The PDF could not be opened: {error}")
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as error:
        return _failed_result(f"The image could not be opened: {error}")
    finally:
        if document is not None:
            document.close()

    all_lines: list[OCRLine] = []
    page_texts: list[str] = []
    successful_pages = 0
    for page_index, page in enumerate(pages):
        try:
            text, lines = _lines_from_tesseract(_preprocess_image(page))
        except TesseractNotFoundError:
            return _failed_result(
                "Tesseract is not installed or is not on PATH. Install Tesseract and restart "
                "the application; the original document remains available for manual entry.",
                page_count=total_pages,
            )
        except TesseractError as error:
            page_warnings.append(f"OCR failed on page {page_index + 1}: {error}")
            continue
        except (cv2.error, OSError, ValueError) as error:
            page_warnings.append(f"Image preprocessing failed on page {page_index + 1}: {error}")
            continue
        page_texts.append(text)
        all_lines.extend(lines)
        successful_pages += 1

    raw_text = "\n\n".join(page_texts)
    processing_warnings = bool(page_warnings)
    extraction = extract_fields(all_lines)
    page_warnings.extend(extraction.warnings)
    no_readable_text = successful_pages > 0 and not raw_text.strip()
    if no_readable_text:
        page_warnings.append(
            "No readable text was recognized. Review the original document and enter fields manually."
        )
    if successful_pages == 0:
        status = "failed"
        if not page_warnings:
            page_warnings.append("OCR did not return usable text.")
    elif processing_warnings or no_readable_text:
        status = "partial"
    else:
        status = "complete"

    return DocumentOCRResult(
        status=status,
        ocr_text=raw_text,
        fields=extraction.fields,
        field_confidence=extraction.confidence,
        warnings=tuple(page_warnings),
        page_count=total_pages,
    )
