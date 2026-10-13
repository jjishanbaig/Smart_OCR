"""FastAPI routes for SmartOCR's local document workflow."""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from pytesseract.pytesseract import TesseractNotFoundError
from starlette.concurrency import run_in_threadpool

from .config import (
    AI_API_KEY,
    AI_MODEL,
    AI_PROVIDER,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_OCR_PSM,
    FRONTEND_DIR,
    JOB_RETENTION_HOURS,
    MAX_BATCH_BYTES,
    MAX_PDF_PAGES,
    MAX_UPLOAD_BYTES,
    OUTPUT_DIR,
    TRANSLATION_URL,
    UPLOAD_DIR,
)
from .export_service import create_export
from .handwriting_service import status as handwriting_status
from .ocr_engine import engine_status
from .optional_services import (
    OptionalServiceUnavailable,
    answer_question,
    assistant_chat,
    detect_script,
    summarize,
    translate,
)
from .prescription_service import extract_candidates
from .processing import DocumentProcessingError, process_document, reprocess_page, validate_content
from .storage import (
    all_jobs,
    cleanup_expired,
    delete_job,
    mark_job_active,
    preview_path,
    public_job,
    read_job,
    write_job,
)
from .table_service import extract_form_fields, extract_table

logger = logging.getLogger("smartocr")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".pdf", ".csv", ".xlsx"}
MAX_FILENAME_LENGTH = 120


class TextUpdate(BaseModel):
    text: str | None = Field(default=None, max_length=1_000_000)
    pages: dict[str, str] | None = None

    @model_validator(mode="after")
    def limit_page_text(self):
        if self.pages is not None and sum(len(value) for value in self.pages.values()) > 1_000_000:
            raise ValueError("Combined corrected page text cannot exceed 1,000,000 characters.")
        return self


class PageRerun(BaseModel):
    rotation: int = Field(default=0)
    language: str | None = None
    psm: int | None = Field(default=None, ge=0, le=13)


class AIQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[AssistantMessage] = Field(default_factory=list, max_length=12)
    document_context: str | None = Field(default=None, max_length=30000)


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    target_language: str = Field(min_length=2, max_length=40)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_expired()
    async def hourly_cleanup():
        while True:
            await asyncio.sleep(3600)
            await run_in_threadpool(cleanup_expired)

    cleanup_task = asyncio.create_task(hourly_cleanup())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="SmartOCR",
    description="Local OCR and document extraction with optional AI integrations.",
    version="2.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.exception_handler(Exception)
async def unexpected_error(_request, exc: Exception):
    logger.exception("Unhandled SmartOCR request failure", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "The request could not be completed. Check the server log for details."})


def safe_name(filename: str) -> str:
    basename = Path((filename or "document").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", basename)
    return cleaned[:MAX_FILENAME_LENGTH] or "document"


def _parse_selected_pages(value: str | None) -> set[int] | None:
    if value is None or not value.strip():
        return None
    try:
        pages = {int(part.strip()) for part in value.split(",") if part.strip()}
    except ValueError as exc:
        raise HTTPException(400, "Page selection must be a comma-separated list of page numbers.") from exc
    if not pages or min(pages) < 1:
        raise HTTPException(400, "Page numbers must be positive integers.")
    if max(pages) > MAX_PDF_PAGES:
        raise HTTPException(400, f"Page numbers cannot exceed the {MAX_PDF_PAGES}-page PDF limit.")
    return pages


async def _save_upload(upload: UploadFile) -> tuple[str, str, Path]:
    if not upload.filename:
        raise HTTPException(400, "Filename is required.")
    filename = safe_name(upload.filename)
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Unsupported file type. Use JPG, JPEG, PNG, WEBP, BMP, PDF, CSV or XLSX.")
    job_id = uuid4().hex
    temporary = UPLOAD_DIR / f"{job_id}.part"
    final_path = UPLOAD_DIR / f"{job_id}{extension}"
    size = 0
    try:
        with temporary.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"Maximum upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                target.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty.")
        validate_content(temporary, extension)
        temporary.replace(final_path)
        return job_id, filename, final_path
    except DocumentProcessingError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        logger.exception("Could not store an upload")
        raise HTTPException(500, "The upload could not be stored.") from exc
    finally:
        await upload.close()


