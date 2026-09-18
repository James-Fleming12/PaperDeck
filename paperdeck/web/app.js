const state = {
  graph: null,
  lastQuery: "",
  papers: null,
  authors: null,
  ingestTimer: null,
};

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

$$("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("nav button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
});

$$(".subtabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".subtabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".graph").forEach((g) => g.classList.remove("active"));
    $(`#graph-${btn.dataset.graph}`).classList.add("active");
    resizeGraphs();
  });
});

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
    return cfg;
  } catch (err) {
    $("#key-status").textContent = "backend error";
    $("#key-status").className = "badge missing";
    throw err;
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
      `Saved to ${res.saved}.` +
      (b.limit_usd != null ? ` Daily budget $${b.limit_usd}.` : "");
    await refreshConfig();
  } catch (err) {
    meta.textContent = `Error: ${err.message}`;
  }
});

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const meta = $("#search-meta");
  const results = $("#results");
  meta.textContent = "searching…";
  results.innerHTML = "";
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
    include_graph: form.get("include_graph") === "on",
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
    if (payload.graph) {
      state.graph = payload.graph;
      renderGraphs();
      meta.textContent += " · graphs ready";
    }
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
    return 2 + Math.log10((node.cited_by_count || 0) + 1) * 3;
  }
  return 2 + Math.log10((node.h_index || 0) + 1) * 4;
}

function ensureGraphs() {
  if (!state.papers) {
    state.papers = ForceGraph()($("#graph-papers"))
      .nodeId("id")
      .nodeLabel("label")
      .nodeVal(nodeValue)
      .nodeColor((n) => (n.type === "paper" ? "#9ece6a" : "#7aa2f7"))
      .linkColor(() => "rgba(122,162,247,0.35)")
      .linkWidth((l) => Math.min(1 + (l.weight || 1) * 0.2, 4))
      .linkDirectionalArrowLength((l) => (l.type === "citation" ? 3 : 0))
      .onNodeClick(showNode);
  }
  if (!state.authors) {
    state.authors = ForceGraph()($("#graph-authors"))
      .nodeId("id")
      .nodeLabel("label")
      .nodeVal(nodeValue)
      .nodeColor(() => "#7aa2f7")
      .linkColor(() => "rgba(158,206,106,0.3)")
      .linkWidth((l) => Math.min(1 + (l.weight || 1) * 0.25, 5))
      .onNodeClick(showNode);
  }
}

function renderGraphs() {
  ensureGraphs();
  const graph = state.graph;
  const nodes = graph.nodes;
  const links = graph.links;

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

  state.papers.graphData({ nodes: paperNodes, links: paperLinks });
  state.authors.graphData({ nodes: authorNodes, links: authorLinks });

  $("#graph-meta").textContent =
    `papers: ${paperNodes.length} nodes / ${paperLinks.length} citations · ` +
    `researchers: ${authorNodes.length} nodes / ${authorLinks.length} co-authorships`;
  resizeGraphs();
}

function resizeGraphs() {
  [state.papers, state.authors].forEach((g) => g && g.width($("#graph-papers").clientWidth).height($("#graph-papers").clientHeight));
}

window.addEventListener("resize", resizeGraphs);

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
    const recent = data.recent.map(
      (w) => `<li>${esc(w.year ?? "")} — <a href="${esc(w.doi_url || w.openalex_url)}" target="_blank" rel="noreferrer">${esc(w.title)}</a></li>`
    );
    const matching = data.matching.map(
      (w) => `<li>${esc(w.year ?? "")} — <a href="${esc(w.doi_url || w.openalex_url)}" target="_blank" rel="noreferrer">${esc(w.title)}</a></li>`
    );
    popup.innerHTML = `<button class="close">×</button>
      <h3>${esc(data.author.display_name || node.id)}</h3>
      <div class="meta">h-index ${data.author.h_index ?? "?"} · cites ${data.author.cited_by_count ?? 0}</div>
      <h4>Recent papers</h4><ul>${recent.join("") || "<li>none cached</li>"}</ul>
      <h4>Matching “${esc(state.lastQuery)}”</h4><ul>${matching.join("") || "<li>none cached</li>"}</ul>`;
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

refreshConfig();
