"use strict";

const content = document.querySelector("#main-content");
const toast = document.querySelector("#toast");
const navReviewCount = document.querySelector("#nav-review-count");
const navTaskCount = document.querySelector("#nav-task-count");
const state = { objectUrl: null, reviewStatus: "pending", taskStatus: "open", dashboardDirty: true };

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  if (options.id) node.id = options.id;
  if (options.href) node.href = options.href;
  if (options.type) node.type = options.type;
  if (options.name) node.name = options.name;
  if (options.value !== undefined) node.value = options.value;
  if (options.placeholder) node.placeholder = options.placeholder;
  if (options.title) node.title = options.title;
  for (const [name, value] of Object.entries(options.attrs || {})) {
    if (value !== null && value !== undefined) node.setAttribute(name, String(value));
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child) node.append(child);
  }
  return node;
}

function clear(node = content) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function cleanupDocumentUrl() {
  if (state.objectUrl) {
    URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (Array.isArray(payload.detail)) detail = payload.detail.map((item) => item.msg).join("; ");
    } catch (_) {
      // Keep the status-based message when the response is not JSON.
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatBytes(value) {
  if (!Number.isFinite(value)) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function badge(value) {
  return element("span", { className: `badge ${String(value).toLowerCase()}`, text: titleCase(value) });
}

function showToast(message, kind = "success") {
  toast.textContent = message;
  toast.className = `toast${kind === "error" ? " error" : ""}`;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 5000);
}

function setActiveNav(section) {
  for (const link of document.querySelectorAll("[data-nav]")) {
    link.classList.toggle("active", link.dataset.nav === section);
  }
}

function pageHeader(eyebrow, title, description, action = null) {
  const left = element("div", {}, [
    element("div", { className: "eyebrow", text: eyebrow }),
    element("h1", { text: title }),
    element("p", { text: description }),
  ]);
  return element("header", { className: "page-head" }, action ? [left, action] : [left]);
}

function emptyState(message) {
  return element("div", { className: "empty-state", text: message });
}

function renderError(error) {
  clear();
  content.append(
    pageHeader("AdminFlow", "Unable to load this view", "The local API returned an error."),
    element("div", { className: "card error-state", text: error.message }),
  );
}

function linkTitle(text, href) {
  return element("a", { text, href });
}

function summaryCard(label, value) {
  return element("article", { className: "card summary-card" }, [
    element("span", { className: "summary-label", text: label }),
    element("strong", { className: "summary-value", text: value }),
  ]);
}

function summaryParagraphs(summary) {
  return String(summary || "")
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => element("p", { className: "packet-summary", text: paragraph }));
}

function dataChips(data, limit = 3) {
  const wrap = element("div", { className: "data-chips" });
  for (const [key, value] of Object.entries(data || {}).slice(0, limit)) {
    const display = typeof value === "object" ? JSON.stringify(value) : String(value ?? "Not set");
    wrap.append(element("span", { className: "data-chip", text: `${titleCase(key)}: ${display}` }));
  }
  return wrap;
}

async function updateReviewCount() {
  try {
    const reviews = await api("/work-item-reviews?status=pending");
    navReviewCount.textContent = reviews.length ? String(reviews.length) : "";
  } catch (_) {
    navReviewCount.textContent = "";
  }
}

async function updateTaskCount() {
  try {
    const tasks = await api("/internal-tasks?status=open");
    navTaskCount.textContent = tasks.length ? String(tasks.length) : "";
  } catch (_) {
    navTaskCount.textContent = "";
  }
}

async function dashboard() {
  setActiveNav("dashboard");
  clear();
  content.append(pageHeader("Overview", "Dashboard", "A current view of intake, work, and decisions in this local AdminFlow system."));
  const loading = element("div", { className: "card loading-state", text: "Loading dashboard…" });
  content.append(loading);
  const [reviews, tasks, workItems, intakeEvents, workflows] = await Promise.all([
    api("/work-item-reviews?status=pending"), api("/internal-tasks?status=open"), api("/work-items"), api("/intake-events"), api("/workflow-definitions"),
  ]);
  navReviewCount.textContent = reviews.length ? String(reviews.length) : "";
  navTaskCount.textContent = tasks.length ? String(tasks.length) : "";
  const terminalByWorkflow = new Map(workflows.map((workflow) => [workflow.id, new Set(workflow.states.filter((item) => item.terminal).map((item) => item.name))]));
  const terminalCount = workItems.filter((item) => terminalByWorkflow.get(item.workflow_definition_id)?.has(item.current_state)).length;
  clear();
  content.append(
    pageHeader("Overview", "Dashboard", "A current view of intake, work, and decisions in this local AdminFlow system."),
    element("section", { className: "summary-grid", attrs: { "aria-label": "System summary" } }, [
      summaryCard("Pending Reviews", reviews.length),
      summaryCard("Open Work Items", workItems.length - terminalCount),
      summaryCard("Open Tasks", tasks.length),
      summaryCard("Terminal Work Items", terminalCount),
    ]),
  );
  const recentWork = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "Recent work items" }), linkTitle("View all", "#work-items")])]);
  const workList = element("ul", { className: "list" });
  for (const item of workItems.slice(0, 6)) {
    workList.append(element("li", { className: "list-row" }, [
      element("div", {}, [element("p", { className: "row-title" }, [linkTitle(item.title, `#work-item/${item.id}`)]), element("div", { className: "row-meta" }, [element("span", { text: item.work_type }), element("span", { text: formatDate(item.created_at) })])]),
      badge(item.current_state),
    ]));
  }
  recentWork.append(workList.childElementCount ? workList : emptyState("No work items have been created."));
  const recentIntake = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "Recent intake" }), linkTitle("View all", "#intake")])]);
  const intakeList = element("ul", { className: "list" });
  for (const event of intakeEvents.slice(0, 6)) {
    intakeList.append(element("li", { className: "list-row" }, [
      element("div", {}, [element("p", { className: "row-title" }, [linkTitle(event.subject || titleCase(event.source_type), `#intake/${event.id}`)]), element("div", { className: "row-meta" }, [element("span", { text: titleCase(event.source_type) }), element("span", { text: formatDate(event.received_at) })])]),
      badge(event.status),
    ]));
  }
  recentIntake.append(intakeList.childElementCount ? intakeList : emptyState("No intake events have arrived."));
  content.append(element("div", { className: "dashboard-grid" }, [recentWork, recentIntake]));
}

async function reviewQueue(status = state.reviewStatus) {
  state.reviewStatus = status;
  setActiveNav("reviews");
  clear();
  content.append(pageHeader("Human review", "Review Queue", "Pending work is ordered oldest first by the backend."));
  const tabs = element("div", { className: "tabs", attrs: { role: "tablist", "aria-label": "Review status" } });
  for (const value of ["pending", "approved", "rejected"]) {
    const button = element("button", { className: `tab${status === value ? " active" : ""}`, text: titleCase(value), type: "button", attrs: { role: "tab", "aria-selected": status === value } });
    button.addEventListener("click", () => reviewQueue(value).catch(renderError));
    tabs.append(button);
  }
  content.append(tabs, element("div", { className: "loading-state", text: "Loading reviews…" }));
  const reviews = await api(`/work-item-reviews?status=${encodeURIComponent(status)}`);
  const packets = await Promise.all(reviews.map(async (review) => {
    try { return await api(`/work-item-reviews/${review.id}/decision-packet`); } catch (_) { return null; }
  }));
  clear(content.lastElementChild);
  content.lastElementChild.remove();
  const list = element("div", { className: "review-list" });
  reviews.forEach((review, index) => {
    const packet = packets[index];
    const action = review.status === "pending" ? element("a", { className: "button", text: "Review", href: `#review/${review.id}` }) : badge(review.status);
    const factPreview = packet?.key_information.slice(0, 2).map((fact) => element("span", { className: "data-chip", text: `${fact.label}: ${fact.display_value}` })) || [];
    list.append(element("article", { className: "card review-card" }, [
      element("div", {}, [
        element("p", { className: "row-title", text: review.title }),
        element("div", { className: "row-meta" }, [element("span", { text: packet ? `${packet.document_type}${packet.confidence_band ? ` · ${packet.confidence_band}` : ""}` : "Administrative review" }), element("span", { text: `Received ${formatDate(review.created_at)}` })]),
        element("div", { className: "data-chips" }, factPreview),
      ]), action,
    ]));
  });
  content.append(reviews.length ? list : emptyState(status === "pending" ? "No items need review." : `No ${status} reviews.`));
  if (status === "pending") navReviewCount.textContent = reviews.length ? String(reviews.length) : "";
}

function isPdf(artifact) {
  return artifact.content_type?.toLowerCase() === "application/pdf" || artifact.original_filename?.toLowerCase().endsWith(".pdf");
}

async function showArtifact(artifact, host) {
  cleanupDocumentUrl();
  clear(host);
  host.append(element("div", { className: "loading-state", text: "Loading document…" }));
  try {
    const response = await fetch(`/intake-artifacts/${artifact.id}/content`);
    if (!response.ok) throw new Error(response.status === 404 ? "Artifact content is not available." : `Document request failed (${response.status})`);
    const blob = await response.blob();
    state.objectUrl = URL.createObjectURL(blob);
    clear(host);
    if (isPdf(artifact)) {
      host.append(element("iframe", { className: "document-viewer", title: artifact.original_filename || "PDF document", attrs: { src: state.objectUrl } }));
    } else {
      host.append(element("div", { className: "document-empty" }, [
        element("div", {}, [element("p", { text: artifact.original_filename || "Unnamed attachment" }), element("p", { className: "muted small", text: `${artifact.content_type || "Unknown type"} · ${formatBytes(artifact.byte_size)}` }), element("a", { className: "button secondary", text: "Open document", href: state.objectUrl, attrs: { target: "_blank", rel: "noopener", download: artifact.original_filename || "artifact" } })]),
      ]));
    }
  } catch (error) {
    clear(host);
    host.append(element("div", { className: "error-state", text: error.message }));
  }
}

