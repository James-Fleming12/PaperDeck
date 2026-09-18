from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .embeddings import pack_vector, unpack_vector

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    doi TEXT,
    title TEXT,
    abstract TEXT,
    year INTEGER,
    publication_date TEXT,
    type TEXT,
    cited_by_count INTEGER DEFAULT 0,
    fwci REAL,
    subfield_id TEXT,
    field_id TEXT,
    topic_ids TEXT,
    venue_id TEXT,
    venue_name TEXT,
    referenced_works TEXT,
    is_retracted INTEGER DEFAULT 0,
    theory_label TEXT,
    theory_score REAL DEFAULT 0,
    empirical_score REAL DEFAULT 0,
    relevance_score REAL,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_works_year ON works(year);
CREATE INDEX IF NOT EXISTS idx_works_subfield ON works(subfield_id);
CREATE INDEX IF NOT EXISTS idx_works_field ON works(field_id);
CREATE INDEX IF NOT EXISTS idx_works_label ON works(theory_label);
CREATE INDEX IF NOT EXISTS idx_works_cited ON works(cited_by_count);

CREATE TABLE IF NOT EXISTS authors (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    cited_by_count INTEGER DEFAULT 0,
    h_index INTEGER,
    works_count INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS institutions (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    country_code TEXT,
    type TEXT,
    cited_by_count INTEGER DEFAULT 0,
    works_count INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_authors (
    work_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    position INTEGER,
    is_corresponding INTEGER DEFAULT 0,
    institution_ids TEXT,
    PRIMARY KEY (work_id, author_id)
);

CREATE INDEX IF NOT EXISTS idx_pa_author ON paper_authors(author_id);

CREATE TABLE IF NOT EXISTS paper_institutions (
    work_id TEXT NOT NULL,
    institution_id TEXT NOT NULL,
    PRIMARY KEY (work_id, institution_id)
);

CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    year INTEGER,
    weight REAL DEFAULT 1.0,
    PRIMARY KEY (src, dst, edge_type, year)
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);

CREATE TABLE IF NOT EXISTS query_cache (
    query_hash TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    work_ids TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    work_id TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (work_id, model)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

CREATE TABLE IF NOT EXISTS work_topics (
    work_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    PRIMARY KEY (work_id, topic_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
    title, abstract, content='works', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS works_ai AFTER INSERT ON works BEGIN
    INSERT INTO works_fts(rowid, title, abstract)
    VALUES (new.rowid, new.title, new.abstract);
END;

CREATE TRIGGER IF NOT EXISTS works_ad AFTER DELETE ON works BEGIN
    INSERT INTO works_fts(works_fts, rowid, title, abstract)
    VALUES ('delete', old.rowid, old.title, old.abstract);
END;

CREATE TRIGGER IF NOT EXISTS works_au AFTER UPDATE ON works BEGIN
    INSERT INTO works_fts(works_fts, rowid, title, abstract)
    VALUES ('delete', old.rowid, old.title, old.abstract);
    INSERT INTO works_fts(rowid, title, abstract)
    VALUES (new.rowid, new.title, new.abstract);
END;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-20000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_works(self, works: Iterable[dict[str, Any]]) -> int:
        rows = list(works)
        if not rows:
            return 0
        sql = """
        INSERT INTO works (
            id, doi, title, abstract, year, publication_date, type,
            cited_by_count, fwci, subfield_id, field_id, topic_ids,
            venue_id, venue_name, referenced_works, is_retracted,
            theory_label, theory_score, empirical_score, relevance_score, updated_at
        ) VALUES (
            :id, :doi, :title, :abstract, :year, :publication_date, :type,
            :cited_by_count, :fwci, :subfield_id, :field_id, :topic_ids,
            :venue_id, :venue_name, :referenced_works, :is_retracted,
            :theory_label, :theory_score, :empirical_score, :relevance_score, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            doi=excluded.doi,
            title=excluded.title,
            abstract=excluded.abstract,
            year=excluded.year,
            publication_date=excluded.publication_date,
            type=excluded.type,
            cited_by_count=excluded.cited_by_count,
            fwci=excluded.fwci,
            subfield_id=excluded.subfield_id,
            field_id=excluded.field_id,
            topic_ids=excluded.topic_ids,
            venue_id=excluded.venue_id,
            venue_name=excluded.venue_name,
            referenced_works=excluded.referenced_works,
            is_retracted=excluded.is_retracted,
            theory_label=excluded.theory_label,
            theory_score=excluded.theory_score,
            empirical_score=excluded.empirical_score,
            relevance_score=excluded.relevance_score,
            updated_at=excluded.updated_at
        """
        now = utcnow()
        payload = []
        for w in rows:
            payload.append(
                {
                    "id": w["id"],
                    "doi": w.get("doi"),
                    "title": w.get("title"),
                    "abstract": w.get("abstract"),
                    "year": w.get("year"),
                    "publication_date": w.get("publication_date"),
                    "type": w.get("type"),
                    "cited_by_count": w.get("cited_by_count") or 0,
                    "fwci": w.get("fwci"),
                    "subfield_id": w.get("subfield_id"),
                    "field_id": w.get("field_id"),
                    "topic_ids": json.dumps(w.get("topic_ids") or []),
                    "venue_id": w.get("venue_id"),
                    "venue_name": w.get("venue_name"),
                    "referenced_works": json.dumps(w.get("referenced_works") or []),
                    "is_retracted": int(bool(w.get("is_retracted"))),
                    "theory_label": w.get("theory_label"),
                    "theory_score": w.get("theory_score") or 0.0,
                    "empirical_score": w.get("empirical_score") or 0.0,
                    "relevance_score": w.get("relevance_score"),
                    "updated_at": now,
                }
            )
        with self.connect() as conn:
            conn.executemany(sql, payload)
            work_topic_rows = [
                (w["id"], tid)
                for w in rows
                for tid in (w.get("topic_ids") or [])
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO work_topics(work_id, topic_id) VALUES (?, ?)",
                work_topic_rows,
            )
        return len(payload)

    def upsert_authors(self, authors: Iterable[dict[str, Any]]) -> None:
        rows = list(authors)
        if not rows:
            return
        now = utcnow()
        sql = """
        INSERT INTO authors (id, display_name, cited_by_count, h_index, works_count, updated_at)
        VALUES (:id, :display_name, :cited_by_count, :h_index, :works_count, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            display_name=excluded.display_name,
            cited_by_count=COALESCE(excluded.cited_by_count, authors.cited_by_count),
            h_index=COALESCE(excluded.h_index, authors.h_index),
            works_count=COALESCE(excluded.works_count, authors.works_count),
            updated_at=excluded.updated_at
        """
        with self.connect() as conn:
            conn.executemany(
                sql,
                [
                    {
                        "id": a["id"],
                        "display_name": a.get("display_name"),
                        "cited_by_count": a.get("cited_by_count"),
                        "h_index": a.get("h_index"),
                        "works_count": a.get("works_count"),
                        "updated_at": now,
                    }
                    for a in rows
                ],
            )

    def upsert_institutions(self, institutions: Iterable[dict[str, Any]]) -> None:
        rows = list(institutions)
        if not rows:
            return
        now = utcnow()
        sql = """
        INSERT INTO institutions (id, display_name, country_code, type, cited_by_count, works_count, updated_at)
        VALUES (:id, :display_name, :country_code, :type, :cited_by_count, :works_count, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            display_name=excluded.display_name,
            country_code=excluded.country_code,
            type=excluded.type,
            cited_by_count=COALESCE(excluded.cited_by_count, institutions.cited_by_count),
            works_count=COALESCE(excluded.works_count, institutions.works_count),
            updated_at=excluded.updated_at
        """
        with self.connect() as conn:
            conn.executemany(
                sql,
                [
                    {
                        "id": i["id"],
                        "display_name": i.get("display_name"),
                        "country_code": i.get("country_code"),
                        "type": i.get("type"),
                        "cited_by_count": i.get("cited_by_count"),
                        "works_count": i.get("works_count"),
                        "updated_at": now,
                    }
                    for i in rows
                ],
            )

    def set_paper_authors(self, work_id: str, links: Iterable[dict[str, Any]]) -> None:
        rows = [link for link in links if link.get("author_id")]
        with self.connect() as conn:
            conn.execute("DELETE FROM paper_authors WHERE work_id = ?", (work_id,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO paper_authors
                    (work_id, author_id, position, is_corresponding, institution_ids)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        work_id,
                        link["author_id"],
                        link.get("position"),
                        int(bool(link.get("is_corresponding"))),
                        json.dumps(link.get("institution_ids") or []),
                    )
                    for link in rows
                ],
            )
            inst_rows = {
                (work_id, iid)
                for link in rows
                for iid in (link.get("institution_ids") or [])
            }
            conn.executemany(
                "INSERT OR IGNORE INTO paper_institutions(work_id, institution_id) VALUES (?, ?)",
                list(inst_rows),
            )

    def set_paper_authors_bulk(
        self, links_by_work: dict[str, list[dict[str, Any]]]
    ) -> None:
        if not links_by_work:
            return
        rows: list[tuple[Any, ...]] = []
        inst_rows: set[tuple[str, str]] = set()
        for work_id, links in links_by_work.items():
            for link in links:
                author_id = link.get("author_id")
                if not author_id:
                    continue
                institution_ids = link.get("institution_ids") or []
                rows.append(
                    (
                        work_id,
                        author_id,
                        link.get("position"),
                        int(bool(link.get("is_corresponding"))),
                        json.dumps(institution_ids),
                    )
                )
                for iid in institution_ids:
                    inst_rows.add((work_id, iid))
        work_ids = list(links_by_work)
        with self.connect() as conn:
            for chunk in _chunks(work_ids, 500):
                placeholders = ",".join("?" * len(chunk))
                conn.execute(
                    f"DELETE FROM paper_authors WHERE work_id IN ({placeholders})",
                    chunk,
                )
                conn.execute(
                    f"DELETE FROM paper_institutions WHERE work_id IN ({placeholders})",
                    chunk,
                )
            conn.executemany(
                """
                INSERT OR REPLACE INTO paper_authors
                    (work_id, author_id, position, is_corresponding, institution_ids)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO paper_institutions(work_id, institution_id) VALUES (?, ?)",
                list(inst_rows),
            )

    def add_edges(self, edges: Iterable[tuple[str, str, str, int | None, float]]) -> None:
        rows = list(edges)
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO edges (src, dst, edge_type, year, weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_edges(
        self,
        node_ids: Iterable[str],
        edge_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return []
        types = list(edge_types or [])
        out: list[dict[str, Any]] = []
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                sql = f"SELECT * FROM edges WHERE src IN ({placeholders})"
                params: list[Any] = list(chunk)
                if types:
                    tph = ",".join("?" * len(types))
                    sql += f" AND edge_type IN ({tph})"
                    params.extend(types)
                for row in conn.execute(sql, params).fetchall():
                    out.append(dict(row))
        return out

    def authors_needing_enrichment(self, author_ids: Iterable[str]) -> list[str]:
        ids = list(dict.fromkeys(author_ids))
        if not ids:
            return []
        missing: list[str] = []
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT id FROM authors WHERE id IN ({placeholders}) AND h_index IS NULL",
                    chunk,
                )
                found = {r["id"] for r in cur.fetchall()}
                cur2 = conn.execute(
                    f"SELECT id FROM authors WHERE id IN ({placeholders})",
                    chunk,
                )
                present = {r["id"] for r in cur2.fetchall()}
                missing.extend(found)
                missing.extend([i for i in chunk if i not in present])
        return list(dict.fromkeys(missing))

    def institutions_needing_enrichment(self, institution_ids: Iterable[str]) -> list[str]:
        ids = list(dict.fromkeys(institution_ids))
        if not ids:
            return []
        missing: list[str] = []
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT id FROM institutions WHERE id IN ({placeholders}) AND works_count IS NULL",
                    chunk,
                )
                found = {r["id"] for r in cur.fetchall()}
                cur2 = conn.execute(
                    f"SELECT id FROM institutions WHERE id IN ({placeholders})",
                    chunk,
                )
                present = {r["id"] for r in cur2.fetchall()}
                missing.extend(found)
                missing.extend([i for i in chunk if i not in present])
        return list(dict.fromkeys(missing))

    def get_works(self, work_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(work_ids))
        if not ids:
            return []
        out: list[dict[str, Any]] = []
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT * FROM works WHERE id IN ({placeholders})", chunk
                )
                out.extend(_row_to_work(r) for r in cur.fetchall())
        return out

    def get_authors(self, author_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(author_ids))
        out: dict[str, dict[str, Any]] = {}
        if not ids:
            return out
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT * FROM authors WHERE id IN ({placeholders})", chunk
                )
                for r in cur.fetchall():
                    out[r["id"]] = dict(r)
        return out

    def get_author(self, author_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            cur = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def works_by_author(self, author_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT w.* FROM works w
                JOIN paper_authors pa ON pa.work_id = w.id
                WHERE pa.author_id = ?
                ORDER BY w.year DESC, w.cited_by_count DESC
                LIMIT ?
                """,
                (author_id, limit),
            )
            return [_row_to_work(r) for r in cur.fetchall()]

    def author_works_matching(
        self, author_id: str, query: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        match = _fts_query(query)
        if not match:
            return []
        with self.connect() as conn:
            try:
                cur = conn.execute(
                    """
                    SELECT w.* FROM works_fts f
                    JOIN works w ON w.rowid = f.rowid
                    JOIN paper_authors pa ON pa.work_id = w.id
                    WHERE works_fts MATCH ? AND pa.author_id = ?
                    ORDER BY w.year DESC, w.cited_by_count DESC
                    LIMIT ?
                    """,
                    (match, author_id, limit),
                )
                return [_row_to_work(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                return []

    def get_institutions(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        wanted = list(dict.fromkeys(ids))
        out: dict[str, dict[str, Any]] = {}
        if not wanted:
            return out
        with self.connect() as conn:
            for chunk in _chunks(wanted, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT * FROM institutions WHERE id IN ({placeholders})", chunk
                )
                for r in cur.fetchall():
                    out[r["id"]] = dict(r)
        return out

    def paper_authors_map(self, work_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        ids = list(dict.fromkeys(work_ids))
        out: dict[str, list[dict[str, Any]]] = {}
        if not ids:
            return out
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"""
                    SELECT pa.*, a.display_name AS author_name, a.h_index AS author_h_index,
                           a.cited_by_count AS author_cited_by_count
                    FROM paper_authors pa
                    LEFT JOIN authors a ON a.id = pa.author_id
                    WHERE pa.work_id IN ({placeholders})
                    """,
                    chunk,
                )
                for r in cur.fetchall():
                    link = dict(r)
                    try:
                        link["institution_ids"] = json.loads(
                            link.get("institution_ids") or "[]"
                        )
                    except (TypeError, json.JSONDecodeError):
                        link["institution_ids"] = []
                    out.setdefault(r["work_id"], []).append(link)
        return out

    def get_cached_query(self, query_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM query_cache WHERE query_hash = ?", (query_hash,)
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "query_hash": row["query_hash"],
            "payload": json.loads(row["payload"]),
            "work_ids": json.loads(row["work_ids"]),
            "created_at": row["created_at"],
        }

    def store_cached_query(
        self, query_hash: str, payload: dict[str, Any], work_ids: list[str]
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO query_cache (query_hash, payload, work_ids, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(query_hash) DO UPDATE SET
                    payload=excluded.payload,
                    work_ids=excluded.work_ids,
                    created_at=excluded.created_at
                """,
                (query_hash, json.dumps(payload), json.dumps(work_ids), utcnow()),
            )

    def embeddings_missing(self, work_ids: Iterable[str], model: str) -> list[str]:
        ids = list(dict.fromkeys(work_ids))
        if not ids:
            return []
        present: set[str] = set()
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT work_id FROM embeddings WHERE model = ? AND work_id IN ({placeholders})",
                    [model, *chunk],
                )
                present.update(r["work_id"] for r in cur.fetchall())
        return [wid for wid in ids if wid not in present]

    def upsert_embeddings(self, model: str, vectors: dict[str, list[float]]) -> None:
        if not vectors:
            return
        now = utcnow()
        rows = [
            (wid, model, len(vec), pack_vector(vec), now)
            for wid, vec in vectors.items()
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO embeddings (work_id, model, dim, vector, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(work_id, model) DO UPDATE SET
                    dim=excluded.dim,
                    vector=excluded.vector,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_embeddings(
        self, work_ids: Iterable[str], model: str
    ) -> dict[str, list[float]]:
        ids = list(dict.fromkeys(work_ids))
        out: dict[str, list[float]] = {}
        if not ids:
            return out
        with self.connect() as conn:
            for chunk in _chunks(ids, 500):
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"SELECT work_id, dim, vector FROM embeddings WHERE model = ? AND work_id IN ({placeholders})",
                    [model, *chunk],
                )
                for row in cur.fetchall():
                    out[row["work_id"]] = unpack_vector(row["vector"], row["dim"])
        return out

    def embedding_count(self, model: str | None = None) -> int:
        with self.connect() as conn:
            if model:
                cur = conn.execute(
                    "SELECT COUNT(*) AS n FROM embeddings WHERE model = ?", (model,)
                )
            else:
                cur = conn.execute(
                    "SELECT COUNT(*) AS n FROM (SELECT DISTINCT model FROM embeddings)"
                )
            return cur.fetchone()["n"]

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            out = {}
            for table in (
                "works",
                "authors",
                "institutions",
                "edges",
                "paper_authors",
                "embeddings",
            ):
                cur = conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
                out[table] = cur.fetchone()["n"]
        return out

    ORDER_COLUMNS = {
        "cited_by_count": "w.cited_by_count",
        "year": "w.year",
    }

    def select_work_ids(
        self,
        subfield_ids: Iterable[str] | None = None,
        field_ids: Iterable[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        min_citations: int | None = None,
        theory_label: str | None = None,
        text: str | None = None,
        order_by: str = "cited_by_count",
        limit: int = 5000,
    ) -> list[str]:
        where: list[str] = []
        params: list[Any] = []
        joins = ""

        subfields = [s for s in (subfield_ids or []) if s]
        fields = [f for f in (field_ids or []) if f]
        scope: list[str] = []
        if subfields:
            scope.append(f"w.subfield_id IN ({','.join('?' * len(subfields))})")
            params.extend(subfields)
        if fields:
            scope.append(f"w.field_id IN ({','.join('?' * len(fields))})")
            params.extend(fields)
        if scope:
            where.append("(" + " OR ".join(scope) + ")")
        if year_from is not None:
            where.append("w.year >= ?")
            params.append(year_from)
        if year_to is not None:
            where.append("w.year <= ?")
            params.append(year_to)
        if min_citations:
            where.append("w.cited_by_count >= ?")
            params.append(min_citations)
        if theory_label and theory_label != "any":
            where.append("w.theory_label = ?")
            params.append(theory_label)

        match = _fts_query(text) if text else None
        if match:
            joins = "JOIN works_fts f ON f.rowid = w.rowid"
            where.append("works_fts MATCH ?")
            params.append(match)

        order = self.ORDER_COLUMNS.get(order_by, "w.cited_by_count")
        sql = f"SELECT w.id FROM works w {joins}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} DESC LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            try:
                cur = conn.execute(sql, params)
                return [r["id"] for r in cur.fetchall()]
            except sqlite3.OperationalError:
                return []

    def facets(self) -> dict[str, Any]:
        with self.connect() as conn:
            subfields = [
                {"id": r["subfield_id"], "count": r["n"]}
                for r in conn.execute(
                    "SELECT subfield_id, COUNT(*) n FROM works "
                    "WHERE subfield_id IS NOT NULL GROUP BY subfield_id ORDER BY n DESC"
                )
            ]
            labels = [
                {"label": r["theory_label"], "count": r["n"]}
                for r in conn.execute(
                    "SELECT theory_label, COUNT(*) n FROM works "
                    "GROUP BY theory_label ORDER BY n DESC"
                )
            ]
            row = conn.execute(
                "SELECT MIN(year) lo, MAX(year) hi FROM works"
            ).fetchone()
            total = conn.execute("SELECT COUNT(*) n FROM works").fetchone()["n"]
        return {
            "total_works": total,
            "subfields": subfields,
            "theory_labels": labels,
            "year_range": [row["lo"], row["hi"]],
        }

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            cur = conn.execute("SELECT MIN(year) AS lo, MAX(year) AS hi FROM works")
            row = cur.fetchone()
        result = self.counts()
        result["year_range"] = [row["lo"], row["hi"]]
        return result


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _fts_query(query: str) -> str | None:
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return None
    return " ".join(f'"{token}"' for token in tokens)


def _row_to_work(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("topic_ids", "referenced_works"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, json.JSONDecodeError):
                d[key] = []
        else:
            d[key] = []
    d["is_retracted"] = bool(d.get("is_retracted"))
    return d
