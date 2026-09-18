const state = {
  graph: null,
  lastQuery: "",
  selectedIds: [],
  selectedSet: new Set(),
  graphStale: false,
  papers: null,
  authors: null,
  ingestTimer: null,
  libraryLoaded: false,
  libraryOffset: 0,
  libraryTotal: 0,
};

const LS = {
  prune: "paperdeck.prune",
  tab: "paperdeck.tab",
  graphTab: "paperdeck.graphTab",
  theme: "paperdeck.theme",
  library: "paperdeck.library",
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

const THEME = {
  border: "#000000",
  text: "#000000",
  bg: "#ffffff",
  citation: "#94a3b8",
  coauthor: "#047857",
  concept: "#2563eb",
};

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

function refreshThemeColors() {
  THEME.border = cssVar("--node-border") || "#000000";
  THEME.text = cssVar("--text") || "#000000";
  THEME.bg = cssVar("--graph-bg") || "#ffffff";
  THEME.citation = cssVar("--border-strong") || "#94a3b8";
  THEME.coauthor = cssVar("--accent-2") || "#047857";
  THEME.concept = cssVar("--accent") || "#2563eb";
}

function setSelected(ids) {
  state.selectedIds = ids || [];
  state.selectedSet = new Set(state.selectedIds);
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
  refreshThemeColors();
  renderLegend();
  redraw();
}

function redraw() {
  if (!state.graph) return;
  const bg = THEME.bg || "#fff";
  if (state.papers && state.papersData) {
    state.papers.backgroundColor(bg).linkColor(linkColorFn).graphData(state.papersData);
  }
  if (state.authors && state.authorsData) {
    state.authors.backgroundColor(bg).linkColor(linkColorFn).graphData(state.authorsData);
  }
}

function linkColorFn(link) {
  if (link.type === "concept") return THEME.concept;
  if (link.type === "coauthorship") return THEME.coauthor;
  return THEME.citation;
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
    loadGraph(state.graphStale || !state.graph);
    state.graphStale = false;
  }
  if (name === "library" && !state.libraryLoaded) {
    loadLibrary(0);
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
    setSelected(payload.selected_ids || []);
    localStorage.setItem(`${LS.prune}.selected`, JSON.stringify(state.selectedIds));
    state.graphStale = true;
    if ($("#tab-graphs").classList.contains("active")) loadGraph(true);
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
  ctx.fillStyle = state.selectedSet.has(node.id)
    ? HIGHLIGHT[currentTheme()]
    : colorForField(node.field_id);
  ctx.fillRect(x, y, size, size);
  ctx.lineWidth = Math.max(0.4, 1 / globalScale);
  ctx.strokeStyle = THEME.border;
  ctx.strokeRect(x, y, size, size);
  if (globalScale > 1.5 && node.label) {
    const fs = Math.max(1.6, 9 / globalScale);
    ctx.font = `${fs}px ui-monospace, monospace`;
    ctx.fillStyle = THEME.text;
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

function makeGraph(el, widthScale, arrows) {
  const graph = ForceGraph()(el)
    .nodeId("id")
    .nodeLabel("label")
    .nodeVal(nodeValue)
    .backgroundColor(THEME.bg)
    .nodeCanvasObject(paintNode)
    .nodePointerAreaPaint(paintPointer)
    .linkColor(linkColorFn)
    .linkWidth((l) => Math.min(0.6 + (l.weight || 1) * widthScale, 3))
    .linkDirectionalArrowLength((l) => (arrows && l.type === "citation" ? 2.5 : 0))
    .cooldownTicks(60)
    .warmupTicks(0)
    .d3AlphaDecay(0.035)
    .d3VelocityDecay(0.38)
    .onNodeClick(showNode);
  const charge = graph.d3Force("charge");
  if (charge && charge.strength) charge.strength(-22);
  const link = graph.d3Force("link");
  if (link && link.distance) {
    link.distance((l) => (l.type === "concept" ? 26 : l.type === "citation" ? 40 : 16));
  }
  return graph;
}

function ensureGraphs() {
  if (!state.papers) state.papers = makeGraph($("#graph-papers"), 0.25, true);
  if (!state.authors) state.authors = makeGraph($("#graph-authors"), 0.3, false);
}

function tabData(nodes, links, allowed, hideIsolated) {
  const ids = new Set(nodes.map((n) => n.id));
  const kept = links
    .filter((l) => allowed.has(l.type) && ids.has(sid(l.source)) && ids.has(sid(l.target)))
    .map((l) => ({ source: sid(l.source), target: sid(l.target), ...l }));
  let keptNodes = nodes;
  if (hideIsolated) {
    const touched = new Set();
    kept.forEach((l) => {
      touched.add(l.source);
      touched.add(l.target);
    });
    keptNodes = nodes.filter((n) => touched.has(n.id));
  }
  return { nodes: keptNodes, links: kept };
}

function renderGraphs() {
  ensureGraphs();
  if (!state.graph) return;
  const nodes = state.graph.nodes;
  const links = state.graph.links;

  const enabled = new Set(
    $$('#prune-form input[name="edge"]:checked').map((i) => i.value)
  );
  const hideIsolated = $("#hide-isolated")?.checked ?? false;

  const paperAllowed = new Set();
  if (enabled.has("citation")) paperAllowed.add("citation");
  if (enabled.has("concept")) paperAllowed.add("concept");
  const authorAllowed = new Set();
  if (enabled.has("coauthorship")) authorAllowed.add("coauthorship");
  if (enabled.has("concept")) authorAllowed.add("concept");

  const paperNodes = nodes.filter((n) => n.type === "paper");
  const authorNodes = nodes.filter((n) => n.type === "author");
  const papers = tabData(paperNodes, links, paperAllowed, hideIsolated);
  const authors = tabData(authorNodes, links, authorAllowed, hideIsolated);

  state.papersData = papers;
  state.authorsData = authors;
  redraw();

  $("#graph-count").textContent =
    `${papers.nodes.length} papers / ${papers.links.length} links · ` +
    `${authors.nodes.length} researchers / ${authors.links.length} links`;
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
  const edges = form.getAll("edge");
  return {
    edge_types: edges,
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
    concept_edges: edges.includes("concept"),
    min_shared_topics: Number(form.get("min_shared_topics") || 2),
    min_link_weight: Number(form.get("min_link_weight") || 1),
    max_papers: Number(form.get("max_papers") || 2000),
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
  form.min_shared_topics.value = prune.min_shared_topics ?? 2;
  form.min_link_weight.value = prune.min_link_weight ?? 1;
  form.year_from.value = prune.year_from ?? "";
  form.year_to.value = prune.year_to ?? "";
  form.min_citations.value = prune.min_citations ?? 0;
  form.min_h_index.value = prune.min_h_index ?? 0;
  form.max_papers.value = prune.max_papers ?? 2000;
  form.external_refs.checked = !!prune.include_external_references;
  const edges = prune.edge_types || ["citation", "coauthorship", "concept"];
  form.querySelectorAll('input[name="edge"]').forEach(
    (el) => (el.checked = edges.includes(el.value))
  );
}

async function loadGraph(force = false) {
  if (!$("#graph-papers")) return;
  if (state.graph && !force) {
    renderGraphs();
    return;
  }
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
  setSelected([]);
  localStorage.removeItem(`${LS.prune}.selected`);
  loadGraph(true);
});

$$('#prune-form input[name="edge"], #hide-isolated').forEach((el) =>
  el.addEventListener("change", () => {
    if (state.graph) renderGraphs();
  })
);

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

function readLibraryForm() {
  const form = new FormData($("#library-form"));
  const subfields = String(form.get("subfields") || "")
    .split(/[\s,]+/)
    .filter(Boolean);
  return {
    categories: form.getAll("lc"),
    subfield_ids: subfields,
    field_ids: [],
    theory_label: form.get("theory_label") || "any",
    text: String(form.get("text") || "").trim() || null,
    year_from: num(form.get("year_from")),
    year_to: num(form.get("year_to")),
    min_citations: Number(form.get("min_citations") || 0),
    min_h_index: Number(form.get("min_h_index") || 0),
    sort: form.get("sort") || "citations",
    limit: Number(form.get("limit") || 50),
  };
}

function writeLibraryForm(cfg) {
  const form = $("#library-form");
  if (!cfg) return;
  form.querySelectorAll('input[name="lc"]').forEach(
    (el) => (el.checked = (cfg.categories || []).includes(el.value))
  );
  form.subfields.value = (cfg.subfield_ids || []).join(", ");
  form.text.value = cfg.text || "";
  form.theory_label.value = cfg.theory_label || "any";
  form.sort.value = cfg.sort || "citations";
  form.year_from.value = cfg.year_from ?? "";
  form.year_to.value = cfg.year_to ?? "";
  form.min_citations.value = cfg.min_citations ?? 0;
  form.min_h_index.value = cfg.min_h_index ?? 0;
  form.limit.value = cfg.limit ?? 50;
}

async function loadLibrary(offset = 0) {
  const cfg = readLibraryForm();
  localStorage.setItem(LS.library, JSON.stringify(cfg));
  state.libraryOffset = Math.max(0, offset);
  $("#library-meta").textContent = "loading…";
  try {
    const payload = await api("/library", {
      method: "POST",
      body: JSON.stringify({ ...cfg, offset: state.libraryOffset }),
    });
    state.libraryTotal = payload.total;
    state.libraryLoaded = true;
    renderLibrary(payload);
  } catch (err) {
    $("#library-meta").textContent = `Error: ${err.message}`;
  }
}

function renderLibrary(payload) {
  const { papers, total, offset, limit } = payload;
  const from = total ? offset + 1 : 0;
  const to = Math.min(offset + limit, total);
  const page = Math.floor(offset / limit) + 1;
  $("#library-meta").textContent =
    `${total.toLocaleString()} cached works · showing ${from}–${to} · ` +
    `page ${page}/${Math.max(1, Math.ceil(total / limit))}`;
  $("#library-prev").disabled = offset <= 0;
  $("#library-next").disabled = offset + limit >= total;
  const root = $("#library-results");
  if (!papers.length) {
    root.innerHTML =
      '<div class="panel">No cached papers match. Run a search or ingest to grow the cache.</div>';
    return;
  }
  root.innerHTML = papers
    .map((p, i) => {
      const authors = p.authors.map((a) => esc(a.name || "?")).join(", ");
      return `<article class="card">
        <h3>${offset + i + 1}. ${esc(p.title)}</h3>
        <div class="authors">${authors} · ${esc(p.year ?? "n/a")} · ${esc(p.venue || "n/a")}</div>
        <div class="tags">
          <span>cites ${p.cited_by_count ?? 0}</span>
          <span>${esc(p.theory_label)}</span>
          <span>field ${esc(p.field_id || "?")}</span>
          <a href="${esc(p.doi_url || p.openalex_url)}" target="_blank" rel="noreferrer">open</a>
        </div>
      </article>`;
    })
    .join("");
}

async function exportLibrary(fmt) {
  const cfg = readLibraryForm();
  try {
    const res = await fetch(`/library/export?format=${fmt}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
    if (!res.ok) throw new Error(res.statusText);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fmt === "bibtex" ? "paperdeck.bib" : "paperdeck.csv";
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    $("#library-meta").textContent = `Export error: ${err.message}`;
  }
}

$("#library-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadLibrary(0);
});
$("#library-prev").addEventListener("click", () => {
  const limit = Number($("#library-form").limit.value || 50);
  loadLibrary(Math.max(0, state.libraryOffset - limit));
});
$("#library-next").addEventListener("click", () => {
  const limit = Number($("#library-form").limit.value || 50);
  loadLibrary(state.libraryOffset + limit);
});
$("#library-csv").addEventListener("click", () => exportLibrary("csv"));
$("#library-bib").addEventListener("click", () => exportLibrary("bibtex"));

function enhanceNumberInputs(root) {
  root.querySelectorAll('input[type="number"]').forEach((input) => {
    if (input.closest(".stepper")) return;
    const wrap = document.createElement("div");
    wrap.className = "stepper";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    [["▲", 1], ["▼", -1]].forEach(([glyph, dir]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = glyph;
      btn.addEventListener("click", () => {
        const stepSize = Number(input.step) || 1;
        const min = input.min !== "" ? Number(input.min) : null;
        let value = (Number(input.value) || 0) + dir * stepSize;
        if (min != null && value < min) value = min;
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      wrap.appendChild(btn);
    });
  });
}

async function boot() {
  applyTheme(localStorage.getItem(LS.theme) || "light");
  const savedSel = localStorage.getItem(`${LS.prune}.selected`);
  setSelected(savedSel ? JSON.parse(savedSel) : []);
  enhanceNumberInputs(document);
  const prune = localStorage.getItem(LS.prune);
  if (prune) writePruneForm(JSON.parse(prune));
  const library = localStorage.getItem(LS.library);
  if (library) writeLibraryForm(JSON.parse(library));
  activateGraphTab(localStorage.getItem(LS.graphTab) || "papers");
  activateTab(localStorage.getItem(LS.tab) || "search");
  await refreshConfig();
}

boot();
