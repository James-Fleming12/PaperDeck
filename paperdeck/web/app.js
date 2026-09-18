const state = {
  graph: null,
  lastQuery: "",
  selectedIds: [],
  papers: null,
  authors: null,
  ingestTimer: null,
};

const LS = {
  prune: "paperdeck.prune",
  tab: "paperdeck.tab",
  graphTab: "paperdeck.graphTab",
  theme: "paperdeck.theme",
};

const FIELD_NAMES = {
  "17": "Computer Science",
  "26": "Mathematics",
  "22": "Engineering",
  "31": "Physics & Astronomy",
  "18": "Decision Sciences",
  "28": "Neuroscience",
  "27": "Medicine",
  "13": "Biochemistry & Genetics",
  "16": "Chemistry",
  "20": "Economics",
};

const FIELD_COLORS = {
  light: {
    "17": "#2563eb",
    "26": "#059669",
    "22": "#d97706",
    "31": "#7c3aed",
    "18": "#db2777",
    "28": "#0891b2",
    "27": "#dc2626",
    "13": "#65a30d",
    "16": "#0d9488",
    "20": "#ca8a04",
  },
  dark: {
    "17": "#9ccfd8",
    "26": "#c4a7e7",
    "22": "#f6c177",
    "31": "#ebbcba",
    "18": "#eb6f92",
    "28": "#31748f",
    "27": "#eb6f92",
    "13": "#9ccfd8",
    "16": "#c4a7e7",
    "20": "#f6c177",
  },
};
const DEFAULT_COLOR = { light: "#64748b", dark: "#908caa" };
const HIGHLIGHT = { light: "#ea580c", dark: "#ff9e64" };

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

function currentTheme() {
  return document.body.dataset.theme || "light";
}

function colorForField(fieldId) {
  const theme = currentTheme();
  return (FIELD_COLORS[theme] && FIELD_COLORS[theme][fieldId]) || DEFAULT_COLOR[theme];
}

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  localStorage.setItem(LS.theme, theme);
  const btn = $("#theme-toggle");
  if (btn) btn.textContent = theme === "dark" ? "light" : "dark";
  renderLegend();
  redraw();
}

function redraw() {
  if (!state.graph) return;
  const bg = cssVar("--graph-bg") || "#fff";
  if (state.papers && state.papersData) {
    state.papers.backgroundColor(bg).graphData(state.papersData);
  }
  if (state.authors && state.authorsData) {
    state.authors.backgroundColor(bg).graphData(state.authorsData);
  }
}

