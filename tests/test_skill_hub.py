"""Skill Hub: merging + relevance ranking (network stubbed) and install-spec parsing."""

from __future__ import annotations

import asyncio

from opendot import ext as ext_mod

ext_mod.load_all()
from opendot.ext import skill_hub as H  # noqa: E402
from opendot.ext import skills as S  # noqa: E402


def _item(name, src, desc="", installs=None, stars=None, frm="skills.sh"):
    return {"name": name, "description": desc, "source": src, "install": f"github:{src}#{name}",
            "installs": installs, "stars": stars, "url": "", "from": frm, "raw": []}


def test_search_ranks_relevance_over_popularity(monkeypatch):
    async def sh(c, q, limit):
        return [_item("azure-quotas", "microsoft/azure-skills", installs=475000),
                _item("code-review-excellence", "x/agents", installs=29000),
                _item("excel-automation", "y/skills", installs=15000)]

    async def mp(c, q, limit):
        return [_item("excel-automation", "y/skills", "Automate Excel workbooks", stars=900,
                      frm="skillsmp")]

    async def nodesc(items):
        return None
    monkeypatch.setattr(H, "_skills_sh", sh)
    monkeypatch.setattr(H, "_skillsmp", mp)
    monkeypatch.setattr(H, "_fill_descriptions", nodesc)
    H._cache.clear()
    res = asyncio.run(H.search("excel", 10))
    assert [r["name"] for r in res] == ["excel-automation"]  # "excellence" ≠ "excel"
    assert res[0]["description"] == "Automate Excel workbooks" and res[0]["stars"] == 900


def test_search_survives_a_dead_source(monkeypatch):
    async def boom(c, q, limit):
        raise RuntimeError("down")

    async def mp(c, q, limit):
        return [_item("pdf", "anthropics/skills", "PDF tools", stars=5, frm="skillsmp")]

    async def nodesc(items):
        return None
    monkeypatch.setattr(H, "_skills_sh", boom)
    monkeypatch.setattr(H, "_skillsmp", mp)
    monkeypatch.setattr(H, "_fill_descriptions", nodesc)
    H._cache.clear()
    res = asyncio.run(H.search("pdf", 5))
    assert res and res[0]["official"]


def test_install_spec_patterns():
    m = S.GH_TREE.match("https://github.com/someone/skill-pack/tree/main/skills/nano-pdf")
    assert m and m.groups() == ("someone", "skill-pack", "main", "skills/nano-pdf")
    m = S.GH_SKILL.match("github:anthropics/skills#pdf")
    assert m and m.groups() == ("anthropics", "skills", "pdf")
    assert not S.GH_SKILL.match("github:evil/../x#y")
