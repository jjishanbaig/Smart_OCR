"""Conservative row/cell grouping from OCR word geometry."""

from __future__ import annotations

import statistics
import re


def extract_table(items: list[dict]) -> dict:
    words = [
        item for item in items
        if item.get("text") and item.get("box") and len(item["box"]) >= 3
    ]
    if len(words) < 4:
        return {"rows": [], "warning": "Not enough positioned words to infer a table."}

    def bounds(item):
        box = item["box"]
        return float(box[0][0]), float(box[0][1]), float(box[2][0]), float(box[2][1])

    line_groups: dict[str, list[dict]] = {}
    for index, item in enumerate(words):
        line_groups.setdefault(str(item.get("line") or f"y-{bounds(item)[1]:.0f}-{index}"), []).append(item)

    grouped = []
    heights = [max(1.0, bounds(item)[3] - bounds(item)[1]) for item in words]
    typical_height = statistics.median(heights)
    gap_limit = max(22.0, typical_height * 1.8)
    for line_items in line_groups.values():
        line_items.sort(key=lambda item: bounds(item)[0])
        cells: list[list[dict]] = []
        for item in line_items:
            if not cells or bounds(item)[0] - bounds(cells[-1][-1])[2] > gap_limit:
                cells.append([item])
            else:
                cells[-1].append(item)
        if cells:
            grouped.append((min(bounds(item)[1] for item in line_items), cells))
    grouped.sort(key=lambda row: row[0])

    multi_cell = [cells for _, cells in grouped if len(cells) > 1]
    if len(multi_cell) < 2:
        return {"rows": [], "warning": "No clear multi-row table structure was found."}

    starts = sorted(bounds(cell[0])[0] for _, cells in grouped for cell in cells)
    columns: list[float] = []
    tolerance = max(22.0, typical_height * 1.5)
    for start in starts:
        if not columns or min(abs(start - value) for value in columns) > tolerance:
            columns.append(start)
    columns.sort()

    rows = []
    for _, cells in grouped:
        row = [""] * len(columns)
        for cell in cells:
            start = bounds(cell[0])[0]
            column = min(range(len(columns)), key=lambda idx: abs(start - columns[idx]))
            value = " ".join(item["text"] for item in cell)
            row[column] = (row[column] + " " + value).strip()
        if any(row):
            rows.append(row)
    max_columns = max((len(row) for row in rows), default=0)
    rows = [row + [""] * (max_columns - len(row)) for row in rows]
    warning = "Table cells are inferred from spacing. Review row and column alignment before use."
    return {"rows": rows, "warning": warning}


def extract_form_fields(pages: list[dict]) -> dict:
    """Return only explicit label:value text found in OCR output."""
    pattern = re.compile(r"^\s*([\w /().-]{2,40})\s*[:：]\s*(.{1,160}?)\s*$")
    fields = []
    for page in pages:
        for line in (page.get("text") or "").splitlines():
            match = pattern.match(line)
            if match:
                fields.append(
                    {
                        "page": page["page"],
                        "label": match.group(1).strip(),
                        "value": match.group(2).strip(),
                        "source_line": line.strip(),
                    }
                )
    return {
        "fields": fields,
        "warning": "Fields are copied from recognized label:value lines. Review them against the source; no missing value is inferred.",
    }