function renderLegend() {
  const el = $("#legend");
  if (!el) return;
  if (!state.graph) {
    el.innerHTML = "";
    return;
  }
  const present = [...new Set(state.graph.nodes.map((n) => n.field_id).filter(Boolean))].sort();
  const items = present.map(
    (fid) =>
      `<span class="item"><span class="swatch" style="background:${colorForField(fid)}"></span>${esc(
        FIELD_NAMES[fid] || `Field ${fid}`
      )}</span>`
  );
  el.innerHTML =
    `<span class="item"><span class="swatch" style="background:${DEFAULT_COLOR[currentTheme()]}"></span>Other</span>` +
    items.join("");
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function sid(value) {
  return value && typeof value === "object" ? value.id : value;
}

function num(value) {
  return value === "" || value == null ? null : Number(value);
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = res.statusText;
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function activateTab(name) {
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  localStorage.setItem(LS.tab, name);
  if (name === "graphs") {
    resizeGraphs();
    loadGraph();
  }
}

$$("nav button").forEach((btn) => btn.addEventListener("click", () => activateTab(btn.dataset.tab)));

function activateGraphTab(name) {
  $$(".subtabs button").forEach((b) => b.classList.toggle("active", b.dataset.graph === name));
  $$(".graph").forEach((g) => g.classList.toggle("active", g.id === `graph-${name}`));
  localStorage.setItem(LS.graphTab, name);
  resizeGraphs();
}

$$(".subtabs button").forEach((btn) =>
  btn.addEventListener("click", () => activateGraphTab(btn.dataset.graph))
);

$("#theme-toggle").addEventListener("click", () =>
  applyTheme(currentTheme() === "dark" ? "light" : "dark")
);

async function refreshConfig() {
  try {
    const cfg = await api("/config");
    const badge = $("#key-status");
    badge.textContent = cfg.has_api_key ? "key configured" : "no API key";
    badge.className = `badge ${cfg.has_api_key ? "ok" : "missing"}`;
    $("#db-stats").textContent =
      `${cfg.db.works.toLocaleString()} works · ${cfg.db.authors.toLocaleString()} authors · ` +
      `${cfg.db.edges.toLocaleString()} edges · ${cfg.db.embeddings.toLocaleString()} vectors · ` +
      `${cfg.db.institutions.toLocaleString()} institutions`;
  } catch {
    $("#key-status").textContent = "backend error";
    $("#key-status").className = "badge missing";
  }
}

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const meta = $("#setup-meta");
  meta.textContent = "validating…";
  try {
    const res = await api("/setup", {
      method: "POST",
      body: JSON.stringify({ api_key: form.get("api_key"), verify: true }),
    });
    const b = res.budget || {};
    meta.textContent =
      `Saved to ${res.saved}.` + (b.limit_usd != null ? ` Daily budget $${b.limit_usd}.` : "");
    await refreshConfig();
  } catch (err) {
    meta.textContent = `Error: ${err.message}`;
  }
});

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const meta = $("#search-meta");
  meta.textContent = "searching…";
  $("#results").innerHTML = "";
  state.lastQuery = String(form.get("query") || "");

  const body = {
    category: form.get("category"),
    query: state.lastQuery,
    paper_type: form.get("paper_type"),
    search_mode: form.get("search_mode"),
    recency: form.get("recency"),
    num_papers: Number(form.get("num_papers")),
    min_citations: Number(form.get("min_citations")),
    high_profile_researchers: form.get("high_profile_researchers") === "on",
    high_profile_schools: form.get("high_profile_schools") === "on",
    rerank: form.get("rerank") === "on",
    include_graph: false,
  };

  try {
    const payload = await api("/search", { method: "POST", body: JSON.stringify(body) });
    const q = payload.query;
    meta.textContent =
      `${q.category_label} · mode=${q.resolved_search_mode} · ` +
      `${payload.cache.candidates} candidates · kept ${payload.kept_after_filters} · ` +
      `cost $${payload.api.cost_usd} · ${payload.cache.hit ? "cache hit" : "fresh"}` +
      (payload.rerank ? ` · rerank ${payload.rerank.provider}` : "");
    renderResults(payload.papers);
    state.selectedIds = payload.selected_ids || [];
    localStorage.setItem(`${LS.prune}.selected`, JSON.stringify(state.selectedIds));
    if (state.graph) renderGraphs();
    else loadGraph();
    await refreshConfig();
  } catch (err) {
    meta.textContent = `Error: ${err.message}`;
  }
});

function renderResults(papers) {
  const root = $("#results");
  if (!papers.length) {
    root.innerHTML = '<div class="panel">No papers matched. Try relaxing filters.</div>';
    return;
  }
  root.innerHTML = papers
    .map((p, i) => {
      const authors = p.authors.map((a) => esc(a.name || "?")).join(", ");
      return `<article class="card">
        <h3>${i + 1}. ${esc(p.title)}</h3>
        <div class="authors">${authors} · ${esc(p.year ?? "n/a")} · ${esc(p.venue || "n/a")}</div>
        <div class="tags">
          <span>cites ${p.cited_by_count ?? 0}</span>
          <span>${esc(p.theory_label)}</span>
          <span>score ${p.score}</span>
          <a href="${esc(p.doi_url || p.openalex_url)}" target="_blank" rel="noreferrer">open</a>
        </div>
      </article>`;
    })
    .join("");
}