function attachmentPane(artifacts) {
  const body = element("div");
  const card = element("section", { className: "card document-pane" });
  if (!artifacts.length) {
    card.append(element("div", { className: "card-header" }, [element("h2", { text: "Original document" })]), element("div", { className: "document-empty", text: "No source artifacts are attached to this intake event." }));
    return card;
  }
  const select = element("select", { attrs: { "aria-label": "Select attachment" } });
  artifacts.forEach((artifact, index) => select.append(element("option", { value: String(index), text: artifact.original_filename || `Attachment ${index + 1}` })));
  const toolbar = element("div", { className: "attachment-toolbar" }, [element("div", { className: "field" }, [element("label", { text: "Original document", attrs: { for: "attachment-select" } }), select])]);
  select.id = "attachment-select";
  select.addEventListener("change", () => showArtifact(artifacts[Number(select.value)], body));
  card.append(toolbar, body);
  showArtifact(artifacts[0], body);
  return card;
}

function structuredEditor(fieldSchema, values, facts = []) {
  const root = element("div");
  const readers = [];
  const labels = new Map(facts.map((fact) => [fact.key, fact.label]));
  fieldSchema.forEach((definition, index) => {
    const current = Object.hasOwn(values, definition.name) ? values[definition.name] : null;
    const field = element("div", { className: "field" });
    const inputId = `structured-field-${index}`;
    field.append(element("label", { text: `${labels.get(definition.name) || titleCase(definition.name)}${definition.required ? " *" : ""}`, attrs: { for: inputId } }));
    let input;
    if (definition.type === "boolean") {
      input = element("select", { id: inputId }, [!definition.required ? element("option", { value: "", text: "Not identified" }) : null, element("option", { value: "true", text: "Yes" }), element("option", { value: "false", text: "No" })]);
      input.value = current === null ? "" : current === false ? "false" : "true";
    } else if (definition.type === "array_string") {
      input = element("textarea", { id: inputId, value: Array.isArray(current) ? current.join("\n") : "", attrs: { rows: "5" } });
    } else {
      const inputType = definition.type === "date" ? "date" : ["integer", "number"].includes(definition.type) ? "number" : "text";
      input = element("input", { id: inputId, type: inputType, value: current ?? "" });
      if (definition.type === "integer") input.step = "1";
      if (definition.type === "number") input.step = "any";
    }
    field.append(input);
    root.append(field);
    readers.push(() => {
      if (!definition.required && input.value.trim() === "") return [definition.name, null];
      if (definition.type === "string" || definition.type === "date") return [definition.name, input.value.trim()];
      if (definition.type === "boolean") return [definition.name, input.value === "true"];
      if (definition.type === "array_string") return [definition.name, input.value.trim() === "" ? (definition.required ? [] : null) : input.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)];
      const raw = input.value.trim();
      if (!raw) throw new Error(`${definition.name} must be a ${definition.type}.`);
      if (definition.type === "integer" && !/^-?\d+$/.test(raw)) throw new Error(`${definition.name} must be an integer.`);
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || (definition.type === "integer" && !Number.isSafeInteger(parsed))) throw new Error(`${definition.name} must be a valid ${definition.type}.`);
      return [definition.name, parsed];
    });
  });
  return { root, read: () => Object.fromEntries(readers.map((reader) => reader())) };
}

function genericEditor(values) {
  const textarea = element("textarea", { id: "generic-data", value: JSON.stringify(values || {}, null, 2), attrs: { rows: "12", spellcheck: "false" } });
  const root = element("div", { className: "field" }, [element("label", { text: "WorkItem data (JSON object)", attrs: { for: "generic-data" } }), element("p", { className: "field-hint", text: "Changes are submitted only with approval." }), textarea]);
  return { root, read: () => { const parsed = JSON.parse(textarea.value); if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("WorkItem data must be a JSON object."); return parsed; } };
}

function getSavedReviewer() {
  try { return localStorage.getItem("adminflow.reviewer") || ""; } catch (_) { return ""; }
}

function saveReviewer(value) {
  try { localStorage.setItem("adminflow.reviewer", value); } catch (_) { /* Storage may be disabled. */ }
}

