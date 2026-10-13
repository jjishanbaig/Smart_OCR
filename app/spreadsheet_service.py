"""Dependency-free CSV and XLSX import for the local document workspace."""

from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import time
import xml.etree.ElementTree as ET


MAX_SHEETS = 50
MAX_ROWS_PER_SHEET = 10_000
MAX_COLUMNS = 256
MAX_CELLS = 20_000
MAX_TEXT_CHARS = 1_000_000
MAX_XLSX_EXPANDED_BYTES = 50 * 1024 * 1024

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class SpreadsheetInputError(ValueError):
    """Raised when an uploaded CSV/XLSX cannot be parsed safely."""


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SpreadsheetInputError("CSV must use UTF-8 or a Windows-compatible text encoding.")


def _parse_csv(path: Path) -> list[tuple[str, list[list[str]]]]:
    try:
        text = _decode_csv(path.read_bytes())
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = []
        cells_seen = 0
        characters = 0
        for row in csv.reader(io.StringIO(text, newline=""), dialect, strict=True):
            if len(rows) >= MAX_ROWS_PER_SHEET:
                raise SpreadsheetInputError(f"CSV is limited to {MAX_ROWS_PER_SHEET:,} rows.")
            if len(row) > MAX_COLUMNS:
                raise SpreadsheetInputError(f"CSV is limited to {MAX_COLUMNS} columns.")
            cells_seen += len(row)
            characters += sum(len(cell) for cell in row)
            if cells_seen > MAX_CELLS or characters > MAX_TEXT_CHARS:
                raise SpreadsheetInputError("CSV contains too many cells or too much text to import safely.")
            rows.append(row)
        if not any(cell.strip() for row in rows for cell in row):
            raise SpreadsheetInputError("The CSV file contains no cell values.")
        return [("CSV data", rows)]
    except SpreadsheetInputError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SpreadsheetInputError("The CSV file is malformed or could not be decoded.") from exc


def _safe_xlsx_members(archive: zipfile.ZipFile) -> set[str]:
    infos = archive.infolist()
    if len(infos) > 2048 or sum(info.file_size for info in infos) > MAX_XLSX_EXPANDED_BYTES:
        raise SpreadsheetInputError("The Excel workbook is too large to unpack safely.")
    names = set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise SpreadsheetInputError("The Excel workbook contains an invalid file path.")
        if name in names:
            raise SpreadsheetInputError("The Excel workbook contains duplicate archive entries.")
        names.add(name)
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise SpreadsheetInputError("The file is not a valid XLSX workbook.")
    return names


def _read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    data = archive.read(name)
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SpreadsheetInputError("The Excel workbook contains unsupported XML declarations.")
    return ET.fromstring(data)


def validate_xlsx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            _safe_xlsx_members(archive)
    except SpreadsheetInputError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise SpreadsheetInputError("The Excel file is not a valid XLSX workbook.") from exc


def _column_index(reference: str, fallback: int) -> int:
    match = re.match(r"([A-Z]+)", reference.upper())
    if not match:
        return fallback
    value = 0
    for character in match.group(1):
        value = value * 26 + ord(character) - ord("A") + 1
    return value - 1


