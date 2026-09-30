"""Settings → Usage: each model reply is counted for the agent and kind of work it
was for; the page adds it up by day, agent, model and kind."""

from __future__ import annotations

import time

from opendot import usage
from opendot.db import db, new_id
from opendot.llm import LLM


class U:  # a provider's usage object
    def __init__(self, i, o, cached=0):
        self.prompt_tokens, self.completion_tokens = i, o
        self.prompt_tokens_details = {"cached_tokens": cached}


def test_usage_is_counted_and_added_up(client, auth_headers):
    db.conn.execute("DELETE FROM usage")
    db.conn.commit()
    ag = db.insert("agents", id=new_id("ag_"), name="Ledger", emoji="x", color="#fff", role="r")
    priced = LLM(model="deepseek/deepseek-chat", base_url="")
    own = LLM(model="my-model", base_url="http://localhost:9/v1")  # not on any price list
    with usage.tagged(agent_id=ag["id"], thread_id="th_x", kind="chat"):
        priced._count(U(1000, 200, cached=600))
        with usage.tagged(agent_id=ag["id"] + "-w1"):  # a helper: same kind of work
            own._count(U(500, 50))
    with usage.tagged(agent_id=ag["id"], kind=usage.kind_of("automation:Morning brief")):
        priced._count(U(300, 30))
    LLM(model="deepseek/deepseek-chat", record=False)._count(U(9, 9))  # the setup test
    priced._count(None)

    rows = db.q("SELECT * FROM usage ORDER BY created")
    assert len(rows) == 3
    assert rows[0]["cached"] == 600 and rows[0]["cost"] > 0 and rows[0]["kind"] == "chat"
    assert rows[1]["cost"] is None and rows[1]["kind"] == "chat"  # inherited from the lead
    assert rows[2]["kind"] == "routine"

    r = client.get("/api/usage?range=today", headers=auth_headers).json()
    t = r["totals"]
    assert (t["calls"], t["input"], t["output"], t["cached"], t["unpriced"]) == (3, 1800, 280, 600, 1)
    assert r["unit"] == "hour" and len(r["buckets"]) == 24
    hour = time.localtime().tm_hour
    assert r["buckets"][hour]["input"] == 1800
    a = r["agents"][0]
    assert a["agent_id"] == ag["id"] and a["calls"] == 3  # the helper counts for its lead
    assert {m["model"] for m in r["models"]} == {"deepseek/deepseek-chat", "my-model"}
    assert {k["kind"]: k["calls"] for k in r["kinds"]} == {"chat": 2, "routine": 1}
    assert r["prev"]["calls"] == 0
    for rng, n in (("7d", 7), ("30d", 30)):
        w = client.get(f"/api/usage?range={rng}", headers=auth_headers).json()
        assert len(w["buckets"]) == n and w["buckets"][-1]["input"] == 1800
    assert client.get("/api/usage?range=all", headers=auth_headers).json()["totals"]["calls"] == 3