async function reviewDetail(reviewId) {
  setActiveNav("reviews");
  clear();
  content.append(element("div", { className: "loading-state", text: "Loading review…" }));
  let packet = await api(`/work-item-reviews/${reviewId}/decision-packet`);
  const review = packet.review;
  clear();
  content.append(pageHeader("Decision packet", packet.title, "Review what came in, what AdminFlow understood, and what approval will do."));
  const reviewCard = element("section", { className: "card review-pane" });
  const packetHost = element("div", { className: "decision-packet" });
  const reviewer = element("input", { id: "reviewer", value: getSavedReviewer(), attrs: { required: "", maxlength: "255", autocomplete: "name" } });
  const notes = element("textarea", { id: "review-notes", attrs: { maxlength: "2000", rows: "4" } });
  reviewCard.append(packetHost);
  content.append(element("div", { className: "review-layout" }, [attachmentPane(packet.artifacts), reviewCard]));

  const section = (label, children, className = "packet-section") => element("section", { className }, [element("h2", { text: label }), ...children]);
  const factList = () => element("dl", { className: "fact-list" }, packet.key_information.map((fact) => element("div", { className: `fact-row${fact.missing ? " missing" : ""}` }, [element("dt", { text: fact.label }), element("dd", { text: fact.display_value })])));
  const originalLink = () => packet.artifacts.length ? element("a", { className: "button secondary", text: "View Original Document", href: `/intake-artifacts/${packet.artifacts[0].id}/content`, attrs: { target: "_blank", rel: "noopener" } }) : element("p", { className: "muted", text: "No original document is available." });

  const resolve = async (decision) => {
    if (!reviewer.value.trim()) { reviewer.focus(); showToast("Your name is required.", "error"); return; }
    saveReviewer(reviewer.value.trim());
    try {
      await api(`/work-item-reviews/${review.id}/resolve`, { method: "POST", body: JSON.stringify({ decision, expected_work_item_state: packet.technical.state, expected_work_item_version: packet.technical.version, reviewer: reviewer.value.trim(), notes: notes.value.trim() || null, ...(decision === "approve" ? { reviewed_data: packet.correction_data, action_plan_id: packet.action_plan?.id || null } : {}) }) });
      cleanupDocumentUrl(); state.dashboardDirty = true;
      showToast(decision === "approve" ? "Approved. The task was created and handed off." : "Moved to manual handling.");
      await Promise.all([updateReviewCount(), updateTaskCount()]); window.location.hash = `work-item/${packet.work_item_id}`;
    } catch (error) {
      if (error.status === 409) { showToast("This item changed while you were reviewing it. Reloading the latest version.", "error"); await reviewDetail(reviewId); }
      else showToast(error.message, "error");
    }
  };

  const drawReadMode = (updated = false) => {
    clear(packetHost);
    const identity = element("header", { className: "packet-identity" }, [element("div", { className: "document-meta", text: `${packet.document_type}${packet.confidence_band ? ` · ${packet.confidence_band}` : ""}${packet.confidence !== null ? ` · ${Math.round(packet.confidence * 100)}%` : ""}` }), element("p", { className: "status-line", text: packet.status_label })]);
    if (updated) identity.append(element("div", { className: "notice", text: "Information updated. Review the revised action before approving." }));
    const attention = packet.attention_items.length
      ? section("Needs your attention", [element("ul", { className: "attention-list" }, packet.attention_items.map((item) => element("li", {}, [element("strong", { text: `⚠ ${item.title}` }), element("p", { text: item.guidance })])))], "packet-section attention-section")
      : null;
    const plan = packet.action_plan
      ? [element("h3", { text: `Create task: ${packet.action_plan.payload.task.title}` }), element("dl", { className: "detail-grid handoff-details" }, [detailItem("Send to", titleCase(packet.action_plan.destination.queue)), detailItem("Responsible role", titleCase(packet.action_plan.destination.role)), detailItem("Assigned to", "Unassigned — available to the responsible queue")]), element("p", { text: packet.action_plan.action_description }), element("p", { text: "Approval performs this internal handoff automatically." }), element("p", { className: "external-effect", text: packet.action_plan.external_effect })]
      : [element("p", { text: "No automated next action is available. Choose manual handling." })];
    const decisionChildren = [];
    if (review.status === "pending") {
      decisionChildren.push(element("div", { className: "decision-fields" }, [element("div", { className: "field" }, [element("label", { text: "Your name", attrs: { for: "reviewer" } }), reviewer]), element("div", { className: "field" }, [element("label", { text: "Optional note", attrs: { for: "review-notes" } }), notes])]), element("div", { className: "action-row decision-actions" }, [element("button", { className: "button quiet", text: "Correct Information", type: "button", attrs: { "data-action": "correct-information" } }), element("button", { className: "button secondary", text: packet.action_plan ? "Handle Manually" : "Reject", type: "button", attrs: { "data-action": "manual" } }), element("button", { className: "button", text: packet.action_plan?.approval_label || "Approve", type: "button", attrs: { "data-action": "approve", disabled: packet.action_plan ? null : "" } })]));
    } else {
      decisionChildren.push(element("p", { text: `${titleCase(review.status)}${review.reviewer ? ` by ${review.reviewer}` : ""}${review.resolved_at ? ` on ${formatDate(review.resolved_at)}` : ""}.` }));
    }
    const summarySource = packet.summary_source === "ai" ? "AI-generated summary" : "Basic summary";
    packetHost.append(identity, section("Summary", [...summaryParagraphs(packet.summary), element("p", { className: "summary-source", text: summarySource })]), section("Key information", [factList()]), ...(attention ? [attention] : []), section("Original document", [originalLink()]), section("What will happen next", plan, "packet-section action-plan-card"), section("Your decision", decisionChildren, "packet-section decision-section"));
    packetHost.querySelector('[data-action="correct-information"]')?.addEventListener("click", drawCorrectionMode);
    packetHost.querySelector('[data-action="manual"]')?.addEventListener("click", () => resolve(packet.action_plan ? "handle_manually" : "reject"));
    packetHost.querySelector('[data-action="approve"]')?.addEventListener("click", () => resolve("approve"));
  };

  const drawCorrectionMode = () => {
    clear(packetHost);
    const editor = packet.correction_schema.length ? structuredEditor(packet.correction_schema, packet.correction_data, packet.key_information) : genericEditor(packet.correction_data);
    const cancel = element("button", { className: "button quiet", text: "Cancel", type: "button" });
    const reviewChanges = element("button", { className: "button", text: "Review Changes", type: "button" });
    packetHost.append(element("header", { className: "packet-identity" }, [element("div", { className: "eyebrow", text: "Correct information" }), element("h2", { text: "Update what AdminFlow will carry forward" }), element("p", { text: "Use the original document to correct any missing or inaccurate information." })]), element("div", { className: "correction-form" }, [editor.root, element("div", { className: "action-row" }, [cancel, reviewChanges])]));
    cancel.addEventListener("click", () => drawReadMode());
    reviewChanges.addEventListener("click", async () => {
      let reviewedData;
      try { reviewedData = editor.read(); } catch (error) { showToast(error.message, "error"); return; }
      try {
        if (packet.action_plan) {
          await api(`/work-item-reviews/${review.id}/action-plan`, { method: "POST", body: JSON.stringify({ expected_work_item_state: packet.technical.state, expected_work_item_version: packet.technical.version, reviewed_data: reviewedData }) });
          packet = await api(`/work-item-reviews/${review.id}/decision-packet`);
        } else {
          packet.correction_data = reviewedData;
          packet.key_information = packet.key_information.map((fact) => ({ ...fact, value: reviewedData[fact.key], display_value: reviewedData[fact.key] ?? "Not identified", missing: reviewedData[fact.key] === null || reviewedData[fact.key] === "" }));
        }
        drawReadMode(true);
      } catch (error) { showToast(error.message, "error"); }
    });
  };

  drawReadMode();
}

