from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .classifier import classify
from .config import Settings, load_settings
from .db import Database
from .embeddings import cosine, get_provider
from .fields import Category, get_category
from .graph import build_graph, expand_with_cached_references
from .openalex import OpenAlexClient
from .ranking import RankingOptions, apply_filters, rank

CACHE_TTL_DAYS = 7
MODERN_WINDOW_YEARS = 6
FOUNDATIONAL_MIN_AGE_YEARS = 10
DEFAULT_CANDIDATES = 300
MAX_CANDIDATES = 1000

PaperType = Literal["theoretical", "empirical", "both"]
SearchMode = Literal["auto", "default", "exact", "semantic"]
Recency = Literal["any", "modern", "foundational"]


class SearchRequest(BaseModel):
    category: str
    query: str
    paper_type: PaperType = "both"
    search_mode: SearchMode = "auto"
    num_papers: int = Field(default=10, ge=1, le=100)
    recency: Recency = "any"
    year_from: int | None = None
    year_to: int | None = None
    min_citations: int = 0
    high_profile_researchers: bool = False
    high_profile_schools: bool = False
    h_index_threshold: int = 25
    institution_citation_threshold: int = 150_000
    include_graph: bool = False
    include_external_references: bool = False
    graph_scope: Literal["reading_list", "candidate_pool"] = "reading_list"
    max_candidates: int = Field(default=DEFAULT_CANDIDATES, ge=20, le=MAX_CANDIDATES)
    rerank: bool = False
    embedding_provider: str = "auto"
    refresh: bool = False


def resolve_recency(req: SearchRequest) -> tuple[int | None, int | None]:
    year_from, year_to = req.year_from, req.year_to
    current = date.today().year
    if req.recency == "modern" and year_from is None:
        year_from = current - MODERN_WINDOW_YEARS
    if req.recency == "foundational" and year_to is None:
        year_to = current - FOUNDATIONAL_MIN_AGE_YEARS
    return year_from, year_to


def resolve_search_mode(req: SearchRequest) -> str:
    if req.search_mode != "auto":
        return req.search_mode
    long_query = len(req.query.split()) >= 5
    if long_query and req.min_citations == 0 and not (
        req.high_profile_researchers or req.high_profile_schools
    ):
        return "semantic"
    return "default"


