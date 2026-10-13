"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  files: [], jobs: [], currentIndex: 0, pageIndex: 0, config: {}, tables: [],
  forms: [], tablesJobId: null, prescriptionCandidates: [], assistantMessages: [], assistantContextJobId: null, dirty: false, toastTimer: null, historyTimer: null,
};
const allowedExtensions = new Set([".jpg", ".jpeg", ".png", ".webp", ".bmp", ".pdf", ".csv", ".xlsx"]);

const languageNames = {
  afr: "Afrikaans", amh: "Amharic", ara: "Arabic", aze: "Azerbaijani", bel: "Belarusian", ben: "Bengali",
  bos: "Bosnian", bre: "Breton", bul: "Bulgarian", cat: "Catalan", ceb: "Cebuano",
  chi_sim: "Chinese (Simplified)", chi_tra: "Chinese (Traditional)", chr: "Cherokee", ces: "Czech",
  cos: "Corsican", cym: "Welsh", dan: "Danish", deu: "German", deu_latf: "German (Fraktur)",
  div: "Divehi", dzo: "Dzongkha", ell: "Greek", eng: "English", enm: "Middle English", epo: "Esperanto", est: "Estonian",
  eus: "Basque", fas: "Persian", fao: "Faroese", fil: "Filipino", fin: "Finnish", fra: "French",
  frk: "Frankish", gle: "Irish", glg: "Galician", grc: "Ancient Greek", guj: "Gujarati", hat: "Haitian Creole",
  heb: "Hebrew", hin: "Hindi", hrv: "Croatian", hun: "Hungarian", hye: "Armenian", iku: "Inuktitut",
  ind: "Indonesian", isl: "Icelandic", ita: "Italian", ita_old: "Italian (old orthography)", jav: "Javanese",
  jpn: "Japanese", kan: "Kannada", kat: "Georgian", kaz: "Kazakh", khm: "Khmer", kir: "Kyrgyz", kor: "Korean",
  kur: "Kurdish", lao: "Lao", lat: "Latin", lav: "Latvian", lit: "Lithuanian", ltz: "Luxembourgish", mal: "Malayalam",
  mar: "Marathi", mkd: "Macedonian", mlt: "Maltese", mon: "Mongolian", mri: "Māori", msa: "Malay", mya: "Burmese", nep: "Nepali",
  nld: "Dutch", nor: "Norwegian", oci: "Occitan", ori: "Odia", osd: "Orientation and script data", pan: "Punjabi", pol: "Polish", pus: "Pashto", por: "Portuguese",
  que: "Quechua", ron: "Romanian", rus: "Russian", san: "Sanskrit", sin: "Sinhala", slk: "Slovak", slv: "Slovenian", snd: "Sindhi", spa: "Spanish", spa_old: "Spanish (old orthography)", sqi: "Albanian",
  srp: "Serbian", swa: "Swahili", swe: "Swedish", syr: "Syriac", tam: "Tamil", tat: "Tatar", tel: "Telugu", tha: "Thai",
  tgk: "Tajik", tir: "Tigrinya", tur: "Turkish", uig: "Uyghur", ukr: "Ukrainian", urd: "Urdu", uzb: "Uzbek", vie: "Vietnamese", yid: "Yiddish",
};

function languageLabel(code) {
  if (languageNames[code]) return `${languageNames[code]} (${code})`;
  const localeCode = { chi_sim: "zh-Hans", chi_tra: "zh-Hant" }[code] || code.replaceAll("_", "-");
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(localeCode);
    if (name && name.toLowerCase() !== localeCode.toLowerCase()) return `${name} (${code})`;
  } catch { /* Fall back to a readable label for uncommon Tesseract model names. */ }
  return `Other language (${code})`;
}

function setStatus(message, tone = "") {
  const node = $("#status");
  node.textContent = message;
  node.dataset.state = tone;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => node.classList.remove("show"), 2800);
}

