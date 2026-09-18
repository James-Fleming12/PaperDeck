from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from .fields import TOP_INSTITUTION_NAMES

DEFAULT_WEIGHTS = {
    "relevance": 0.35,
    "citations_per_year": 0.30,
    "author_prestige": 0.20,
    "institution_prestige": 0.10,
    "recency": 0.05,
}


@dataclass
class RankingOptions:
    paper_type: str = "both"
    min_citations: int = 0
    year_from: int | None = None
    year_to: int | None = None
    recency: str = "any"
    high_profile_researchers: bool = False
    high_profile_schools: bool = False
    h_index_threshold: int = 25
    institution_citation_threshold: int = 150_000
    weights: dict[str, float] | None = None


def _normalize(values: list[float], log: bool = False) -> list[float]:
    if not values:
        return []
    mapped = [math.log1p(max(v, 0.0)) if log else max(v, 0.0) for v in values]
    lo, hi = min(mapped), max(mapped)
    if hi - lo < 1e-12:
        return [1.0 if hi > 0 else 0.0 for _ in mapped]
    return [(v - lo) / (hi - lo) for v in mapped]


def _author_metrics(
    work_id: str,
    paper_authors: dict[str, list[dict[str, Any]]],
    author_stats: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    links = paper_authors.get(work_id, [])
    h_indices = []
    citations = []
    for link in links:
        stats = author_stats.get(link["author_id"]) or {}
        h = stats.get("h_index")
        c = stats.get("cited_by_count")
        if h is not None:
            h_indices.append(float(h))
        if c is not None:
            citations.append(float(c))
    max_h = max(h_indices) if h_indices else 0.0
    total_citations = sum(citations) if citations else 0.0
    return max_h, total_citations


def _institution_metrics(
    work_id: str,
    paper_authors: dict[str, list[dict[str, Any]]],
    institution_stats: dict[str, dict[str, Any]],
) -> tuple[float, bool]:
    links = paper_authors.get(work_id, [])
    max_cited = 0.0
    is_top_name = False
    for link in links:
        for iid in link.get("institution_ids") or []:
            stats = institution_stats.get(iid) or {}
            cited = stats.get("cited_by_count")
            if cited is not None:
                max_cited = max(max_cited, float(cited))
            name = (stats.get("display_name") or "").lower()
            if name and any(top in name for top in TOP_INSTITUTION_NAMES):
                is_top_name = True
    return max_cited, is_top_name


def apply_filters(
    work_ids: list[str],
    works: dict[str, dict[str, Any]],
    paper_authors: dict[str, list[dict[str, Any]]],
    author_stats: dict[str, dict[str, Any]],
    institution_stats: dict[str, dict[str, Any]],
    options: RankingOptions,
) -> list[str]:
    kept: list[str] = []
    for wid in work_ids:
        work = works.get(wid)
        if not work:
            continue
        if work.get("is_retracted"):
            continue
        if (work.get("cited_by_count") or 0) < options.min_citations:
            continue
        year = work.get("year")
        if options.year_from and (year is None or year < options.year_from):
            continue
        if options.year_to and (year is None or year > options.year_to):
            continue

        label = work.get("theory_label")
        if options.paper_type == "theoretical" and label != "theoretical":
            continue
        if options.paper_type == "empirical" and label != "empirical":
            continue

        if options.high_profile_researchers:
            max_h, _ = _author_metrics(wid, paper_authors, author_stats)
            if max_h < options.h_index_threshold:
                continue

        if options.high_profile_schools:
            max_cited, is_top = _institution_metrics(
                wid, paper_authors, institution_stats
            )
            if not is_top and max_cited < options.institution_citation_threshold:
                continue

        kept.append(wid)
    return kept


def rank(
    work_ids: list[str],
    works: dict[str, dict[str, Any]],
    paper_authors: dict[str, list[dict[str, Any]]],
    author_stats: dict[str, dict[str, Any]],
    institution_stats: dict[str, dict[str, Any]],
    options: RankingOptions,
) -> list[dict[str, Any]]:
    if not work_ids:
        return []
    current_year = date.today().year
    rows: list[dict[str, Any]] = []
    for wid in work_ids:
        work = works[wid]
        year = work.get("year") or current_year
        age = max(current_year - year, 1)
        cited = float(work.get("cited_by_count") or 0)
        max_h, total_author_citations = _author_metrics(wid, paper_authors, author_stats)
        max_inst_cited, is_top = _institution_metrics(
            wid, paper_authors, institution_stats
        )
        rows.append(
            {
                "id": wid,
                "relevance": float(work.get("relevance_score") or 0.0),
                "citations_per_year": cited / age,
                "author_prestige": max_h,
                "total_author_citations": total_author_citations,
                "institution_prestige": max_inst_cited,
                "is_top_institution": is_top,
                "year": year,
                "age": age,
            }
        )

    norm_rel = _normalize([r["relevance"] for r in rows])
    norm_cpy = _normalize([r["citations_per_year"] for r in rows], log=True)
    norm_auth = _normalize([r["author_prestige"] for r in rows], log=True)
    norm_inst = _normalize([r["institution_prestige"] for r in rows], log=True)
    norm_year = _normalize([float(r["year"]) for r in rows])

    weights = {**DEFAULT_WEIGHTS, **(options.weights or {})}
    if options.recency == "any":
        weights.pop("recency", None)
    total_weight = sum(weights.values()) or 1.0

    scored: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if options.recency == "foundational":
            recency_component = 1.0 - norm_year[i]
        else:
            recency_component = norm_year[i]
        components = {
            "relevance": norm_rel[i],
            "citations_per_year": norm_cpy[i],
            "author_prestige": norm_auth[i],
            "institution_prestige": norm_inst[i],
            "recency": recency_component,
        }
        score = sum(weights.get(k, 0.0) * v for k, v in components.items()) / total_weight
        row_out = dict(row)
        row_out["score"] = round(score, 6)
        row_out["components"] = {k: round(v, 4) for k, v in components.items()}
        scored.append(row_out)

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored
