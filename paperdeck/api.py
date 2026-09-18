from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .fields import CATEGORIES
from .openalex import OpenAlexError
from .service import PaperDeckService, SearchRequest

app = FastAPI(title="PaperDeck", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_service: PaperDeckService | None = None


def get_service() -> PaperDeckService:
    global _service
    if _service is None:
        _service = PaperDeckService()
    return _service


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/categories")
def categories() -> dict:
    return {
        key: {"label": cat.label, "filters": cat.openalex_filters}
        for key, cat in CATEGORIES.items()
    }


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
