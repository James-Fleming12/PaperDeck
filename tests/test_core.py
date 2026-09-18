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
