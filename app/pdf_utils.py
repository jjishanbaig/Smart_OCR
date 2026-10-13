"""Bounded PDF inspection and page rendering helpers."""

from __future__ import annotations

import pymupdf as fitz
import numpy as np
import cv2


class PDFInputError(ValueError):
    """A PDF could not be safely opened or processed."""


def open_pdf(pdf_bytes: bytes, max_pages: int = 50) -> fitz.Document:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PDFInputError("The PDF is corrupted or could not be opened.") from exc
    if document.needs_pass:
        document.close()
        raise PDFInputError("This PDF is password-protected. Remove its password before upload.")
    if document.page_count == 0:
        document.close()
        raise PDFInputError("The PDF contains no pages.")
    if document.page_count > max_pages:
        document.close()
        raise PDFInputError(f"PDFs are limited to {max_pages} pages.")
    return document


def page_to_image(page: fitz.Page, dpi: int = 180) -> np.ndarray:
    scale = dpi / 72.0
    width = max(1, round(page.rect.width * scale))
    height = max(1, round(page.rect.height * scale))
    if width * height > 40_000_000 or max(width, height) > 12_000:
        raise PDFInputError("A PDF page exceeds the 40-megapixel rendering safety limit.")
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    if pixmap.n == 4:
        return cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def pdf_to_images(pdf_bytes: bytes, dpi: int = 180, max_pages: int = 50):
    """Compatibility helper retained for callers of the original project."""
    document = open_pdf(pdf_bytes, max_pages=max_pages)
    try:
        return [
            (page.number + 1, page_to_image(page, dpi=dpi))
            for page in document
        ]
    finally:
        document.close()