function detailItem(label, value) {
  return element("div", { className: "detail-item" }, [element("dt", { text: label }), element("dd", { text: value ?? "—" })]);
}

async function tasks(taskStatus = state.taskStatus) {
  state.taskStatus = taskStatus;
  setActiveNav("tasks");
  clear();
  content.append(pageHeader("Internal work", "Tasks", "Work handed off to AdminFlow queues and responsible roles."));
  const tabs = element("div", { className: "tabs", attrs: { role: "tablist", "aria-label": "Task status" } });
  for (const value of ["open", "completed"]) {
    const button = element("button", { className: `tab${taskStatus === value ? " active" : ""}`, text: titleCase(value), type: "button", attrs: { role: "tab", "aria-selected": taskStatus === value } });
    button.addEventListener("click", () => tasks(value).catch(renderError));
    tabs.append(button);
  }
  const host = element("div", { className: "task-list" }, [element("div", { className: "loading-state", text: "Loading tasks…" })]);
  content.append(tabs, host);
  const rows = await api(`/internal-tasks?status=${encodeURIComponent(taskStatus)}`);
  clear(host);
  for (const task of rows) {
    host.append(element("article", { className: "card task-card" }, [
      element("div", {}, [element("h2", { text: task.title }), element("div", { className: "row-meta" }, [element("span", { text: titleCase(task.queue) }), element("span", { text: `Responsible role: ${titleCase(task.owner_role) || "Not specified"}` }), task.due_at ? element("span", { text: `Due: ${formatDate(task.due_at)}` }) : null, element("span", { text: `Created: ${formatDate(task.created_at)}` })]), badge(task.status)]),
      element("a", { className: "button", text: "Open Task", href: `#task/${task.id}` }),
    ]));
  }
  if (!rows.length) host.append(emptyState(`No ${taskStatus} tasks.`));
  if (taskStatus === "open") navTaskCount.textContent = rows.length ? String(rows.length) : "";
}

