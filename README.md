# SmartOCR

SmartOCR is a local FastAPI document workspace for OCR, scanned and digital PDFs, CSV and XLSX spreadsheet import, editable results, exports, and short-lived OCR history. Tesseract remains the primary OCR engine. The app starts without an AI key, translation endpoint, downloaded handwriting model, or spreadsheet package; CSV/XLSX import uses Python's standard library.

## Windows setup (Python 3.14)

Use Python 3.14. The project's existing `venv3` was created with Python 3.14.0. To create a new environment if needed:

```powershell
py -3.14 --version
py -3.14 -m venv venv3
```

Activate it and install the pinned, inspected dependencies:

```powershell
.\venv3\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run the interpreter directly instead:

```powershell
.\venv3\Scripts\python.exe -m pip install -r requirements.txt
```

The package versions in `requirements.txt` are the versions installed in the project's Python 3.14.0 Windows environment. No PyTorch, Paddle, Transformers, language-detection, or Excel-writing package is installed.

## Install Tesseract separately

1. Install Tesseract OCR for Windows from the [UB Mannheim Windows builds](https://github.com/UB-Mannheim/tesseract/wiki). Include English language data.
2. Confirm it is available in PowerShell:

   ```powershell
   tesseract --version
   tesseract --list-langs
   ```

3. If Tesseract is not on `PATH`, copy `.env.example` to `.env` and set `TESSERACT_CMD` to the executable path, commonly `C:\Program Files\Tesseract-OCR\tesseract.exe`.
4. Install additional Tesseract language data separately, then set `TESSERACT_LANG` or choose an installed language in the interface. Combined language codes such as `eng+fra` work when every listed model is installed.

## Configure and run

Optional settings are read from `.env` at the project root. Copy the example file first:

```powershell
Copy-Item .env.example .env
```

The example contains no secret. Keep API keys only in the local `.env` or the process environment; do not commit them.

Start the app from the project directory:

```powershell
.\venv3\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Interactive API documentation is at <http://127.0.0.1:8000/docs>. The default bind address is local-only. Do not expose this single-user service to a network without adding authentication and access controls.

## Main features

- Upload JPG, JPEG, PNG, WEBP, BMP, PDF, CSV, and Excel XLSX files by browse or drag-and-drop. Spreadsheet rows and worksheets are editable and available in the table view; legacy XLS files are not supported.
- Validate image and PDF signatures, corruption, empty files, dimensions, file size, PDF passwords, page limits, and XLSX archive limits.
- Process batches of up to 20 files. The default upload limit is 15 MB per file, 75 MB per batch, and 50 pages per PDF; the limits can be configured.
- Run Tesseract with a selectable installed language and page segmentation mode. Word boxes and relative confidence estimates are returned where available. Confidence is an estimate, not a calibrated probability.
- Compare source and preprocessing previews. Preprocessing adds conservative contrast, denoising, threshold, upscaling, sharpening, and reliable small-skew alternatives while retaining the original input.
- Extract usable text embedded in digital PDF pages without invoking OCR on those pages. Render scanned pages and use Tesseract when needed. Results preserve page order and include page-level status.
- Edit document and page text, search and replace, copy, count words and characters, save corrections, and re-run OCR for an individual page with optional right-angle rotation.
- Download corrected TXT or DOCX, OCR JSON, inferred table CSV, and searchable PDF. Searchable PDFs retain original page content and add OCR words as invisible positioned text. Built-in PDF fonts have limited support for non-Latin scripts.
- Review heuristic table cells and explicit `label: value` form candidates. Both can be edited before CSV download. Ambiguous rows and cells are marked for review.
- Use a separate prescription mode to review literal dosage, frequency, duration, instruction, and medicine-name candidates. Candidate fields stay editable and exports include an unverified-data warning.
- Use Gemini Vision for handwriting mode when the Gemini key is configured, with TrOCR and Tesseract as local fallbacks. Review transcriptions against the original image.
- Use the general AI assistant for questions beyond a specific document, or use document Q&A and summaries in the document tools.
- View real document and page counts, outcomes, confidence estimates, durations, file types, and searchable history.
- Delete a document and source upload at any time. Jobs, OCR results, previews, and source documents expire after 24 hours by default; configure `SMARTOCR_RETENTION_HOURS` to change the period.

## Optional integrations

### Gemini AI and assistant

Set `SMARTOCR_AI_PROVIDER=gemini`, `GEMINI_API_KEY`, and optionally `SMARTOCR_AI_MODEL` in the local `.env` file. Gemini powers handwritten image/PDF transcription, document summaries and questions, and the general AI Assistant page. Handwriting images and text are sent to Google only when you choose handwriting mode or an AI feature. The general assistant keeps conversation history in the browser for the current page session and can include the selected document's OCR text when its context checkbox is enabled. Without an AI key, document summaries and questions retain their local extractive fallback; general chat is unavailable.

An OpenAI-compatible provider can instead be configured with `SMARTOCR_AI_PROVIDER=openai-compatible`, `SMARTOCR_AI_API_BASE_URL`, `SMARTOCR_AI_API_KEY`, and `SMARTOCR_AI_MODEL`.

### Translation

Set `SMARTOCR_TRANSLATION_URL` to a trusted LibreTranslate-compatible endpoint. Set `SMARTOCR_TRANSLATION_API_KEY` when required by that endpoint. Translation sends the requested text to the configured external service and returns it alongside the unchanged original. No text is sent when the feature is not configured or invoked.

### Handwriting

When `GEMINI_API_KEY` is configured, handwriting mode sends the page image to Gemini Vision for transcription. If Gemini is unavailable, the app falls back to optional TrOCR (when enabled) and then Tesseract. Review the output against the uploaded page, especially names, numbers, and medical text.

