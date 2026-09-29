"""Question cards (lettered options) and the routine card an agent can hand over."""

from __future__ import annotations

import asyncio
import json

import pytest

from opendot.tools import EXEC, TOOLS, Ctx, routine_cron


def _ctx():
    return Ctx(agent={"id": "ag_x", "name": "Trip Buddy"}, thread_id="th_x", run_id="r")


def _dm(client, auth_headers):
    dot = client.get("/api/agents", headers=auth_headers).json()[0]
    tid = next(t["id"] for t in client.get("/api/threads", headers=auth_headers).json()
               if dot["id"] in t["members"])
    return dot, tid


def test_schema_offers_question_form():
    p = TOOLS["offer_choices"]["function"]["parameters"]
    assert {"options", "question", "details", "multi"} <= set(p["properties"])
    assert p["required"] == ["options"]
    assert "Not for yes/no" in TOOLS["offer_choices"]["function"]["description"]
    r = TOOLS["suggest_routine"]["function"]["parameters"]
    assert r["properties"]["every"]["enum"] == ["day", "weekdays", "weekly", "monthly"]


def test_plain_chips_unchanged():
    ctx = _ctx()
    out = asyncio.run(EXEC["offer_choices"](ctx, options=["Yes, go ahead", " ", "Not now"]))
    assert out["ok"] and ctx.extra["choices"] == ["Yes, go ahead", "Not now"]


def test_question_card_letters_details_and_limits():
    ctx = _ctx()
    out = asyncio.run(EXEC["offer_choices"](
        ctx, question="Where first?", multi=True,
        options=["Porto", {"label": "Braga", "detail": "1h by train"}, "Aveiro",
                 "d", "e", "f", "g"],
        details=["walkable, great food"]))
    assert out["letters"]["A"] == "Porto"
    card = ctx.extra["choices"][0]
    assert card["card"] == "question" and card["multi"] is True
    assert [o["key"] for o in card["options"]] == list("ABCDEF")  # capped at 6
    assert card["options"][0]["detail"] == "walkable, great food"
    assert card["options"][1] == {"key": "B", "label": "Braga", "detail": "1h by train"}
    one = _ctx()
    assert "error" in asyncio.run(EXEC["offer_choices"](one, options=["only"], question="Pick?"))
    assert "error" in asyncio.run(EXEC["offer_choices"](one, options=[]))


def test_question_card_persisted_on_message(client, fake_llm, auth_headers):
    from opendot.runtime import run_agent
    dot, tid = _dm(client, auth_headers)
    fake_llm.push(tool_calls=[{"id": "c1", "name": "offer_choices", "arguments": json.dumps({
        "question": "Which weekend works?", "options": ["Nov 6–8", "Nov 13–15", "Nov 20–22"],
        "details": ["cheapest flights", "", "warmest forecast"]})}])
    fake_llm.push(content="Found three weekends that work.")
    msg = asyncio.run(run_agent(dot["id"], tid))
    card = msg["meta"]["choices"][0]
    assert card["question"] == "Which weekend works?" and card["multi"] is False
    assert [o["label"] for o in card["options"]] == ["Nov 6–8", "Nov 13–15", "Nov 20–22"]
    listed = client.get("/api/threads", headers=auth_headers).json()
    assert next(t for t in listed if t["id"] == tid)["needs_you"] is True
    stored = client.get(f"/api/threads/{tid}/messages", headers=auth_headers).json()
    stored = stored["messages"] if isinstance(stored, dict) else stored
    assert stored[-1]["meta"]["choices"][0]["options"][2]["detail"] == "warmest forecast"


@pytest.mark.parametrize("every,kw,cron", [
    ("day", {"time": "10:00"}, "0 10 * * *"),
    ("weekdays", {"time": "7:30"}, "30 7 * * 1-5"),
    ("weekly", {"time": "18:05", "weekday": "Friday"}, "5 18 * * 5"),
    ("monthly", {"time": "09:00", "day": 31}, "0 9 28 * *"),
])
def test_routine_cron(every, kw, cron):
    assert routine_cron(every, **kw) == cron


def test_routine_cron_rejects_nonsense():
    for bad in [("day", {"time": "25:00"}), ("hourly", {}), ("weekly", {"weekday": "xyz"})]:
        with pytest.raises(ValueError):
            routine_cron(bad[0], **bad[1])


def test_routine_card_persisted_and_creates_nothing(client, fake_llm, auth_headers):
    from opendot.runtime import run_agent
    dot, tid = _dm(client, auth_headers)
    before = len(client.get("/api/automations", headers=auth_headers).json())
    fake_llm.push(tool_calls=[{"id": "c1", "name": "suggest_routine", "arguments": json.dumps({
        "prompt": "Check the pottery class list for a free Saturday spot", "every": "weekly",
        "weekday": "thu", "time": "08:00"})}])
    fake_llm.push(content="Want me to check every Thursday morning?")
    msg = asyncio.run(run_agent(dot["id"], tid))
    card = msg["meta"]["choices"][0]
    assert card["card"] == "routine" and card["cron"] == "0 8 * * 4"
    assert card["agent_id"] == dot["id"] and card["time"] == "08:00"
    assert len(client.get("/api/automations", headers=auth_headers).json()) == before
    # the card's Create button posts to the normal automations API
    a = client.post("/api/automations", headers=auth_headers, json={
        "agent_id": dot["id"], "kind": "cron", "schedule": card["cron"], "name": card["name"],
        "prompt": card["prompt"], "thread_id": tid}).json()
    assert a["next_run"] and a["thread_id"] == tid


def test_suggest_routine_needs_no_ok():
    from opendot.gatekeeper import DEFAULT_POLICY
    assert DEFAULT_POLICY["suggest_routine"] == "allow"
    ctx = _ctx()
    out = asyncio.run(EXEC["suggest_routine"](ctx, prompt="x", every="day", time="nope"))
    assert "error" in out
    assert "choices" not in ctx.extra
