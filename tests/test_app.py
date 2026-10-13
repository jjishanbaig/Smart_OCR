from __future__ import annotations

import asyncio
from contextlib import ExitStack
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pymupdf as fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.main import app
from app.ocr_engine import engine_status, run_ocr
from app.optional_services import answer_question, summarize, translate
from app.preprocessing import preprocess_variants


def png_bytes(text: str = "SMART OCR") -> bytes:
    image = Image.new("RGB", (520, 180), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    draw.text((18, 52), text, font=font, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def pdf_bytes(*texts: str) -> bytes:
    document = fitz.open()
    for text in texts:
        page = document.new_page(width=612, height=792)
        if text:
            page.insert_text((72, 90), text, fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, bytes]:
    boundary = b"SmartOCRBoundary"
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            b"--" + boundary + b"\r\n",
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8") + b"\r\n",
        ])
    for field_name, filename, content, content_type in files:
        chunks.extend([
            b"--" + boundary + b"\r\n",
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content + b"\r\n",
        ])
    chunks.append(b"--" + boundary + b"--\r\n")
    return b"".join(chunks), boundary


def call_asgi(method: str, path: str, body: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None):
    messages = []
    received = False
    request_headers = list(headers or [])
    request_headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path.split("?", 1)[0],
        "raw_path": path.split("?", 1)[0].encode(),
        "query_string": path.split("?", 1)[1].encode() if "?" in path else b"",
        "root_path": "",
        "headers": request_headers,
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "app": app,
    }

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    payload = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    response_headers = {key.decode().lower(): value.decode() for key, value in start.get("headers", [])}
    return start["status"], response_headers, payload


def upload_request(path: str, filename: str, content: bytes, content_type: str, field: str = "file", mode_fields: dict[str, str] | None = None):
    body, boundary = multipart(mode_fields or {}, [(field, filename, content, content_type)])
    return call_asgi(
        "POST",
        path,
        body,
        [(b"content-type", b"multipart/form-data; boundary=" + boundary)],
    )


MOCK_ITEMS = [
    {"text": "Invoice", "confidence": 0.94, "box": [[20, 50], [160, 50], [160, 90], [20, 90]], "line": "1-1-1"},
    {"text": "Total", "confidence": 0.91, "box": [[170, 50], [260, 50], [260, 90], [170, 90]], "line": "1-1-1"},
]


class SmartOCRApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.upload_dir = base / "uploads"
        self.output_dir = base / "outputs"
        self.upload_dir.mkdir()
        self.output_dir.mkdir()
        self.stack = ExitStack()
        self.stack.enter_context(patch("app.main.UPLOAD_DIR", self.upload_dir))
        self.stack.enter_context(patch("app.main.OUTPUT_DIR", self.output_dir))
        self.stack.enter_context(patch("app.storage.UPLOAD_DIR", self.upload_dir))
        self.stack.enter_context(patch("app.storage.OUTPUT_DIR", self.output_dir))
        self.stack.enter_context(patch("app.main.MAX_UPLOAD_BYTES", 15 * 1024 * 1024))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def upload_image(self, content: bytes | None = None, endpoint: str = "/api/ocr"):
        content = content if content is not None else png_bytes()
        with patch("app.processing.run_ocr", return_value=MOCK_ITEMS):
            status, headers, payload = upload_request(endpoint, "scan.png", content, "image/png")
        return status, headers, json.loads(payload) if payload.startswith(b"{") else payload

    def test_health_reports_ocr_availability_without_breaking_api(self):
        status, _, body = call_asgi("GET", "/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn(data["status"], {"ok", "degraded"})
        self.assertIn("ocr", data)
        self.assertIn("handwriting", data["optional"])

    def test_valid_image_upload_returns_word_geometry_and_confidence_estimate(self):
        status, _, result = self.upload_image()
        self.assertEqual(status, 200)
        self.assertEqual(result["pages"][0]["source"], "ocr")
        self.assertEqual(result["pages"][0]["items"][0]["text"], "Invoice")
        self.assertGreater(result["average_confidence"], 0.8)
        self.assertIn("estimate", "Average OCR confidence estimate")
        self.assertTrue(result["pages"][0]["preview_url"].startswith("/api/jobs/"))

    def test_empty_upload_is_rejected(self):
        status, _, payload = upload_request("/api/ocr", "empty.png", b"", "image/png")
        self.assertEqual(status, 400)
        self.assertIn("empty", json.loads(payload)["detail"].lower())

    def test_extension_and_file_signature_must_match(self):
        status, _, payload = upload_request("/api/ocr", "scan.jpg", png_bytes(), "image/jpeg")
        self.assertEqual(status, 400)
        self.assertIn("extension", json.loads(payload)["detail"].lower())

    def test_oversized_upload_is_rejected_before_image_decode(self):
        with patch("app.main.MAX_UPLOAD_BYTES", 100):
            status, _, payload = upload_request("/api/ocr", "large.png", b"x" * 101, "image/png")
        self.assertEqual(status, 413)

    def test_corrupted_pdf_is_rejected(self):
        status, _, payload = upload_request("/api/ocr", "broken.pdf", b"%PDF-1.7\nnot a real pdf", "application/pdf")
        self.assertEqual(status, 400)
        self.assertIn("corrupt", json.loads(payload)["detail"].lower())

    def test_multipage_digital_pdf_uses_embedded_text_without_ocr(self):
        status, _, payload = upload_request("/api/ocr", "digital.pdf", pdf_bytes("First digital page", "Second digital page"), "application/pdf")
        self.assertEqual(status, 200)
        result = json.loads(payload)
        self.assertEqual(len(result["pages"]), 2)
        self.assertTrue(all(page["source"] == "embedded_text" for page in result["pages"]))
        self.assertIn("Second digital page", result["text"])

    def test_scanned_pdf_page_uses_ocr_fallback(self):
        image = Image.open(io.BytesIO(png_bytes("SCANNED PAGE")))
        encoded = io.BytesIO(); image.save(encoded, format="PNG")
        document = fitz.open(); page = document.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=encoded.getvalue())
        scanned_pdf = document.tobytes(); document.close()
        with patch("app.processing.run_ocr", return_value=MOCK_ITEMS) as mock_ocr:
            status, _, payload = upload_request("/api/ocr", "scan.pdf", scanned_pdf, "application/pdf")
        self.assertEqual(status, 200)
        result = json.loads(payload)
        self.assertEqual(result["pages"][0]["source"], "ocr")
        self.assertGreaterEqual(mock_ocr.call_count, 1)

    def test_batch_endpoint_processes_multiple_files(self):
        body, boundary = multipart(
            {"language": "eng", "psm": "6"},
            [
                ("files", "first.png", png_bytes("FIRST"), "image/png"),
                ("files", "second.png", png_bytes("SECOND"), "image/png"),
            ],
        )
        with patch("app.processing.run_ocr", return_value=MOCK_ITEMS):
            status, _, payload = call_asgi(
                "POST", "/api/ocr/batch", body,
                [(b"content-type", b"multipart/form-data; boundary=SmartOCRBoundary")],
            )
        result = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(len(result["jobs"]), 2)

    def test_pdf_page_can_be_rotated_and_reprocessed(self):
        status, _, payload = upload_request("/api/ocr", "digital.pdf", pdf_bytes("Digital page"), "application/pdf")
        self.assertEqual(status, 200)
        job_id = json.loads(payload)["job_id"]
        request = json.dumps({"rotation": 90, "language": "eng", "psm": 6}).encode()
        with patch("app.processing.run_ocr", return_value=MOCK_ITEMS):
            status, _, result = call_asgi(
                "POST", f"/api/jobs/{job_id}/pages/1/ocr", request,
                [(b"content-type", b"application/json")],
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(result)["pages"][0]["rotation"], 90)

    def test_page_selection_runs_ocr_only_on_requested_scanned_page(self):
        image = Image.open(io.BytesIO(png_bytes("PAGE IMAGE")))
        encoded = io.BytesIO(); image.save(encoded, format="PNG")
        document = fitz.open()
        for _ in range(2):
            page = document.new_page(width=612, height=792)
            page.insert_image(page.rect, stream=encoded.getvalue())
        scanned_pdf = document.tobytes(); document.close()
        with patch("app.processing.run_ocr", return_value=MOCK_ITEMS) as mock_ocr:
            status, _, payload = upload_request("/api/ocr", "two_scanned_pages.pdf", scanned_pdf, "application/pdf", mode_fields={"pages": "2"})
        result = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["pages"][0]["status"], "not_selected")
        self.assertEqual(result["pages"][1]["status"], "complete")
        self.assertGreaterEqual(mock_ocr.call_count, 1)

    def test_prescription_mode_returns_literal_review_candidates(self):
        item = {"text": "Amoxicillin 500 mg twice daily for 5 days after food", "confidence": 0.55,
                "box": [[8, 20], [500, 20], [500, 70], [8, 70]], "line": "1-1-1"}
        with patch("app.processing.run_ocr", return_value=[item]):
            status, _, payload = upload_request("/api/prescription", "rx.png", png_bytes(), "image/png")
        result = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertTrue(result["prescription"]["candidates"][0]["requires_review"])
        self.assertEqual(result["prescription"]["candidates"][0]["medicine_candidate"], "Amoxicillin")
        self.assertEqual(result["prescription"]["candidates"][0]["frequency_candidate"], "twice daily")
        self.assertIn("unverified", result["prescription"]["warning"].lower())

    def test_encrypted_pdf_has_a_clear_error(self):
        document = fitz.open(); page = document.new_page(); page.insert_text((72, 72), "Private")
        protected = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
        document.close()
        status, _, payload = upload_request("/api/ocr", "locked.pdf", protected, "application/pdf")
        self.assertEqual(status, 400)
        self.assertIn("password-protected", json.loads(payload)["detail"])

    def test_frontend_assets_load_and_all_script_ids_exist(self):
        from html.parser import HTMLParser

        class IdParser(HTMLParser):
            def __init__(self):
                super().__init__(); self.ids = set()
            def handle_starttag(self, _tag, attrs):
                self.ids.update(value for key, value in attrs if key == "id")

        status, _, html = call_asgi("GET", "/")
        self.assertEqual(status, 200)
        parser = IdParser(); parser.feed(html.decode("utf-8"))
        script = (Path(__file__).parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        references = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', script))
        self.assertFalse(references - parser.ids, references - parser.ids)
        for asset in ("/static/app.js", "/static/app.css"):
            asset_status, _, content = call_asgi("GET", asset)
            self.assertEqual(asset_status, 200)
            self.assertTrue(content)

    def test_low_quality_preprocessing_keeps_source_and_variants(self):
        image = Image.open(io.BytesIO(png_bytes("LOW QUALITY"))).filter(ImageFilter.GaussianBlur(2.5))
        encoded = io.BytesIO(); image.save(encoded, format="PNG")
        variants = preprocess_variants(encoded.getvalue())
        names = [name for name, _ in variants]
        self.assertEqual(names[0], "original")
        self.assertIn("contrast_denoised", names)
        self.assertIn("adaptive", names)
        self.assertTrue(all(variant.size for _, variant in variants))

    def test_corrections_do_not_modify_the_uploaded_original(self):
        status, _, result = self.upload_image()
        self.assertEqual(status, 200)
        job_id = result["job_id"]
        source = self.upload_dir / f"{job_id}.png"
        original_bytes = source.read_bytes()
        patch_body = json.dumps({"text": "Reviewed invoice", "pages": {"1": "Reviewed invoice"}}).encode()
        status, _, payload = call_asgi("PATCH", f"/api/jobs/{job_id}/text", patch_body, [(b"content-type", b"application/json")])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["text"], "Reviewed invoice")
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_txt_docx_json_csv_and_searchable_pdf_exports(self):
        status, _, result = self.upload_image()
        self.assertEqual(status, 200)
        job_id = result["job_id"]
        formats = {
            "txt": b"Page 1",
            "docx": b"PK",
            "json": b'"pages"',
            "csv": b"page,row,column,value",
            "searchable-pdf": b"%PDF",
        }
        bodies = {}
        for kind, marker in formats.items():
            export_status, headers, body = call_asgi("GET", f"/api/jobs/{job_id}/export/{kind}")
            self.assertEqual(export_status, 200, kind)
            self.assertIn(marker, body, kind)
            bodies[kind] = body
        with zipfile.ZipFile(io.BytesIO(bodies["docx"])) as docx:
            self.assertIn(b"Invoice", docx.read("word/document.xml"))
        searchable = fitz.open(stream=bodies["searchable-pdf"], filetype="pdf")
        self.assertIn("Invoice", searchable[0].get_text("text"))
        searchable.close()

    def test_optional_ai_and_translation_return_clear_unconfigured_errors(self):
        status, _, result = self.upload_image()
        self.assertEqual(status, 200)
        with patch("app.optional_services.AI_API_KEY", ""), patch("app.optional_services.TRANSLATION_URL", ""):
            ai_status, _, ai_body = call_asgi("POST", f"/api/jobs/{result['job_id']}/ai/summary")
            tr_status, _, tr_body = call_asgi("POST", "/api/translate", json.dumps({"text": "Invoice", "target_language": "es"}).encode(), [(b"content-type", b"application/json")])
        self.assertEqual(ai_status, 503)
        self.assertIn("not configured", json.loads(ai_body)["detail"].lower())
        self.assertEqual(tr_status, 503)
        self.assertIn("not configured", json.loads(tr_body)["detail"].lower())

    def test_form_fields_are_literal_label_value_pairs(self):
        job = {
            "pages": [{"page": 1, "text": "Name: Ada Lovelace\nAmount: 25.00\nNo value here"}],
        }
        from app.table_service import extract_form_fields
        extracted = extract_form_fields(job["pages"])
        self.assertEqual(extracted["fields"][0]["label"], "Name")
        self.assertEqual(extracted["fields"][1]["value"], "25.00")

    def test_table_geometry_groups_cells_without_filling_missing_values(self):
        from app.table_service import extract_table

        def word(text, x, y, line):
            return {"text": text, "line": line, "box": [[x, y], [x + 30, y], [x + 30, y + 12], [x, y + 12]]}

        result = extract_table([
            word("Item", 0, 0, "1"), word("Price", 100, 0, "1"),
            word("Book", 0, 20, "2"), word("10", 100, 20, "2"),
        ])
        self.assertEqual(result["rows"], [["Item", "Price"], ["Book", "10"]])
        self.assertIn("inferred", result["warning"])

    def test_configured_ai_and_translation_use_passage_and_original_text(self):
        pages = [{"page": 2, "text": "Invoice total is 42 dollars."}]
        with patch("app.optional_services.AI_API_KEY", "test-key"), patch("app.optional_services._chat", return_value="The total is 42 dollars [Page 2]."):
            answer = answer_question(pages, "What is the invoice total?")
            summary_result = summarize(pages)
        self.assertTrue(answer["supported"])
        self.assertEqual(answer["sources"][0]["page"], 2)
        self.assertIn("42 dollars", summary_result["summary"])
        with patch("app.optional_services.TRANSLATION_URL", "https://translation.invalid/translate"), patch("app.optional_services._post_json", return_value={"translatedText": "Total: 42 dollars"}):
            translated = translate("Invoice total is 42 dollars.", "es")
        self.assertEqual(translated["original_text"], "Invoice total is 42 dollars.")
        self.assertEqual(translated["translated_text"], "Total: 42 dollars")

    def test_dashboard_history_and_delete_use_real_job_data(self):
        status, _, result = self.upload_image()
        self.assertEqual(status, 200)
        job_id = result["job_id"]
        dashboard_status, _, dashboard_body = call_asgi("GET", "/api/dashboard")
        history_status, _, history_body = call_asgi("GET", "/api/history?search=scan.png")
        self.assertEqual(dashboard_status, 200)
        self.assertEqual(history_status, 200)
        self.assertEqual(json.loads(dashboard_body)["total_documents"], 1)
        self.assertEqual(json.loads(history_body)["documents"][0]["job_id"], job_id)
        delete_status, _, delete_body = call_asgi("DELETE", f"/api/jobs/{job_id}")
        self.assertEqual(delete_status, 200)
        self.assertTrue(json.loads(delete_body)["deleted"])


@unittest.skipUnless(engine_status()["available"], "Tesseract executable or language data is unavailable")
class TesseractIntegrationTests(unittest.TestCase):
    def test_printed_text_ocr_returns_nonempty_word_confidence_estimates(self):
        image = Image.new("RGB", (700, 180), "white")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 42)
        except OSError:
            font = ImageFont.load_default()
        draw.text((20, 55), "SMART OCR TEST", fill="black", font=font)
        data = io.BytesIO(); image.save(data, format="PNG")
        items = run_ocr(data.getvalue(), language="eng", psm=6)
        self.assertTrue(items)
        self.assertTrue(all(0 <= item["confidence"] <= 1 for item in items))


if __name__ == "__main__":
    unittest.main()
