from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from .config import load_settings, save_api_key
from .fields import CATEGORIES
from .openalex import OpenAlexClient, OpenAlexError
from .service import IngestRequest, PaperDeckService, SearchRequest

app = typer.Typer(
    name="paperdeck",
    help="Scoped, cached reading-list generator and temporal research graph over OpenAlex.",
    no_args_is_help=True,
    add_completion=False,
)


def _service() -> PaperDeckService:
    return PaperDeckService()


def _print_papers(payload: dict) -> None:
    query = payload["query"]
    typer.echo(
        f"{query['category_label']} | '{query['query']}' | "
        f"type={query['paper_type']} mode={query['resolved_search_mode']} "
        f"years={query['resolved_year_from']}..{query['resolved_year_to']}"
    )
    typer.echo(
        f"candidates={payload['cache']['candidates']} "
        f"kept={payload['kept_after_filters']} "
        f"returned={payload['returned']} "
        f"cache_hit={payload['cache']['hit']} "
        f"cost=${payload['api']['cost_usd']:.4f}"
    )
    typer.echo("-" * 72)
    for i, paper in enumerate(payload["papers"], 1):
        authors = ", ".join(a["name"] or "?" for a in paper["authors"]) or "?"
        typer.echo(f"{i:>2}. {paper['title']}")
        typer.echo(
            f"    {authors} ({paper['year']}) | {paper['venue'] or 'n/a'} | "
            f"cites={paper['cited_by_count']} | {paper['theory_label']} | "
            f"score={paper['score']}"
        )
        typer.echo(f"    {paper['doi_url'] or paper['openalex_url']}")
    if payload.get("rerank"):
        info = payload["rerank"]
        typer.echo(
            f"rerank: {info['provider']} dim={info['dim']} "
            f"embedded_now={info['embedded_now']} scored={info['scored']}"
        )
    if payload.get("graph"):
        meta = payload["graph"]["meta"]
        typer.echo("-" * 72)
        typer.echo(
            f"graph: {meta['paper_nodes']} papers, {meta['author_nodes']} authors, "
            f"edges={meta['edge_counts']}"
        )


@app.command()
def setup(
    key: str = typer.Option(None, "--key", help="Provide the key non-interactively."),
    no_validate: bool = typer.Option(
        False, "--no-validate", help="Save without checking the key against OpenAlex."
    ),
) -> None:
    """One-time setup: store your OpenAlex API key outside the repo."""
    if not key:
        key = typer.prompt("OpenAlex API key", hide_input=True)
    key = key.strip()
    if not key:
        raise typer.BadParameter("Empty key")

    if not no_validate:
        settings = load_settings(api_key=key)

        async def check() -> None:
            async with OpenAlexClient(
                api_key=key,
                base_url=settings.openalex_base_url,
                user_agent=settings.user_agent,
            ) as client:
                data = await client.rate_limit()
                info = client.last_rate_limit
                typer.echo(
                    f"Key valid. Daily budget: ${info.limit_usd if info.limit_usd is not None else '?'} "
                    f"| remaining: ${info.remaining_usd if info.remaining_usd is not None else '?'} "
                    f"| resets in {info.reset_seconds}s"
                )
                if data.get("cost_usd") is not None:
                    pass

        try:
            asyncio.run(check())
        except OpenAlexError as exc:
            typer.echo(f"Validation failed: {exc}", err=True)
            raise typer.Exit(code=1)

    path = save_api_key(key)
    typer.echo(f"Saved to {path} (chmod 600). Key is now available to all paperdeck commands.")


