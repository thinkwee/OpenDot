"""The search chain: providers are tried in order and the first non-empty
result set wins; a small on-disk cache short-circuits repeats; when every
configured provider is unavailable it reports that clearly instead of
silently returning nothing."""

from __future__ import annotations

from pathlib import Path

from opendot.computer.search import search_chain


async def test_falls_through_to_a_working_provider(tmp_path: Path):
    calls = []

    async def searxng(q, n):
        calls.append("searxng")
        return None  # not configured / failed

    async def tavily(q, n):
        calls.append("tavily")
        return None

    async def brave(q, n):
        calls.append("brave")
        return [{"title": "hit", "url": "https://x.test", "snippet": "..."}]

    import opendot.computer.search as s
    orig = (s._try_searxng, s._try_tavily, s._try_brave)
    s._try_searxng, s._try_tavily, s._try_brave = searxng, tavily, brave
    try:
        rows, provider = await search_chain("test query", 5, tmp_path)
    finally:
        s._try_searxng, s._try_tavily, s._try_brave = orig
    assert provider == "brave"
    assert rows[0]["url"] == "https://x.test"


async def test_cache_hits_skip_the_network(tmp_path: Path):
    import opendot.computer.search as s
    calls = {"n": 0}

    async def searxng(q, n):
        calls["n"] += 1
        return [{"title": "t", "url": "https://cached.test", "snippet": ""}]

    orig = s._try_searxng
    s._try_searxng = searxng
    try:
        rows1, p1 = await search_chain("cacheable", 5, tmp_path)
        rows2, p2 = await search_chain("cacheable", 5, tmp_path)
    finally:
        s._try_searxng = orig
    assert p1 == "searxng"
    assert p2 == "cache"
    assert calls["n"] == 1


async def test_browser_fallback_used_when_everything_else_fails(tmp_path: Path):
    import opendot.computer.search as s

    async def none_provider(q, n):
        return None

    orig = (s._try_searxng, s._try_tavily, s._try_brave, s._try_ddgs)
    s._try_searxng = s._try_tavily = s._try_brave = s._try_ddgs = none_provider
    try:
        async def browser_search(q, n):
            return [{"title": "scraped", "url": "https://bing.test", "snippet": ""}]
        rows, provider = await search_chain("last resort", 5, tmp_path, browser_search)
    finally:
        s._try_searxng, s._try_tavily, s._try_brave, s._try_ddgs = orig
    assert provider == "browser"
    assert rows[0]["url"] == "https://bing.test"


async def test_reports_failure_when_nothing_works(tmp_path: Path):
    import opendot.computer.search as s

    async def none_provider(q, n):
        return None

    orig = (s._try_searxng, s._try_tavily, s._try_brave, s._try_ddgs)
    s._try_searxng = s._try_tavily = s._try_brave = s._try_ddgs = none_provider
    try:
        rows, provider = await search_chain("nothing works", 5, tmp_path)
    finally:
        s._try_searxng, s._try_tavily, s._try_brave, s._try_ddgs = orig
    assert rows == []
    assert provider == "none"