async function apiJSON(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status}).`);
  return payload;
}

function formatBytes(size) {
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(2)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;
}

function addFiles(fileList) {
  const maxBytes = state.config.max_upload_bytes || 15 * 1024 * 1024;
  for (const file of fileList) {
    const dot = file.name.lastIndexOf(".");
    const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
    if (!allowedExtensions.has(extension)) {
      toast(`${file.name}: choose an image, PDF, CSV, or XLSX workbook.`);
      continue;
    }
    if (!file.size) {
      toast(`${file.name}: the file is empty.`);
      continue;
    }
    if (file.size > maxBytes) {
      toast(`${file.name}: exceeds the ${state.config.max_upload_mb || 15} MB limit.`);
      continue;
    }
    if (state.files.some((entry) => entry.file.name === file.name && entry.file.size === file.size && entry.file.lastModified === file.lastModified)) continue;
    state.files.push({ file, preview: [".csv", ".xlsx", ".pdf"].includes(extension) ? null : URL.createObjectURL(file) });
  }
  renderFiles();
  $("#fileInput").value = "";
}

function renderFiles() {
  const list = $("#fileList");
  list.replaceChildren();
  state.files.forEach(({ file, preview }, index) => {
    const card = document.createElement("div");
    card.className = "file-card";
    if (preview) {
      const image = document.createElement("img");
      image.className = "file-thumb";
      image.src = preview;
      image.alt = `Preview of ${file.name}`;
      card.append(image);
    } else {
      const type = document.createElement("span");
      type.className = "file-thumb file-pdf";
      type.textContent = file.name.toLowerCase().endsWith(".xlsx") ? "XLSX" : file.name.toLowerCase().endsWith(".csv") ? "CSV" : "PDF";
      card.append(type);
    }
    const description = document.createElement("div");
    description.className = "file-description";
    const name = document.createElement("div");
    name.className = "file-name";
    name.textContent = file.name;
    name.title = file.name;
    const meta = document.createElement("div");
    meta.className = "file-meta";
    meta.textContent = formatBytes(file.size);
    description.append(name, meta);
    const stateLabel = document.createElement("span");
    stateLabel.className = "file-meta";
    stateLabel.textContent = "Ready";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-file";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      if (preview) URL.revokeObjectURL(preview);
      state.files.splice(index, 1);
      renderFiles();
    });
    card.append(description, stateLabel, remove);
    list.append(card);
  });
  $("#processButton").disabled = !state.files.length;
  $("#clearFilesButton").disabled = !state.files.length;
}

function clearFiles() {
  state.files.forEach((entry) => entry.preview && URL.revokeObjectURL(entry.preview));
  state.files = [];
  renderFiles();
  setStatus("");
}

function formForFiles(files) {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  form.append("language", $("#languageSelect").value);
  form.append("psm", $("#psmSelect").value);
  form.append("mode", $("#engineSelect").value);
  return form;
}

function formForFile(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("language", $("#languageSelect").value);
  form.append("psm", $("#psmSelect").value);
  form.append("mode", $("#engineSelect").value);
  return form;
}

function uploadXHR(url, form, onProgress, onUploaded = () => {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    });
    request.upload.addEventListener("load", onUploaded);
    request.addEventListener("load", () => {
      let data = {};
      try { data = JSON.parse(request.responseText); } catch { /* The error below remains useful. */ }
      if (request.status < 200 || request.status >= 300) reject(new Error(data.detail || `Upload failed (${request.status}).`));
      else resolve(data);
    });
    request.addEventListener("error", () => reject(new Error("The server could not be reached.")));
    request.addEventListener("abort", () => reject(new Error("Upload cancelled.")));
    request.send(form);
  });
}

async function processFiles() {
  if (!state.files.length) return;
  const button = $("#processButton");
  const progress = $("#uploadProgress");
  const mode = $("#modeSelect").value;
  const engineMode = $("#engineSelect").value;
  button.disabled = true;
  button.textContent = "Processing…";
  progress.classList.remove("hidden");
  progress.value = 0;
  $("#resultsSection").classList.add("hidden");
  state.jobs = [];
  const files = state.files.map((entry) => entry.file);
  try {
    if (mode === "prescription") {
      for (let index = 0; index < files.length; index += 1) {
        setStatus(`Uploading prescription ${index + 1} of ${files.length}…`);
        const job = await uploadXHR("/api/prescription", formForFile(files[index]), (value) => {
          progress.value = Math.round(((index + value / 100) / files.length) * 100);
        }, () => setStatus(`Upload complete. Reviewing prescription ${index + 1} of ${files.length}…`));
        state.jobs.push(job);
        setStatus(`Reviewed ${index + 1} of ${files.length} prescription${files.length === 1 ? "" : "s"}.`);
      }
    } else if (files.length > 1) {
      setStatus(`Uploading ${files.length} documents…`);
      const batch = await uploadXHR("/api/ocr/batch", formForFiles(files), (value) => { progress.value = value; }, () => setStatus(`Upload complete. Processing ${files.length} documents…`));
      state.jobs = batch.jobs || [];
      if (batch.errors?.length) {
        const errors = batch.errors.map((item) => `${item.filename}: ${item.message}`).join(" · ");
        toast(`${batch.failed} file${batch.failed === 1 ? "" : "s"} could not be processed. ${errors}`);
      }
    } else {
      setStatus("Uploading document…");
      const job = await uploadXHR("/api/ocr", formForFile(files[0]), (value) => { progress.value = value; }, () => setStatus("Upload complete. Document processing is running…"));
      state.jobs = [job];
    }
    progress.value = 100;
    if (!state.jobs.length) {
      setStatus("No documents were processed. Check the file errors and try again.", "error");
      return;
    }
    state.currentIndex = 0;
    state.pageIndex = 0;
    state.dirty = false;
    renderDocumentPicker();
    renderJob();
    $("#resultsSection").classList.remove("hidden");
    setStatus(`Finished processing ${state.jobs.length} document${state.jobs.length === 1 ? "" : "s"}.`, "success");
    if (mode === "prescription") activateTab("prescriptionView");
    else activateTab("ocrView");
    await loadLanguageInfo();
    $("#resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = state.files.length === 0;
    button.textContent = mode === "prescription" ? "Review prescription" : engineMode === "handwriting" ? "Recognize handwriting" : "Extract text";
    setTimeout(() => progress.classList.add("hidden"), 700);
  }
}

function currentJob() { return state.jobs[state.currentIndex] || null; }

function renderDocumentPicker() {
  const wrap = $("#documentPickerWrap");
  const picker = $("#documentPicker");
  picker.replaceChildren();
  state.jobs.forEach((job, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `${job.filename} · ${job.pages.length} page${job.pages.length === 1 ? "" : "s"}`;
    picker.append(option);
  });
  wrap.classList.toggle("hidden", state.jobs.length < 2);
  picker.value = String(state.currentIndex);
}

function renderJob() {
  const job = currentJob();
  if (!job) return;
  if (state.assistantContextJobId !== job.job_id) {
    state.assistantContextJobId = job.job_id;
    $("#includeDocumentContext").checked = true;
    state.assistantMessages = [];
    renderAssistantChat();
  }
  $("#includeDocumentContext").disabled = false;
  if (state.tablesJobId !== job.job_id) {
    state.tablesJobId = null;
    state.tables = [];
    state.forms = [];
    state.prescriptionCandidates = [];
  }
  const summary = $("#documentSummary");
  summary.replaceChildren();
  const chips = [
    ["Document", job.filename], ["Pages", job.pages.length],
    ["Words", (job.text || "").trim() ? (job.text || "").trim().split(/\s+/).length : 0],
    ["Characters", (job.text || "").length],
    ["Average OCR confidence estimate", job.average_confidence == null ? "Not available" : `${(job.average_confidence * 100).toFixed(1)}%`],
    ["Processing", `${Number(job.duration_seconds || 0).toFixed(2)} s`],
  ];
  chips.forEach(([label, value]) => {
    const chip = document.createElement("span");
    chip.className = "stat-chip";
    const strong = document.createElement("strong");
    strong.textContent = `${label}: `;
    chip.append(strong, document.createTextNode(String(value)));
    summary.append(chip);
  });
  const warningList = $("#warningList");
  warningList.replaceChildren();
  (job.warnings || []).forEach((warning) => {
    const item = document.createElement("div");
    item.className = "warning-item";
    item.textContent = warning;
    warningList.append(item);
  });
  $("#masterText").value = job.text || "";
  $("#originalText").textContent = job.original_text || "";
  updateCounts();
  state.dirty = false;
  updateDirty();
  state.pageIndex = Math.min(state.pageIndex, Math.max(0, job.pages.length - 1));
  renderPagePicker();
  renderPage();
  renderTableView();
  renderPrescription();
  if ($('.tab.active')?.dataset.view === "tablesView" && state.tablesJobId !== job.job_id) loadTables();
  const aiEnabled = true;
  $("#summaryButton").disabled = !aiEnabled;
  $("#askButton").disabled = !aiEnabled;
  $("#aiAvailability").textContent = state.config.ai_provider_available
    ? "Gemini can summarize or answer questions from this document. Document text is sent only when you choose an AI tool; if the provider is unreachable, SmartOCR falls back to local source excerpts."
    : "The assistant works offline with sentence summaries and matching source passages. Add an API provider only if you want generated answers.";
  const translationEnabled = Boolean(state.config.translation_available);
  $("#translateButton").disabled = !translationEnabled || !$("#targetLanguage").value;
  $("#translationAvailability").textContent = translationEnabled
    ? "Translation uses the server-configured external service. The recognized text remains unchanged."
    : "Translation is unavailable until a service URL is configured on the server.";
  $("#summaryOutput").classList.add("hidden");
  $("#answerOutput").classList.add("hidden");
  $("#translationOutput").classList.add("hidden");
}

function renderPagePicker() {
  const job = currentJob();
  const picker = $("#pagePicker");
  picker.replaceChildren();
  (job?.pages || []).forEach((page, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `${page.sheet_name || `Page ${page.page}`} · ${page.status}`;
    picker.append(option);
  });
  picker.value = String(state.pageIndex);
  $("#pagePicker").disabled = !job?.pages?.length;
}

function renderPage() {
  const job = currentJob();
  if (!job?.pages?.length) return;
  const page = job.pages[state.pageIndex];
  $("#pageTitle").textContent = page.sheet_name || `Page ${page.page}`;
  $("#pagePicker").value = String(state.pageIndex);
  $("#pageText").value = page.corrected_text ?? page.text ?? "";
  $("#pageOriginalText").textContent = page.original_text ?? page.text ?? "";
  $("#rotationSelect").value = String(page.rotation || 0);
  $("#prevPageButton").disabled = state.pageIndex <= 0;
  $("#nextPageButton").disabled = state.pageIndex >= job.pages.length - 1;
  const spreadsheet = [".csv", ".xlsx"].some((suffix) => job.filename.toLowerCase().endsWith(suffix));
  $("#rerunButton").disabled = spreadsheet;
  $("#rotationSelect").disabled = spreadsheet;
  const cacheTag = encodeURIComponent(job.job_id + page.page + (page.rotation || 0));
  const original = $("#originalPreview");
  original.closest("figure").classList.toggle("hidden", !page.preview_url);
  $("#previewUnavailable").classList.toggle("hidden", Boolean(page.preview_url));
  if (page.preview_url) {
    const originalUrl = new URL(page.preview_url, window.location.origin);
    originalUrl.searchParams.set("v", cacheTag);
    original.src = `${originalUrl.pathname}${originalUrl.search}`;
    original.alt = `Original image for page ${page.page}`;
  } else original.removeAttribute("src");
  const processedFigure = $("#processedFigure");
  if (page.processed_preview_url) {
    processedFigure.classList.remove("hidden");
    const processedUrl = new URL(page.processed_preview_url, window.location.origin);
    processedUrl.searchParams.set("v", cacheTag);
    $("#processedPreview").src = `${processedUrl.pathname}${processedUrl.search}`;
    $("#processedPreview").alt = `Preprocessed image used for page ${page.page}`;
  } else {
    processedFigure.classList.add("hidden");
    $("#processedPreview").removeAttribute("src");
  }
  updateCounts();
}

function updateCounts() {
  const text = $("#masterText").value;
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  $("#counts").textContent = `${words} word${words === 1 ? "" : "s"} · ${text.length} character${text.length === 1 ? "" : "s"}`;
}

function updateDirty() {
  $("#dirtyLabel").textContent = state.dirty ? "Unsaved changes" : "All changes saved";
  $("#dirtyLabel").dataset.dirty = String(state.dirty);
  $("#saveButton").disabled = !state.dirty;
  const isSpreadsheet = [".csv", ".xlsx"].some((suffix) => currentJob()?.filename?.toLowerCase().endsWith(suffix));
  document.querySelectorAll(".export-button").forEach((button) => {
    button.disabled = state.dirty || (isSpreadsheet && button.dataset.format === "searchable-pdf");
  });
}

function composeCorrectedPages(job) {
  return job.pages.map((page) => {
    const text = page.corrected_text ?? page.text ?? "";
    return text.trim() ? `--- Page ${page.page} ---\n${text}` : "";
  }).filter(Boolean).join("\n\n");
}

async function saveCorrections() {
  const job = currentJob();
  if (!job || !state.dirty) return;
  const pageCorrections = {};
  job.pages.forEach((page) => {
    const corrected = page.corrected_text ?? page.text ?? "";
    if (corrected !== (page.original_text ?? page.text ?? "")) pageCorrections[String(page.page)] = corrected;
  });
  try {
    const updated = await apiJSON(`/api/jobs/${job.job_id}/text`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: $("#masterText").value, pages: pageCorrections }),
    });
    state.jobs[state.currentIndex] = updated;
    state.dirty = false;
    renderJob();
    toast("Corrections saved.");
  } catch (error) { toast(error.message); }
}

function activateTab(viewId) {
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.view === viewId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tool-view").forEach((view) => view.classList.toggle("hidden", view.id !== viewId));
  if (viewId === "tablesView") loadTables();
  if (viewId === "prescriptionView") renderPrescription();
}

async function loadTables() {
  const job = currentJob();
  if (!job) return;
  if (state.tablesJobId === job.job_id) { renderTableView(); return; }
  try {
    const [data, formData] = await Promise.all([
      apiJSON(`/api/jobs/${job.job_id}/tables`),
      apiJSON(`/api/jobs/${job.job_id}/forms`),
    ]);
    if (currentJob()?.job_id !== job.job_id) return;
    state.tables = data.tables || [];
    state.forms = formData.fields || [];
    state.tablesJobId = job.job_id;
    renderTableView(data.warning, formData.warning);
  } catch (error) { $("#tableContent").textContent = error.message; }
}

function renderTableView(generalWarning = "", formWarning = "") {
  const host = $("#tableContent");
  if (!host || !currentJob()) return;
  host.replaceChildren();
  if (generalWarning) {
    const note = document.createElement("p");
    note.className = "table-warning";
    note.textContent = generalWarning;
    host.append(note);
  }
  if (!state.tables.length) {
    const note = document.createElement("p");
    note.className = "empty-state";
    note.textContent = "No clear table layout was detected in this document.";
    host.append(note);
  } else state.tables.forEach((table, tableIndex) => {
    const card = document.createElement("div");
    card.className = "table-card";
    const title = document.createElement("h4");
    title.textContent = table.name || `Page ${table.page}`;
    card.append(title);
    if (table.warning) {
      const warning = document.createElement("p");
      warning.className = "table-warning";
      warning.textContent = table.warning;
      card.append(warning);
    }
    if (!table.rows?.length) {
      const noRows = document.createElement("p");
      noRows.className = "muted small";
      noRows.textContent = "No table cells were inferred on this page.";
      card.append(noRows);
      host.append(card);
      return;
    }
    const tableElement = document.createElement("table");
    const body = document.createElement("tbody");
    table.rows.forEach((row, rowIndex) => {
      const tr = document.createElement("tr");
      row.forEach((value, columnIndex) => {
        const td = document.createElement("td");
        const input = document.createElement("input");
        input.type = "text";
        input.value = value;
        input.setAttribute("aria-label", `Page ${table.page}, row ${rowIndex + 1}, column ${columnIndex + 1}`);
        input.addEventListener("input", () => { state.tables[tableIndex].rows[rowIndex][columnIndex] = input.value; });
        td.append(input);
        tr.append(td);
      });
      body.append(tr);
    });
    tableElement.append(body);
    card.append(tableElement);
    host.append(card);
  });
  renderForms(formWarning);
}

function renderForms(warning = "") {
  const host = $("#formContent");
  host.replaceChildren();
  if (warning) {
    const note = document.createElement("p"); note.className = "table-warning"; note.textContent = warning; host.append(note);
  }
  if (!state.forms.length) {
    const empty = document.createElement("p"); empty.className = "muted small"; empty.textContent = "No explicit label:value fields were found."; host.append(empty); return;
  }
  state.forms.forEach((field, index) => {
    const row = document.createElement("div"); row.className = "form-field-row";
    const label = document.createElement("label"); label.textContent = `Page ${field.page} · ${field.label}`;
    const input = document.createElement("input"); input.type = "text"; input.value = field.value;
    input.setAttribute("aria-label", `${field.label} value, page ${field.page}`);
    input.addEventListener("input", () => { state.forms[index].value = input.value; });
    const source = document.createElement("small"); source.className = "muted"; source.textContent = `Source: ${field.source_line}`;
    row.append(label, input, source); host.append(row);
  });
}

function csvCell(value) {
  let text = String(value ?? "");
  if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadBlob(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadEditedTables() {
  const rows = [["worksheet or page", "row", "column", "value"]];
  state.tables.forEach((table) => (table.rows || []).forEach((row, rowIndex) => row.forEach((value, columnIndex) => {
    if (value) rows.push([table.name || `Page ${table.page}`, rowIndex + 1, columnIndex + 1, value]);
  })));
  const csv = "\uFEFF" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const job = currentJob();
  downloadBlob(csv, `${job?.filename?.replace(/\.[^.]+$/, "") || "document"}_tables.csv`, "text/csv;charset=utf-8");
}

function downloadEditedForms() {
  const job = currentJob();
  const rows = [["page", "label", "value", "source_line"], ...state.forms.map((field) => [field.page, field.label, field.value, field.source_line])];
  const csv = "\uFEFF" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  downloadBlob(csv, `${job?.filename?.replace(/\.[^.]+$/, "") || "document"}_fields.csv`, "text/csv;charset=utf-8");
}

function renderPrescription() {
  const job = currentJob();
  const host = $("#prescriptionContent");
  if (!host || !job) return;
  host.replaceChildren();
  $("#prescriptionOriginal").textContent = job.prescription?.original_text || job.original_text || "";
  if (!job.prescription?.candidates?.length) {
    const note = document.createElement("p");
    note.className = "empty-state";
    note.textContent = "No dosage or frequency patterns were found. No fields have been filled in.";
    host.append(note);
    state.prescriptionCandidates = [];
    return;
  }
  if (state.prescriptionCandidates.length !== job.prescription.candidates.length) {
    state.prescriptionCandidates = job.prescription.candidates.map((candidate) => ({ ...candidate }));
  }
  const fieldNames = [
    ["medicine_candidate", "Medicine name candidate"], ["dosage_candidate", "Dosage candidate"],
    ["frequency_candidate", "Frequency candidate"], ["duration_candidate", "Duration candidate"],
    ["instructions_candidate", "Instructions candidate"],
  ];
  state.prescriptionCandidates.forEach((candidate, index) => {
    const card = document.createElement("div");
    card.className = "candidate-card";
    const heading = document.createElement("h4");
    heading.textContent = `Candidate ${index + 1} · Page ${candidate.page} · verify manually`;
    const fields = document.createElement("div");
    fields.className = "candidate-fields";
    fieldNames.forEach(([key, label]) => {
      const wrapper = document.createElement("label");
      wrapper.textContent = label;
      const input = document.createElement("input");
      input.type = "text";
      input.value = candidate[key] || "";
      input.setAttribute("aria-label", label);
      input.addEventListener("input", () => { state.prescriptionCandidates[index][key] = input.value; });
      wrapper.append(input);
      fields.append(wrapper);
    });
    const original = document.createElement("p");
    original.className = "candidate-source";
    original.textContent = `Recognized line: ${candidate.recognized_line}`;
    card.append(heading, fields, original);
    host.append(card);
  });
}

function currentPrescriptionExport() {
  const job = currentJob();
  return {
    filename: job?.filename,
    reviewed_at: new Date().toISOString(),
    candidates: state.prescriptionCandidates,
    original_recognized_text: job?.prescription?.original_text || job?.original_text || "",
    warning: job?.prescription?.warning || "Unverified OCR candidates. A qualified human must check the original document before clinical use.",
  };
}

function exportPrescription(format) {
  const data = currentPrescriptionExport();
  const base = (data.filename || "prescription").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]+/g, "_");
  if (format === "json") downloadBlob(JSON.stringify(data, null, 2), `${base}_review.json`, "application/json");
  else if (format === "csv") {
    const keys = ["page", "medicine_candidate", "dosage_candidate", "frequency_candidate", "duration_candidate", "instructions_candidate", "recognized_line"];
    const csv = "\uFEFF" + [keys, ...data.candidates.map((candidate) => keys.map((key) => candidate[key] || ""))].map((row) => row.map(csvCell).join(",")).join("\r\n");
    downloadBlob(csv, `${base}_review.csv`, "text/csv;charset=utf-8");
  } else {
    const lines = [data.warning, "", ...data.candidates.map((candidate, index) => [
      `Candidate ${index + 1} (page ${candidate.page})`,
      `Medicine: ${candidate.medicine_candidate || ""}`, `Dosage: ${candidate.dosage_candidate || ""}`,
      `Frequency: ${candidate.frequency_candidate || ""}`, `Duration: ${candidate.duration_candidate || ""}`,
      `Instructions: ${candidate.instructions_candidate || ""}`, `Recognized line: ${candidate.recognized_line || ""}`,
    ].join("\n")), "", "Original recognized text:", data.original_recognized_text];
    downloadBlob(lines.join("\n\n"), `${base}_review.txt`, "text/plain;charset=utf-8");
  }
}

async function loadLanguageInfo() {
  const job = currentJob();
  if (!job) return;
  try {
    const info = await apiJSON(`/api/jobs/${job.job_id}/language`);
    const usedLanguage = info.ocr_language ? languageLabel(info.ocr_language) : "Not applicable to spreadsheet import";
    $("#languageInfo").textContent = info.script
      ? `OCR language: ${usedLanguage}. Writing system estimate: ${info.script}. ${info.warning}`
      : `OCR language: ${usedLanguage}. ${info.warning}`;
  } catch { $("#languageInfo").textContent = "Language detection is not available for this result."; }
}

async function rerunCurrentPage() {
  const job = currentJob();
  if (!job) return;
  if (state.dirty && !confirm("You have unsaved edits. Re-running this page may replace its current correction. Continue?")) return;
  const page = job.pages[state.pageIndex];
  const button = $("#rerunButton");
  button.disabled = true;
  button.textContent = "Re-running…";
  try {
    const updated = await apiJSON(`/api/jobs/${job.job_id}/pages/${page.page}/ocr`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rotation: Number($("#rotationSelect").value), language: $("#languageSelect").value, psm: Number($("#psmSelect").value) }),
    });
    state.jobs[state.currentIndex] = updated;
    renderJob();
    await loadLanguageInfo();
    toast("Page OCR completed.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "Re-run OCR"; }
}

async function showHistory(search = "") {
  $("#historyList").replaceChildren();
  $("#historyList").append(Object.assign(document.createElement("p"), { className: "empty-state", textContent: "Loading documents…" }));
  try {
    const [dashboard, history] = await Promise.all([
      apiJSON("/api/dashboard"),
      apiJSON(`/api/history?search=${encodeURIComponent(search)}&limit=100`),
    ]);
    const stats = [
      ["Documents processed", dashboard.total_documents], ["Pages processed", dashboard.total_pages],
      ["Successful pages", dashboard.successful_pages], ["Failed or empty", dashboard.failed_pages],
      ["Avg. OCR confidence estimate", dashboard.average_confidence_estimate == null ? "—" : `${(dashboard.average_confidence_estimate * 100).toFixed(1)}%`],
      ["Avg. processing time", `${dashboard.average_duration_seconds}s`],
      ["File types", Object.entries(dashboard.document_types || {}).map(([key, value]) => `${key} ${value}`).join(" · ") || "—"],
      ["Recent results", dashboard.recent_documents.length],
    ];
    const grid = $("#dashboardStats");
    grid.replaceChildren();
    stats.forEach(([label, value]) => {
      const card = document.createElement("div");
      card.className = "dashboard-card";
      const name = document.createElement("span"); name.textContent = label;
      const val = document.createElement("strong"); val.textContent = String(value);
      card.append(name, val); grid.append(card);
    });
    $("#retentionNote").textContent = `Local results and source uploads are automatically deleted after ${history.retention_hours} hours. You can delete a document sooner.`;
    renderHistoryRows(history.documents || []);
  } catch (error) {
    $("#historyList").replaceChildren(Object.assign(document.createElement("p"), { className: "empty-state", textContent: error.message }));
  }
}

function renderHistoryRows(documents) {
  const host = $("#historyList");
  host.replaceChildren();
  if (!documents.length) {
    const empty = document.createElement("p"); empty.className = "empty-state";
    empty.textContent = "No saved documents match this search."; host.append(empty); return;
  }
  documents.forEach((documentInfo) => {
    const row = document.createElement("div"); row.className = "history-row";
    const open = document.createElement("button"); open.type = "button"; open.className = "history-open";
    const title = document.createElement("strong"); title.textContent = documentInfo.filename;
    const date = document.createElement("span"); date.textContent = new Date(documentInfo.created_at).toLocaleString();
    open.append(title, date);
    open.addEventListener("click", () => openHistoryJob(documentInfo.job_id));
    const pages = document.createElement("span"); pages.className = "history-meta"; pages.textContent = `${documentInfo.pages} page${documentInfo.pages === 1 ? "" : "s"}`;
    const confidence = document.createElement("span"); confidence.className = "history-meta";
    confidence.textContent = documentInfo.average_confidence == null ? "No confidence estimate" : `${(documentInfo.average_confidence * 100).toFixed(1)}% estimate`;
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "button secondary history-delete"; remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete ${documentInfo.filename}`);
    remove.addEventListener("click", async () => {
      if (!confirm(`Delete ${documentInfo.filename} and its temporary source upload?`)) return;
      try { await apiJSON(`/api/jobs/${documentInfo.job_id}`, { method: "DELETE" }); await showHistory($("#historySearch").value); }
      catch (error) { toast(error.message); }
    });
    row.append(open, pages, confidence, remove); host.append(row);
  });
}

