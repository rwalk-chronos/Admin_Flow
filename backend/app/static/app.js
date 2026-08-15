"use strict";

const content = document.querySelector("#main-content");
const toast = document.querySelector("#toast");
const navReviewCount = document.querySelector("#nav-review-count");
const state = { objectUrl: null, reviewStatus: "pending", dashboardDirty: true };

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

async function dashboard() {
  setActiveNav("dashboard");
  clear();
  content.append(pageHeader("Overview", "Dashboard", "A current view of intake, work, and decisions in this local AdminFlow system."));
  const loading = element("div", { className: "card loading-state", text: "Loading dashboard…" });
  content.append(loading);
  const [reviews, workItems, intakeEvents, workflows] = await Promise.all([
    api("/work-item-reviews?status=pending"), api("/work-items"), api("/intake-events"), api("/workflow-definitions"),
  ]);
  navReviewCount.textContent = reviews.length ? String(reviews.length) : "";
  const terminalByWorkflow = new Map(workflows.map((workflow) => [workflow.id, new Set(workflow.states.filter((item) => item.terminal).map((item) => item.name))]));
  const terminalCount = workItems.filter((item) => terminalByWorkflow.get(item.workflow_definition_id)?.has(item.current_state)).length;
  clear();
  content.append(
    pageHeader("Overview", "Dashboard", "A current view of intake, work, and decisions in this local AdminFlow system."),
    element("section", { className: "summary-grid", attrs: { "aria-label": "System summary" } }, [
      summaryCard("Pending Reviews", reviews.length),
      summaryCard("Open Work Items", workItems.length - terminalCount),
      summaryCard("Recent Intake", intakeEvents.length),
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
  clear(content.lastElementChild);
  content.lastElementChild.remove();
  const list = element("div", { className: "review-list" });
  for (const review of reviews) {
    const action = review.status === "pending" ? element("a", { className: "button", text: "Review", href: `#review/${review.id}` }) : badge(review.status);
    list.append(element("article", { className: "card review-card" }, [
      element("div", {}, [
        element("p", { className: "row-title", text: review.title }),
        element("div", { className: "row-meta" }, [element("span", { text: review.work_type }), element("span", { text: `Review state: ${titleCase(review.state)}` }), element("span", { text: formatDate(review.created_at) })]),
        dataChips(review.work_item_data),
      ]), action,
    ]));
  }
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

function structuredEditor(fieldSchema, values) {
  const root = element("div");
  const readers = [];
  fieldSchema.forEach((definition, index) => {
    const current = Object.hasOwn(values, definition.name) ? values[definition.name] : null;
    const field = element("div", { className: "field" });
    const inputId = `structured-field-${index}`;
    field.append(element("label", { text: `${definition.name}${definition.required ? " *" : ""}`, attrs: { for: inputId } }), element("p", { className: "field-hint", text: `${definition.description} · ${definition.type}` }));
    let input;
    if (definition.type === "boolean") {
      input = element("select", { id: inputId }, [element("option", { value: "true", text: "True" }), element("option", { value: "false", text: "False" })]);
      input.value = current === false ? "false" : "true";
    } else if (definition.type === "array_string") {
      input = element("textarea", { id: inputId, value: Array.isArray(current) ? current.join("\n") : "", attrs: { rows: "5" } });
    } else {
      const inputType = definition.type === "date" ? "date" : ["integer", "number"].includes(definition.type) ? "number" : "text";
      input = element("input", { id: inputId, type: inputType, value: current ?? "" });
      if (definition.type === "integer") input.step = "1";
      if (definition.type === "number") input.step = "any";
    }
    let enabled = true;
    if (!definition.required) {
      enabled = current !== null;
      const toggle = element("input", { type: "checkbox", attrs: { "aria-label": `Set ${definition.name}` } });
      toggle.checked = enabled;
      input.disabled = !enabled;
      toggle.addEventListener("change", () => { enabled = toggle.checked; input.disabled = !enabled; });
      field.append(element("label", { className: "optional-toggle" }, [toggle, document.createTextNode("Set optional value")]));
    }
    field.append(input);
    root.append(field);
    readers.push(() => {
      if (!definition.required && !enabled) return [definition.name, null];
      if (definition.type === "string" || definition.type === "date") return [definition.name, input.value];
      if (definition.type === "boolean") return [definition.name, input.value === "true"];
      if (definition.type === "array_string") return [definition.name, input.value === "" ? [] : input.value.split(/\r?\n/)];
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
  const review = await api(`/work-item-reviews/${reviewId}`);
  const workItem = await api(`/work-items/${review.work_item_id}`);
  const [artifacts, structured] = await Promise.all([
    api(`/intake-events/${workItem.intake_event_id}/artifacts`),
    workItem.document_structured_extraction_id ? api(`/document-structured-extractions/${workItem.document_structured_extraction_id}`) : Promise.resolve(null),
  ]);
  clear();
  content.append(pageHeader("Human review", workItem.title, "Compare the original source with the data AdminFlow will carry forward."));
  const reviewCard = element("section", { className: "card review-pane" }, [element("div", { className: "card-header" }, [element("h2", { text: "AdminFlow review" }), badge(review.status)])]);
  const form = element("form", { className: "review-form" });
  form.append(element("dl", { className: "detail-grid" }, [detailItem("Work type", workItem.work_type), detailItem("State", workItem.current_state), detailItem("Version", workItem.version), detailItem("Received", formatDate(review.created_at))]));
  const editor = structured ? structuredEditor(structured.field_schema, workItem.data) : genericEditor(workItem.data);
  form.append(element("hr"), editor.root);
  const reviewer = element("input", { id: "reviewer", value: getSavedReviewer(), attrs: { required: "", maxlength: "255", autocomplete: "name" } });
  const notes = element("textarea", { id: "review-notes", attrs: { maxlength: "2000", rows: "4" } });
  form.append(element("div", { className: "field" }, [element("label", { text: "Reviewer *", attrs: { for: "reviewer" } }), reviewer]), element("div", { className: "field" }, [element("label", { text: "Notes", attrs: { for: "review-notes" } }), notes]));
  const rejectButton = element("button", { className: "button danger", text: "Reject", type: "button" });
  const approveButton = element("button", { className: "button", text: "Approve", type: "button" });
  const actions = element("div", { className: "action-row" }, [rejectButton, approveButton]);
  if (review.status !== "pending") {
    rejectButton.disabled = true;
    approveButton.disabled = true;
  }
  form.append(actions);
  reviewCard.append(form);
  content.append(element("div", { className: "review-layout" }, [attachmentPane(artifacts), reviewCard]));

  const resolve = async (decision) => {
    if (!reviewer.value.trim()) { reviewer.focus(); showToast("Reviewer is required.", "error"); return; }
    let reviewedData;
    if (decision === "approve") {
      try { reviewedData = editor.read(); } catch (error) { showToast(error.message, "error"); return; }
    }
    saveReviewer(reviewer.value.trim());
    rejectButton.disabled = true;
    approveButton.disabled = true;
    try {
      await api(`/work-item-reviews/${review.id}/resolve`, { method: "POST", body: JSON.stringify({ decision, expected_work_item_state: workItem.current_state, expected_work_item_version: workItem.version, reviewer: reviewer.value.trim(), notes: notes.value.trim() || null, ...(decision === "approve" ? { reviewed_data: reviewedData } : {}) }) });
      cleanupDocumentUrl();
      state.dashboardDirty = true;
      showToast(`Review ${decision === "approve" ? "approved" : "rejected"} successfully.`);
      await updateReviewCount();
      window.location.hash = "reviews";
    } catch (error) {
      if (error.status === 409) {
        showToast("This item changed while you were reviewing it. Reloading the latest version.", "error");
        await reviewDetail(reviewId);
      } else {
        showToast(error.message, "error");
        rejectButton.disabled = false;
        approveButton.disabled = false;
      }
    }
  };
  approveButton.addEventListener("click", () => resolve("approve"));
  rejectButton.addEventListener("click", () => resolve("reject"));
}

function detailItem(label, value) {
  return element("div", { className: "detail-item" }, [element("dt", { text: label }), element("dd", { text: value ?? "—" })]);
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
  const [item, transitions, reviews] = await Promise.all([api(`/work-items/${id}`), api(`/work-items/${id}/transitions`), api(`/work-items/${id}/reviews`)]);
  clear();
  content.append(pageHeader("Work item", item.title, "Read-only workflow state, source lineage, and audit history.", element("a", { className: "button secondary", text: "Back to work items", href: "#work-items" })));
  const overview = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "Details" }), badge(item.current_state)]), element("div", { className: "card-body" }, [element("dl", { className: "detail-grid" }, [detailItem("Work type", item.work_type), detailItem("Version", item.version), detailItem("Workflow definition", item.workflow_definition_id), detailItem("Intake event", item.intake_event_id), detailItem("Structured extraction", item.document_structured_extraction_id), detailItem("Updated", formatDate(item.updated_at))])])]);
  const data = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "WorkItem data" })]), element("div", { className: "card-body" }, [element("pre", { className: "data-view", text: JSON.stringify(item.data, null, 2) })])]);
  const transitionCard = historyCard("Transition history", transitions.map((entry) => ({ title: `Version ${entry.version}: ${titleCase(entry.from_state || "Created")} → ${titleCase(entry.to_state)}`, meta: `${formatDate(entry.created_at)}${entry.reason ? ` · ${entry.reason}` : ""}` })));
  const reviewCard = historyCard("Review history", reviews.map((entry) => ({ title: `${titleCase(entry.status)} in ${titleCase(entry.state)}`, meta: `${formatDate(entry.created_at)}${entry.reviewer ? ` · ${entry.reviewer}` : ""}` })));
  content.append(element("div", { className: "section-stack" }, [overview, data, transitionCard, reviewCard]));
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
  clear();
  content.append(pageHeader("Intake event", event.subject || titleCase(event.source_type), "Source details and immutable attachments.", element("a", { className: "button secondary", text: "Back to intake", href: "#intake" })));
  const details = element("section", { className: "card" }, [element("div", { className: "card-header" }, [element("h2", { text: "Event details" }), badge(event.status)]), element("div", { className: "card-body" }, [element("dl", { className: "detail-grid" }, [detailItem("Received", formatDate(event.received_at)), detailItem("Source type", titleCase(event.source_type)), detailItem("Sender", event.sender), detailItem("Recipient", event.recipient), detailItem("External ID", event.external_id), detailItem("Attachments", artifacts.length)]), event.body_text ? element("div", {}, [element("h3", { text: "Body text" }), element("pre", { className: "data-view", text: event.body_text })]) : null])]);
  content.append(element("div", { className: "section-stack" }, [details, window.ManualIntake.artifactStatusList(artifacts, extractionLists), attachmentPane(artifacts)]));
}

async function route() {
  cleanupDocumentUrl();
  const [name = "dashboard", id] = window.location.hash.slice(1).split("/");
  if (name !== "new-intake") window.ManualIntake.clearFiles();
  try {
    if (name === "new-intake") await window.ManualIntake.render();
    else if (name === "reviews") await reviewQueue();
    else if (name === "review" && id) await reviewDetail(id);
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
route();
