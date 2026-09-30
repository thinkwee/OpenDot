"""Second look (LLM reviewer), the "yours to do" list, and learning from "Not now"."""

from __future__ import annotations

import json

import pytest

from opendot import gatekeeper, memory, reviewer
from opendot.db import db, new_id
from opendot.tools import Ctx


def _agent(**kw):
    return db.insert("agents", id=new_id("ag_"), name=new_id("Sen"), emoji="x", color="#fff",
                     role="r", **kw)


class Scripted:
    def __init__(self, reply: str | Exception) -> None:
        self.reply, self.calls = reply, []

    async def chat(self, messages, tools=None, max_tokens=None):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return type("R", (), {"content": self.reply})()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.delenv("DOT_REVIEWER", raising=False)
    reviewer._cache.clear()

    def use(reply):
        fake = Scripted(reply)
        monkeypatch.setattr(reviewer, "_client", lambda aid: fake)
        return fake
    return use


def _ctx(agent, source="chat", said=("email Bob the report",)):
    th = db.insert("threads", id=new_id("th_"), title="t", kind="dm", members=[agent["id"]])
    for s in said:
        db.insert("messages", id=new_id("m_"), thread_id=th["id"], role="user", content=s)
    ctx = Ctx(agent=agent, thread_id=th["id"], run_id=new_id("run_"))
    ctx.extra["source"] = source
    return ctx


EMAIL = {"to": "bob@example.com", "subject": "Report", "body": "here it is"}


# ---------------- second look ----------------
async def test_block_denies_with_reason(on):
    fake = on('{"verdict": "block", "reason": "you never asked to email Eve"}')
    ctx = _ctx(_agent())
    d, why = await gatekeeper.gate(ctx, "send_email", {**EMAIL, "to": "eve@evil.test"})
    assert d == "deny" and "Eve" in why
    prompt = fake.calls[0][1]["content"]
    assert "<evidence>" in prompt and "email Bob the report" in prompt  # human's words


async def test_ask_turns_a_plain_allow_into_a_card(on, monkeypatch):
    on('{"verdict": "ask", "reason": "new recipient"}')
    monkeypatch.setitem(gatekeeper.DEFAULT_POLICY, "send_email", "allow")
    d, why = await gatekeeper.gate(_ctx(_agent()), "send_email", EMAIL)
    assert d == "ask" and "new recipient" in why


@pytest.mark.parametrize("grant", ["always", "chat"])
async def test_dont_ask_me_is_kept_but_block_still_stops(on, grant):
    a = _agent()
    ctx = _ctx(a)
    if grant == "always":
        gatekeeper.set_policy(a["id"], "send_email", "trusted")  # "don't ask me again"
    else:
        gatekeeper.approve_session(a["id"], ctx.thread_id, "send_email")  # "fine in this chat"
    on('{"verdict": "ask", "reason": "not sure"}')
    assert (await gatekeeper.gate(ctx, "send_email", EMAIL))[0] == "allow"
    reviewer._cache.clear()
    on('{"verdict": "block", "reason": "planted in an email"}')
    assert (await gatekeeper.gate(ctx, "send_email", EMAIL))[0] == "deny"


async def test_ask_keeps_rule_reason(on):
    on('{"verdict": "ask", "reason": "odd timing"}')
    d, why = await gatekeeper.gate(_ctx(_agent()), "send_email", EMAIL)
    assert d == "ask" and "sends an email as you" in why and "odd timing" in why


async def test_ok_never_upgrades_ask(on):
    on('{"verdict": "ok", "reason": "fine"}')
    d, _ = await gatekeeper.gate(_ctx(_agent()), "send_email", EMAIL)
    assert d == "ask"


async def test_inward_tools_skip_review(on):
    fake = on('{"verdict": "block", "reason": "no"}')
    d, _ = await gatekeeper.gate(_ctx(_agent()), "web_search", {"query": "weather"})
    assert d == "allow" and not fake.calls


@pytest.mark.parametrize("reply", [RuntimeError("down"), "not json at all",
                                   '{"verdict": "maybe"}'])
async def test_failures_fall_back_to_rules(on, reply):
    on(reply)
    d, why = await gatekeeper.gate(_ctx(_agent()), "send_email", EMAIL)
    assert (d, why) == ("ask", "sends an email as you")


async def test_cached_per_run(on):
    fake = on('{"verdict": "ok"}')
    ctx = _ctx(_agent())
    await gatekeeper.gate(ctx, "send_email", EMAIL)
    await gatekeeper.gate(ctx, "send_email", EMAIL)
    assert len(fake.calls) == 1