function nodeValue(node) {
  if (node.type === "paper") {
    return 4 + Math.log10((node.cited_by_count || 0) + 1) * 6;
  }
  return 4 + Math.log10((node.h_index || 0) + 1) * 7;
}

function nodeSize(node) {
  if (node.type === "paper") {
    return 3 + Math.log10((node.cited_by_count || 0) + 1) * 2.2;
  }
  return 3 + Math.log10((node.h_index || 0) + 1) * 3;
}

function paintNode(node, ctx, globalScale) {
  const size = nodeSize(node);
  const x = node.x - size / 2;
  const y = node.y - size / 2;
  const selected = new Set(state.selectedIds);
  ctx.fillStyle = selected.has(node.id) ? HIGHLIGHT[currentTheme()] : colorForField(node.field_id);
  ctx.fillRect(x, y, size, size);
  ctx.lineWidth = Math.max(0.4, 1 / globalScale);
  ctx.strokeStyle = cssVar("--node-border") || "#000";
  ctx.strokeRect(x, y, size, size);
  if (globalScale > 1.5 && node.label) {
    const fs = Math.max(1.6, 9 / globalScale);
    ctx.font = `${fs}px ui-monospace, monospace`;
    ctx.fillStyle = cssVar("--text") || "#000";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const label = node.label.length > 46 ? `${node.label.slice(0, 46)}…` : node.label;
    ctx.fillText(label, node.x, node.y + size / 2 + 1);
  }
}

function paintPointer(node, color, ctx) {
  const size = nodeSize(node);
  ctx.fillStyle = color;
  ctx.fillRect(node.x - size / 2, node.y - size / 2, size, size);
}

function makeGraph(el, linkColor, widthScale, arrows) {
  return ForceGraph()(el)
    .nodeId("id")
    .nodeLabel("label")
    .nodeVal(nodeValue)
    .backgroundColor(cssVar("--graph-bg") || "#fff")
    .nodeCanvasObject(paintNode)
    .nodePointerAreaPaint(paintPointer)
    .linkColor(linkColor)
    .linkWidth((l) => Math.min(1 + (l.weight || 1) * widthScale, 5))
    .linkDirectionalArrowLength((l) => (arrows && l.type === "citation" ? 3 : 0))
    .onNodeClick(showNode);
}

function ensureGraphs() {
  if (!state.papers) {
    state.papers = makeGraph($("#graph-papers"), () => cssVar("--border-strong"), 0.2, true);
  }
  if (!state.authors) {
    state.authors = makeGraph($("#graph-authors"), () => cssVar("--border-strong"), 0.25, false);
  }
}

function renderGraphs() {
  ensureGraphs();
  if (!state.graph) return;
  const nodes = state.graph.nodes;
  const links = state.graph.links;

  const paperNodes = nodes.filter((n) => n.type === "paper");
  const paperIds = new Set(paperNodes.map((n) => n.id));
  const paperLinks = links
    .filter((l) => l.type === "citation" && paperIds.has(sid(l.source)) && paperIds.has(sid(l.target)))
    .map((l) => ({ source: sid(l.source), target: sid(l.target), ...l }));

  const authorNodes = nodes.filter((n) => n.type === "author");
  const authorIds = new Set(authorNodes.map((n) => n.id));
  const authorLinks = links
    .filter((l) => l.type === "coauthorship" && authorIds.has(sid(l.source)) && authorIds.has(sid(l.target)))
    .map((l) => ({ source: sid(l.source), target: sid(l.target), ...l }));

  state.papersData = { nodes: paperNodes, links: paperLinks };
  state.authorsData = { nodes: authorNodes, links: authorLinks };
  redraw();

  $("#graph-count").textContent =
    `${paperNodes.length} papers / ${paperLinks.length} citations · ` +
    `${authorNodes.length} researchers / ${authorLinks.length} co-authorships`;
  $("#graph-meta").textContent =
    `Persistent graph from cache · year range ${state.graph.year_range?.[0] ?? "?"}–` +
    `${state.graph.year_range?.[1] ?? "?"} · selected ${state.selectedIds.length}`;
  renderLegend();
  resizeGraphs();
}

