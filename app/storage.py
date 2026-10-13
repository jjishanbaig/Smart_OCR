"""Short-lived local job storage with atomic writes and explicit deletion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import threading

import cv2
import numpy as np

from .config import JOB_RETENTION_HOURS, OUTPUT_DIR, UPLOAD_DIR

_JOB_ID = re.compile(r"^[a-f0-9]{32}$")
_write_lock = threading.RLock()
_active_jobs: set[str] = set()


def mark_job_active(job_id: str, active: bool) -> None:
    if not valid_job_id(job_id):
        return
    with _write_lock:
        if active:
            _active_jobs.add(job_id)
        else:
            _active_jobs.discard(job_id)


def valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID.fullmatch(job_id or ""))


def job_path(job_id: str) -> Path:
    if not valid_job_id(job_id):
        raise ValueError("Invalid document identifier.")
    return OUTPUT_DIR / f"{job_id}.json"


def write_job(job: dict) -> None:
    path = job_path(job["job_id"])
    temporary = path.with_suffix(".json.tmp")
    with _write_lock:
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def read_job(job_id: str) -> dict | None:
    try:
        path = job_path(job_id)
        with _write_lock:
            if not path.is_file():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError):
        return None


def source_path(job: dict) -> Path:
    return UPLOAD_DIR / f"{job['job_id']}{job['file_extension']}"


def preview_path(job_id: str, page: int, kind: str) -> Path:
    if not valid_job_id(job_id) or page < 1 or kind not in {"original", "processed"}:
        raise ValueError("Invalid preview request.")
    return OUTPUT_DIR / f"{job_id}_p{page}_{kind}.jpg"


def save_preview(job_id: str, page: int, kind: str, image: np.ndarray) -> None:
    path = preview_path(job_id, page, kind)
    height, width = image.shape[:2]
    scale = min(1.0, 760 / max(height, width))
    if scale < 1:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not success:
        raise ValueError("Could not create a page preview.")
    path.write_bytes(encoded.tobytes())


def _remove_job_files(job_id: str, job: dict | None = None) -> None:
    if not valid_job_id(job_id):
        return
    if job:
        source = source_path(job)
        source.unlink(missing_ok=True)
    else:
        for path in UPLOAD_DIR.glob(f"{job_id}.*"):
            if path.is_file():
                path.unlink(missing_ok=True)
    job_path(job_id).unlink(missing_ok=True)
    for path in OUTPUT_DIR.glob(f"{job_id}_p*_*.jpg"):
        if path.is_file():
            path.unlink(missing_ok=True)


def delete_job(job_id: str) -> bool:
    job = read_job(job_id)
    if job is None:
        return False
    with _write_lock:
        _remove_job_files(job_id, job)
    return True


def cleanup_expired(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=JOB_RETENTION_HOURS)
    removed = 0
    for path in OUTPUT_DIR.glob("[a-f0-9][a-f0-9]*.json"):
        job_id = path.stem
        if not valid_job_id(job_id):
            continue
        job = read_job(job_id)
        try:
            created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00")) if job else None
        except (ValueError, TypeError, KeyError):
            created = None
        if created is None or created < cutoff:
            with _write_lock:
                _remove_job_files(job_id, job)
            removed += 1
    # Recover files left behind when the process stops during an upload.
    for path in UPLOAD_DIR.glob("*.part"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    orphan_cutoff = now - timedelta(hours=1)
    for path in list(UPLOAD_DIR.iterdir()) + list(OUTPUT_DIR.glob("*_p*_*.jpg")):
        job_id = path.name.split("_p", 1)[0].split(".", 1)[0]
        if not valid_job_id(job_id) or job_path(job_id).exists() or job_id in _active_jobs:
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < orphan_cutoff:
                _remove_job_files(job_id)
                removed += 1
        except OSError:
            continue
    return removed


def all_jobs() -> list[dict]:
    jobs = []
    for path in OUTPUT_DIR.glob("[a-f0-9][a-f0-9]*.json"):
        if not valid_job_id(path.stem):
            continue
        job = read_job(path.stem)
        if job is not None:
            jobs.append(job)
    return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)


def public_job(job: dict) -> dict:
    """Return the API-safe fields; never expose local filesystem paths."""
    job_id = job["job_id"]
    result = {
        "success": True,
        "job_id": job_id,
        "filename": job["filename"],
        "created_at": job["created_at"],
        "mode": job.get("mode", "ocr"),
        "pages": [],
        "text": job.get("corrected_text", job.get("text", "")),
        "original_text": job.get("text", ""),
        "word_count": len(job.get("corrected_text", job.get("text", "")).split()),
        "character_count": len(job.get("corrected_text", job.get("text", ""))),
        "average_confidence": job.get("average_confidence"),
        "duration_seconds": job.get("duration_seconds", 0),
        "warnings": job.get("warnings", []),
        "download_url": f"/api/download/{job_id}",
        "exports": {"txt": f"/api/jobs/{job_id}/export/txt", "docx": f"/api/jobs/{job_id}/export/docx", "json": f"/api/jobs/{job_id}/export/json", "csv": f"/api/jobs/{job_id}/export/csv", "searchable_pdf": f"/api/jobs/{job_id}/export/searchable-pdf"},
    }
    corrected_pages = job.get("corrected_pages", {})
    for page in job.get("pages", []):
        page_number = page["page"]
        result["pages"].append(
            {
                **page,
                "original_text": page.get("text", ""),
                "corrected_text": corrected_pages.get(str(page_number), page.get("text", "")),
                "preview_url": f"/api/jobs/{job_id}/preview/{page_number}?kind=original" if page.get("has_preview", True) else None,
                "processed_preview_url": f"/api/jobs/{job_id}/preview/{page_number}?kind=processed" if page.get("has_processed_preview") else None,
                "has_processed_preview": None,
            }
        )
    for page in result["pages"]:
        page.pop("has_processed_preview", None)
    if job.get("prescription"):
        result["prescription"] = job["prescription"]
    return result
