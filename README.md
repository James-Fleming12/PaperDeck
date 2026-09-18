# PaperDeck

Scoped, cached reading-list generator and temporal research graph over
[OpenAlex](https://openalex.org). Pick one of four categories, describe a topic,
apply filters, and get a ranked reading list plus an optional graph of the
papers and researchers over time.

Categories: `ml_theory`, `tcs`, `math`, `rendering`.

## Quick start (no CLI required)

```bash
./launch.sh          # macOS/Linux
launch.bat           # Windows
```

The launcher installs dependencies and opens the local web UI in your browser.
There you can save your API key, search, run a bounded ingest, and explore the
paper and researcher graphs. The CLI remains available for everything.

## Install

```bash
uv sync
```

## One-time API key setup (not hardcoded, stored outside the repo)

```bash
uv run paperdeck setup
```

This prompts for your free OpenAlex API key (from
<https://openalex.org/settings/api>), validates it against `/rate-limit`, and
writes it to `~/.config/paperdeck/config.toml` with `chmod 600`. Resolution
order at runtime:

```
--api-key / OPENALEX_API_KEY env var  >  ~/.config/paperdeck/config.toml  >  ./.env
```

`.env` is gitignored; commit only `.env.example`. Non-interactive setups can use
`paperdeck setup --key "$OPENALEX_API_KEY"`. Without any key the tool still
works against OpenAlex's keyless budget ($0.10/day), which is enough to test.

## Usage

```bash
# Reading list: modern theoretical Fourier analysis in ML theory
uv run paperdeck search -c ml_theory -q "Fourier analysis" --type theoretical --recency modern -n 10

# Math, foundational, high-profile researchers and schools
uv run paperdeck search -c math -q "Fourier analysis" --recency foundational \
    --high-profile-researchers --high-profile-schools -n 8

# TCS online bandits, foundational
uv run paperdeck search -c tcs -q "online bandits" --recency foundational

# Semantic rerank over a large scoped keyword pool (removes the 50-result semantic cap)
uv run paperdeck search -c ml_theory -q "random fourier features" --mode exact \
    --type theoretical --rerank --max-candidates 400 -n 10

# Include the temporal graph in the output
uv run paperdeck search -c ml_theory -q "random fourier features" --type theoretical --graph --json

# Export a graph to JSON (--kind both|papers|authors)
uv run paperdeck graph -c math -q "Fourier analysis" -o math_fourier.json --scope reading_list --kind authors

# Bounded ingest: grow the local corpus without the 740 GB snapshot
uv run paperdeck ingest -c tcs -c rendering --min-citations 20 --max-works 50000

# Cache/budget stats
uv run paperdeck stats

# Local web UI + API (opens the browser with --open)
uv run paperdeck serve --open
```

## Local web UI

`paperdeck serve --open` (or `./launch.sh`) starts the UI at
<http://127.0.0.1:8000>. It covers the whole workflow with no CLI:

- **Setup** — paste your OpenAlex key; validated and stored outside the repo.
- **Search** — category, query, theory/empirical, recency, citations, prestige
  filters, and local rerank. Results list with authors, year, venue, DOI.
- **Ingest** — bounded bulk fill of the cache per category (min citations,
  max works, optional author metrics and embeddings) with live progress.
- **Graphs** — separate **Papers** (citation) and **Researchers** (co-authorship)
  views. Click any node for details; clicking a researcher shows their recent
  papers and the ones matching the current query.

## Bounded ingest

Rather than downloading the ~740 GB OpenAlex snapshot, `ingest` pages the API
with pure filters (list pricing, ~$0.10/1,000 requests) up to a cap:

```bash
uv run paperdeck ingest -c ml_theory --min-citations 50 --max-works 200000
uv run paperdeck ingest -c tcs -c rendering --min-citations 20 --max-works 50000 --embeddings
```

Rough sizing: 200k works ≈ 0.3 GB transferred and ~$0.20; the four categories
total ~15.7M works (~$16, ~22 GB), which you would rarely need all of.

## Local embeddings / semantic rerank

`--rerank` scores the category-scoped candidate pool with a local embedding model,
which lets you use `search`/`search.exact` (full filter set, up to 10,000 results via
cursor) and still get semantic ordering — something OpenAlex's native `search.semantic`
cannot do, since it is capped at 50 results and rejects topic filters. Vectors are
cached in SQLite and reused across queries.

```bash
uv sync --extra rerank      # ONNX backend (fastembed, small; no torch)
# or: uv sync --extra rerank-torch   # sentence-transformers

uv run paperdeck search -c math -q "Fourier analysis" --rerank --mode exact -n 10
```

Provider selection: `--embed-provider auto|fastembed|sentence-transformers|hashing`.
`auto` picks fastembed, then sentence-transformers, then a dependency-free hashing
fallback, so `--rerank` always runs. First fastembed run downloads `BAAI/bge-small-en-v1.5`
(384-dim).

## How it works

- **Query builder** maps a category to OpenAlex filters (`fields.py`) and builds a
  paged query (`openalex.py`): `per_page=100` + cursor paging, `mailto`-free API-key
  auth, `search` / `search.exact` / `search.semantic`, and `select` to trim payloads.
- **Abstracts** are reconstructed from `abstract_inverted_index` into plain text.
- **Classifier** (`classifier.py`) scores title + abstract keywords and venue signal
  to label papers `theoretical` / `empirical` / `mixed`.
- **Ranking** (`ranking.py`) normalizes citations-per-year, author h-index, institution
  citation prestige, relevance, and recency, then applies configurable weights.
- **Cache** (`db.py`, SQLite): candidate pools keyed by normalized query, per-work rows
  with a 7-day pool TTL, per-author/institution metrics with longer life. Repeat and
  overlapping queries mostly avoid the API.
- **Graph** (`graph.py`): a temporal view over the cache. Papers and authors are nodes;
  authorship, co-authorship, and citation are edges carrying a `year`.

## Notes / limitations

- Query caching is keyed on `(category, query, search_mode, year window)`; `num_papers`,
  citation and prestige filters are applied locally so they reuse the same pool.
- Citation edges are scoped to nodes present in the corpus unless
  `include_external_references` is enabled.
- Semantic search (`--mode semantic`) returns at most 50 results per query and cannot
  be combined with a `cited_by_count` filter; PaperDeck applies citation filters locally.
- OpenAlex's free key budget is $1/day of API credits (searches cost $1/1,000 calls,
  list+filter $0.10/1,000). Very heavy refinement sessions may exhaust it.
