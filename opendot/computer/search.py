"""A search chain that (almost) never says "no results".

Providers are tried in order, each optional / auto-skipped if unconfigured:

  1. SearXNG   (``DOT_SEARXNG_URL``)      — self-hosted metasearch, JSON API
  2. Tavily    (``TAVILY_API_KEY``)       — LLM-oriented search API
  3. Brave     (``BRAVE_API_KEY``)        — Brave Search API
  4. ddgs, rotating backends + retry/backoff + a small on-disk cache
  5. in-browser search (last resort)      — open a results page in the agent's
     *visible* browser tab and scrape it; supplied by the caller as a coroutine
     so this module doesn't depend on Playwright.

Measured on 30 EN+中文 queries before this change: ddgs-only, ~41% failure
("No results found" — rate limiting). See docs/ws/W7.md for after numbers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .. import gatekeeper

log = logging.getLogger("opendot.computer.search")

DDGS_BACKENDS = ["duckduckgo", "bing", "brave", "mojeek", "yahoo", "startpage"]
CACHE_TTL = 900  # 15 min


def _cache_path(cache_dir: Path, query: str, n: int) -> Path:
    h = hashlib.sha1(f"{query}|{n}".encode()).hexdigest()[:20]
    return cache_dir / f"{h}.json"


def _cache_get(cache_dir: Path, query: str, n: int) -> list[dict] | None:
    p = _cache_path(cache_dir, query, n)
    if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _cache_set(cache_dir: Path, query: str, n: int, rows: list[dict]) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(cache_dir, query, n).write_text(json.dumps(rows))
    except Exception:
        pass


async def _try_searxng(query: str, n: int) -> list[dict] | None:
    import os
    url = os.environ.get("DOT_SEARXNG_URL")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(url.rstrip("/") + "/search",
                            params={"q": query, "format": "json", "language": "auto"})
        r.raise_for_status()
        data = r.json()
        rows = [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")}
                for x in data.get("results", [])[:n]]
        return rows or None
    except Exception as e:
        log.debug("searxng failed: %s", e)
        return None


async def _try_tavily(query: str, n: int) -> list[dict] | None:
    key = gatekeeper.vault_all().get("TAVILY_API_KEY")
    import os
    key = key or os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://api.tavily.com/search",
                             json={"api_key": key, "query": query, "max_results": n})
        r.raise_for_status()
        data = r.json()
        rows = [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")}
                for x in data.get("results", [])[:n]]
        return rows or None
    except Exception as e:
        log.debug("tavily failed: %s", e)
        return None


async def _try_brave(query: str, n: int) -> list[dict] | None:
    key = gatekeeper.vault_all().get("BRAVE_API_KEY")
    import os
    key = key or os.environ.get("BRAVE_API_KEY")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get("https://api.search.brave.com/res/v1/web/search",
                            params={"q": query, "count": n},
                            headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        rows = [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("description")}
                for x in data.get("web", {}).get("results", [])[:n]]
        return rows or None
    except Exception as e:
        log.debug("brave failed: %s", e)
        return None


async def _try_ddgs(query: str, n: int) -> list[dict] | None:
    def _run(backend: str):
        from ddgs import DDGS
        return list(DDGS().text(query, backend=backend, max_results=n))

    for backend in DDGS_BACKENDS:
        for attempt in range(2):
            try:
                rows = await asyncio.to_thread(_run, backend)
                if rows:
                    return [{"title": r.get("title"), "url": r.get("href"),
                            "snippet": r.get("body")} for r in rows]
            except Exception as e:
                log.debug("ddgs backend=%s attempt=%s failed: %s", backend, attempt, e)
                await asyncio.sleep(0.6 * (attempt + 1))
    return None


async def search_chain(query: str, n: int, cache_dir: Path,
                       browser_search: Callable[[str, int], Awaitable[list[dict] | None]] | None
                       = None) -> tuple[list[dict], str]:
    """Return (results, provider_used)."""
    cached = _cache_get(cache_dir, query, n)
    if cached:
        return cached, "cache"
    for name, fn in (("searxng", _try_searxng), ("tavily", _try_tavily),
                     ("brave", _try_brave), ("ddgs", _try_ddgs)):
        try:
            rows = await fn(query, n)
        except Exception as e:
            log.debug("%s errored: %s", name, e)
            rows = None
        if rows:
            _cache_set(cache_dir, query, n, rows)
            return rows, name
    if browser_search:
        try:
            rows = await browser_search(query, n)
        except Exception as e:
            log.debug("browser search errored: %s", e)
            rows = None
        if rows:
            _cache_set(cache_dir, query, n, rows)
            return rows, "browser"
    return [], "none"