## API overview

All endpoints are same-origin with the frontend. See `/docs` for request schemas.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health`, `GET /api/config` | OCR executable, installed languages, limits, and optional-service status |
| `POST /api/ocr` | Process one image, PDF, CSV, or XLSX upload; accepts `file`, optional `language`, `psm`, `mode` (`ocr` or `handwriting`), and comma-separated `pages` form fields |
| `POST /api/ocr/batch` | Process up to 20 image, PDF, CSV, or XLSX files; returns per-file results and errors |
| `POST /api/prescription` | Process one file in prescription-review mode |
| `GET /api/jobs/{id}` | Reopen a saved OCR result |
| `PATCH /api/jobs/{id}/text` | Save corrected document text and/or a page-number-to-text map |
| `POST /api/jobs/{id}/pages/{page}/ocr` | Re-run one page; JSON body supports `rotation`, `language`, and `psm` |
| `GET /api/jobs/{id}/preview/{page}?kind=original\|processed` | Read the saved page preview |
| `GET /api/jobs/{id}/tables`, `GET /api/jobs/{id}/forms` | Read heuristic table cells and literal form candidates |
| `GET /api/jobs/{id}/prescription` | Read prescription candidates and original recognized text |
| `POST /api/jobs/{id}/ai/summary`, `POST /api/jobs/{id}/ai/ask` | Optional AI tools; the ask body is `{"question":"..."}` |
| `POST /api/assistant/chat` | General assistant chat; body accepts `{"message":"...","history":[]}` |
| `POST /api/translate` | Optional translation; body is `{"text":"...","target_language":"es"}` |
| `GET /api/jobs/{id}/export/{txt\|docx\|json\|csv\|searchable-pdf}` | Download an export; `?corrected=false` selects original OCR text for text exports |
| `GET /api/download/{id}` | Backward-compatible corrected TXT download |
| `GET /api/history`, `GET /api/dashboard` | Search local short-lived job history and show real statistics |
| `DELETE /api/jobs/{id}` | Delete saved result, source upload, and previews |

## Verification

The unittest suite creates temporary image and PDF fixtures; it does not need sample documents or a test-only package. A printed-text integration test runs only when Tesseract and English data are available.

```powershell
.\venv3\Scripts\python.exe -m unittest discover -s tests -v
```

## Feature boundaries and privacy

- Language identification reports a writing-system estimate (such as Latin or Cyrillic), not a guessed language. The OCR language selector displays full names alongside Tesseract's model codes.
- Table and key-value extraction use OCR geometry and literal text patterns. They are not a semantic invoice parser and may misalign ambiguous layouts. Verify every cell and value.
- `pdfplumber` is not included in the Python 3.14 environment. Digital PDF tables use the same conservative word-position heuristics as scanned tables; no semantic table model is bundled.
- CSV and Excel `.xlsx` import use the Python standard library. Legacy `.xls` import and `.xlsx` export are unavailable; spreadsheet exports use CSV.
- DOCX export is assembled with Python's standard library rather than adding `python-docx` to the Python 3.14 environment.
- There is no database or user-account system. History is filesystem-backed for this local, single-user app. Do not use it as a multi-user service without authentication, per-user access controls, and an appropriate database.
- Source files and extracted OCR text remain on the local machine until deleted or their retention time expires. Optional AI and translation providers receive text only when their feature is explicitly used.
- Generated AI and translation providers are optional. Missing configuration does not block local document summaries, source matching, or basic OCR.

## Troubleshooting

- **Tesseract not found:** install the Windows executable, add it to `PATH`, or set `TESSERACT_CMD` in `.env`; restart Uvicorn and check `/api/health`.
- **Language data missing:** install the requested `.traineddata` file into Tesseract's `tessdata` directory and check `tesseract --list-langs`.
- **PDF password or page-limit error:** remove the password before upload or split the PDF into files of at most the configured page count.
- **No or low-confidence text:** try a higher-quality scan, choose a suitable segmentation mode, rotate the page, or re-run OCR. Review all names, numbers, and medical text manually.
- **AI or translation unavailable:** set the matching optional environment settings and restart the app. Check provider URL, key, model name, network access, and provider-side limits.
- **History item expired:** the default retention is 24 hours. Re-upload the original to process it again; a deleted/expired source cannot be used to re-run OCR or produce a searchable PDF.

### Optional dedicated handwriting model (experimental)

The default app deliberately does not install PyTorch or Transformers because their support varies by Python version and Windows build. To evaluate TrOCR, first create a separate test environment or confirm compatible wheels for your Python 3.14 setup. Install compatible `torch` and `transformers` only after verifying support, then set these variables in `.env`:

```env
SMARTOCR_ENABLE_TROCR=1
SMARTOCR_TROCR_MODEL=microsoft/trocr-base-handwritten
```

Restart the server. The first model use may download model weights and requires internet access; later use generally uses the local model cache. TrOCR is primarily trained for handwritten text-line images, so this project segments rows heuristically before recognition. Complex page layouts, slanted photos, multiple columns, and segmentation errors can still reduce quality. If loading or recognition fails, the app logs the failure and falls back to Tesseract. The TrOCR result does not provide a calibrated confidence score; do not interpret a missing score as 100% confidence.

### Handwriting evaluation

An OCR confidence estimate is not the same as recognition accuracy. For a meaningful evaluation, provide a ground-truth transcription for each test image and calculate character error rate (CER) and word error rate (WER). Review all handwritten output before relying on it, especially medical, legal, or financial text.