async function taskDetail(taskId) {
  setActiveNav("tasks");
  clear();
  content.append(element("div", { className: "loading-state", text: "Loading task…" }));
  let task = await api(`/internal-tasks/${taskId}`);
  const render = () => {
    clear();
    content.append(pageHeader("Internal task", task.title, "The queue handoff, source material, and completion record.", element("a", { className: "button secondary", text: "Back to tasks", href: "#tasks" })));
    const overview = element("section", { className: "card packet-section task-overview" }, [element("div", { className: "eyebrow", text: "Status" }), element("h2", { text: titleCase(task.status) }), element("dl", { className: "detail-grid" }, [detailItem("Queue", titleCase(task.queue)), detailItem("Responsible role", titleCase(task.owner_role) || "Not specified"), detailItem("Assigned to", "Unassigned — available to the responsible queue"), detailItem("Due", task.due_at ? formatDate(task.due_at) : "No due date"), detailItem("Created", formatDate(task.created_at))])]);
    const facts = element("section", { className: "card packet-section" }, [element("h2", { text: "What this task is for" }), element("dl", { className: "fact-list" }, Object.entries(task.facts_snapshot).map(([key, value]) => detailItem(titleCase(key), value === null || value === "" ? "Not identified" : Array.isArray(value) ? value.join(", ") : String(value))))]);
    const links = element("section", { className: "card packet-section" }, [element("h2", { text: "Source" }), element("div", { className: "action-row source-actions" }, [task.source_artifact_ids[0] ? element("a", { className: "button secondary", text: "View Original Document", href: `/intake-artifacts/${task.source_artifact_ids[0]}/content`, attrs: { target: "_blank", rel: "noopener" } }) : null, element("a", { className: "button secondary", text: "View Source Work Item", href: `#work-item/${task.work_item_id}` })])]);
    const completion = element("section", { className: "card packet-section completion-card" }, [element("h2", { text: task.status === "open" ? "Complete task" : "Task completed" })]);
    if (task.status === "completed") {
      completion.append(element("dl", { className: "detail-grid" }, [detailItem("Completed by", task.completed_by), detailItem("Completed", formatDate(task.completed_at)), detailItem("Note", task.completion_note || "No completion note") ]));
    } else {
      const completedBy = element("input", { id: "completed-by", value: getSavedReviewer(), attrs: { required: "", maxlength: "255", autocomplete: "name" } });
      const completionNote = element("textarea", { id: "completion-note", attrs: { maxlength: "2000", rows: "4" } });
      const completeButton = element("button", { className: "button", text: "Mark Task Complete", type: "button" });
      completion.append(element("div", { className: "decision-fields" }, [element("div", { className: "field" }, [element("label", { text: "Your name", attrs: { for: "completed-by" } }), completedBy]), element("div", { className: "field" }, [element("label", { text: "Completion note (optional)", attrs: { for: "completion-note" } }), completionNote])]), element("div", { className: "action-row" }, [completeButton]));
      completeButton.addEventListener("click", async () => {
        if (!completedBy.value.trim()) { completedBy.focus(); showToast("Your name is required.", "error"); return; }
        try {
          task = await api(`/internal-tasks/${task.id}/complete`, { method: "POST", body: JSON.stringify({ completed_by: completedBy.value.trim(), completion_note: completionNote.value.trim() || null }) });
          saveReviewer(completedBy.value.trim());
          await updateTaskCount();
          showToast("Task completed. The source WorkItem is now complete.");
          render();
        } catch (error) { showToast(error.message, "error"); }
      });
    }
    const technical = element("details", { className: "card technical-details" }, [element("summary", { text: "Technical details" }), element("div", { className: "card-body" }, [element("pre", { className: "data-view", text: JSON.stringify({ id: task.id, action_execution_id: task.action_execution_id, work_item_id: task.work_item_id }, null, 2) })])]);
    content.append(element("div", { className: "section-stack cognitive-task" }, [overview, facts, links, completion, technical]));
  };
  render();
}

async function workItems() {
  setActiveNav("work-items");
  clear();
  content.append(pageHeader("Workflow", "Work Items", "Search current deterministic work by title, work type, or state."));
  const search = element("input", { type: "search", placeholder: "Search title", attrs: { "aria-label": "Search work-item titles" } });
  const type = element("select", { attrs: { "aria-label": "Filter by work type" } });
  const itemState = element("select", { attrs: { "aria-label": "Filter by state" } });
  const filters = element("div", { className: "filter-bar" }, [search, type, itemState]);
  const host = element("section", { className: "card" }, [element("div", { className: "loading-state", text: "Loading work items…" })]);
  content.append(filters, host);
  const items = await api("/work-items");
  for (const [select, label, values] of [[type, "All work types", items.map((item) => item.work_type)], [itemState, "All states", items.map((item) => item.current_state)]]) {
    select.append(element("option", { value: "", text: label }));
    [...new Set(values)].sort().forEach((value) => select.append(element("option", { value, text: titleCase(value) })));
  }
  const draw = () => {
    clear(host);
    const query = search.value.trim().toLocaleLowerCase();
    const filtered = items.filter((item) => (!query || item.title.toLocaleLowerCase().includes(query)) && (!type.value || item.work_type === type.value) && (!itemState.value || item.current_state === itemState.value));
    const list = element("ul", { className: "list" });
    filtered.forEach((item) => list.append(element("li", { className: "list-row" }, [element("div", {}, [element("p", { className: "row-title" }, [linkTitle(item.title, `#work-item/${item.id}`)]), element("div", { className: "row-meta" }, [element("span", { text: item.work_type }), element("span", { text: `Version ${item.version}` }), element("span", { text: formatDate(item.created_at) })])]), badge(item.current_state)])));
    host.append(filtered.length ? list : emptyState("No work items match these filters."));
  };
  [search, type, itemState].forEach((control) => control.addEventListener("input", draw));
  draw();
}

