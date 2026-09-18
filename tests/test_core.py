from __future__ import annotations

from pathlib import Path

from paperdeck.classifier import classify
from paperdeck.db import Database
from paperdeck.embeddings import get_provider
from paperdeck.fields import get_category
from paperdeck.graph import build_graph, expand_with_cached_references
from paperdeck.openalex import normalize_work, reconstruct_abstract
from paperdeck.service import SearchRequest, PaperDeckService, dedupe_by_title


def test_reconstruct_abstract() -> None:
    inverted = {"We": [0], "prove": [1], "a": [2], "theorem": [3]}
    assert reconstruct_abstract(inverted) == "We prove a theorem"
    assert reconstruct_abstract(None) is None


def test_normalize_work_short_ids() -> None:
    raw = {
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1/abc",
        "title": "T",
        "publication_year": 2024,
        "cited_by_count": 5,
        "referenced_works": ["https://openalex.org/W9"],
        "primary_topic": {"subfield": {"id": "https://openalex.org/subfields/1702"}},
        "authorships": [
            {
                "author": {"id": "https://openalex.org/A1", "display_name": "A"},
                "institutions": [{"id": "https://openalex.org/I1", "display_name": "MIT"}],
                "is_corresponding": True,
            }
        ],
    }
    work = normalize_work(raw)
    assert work["id"] == "W123"
    assert work["doi"] == "10.1/abc"
    assert work["subfield_id"] == "1702"
    assert work["referenced_works"] == ["W9"]
    assert work["authorships"][0]["author_id"] == "A1"
    assert work["authorships"][0]["institution_ids"] == ["I1"]


def test_classifier_venue_signal() -> None:
    theory = classify(
        "A tight lower bound",
        "We prove a theorem and give a convergence result.",
        venue="COLT",
        category=get_category("ml_theory"),
    )
    empirical = classify(
        "A new benchmark",
        "We train on a dataset and outperform the baseline on the test set.",
        venue="CVPR",
        category=get_category("ml_theory"),
    )
    assert theory.label == "theoretical"
    assert empirical.label == "empirical"


def test_classifier_low_signal_is_mixed() -> None:
    result = classify("Short note", "A brief remark.", category=get_category("math"))
    assert result.label == "mixed"


def test_category_filters_and_matches() -> None:
    math = get_category("math")
    assert math.openalex_filters == ["primary_topic.field.id:26"]
    assert math.matches("2602", "26")
    assert not math.matches("1702", "17")

    rendering = get_category("rendering")
    assert rendering.openalex_filters == ["primary_topic.subfield.id:1704"]


def test_dedupe_prefers_published_version() -> None:
    works = {
        "W1": {"title": "Fourier Features", "venue_name": "arXiv", "cited_by_count": 10},
        "W2": {"title": "fourier  features!", "venue_name": "NeurIPS", "cited_by_count": 3},
    }
    assert dedupe_by_title(["W1", "W2"], works) == ["W2"]


def _make_work(wid: str, title: str, year: int, refs: list[str], label: str = "theoretical"):
    return {
        "id": wid,
        "title": title,
        "year": year,
        "referenced_works": refs,
        "cited_by_count": 1,
        "theory_label": label,
        "subfield_id": "1702",
        "field_id": "17",
    }


def test_db_and_graph_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    db.init()
    db.upsert_works(
        [
            _make_work("W1", "First", 2000, []),
            _make_work("W2", "Second", 2020, ["W1"]),
        ]
    )
    db.upsert_authors(
        [
            {"id": "A1", "display_name": "Alice", "h_index": 40, "cited_by_count": 100},
            {"id": "A2", "display_name": "Bob", "h_index": 5, "cited_by_count": 10},
        ]
    )
    db.set_paper_authors(
        "W1", [{"author_id": "A1", "position": 0, "institution_ids": ["I1"]}]
    )
    db.set_paper_authors(
        "W2",
        [
            {"author_id": "A1", "position": 0, "institution_ids": ["I1"]},
            {"author_id": "A2", "position": 1, "institution_ids": []},
        ],
    )
    db.add_edges([("W2", "W1", "citation", 2020, 1.0)])

    graph = build_graph(db, ["W1", "W2"])
    assert graph["meta"]["paper_nodes"] == 2
    assert graph["meta"]["author_nodes"] == 2
    assert graph["meta"]["edge_counts"]["citation"] == 1
    assert graph["meta"]["edge_counts"]["authorship"] == 3
    assert graph["meta"]["edge_counts"]["coauthorship"] == 1
    assert graph["year_range"] == [2000, 2020]

    expanded = expand_with_cached_references(db, ["W2"])
    assert set(expanded) == {"W1", "W2"}

    counts = db.counts()
    assert counts["works"] == 2
    assert counts["edges"] == 1


