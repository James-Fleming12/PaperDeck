from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import config_path, load_settings, save_api_key
from .fields import CATEGORIES
from .openalex import OpenAlexClient, OpenAlexError
from .service import GraphRequest, IngestRequest, PaperDeckService, SearchRequest

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="PaperDeck", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_service: PaperDeckService | None = None
_jobs: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()


def get_service() -> PaperDeckService:
    global _service
    if _service is None:
        _service = PaperDeckService()
    return _service


class SetupRequest(BaseModel):
    api_key: str
    verify: bool = True


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/categories")
def categories() -> dict:
    return {
        key: {"label": cat.label, "filters": cat.openalex_filters}
        for key, cat in CATEGORIES.items()
    }


@app.get("/config")
def config() -> dict:
    service = get_service()
    return {
        "has_api_key": service.settings.has_key,
        "config_path": str(config_path()),
        "db_path": str(service.settings.db_path),
        "db": service.db.stats(),
    }


@app.post("/setup")
async def setup(req: SetupRequest) -> dict:
    key = req.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="Empty API key")
    if req.verify:
        settings = load_settings(api_key=key)
        try:
            async with OpenAlexClient(
                api_key=key,
                base_url=settings.openalex_base_url,
                user_agent=settings.user_agent,
            ) as client:
                await client.rate_limit()
                info = client.last_rate_limit
        except OpenAlexError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        budget = {
            "limit_usd": info.limit_usd,
            "remaining_usd": info.remaining_usd,
        }
    else:
        budget = {}

    path = save_api_key(key)
    global _service
    _service = None
    return {"saved": str(path), "budget": budget}


@app.get("/stats")
def stats() -> dict:
    return get_service().stats()


@app.post("/search")
async def search(req: SearchRequest) -> dict:
    try:
        return await get_service().search(req)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OpenAlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/graph")
async def graph(req: SearchRequest) -> dict:
    req.include_graph = True
    try:
        payload = await get_service().search(req)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OpenAlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return JSONResponse(
        {
            "graph": payload["graph"],
            "selected_ids": (payload.get("graph") or {}).get("selected_ids"),
            "query": payload["query"],
            "api": payload["api"],
        }
    )


@app.post("/graph/view")
def graph_view(req: GraphRequest) -> dict:
    try:
        return get_service().graph_view(req)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/graph/facets")
def graph_facets() -> dict:
    return get_service().db.facets()


@app.get("/author/{author_id}")
def author(author_id: str, q: str | None = None, limit: int = 30) -> dict:
    service = get_service()
    profile = service.db.get_author(author_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Author not in local cache yet")
    return {
        "author": profile,
        "recent": [_work_summary(w) for w in service.db.works_by_author(author_id, limit)],
        "matching": [
            _work_summary(w)
            for w in service.db.author_works_matching(author_id, q or "", limit)
        ]
        if q
        else [],
    }


def _work_summary(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": work.get("id"),
        "title": work.get("title"),
        "year": work.get("year"),
        "venue": work.get("venue_name"),
        "cited_by_count": work.get("cited_by_count"),
        "theory_label": work.get("theory_label"),
        "doi_url": f"https://doi.org/{work['doi']}" if work.get("doi") else None,
        "openalex_url": f"https://openalex.org/{work['id']}",
    }


@app.post("/ingest")
async def ingest(req: IngestRequest) -> dict:
    for key in req.categories:
        if key not in CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown category '{key}'")
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "id": job_id,
        "status": "running",
        "request": req.model_dump(),
        "progress": {"stage": "starting", "overall": 0},
        "summary": None,
        "error": None,
    }
    _jobs[job_id] = job

    def on_progress(payload: dict) -> None:
        job["progress"] = payload

    async def run() -> None:
        try:
            summary = await get_service().ingest(req, progress=on_progress)
            job["summary"] = summary
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            job["error"] = str(exc)
            job["status"] = "error"

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id, "status": "running"}


@app.get("/ingest/{job_id}")
def ingest_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown ingest job")
    return job


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
