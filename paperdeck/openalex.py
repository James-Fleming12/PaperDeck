from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from .fields import Category

WORK_SELECT = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "cited_by_count",
        "fwci",
        "referenced_works",
        "authorships",
        "primary_topic",
        "topics",
        "primary_location",
        "abstract_inverted_index",
        "is_retracted",
        "ids",
        "relevance_score",
    ]
)

AUTHOR_SELECT = "id,display_name,cited_by_count,summary_stats,works_count"
INSTITUTION_SELECT = "id,display_name,country_code,type,cited_by_count,works_count"

MAX_PER_PAGE = 100
SEMANTIC_PER_PAGE = 50
OR_BATCH = 100


class OpenAlexError(RuntimeError):
    pass


class MissingApiKey(OpenAlexError):
    pass


@dataclass
class SearchResult:
    works: list[dict[str, Any]]
    count: int
    cost_usd: float = 0.0
    requests: int = 0


@dataclass
class RateLimitInfo:
    limit_usd: float | None = None
    remaining_usd: float | None = None
    limit: int | None = None
    remaining: int | None = None
    cost_usd: float | None = None
    reset_seconds: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def short_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1]


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions)


class OpenAlexClient:
    def __init__(
        self,
        api_key: str | None,
        base_url: str = "https://api.openalex.org",
        timeout: float = 30.0,
        user_agent: str = "paperdeck/0.1",
        require_key: bool = False,
    ):
        if require_key and not api_key:
            raise MissingApiKey("No OpenAlex API key configured. Run `paperdeck setup`.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent
        self.last_rate_limit = RateLimitInfo()
        self.total_cost_usd = 0.0
        self.total_requests = 0
        self.last_count = 0
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    async def __aenter__(self) -> "OpenAlexClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {k: v for k, v in extra.items() if v is not None}
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _capture_rate_limit(self, headers: httpx.Headers, cost: float | None) -> None:
        def as_float(name: str) -> float | None:
            v = headers.get(name)
            try:
                return float(v) if v is not None else None
            except ValueError:
                return None

        self.last_rate_limit = RateLimitInfo(
            limit_usd=as_float("x-ratelimit-limit-usd"),
            remaining_usd=as_float("x-ratelimit-remaining-usd"),
            limit=int(as_float("x-ratelimit-limit") or 0) or None,
            remaining=int(as_float("x-ratelimit-remaining") or 0) or None,
            cost_usd=cost,
            reset_seconds=int(as_float("x-ratelimit-reset") or 0) or None,
            raw=dict(headers),
        )

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            response = await self._client.get(path, params=params)
            if response.status_code == 429:
                retry = float(response.headers.get("Retry-After", "1") or 1)
                body = response.text[:200]
                if attempt >= 2 or retry > 15:
                    raise OpenAlexError(
                        "OpenAlex rate limit / daily budget exhausted (429). "
                        "Configure a free API key with `paperdeck setup` for $1/day. "
                        f"Details: {body}"
                    )
                await asyncio.sleep(min(retry, 10.0))
                attempt += 1
                continue
            if response.status_code == 401:
                raise MissingApiKey("OpenAlex rejected the API key (401).")
            if response.status_code >= 400:
                raise OpenAlexError(
                    f"OpenAlex {response.status_code}: {response.text[:300]}"
                )
            data = response.json()
            cost = data.get("meta", {}).get("cost_usd")
            self.total_requests += 1
            if cost:
                self.total_cost_usd += cost
            self._capture_rate_limit(response.headers, cost)
            return data

    async def rate_limit(self) -> dict[str, Any]:
        return await self._get("/rate-limit", self._params({}))

    async def search_works(
        self,
        query: str,
        category: Category,
        search_mode: str = "default",
        year_from: int | None = None,
        year_to: int | None = None,
        min_citations: int | None = None,
        extra_filters: list[str] | None = None,
        max_results: int = 100,
        sort: str | None = None,
    ) -> SearchResult:
        filters: list[str] = []
        if search_mode != "semantic":
            filters.extend(category.openalex_filters)
        if year_from and year_to:
            filters.append(f"publication_year:{year_from}-{year_to}")
        elif year_from:
            filters.append(f"publication_year:>{year_from - 1}")
        elif year_to:
            filters.append(f"publication_year:<{year_to + 1}")
        if extra_filters:
            if search_mode == "semantic":
                allowed = {"primary_location.source.id"}
                extra_filters = [
                    f for f in extra_filters if f.split(":")[0] in allowed
                ]
            filters.extend(extra_filters)
        filters.append("is_retracted:false")
        if min_citations and search_mode != "semantic":
            filters.append(f"cited_by_count:>{min_citations - 1}")

        per_page = SEMANTIC_PER_PAGE if search_mode == "semantic" else MAX_PER_PAGE
        select = WORK_SELECT
        works: list[dict[str, Any]] = []
        cursor = "*"
        requests = 0
        total_count = 0
        cost = 0.0

        while len(works) < max_results:
            params: dict[str, Any] = {
                "filter": ",".join(filters),
                "per-page": min(per_page, max_results - len(works)),
                "select": select,
            }
            if search_mode == "semantic":
                params["search.semantic"] = query
                params["page"] = 1
            else:
                params["cursor"] = cursor
                if search_mode == "exact":
                    params["search.exact"] = query
                else:
                    params["search"] = query
            if sort and search_mode != "semantic":
                params["sort"] = sort

            data = await self._get("/works", self._params(params))
            requests += 1
            total_count = data.get("meta", {}).get("count", total_count)
            cost += data.get("meta", {}).get("cost_usd", 0.0) or 0.0
            results = data.get("results", [])
            works.extend(normalize_work(w) for w in results)
            cursor = data.get("meta", {}).get("next_cursor")
            if not results or not cursor:
                break
            if search_mode == "semantic":
                break

        return SearchResult(
            works=works[:max_results],
            count=total_count,
            cost_usd=round(cost, 6),
            requests=requests,
        )

    async def iter_works(
        self,
        category: Category,
        year_from: int | None = None,
        year_to: int | None = None,
        min_citations: int | None = None,
        extra_filters: list[str] | None = None,
        batch: int = 100,
    ):
        filters: list[str] = list(category.openalex_filters)
        if year_from and year_to:
            filters.append(f"publication_year:{year_from}-{year_to}")
        elif year_from:
            filters.append(f"publication_year:>{year_from - 1}")
        elif year_to:
            filters.append(f"publication_year:<{year_to + 1}")
        if extra_filters:
            filters.extend(extra_filters)
        filters.append("is_retracted:false")
        if min_citations:
            filters.append(f"cited_by_count:>{min_citations - 1}")

        cursor = "*"
        while True:
            params = {
                "filter": ",".join(filters),
                "per-page": min(batch, MAX_PER_PAGE),
                "select": WORK_SELECT,
                "cursor": cursor,
            }
            data = await self._get("/works", self._params(params))
            self.last_count = data.get("meta", {}).get("count", self.last_count)
            results = data.get("results", [])
            if results:
                yield [normalize_work(w) for w in results]
            cursor = data.get("meta", {}).get("next_cursor")
            if not results or not cursor:
                break

    async def fetch_works(self, work_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in _chunks(list(dict.fromkeys(work_ids)), OR_BATCH):
            flt = "|".join(chunk)
            params = {
                "filter": f"openalex_id:{flt}",
                "per-page": len(chunk),
                "select": WORK_SELECT,
            }
            data = await self._get("/works", self._params(params))
            out.extend(normalize_work(w) for w in data.get("results", []))
        return out

    async def enrich_authors(self, author_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in _chunks(list(dict.fromkeys(author_ids)), OR_BATCH):
            params = {
                "filter": f"openalex_id:{'|'.join(chunk)}",
                "per-page": len(chunk),
                "select": AUTHOR_SELECT,
            }
            data = await self._get("/authors", self._params(params))
            for a in data.get("results", []):
                stats = a.get("summary_stats") or {}
                out.append(
                    {
                        "id": short_id(a.get("id")),
                        "display_name": a.get("display_name"),
                        "cited_by_count": a.get("cited_by_count"),
                        "h_index": stats.get("h_index"),
                        "works_count": a.get("works_count"),
                    }
                )
        return out

    async def enrich_institutions(self, institution_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk in _chunks(list(dict.fromkeys(institution_ids)), OR_BATCH):
            params = {
                "filter": f"openalex_id:{'|'.join(chunk)}",
                "per-page": len(chunk),
                "select": INSTITUTION_SELECT,
            }
            data = await self._get("/institutions", self._params(params))
            for i in data.get("results", []):
                out.append(
                    {
                        "id": short_id(i.get("id")),
                        "display_name": i.get("display_name"),
                        "country_code": i.get("country_code"),
                        "type": i.get("type"),
                        "cited_by_count": i.get("cited_by_count"),
                        "works_count": i.get("works_count"),
                    }
                )
        return out


def normalize_work(w: dict[str, Any]) -> dict[str, Any]:
    primary_topic = w.get("primary_topic") or {}
    subfield = primary_topic.get("subfield") or {}
    field_ = primary_topic.get("field") or {}
    location = w.get("primary_location") or {}
    source = location.get("source") or {}
    topics = w.get("topics") or []

    authorships: list[dict[str, Any]] = []
    institution_details: dict[str, dict[str, Any]] = {}
    for position, authorship in enumerate(w.get("authorships") or []):
        author = authorship.get("author") or {}
        author_id = short_id(author.get("id"))
        inst_ids: list[str] = []
        for inst in authorship.get("institutions") or []:
            iid = short_id(inst.get("id"))
            if not iid:
                continue
            inst_ids.append(iid)
            institution_details[iid] = {
                "id": iid,
                "display_name": inst.get("display_name"),
                "country_code": inst.get("country_code"),
                "type": inst.get("type"),
            }
        authorships.append(
            {
                "author_id": author_id,
                "display_name": author.get("display_name"),
                "position": position,
                "is_corresponding": bool(authorship.get("is_corresponding")),
                "institution_ids": inst_ids,
            }
        )

    return {
        "id": short_id(w.get("id")),
        "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None,
        "title": w.get("title") or w.get("display_name"),
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "year": w.get("publication_year"),
        "publication_date": w.get("publication_date"),
        "type": w.get("type"),
        "cited_by_count": w.get("cited_by_count") or 0,
        "fwci": w.get("fwci"),
        "subfield_id": short_id(subfield.get("id")),
        "field_id": short_id(field_.get("id")),
        "topic_ids": [short_id(t.get("id")) for t in topics if t.get("id")],
        "venue_id": short_id(source.get("id")),
        "venue_name": source.get("display_name"),
        "referenced_works": [short_id(r) for r in (w.get("referenced_works") or [])],
        "is_retracted": bool(w.get("is_retracted")),
        "relevance_score": w.get("relevance_score"),
        "authorships": authorships,
        "institution_details": list(institution_details.values()),
    }


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
