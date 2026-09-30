"""One responsibility per agent, its own identity, watch-and-act, one-tap answers."""

from __future__ import annotations

import asyncio
import json
import time

from opendot.db import db


def _spec(**kw):
    base = {"name": "Hilltop Climbing", "emoji": "🧗", "avatar": "panda",
            "role": "books your climbing sessions", "responsibility": "Book sessions.",
            "context": "Open 7-22", "boundary": "", "greeting": "Hi! I'm Hilltop Climbing."}
    return json.dumps({**base, **kw})


def test_default_install_is_one_front_desk(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()
    assert [a["name"] for a in agents] == ["Pip"]
    assert agents[0]["origin"] == "default"


def test_hire_from_sentence(client, fake_llm, auth_headers):
    fake_llm.push(content="```json\n" + _spec(name="Paper Keeper", avatar="owl") + "\n```")
    r = client.post("/api/agents/hire", json={"text": "keep track of our passports"},
                    headers=auth_headers).json()
    a = r["agent"]
    assert a["name"] == "Paper Keeper" and a["avatar"] == "owl" and a["origin"] == "manual"
    msgs = client.get(f"/api/threads/{r['thread']['id']}/messages",
                      headers=auth_headers).json()["messages"]
    assert msgs[0]["role"] == "user" and "passports" in msgs[0]["content"]  # it starts on it


def test_hire_from_link(client, fake_llm, auth_headers, monkeypatch):
    from opendot.ext import hire

    async def page(url):
        return "Hilltop Climbing — book a session online. Open 7-22."
    monkeypatch.setattr(hire, "_read_page", page)
    fake_llm.push(content=_spec())
    r = client.post("/api/agents/hire", json={"url": "https://hilltop.example/book",
                                              "origin": "qr"}, headers=auth_headers).json()
    assert r["agent"]["origin"] == "qr"
    assert r["agent"]["origin_url"] == "https://hilltop.example/book"
    assert "Hilltop Climbing" in fake_llm.calls[-1]["messages"][1]["content"]


def test_hire_template_and_name_clash(client, auth_headers):
    a1 = client.post("/api/agents/hire", json={"template": "travel"}, headers=auth_headers)
    a2 = client.post("/api/agents/hire", json={"template": "travel"}, headers=auth_headers)
    assert a1.json()["agent"]["name"] == "Trip Buddy"
    assert a2.json()["agent"]["name"] == "Trip Buddy 2"


def test_hire_from_qr_picture(client, fake_llm, auth_headers, monkeypatch, tmp_path):
    import qrcode

    from opendot.ext import hire

    async def page(url):
        return "Timetable"
    monkeypatch.setattr(hire, "_read_page", page)
    p = tmp_path / "poster.png"
    qrcode.make("https://hilltop.example/timetable").save(p)
    up = client.post("/api/uploads", files={"file": ("poster.png", p.read_bytes(), "image/png")},
                     headers=auth_headers).json()
    fake_llm.push(content=_spec())
    r = client.post("/api/agents/hire", json={"upload_id": up["id"]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["origin_url"] == "https://hilltop.example/timetable"


def test_prompt_carries_the_job(client):
    from opendot.runtime import make_agent, system_prompt
    a, t = make_agent(name="Spot Spotter", responsibility="Get a spot in the Saturday pottery class.",
                      boundary="Before signing me up.")
    p = system_prompt(a, t, "chat")
    assert "Your responsibility: Get a spot in the Saturday pottery class." in p
    assert "Come back to the human when: Before signing me up." in p


def test_offer_choices_end_up_on_the_message(client, fake_llm, auth_headers):
    from opendot.runtime import run_agent
    dot = client.get("/api/agents", headers=auth_headers).json()[0]
    tid = next(t["id"] for t in client.get("/api/threads", headers=auth_headers).json() if dot["id"] in t["members"])
    fake_llm.push(tool_calls=[{"id": "c1", "name": "offer_choices",
                               "arguments": json.dumps({"options": ["Yes, go ahead",
                                                                    "Not now"]})}])
    fake_llm.push(content="Found a spot on Saturday at 10 ✓")
    msg = asyncio.run(run_agent(dot["id"], tid))
    assert msg["meta"]["choices"] == ["Yes, go ahead", "Not now"]


def test_watch_checks_then_closes(client, fake_llm):
    from opendot.ext import watch
    from opendot.runtime import make_agent, run_agent
    from opendot.tools import Ctx
    a, t = make_agent(name="Deal Watcher")
    ctx = Ctx(agent=a, thread_id=t["id"], run_id="r")
    w = asyncio.run(watch._watch(ctx, "Rice cooker", "Rice cooker under $90", "Send me the link",
                                 every_minutes=1))
    assert w["ok"]
    row = db.one("SELECT * FROM automations WHERE id=?", w["id"])
    assert row["kind"] == "watch" and row["every_min"] == watch.MIN_EVERY

    started = []
    import opendot.runtime as rt
    orig = rt.spawn
    rt.spawn = lambda coro: (started.append(coro), coro.close())
    try:
        assert asyncio.run(watch.tick(time.time() + 10)) == 1
    finally:
        rt.spawn = orig
    row = db.one("SELECT * FROM automations WHERE id=?", w["id"])
    assert row["checks"] == 1 and row["next_run"] > time.time()

    # a quiet check leaves nothing behind
    fake_llm.push(content="NO_REPLY")
    assert asyncio.run(run_agent(a["id"], t["id"], "watch:Rice cooker", "check")) is None

    asyncio.run(watch._watch_done(ctx, w["id"], "dropped to $99"))
    row = db.one("SELECT * FROM automations WHERE id=?", w["id"])
    assert row["enabled"] == 0 and row["outcome"] == "dropped to $99"


def test_watch_expires(client):
    from opendot.ext import watch
    from opendot.runtime import make_agent
    from opendot.tools import Ctx
    a, t = make_agent(name="Spot Spotter")
    w = asyncio.run(watch._watch(Ctx(agent=a, thread_id=t["id"], run_id="r"), "Court",
                                 "A free tennis court on Sunday", "Hold it", for_days=1))
    import opendot.runtime as rt
    orig = rt.spawn
    rt.spawn = lambda coro: coro.close()
    try:
        asyncio.run(watch.tick(time.time() + 2 * 86400))
    finally:
        rt.spawn = orig
    assert db.one("SELECT enabled FROM automations WHERE id=?", w["id"])["enabled"] == 0


def test_every_agent_gets_its_own_address(client, auth_headers):
    from opendot.ext import email as em
    from opendot.runtime import make_agent
    client.put("/api/identity/mailbox", json={"address": "myagents@gmail.com",
                                              "style": "plus"}, headers=auth_headers)
    a, _ = make_agent(name="Trip Buddy")
    assert em.get_config(a["id"])["address"] == "myagents+trip-buddy@gmail.com"
    assert em.route(["myagents+trip-buddy@gmail.com"]) == a["id"]
    dot = db.one("SELECT id FROM agents WHERE origin='default'")
    assert em.route(["myagents@gmail.com"]) == dot["id"]  # bare mailbox → front desk
    client.put("/api/identity/mailbox", json={"address": "x@agents.example.com",
                                              "style": "domain",
                                              "domain": "agents.example.com"},
               headers=auth_headers)
    addrs = client.get("/api/identity/addresses", headers=auth_headers).json()
    assert addrs[a["id"]] == "trip-buddy@agents.example.com"


def test_let_an_agent_go(client, auth_headers):
    from opendot.runtime import make_agent
    a, t = make_agent(name="Weekend Pal")
    assert client.delete(f"/api/agents/{a['id']}", headers=auth_headers).json()["ok"]
    assert not db.one("SELECT id FROM threads WHERE id=?", t["id"])
    dot = client.get("/api/agents", headers=auth_headers).json()[0]
    assert client.delete(f"/api/agents/{dot['id']}", headers=auth_headers).status_code == 400


def test_approval_says_what_it_wants(client):
    from opendot.gatekeeper import say
    assert say("send_email", {"to": "hotel@x.com", "subject": "Bike storage"}) == \
        "email hotel@x.com — “Bike storage”"


def test_approval_card_carries_friendly_words(client):
    from opendot import gatekeeper
    from opendot.runtime import make_agent
    a, t = make_agent(name="Nest Keeper")

    async def go():
        task = asyncio.create_task(gatekeeper.request_approval(
            a["id"], t["id"], "send_email", {"to": "windows@x.com", "subject": "Quote"}, ""))
        await asyncio.sleep(0.05)
        ap = db.one("SELECT * FROM approvals WHERE agent_id=?", a["id"])
        gatekeeper.decide(ap["id"], True)
        assert await task
        return ap
    ap = asyncio.run(go())
    assert ap["args"]["_what"] == "email windows@x.com — “Quote”"
    item = db.one("SELECT title FROM inbox WHERE ref=?", ap["id"])
    assert item["title"] == "Nest Keeper wants to email windows@x.com — “Quote”"


def test_answer_written_next_to_choices_is_kept(client, fake_llm, auth_headers):
    from opendot.runtime import run_agent
    dot = client.get("/api/agents", headers=auth_headers).json()[0]
    tid = next(t["id"] for t in client.get("/api/threads", headers=auth_headers).json()
               if dot["id"] in t["members"])
    fake_llm.push(content="Best pick: the Turner show at Tate Britain, 2pm.",
                  tool_calls=[{"id": "c1", "name": "offer_choices",
                               "arguments": json.dumps({"options": ["Go", "Others"]})}])
    fake_llm.push(content="Want me to book?")
    msg = asyncio.run(run_agent(dot["id"], tid))
    assert msg["content"].startswith("Best pick: the Turner show")
    assert msg["content"].endswith("Want me to book?")


def test_server_speaks_the_humans_language(client, auth_headers):
    from opendot.gatekeeper import say
    assert client.put("/api/settings/lang", json={"lang": "zh"}, headers=auth_headers).json() == {"lang": "zh"}
    assert say("send_email", {"to": "hotel@x.com", "subject": "自行车停放"}) == "给 hotel@x.com 发邮件「自行车停放」"
    tpl = client.get("/api/agents/templates", headers=auth_headers).json()
    assert tpl[0]["name"] == "旅行搭子"
    dot = client.get("/api/agents", headers=auth_headers).json()[0]
    assert dot["role"] == "你的前台"
    r = client.post("/api/agents/hire", json={"template": "papers"}, headers=auth_headers).json()
    assert r["agent"]["name"] == "证件管家"
    client.put("/api/settings/lang", json={"lang": "en"}, headers=auth_headers)
    assert client.get("/api/agents", headers=auth_headers).json()[0]["role"] == "your front desk"
    assert client.put("/api/settings/lang", json={"lang": "fr"},
                      headers=auth_headers).status_code == 400


def test_friendly_words_survive_the_api(client, auth_headers):
    # FastAPI's encoder silently drops keys starting with "_sa" (SQLAlchemy internals),
    # which is why the field is called _what
    from opendot.db import new_id
    db.insert("approvals", id=new_id("ap_"), agent_id="a", thread_id="t", tool="send_email",
              args={"to": "x@y.z", "_what": "email x@y.z"}, reason="", status="pending")
    ap = client.get("/api/bootstrap", headers=auth_headers).json()["approvals"][0]
    assert ap["args"]["_what"] == "email x@y.z"


def test_new_agents_get_an_animal_the_team_lacks():
    from opendot.runtime import ANIMALS, make_agent, pick_animal
    make_agent(name="Hoot", avatar="owl")
    for r in db.q("SELECT id FROM agents"):
        db.update("agents", r["id"], avatar="owl")
    assert pick_animal("owl") != "owl"  # already on the team
    assert pick_animal("tiger") == "tiger"  # nobody has it yet
    faces = [make_agent(name=f"Z{i}", avatar="owl")[0]["avatar"] for i in range(len(ANIMALS) - 1)]
    assert sorted(faces) == sorted(a for a in ANIMALS if a != "owl")  # every animal before repeats