function resizeGraphs() {
  const el = $("#graph-papers");
  if (!el) return;
  [state.papers, state.authors].forEach(
    (g) => g && g.width(el.clientWidth).height(el.clientHeight)
  );
}

window.addEventListener("resize", resizeGraphs);

function readPruneForm() {
  const form = new FormData($("#prune-form"));
  const kinds = form.getAll("kind");
  const subfields = String(form.get("subfields") || "")
    .split(/[\s,]+/)
    .filter(Boolean);
  return {
    kinds: kinds.length ? kinds : ["papers", "authors"],
    categories: form.getAll("category"),
    subfield_ids: subfields,
    field_ids: [],
    year_from: num(form.get("year_from")),
    year_to: num(form.get("year_to")),
    min_citations: Number(form.get("min_citations") || 0),
    theory_label: form.get("theory_label") || "any",
    text: String(form.get("text") || "").trim() || null,
    min_h_index: Number(form.get("min_h_index") || 0),
    include_external_references: form.get("external_refs") === "on",
    max_papers: Number(form.get("max_papers") || 5000),
    selected_ids: state.selectedIds,
  };
}

function writePruneForm(prune) {
  const form = $("#prune-form");
  if (!prune) return;
  form.querySelectorAll('input[name="kind"]').forEach(
    (el) => (el.checked = prune.kinds ? prune.kinds.includes(el.value) : true)
  );
  form.querySelectorAll('input[name="category"]').forEach(
    (el) => (el.checked = (prune.categories || []).includes(el.value))
  );
  form.subfields.value = (prune.subfield_ids || []).join(", ");
  form.text.value = prune.text || "";
  form.theory_label.value = prune.theory_label || "any";
  form.year_from.value = prune.year_from ?? "";
  form.year_to.value = prune.year_to ?? "";
  form.min_citations.value = prune.min_citations ?? 0;
  form.min_h_index.value = prune.min_h_index ?? 0;
  form.max_papers.value = prune.max_papers ?? 5000;
  form.external_refs.checked = !!prune.include_external_references;
}

async function loadGraph() {
  if (!$("#graph-papers")) return;
  const prune = readPruneForm();
  localStorage.setItem(LS.prune, JSON.stringify(prune));
  $("#graph-meta").textContent = "building persistent graph…";
  try {
    state.graph = await api("/graph/view", { method: "POST", body: JSON.stringify(prune) });
    renderGraphs();
  } catch (err) {
    $("#graph-meta").textContent = `Graph error: ${err.message}`;
  }
}

$("#prune-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadGraph();
});

$("#prune-reset").addEventListener("click", () => {
  $("#prune-form").reset();
  state.selectedIds = [];
  localStorage.removeItem(`${LS.prune}.selected`);
  loadGraph();
});

