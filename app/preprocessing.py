"""Conservative image variants for printed-document OCR."""

from __future__ import annotations

import cv2
import numpy as np


def decode_image(image_bytes: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("The image could not be decoded.")
    if image.shape[0] * image.shape[1] > 40_000_000:
        raise ValueError("Image dimensions exceed the 40-megapixel safety limit.")
    return image


def _rotate(image: np.ndarray, angle: float) -> np.ndarray:
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _estimate_skew(gray: np.ndarray) -> float | None:
    if max(gray.shape) > 1400:
        scale = 1400 / max(gray.shape)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 1800,
        threshold=max(30, gray.shape[1] // 12),
        minLineLength=max(40, gray.shape[1] // 8),
        maxLineGap=15,
    )
    if lines is None:
        return None
    angles = []
    for line in np.asarray(lines).reshape(-1, 4):
        x1, y1, x2, y2 = map(int, line)
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if abs(angle) <= 8:
            angles.append(angle)
    if not angles:
        return None
    angle = float(np.median(angles))
    return angle if 0.35 <= abs(angle) <= 8 else None


def preprocess_variants(image_bytes: bytes) -> list[tuple[str, np.ndarray]]:
    """Return the source plus gentle alternatives; do not replace the source."""
    image = decode_image(image_bytes)
    variants: list[tuple[str, np.ndarray]] = [("original", image)]
    height, width = image.shape[:2]
    if max(height, width) < 1800 and min(height, width) < 1100:
        scale = min(2.0, 1800 / max(height, width))
        if scale > 1.12:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    # A 3x3 median filter is predictable on large scans and retains thin
    # strokes better than aggressive denoising.
    denoised = cv2.medianBlur(clahe, 3)
    variants.append(("contrast_denoised", cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)))

    blur = cv2.GaussianBlur(denoised, (0, 0), 1.0)
    sharpened = cv2.addWeighted(denoised, 1.35, blur, -0.35, 0)
    _, otsu = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(("otsu", otsu))

    block_size = max(15, min(41, (min(gray.shape) // 30) | 1))
    adaptive = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size,
        11,
    )
    variants.append(("adaptive", adaptive))

    angle = _estimate_skew(denoised)
    if angle is not None:
        deskewed = _rotate(image, angle)
        variants.append(("deskewed", deskewed))

    return variants