def test_hashing_provider_is_deterministic_and_normalized() -> None:
    provider = get_provider("hashing")
    first = provider.encode(["Fourier analysis of neural networks"])
    second = provider.encode(["Fourier analysis of neural networks"])
    assert first == second
    norm = sum(x * x for x in first[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-5

    query = provider.encode(["fourier neural network"])[0]
    close = provider.encode(["Fourier analysis of neural networks"])[0]
    far = provider.encode(["soil organic carbon spectroscopy"])[0]
    from paperdeck.embeddings import cosine

    assert cosine(query, close) > cosine(query, far)


def test_embedding_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "e.db")
    db.init()
    db.upsert_works([_make_work("W1", "A", 2020, []), _make_work("W2", "B", 2021, [])])
    provider = get_provider("hashing")
    vectors = provider.encode(["first paper", "second paper"])
    db.upsert_embeddings(provider.name, {"W1": vectors[0], "W2": vectors[1]})

    assert db.embeddings_missing(["W1", "W2"], provider.name) == []
    loaded = db.get_embeddings(["W1"], provider.name)
    assert all(abs(a - b) < 1e-6 for a, b in zip(loaded["W1"], vectors[0]))
    assert db.embedding_count(provider.name) == 2


def test_service_rerank_offline(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    db.init()
    db.upsert_works(
        [
            _make_work("W1", "Fourier analysis of neural networks", 2020, []),
            _make_work("W2", "Soil carbon spectroscopy", 2021, []),
        ]
    )
    service = PaperDeckService(db=db)
    service.settings.db_path = db.path
    works = {w["id"]: w for w in db.get_works(["W1", "W2"])}
    req = SearchRequest(
        category="ml_theory", query="fourier neural networks", rerank=True,
        embedding_provider="hashing",
    )
    info = service._rerank(req, ["W1", "W2"], works)
    assert info["embedded_now"] == 2
    assert works["W1"]["relevance_score"] > works["W2"]["relevance_score"]


def _graph_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "g.db")
    db.init()
    db.upsert_works(
        [
            _make_work("W1", "Fourier analysis of neural networks", 2000, []),
            _make_work("W2", "Soil carbon spectroscopy", 2020, ["W1"]),
        ]
    )
    db.upsert_authors(
        [
            {"id": "A1", "display_name": "Alice", "h_index": 40, "cited_by_count": 100},
            {"id": "A2", "display_name": "Bob", "h_index": 5, "cited_by_count": 10},
        ]
    )
    db.set_paper_authors("W1", [{"author_id": "A1", "position": 0, "institution_ids": []}])
    db.set_paper_authors(
        "W2",
        [
            {"author_id": "A1", "position": 0, "institution_ids": []},
            {"author_id": "A2", "position": 1, "institution_ids": []},
        ],
    )
    db.add_edges([("W2", "W1", "citation", 2020, 1.0)])
    return db


def test_graph_kinds(tmp_path: Path) -> None:
    db = _graph_db(tmp_path)
    papers = build_graph(db, ["W1", "W2"], kind="papers")
    assert all(n["type"] == "paper" for n in papers["nodes"])
    assert papers["meta"]["author_nodes"] == 0
    assert papers["meta"]["edge_counts"].get("citation") == 1
    assert "coauthorship" not in papers["meta"]["edge_counts"]

    authors = build_graph(db, ["W1", "W2"], kind="authors")
    assert all(n["type"] == "author" for n in authors["nodes"])
    assert authors["meta"]["edge_counts"].get("coauthorship") == 1
    assert "citation" not in authors["meta"]["edge_counts"]


def test_author_queries(tmp_path: Path) -> None:
    db = _graph_db(tmp_path)
    profile = db.get_author("A1")
    assert profile["display_name"] == "Alice"

    recent = {w["id"] for w in db.works_by_author("A1")}
    assert recent == {"W1", "W2"}

    matching = db.author_works_matching("A1", "fourier")
    assert [w["id"] for w in matching] == ["W1"]


def test_high_profile_school_parses_institution_ids(tmp_path: Path) -> None:
    from paperdeck.ranking import RankingOptions, apply_filters

    db = Database(tmp_path / "h.db")
    db.init()
    db.upsert_works([_make_work("W1", "Fourier analysis", 2020, [])])
    db.upsert_authors([{"id": "A1", "display_name": "Alice"}])
    db.upsert_institutions(
        [
            {
                "id": "I1",
                "display_name": "Massachusetts Institute of Technology",
                "cited_by_count": 5_000_000,
                "works_count": 100_000,
            }
        ]
    )
    db.set_paper_authors(
        "W1", [{"author_id": "A1", "position": 0, "institution_ids": ["I1"]}]
    )

    paper_authors = db.paper_authors_map(["W1"])
    assert paper_authors["W1"][0]["institution_ids"] == ["I1"]

    works = {w["id"]: w for w in db.get_works(["W1"])}
    author_stats = db.get_authors(["A1"])
    institution_stats = db.get_institutions(["I1"])
    kept = apply_filters(
        ["W1"],
        works,
        paper_authors,
        author_stats,
        institution_stats,
        RankingOptions(high_profile_schools=True),
    )
    assert kept == ["W1"]


def test_api_smoke(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PAPERDECK_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)

    from fastapi.testclient import TestClient

    from paperdeck import api as api_mod

    api_mod._service = None
    client = TestClient(api_mod.app)

    assert client.get("/health").json() == {"status": "ok"}
    assert "ml_theory" in client.get("/categories").json()
    assert client.get("/config").json()["has_api_key"] is False
    index = client.get("/")
    assert index.status_code == 200
    assert "PaperDeck" in index.text
    assert client.get("/ingest/nope").status_code == 404
