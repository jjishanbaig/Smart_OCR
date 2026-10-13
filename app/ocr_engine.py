"""Tesseract adapter with word geometry and relative confidence estimates."""

from __future__ import annotations

import shutil

import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from pytesseract.pytesseract import TesseractNotFoundError


def engine_status() -> dict:
    """Report whether the executable and English data can be reached."""
    try:
        executable = pytesseract.pytesseract.tesseract_cmd
        binary = bool(shutil.which(executable) or (executable and __import__("os").path.isfile(executable)))
        if not binary:
            return {"available": False, "languages": [], "message": "Tesseract OCR was not found."}
        languages = sorted(pytesseract.get_languages(config=""))
        return {"available": True, "languages": languages, "message": "Tesseract is ready."}
    except TesseractNotFoundError:
        return {"available": False, "languages": [], "message": "Tesseract OCR was not found."}
    except Exception:
        return {"available": False, "languages": [], "message": "Tesseract could not be started."}


def _as_rgb(image: np.ndarray | bytes) -> np.ndarray:
    if isinstance(image, bytes):
        image = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("The image could not be decoded.")
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def run_ocr(image: np.ndarray | bytes, language: str = "eng", psm: int = 3) -> list[dict]:
    if not 0 <= int(psm) <= 13:
        raise ValueError("Tesseract page segmentation mode must be between 0 and 13.")
    data = pytesseract.image_to_data(
        _as_rgb(image),
        lang=language,
        output_type=Output.DICT,
        config=f"--psm {int(psm)}",
    )
    items = []
    for index, raw in enumerate(data.get("text", [])):
        word = (raw or "").strip()
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError, KeyError):
            confidence = -1
        if not word or confidence < 0:
            continue
        x, y, width, height = [
            int(data[key][index]) for key in ("left", "top", "width", "height")
        ]
        line = "-".join(
            str(data[key][index])
            for key in ("block_num", "par_num", "line_num")
            if key in data
        )
        items.append(
            {
                "text": word,
                "confidence": round(confidence / 100, 4),
                "box": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
                "line": line,
            }
        )
    return items


def combine_text(items: list[dict]) -> str:
    lines: list[str] = []
    current_line = None
    words: list[str] = []
    for item in items:
        line = item.get("line")
        if current_line is not None and line != current_line:
            lines.append(" ".join(words))
            words = []
        current_line = line
        words.append(item["text"])
    if words:
        lines.append(" ".join(words))
    return "\n".join(lines).strip()
