/* DocTalk frontend — vanilla JS, no build step.
 *
 * Everything rendered from a document or a model reply is inserted as a text
 * node, never as HTML. Uploaded content is untrusted, and the model echoes it
 * back in answers and source previews, so string-built markup would be an
 * injection path straight from an uploaded file into the page.
 */

const $ = (id) => document.getElementById(id);

const els = {
  slots: $("slots"),
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  uploading: $("uploading"),
  uploadingLabel: $("uploading-label"),
  doclist: $("doclist"),
  report: $("report"),
  messages: $("messages"),
  composer: $("composer"),
  question: $("question"),
  send: $("send"),
};

let workspace = { documents: [], used: 0, capacity: 5, remaining: 5 };
let busy = false;

/* ------------------------------------------------------------- utilities */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(path, className) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path);
  svg.appendChild(p);
  return svg;
}

const ICON_TRASH = "M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m2 0v12a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V7";
const ICON_CHEVRON = "M9 6l6 6-6 6";

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* non-JSON error body — keep the status line */ }
    throw new Error(detail);
  }
  return response.json();
}

/* ------------------------------------------------------------- workspace */

function renderWorkspace(state) {
  workspace = state;

  els.slots.textContent = `${state.used} / ${state.capacity}`;
  els.slots.dataset.full = String(state.remaining === 0);

  const full = state.remaining === 0;
  els.dropzone.setAttribute("aria-disabled", String(full));
  els.dropzone.querySelector(".dropzone__title").textContent =
    full ? "Workspace is full" : "Drop files here";
  els.dropzone.querySelector(".dropzone__hint").textContent =
    full ? "Remove a document to free a slot" : "or click to browse · PDF, DOCX, MD";

  els.doclist.replaceChildren();
  for (const doc of state.documents) {
    const item = el("li", "doc");
    item.appendChild(el("span", "doc__name", doc.filename));
    item.appendChild(el(
      "span", "doc__meta",
      `${doc.file_type.toUpperCase()} · ${doc.chunk_count} chunks · ${doc.char_count.toLocaleString()} chars`,
    ));

    const remove = el("button", "doc__delete");
    remove.type = "button";
    remove.title = `Remove ${doc.filename}`;
    remove.setAttribute("aria-label", `Remove ${doc.filename}`);
    remove.appendChild(icon(ICON_TRASH));
    remove.addEventListener("click", () => deleteDocument(doc));
    item.appendChild(remove);

    els.doclist.appendChild(item);
  }

  renderEmptyState();
}

function renderEmptyState() {
  const hasTurns = els.messages.querySelector(".turn") !== null;
  const placeholder = els.messages.querySelector(".empty");

  if (hasTurns) {
    placeholder?.remove();
  } else if (!placeholder) {
    els.messages.appendChild($("tpl-empty").content.cloneNode(true));
  }
}

async function loadWorkspace() {
  try {
    renderWorkspace(await api("/api/documents"));
  } catch (error) {
    showReport([{ filename: "Workspace", status: "rejected", error: error.message }]);
  }
}

async function deleteDocument(doc) {
  if (!confirm(`Remove "${doc.filename}" from the workspace?`)) return;
  try {
    renderWorkspace(await api(`/api/documents/${encodeURIComponent(doc.id)}`, { method: "DELETE" }));
    els.report.replaceChildren();
  } catch (error) {
    showReport([{ filename: doc.filename, status: "rejected", error: error.message }]);
  }
}

/* ---------------------------------------------------------------- upload */

function showReport(results) {
  els.report.replaceChildren();
  for (const result of results) {
    const text = {
      ingested: `${result.filename} — added (${result.chunk_count} chunks)`,
      duplicate: `${result.filename} — already in the workspace`,
      rejected: `${result.filename} — ${result.error}`,
    }[result.status];
    const item = el("div", "report__item", text);
    item.dataset.kind = result.status;
    els.report.appendChild(item);
  }
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList);
  if (files.length === 0 || busy) return;

  const form = new FormData();
  for (const file of files) form.append("files", file);

  setBusy(true);
  els.uploading.hidden = false;
  els.uploadingLabel.textContent =
    files.length === 1 ? `Processing ${files[0].name}…` : `Processing ${files.length} files…`;
  els.report.replaceChildren();

  try {
    const data = await api("/api/documents", { method: "POST", body: form });
    renderWorkspace(data.workspace);
    showReport(data.results);
  } catch (error) {
    showReport([{ filename: "Upload", status: "rejected", error: error.message }]);
  } finally {
    els.uploading.hidden = true;
    els.fileInput.value = "";
    setBusy(false);
  }
}