def query_hash(req: SearchRequest, search_mode: str) -> str:
    key = {
        "category": req.category,
        "query": " ".join(req.query.lower().split()),
        "mode": search_mode,
        "year_from": resolve_recency(req)[0],
        "year_to": resolve_recency(req)[1],
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


class PaperDeckService:
    def __init__(self, settings: Settings | None = None, db: Database | None = None):
        self.settings = settings or load_settings()
        self.db = db or Database(self.settings.db_path)
        self.db.init()

    @classmethod
    def for_path(cls, path: Path) -> "PaperDeckService":
        settings = load_settings()
        return cls(settings=settings, db=Database(path))

    def _client(self) -> OpenAlexClient:
        return OpenAlexClient(
            api_key=self.settings.openalex_api_key,
            base_url=self.settings.openalex_base_url,
            timeout=self.settings.request_timeout,
            user_agent=self.settings.user_agent,
        )

    async def search(self, req: SearchRequest) -> dict[str, Any]:
        category = get_category(req.category)
        search_mode = resolve_search_mode(req)
        qhash = query_hash(req, search_mode)

        cached = None if req.refresh else self.db.get_cached_query(qhash)
        cache_fresh = cached is not None and _age_days(cached["created_at"]) < CACHE_TTL_DAYS

        cost = 0.0
        requests = 0
        candidate_count = 0
        used_cache = bool(cache_fresh)

        if cache_fresh:
            work_ids = cached["work_ids"]
        else:
            work_ids, cost, requests, candidate_count = await self._fetch_and_persist(
                req, category, search_mode, qhash
            )

        works_list = self.db.get_works(work_ids)
        if search_mode == "semantic":
            works_list = [
                w
                for w in works_list
                if category.matches(w.get("subfield_id"), w.get("field_id"))
            ]
        works = {w["id"]: w for w in works_list}
        scoped_ids = dedupe_by_title(list(works.keys()), works)
        rerank_info = None
        if req.rerank:
            rerank_info = self._rerank(req, scoped_ids, works)
        paper_authors = self.db.paper_authors_map(work_ids)
        author_stats = self.db.get_authors(
            {link["author_id"] for links in paper_authors.values() for link in links}
        )
        institution_stats = self._institution_stats(paper_authors, works)

        options = RankingOptions(
            paper_type=req.paper_type,
            min_citations=req.min_citations,
            year_from=resolve_recency(req)[0],
            year_to=resolve_recency(req)[1],
            recency=req.recency,
            high_profile_researchers=req.high_profile_researchers,
            high_profile_schools=req.high_profile_schools,
            h_index_threshold=req.h_index_threshold,
            institution_citation_threshold=req.institution_citation_threshold,
        )

        filtered_ids = apply_filters(
            scoped_ids, works, paper_authors, author_stats, institution_stats, options
        )
        ranked = rank(
            filtered_ids, works, paper_authors, author_stats, institution_stats, options
        )
        selected = ranked[: req.num_papers]
        selected_ids = [r["id"] for r in selected]

        papers = [
            self._paper_payload(
                row, works[row["id"]], paper_authors, author_stats
            )
            for row in selected
        ]

        graph = None
        if req.include_graph:
            scope_ids = selected_ids
            if req.graph_scope == "candidate_pool":
                scope_ids = scoped_ids
            scope_ids = expand_with_cached_references(self.db, scope_ids)
            graph = build_graph(
                self.db,
                scope_ids,
                include_external_references=req.include_external_references,
            )
            graph["selected_ids"] = selected_ids

        budget = self.db.stats()
        return {
            "query": {
                **req.model_dump(),
                "resolved_search_mode": search_mode,
                "resolved_year_from": resolve_recency(req)[0],
                "resolved_year_to": resolve_recency(req)[1],
                "category_label": category.label,
            },
            "cache": {
                "hit": used_cache,
                "query_hash": qhash,
                "candidates": len(work_ids),
            },
            "candidate_count": candidate_count or len(work_ids),
            "kept_after_filters": len(filtered_ids),
            "returned": len(papers),
            "papers": papers,
            "graph": graph,
            "rerank": rerank_info,
            "api": {
                "cost_usd": round(cost, 6),
                "requests": requests,
                "budget": budget,
            },
        }

    async def _fetch_and_persist(
        self,
        req: SearchRequest,
        category: Category,
        search_mode: str,
        qhash: str,
    ) -> tuple[list[str], float, int, int]:
        fetch_target = min(max(req.max_candidates, req.num_papers * 5), MAX_CANDIDATES)
        async with self._client() as client:
            result = await client.search_works(
                query=req.query,
                category=category,
                search_mode=search_mode,
                year_from=resolve_recency(req)[0],
                year_to=resolve_recency(req)[1],
                min_citations=req.min_citations,
                max_results=fetch_target,
            )
            works = result.works
            cost = result.cost_usd
            requests = result.requests
            count = result.count
            await self._persist(works, category, client)

        work_ids = [w["id"] for w in works if w.get("id")]
        self.db.store_cached_query(
            qhash,
            {
                "category": req.category,
                "query": req.query,
                "search_mode": search_mode,
                "fetched": len(work_ids),
            },
            work_ids,
        )
        return work_ids, cost, requests, count

    async def _persist(
        self, works: list[dict[str, Any]], category: Category, client: OpenAlexClient
    ) -> None:
        author_ids: set[str] = set()
        institution_ids: set[str] = set()
        citation_edges: list[tuple[str, str, str, int | None, float]] = []
        authorship_edges: list[tuple[str, str, str, int | None, float]] = []
        basic_authors: dict[str, dict[str, Any]] = {}
        basic_institutions: dict[str, dict[str, Any]] = {}

        for work in works:
            title = work.get("title")
            venue = work.get("venue_name")
            result = classify(title, work.get("abstract"), venue, category)
            work["theory_label"] = result.label
            work["theory_score"] = result.theory_score
            work["empirical_score"] = result.empirical_score

            for inst in work.get("institution_details") or []:
                if inst.get("id"):
                    institution_ids.add(inst["id"])
                    basic_institutions.setdefault(inst["id"], inst)

            for link in work.get("authorships") or []:
                aid = link.get("author_id")
                if not aid:
                    continue
                author_ids.add(aid)
                basic_authors.setdefault(
                    aid, {"id": aid, "display_name": link.get("display_name")}
                )
                authorship_edges.append(
                    (work["id"], aid, "authorship", work.get("year"), 1.0)
                )

            for ref in work.get("referenced_works") or []:
                if ref:
                    citation_edges.append(
                        (work["id"], ref, "citation", work.get("year"), 1.0)
                    )

        self.db.upsert_works(works)
        self.db.upsert_authors(list(basic_authors.values()))
        self.db.upsert_institutions(list(basic_institutions.values()))
        for work in works:
            self.db.set_paper_authors(work["id"], work.get("authorships") or [])
        self.db.add_edges(authorship_edges)
        self.db.add_edges(citation_edges)

        need_authors = self.db.authors_needing_enrichment(list(author_ids))
        if need_authors:
            enriched = await client.enrich_authors(need_authors)
            self.db.upsert_authors(enriched)

        need_institutions = self.db.institutions_needing_enrichment(list(institution_ids))
        if need_institutions:
            enriched_inst = await client.enrich_institutions(need_institutions)
            self.db.upsert_institutions(enriched_inst)

    def _rerank(
        self,
        req: SearchRequest,
        scoped_ids: list[str],
        works: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        provider = get_provider(req.embedding_provider)
        texts = {
            wid: _embed_text(works[wid])
            for wid in scoped_ids
        }
        missing = self.db.embeddings_missing(scoped_ids, provider.name)
        embedded = 0
        batch_size = 64
        for i in range(0, len(missing), batch_size):
            batch = missing[i : i + batch_size]
            vectors = provider.encode([texts[wid] for wid in batch])
            self.db.upsert_embeddings(
                provider.name, dict(zip(batch, vectors, strict=True))
            )
            embedded += len(batch)

        vectors = self.db.get_embeddings(scoped_ids, provider.name)
        query_vec = provider.encode([req.query])[0]
        for wid in scoped_ids:
            vec = vectors.get(wid)
            if vec:
                works[wid]["relevance_score"] = cosine(query_vec, vec)
        return {
            "enabled": True,
            "provider": provider.name,
            "dim": provider.dim,
            "embedded_now": embedded,
            "scored": sum(1 for wid in scoped_ids if wid in vectors),
        }

    def _institution_stats(
        self,
        paper_authors: dict[str, list[dict[str, Any]]],
        works: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        ids = {
            iid
            for links in paper_authors.values()
            for link in links
            for iid in (link.get("institution_ids") or [])
        }
        return self.db.get_institutions(ids)

    def _paper_payload(
        self,
        row: dict[str, Any],
        work: dict[str, Any],
        paper_authors: dict[str, list[dict[str, Any]]],
        author_stats: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        links = sorted(
            paper_authors.get(work["id"], []),
            key=lambda link: link.get("position") or 0,
        )
        authors = []
        for link in links:
            stats = author_stats.get(link["author_id"]) or {}
            authors.append(
                {
                    "id": link["author_id"],
                    "name": link.get("author_name") or stats.get("display_name"),
                    "h_index": stats.get("h_index"),
                }
            )
        doi = work.get("doi")
        return {
            "id": work["id"],
            "title": work.get("title"),
            "abstract": work.get("abstract"),
            "year": work.get("year"),
            "venue": work.get("venue_name"),
            "cited_by_count": work.get("cited_by_count"),
            "theory_label": work.get("theory_label"),
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else None,
            "openalex_url": f"https://openalex.org/{work['id']}",
            "authors": authors,
            "score": row.get("score"),
            "score_components": row.get("components"),
        }

    def stats(self) -> dict[str, Any]:
        return {
            "db_path": str(self.settings.db_path),
            "has_api_key": self.settings.has_key,
            **self.db.stats(),
        }


def _embed_text(work: dict[str, Any]) -> str:
    title = (work.get("title") or "").strip()
    abstract = (work.get("abstract") or "").strip()
    return f"{title}. {abstract}".strip() or (work.get("id") or "")


def _normalized_title(title: str | None) -> str:
    import re

    if not title:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def dedupe_by_title(
    work_ids: list[str], works: dict[str, dict[str, Any]]
) -> list[str]:
    best: dict[str, str] = {}
    for wid in work_ids:
        work = works[wid]
        key = _normalized_title(work.get("title")) or wid
        current = best.get(key)
        if current is None:
            best[key] = wid
            continue
        if _version_rank(works[current]) < _version_rank(work):
            best[key] = wid
    chosen = set(best.values())
    return [wid for wid in work_ids if wid in chosen]


def _version_rank(work: dict[str, Any]) -> tuple[int, int]:
    venue = (work.get("venue_name") or "").lower()
    is_preprint = 0 if "arxiv" in venue or "preprint" in venue else 1
    if not venue:
        is_preprint = 0
    return (is_preprint, work.get("cited_by_count") or 0)


def _age_days(created_at: str) -> float:
    from datetime import datetime, timezone

    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return 1e9
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() / 86400