async function openHistoryJob(jobId) {
  if (state.dirty && !confirm("Discard unsaved edits and open this document?")) return;
  try {
    const job = await apiJSON(`/api/jobs/${jobId}`);
    state.jobs = [job]; state.currentIndex = 0; state.pageIndex = 0; state.dirty = false;
    renderDocumentPicker(); renderJob(); $("#resultsSection").classList.remove("hidden");
    activateTab("ocrView"); await loadLanguageInfo();
    activateMainView("workspace");
    $("#resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message); }
}

async function runAI(kind) {
  const job = currentJob();
  if (!job) return;
  if (state.dirty) { toast("Save corrections before asking the assistant so it uses the reviewed text."); return; }
  const button = kind === "summary" ? $("#summaryButton") : $("#askButton");
  const output = kind === "summary" ? $("#summaryOutput") : $("#answerOutput");
  button.disabled = true; button.textContent = kind === "summary" ? "Summarizing…" : "Thinking…";
  output.classList.remove("hidden"); output.textContent = "Working…";
  try {
    const options = kind === "summary" ? { method: "POST" } : {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: $("#questionInput").value }),
    };
    const result = await apiJSON(`/api/jobs/${job.job_id}/ai/${kind === "summary" ? "summary" : "ask"}`, options);
    output.replaceChildren();
    const answer = document.createElement("p"); answer.textContent = result.summary || result.answer || ""; output.append(answer);
    if (result.sources?.length) {
      const sourceLabel = document.createElement("strong"); sourceLabel.textContent = "Supporting passages"; output.append(sourceLabel);
      result.sources.forEach((source) => { const passage = document.createElement("p"); passage.textContent = `[Page ${source.page}] ${source.text}`; output.append(passage); });
    } else if (result.source_pages?.length) {
      const pages = document.createElement("p"); pages.textContent = `Source pages: ${result.source_pages.join(", ")}`; output.append(pages);
    }
    const warning = document.createElement("p"); warning.className = "muted small"; warning.textContent = result.warning || ""; output.append(warning);
  } catch (error) { output.textContent = error.message; }
  finally { button.disabled = !state.config.ai_available; button.textContent = kind === "summary" ? "Summarize document" : "Ask"; }
}