@app.command()
def search(
    category: str = typer.Option(..., "--category", "-c", help=", ".join(CATEGORIES)),
    query: str = typer.Option(..., "--query", "-q"),
    paper_type: str = typer.Option("both", "--type", help="theoretical|empirical|both"),
    mode: str = typer.Option("auto", "--mode", help="auto|default|exact|semantic"),
    num: int = typer.Option(10, "--num", "-n"),
    recency: str = typer.Option("any", "--recency", help="any|modern|foundational"),
    year_from: int = typer.Option(None, "--year-from"),
    year_to: int = typer.Option(None, "--year-to"),
    min_citations: int = typer.Option(0, "--min-citations"),
    high_profile_researchers: bool = typer.Option(False, "--high-profile-researchers"),
    high_profile_schools: bool = typer.Option(False, "--high-profile-schools"),
    graph: bool = typer.Option(False, "--graph", help="Include the temporal graph."),
    external_refs: bool = typer.Option(False, "--external-refs"),
    max_candidates: int = typer.Option(300, "--max-candidates"),
    rerank: bool = typer.Option(
        False, "--rerank", help="Rerank the scoped pool with local embeddings."
    ),
    embed_provider: str = typer.Option(
        "auto", "--embed-provider", help="auto|fastembed|sentence-transformers|hashing"
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore the query cache."),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """Generate a reading list for a topic within a category."""
    req = SearchRequest(
        category=category,
        query=query,
        paper_type=paper_type,
        search_mode=mode,
        num_papers=num,
        recency=recency,
        year_from=year_from,
        year_to=year_to,
        min_citations=min_citations,
        high_profile_researchers=high_profile_researchers,
        high_profile_schools=high_profile_schools,
        include_graph=graph,
        include_external_references=external_refs,
        max_candidates=max_candidates,
        rerank=rerank,
        embedding_provider=embed_provider,
        refresh=refresh,
    )
    try:
        payload = asyncio.run(_service().search(req))
    except (KeyError, OpenAlexError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _print_papers(payload)


@app.command()
def ingest(
    category: list[str] = typer.Option(
        ..., "--category", "-c", help="Repeatable. " + ", ".join(CATEGORIES)
    ),
    min_citations: int = typer.Option(0, "--min-citations"),
    max_works: int = typer.Option(100000, "--max-works", help="Per category by default."),
    year_from: int = typer.Option(None, "--year-from"),
    year_to: int = typer.Option(None, "--year-to"),
    per_category: bool = typer.Option(
        True, "--per-category/--total", help="Apply --max-works per category or overall."
    ),
    enrich: bool = typer.Option(
        False, "--enrich", help="Also fetch author h-index / institution metrics."
    ),
    embeddings: bool = typer.Option(
        False, "--embeddings", help="Precompute local embeddings while ingesting."
    ),
    embed_provider: str = typer.Option("auto", "--embed-provider"),
) -> None:
    """Bounded bulk ingest of a category into the local cache (no 740 GB snapshot)."""
    req = IngestRequest(
        categories=category,
        min_citations=min_citations,
        year_from=year_from,
        year_to=year_to,
        max_works=max_works,
        per_category=per_category,
        enrich=enrich,
        include_embeddings=embeddings,
        embedding_provider=embed_provider,
    )

    def progress(payload: dict) -> None:
        if payload.get("stage") == "ingesting":
            typer.echo(
                f"\r{payload['category_label']}: "
                f"{payload['fetched']:,}/{payload['category_total']:,}",
                nl=False,
            )
        else:
            typer.echo()

    try:
        summary = asyncio.run(_service().ingest(req, progress=progress))
    except (KeyError, OpenAlexError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    typer.echo(f"ingested {summary['ingested']:,} works "
               f"({summary['requests']} requests, ${summary['cost_usd']})")
    for row in summary["categories"]:
        typer.echo(f"  {row['label']}: {row['ingested']:,} of {row['available']:,}")


@app.command()
def graph(
    category: str = typer.Option(..., "--category", "-c"),
    query: str = typer.Option(..., "--query", "-q"),
    out: Path = typer.Option(Path("graph.json"), "--out", "-o"),
    paper_type: str = typer.Option("both", "--type"),
    mode: str = typer.Option("auto", "--mode"),
    num: int = typer.Option(25, "--num", "-n"),
    recency: str = typer.Option("any", "--recency"),
    scope: str = typer.Option("reading_list", "--scope", help="reading_list|candidate_pool"),
    kind: str = typer.Option("both", "--kind", help="both|papers|authors"),
    external_refs: bool = typer.Option(False, "--external-refs"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Export the temporal graph for a query as JSON."""
    req = SearchRequest(
        category=category,
        query=query,
        paper_type=paper_type,
        search_mode=mode,
        num_papers=num,
        recency=recency,
        include_graph=True,
        graph_scope=scope,
        graph_kind=kind,
        include_external_references=external_refs,
        refresh=refresh,
    )
    try:
        payload = asyncio.run(_service().search(req))
    except (KeyError, OpenAlexError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    out.write_text(json.dumps(payload.get("graph"), indent=2))
    meta = (payload.get("graph") or {}).get("meta", {})
    typer.echo(f"Wrote {out} | {meta}")


@app.command()
def stats() -> None:
    """Show cache and budget stats."""
    typer.echo(json.dumps(_service().stats(), indent=2))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
    open_browser: bool = typer.Option(False, "--open", help="Open the UI in a browser."),
) -> None:
    """Run the local web UI and API."""
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    typer.echo(f"PaperDeck UI: {url}")
    uvicorn.run("paperdeck.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
