"use strict";

window.ManualIntake = (() => {
  let files = [];
  const content = document.querySelector("#main-content");
  const toast = document.querySelector("#toast");

  function el(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = String(options.text);
    if (options.id) node.id = options.id;
    if (options.href) node.href = options.href;
    if (options.type) node.type = options.type;
    if (options.value !== undefined) node.value = options.value;
    for (const [name, value] of Object.entries(options.attrs || {})) node.setAttribute(name, String(value));
    for (const child of Array.isArray(children) ? children : [children]) if (child) node.append(child);
    return node;
  }

  function empty(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  async function request(path, options = {}) {
    const form = options.body instanceof FormData;
    const response = await fetch(path, {
      ...options,
      headers: options.body && !form ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
    });
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === "string") detail = payload.detail;
      } catch (_) {
        // Retain the status-based message for non-JSON errors.
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function notify(message, failed = false) {
    toast.textContent = message;
    toast.className = `toast${failed ? " error" : ""}`;
    toast.hidden = false;
    window.setTimeout(() => { toast.hidden = true; }, 5000);
  }

  function formatBytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 ** 2).toFixed(1)} MB`;
  }

  function isPdfFile(file) {
    return file.type.toLowerCase() === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  }

  function isPdfArtifact(artifact) {
    return artifact.content_type?.toLowerCase() === "application/pdf" || artifact.original_filename?.toLowerCase().endsWith(".pdf");
  }

  function shouldRunOcr(status) {
    return status === "partial" || status === "needs_ocr";
  }

  function hasReadableText(extraction) {
    return extraction.status === "extracted" || (
      extraction.status === "partial"
      && typeof extraction.text_content === "string"
      && extraction.text_content.trim().length > 0
    );
  }

  function addFiles(incoming, host) {
    for (const file of incoming) {
      const duplicate = files.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
      if (!duplicate) files.push(file);
    }
    renderFiles(host);
  }

  function renderFiles(host) {
    empty(host);
    if (!files.length) {
      host.append(el("p", { className: "muted", text: "No documents selected." }));
      return;
    }
    const list = el("ul", { className: "selected-files" });
    files.forEach((file, index) => {
      const remove = el("button", { className: "button secondary compact", text: "Remove", type: "button", attrs: { "aria-label": `Remove ${file.name}` } });
      remove.addEventListener("click", () => { files.splice(index, 1); renderFiles(host); });
      list.append(el("li", { className: "selected-file" }, [el("div", {}, [el("strong", { text: file.name }), el("span", { className: "muted small", text: formatBytes(file.size) })]), remove]));
    });
    host.append(list);
  }

  function progressRow(file) {
    const status = el("span", { className: "processing-status", text: "Waiting" });
    return {
      node: el("li", { className: "processing-row" }, [el("div", {}, [el("strong", { text: file.name }), el("span", { className: "muted small", text: formatBytes(file.size) })]), status]),
      update(message, kind = "") { status.textContent = message; status.className = `processing-status ${kind}`.trim(); },
    };
  }

  async function processExtraction(extractionId, progress) {
    progress.update("Organizing document…");
    try {
      const result = await request(`/document-extractions/${extractionId}/process`, { method: "POST", body: JSON.stringify({ profile_id: "generic_office" }) });
      progress.update("Ready for review", "ready");
      return { ok: true, reviewId: result.review_id };
    } catch (error) {
      const message = error.status === 503 ? "Document received, but document processing is not configured." : "Document received, but document processing could not be completed.";
      progress.update(message, "failed");
      return { ok: false };
    }
  }

  async function processFile(eventId, file, progress) {
    progress.update("Uploading");
    const body = new FormData();
    body.append("file", file, file.name);
    let artifact;
    try {
      artifact = await request(`/intake-events/${eventId}/artifacts`, { method: "POST", body });
    } catch (error) {
      progress.update(`Upload failed — ${error.message}`, "failed");
      return { ok: false };
    }
    if (!isPdfFile(file)) {
      progress.update("Received — text processing is not available for this file type", "unavailable");
      return { ok: true };
    }
    progress.update("Extracting text");
    let extraction;
    try {
      extraction = await request(`/intake-artifacts/${artifact.id}/extract`, { method: "POST" });
    } catch (error) {
      if (error.status === 415) progress.update("Received — text processing is not supported for this file", "unavailable");
      else progress.update(`Document received, but text processing could not be completed — ${error.message}`, "failed");
      return { ok: false };
    }
    if (extraction.status === "extracted") {
      return processExtraction(extraction.id, progress);
    }
    if (extraction.status === "password_required") {
      progress.update("Document received. PDF password is required before text can be processed.", "unavailable");
      return { ok: false };
    }
    if (extraction.status === "failed") {
      progress.update("Document received, but text processing could not be completed", "failed");
      return { ok: false };
    }
    if (!shouldRunOcr(extraction.status)) {
      progress.update(`Document received — ${extraction.status}`, "unavailable");
      return { ok: false };
    }
    progress.update("Running local OCR…");
    try {
      const ocr = await request(`/document-extractions/${extraction.id}/ocr`, { method: "POST" });
      if (hasReadableText(ocr)) {
        return processExtraction(ocr.id, progress);
      }
      if (ocr.status === "partial" || ocr.status === "needs_ocr") progress.update("Document received, but some text could not be processed", "failed");
      else progress.update("Document received, but text processing could not be completed", "failed");
      return { ok: false };
    } catch (error) {
      progress.update(`Document received, but text processing could not be completed — ${error.message}`, "failed");
      return { ok: false };
    }
  }

  async function render() {
    empty(content);
    const heading = el("header", { className: "page-head" }, [el("div", {}, [el("div", { className: "eyebrow", text: "Local intake" }), el("h1", { text: "Manual Intake" }), el("p", { text: "Preserve local documents and process eligible PDFs with native extraction and local OCR." })])]);
    const form = el("form", { className: "card manual-intake-form" });
    const subject = el("input", { id: "manual-subject", attrs: { maxlength: "500", autocomplete: "off" } });
    const sender = el("input", { id: "manual-sender", attrs: { maxlength: "255", autocomplete: "off" } });
    const notes = el("textarea", { id: "manual-notes", attrs: { rows: "5" } });
    const input = el("input", { id: "manual-files", type: "file", attrs: { multiple: "", hidden: "" } });
    const drop = el("label", { className: "drop-zone", attrs: { for: "manual-files", tabindex: "0" } }, [el("strong", { text: "Drop documents here" }), el("span", { text: "or click to choose files" })]);
    const selected = el("div", { attrs: { "aria-live": "polite" } });
    const progressHost = el("div", { className: "processing-panel", attrs: { "aria-live": "polite" } });
    const eventLink = el("div");
    const cancel = el("a", { className: "button secondary", text: "Cancel", href: "#intake" });
    const submit = el("button", { className: "button", text: "Add Intake", type: "submit" });
    cancel.addEventListener("click", (event) => { if (submit.disabled) event.preventDefault(); });
    form.append(
      el("div", { className: "field" }, [el("label", { text: "Subject", attrs: { for: "manual-subject" } }), subject]),
      el("div", { className: "field" }, [el("label", { text: "From / Sender", attrs: { for: "manual-sender" } }), sender]),
      el("div", { className: "field" }, [el("label", { text: "Notes", attrs: { for: "manual-notes" } }), notes]),
      el("fieldset", { className: "file-fieldset" }, [el("legend", { text: "Documents" }), input, drop, selected]),
      progressHost, eventLink, el("div", { className: "action-row" }, [cancel, submit]),
    );
    const providerStatus = el("p", { className: "provider-status", text: "Document processing: Loading…" });
    content.append(heading, providerStatus, form);
    request("/document-processing/config").then((config) => { providerStatus.textContent = "Document processing: " + config.provider_display_name + (config.configured ? "" : " — not configured"); }).catch(() => { providerStatus.textContent = "Document processing: Configuration unavailable"; });
    renderFiles(selected);
    input.addEventListener("change", () => { addFiles(input.files, selected); input.value = ""; });
    drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); } });
    for (const name of ["dragenter", "dragover"]) drop.addEventListener(name, (event) => { event.preventDefault(); drop.classList.add("dragging"); });
    for (const name of ["dragleave", "drop"]) drop.addEventListener(name, (event) => { event.preventDefault(); drop.classList.remove("dragging"); });
    drop.addEventListener("drop", (event) => addFiles(event.dataTransfer.files, selected));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!files.length) { notify("Select at least one document.", true); input.focus(); return; }
      const selectedFiles = [...files];
      submit.disabled = true;
      cancel.setAttribute("aria-disabled", "true");
      empty(progressHost);
      empty(eventLink);
      const rows = selectedFiles.map(progressRow);
      const list = el("ul", { className: "processing-list" });
      rows.forEach((row) => list.append(row.node));
      progressHost.append(el("h2", { text: "Processing documents" }), list);
      let intakeEvent;
      try {
        intakeEvent = await request("/intake-events", { method: "POST", body: JSON.stringify({ source_type: "manual_upload", external_id: null, sender: sender.value || null, recipient: null, subject: subject.value || null, body_text: notes.value || null, received_at: new Date().toISOString(), raw_metadata: {} }) });
      } catch (error) {
        notify(`Intake could not be created — ${error.message}`, true);
        submit.disabled = false;
        cancel.removeAttribute("aria-disabled");
        return;
      }
      let complete = true;
      const reviewIds = [];
      for (let index = 0; index < selectedFiles.length; index += 1) {
        const outcome = await processFile(intakeEvent.id, selectedFiles[index], rows[index]);
        if (!outcome.ok) complete = false;
        if (outcome.reviewId) {
          reviewIds.push(outcome.reviewId);
          eventLink.append(el("a", { className: "button secondary", text: "Review " + selectedFiles[index].name, href: "#review/" + outcome.reviewId }));
        }
      }
      eventLink.append(el("a", { className: "button secondary", text: "Open IntakeEvent", href: `#intake/${intakeEvent.id}` }));
      if (complete) {
        notify("Intake received and document processing completed.");
        files = [];
        window.setTimeout(() => { window.location.hash = selectedFiles.length === 1 && reviewIds.length === 1 ? "review/" + reviewIds[0] : "intake/" + intakeEvent.id; }, 700);
      } else {
        notify("Intake was created, but one or more documents need attention.", true);
      }
    });
  }

  function extractionText(artifact, extraction) {
    if (!extraction) return isPdfArtifact(artifact) ? "Text processing: No extraction recorded" : "Text processing: Not available";
    if (extraction.status === "extracted") return `${extraction.extraction_method === "pdf_text_ocr" ? "Text: OCR extracted" : "Text: Extracted"} · Pages: ${extraction.page_count} · Characters: ${extraction.character_count.toLocaleString()}`;
    if (extraction.status === "password_required") return "Text: PDF password required";
    if (extraction.status === "needs_ocr") return `Text: Needs OCR · Pages: ${extraction.page_count}`;
    if (extraction.status === "partial") return `Text: Partially extracted · Pages: ${extraction.page_count} · Characters: ${extraction.character_count.toLocaleString()}`;
    return `Text processing: Failed${extraction.error_message ? ` — ${extraction.error_message}` : ""}`;
  }

  function artifactStatusList(artifacts, extractionLists) {
    const card = el("section", { className: "card" }, [el("div", { className: "card-header" }, [el("h2", { text: "Attachment processing" })])]);
    if (!artifacts.length) { card.append(el("div", { className: "empty-state", text: "No attachments." })); return card; }
    const list = el("ul", { className: "artifact-status-list" });
    artifacts.forEach((artifact, index) => {
      const extractions = extractionLists[index];
      const current = extractions.find((item) => item.extraction_method === "pdf_text_ocr") || extractions[0] || null;
      list.append(el("li", {}, [el("strong", { text: artifact.original_filename || "Unnamed attachment" }), el("span", { className: "muted small", text: `${artifact.content_type || "Unknown type"} · ${formatBytes(artifact.byte_size)}` }), el("span", { text: extractionText(artifact, current) })]));
    });
    card.append(list);
    return card;
  }

  function relatedWorkList(items, reviewLists) {
    const card = el("section", { className: "card" }, [el("div", { className: "card-header" }, [el("h2", { text: "Related work" })])]);
    if (!items.length) { card.append(el("div", { className: "empty-state", text: "No related WorkItems." })); return card; }
    const list = el("ul", { className: "list" });
    items.forEach((item, index) => { const pending = reviewLists[index].find((review) => review.status === "pending"); list.append(el("li", { className: "list-row" }, [el("div", {}, [el("strong", { text: item.title }), el("div", { className: "row-meta" }, [el("span", { text: item.work_type }), el("span", { text: item.current_state }), el("span", { text: "Version " + item.version })])]), pending ? el("a", { className: "button", text: "Review", href: "#review/" + pending.id }) : null])); });
    card.append(list); return card;
  }

  return { render, clearFiles() { files = []; }, shouldRunOcr, artifactStatusList, relatedWorkList };
})();