function renderAssistantChat(messages = state.assistantMessages) {
  const host = $("#assistantChatMessages");
  host.replaceChildren();
  if (!messages.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Ask a question to start a conversation.";
    host.append(empty);
    return;
  }
  messages.forEach((message) => {
    const bubble = document.createElement("div");
    bubble.className = `chat-message ${message.role === "user" ? "user" : "assistant"}`;
    const label = document.createElement("strong");
    label.textContent = message.role === "user" ? "You" : "Assistant";
    const body = document.createElement("p");
    body.textContent = message.content;
    bubble.append(label, body);
    host.append(bubble);
  });
  host.scrollTop = host.scrollHeight;
}

async function sendAssistantMessage(event) {
  event.preventDefault();
  const input = $("#assistantChatInput");
  const button = $("#assistantChatSendButton");
  const message = input.value.trim();
  if (!message || !state.config.ai_provider_available) return;
  button.disabled = true;
  button.textContent = "Thinking…";
  renderAssistantChat([...state.assistantMessages, { role: "user", content: message }]);
  try {
    const result = await apiJSON("/api/assistant/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: state.assistantMessages.slice(-12),
        document_context: $("#includeDocumentContext").checked && currentJob()
          ? (($("#masterText").value || currentJob().corrected_text || currentJob().text || "").slice(0, 30000))
          : undefined,
      }),
    });
    state.assistantMessages.push({ role: "user", content: message }, { role: "assistant", content: result.answer });
    state.assistantMessages = state.assistantMessages.slice(-24);
    input.value = "";
    renderAssistantChat();
  } catch (error) {
    renderAssistantChat([
      ...state.assistantMessages,
      { role: "user", content: message },
      { role: "assistant", content: `I couldn't complete that request: ${error.message}` },
    ]);
  } finally {
    button.disabled = !state.config.ai_provider_available;
    button.textContent = "Send";
    input.focus();
  }
}