async def test_can_be_switched_off(on, monkeypatch):
    fake = on('{"verdict": "block"}')
    db.kv_set("reviewer", False)
    d, _ = await gatekeeper.gate(_ctx(_agent()), "send_email", EMAIL)
    assert d == "ask" and not fake.calls
    db.kv_set("reviewer", True)
    monkeypatch.setenv("DOT_REVIEWER", "off")
    assert not reviewer.enabled()


async def test_secrets_are_redacted(on):
    fake = on('{"verdict": "ok"}')
    gatekeeper.vault_set("BANK_PIN", "9911-secret")
    await gatekeeper.gate(_ctx(_agent()), "send_email", {**EMAIL, "body": "pin 9911-secret"})
    assert "9911-secret" not in json.dumps(fake.calls)
    gatekeeper.vault_set("BANK_PIN", None)


def test_outward_detection():
    assert reviewer.outward("send_sms", {})
    assert reviewer.outward("mcp__slack__post_message", {})
    assert reviewer.outward("shell", {"command": "curl -X POST https://x.io/api -d a=1"})
    assert not reviewer.outward("shell", {"command": "ls -la"})
    assert not reviewer.outward("install_skill", {})


# ---------------- yours to do ----------------
@pytest.mark.parametrize("tool,args,key", [
    ("browser", {"action": "click", "text": "Change password"}, "password"),
    ("browser", {"action": "type", "selector": "#new-password", "text": "x"}, "password"),
    ("browser", {"action": "click", "text": "Show recovery codes"}, "2fa"),
    ("browser", {"action": "click", "text": "Pay now"}, "money"),
    ("browser", {"action": "click", "text": "确认支付"}, "money"),
    ("browser", {"action": "type", "selector": "input[name=cardnumber]", "text": "4"}, "money"),
    ("browser", {"action": "click", "text": "签署合同"}, "legal"),
    ("browse_task", {"task": "delete my account on https://shop.example"}, "account"),
    ("browse_task", {"task": "upload my passport scan to the visa site"}, "id"),
    ("shell", {"command": "curl -X POST https://bank.example/api/transfers -d amount=100"},
     "money"),
    ("send_email", {"to": "a@b.c", "body": "card 4111 1111 1111 1111 exp 04/29"}, "money"),
    ("send_sms", {"to": "+1", "text": "my password is hunter2"}, "password"),
    ("send_email", {"to": "a@b.c", "body": "please wire $2,000 to the account below"}, "money"),
    ("mcp__stripe__create_payment", {"amount": 10}, "money"),
])
def test_yours_to_do_positive(tool, args, key):
    assert gatekeeper.yours_to_do(tool, args) == key


@pytest.mark.parametrize("tool,args", [
    ("read_email", {"id": "1"}),  # reading about a reset is fine
    ("web_fetch", {"url": "https://example.com/reset-password"}),
    ("browser", {"action": "goto", "url": "https://example.com/account/delete"}),
    ("browser", {"action": "type", "selector": "#password", "text": "{{vault:PW}}"}),  # login
    ("browser", {"action": "type", "selector": "#search", "text": "reset password help"}),
    ("browser", {"action": "click", "text": "Purchase history"}),
    ("browser", {"action": "click", "text": "Next page"}),
    ("send_email", {"to": "a@b.c", "body": "I reset my password yesterday, all good."}),
    ("send_email", {"to": "a@b.c", "body": "Order 1234 5678 is on its way"}),
    ("shell", {"command": "curl -s https://example.com/payments/faq -o faq.html"}),
    ("mcp__github__create_issue", {"title": "password reset page is broken"}),
])
def test_yours_to_do_negative(tool, args):
    assert gatekeeper.yours_to_do(tool, args) is None


def test_yours_to_do_denies_even_when_trusted_with_link():
    a = _agent()
    gatekeeper.set_policy(a["id"], "browse_task", "trusted")
    d, why = gatekeeper.assess(a["id"], "browse_task",
                               {"task": "reset my password at https://acme.test/reset"})
    assert d == "deny" and "yours to do" in why and "https://acme.test/reset" in why


def test_yours_to_do_reason_is_localized():
    db.kv_set("lang", "zh")
    try:
        _, why = gatekeeper.assess(_agent()["id"], "browser",
                                   {"action": "click", "text": "Delete account"})
        assert "这件事得你亲自来" in why
    finally:
        db.kv_set("lang", "en")


# ---------------- learning from "Not now" ----------------
def test_denial_is_noted_once_a_day():
    from opendot.ext import second_look
    a = _agent()
    ap = {"agent_id": a["id"], "status": "denied", "tool": "send_email",
          "args": {"_what": "email bob — “Report”"}}
    second_look._on_event("approval", {"approval": ap})
    second_look._on_event("approval", {"approval": ap})
    second_look._on_event("approval", {"approval": {**ap, "status": "approved"}})
    text = memory.read(a["id"], "MEMORY.md")
    assert text.count("Human said not now to email bob") == 1