async def _process_upload(
    upload: UploadFile,
    language: str | None,
    psm: int | None,
    selected_pages: str | None,
    mode: str = "ocr",
) -> dict:
    page_selection = _parse_selected_pages(selected_pages)
    job_id, filename, path = await _save_upload(upload)
    mark_job_active(job_id, True)
    try:
        job = await run_in_threadpool(
            process_document,
            path,
            job_id,
            filename,
            path.suffix.lower(),
            language,
            psm,
            page_selection,
            mode,
        )
        if page_selection and max(page_selection) > len(job["pages"]):
            raise DocumentProcessingError("A selected page number is outside this document.")
        if mode == "prescription":
            job["prescription"] = extract_candidates(job["pages"])
        write_job(job)
        return public_job(job)
    except DocumentProcessingError as exc:
        _remove_uncommitted_files(job_id, path)
        raise HTTPException(400, str(exc)) from exc
    except TesseractNotFoundError as exc:
        _remove_uncommitted_files(job_id, path)
        raise HTTPException(503, "Tesseract OCR is not installed or could not be started. Check the OCR status and TESSERACT_CMD setting.") from exc
    except HTTPException:
        _remove_uncommitted_files(job_id, path)
        raise
    except Exception as exc:
        _remove_uncommitted_files(job_id, path)
        logger.exception("Document processing failed for job %s", job_id)
        raise HTTPException(500, "Document processing failed. The upload was discarded.") from exc
    finally:
        mark_job_active(job_id, False)


def _remove_uncommitted_files(job_id: str, path: Path) -> None:
    path.unlink(missing_ok=True)
    for preview in OUTPUT_DIR.glob(f"{job_id}_p*_*.jpg"):
        preview.unlink(missing_ok=True)


def _job_or_404(job_id: str) -> dict:
    cleanup_expired()
    job = read_job(job_id)
    if job is None:
        raise HTTPException(404, "Document result was not found or has expired.")
    return job


@app.get("/")
async def home():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/api/health")
async def health():
    engine = engine_status()
    return {
        "status": "ok" if engine["available"] else "degraded",
        "service": "SmartOCR",
        "ocr": engine,
        "optional": {
            "handwriting": handwriting_status(),
            "ai": {"available": True, "mode": "external" if AI_API_KEY else "local extractive", "provider": AI_PROVIDER, "provider_configured": bool(AI_API_KEY), "model_configured": bool(AI_MODEL)},
            "translation": {"available": bool(TRANSLATION_URL), "external_service_configured": bool(TRANSLATION_URL)},
        },
    }


@app.get("/api/config")
async def client_config():
    engine = engine_status()
    return {
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_batch_bytes": MAX_BATCH_BYTES,
        "max_batch_mb": MAX_BATCH_BYTES // (1024 * 1024),
        "max_pdf_pages": MAX_PDF_PAGES,
        "retention_hours": JOB_RETENTION_HOURS,
        "supported_extensions": sorted(ALLOWED_EXTENSIONS),
        "ocr_languages": engine["languages"],
        "default_language": DEFAULT_OCR_LANGUAGE,
        "default_psm": DEFAULT_OCR_PSM,
        "ocr_available": engine["available"],
        "handwriting": handwriting_status(),
        "ai_available": True,
        "ai_provider_available": bool(AI_API_KEY),
        "ai_provider": AI_PROVIDER,
        "translation_available": bool(TRANSLATION_URL),
    }


@app.post("/api/ocr")
async def extract_text(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    psm: int | None = Form(default=None),
    pages: str | None = Form(default=None),
    mode: str = Form(default="ocr"),
):
    if mode not in {"ocr", "handwriting"}:
        raise HTTPException(400, "Choose printed text OCR or handwriting mode.")
    cleanup_expired()
    return await _process_upload(file, language, psm, pages, mode=mode)