function activateMainView(view) {
  $("#workspaceView").classList.toggle("hidden", view !== "workspace");
  $("#assistantHomeView").classList.toggle("hidden", view !== "assistant");
  $("#historyView").classList.toggle("hidden", view !== "history");
  $("#workspaceNav").classList.toggle("active", view === "workspace");
  $("#assistantNav").classList.toggle("active", view === "assistant");
  $("#historyNav").classList.toggle("active", view === "history");
  if (view === "history") showHistory($("#historySearch").value);
  if (view === "assistant") $("#assistantChatInput").focus();
}

async function translateCorrectedText() {
  if (!state.config.translation_available) return;
  if (!$("#targetLanguage").value) { toast("Choose a target language."); return; }
  const output = $("#translationOutput"); output.classList.remove("hidden"); output.textContent = "Translating…";
  try {
    const result = await apiJSON("/api/translate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: $("#masterText").value, target_language: $("#targetLanguage").value }),
    });
    output.replaceChildren();
    const originalHeading = document.createElement("strong"); originalHeading.textContent = "Original text";
    const original = document.createElement("p"); original.textContent = result.original_text;
    let targetName = result.target_language;
    try { targetName = new Intl.DisplayNames(["en"], { type: "language" }).of(result.target_language) || targetName; } catch { /* Keep the returned language label. */ }
    const translatedHeading = document.createElement("strong"); translatedHeading.textContent = `Translation (${targetName})`;
    const translated = document.createElement("p"); translated.textContent = result.translated_text;
    const warning = document.createElement("p"); warning.className = "muted small"; warning.textContent = result.warning;
    output.append(originalHeading, original, translatedHeading, translated, warning);
  } catch (error) { output.textContent = error.message; }
}

