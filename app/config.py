"""Runtime configuration for the local SmartOCR service."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

try:
    from dotenv import load_dotenv
except ImportError:  # .env support stays optional for direct source use.
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
FRONTEND_DIR = BASE_DIR / "frontend"

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

MAX_UPLOAD_BYTES = max(1, int(os.getenv("SMARTOCR_MAX_UPLOAD_MB", "15"))) * 1024 * 1024
MAX_BATCH_BYTES = max(1, int(os.getenv("SMARTOCR_MAX_BATCH_MB", "75"))) * 1024 * 1024
MAX_PDF_PAGES = max(1, int(os.getenv("SMARTOCR_MAX_PDF_PAGES", "50")))
PDF_RENDER_DPI = min(300, max(100, int(os.getenv("SMARTOCR_PDF_DPI", "180"))))
JOB_RETENTION_HOURS = max(1, int(os.getenv("SMARTOCR_RETENTION_HOURS", "24")))
DEFAULT_OCR_LANGUAGE = os.getenv("TESSERACT_LANG", "eng")
DEFAULT_OCR_PSM = min(13, max(0, int(os.getenv("TESSERACT_PSM", "3"))))

TESSERACT_CMD = os.getenv("TESSERACT_CMD", "").strip()
if TESSERACT_CMD:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
elif not shutil.which("tesseract"):
    _windows_tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if _windows_tesseract.is_file():
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = str(_windows_tesseract)

AI_PROVIDER = os.getenv("SMARTOCR_AI_PROVIDER", "auto").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
_COMPAT_API_KEY = os.getenv("SMARTOCR_AI_API_KEY", "").strip()
if AI_PROVIDER == "auto":
    AI_PROVIDER = "gemini" if GEMINI_API_KEY else ("openai-compatible" if _COMPAT_API_KEY else "none")

if AI_PROVIDER == "gemini":
    AI_API_KEY = GEMINI_API_KEY or _COMPAT_API_KEY
    _default_ai_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    _default_ai_model = "gemini-3.8-flash"
elif AI_PROVIDER in {"openai", "openai-compatible"}:
    AI_API_KEY = _COMPAT_API_KEY or GEMINI_API_KEY
    _default_ai_base_url = "https://api.openai.com/v1"
    _default_ai_model = "gpt-4o-mini"
else:
    AI_API_KEY = ""
    _default_ai_base_url = "https://api.openai.com/v1"
    _default_ai_model = "gpt-4o-mini"

AI_API_BASE_URL = (os.getenv("SMARTOCR_AI_API_BASE_URL", "").strip() or _default_ai_base_url).rstrip("/")
AI_MODEL = os.getenv("SMARTOCR_AI_MODEL", _default_ai_model).strip()
TRANSLATION_URL = os.getenv("SMARTOCR_TRANSLATION_URL", "").strip()
TRANSLATION_API_KEY = os.getenv("SMARTOCR_TRANSLATION_API_KEY", "").strip()

for _directory in (UPLOAD_DIR, OUTPUT_DIR):
    _directory.mkdir(parents=True, exist_ok=True)
