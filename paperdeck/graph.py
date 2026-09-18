from __future__ import annotations

from itertools import combinations
from typing import Any

from .db import Database


def build_graph(
    db: Database,
    work_ids: list[str],
    include_external_references: bool = False,
    max_nodes: int = 20_000,
) -> dict[str, Any]:
    corpus = db.get_works(work_ids)
    corpus_ids = {w["id"] for w in corpus}
    paper_authors = db.paper_authors_map(corpus_ids)

    external_nodes: list[dict[str, Any]] = []
    if include_external_references:
        external_ids = sorted(
            {
                ref
                for work in corpus
                for ref in (work.get("referenced_works") or [])
                if ref and ref not in corpus_ids
            }
        )
        external_nodes = [
            {
                "id": rid,
                "type": "paper",
                "label": None,
                "year": None,
                "external": True,
            }
            for rid in external_ids
        ]

    author_ids = {
        link["author_id"]
        for links in paper_authors.values()
        for link in links
        if link.get("author_id")
    }
    node_ids = corpus_ids | {n["id"] for n in external_nodes} | author_ids
    if len(node_ids) > max_nodes:
        raise ValueError(
            f"Graph would contain {len(node_ids)} nodes (limit {max_nodes}). "
            "Reduce the corpus size or disable external references."
        )

    author_stats = db.get_authors(author_ids)

    nodes: list[dict[str, Any]] = []
    for work in corpus:
        nodes.append(
            {
                "id": work["id"],
                "type": "paper",
                "label": work.get("title"),
                "year": work.get("year"),
                "cited_by_count": work.get("cited_by_count"),
                "theory_label": work.get("theory_label"),
                "venue": work.get("venue_name"),
                "doi": work.get("doi"),
                "external": False,
            }
        )
    nodes.extend(external_nodes)
    for aid in sorted(author_ids):
        stats = author_stats.get(aid) or {}
        nodes.append(
            {
                "id": aid,
                "type": "author",
                "label": stats.get("display_name"),
                "h_index": stats.get("h_index"),
                "cited_by_count": stats.get("cited_by_count"),
                "external": False,
            }
        )

    links: list[dict[str, Any]] = []
    seen_authorship: set[tuple[str, str]] = set()
    coauthor_pairs: dict[tuple[str, str], dict[str, Any]] = {}

    for work in corpus:
        wid = work["id"]
        year = work.get("year")
        authored = [
            link["author_id"]
            for link in paper_authors.get(wid, [])
            if link.get("author_id")
        ]
        for aid in authored:
            key = (wid, aid)
            if key in seen_authorship:
                continue
            seen_authorship.add(key)
            links.append(
                {
                    "source": wid,
                    "target": aid,
                    "type": "authorship",
                    "year": year,
                    "weight": 1.0,
                }
            )
        for a, b in combinations(sorted(set(authored)), 2):
            entry = coauthor_pairs.setdefault(
                (a, b),
                {
                    "source": a,
                    "target": b,
                    "type": "coauthorship",
                    "year": year,
                    "weight": 0.0,
                },
            )
            entry["weight"] += 1.0
            if year is not None:
                entry["year"] = (
                    year if entry["year"] is None else min(entry["year"], year)
                )

    links.extend(coauthor_pairs.values())

    for edge in db.get_edges(corpus_ids, edge_types=["citation"]):
        if edge["dst"] in node_ids:
            links.append(
                {
                    "source": edge["src"],
                    "target": edge["dst"],
                    "type": "citation",
                    "year": edge["year"],
                    "weight": edge["weight"],
                }
            )

    years = [n["year"] for n in nodes if n.get("year")]
    year_range = [min(years), max(years)] if years else [None, None]

    counts: dict[str, int] = {}
    for link in links:
        counts[link["type"]] = counts.get(link["type"], 0) + 1

    return {
        "nodes": nodes,
        "links": links,
        "year_range": year_range,
        "meta": {
            "paper_nodes": sum(
                1 for n in nodes if n["type"] == "paper" and not n.get("external")
            ),
            "author_nodes": sum(1 for n in nodes if n["type"] == "author"),
            "external_nodes": sum(1 for n in nodes if n.get("external")),
            "edge_counts": counts,
        },
    }


def expand_with_cached_references(db: Database, work_ids: list[str]) -> list[str]:
    works = db.get_works(work_ids)
    referenced = {
        ref
        for work in works
        for ref in (work.get("referenced_works") or [])
        if ref
    }
    if not referenced:
        return list(dict.fromkeys(work_ids))
    cached = {w["id"] for w in db.get_works(list(referenced))}
    return list(dict.fromkeys([*work_ids, *sorted(cached)]))