function exportServerFile(format) {
  const job = currentJob();
  if (!job) return;
  if (state.dirty) { toast("Save corrections before exporting."); return; }
  window.location.href = `/api/jobs/${job.job_id}/export/${format}`;
}

async function initialize() {
  try {
    state.config = await apiJSON("/api/config");
    const providerName = state.config.ai_provider === "gemini" ? "Gemini" : "AI provider";
    $("#assistantHomeAvailability").textContent = state.config.ai_provider_available
      ? `${providerName} is configured. Ask general questions and continue the conversation; chat history stays in this browser session.`
      : "The general assistant needs an API key. Add GEMINI_API_KEY to the local .env file and restart SmartOCR.";
    $("#assistantChatSendButton").disabled = !state.config.ai_provider_available;
    $("#assistantChatInput").disabled = !state.config.ai_provider_available;
    $("#engineSelect option[value='handwriting']").textContent = state.config.ai_provider === "gemini" && state.config.ai_provider_available
      ? "Handwriting · Gemini Vision"
      : "Handwriting · local best effort";
    $("#fileHelp").textContent = `JPG, JPEG, PNG, WEBP, BMP, PDF, CSV, or Excel XLSX · up to ${state.config.max_upload_mb} MB each`;
    const language = $("#languageSelect"); language.replaceChildren();
    const installed = state.config.ocr_languages?.length ? state.config.ocr_languages : [state.config.default_language || "eng"];
    installed.forEach((code) => {
      const option = document.createElement("option"); option.value = code; option.textContent = languageLabel(code); language.append(option);
    });
    language.value = installed.includes(state.config.default_language) ? state.config.default_language : installed[0];
    const badge = $("#engineBadge");
    badge.textContent = state.config.ocr_available ? "Tesseract ready" : "Tesseract unavailable";
    badge.dataset.state = state.config.ocr_available ? "success" : "error";
    if (!state.config.ocr_available) setStatus("Tesseract was not found. Digital PDF text may still be extracted; scanned pages need Tesseract.", "error");
  } catch (error) {
    $("#engineBadge").textContent = "Server unavailable";
    $("#engineBadge").dataset.state = "error";
    toast(error.message);
  }
}