async function showNode(node) {
  const popup = $("#popup");
  popup.classList.remove("hidden");
  if (node.type === "paper") {
    popup.innerHTML = `<button class="close">×</button>
      <h3>${esc(node.label || node.id)}</h3>
      <div class="meta">${esc(node.venue || "n/a")} · ${esc(node.year ?? "n/a")} · cites ${node.cited_by_count ?? 0} · ${esc(node.theory_label || "")}</div>
      <p><a href="https://openalex.org/${esc(node.id)}" target="_blank" rel="noreferrer">OpenAlex</a></p>`;
    bindClose(popup);
    return;
  }

  popup.innerHTML = `<button class="close">×</button>
    <h3>${esc(node.label || node.id)}</h3>
    <div class="meta">h-index ${node.h_index ?? "?"} · cites ${node.cited_by_count ?? 0}</div>
    <h4>Loading…</h4>`;
  bindClose(popup);
  try {
    const data = await api(`/author/${encodeURIComponent(node.id)}?q=${encodeURIComponent(state.lastQuery)}`);
    const list = (items) =>
      items
        .map(
          (w) =>
            `<li>${esc(w.year ?? "")} — <a href="${esc(w.doi_url || w.openalex_url)}" target="_blank" rel="noreferrer">${esc(w.title)}</a></li>`
        )
        .join("") || "<li>none cached</li>";
    popup.innerHTML = `<button class="close">×</button>
      <h3>${esc(data.author.display_name || node.id)}</h3>
      <div class="meta">h-index ${data.author.h_index ?? "?"} · cites ${data.author.cited_by_count ?? 0}</div>
      <h4>Recent papers</h4><ul>${list(data.recent)}</ul>
      <h4>Matching “${esc(state.lastQuery)}”</h4><ul>${list(data.matching)}</ul>`;
    bindClose(popup);
  } catch (err) {
    popup.innerHTML = `<button class="close">×</button><h3>${esc(node.label || node.id)}</h3><div class="meta">${esc(err.message)}</div>`;
    bindClose(popup);
  }
}

function bindClose(popup) {
  const btn = popup.querySelector(".close");
  if (btn) btn.addEventListener("click", () => popup.classList.add("hidden"));
}

$("#ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const categories = form.getAll("category");
  if (!categories.length) {
    $("#ingest-status").textContent = "Pick at least one category.";
    return;
  }
  const body = {
    categories,
    min_citations: Number(form.get("min_citations")),
    max_works: Number(form.get("max_works")),
    year_from: form.get("year_from") ? Number(form.get("year_from")) : null,
    year_to: form.get("year_to") ? Number(form.get("year_to")) : null,
    enrich: form.get("enrich") === "on",
    include_embeddings: form.get("include_embeddings") === "on",
  };
  $("#ingest-status").textContent = "starting…";
  $("#ingest-summary").textContent = "";
  try {
    const { job_id } = await api("/ingest", { method: "POST", body: JSON.stringify(body) });
    pollIngest(job_id);
  } catch (err) {
    $("#ingest-status").textContent = `Error: ${err.message}`;
  }
});

function pollIngest(jobId) {
  clearInterval(state.ingestTimer);
  state.ingestTimer = setInterval(async () => {
    try {
      const job = await api(`/ingest/${jobId}`);
      const p = job.progress || {};
      if (p.stage === "ingesting") {
        const pct = p.category_total ? Math.min(100, (p.fetched / p.category_total) * 100) : 0;
        $("#ingest-bar").style.width = `${pct}%`;
        $("#ingest-status").textContent =
          `${p.category_label}: ${p.fetched.toLocaleString()} / ${p.category_total.toLocaleString()} ` +
          `(overall ${p.overall.toLocaleString()})`;
      } else if (job.status === "done") {
        clearInterval(state.ingestTimer);
        $("#ingest-bar").style.width = "100%";
        $("#ingest-status").textContent = "Done.";
        $("#ingest-summary").textContent = JSON.stringify(job.summary, null, 2);
        await refreshConfig();
      } else if (job.status === "error") {
        clearInterval(state.ingestTimer);
        $("#ingest-status").textContent = `Error: ${job.error}`;
      }
    } catch (err) {
      clearInterval(state.ingestTimer);
      $("#ingest-status").textContent = `Error: ${err.message}`;
    }
  }, 1000);
}

async function boot() {
  applyTheme(localStorage.getItem(LS.theme) || "light");
  const savedSel = localStorage.getItem(`${LS.prune}.selected`);
  if (savedSel) state.selectedIds = JSON.parse(savedSel);
  const prune = localStorage.getItem(LS.prune);
  if (prune) writePruneForm(JSON.parse(prune));
  activateGraphTab(localStorage.getItem(LS.graphTab) || "papers");
  activateTab(localStorage.getItem(LS.tab) || "search");
  await refreshConfig();
  loadGraph();
}

boot();