function wireDropzone() {
  const openPicker = () => {
    if (workspace.remaining > 0 && !busy) els.fileInput.click();
  };

  els.dropzone.addEventListener("click", openPicker);
  els.dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openPicker();
    }
  });

  els.fileInput.addEventListener("change", () => uploadFiles(els.fileInput.files));

  for (const type of ["dragenter", "dragover"]) {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      if (workspace.remaining > 0) els.dropzone.dataset.drag = "true";
    });
  }
  for (const type of ["dragleave", "drop"]) {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      delete els.dropzone.dataset.drag;
    });
  }
  els.dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer?.files?.length) uploadFiles(event.dataTransfer.files);
  });

  // Dropping anywhere else must not make the browser navigate to the file.
  for (const type of ["dragover", "drop"]) {
    window.addEventListener(type, (event) => event.preventDefault());
  }
}

/* ------------------------------------------------------------------- ask */

function setBusy(value) {
  busy = value;
  els.send.disabled = value;
  els.question.disabled = value;
}

function addTurn(role) {
  renderEmptyState();
  const turn = el("div", `turn turn--${role}`);
  els.messages.appendChild(turn);
  els.messages.scrollTop = els.messages.scrollHeight;
  return turn;
}

/** Render answer text, turning every `[n]` marker into a chip that opens
 *  the matching source. Built as text nodes — see the file header. */
function renderAnswerText(bubble, text, citations) {
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const pattern = /\[(\d{1,3})\]/g;
  let cursor = 0;

  for (const match of text.matchAll(pattern)) {
    const marker = Number(match[1]);
    if (!byMarker.has(marker)) continue;

    bubble.appendChild(document.createTextNode(text.slice(cursor, match.index)));

    const chip = el("button", "cite", String(marker));
    chip.type = "button";
    chip.title = byMarker.get(marker).label;
    chip.addEventListener("click", () => {
      const target = bubble.closest(".turn").querySelector(`[data-marker="${marker}"]`);
      target?.querySelector(".source__toggle").click();
      target?.scrollIntoView({ block: "nearest" });
    });
    bubble.appendChild(chip);

    cursor = match.index + match[0].length;
  }
  bubble.appendChild(document.createTextNode(text.slice(cursor)));
}

function renderSources(citations) {
  const wrap = el("div", "sources");
  wrap.appendChild(el("div", "sources__label", `${citations.length} source${citations.length === 1 ? "" : "s"}`));

  // Two chunks off the same page share a label. Number them so the list never
  // shows the same provenance twice; a lone source keeps the plain label.
  const tally = new Map();
  for (const c of citations) tally.set(c.label, (tally.get(c.label) ?? 0) + 1);
  const nth = new Map();

  for (const citation of citations) {
    let label = citation.label;
    if (tally.get(label) > 1) {
      const n = (nth.get(label) ?? 0) + 1;
      nth.set(label, n);
      label += ` · excerpt ${n} of ${tally.get(label)}`;
    }
    const source = el("div", "source");
    source.dataset.marker = String(citation.marker);

    const toggle = el("button", "source__toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.appendChild(el("span", "source__marker", String(citation.marker)));
    toggle.appendChild(el("span", "source__label", label));
    toggle.appendChild(icon(ICON_CHEVRON, "source__chevron"));

    const body = el("div", "source__body");
    body.hidden = true;
    body.appendChild(el("p", "source__text", citation.text));

    const facts = el("div", "source__facts");
    facts.appendChild(el("span", null, citation.chunk_id));
    // Which retrieval leg surfaced this chunk — unset on the summarize route,
    // which selects sources structurally rather than by relevance.
    if (citation.dense_rank !== null) facts.appendChild(el("span", null, `dense #${citation.dense_rank}`));
    if (citation.lexical_rank !== null) facts.appendChild(el("span", null, `lexical #${citation.lexical_rank}`));
    if (citation.dense_rank !== null || citation.lexical_rank !== null) {
      facts.appendChild(el("span", null, `rerank ${citation.rerank_score.toFixed(2)}`));
    }
    body.appendChild(facts);

    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      body.hidden = open;
    });

    source.append(toggle, body);
    wrap.appendChild(source);
  }
  return wrap;
}