async function workItemDetail(id) {
  setActiveNav("work-items");
  clear();
  content.append(element("div", { className: "loading-state", text: "Loading work item…" }));
  const [item, transitions, reviews, plans] = await Promise.all([api(`/work-items/${id}`), api(`/work-items/${id}/transitions`), api(`/work-items/${id}/reviews`), api(`/work-items/${id}/action-plans`)]);
  const artifacts = await api(`/intake-events/${item.intake_event_id}/artifacts`);
  const executions = (await Promise.all(plans.map((plan) => api(`/action-plans/${plan.id}/executions`)))).flat();
  clear();
  if (plans.length) {
    const packet = await api(`/work-items/${id}/decision-packet`);
    content.append(pageHeader("Administrative work", packet.title, "A permanent record of what came in, the human decision, and what happened.", element("a", { className: "button secondary", text: "Back to work items", href: "#work-items" })));
    const statusCard = element("section", { className: "card packet-section status-card" }, [element("div", { className: "eyebrow", text: "Status" }), element("h2", { text: packet.status_label }), element("p", { className: "document-meta", text: `${packet.document_type}${packet.confidence_band ? ` · ${packet.confidence_band}` : ""}` })]);
    const sourceCard = element("section", { className: "card packet-section" }, [element("h2", { text: "What came in" }), ...summaryParagraphs(packet.summary), element("p", { className: "summary-source", text: packet.summary_source === "ai" ? "AI-generated summary" : "Basic summary" }), artifacts.length ? element("a", { className: "button secondary", text: "View Original Document", href: `/intake-artifacts/${artifacts[0].id}/content`, attrs: { target: "_blank", rel: "noopener" } }) : element("p", { text: "No original document is available." })]);
    const facts = element("section", { className: "card packet-section" }, [element("h2", { text: "Key information" }), element("dl", { className: "fact-list" }, packet.key_information.map((fact) => element("div", { className: "fact-row" }, [element("dt", { text: fact.label }), element("dd", { text: fact.display_value })]))) ]);
    const result = packet.action_result;
    const happened = element("section", { className: "card packet-section action-result" }, [element("h2", { text: "What happened" }), result ? element("div", {}, [element("h3", { text: result.message }), element("p", { text: result.task_status === "open" ? "The document was approved and handed off. The follow-up task still needs to be completed." : "The follow-up task has been completed." }), element("dl", { className: "detail-grid" }, [detailItem("Task", result.task_title), detailItem("Queue", result.queue), detailItem("Responsible role", result.owner_role), detailItem("Task status", titleCase(result.task_status)), detailItem("Task created", formatDate(result.task_created_at))]), result.task_id ? element("a", { className: "button secondary", text: "View Task", href: `#task/${result.task_id}` }) : null]) : element("p", { text: packet.status_label === "Handled manually" ? "The proposed Action Plan was not executed. This item was preserved for manual handling." : "No action result has been recorded." })]);
    const reviewEntry = packet.review;
    const reviewCard = element("section", { className: "card packet-section" }, [element("h2", { text: "Document review" }), reviewEntry ? element("p", { text: `${titleCase(reviewEntry.status)}${reviewEntry.reviewer ? ` by ${reviewEntry.reviewer}` : ""}${reviewEntry.resolved_at ? ` on ${formatDate(reviewEntry.resolved_at)}` : ""}.` }) : element("p", { text: "No review record is available." }), reviewEntry?.notes ? element("p", { text: reviewEntry.notes }) : null]);
    const taskCompletion = result?.task_status === "completed" ? element("section", { className: "card packet-section" }, [element("h2", { text: "Task completion" }), element("dl", { className: "detail-grid" }, [detailItem("Completed by", result.task_completed_by), detailItem("Completed", formatDate(result.task_completed_at)), detailItem("Note", result.task_completion_note || "No completion note")])]) : null;
    const actionCard = historyCard("Action history", plans.map((plan) => { const execution = executions.find((value) => value.action_plan_id === plan.id); return { title: plan.action_title, meta: execution ? `${titleCase(execution.status)} · ${formatDate(execution.completed_at)}` : plan.superseded_at ? "Superseded after corrected information" : "Not executed" }; }));
    const technical = element("details", { className: "card technical-details" }, [element("summary", { text: "Technical details" }), element("div", { className: "card-body" }, [element("pre", { className: "data-view", text: JSON.stringify({ ...packet.technical, data: item.data, transitions }, null, 2) })])]);
    content.append(element("div", { className: "section-stack cognitive-work-item" }, [statusCard, sourceCard, facts, happened, reviewCard, taskCompletion, actionCard, technical]));
    return;
  }
  content.append(pageHeader("Work item", item.title, "Read-only workflow state, source lineage, and audit history.", element("a", { className: "button secondary", text: "Back to work items", href: "#work-items" })));
  const overview = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "Details" }), badge(item.current_state)]), element("div", { className: "card-body" }, [element("dl", { className: "detail-grid" }, [detailItem("Work type", item.work_type), detailItem("Version", item.version), detailItem("Workflow definition", item.workflow_definition_id), detailItem("Intake event", item.intake_event_id), detailItem("Structured extraction", item.document_structured_extraction_id), detailItem("Updated", formatDate(item.updated_at))])])]);
  const data = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "WorkItem data" })]), element("div", { className: "card-body" }, [element("pre", { className: "data-view", text: JSON.stringify(item.data, null, 2) })])]);
  const transitionCard = historyCard("Transition history", transitions.map((entry) => ({ title: `Version ${entry.version}: ${titleCase(entry.from_state || "Created")} → ${titleCase(entry.to_state)}`, meta: `${formatDate(entry.created_at)}${entry.reason ? ` · ${entry.reason}` : ""}` })));
  const reviewCard = historyCard("Review history", reviews.map((entry) => ({ title: `${titleCase(entry.status)} in ${titleCase(entry.state)}`, meta: `${formatDate(entry.created_at)}${entry.reviewer ? ` · ${entry.reviewer}` : ""}` })));
  const actionCard = historyCard("Action history", plans.map((plan) => { const execution = executions.find((value) => value.action_plan_id === plan.id); return { title: `${plan.action_title} · revision ${plan.revision}`, meta: execution ? `${titleCase(execution.status)} · ${formatDate(execution.completed_at)}` : plan.superseded_at ? "Superseded before authorization" : "Awaiting authorization" }; }));
  content.append(element("div", { className: "section-stack" }, [overview, attachmentPane(artifacts), data, actionCard, transitionCard, reviewCard]));
}