def _read_xlsx(path: Path) -> list[tuple[str, list[list[str]]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = _safe_xlsx_members(archive)
            workbook = _read_xml(archive, "xl/workbook.xml")
            rels = _read_xml(archive, "xl/_rels/workbook.xml.rels")
            targets = {}
            for rel in rels.findall(f"{{{_PKG_REL_NS}}}Relationship"):
                if rel.get("TargetMode") == "External":
                    continue
                target = (rel.get("Target") or "").replace("\\", "/")
                resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                if resolved.startswith("xl/") and ".." not in resolved.split("/"):
                    targets[rel.get("Id", "")] = resolved

            strings = []
            if "xl/sharedStrings.xml" in members:
                shared = _read_xml(archive, "xl/sharedStrings.xml")
                strings = ["".join(node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t")) for item in shared.findall(f"{{{_MAIN_NS}}}si")]

            sheets = workbook.findall(f".//{{{_MAIN_NS}}}sheet")
            if len(sheets) > MAX_SHEETS:
                raise SpreadsheetInputError(f"Excel workbooks are limited to {MAX_SHEETS} worksheets.")
            result = []
            total_cells = 0
            total_characters = 0
            for sheet in sheets:
                name = (sheet.get("name") or f"Worksheet {len(result) + 1}")[:120]
                target = targets.get(sheet.get(f"{{{_REL_NS}}}id", ""))
                if not target or target not in members:
                    continue
                root = _read_xml(archive, target)
                rows = []
                for row_element in root.findall(f".//{{{_MAIN_NS}}}sheetData/{{{_MAIN_NS}}}row"):
                    if len(rows) >= MAX_ROWS_PER_SHEET:
                        raise SpreadsheetInputError(f"Each worksheet is limited to {MAX_ROWS_PER_SHEET:,} rows.")
                    row_values: list[str] = []
                    for cell in row_element.findall(f"{{{_MAIN_NS}}}c"):
                        column = _column_index(cell.get("r", ""), len(row_values))
                        if column >= MAX_COLUMNS:
                            raise SpreadsheetInputError(f"Each worksheet is limited to {MAX_COLUMNS} columns.")
                        if len(row_values) <= column:
                            row_values.extend([""] * (column + 1 - len(row_values)))
                        cell_type = cell.get("t", "")
                        value_node = cell.find(f"{{{_MAIN_NS}}}v")
                        if cell_type == "inlineStr":
                            value = "".join(node.text or "" for node in cell.iter(f"{{{_MAIN_NS}}}t"))
                        elif value_node is None or value_node.text is None:
                            value = ""
                        elif cell_type == "s":
                            try:
                                value = strings[int(value_node.text)]
                            except (ValueError, IndexError):
                                value = ""
                        elif cell_type == "b":
                            value = "TRUE" if value_node.text == "1" else "FALSE"
                        else:
                            value = value_node.text
                        row_values[column] = value
                        if value:
                            total_cells += 1
                            total_characters += len(value)
                        if total_cells > MAX_CELLS or total_characters > MAX_TEXT_CHARS:
                            raise SpreadsheetInputError("The workbook contains too many populated cells or too much text to import safely.")
                    rows.append(row_values)
                result.append((name, rows))
            if not result:
                raise SpreadsheetInputError("No readable worksheets were found in the Excel workbook.")
            return result
    except SpreadsheetInputError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, RuntimeError, NotImplementedError) as exc:
        raise SpreadsheetInputError("The Excel workbook is malformed or could not be read.") from exc


def read_spreadsheet(path: Path, extension: str) -> list[tuple[str, list[list[str]]]]:
    if extension == ".csv":
        return _parse_csv(path)
    if extension == ".xlsx":
        return _read_xlsx(path)
    raise SpreadsheetInputError("Use a CSV file or an Excel XLSX workbook. Legacy XLS files are not supported.")


def process_spreadsheet(path: Path, job_id: str, filename: str, extension: str) -> dict:
    started = time.perf_counter()
    sheets = read_spreadsheet(path, extension)
    pages = []
    text_sections = []
    for page_number, (sheet_name, rows) in enumerate(sheets, start=1):
        lines = ["\t".join(row).rstrip() for row in rows]
        while lines and not lines[-1]:
            lines.pop()
        data_text = "\n".join(lines).strip()
        sheet_text = f"Worksheet: {sheet_name}\n{data_text}" if data_text else f"Worksheet: {sheet_name} (empty)"
        text_sections.append(sheet_text)
        items = []
        max_columns = max((len(row) for row in rows), default=1)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                value = str(value).strip()
                if not value:
                    continue
                x = column_index * 180
                y = row_index * 28
                width = max(30, min(170, len(value) * 8))
                items.append({
                    "text": value,
                    "confidence": None,
                    "box": [[x, y], [x + width, y], [x + width, y + 20], [x, y + 20]],
                    "line": f"sheet-{page_number}-row-{row_index + 1}",
                })
        pages.append({
            "page": page_number,
            "sheet_name": sheet_name,
            "text": sheet_text,
            "items": items,
            "confidence": None,
            "source": "spreadsheet",
            "status": "complete",
            "warning": None,
            "variant": None,
            "width": max(1, max_columns * 180),
            "height": max(1, len(rows) * 28),
            "ocr_width": None,
            "ocr_height": None,
            "rotation": 0,
            "has_preview": False,
            "has_processed_preview": False,
            "sheet_rows": rows,
        })
    text = "\n\n".join(text_sections)
    return {
        "job_id": job_id,
        "filename": filename,
        "file_extension": extension,
        "file_size": path.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "spreadsheet",
        "pages": pages,
        "text": text,
        "corrected_text": text,
        "corrected_pages": {},
        "average_confidence": None,
        "warnings": [],
        "duration_seconds": round(time.perf_counter() - started, 3),
        "language": None,
        "psm": None,
    }