function renderObservability(obs) {
  const wrap = el("div", "obs");

  const toggle = el("button", "obs__toggle", "Observability details");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");

  const body = el("div", "obs__body");
  body.hidden = true;

  const cost = obs.usage.cost_usd === null ? "n/a" : `$${obs.usage.cost_usd.toFixed(6)}`;
  const grid = el("div", "obs__grid");
  const facts = [
    ["Route", obs.route],
    ["Total", `${Math.round(obs.total_latency_ms)} ms`],
    ["LLM", `${Math.round(obs.llm_latency_ms)} ms`],
    ["Retrieval etc.", `${Math.round(obs.overhead_ms)} ms`],
    ["Model", obs.model ?? "—"],
    ["Tokens", `${obs.usage.total_tokens} (${obs.usage.prompt_tokens} in / ${obs.usage.completion_tokens} out)`],
    ["Cost", cost],
    ["LLM calls", String(obs.attempts)],
    ["Trace", obs.trace_id ?? "—"],
  ];
  for (const [key, value] of facts) {
    const item = el("div", "obs__item");
    item.appendChild(el("div", "obs__key", key));
    item.appendChild(el("div", "obs__value", value));
    grid.appendChild(item);
  }
  body.appendChild(grid);

  const path = el("div", "obs__path");
  path.appendChild(el("div", "obs__key", "Graph path"));

  const slowest = Math.max(...obs.steps.map((s) => s.duration_ms), 1);
  const steps = el("div", "obs__nodes");
  const seen = new Set();

  for (const step of obs.steps) {
    const row = el("div", "node");
    // A repeated node means governance sent the answer back for correction.
    if (seen.has(step.node)) row.dataset.repeat = "true";
    seen.add(step.node);

    row.appendChild(el("span", "node__name", step.node));

    const bar = el("span", "node__bar");
    const fill = el("span", "node__fill");
    fill.style.width = `${Math.max(2, (step.duration_ms / slowest) * 100)}%`;
    bar.appendChild(fill);
    row.appendChild(bar);

    row.appendChild(el("span", "node__ms", `${Math.round(step.duration_ms)} ms`));

    const verdict = step.detail?.verdict;
    if (verdict) row.appendChild(el("span", "node__verdict", verdict.replace(/_/g, " ")));

    steps.appendChild(row);
  }

  path.appendChild(steps);
  body.appendChild(path);

  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!open));
    body.hidden = open;
  });

  wrap.append(toggle, body);
  return wrap;
}

function renderAnswer(turn, answer) {
  turn.replaceChildren();

  const bubble = el("div", "bubble");
  bubble.dataset.refused = String(answer.refused);
  renderAnswerText(bubble, answer.text, answer.citations);
  turn.appendChild(bubble);

  if (answer.citations.length > 0) turn.appendChild(renderSources(answer.citations));
  turn.appendChild(renderObservability(answer.observability));

  els.messages.scrollTop = els.messages.scrollHeight;
}

async function ask(question) {
  const userTurn = addTurn("user");
  userTurn.appendChild(el("div", "bubble", question));

  const answerTurn = addTurn("assistant");
  const thinking = el("div", "thinking");
  thinking.append(el("span"), el("span"), el("span"));
  answerTurn.appendChild(thinking);

  setBusy(true);
  try {
    const answer = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    renderAnswer(answerTurn, answer);
  } catch (error) {
    answerTurn.replaceChildren();
    const bubble = el("div", "bubble", error.message);
    bubble.dataset.error = "true";
    answerTurn.appendChild(bubble);
  } finally {
    setBusy(false);
    els.question.focus();
  }
}

/* ----------------------------------------------------------------- setup */

function wireComposer() {
  const autosize = () => {
    els.question.style.height = "auto";
    els.question.style.height = `${els.question.scrollHeight}px`;
  };

  els.question.addEventListener("input", autosize);

  els.question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      els.composer.requestSubmit();
    }
  });

  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = els.question.value.trim();
    if (!question || busy) return;
    els.question.value = "";
    autosize();
    ask(question);
  });
}

wireDropzone();
wireComposer();
loadWorkspace();