$("#fileInput").addEventListener("change", (event) => addFiles(event.target.files));
$("#clearFilesButton").addEventListener("click", clearFiles);
$("#processButton").addEventListener("click", processFiles);
function updateProcessButtonLabel() {
  $("#processButton").textContent = $("#modeSelect").value === "prescription"
    ? "Review prescription"
    : $("#engineSelect").value === "handwriting" ? "Recognize handwriting" : "Extract text";
}
$("#modeSelect").addEventListener("change", updateProcessButtonLabel);
$("#engineSelect").addEventListener("change", updateProcessButtonLabel);
$("#targetLanguage").addEventListener("change", () => {
  $("#translateButton").disabled = !state.config.translation_available || !$("#targetLanguage").value;
});
$("#dropZone").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("#fileInput").click(); }
});
["dragenter", "dragover"].forEach((name) => $("#dropZone").addEventListener(name, (event) => { event.preventDefault(); $("#dropZone").classList.add("drag"); }));
["dragleave", "drop"].forEach((name) => $("#dropZone").addEventListener(name, (event) => { event.preventDefault(); $("#dropZone").classList.remove("drag"); }));
$("#dropZone").addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
$("#masterText").addEventListener("input", () => { state.dirty = true; updateDirty(); updateCounts(); });
$("#pageText").addEventListener("input", () => {
  const page = currentJob()?.pages[state.pageIndex];
  if (!page) return;
  page.corrected_text = $("#pageText").value;
  $("#masterText").value = composeCorrectedPages(currentJob());
  state.dirty = true; updateDirty(); updateCounts();
});
$("#saveButton").addEventListener("click", saveCorrections);
$("#copyButton").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#masterText").value); toast("Text copied."); }
  catch { toast("Clipboard access is unavailable. Select and copy the text manually."); }
});
$("#selectAllButton").addEventListener("click", () => { $("#masterText").focus(); $("#masterText").select(); });
$("#clearTextButton").addEventListener("click", () => { $("#masterText").value = ""; state.dirty = true; updateDirty(); updateCounts(); $("#masterText").focus(); });
$("#findNextButton").addEventListener("click", () => {
  const text = $("#masterText"); const query = $("#findInput").value;
  if (!query) { toast("Enter text to find."); return; }
  const from = text.selectionEnd;
  let index = text.value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase(), from);
  if (index < 0) index = text.value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (index < 0) { toast("No match found."); return; }
  text.focus(); text.setSelectionRange(index, index + query.length);
});
$("#replaceButton").addEventListener("click", () => {
  const query = $("#findInput").value;
  if (!query) { toast("Enter text to find."); return; }
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const text = $("#masterText");
  const updated = text.value.replace(new RegExp(escaped, "gi"), () => $("#replaceInput").value);
  if (updated === text.value) { toast("No matches to replace."); return; }
  text.value = updated; state.dirty = true; updateDirty(); updateCounts(); toast("Matching text replaced.");
});
$("#pagePicker").addEventListener("change", (event) => { state.pageIndex = Number(event.target.value); renderPage(); });
$("#prevPageButton").addEventListener("click", () => { state.pageIndex = Math.max(0, state.pageIndex - 1); renderPage(); });
$("#nextPageButton").addEventListener("click", () => { state.pageIndex = Math.min(currentJob().pages.length - 1, state.pageIndex + 1); renderPage(); });
$("#rerunButton").addEventListener("click", rerunCurrentPage);
document.querySelectorAll(".export-button").forEach((button) => button.addEventListener("click", () => exportServerFile(button.dataset.format)));
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.view)));
$("#documentPicker").addEventListener("change", (event) => {
  if (state.dirty && !confirm("Discard unsaved edits and switch documents?")) { event.target.value = String(state.currentIndex); return; }
  state.currentIndex = Number(event.target.value); state.pageIndex = 0; state.dirty = false; renderJob(); loadLanguageInfo();
});
$("#tableCsvButton").addEventListener("click", downloadEditedTables);
$("#formCsvButton").addEventListener("click", downloadEditedForms);
$("#summaryButton").addEventListener("click", () => runAI("summary"));
$("#questionForm").addEventListener("submit", (event) => { event.preventDefault(); runAI("ask"); });
$("#assistantChatForm").addEventListener("submit", sendAssistantMessage);
$("#clearAssistantChatButton").addEventListener("click", () => {
  state.assistantMessages = [];
  renderAssistantChat();
  $("#assistantChatInput").focus();
});
$("#translateButton").addEventListener("click", translateCorrectedText);
$("#prescriptionTxtButton").addEventListener("click", () => exportPrescription("txt"));
$("#prescriptionJsonButton").addEventListener("click", () => exportPrescription("json"));
$("#prescriptionCsvButton").addEventListener("click", () => exportPrescription("csv"));
$("#workspaceNav").addEventListener("click", () => activateMainView("workspace"));
$("#assistantNav").addEventListener("click", () => activateMainView("assistant"));
$("#historyNav").addEventListener("click", () => activateMainView("history"));
$("#refreshHistoryButton").addEventListener("click", () => showHistory($("#historySearch").value));
$("#historySearch").addEventListener("input", () => {
  clearTimeout(state.historyTimer);
  state.historyTimer = setTimeout(() => showHistory($("#historySearch").value), 280);
});
$("#themeButton").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("smartocr-theme", dark ? "dark" : "light"); } catch { /* Theme still works for this page. */ }
});
try { document.documentElement.dataset.theme = localStorage.getItem("smartocr-theme") || "light"; } catch { /* Use the light theme. */ }

initialize();
