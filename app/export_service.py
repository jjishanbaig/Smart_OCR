"""Text, office, table CSV and genuinely searchable PDF exports."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import io
import json
import re
import zipfile
from pathlib import Path

import cv2
import pymupdf as fitz
import numpy as np

from .config import PDF_RENDER_DPI
from .pdf_utils import open_pdf, page_to_image
from .preprocessing import decode_image
from .storage import source_path
from .table_service import extract_table


@dataclass
class Export:
    data: bytes
    media_type: str
    filename: str


def _document_text(job: dict, corrected: bool) -> str:
    if corrected:
        return job.get("corrected_text", job.get("text", ""))
    return job.get("text", "")


def _safe_xml_text(text: str) -> str:
    return "".join(
        character for character in text
        if character in "\t\n\r" or ord(character) >= 32
    )


def _make_docx(text: str) -> bytes:
    paragraphs = []
    for line in _safe_xml_text(text).splitlines() or [""]:
        paragraphs.append(
            "<w:p><w:r><w:t xml:space=\"preserve\">"
            + escape(line)
            + "</w:t></w:r></w:p>"
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def _table_csv(job: dict) -> bytes:
    import csv

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if any(page.get("sheet_rows") is not None for page in job.get("pages", [])):
        writer.writerow(["worksheet", "row", "column", "value"])
        for page in job.get("pages", []):
            for row_index, row in enumerate(page.get("sheet_rows") or [], start=1):
                for column_index, value in enumerate(row, start=1):
                    if value != "":
                        writer.writerow([page.get("sheet_name", f"Worksheet {page['page']}"), row_index, column_index, _safe_csv_value(value)])
        return output.getvalue().encode("utf-8-sig")
    writer.writerow(["page", "row", "column", "value"])
    for page in job.get("pages", []):
        result = extract_table(page.get("items", []))
        for row_index, row in enumerate(result["rows"], start=1):
            for column_index, value in enumerate(row, start=1):
                if value:
                    writer.writerow([page["page"], row_index, column_index, _safe_csv_value(value)])
    return output.getvalue().encode("utf-8-sig")


def _safe_csv_value(value: object) -> str:
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _insert_invisible_text(page: fitz.Page, items: list[dict], width: int, height: int) -> None:
    display_rect = page.rect
    for item in items:
        text = str(item.get("text", "")).strip()
        box = item.get("box") or []
        if not text or len(box) < 3 or not width or not height:
            continue
        try:
            x0 = float(box[0][0]) / width * display_rect.width
            y0 = float(box[0][1]) / height * display_rect.height
            x1 = float(box[2][0]) / width * display_rect.width
            y1 = float(box[2][1]) / height * display_rect.height
            display_box = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            native_box = display_box * page.derotation_matrix
            font_size = max(3.5, min(18.0, native_box.height * 0.82))
            inserted = page.insert_textbox(
                native_box,
                text,
                fontname="helv",
                fontsize=font_size,
                color=(0, 0, 0),
                render_mode=3,
                overlay=True,
            )
            if inserted < 0:
                page.insert_text(
                    (native_box.x0, native_box.y1 - 0.5),
                    text,
                    fontname="helv",
                    fontsize=font_size,
                    render_mode=3,
                    overlay=True,
                )
        except (ValueError, TypeError, RuntimeError, UnicodeEncodeError):
            # Base-14 Helvetica cannot encode every writing system. Skipping an
            # unsupported glyph is safer than replacing recognized content.
            continue


def _searchable_pdf(job: dict) -> bytes:
    if job.get("file_extension") in {".csv", ".xlsx"}:
        raise ValueError("Searchable PDF export is for image and PDF uploads. Export this spreadsheet as CSV instead.")
    original = source_path(job)
    if not original.is_file():
        raise ValueError("The source upload has expired; searchable PDF export is no longer available.")
    by_number = {page["page"]: page for page in job.get("pages", [])}
    output = fitz.open()
    try:
        if job["file_extension"] == ".pdf":
            source = open_pdf(original.read_bytes())
            try:
                for source_page in source:
                    number = source_page.number + 1
                    result = by_number.get(number, {})
                    rotation_delta = int(result.get("rotation", 0))
                    if rotation_delta:
                        source_page.set_rotation((source_page.rotation + rotation_delta) % 360)
                    if result.get("source") == "ocr" and result.get("items"):
                        _insert_invisible_text(
                            source_page,
                            result["items"],
                            int(result.get("ocr_width") or result.get("width") or 1),
                            int(result.get("ocr_height") or result.get("height") or 1),
                        )
                return source.tobytes(garbage=4, deflate=True)
            finally:
                source.close()

        image = decode_image(original.read_bytes())
        result = by_number.get(1, {})
        rotation = int(result.get("rotation", 0))
        if rotation == 90:
            image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            image = cv2.rotate(image, cv2.ROTATE_180)
        elif rotation == 270:
            image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        height, width = image.shape[:2]
        page = output.new_page(width=width * 72 / PDF_RENDER_DPI, height=height * 72 / PDF_RENDER_DPI)
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise ValueError("Could not prepare the source image for PDF export.")
        page.insert_image(page.rect, stream=encoded.tobytes())
        _insert_invisible_text(
            page,
            result.get("items", []),
            int(result.get("ocr_width") or width),
            int(result.get("ocr_height") or height),
        )
        return output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()


def create_export(job: dict, kind: str, corrected: bool = True) -> Export:
    base_name = Path(job["filename"]).stem or "document"
    base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name)[:80] or "document"
    text = _document_text(job, corrected)
    if kind == "txt":
        return Export(text.encode("utf-8"), "text/plain; charset=utf-8", f"{base_name}_text.txt")
    if kind == "json":
        payload = {
            "filename": job["filename"],
            "created_at": job["created_at"],
            "text": job.get("text", ""),
            "corrected_text": job.get("corrected_text", job.get("text", "")),
            "pages": job.get("pages", []),
            "average_confidence_estimate": job.get("average_confidence"),
            "warnings": job.get("warnings", []),
        }
        return Export(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", f"{base_name}_ocr.json")
    if kind == "docx":
        return Export(
            _make_docx(text),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"{base_name}_corrected.docx",
        )
    if kind == "csv":
        return Export(_table_csv(job), "text/csv; charset=utf-8", f"{base_name}_tables.csv")
    if kind == "searchable-pdf":
        return Export(_searchable_pdf(job), "application/pdf", f"{base_name}_searchable.pdf")
    if kind in {"xlsx", "excel"}:
        raise NotImplementedError("Excel export is unavailable because no Excel-writing library is installed. CSV export is available.")
    raise ValueError("Choose TXT, DOCX, JSON, CSV or searchable-pdf export.")