function historyCard(title, entries) {
  const card = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: title })])]);
  if (!entries.length) { card.append(emptyState("No history recorded.")); return card; }
  const list = element("ol", { className: "timeline" });
  entries.forEach((entry) => list.append(element("li", {}, [element("strong", { text: entry.title }), element("div", { className: "muted small", text: entry.meta })])));
  card.append(element("div", { className: "card-body" }, [list]));
  return card;
}

async function intake() {
  setActiveNav("intake");
  clear();
  content.append(pageHeader("Sources", "Intake", "Recent incoming events and their preserved source artifacts.", element("a", { className: "button", text: "+ New Intake", href: "#new-intake" })), element("div", { className: "card loading-state", text: "Loading intake…" }));
  const events = await api("/intake-events");
  const artifactLists = await Promise.all(events.map((event) => api(`/intake-events/${event.id}/artifacts`)));
  content.lastElementChild.remove();
  const card = element("section", { className: "card" });
  const list = element("ul", { className: "list" });
  events.forEach((event, index) => list.append(element("li", { className: "list-row" }, [element("div", {}, [element("p", { className: "row-title" }, [linkTitle(event.subject || titleCase(event.source_type), `#intake/${event.id}`)]), element("div", { className: "row-meta" }, [element("span", { text: formatDate(event.received_at) }), element("span", { text: titleCase(event.source_type) }), event.sender ? element("span", { text: event.sender }) : null, element("span", { text: `${artifactLists[index].length} attachment${artifactLists[index].length === 1 ? "" : "s"}` })])]), badge(event.status)])));
  card.append(events.length ? list : emptyState("No intake events have arrived."));
  content.append(card);
}

async function intakeDetail(id) {
  setActiveNav("intake");
  clear();
  content.append(element("div", { className: "loading-state", text: "Loading intake event…" }));
  const [event, artifacts] = await Promise.all([api(`/intake-events/${id}`), api(`/intake-events/${id}/artifacts`)]);
  const extractionLists = await Promise.all(artifacts.map((artifact) => api(`/intake-artifacts/${artifact.id}/extractions`)));
  const relatedItems = (await api("/work-items")).filter((item) => item.intake_event_id === id);
  const relatedReviews = await Promise.all(relatedItems.map((item) => api(`/work-items/${item.id}/reviews`)));
  clear();
  content.append(pageHeader("Intake event", event.subject || titleCase(event.source_type), "Source details and immutable attachments.", element("a", { className: "button secondary", text: "Back to intake", href: "#intake" })));
  const details = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "Event details" }), badge(event.status)]), element("div", { className: "card-body" }, [element("dl", { className: "detail-grid" }, [detailItem("Received", formatDate(event.received_at)), detailItem("Source type", titleCase(event.source_type)), detailItem("Sender", event.sender), detailItem("Recipient", event.recipient), detailItem("External ID", event.external_id), detailItem("Attachments", artifacts.length)]), event.body_text ? element("div", {}, [element("h3", { text: "Body text" }), element("pre", { className: "data-view", text: event.body_text })]) : null])]);
  content.append(element("div", { className: "section-stack" }, [details, window.ManualIntake.relatedWorkList(relatedItems, relatedReviews), window.ManualIntake.artifactStatusList(artifacts, extractionLists), attachmentPane(artifacts)]));
}

async function route() {
  cleanupDocumentUrl();
  const [name = "dashboard", id] = window.location.hash.slice(1).split("/");
  if (name !== "new-intake") window.ManualIntake.clearFiles();
  try {
    if (name === "new-intake") await window.ManualIntake.render();
    else if (name === "reviews") await reviewQueue();
    else if (name === "review" && id) await reviewDetail(id);
    else if (name === "tasks") await tasks();
    else if (name === "task" && id) await taskDetail(id);
    else if (name === "work-items") await workItems();
    else if (name === "work-item" && id) await workItemDetail(id);
    else if (name === "intake" && id) await intakeDetail(id);
    else if (name === "intake") await intake();
    else await dashboard();
    content.focus({ preventScroll: true });
  } catch (error) {
    renderError(error);
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", cleanupDocumentUrl);
updateReviewCount();
updateTaskCount();
route();
