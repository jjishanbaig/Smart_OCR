"""Document validation, text extraction and OCR orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import statistics
import time

import cv2
import pymupdf as fitz
import numpy as np
from PIL import Image
import pytesseract
from pytesseract.pytesseract import TesseractNotFoundError

from .config import DEFAULT_OCR_LANGUAGE, DEFAULT_OCR_PSM, MAX_PDF_PAGES, PDF_RENDER_DPI
from .ocr_engine import combine_text, engine_status, run_ocr
from .pdf_utils import PDFInputError, open_pdf, page_to_image
from .preprocessing import decode_image, preprocess_variants
from .spreadsheet_service import SpreadsheetInputError, process_spreadsheet, validate_xlsx
from .storage import save_preview

logger = logging.getLogger(__name__)


class DocumentProcessingError(ValueError):
    pass


def validate_content(path: Path, extension: str) -> None:
    if extension == ".xlsx":
        try:
            validate_xlsx(path)
        except SpreadsheetInputError as exc:
            raise DocumentProcessingError(str(exc)) from exc
        return
    if extension == ".csv":
        try:
            if not path.read_bytes().strip():
                raise DocumentProcessingError("The CSV file is empty.")
        except OSError as exc:
            raise DocumentProcessingError("The CSV file could not be read.") from exc
        return
    if extension == ".pdf":
        try:
            with path.open("rb") as file:
                signature = file.read(1024)
            if b"%PDF-" not in signature:
                raise DocumentProcessingError("The file does not have a valid PDF signature.")
        except OSError as exc:
            raise DocumentProcessingError("The uploaded PDF could not be read.") from exc
        return

    expected = {
        ".jpg": {"JPEG"},
        ".jpeg": {"JPEG"},
        ".png": {"PNG"},
        ".webp": {"WEBP"},
        ".bmp": {"BMP"},
    }.get(extension, set())
    try:
        with Image.open(path) as image:
            if image.format not in expected:
                raise DocumentProcessingError(
                    "The file contents do not match its image extension."
                )
            if image.width <= 0 or image.height <= 0:
                raise DocumentProcessingError("The image has invalid dimensions.")
            if image.width * image.height > 40_000_000:
                raise DocumentProcessingError("Image dimensions exceed the 40-megapixel safety limit.")
            image.verify()
    except DocumentProcessingError:
        raise
    except Exception as exc:
        raise DocumentProcessingError("The image is corrupted or could not be decoded.") from exc


def _confidence(items: list[dict]) -> float | None:
    values = [item["confidence"] for item in items if isinstance(item.get("confidence"), (int, float))]
    return round(sum(values) / len(values), 4) if values else None


def _validate_language(language: str) -> None:
    if not language or len(language) > 100 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+.-" for ch in language):
        raise DocumentProcessingError("Choose a valid Tesseract language code.")
    status = engine_status()
    if not status["available"]:
        raise TesseractNotFoundError()
    installed = set(status["languages"])
    requested = language.split("+")
    missing = [code for code in requested if code not in installed]
    if missing:
        raise DocumentProcessingError(
            f"Tesseract language data is not installed for: {', '.join(missing)}."
        )


def _best_variant(variants: list[tuple[str, np.ndarray]], language: str, psm: int):
    best = None
    best_score = float("-inf")
    for name, image in variants:
        items = run_ocr(image, language=language, psm=psm)
        confidence = _confidence(items) or 0.0
        words = len(items)
        # Confidence is an estimate. A small word-count term avoids favoring a
        # clean result that silently dropped most of the page.
        score = confidence * min(words, 18) / 18 + min(words, 18) * 0.002
        if score > best_score:
            best = (name, image, items)
            best_score = score
        if confidence >= 0.82 and words >= 8:
            break
    return best or ("original", variants[0][1], [])


def _best_handwriting_variant(variants: list[tuple[str, np.ndarray]], language: str, psm: int):
    """Try a few text layouts with Tesseract; this is a fallback, not a handwriting model."""
    psms = [6, 11, 13] if psm == 3 else list(dict.fromkeys([psm, 6, 11]))
    chosen_variants = [item for item in variants if item[0] in {"original", "contrast_denoised", "adaptive"}]
    if not chosen_variants:
        chosen_variants = variants[:3]
    best = None
    best_score = float("-inf")
    for name, image in chosen_variants:
        for candidate_psm in psms:
            items = run_ocr(image, language=language, psm=candidate_psm)
            confidence = _confidence(items) or 0.0
            words = len(items)
            score = confidence * min(words, 18) / 18 + min(words, 18) * 0.002
            if score > best_score:
                best = (name, image, items)
                best_score = score
    return best or ("original", variants[0][1], [])


def _rotate_image(image: np.ndarray, degrees: int) -> np.ndarray:
    return {
        0: image,
        90: cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
        180: cv2.rotate(image, cv2.ROTATE_180),
        270: cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }[degrees]


def _looks_like_text(text: str) -> bool:
    compact = "".join(character for character in text if character.isalnum())
    return len(compact) >= 5 and any(character.isalpha() for character in compact)


def _embedded_items(page: fitz.Page) -> list[dict]:
    items = []
    for word in page.get_text("words"):
        x0, y0, x1, y1, text = word[:5]
        items.append(
            {
                "text": text,
                "confidence": None,
                "box": [[round(x0, 2), round(y0, 2)], [round(x1, 2), round(y0, 2)], [round(x1, 2), round(y1, 2)], [round(x0, 2), round(y1, 2)]],
                "line": f"{word[5]}-{word[6]}-{word[7]}",
            }
        )
    return items


def _run_image_ocr(image: np.ndarray, language: str, psm: int, mode: str = "ocr"):
    variants = preprocess_variants(cv2.imencode(".png", image)[1].tobytes())
    if mode == "handwriting":
        try:
            from .config import AI_PROVIDER, GEMINI_API_KEY
            if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
                from .optional_services import recognize_handwriting_with_gemini

                vision_image = image
                max_dimension = max(vision_image.shape[:2])
                if max_dimension > 2800:
                    scale = 2800 / max_dimension
                    vision_image = cv2.resize(vision_image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                encoded_ok, encoded_image = cv2.imencode(".jpg", vision_image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if encoded_ok:
                    text = recognize_handwriting_with_gemini(encoded_image.tobytes())
                    items = [
                        {
                            "text": line.strip(),
                            "confidence": None,
                            "box": [[0, line_no], [image.shape[1], line_no], [image.shape[1], line_no + 1], [0, line_no + 1]],
                            "line": f"gemini-handwriting-{line_no}",
                        }
                        for line_no, line in enumerate(text.splitlines(), 1)
                        if line.strip()
                    ]
                    if items:
                        return "gemini_handwriting", image, items
        except Exception as exc:
            logger.warning("Gemini handwriting recognition failed; falling back to local OCR: %s", exc)
        # Prefer the dedicated handwriting model only when explicitly enabled and
        # its dependencies/model are available. Fall back safely to Tesseract.
        try:
            from .handwriting_service import recognize, ENABLED
            if ENABLED:
                trocr_text = recognize(image)
                if trocr_text.strip():
                    items = []
                    for line_no, line_text in enumerate(trocr_text.splitlines(), 1):
                        if line_text.strip():
                            items.append({
                                "text": line_text.strip(), "confidence": None,
                                "box": [[0, line_no], [image.shape[1], line_no], [image.shape[1], line_no + 1], [0, line_no + 1]],
                                "line": f"trocr-{line_no}",
                            })
                    return "trocr_handwritten", image, items
        except Exception as exc:
            logger.warning("Optional TrOCR recognition failed; falling back to Tesseract: %s", exc)
        return _best_handwriting_variant(variants, language, psm)
    return _best_variant(variants, language, psm)


def _page_result(
    job_id: str,
    number: int,
    image: np.ndarray,
    text: str,
    items: list[dict],
    source: str,
    status: str,
    embedded_text: str = "",
    variant: str | None = None,
    processed_image: np.ndarray | None = None,
    rotation: int = 0,
    warning: str | None = None,
) -> dict:
    save_preview(job_id, number, "original", image)
    if processed_image is not None:
        save_preview(job_id, number, "processed", processed_image)
    height, width = image.shape[:2]
    ocr_height, ocr_width = processed_image.shape[:2] if processed_image is not None else (height, width)
    return {
        "page": number,
        "text": text,
        "embedded_text": embedded_text,
        "items": items,
        "confidence": _confidence(items),
        "source": source,
        "status": status,
        "warning": warning,
        "variant": variant,
        "width": width,
        "height": height,
        "ocr_width": ocr_width,
        "ocr_height": ocr_height,
        "rotation": rotation,
        "has_processed_preview": processed_image is not None,
    }


def _process_page_image(
    image: np.ndarray,
    job_id: str,
    page_number: int,
    language: str,
    psm: int,
    selected: bool,
    embedded_text: str = "",
    rotation: int = 0,
    recognition_mode: str = "ocr",
) -> dict:
    if not selected:
        warning = "OCR was not requested for this page." if not embedded_text else None
        return _page_result(
            job_id, page_number, image, embedded_text, [],
            "embedded_text" if embedded_text else "not_processed",
            "embedded" if embedded_text else "not_selected", embedded_text, warning=warning,
        )

    ocr_image = _rotate_image(image, rotation)
    try:
        variant_name, processed, items = _run_image_ocr(ocr_image, language, psm, recognition_mode)
    except TesseractNotFoundError:
        if embedded_text:
            return _page_result(
                job_id, page_number, image, embedded_text, [], "embedded_text",
                "embedded", embedded_text,
                warning="Tesseract is not installed; embedded PDF text was preserved.",
            )
        if page_number > 0 and embedded_text == "":
            return _page_result(
                job_id, page_number, image, "", [], "ocr_unavailable", "failed", warning="Tesseract OCR is not installed or could not be started.",
            )
        raise
    except pytesseract.TesseractError as exc:
        if embedded_text:
            return _page_result(
                job_id, page_number, image, embedded_text, [], "embedded_text",
                "embedded", embedded_text, warning="OCR could not run; embedded PDF text was preserved.",
            )
        return _page_result(
            job_id, page_number, image, "", [], "ocr_failed", "failed", warning="Tesseract could not process this page.",
        )

    recognized = combine_text(items)
    chosen_text = recognized if recognized or not embedded_text else embedded_text
    chosen_source = "ocr" if recognized else ("embedded_text" if embedded_text else "ocr")
    confidence = _confidence(items)
    warning = None
    status = "complete" if chosen_text else "empty"
    if not chosen_text:
        warning = "No readable text was detected on this page."
    elif confidence is not None and confidence < 0.4:
        warning = "OCR confidence is low; review this page carefully."
    if recognition_mode == "handwriting":
        if variant_name == "gemini_handwriting":
            handwriting_note = "Handwriting recognition used Gemini Vision; review names, numbers, and uncertain words against the source image."
        elif variant_name == "trocr_handwritten":
            handwriting_note = "Handwriting recognition used TrOCR; review output because model output can be incorrect."
        else:
            handwriting_note = "Handwriting mode used Tesseract fallback. Cursive recognition may be poor."
        warning = f"{warning} {handwriting_note}" if warning else handwriting_note
    return _page_result(
        job_id,
        page_number,
        image,
        chosen_text,
        items if recognized else [],
        chosen_source,
        status,
        embedded_text,
        variant_name,
        None if variant_name == "original" else processed,
        rotation,
        warning,
    )


def _compose_text(pages: list[dict]) -> str:
    sections = []
    for page in pages:
        text = page.get("text", "").strip()
        if text:
            sections.append(f"--- Page {page['page']} ---\n{text}")
    return "\n\n".join(sections)


def process_document(
    path: Path,
    job_id: str,
    filename: str,
    extension: str,
    language: str | None = None,
    psm: int | None = None,
    selected_pages: set[int] | None = None,
    mode: str = "ocr",
) -> dict:
    started = time.perf_counter()
    if extension in {".csv", ".xlsx"}:
        try:
            return process_spreadsheet(path, job_id, filename, extension)
        except SpreadsheetInputError as exc:
            raise DocumentProcessingError(str(exc)) from exc
    language = language or DEFAULT_OCR_LANGUAGE
    psm = DEFAULT_OCR_PSM if psm is None else int(psm)
    if not 0 <= psm <= 13:
        raise DocumentProcessingError("Tesseract page segmentation mode must be between 0 and 13.")
    pages = []
    if extension == ".pdf":
        try:
            document = open_pdf(path.read_bytes(), MAX_PDF_PAGES)
        except PDFInputError as exc:
            raise DocumentProcessingError(str(exc)) from exc
        try:
            if selected_pages and max(selected_pages) > document.page_count:
                raise DocumentProcessingError("A selected page number is outside this document.")
            for page in document:
                number = page.number + 1
                try:
                    embedded = page.get_text("text").strip()
                except Exception as exc:
                    raise DocumentProcessingError("A PDF page is corrupted or could not be read.") from exc
                has_embedded = _looks_like_text(embedded)
                must_ocr = (selected_pages is None or number in selected_pages) and (not has_embedded or number in (selected_pages or set()))
                try:
                    image = page_to_image(page, PDF_RENDER_DPI)
                except PDFInputError as exc:
                    raise DocumentProcessingError(str(exc)) from exc
                if must_ocr:
                    result = _process_page_image(
                        image, job_id, number, language, psm, True,
                        embedded if has_embedded else "", recognition_mode=mode,
                    )
                    if result.get("items") and has_embedded:
                        result["embedded_text"] = embedded
                    pages.append(result)
                elif has_embedded:
                    pages.append(
                        _page_result(
                            job_id, number, image, embedded, _embedded_items(page),
                            "embedded_text", "embedded", embedded,
                        )
                    )
                else:
                    pages.append(
                        _process_page_image(
                            image, job_id, number, language, psm, False,
                        )
                    )
        finally:
            document.close()
    else:
        try:
            original = decode_image(path.read_bytes())
        except ValueError as exc:
            raise DocumentProcessingError(str(exc)) from exc
        pages.append(
            _process_page_image(
                original, job_id, 1, language, psm,
                selected_pages is None or 1 in selected_pages,
                recognition_mode=mode,
            )
        )

    text = _compose_text(pages)
    confidence_values = [page["confidence"] for page in pages if page.get("confidence") is not None]
    warnings = [page["warning"] for page in pages if page.get("warning")]
    if not text and not warnings:
        warnings.append("No readable text was detected.")
    return {
        "job_id": job_id,
        "filename": filename,
        "file_extension": extension,
        "file_size": path.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "pages": pages,
        "text": text,
        "corrected_text": text,
        "corrected_pages": {},
        "average_confidence": round(statistics.mean(confidence_values), 4) if confidence_values else None,
        "warnings": warnings,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "language": language,
        "psm": psm,
    }


def reprocess_page(job: dict, page_number: int, rotation: int = 0, language: str | None = None, psm: int | None = None) -> dict:
    if rotation not in {0, 90, 180, 270}:
        raise DocumentProcessingError("Rotation must be 0, 90, 180 or 270 degrees.")
    path = Path(__import__("app.storage", fromlist=["source_path"]).source_path(job))
    if not path.is_file():
        raise DocumentProcessingError("The original upload has expired. Upload the document again to reprocess it.")
    if job.get("file_extension") in {".csv", ".xlsx"}:
        raise DocumentProcessingError("Spreadsheet cells are imported directly and do not need OCR reprocessing.")
    language = language or job.get("language", DEFAULT_OCR_LANGUAGE)
    psm = DEFAULT_OCR_PSM if psm is None else int(psm)
    if job["file_extension"] == ".pdf":
        try:
            document = open_pdf(path.read_bytes(), MAX_PDF_PAGES)
        except PDFInputError as exc:
            raise DocumentProcessingError(str(exc)) from exc
        try:
            if page_number < 1 or page_number > document.page_count:
                raise DocumentProcessingError("Page number is out of range.")
            page = document[page_number - 1]
            try:
                image = page_to_image(page, PDF_RENDER_DPI)
            except PDFInputError as exc:
                raise DocumentProcessingError(str(exc)) from exc
            embedded = page.get_text("text").strip()
        finally:
            document.close()
    else:
        if page_number != 1:
            raise DocumentProcessingError("Image uploads contain one page.")
        image = decode_image(path.read_bytes())
        embedded = ""
    result = _process_page_image(
        image, job["job_id"], page_number, language, psm, True,
        embedded if _looks_like_text(embedded) else "", rotation,
        job.get("mode", "ocr"),
    )
    existing = job["pages"]
    existing[page_number - 1] = result
    job["text"] = _compose_text(existing)
    job.setdefault("corrected_pages", {}).pop(str(page_number), None)
    job["corrected_text"] = _compose_text(
        [
            {**page, "text": job.get("corrected_pages", {}).get(str(page["page"]), page.get("text", ""))}
            for page in existing
        ]
    )
    job["average_confidence"] = round(
        statistics.mean([p["confidence"] for p in existing if p.get("confidence") is not None]), 4
    ) if any(p.get("confidence") is not None for p in existing) else None
    job["warnings"] = [page["warning"] for page in existing if page.get("warning")]
    job["language"] = language
    job["psm"] = psm
    return job
