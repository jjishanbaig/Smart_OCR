"""Optional TrOCR handwriting recognition with a safe Tesseract fallback.

TrOCR is loaded lazily so the base app continues to run on Python 3.14 without
PyTorch/Transformers installed. It is intended for line crops, not whole pages.
"""
from __future__ import annotations

import os
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

from .config import AI_PROVIDER, GEMINI_API_KEY, AI_MODEL

MODEL_ID = os.getenv("SMARTOCR_TROCR_MODEL", "microsoft/trocr-base-handwritten").strip()
ENABLED = os.getenv("SMARTOCR_ENABLE_TROCR", "0").strip().lower() in {"1", "true", "yes"}


def status() -> dict:
    if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
        return {"available": True, "engine": f"Gemini Vision ({AI_MODEL})", "message": "Gemini handwriting recognition is ready; review transcribed text against the image."}
    if not ENABLED:
        return {"available": False, "engine": "Tesseract fallback", "message": "Dedicated handwriting AI is optional. Set SMARTOCR_ENABLE_TROCR=1 after installing compatible torch/transformers to enable TrOCR."}
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
        return {"available": True, "engine": f"TrOCR ({MODEL_ID})", "message": "TrOCR enabled; recognition quality depends on image and handwriting."}
    except Exception as exc:
        return {"available": False, "engine": "Tesseract fallback", "message": f"TrOCR dependencies are unavailable; using Tesseract fallback ({type(exc).__name__})."}


@lru_cache(maxsize=1)
def _load_model():
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    processor = TrOCRProcessor.from_pretrained(MODEL_ID)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID)
    model.eval()
    return processor, model


def _line_crops(image: np.ndarray) -> list[np.ndarray]:
    """Find broad handwritten text lines with horizontal projection; retain gray crops."""
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    h, w = gray.shape[:2]
    if max(h, w) > 2200:
        scale = 2200 / max(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    # Adaptive threshold handles uneven paper lighting better than global threshold.
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 13)
    row_ink = np.count_nonzero(binary, axis=1)
    threshold = max(2, int(gray.shape[1] * 0.003))
    active = row_ink > threshold
    # Bridge small gaps between cursive strokes within a text line.
    for i in range(1, len(active) - 1):
        if active[i-1] and active[i+1]: active[i] = True
    spans = []
    start = None
    for i, val in enumerate(active):
        if val and start is None: start = i
        elif not val and start is not None:
            if i - start >= max(5, int(gray.shape[0] * 0.006)): spans.append((start, i))
            start = None
    if start is not None and len(active) - start >= 5: spans.append((start, len(active)))
    if not spans: return [gray]
    # Merge neighboring spans separated by tiny gaps and crop with padding.
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] < max(8, int(gray.shape[0] * 0.012)):
            merged[-1] = (merged[-1][0], b)
        else: merged.append((a, b))
    pad = max(5, int(gray.shape[0] * 0.008))
    crops = [gray[max(0,a-pad):min(gray.shape[0],b+pad), :] for a,b in merged]
    return [c for c in crops if c.size and c.shape[0] >= 8 and c.shape[1] >= 30][:100] or [gray]


def recognize(image: np.ndarray) -> str:
    """Recognize text lines with TrOCR. Raises if disabled/unavailable for caller fallback."""
    if not ENABLED:
        raise RuntimeError("TrOCR is not enabled")
    processor, model = _load_model()
    lines = []
    for crop in _line_crops(image):
        rgb = cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB) if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        pixels = processor(images=pil, return_tensors="pt").pixel_values
        generated = model.generate(pixels, max_new_tokens=128)
        text = processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
        if text: lines.append(text)
    return "\n".join(lines).strip()