@app.post("/api/ocr/batch")
async def extract_batch(
    files: list[UploadFile] = File(...),
    language: str | None = Form(default=None),
    psm: int | None = Form(default=None),
    mode: str = Form(default="ocr"),
):
    if mode not in {"ocr", "handwriting"}:
        for upload in files:
            await upload.close()
        raise HTTPException(400, "Choose printed text OCR or handwriting mode.")
    if not files:
        raise HTTPException(400, "Choose at least one file.")
    if len(files) > 20:
        raise HTTPException(413, "Batch uploads are limited to 20 documents.")
    reported_size = sum(getattr(upload, "size", 0) or 0 for upload in files)
    if reported_size > MAX_BATCH_BYTES:
        for upload in files:
            await upload.close()
        raise HTTPException(413, f"Total batch size exceeds {MAX_BATCH_BYTES // (1024 * 1024)} MB.")
    cleanup_expired()
    results = []
    errors = []
    for upload in files:
        try:
            results.append(await _process_upload(upload, language, psm, None, mode=mode))
        except HTTPException as exc:
            errors.append({"filename": safe_name(upload.filename or "document"), "status": exc.status_code, "message": str(exc.detail)})
    return {"success": bool(results), "jobs": results, "errors": errors, "processed": len(results), "failed": len(errors)}


@app.post("/api/prescription")
async def process_prescription(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    psm: int | None = Form(default=None),
    pages: str | None = Form(default=None),
):
    cleanup_expired()
    return await _process_upload(file, language, psm, pages, mode="prescription")


@app.get("/api/history")
async def history(search: str = "", limit: int = 50):
    cleanup_expired()
    limit = min(100, max(1, limit))
    query = search.strip().casefold()
    jobs = all_jobs()
    if query:
        jobs = [job for job in jobs if query in job.get("filename", "").casefold() or query in job.get("text", "").casefold() or query in job.get("corrected_text", "").casefold()]
    return {"documents": [
        {
            "job_id": job["job_id"],
            "filename": job["filename"],
            "created_at": job["created_at"],
            "pages": len(job.get("pages", [])),
            "mode": job.get("mode", "ocr"),
            "average_confidence": job.get("average_confidence"),
            "duration_seconds": job.get("duration_seconds", 0),
            "status": "failed" if all(page.get("status") == "failed" for page in job.get("pages", [])) else "complete",
            "job_id_url": f"/api/jobs/{job['job_id']}",
        }
        for job in jobs[:limit]
    ], "retention_hours": JOB_RETENTION_HOURS, "total_matches": len(jobs)}


@app.get("/api/dashboard")
async def dashboard():
    cleanup_expired()
    jobs = all_jobs()
    pages = [page for job in jobs for page in job.get("pages", [])]
    successful = [page for page in pages if page.get("status") in {"complete", "embedded"}]
    failed = [page for page in pages if page.get("status") in {"failed", "empty"}]
    by_type: dict[str, int] = {}
    for job in jobs:
        extension = job.get("file_extension", "unknown").lstrip(".").upper()
        by_type[extension] = by_type.get(extension, 0) + 1
    confidences = [page["confidence"] for page in pages if page.get("confidence") is not None]
    return {
        "total_documents": len(jobs),
        "total_pages": len(pages),
        "successful_pages": len(successful),
        "failed_pages": len(failed),
        "average_confidence_estimate": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "average_duration_seconds": round(sum(job.get("duration_seconds", 0) for job in jobs) / len(jobs), 3) if jobs else 0,
        "document_types": by_type,
        "recent_documents": (await history(limit=8))["documents"],
        "retention_hours": JOB_RETENTION_HOURS,
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return public_job(_job_or_404(job_id))


@app.patch("/api/jobs/{job_id}/text")
async def update_text(job_id: str, update: TextUpdate):
    job = _job_or_404(job_id)
    if update.text is None and update.pages is None:
        raise HTTPException(400, "Provide corrected document text or corrected page text.")
    valid_pages = {str(page["page"]) for page in job.get("pages", [])}
    if update.pages is not None and any(number not in valid_pages for number in update.pages):
        raise HTTPException(400, "A corrected page number is outside this document.")
    if update.pages is not None:
        job.setdefault("corrected_pages", {}).update(update.pages)
    if update.text is not None:
        job["corrected_text"] = update.text
    elif update.pages is not None:
        job["corrected_text"] = "\n\n".join(
            f"--- Page {page['page']} ---\n{job.get('corrected_pages', {}).get(str(page['page']), page.get('text', ''))}"
            for page in job.get("pages", [])
            if job.get("corrected_pages", {}).get(str(page['page']), page.get("text", "")).strip()
        )
    write_job(job)
    return public_job(job)


@app.post("/api/jobs/{job_id}/pages/{page_number}/ocr")
async def ocr_page(job_id: str, page_number: int, settings: PageRerun):
    job = _job_or_404(job_id)
    try:
        updated = await run_in_threadpool(reprocess_page, job, page_number, settings.rotation, settings.language, settings.psm)
    except DocumentProcessingError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TesseractNotFoundError as exc:
        raise HTTPException(503, "Tesseract OCR is unavailable.") from exc
    except Exception as exc:
        logger.exception("Page reprocessing failed for job %s", job_id)
        raise HTTPException(500, "The selected page could not be reprocessed.") from exc
    write_job(updated)
    return public_job(updated)


@app.get("/api/jobs/{job_id}/preview/{page_number}")
async def get_preview(job_id: str, page_number: int, kind: str = "original"):
    job = _job_or_404(job_id)
    if page_number < 1 or page_number > len(job.get("pages", [])):
        raise HTTPException(404, "Page was not found.")
    if kind not in {"original", "processed"}:
        raise HTTPException(400, "Preview type must be original or processed.")
    page = preview_path(job_id, page_number, kind)
    if not page.is_file():
        raise HTTPException(404, "This preview is not available.")
    return FileResponse(page, media_type="image/jpeg", filename=f"page_{page_number}_{kind}.jpg")


@app.get("/api/jobs/{job_id}/tables")
async def tables(job_id: str):
    job = _job_or_404(job_id)
    return {
        "tables": [
            (
                {"page": page["page"], "name": page.get("sheet_name"), "rows": page["sheet_rows"], "warning": "Imported worksheet cells. Review the values before exporting."}
                if page.get("sheet_rows") is not None
                else {"page": page["page"], **extract_table(page.get("items", []))}
            )
            for page in job.get("pages", [])
        ],
        "warning": (
            "Worksheet cells are imported as stored. Review them against the original workbook."
            if job.get("mode") == "spreadsheet"
            else "OCR table rows are inferred from word spacing and may be ambiguous. Verify every cell against the source page."
        ),
    }


@app.get("/api/jobs/{job_id}/forms")
async def form_fields(job_id: str):
    job = _job_or_404(job_id)
    return extract_form_fields(job.get("pages", []))


@app.get("/api/jobs/{job_id}/prescription")
async def prescription(job_id: str):
    job = _job_or_404(job_id)
    return job.get("prescription") or extract_candidates(job.get("pages", []))


@app.post("/api/jobs/{job_id}/ai/summary")
async def ai_summary(job_id: str):
    job = _job_or_404(job_id)
    try:
        return await run_in_threadpool(summarize, job.get("pages", []))
    except OptionalServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/jobs/{job_id}/ai/ask")
async def ai_question(job_id: str, request: AIQuestion):
    job = _job_or_404(job_id)
    try:
        return await run_in_threadpool(answer_question, job.get("pages", []), request.question)
    except OptionalServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/assistant/chat")
async def chat_assistant(request: AssistantChatRequest):
    if not request.message.strip():
        raise HTTPException(400, "Enter a question for the assistant.")
    try:
        history = [message.model_dump() for message in request.history]
        return await run_in_threadpool(assistant_chat, request.message, history, request.document_context)
    except OptionalServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/translate")
async def translate_text(request: TranslationRequest):
    try:
        return await run_in_threadpool(translate, request.text, request.target_language)
    except OptionalServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/jobs/{job_id}/language")
async def language(job_id: str):
    job = _job_or_404(job_id)
    detected = detect_script(job.get("corrected_text", job.get("text", "")))
    return {**detected, "ocr_language": job.get("language", DEFAULT_OCR_LANGUAGE)}


@app.get("/api/jobs/{job_id}/export/{kind}")
async def export_document(job_id: str, kind: str, corrected: bool = True):
    job = _job_or_404(job_id)
    try:
        exported = await run_in_threadpool(create_export, job, kind.lower(), corrected)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(410 if "expired" in str(exc).casefold() else 400, str(exc)) from exc
    return Response(
        content=exported.data,
        media_type=exported.media_type,
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )


@app.get("/api/download/{job_id}")
async def download_text(job_id: str):
    job = _job_or_404(job_id)
    exported = create_export(job, "txt", corrected=True)
    return Response(
        content=exported.data,
        media_type=exported.media_type,
        headers={"Content-Disposition": f'attachment; filename="{Path(job["filename"]).stem}_text.txt"'},
    )


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str):
    if not delete_job(job_id):
        raise HTTPException(404, "Document result was not found or has expired.")
    return {"deleted": True, "job_id": job_id}
