"""Making agents — one per responsibility, as cheap as a sentence, a link or a QR code.

    GET  /api/agents/templates        ready-made agents for everyday errands
    POST /api/agents/hire             {text | url | template | upload_id} → {agent, thread}

Plus the ``create_agent`` tool, so the front-desk agent can set up a dedicated agent
when a request turns into an ongoing job ("keep track of our passports from now on").
See docs/SOUL.md.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from fastapi import APIRouter, Body, HTTPException

from ..db import db
from ..runtime import make_agent, run_agent, spawn
from ..tools import Ctx, S, fn, register_tool

log = logging.getLogger("opendot.ext.hire")
router = APIRouter(prefix="/api/agents")

ANIMALS = ["fox", "cat", "bunny", "bear", "frog", "owl", "panda", "raccoon", "duck", "shiba",
           "axolotl", "penguin"]

TEMPLATES = [
    dict(key="travel", name="Trip Buddy", emoji="🧳", avatar="penguin", color="#8EC5FF",
         role="plans your trips",
         responsibility="Plan trips end to end: getting there, where to stay, getting around, "
                        "what to do each day. Keep an eye on fares for trips we've talked about.",
         greeting="Hi! I'm your Trip Buddy 🧳 Where to next, and who's coming along?"),
    dict(key="papers", name="Paper Keeper", emoji="📎", avatar="owl", color="#FFD37A",
         role="remembers when things run out",
         responsibility="Keep track of passports, ID cards, visas, warranties and memberships: "
                        "know when each one runs out, remind well ahead, and gather what the "
                        "renewal needs.",
         greeting="Hey 📎 Tell me what's in your document drawer (a photo works). I'll "
                  "remember when each thing runs out and nudge you well before."),
    dict(key="home", name="Nest Keeper", emoji="🏡", avatar="bear", color="#8FD6B8",
         role="keeps the home's little chores on schedule",
         responsibility="Keep the home's small recurring upkeep on schedule: filter swaps, "
                        "smoke-alarm batteries, descaling the kettle, the range-hood filter. "
                        "Remind at the right time, and find someone when it needs a pro.",
         greeting="Hi 🏡 What in your place needs looking after now and then? Tell me roughly "
                  "when it was last done and I'll keep track."),
    dict(key="slots", name="Spot Spotter", emoji="🎯", avatar="fox", color="#FF9CB8",
         role="catches a spot the moment one frees up",
         responsibility="Keep checking things that are full right now (a class, a court, a "
                        "waiting list) and tell the human the moment a spot frees up; grab it "
                        "straight away if they've said so.",
         greeting="What's full that you wish wasn't? 🎯 A pottery class, a tennis court on "
                  "Sunday morning… I'll keep checking and ping you the moment a spot opens."),
    dict(key="deals", name="Deal Watcher", emoji="🏷️", avatar="raccoon", color="#C8E07E",
         role="watches prices for you",
         responsibility="Track prices of things the human wants (flights, gadgets, hotels) "
                        "and say when they drop below their target.",
         greeting="What should I keep an eye on? 🏷️ Give me the thing and your target price."),
    dict(key="fun", name="Weekend Pal", emoji="🎈", avatar="bunny", color="#B9A6F2",
         role="finds good ways to spend free days",
         responsibility="Find things worth doing on free days (markets, walks, gigs, "
                        "workshops) to fit the human's taste, the weather and who's coming "
                        "along.",
         greeting="Hi! 🎈 Got a free day coming up? Tell me who's coming and what you're in "
                  "the mood for."),
    dict(key="habit", name="Habit Buddy", emoji="🌱", avatar="frog", color="#7ED6E0",
         role="helps a small habit stick",
         responsibility="Help the human keep one small habit going: drinking water, a walk "
                        "after lunch, reading before bed, practising an instrument. Nudge at "
                        "the right moment, keep count, never nag.",
         greeting="Hey 🌱 What's one small thing you'd like to do more often? We'll start "
                  "tiny."),
    dict(key="tomorrow", name="Tomorrow Cat", emoji="🌙", avatar="cat", color="#FFB38A",
         role="tells you about tomorrow, tonight",
         responsibility="Each evening, look at tomorrow: what's on, what to bring, when to "
                        "leave, the weather, and any replies still owed. One short message, "
                        "then quiet.",
         greeting="Evening! 🌙 When do you usually wind down? I'll send you tomorrow in one "
                  "short message around then."),
]


# 中文: name, role, responsibility, greeting
TEMPLATES_ZH = {
    'travel': ('旅行搭子', '帮你规划旅行',
               '从头到尾规划旅行：怎么去、住哪儿、当地怎么走、每天玩什么；聊过的行程会一直帮你盯着票价。',
               '嗨！我是你的旅行搭子 🧳 下一趟去哪儿？都有谁一起？'),
    'papers': ('证件管家', '帮你记着什么时候到期',
               '帮你记着护照、身份证、签证、保修卡和各种会员卡什么时候到期，提前好几周提醒你，再把续办要准备的材料理清楚。',
               '嘿 📎 说说你抽屉里都有哪些证件和保修卡（拍张照也行），我记住它们的到期日，快到了提前叫你。'),
    'home': ('小窝管家', '把家里的定期小事记牢',
             '把家里那些隔一阵就要做的小事排好：换滤芯、换烟雾报警器电池、给水壶除垢、洗油烟机滤网。到点提醒你，需要师傅的话帮你找好人。',
             '嗨 🏡 家里有哪些东西要隔一阵打理一下？告诉我上次大概什么时候弄的，剩下的我来记。'),
    'slots': ('捡漏小狐', '一有空位马上叫你',
              '盯着那些现在满了的东西：课程、球场、候补名单……一空出位置马上告诉你；你提前说过可以的话，就直接帮你占下。',
              '有什么满了、你又特别想要的？🎯 陶艺课、周日早上的网球场……我会一直帮你蹲着，一有空位马上叫你。'),
    'deals': ('降价雷达', '帮你盯价格', '盯着你想买的东西（机票、数码、酒店）的价格，低于目标价就告诉你。',
              '要我盯什么？🏷️ 告诉我东西和你的目标价。'),
    'fun': ('周末玩伴', '帮你安排空闲的日子',
            '按你的口味、天气和同行的人，找空闲日子里值得去的地方：市集、散步路线、小演出、手作课。',
            '嗨！🎈 最近哪天有空？告诉我跟谁一起、想玩点什么。'),
    'habit': ('打卡伙伴', '陪你把小习惯坚持下来',
              '陪你坚持一个小习惯：多喝水、午饭后走走、睡前读几页、练会儿琴。挑对的时候提醒一下，帮你记着天数，绝不唠叨。',
              '嘿 🌱 有什么小事你想做得更勤一点？咱们从很小的一步开始。'),
    'tomorrow': ('明日小猫', '每晚帮你看一眼明天',
                 '每天晚上帮你看一眼明天：有什么安排、要带什么、几点出门、天气怎样、还有谁的消息没回。一条短消息，说完就安静。',
                 '晚上好 🌙 你一般几点开始放松？我就在那会儿用一条短消息把明天告诉你。'),
}


def localized(t: dict) -> dict:
    from .lang import lang
    if lang() != "zh" or t["key"] not in TEMPLATES_ZH:
        return dict(t)
    name, role, resp, greet = TEMPLATES_ZH[t["key"]]
    return {**t, "name": name, "role": role, "responsibility": resp, "greeting": greet}


@router.get("/templates")
async def templates():
    return [localized(t) for t in TEMPLATES]


# ---------------- designing an agent with the LLM ----------------
SPEC_PROMPT = """You set up a small personal agent that takes care of ONE responsibility for
a person. From the input, write its profile as a single JSON object, nothing else:
{"name": short friendly name in the person's language (1-3 words, like "Trip Buddy",
   "Hilltop Climbing", "证件管家", "机票雷达"; a place keeps its own name),
 "emoji": one emoji,
 "avatar": one of %s,
 "role": 3-7 words in the person's language, what it does ("plans your Porto trip"),
 "responsibility": 1-2 sentences in the person's language, the ongoing job,
 "context": useful facts from the input (addresses, hours, menu, dates, constraints) or "",
 "boundary": when it must come back to the person before acting, or "",
 "greeting": its first message to the person, 1-2 short sentences, in the SAME language as
   the person's input (for a web page/QR code: the page's language), warm like a friend
   texting, saying what it can do and asking the one most useful question}
If the input is a shop/restaurant/venue/service page, the agent represents the person's
relationship with that place (e.g. books sessions, asks questions, keeps an eye on their notices)
and is named after it."""


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


async def design(text: str) -> dict:
    """Ask the model for a profile; falls back to a plain one if it can't."""
    from .. import llm as llm_mod
    from .lang import lang
    if lang() == "zh":
        text += ("\n\n(The person uses the app in Chinese: write name, role, responsibility, "
                 "context, boundary and greeting in Chinese; a place may keep its own name.)")
    spec: dict = {}
    try:
        r = await llm_mod.llm.chat([
            {"role": "system", "content": SPEC_PROMPT % ", ".join(ANIMALS)},
            {"role": "user", "content": text[:12000]}], max_tokens=1200)
        spec = _parse_json(r.content)
    except Exception as e:
        log.warning("agent design failed: %s", e)
    if not spec.get("name"):
        words = re.sub(r"https?://\S+", "", text).split()
        spec.setdefault("name", " ".join(words[:2]).title()[:24] or "Helper")
        spec.setdefault("responsibility", text.strip()[:400])
    if spec.get("avatar") not in ANIMALS:
        spec["avatar"] = ""
    return spec


async def _read_page(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20,
                                     headers={"User-Agent": "Mozilla/5.0 OpenDot"}) as c:
            r = await c.get(url)
        html = r.text
        try:
            import trafilatura
            text = trafilatura.extract(html, url=str(r.url)) or ""
        except Exception:
            text = ""
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        head = title.group(1).strip() if title else ""
        return f"{head}\n\n{text or re.sub(r'<[^>]+>', ' ', html)}"[:10000]
    except Exception as e:
        log.info("could not read %s: %s", url, e)
        return ""


def decode_qr(path: str) -> list[str]:
    try:
        import zxingcpp
        from PIL import Image
        with Image.open(path) as im:
            return [r.text for r in zxingcpp.read_barcodes(im) if r.text]
    except Exception as e:
        log.info("qr decode failed for %s: %s", path, e)
        return []


URL_RX = re.compile(r"https?://\S+")


async def hire(text: str = "", url: str = "", template: str = "", origin: str = "",
               first_task: str = "") -> tuple[dict, dict]:
    if template:
        t = next((x for x in TEMPLATES if x["key"] == template), None)
        if not t:
            raise ValueError("no such template")
        spec = {k: v for k, v in localized(t).items() if k != "key"}
        return make_agent(**spec, origin="template")
    if not url:
        m = URL_RX.search(text or "")
        url = m.group(0) if m else ""
    if url:
        page = await _read_page(url)
        prompt = f"Made from {'a QR code' if origin == 'qr' else 'a link'}: {url}\n"
        if text and text.strip() != url:
            prompt += f"The person said: {text}\n"
        prompt += f"\nPage content:\n{page or '(could not load the page)'}"
        spec = await design(prompt)
        origin = origin or "link"
    else:
        if not (text or "").strip():
            raise ValueError("tell me what it should take care of")
        spec = await design(f"The person wants an agent for: {text}")
        origin = origin or "manual"
    # made from a sentence: that sentence is already the first job, so it becomes your
    # first message and they get going; from a place (link/QR) they say hi and ask
    starts = origin == "manual" and not url
    agent, thread = make_agent(
        name=spec.get("name", ""), role=spec.get("role", ""), emoji=spec.get("emoji") or "🌟",
        responsibility=spec.get("responsibility", ""), context=spec.get("context", ""),
        boundary=spec.get("boundary", ""), greeting="" if starts else spec.get("greeting", ""),
        avatar=spec.get("avatar", ""), origin=origin, origin_url=url)
    if starts:
        from ..runtime import on_user_message
        await on_user_message(thread["id"], text.strip())
    elif first_task:
        spawn(run_agent(agent["id"], thread["id"], "chat", prompt=first_task))
    return agent, thread


@router.post("/hire")
async def api_hire(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    origin = body.get("origin") or ""
    if body.get("upload_id"):
        row = db.one("SELECT * FROM uploads WHERE id=?", body["upload_id"])
        if not row:
            raise HTTPException(404, "no such upload")
        from .uploads import _abs
        codes = decode_qr(str(_abs(row["rel"])))
        if not codes:
            raise HTTPException(400, "couldn't find a QR code in that picture")
        url, origin = codes[0], "qr"
        if not URL_RX.match(url):  # a QR code with plain text in it
            body["text"], url = url, ""
    try:
        agent, thread = await hire(text=body.get("text") or "", url=url,
                                   template=body.get("template") or "", origin=origin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"agent": agent, "thread": thread}


# ---------------- tool: the front desk sets up a dedicated agent ----------------
async def _create_agent(ctx: Ctx, responsibility: str, name: str = "", context: str = "",
                        boundary: str = "", first_task: str = "") -> dict:
    if ctx.depth > 0:
        return {"error": "helpers can't make agents"}
    desc = f"{name + ': ' if name else ''}{responsibility}"
    if context:
        desc += f"\nWhat they told me: {context}"
    spec = await design(f"The person wants an agent for: {desc}")
    agent, thread = make_agent(
        name=name or spec.get("name", ""), role=spec.get("role", ""),
        emoji=spec.get("emoji") or "🌟", responsibility=responsibility,
        context="\n".join(x for x in (context, spec.get("context", "")) if x),
        boundary=boundary or spec.get("boundary", ""), greeting=spec.get("greeting", ""),
        avatar=spec.get("avatar", ""), origin="chat")
    if first_task:
        spawn(run_agent(agent["id"], thread["id"], "chat", prompt=first_task))
    return {"ok": True, "name": agent["name"], "agent_id": agent["id"],
            "note": f"{agent['name']} now has its own chat. Tell the human in one line."}


register_tool(
    "create_agent",
    fn("create_agent", "Set up a new dedicated agent for ONE ongoing responsibility (e.g. "
       "'our passports and warranties', 'a spot in the Saturday pottery class', 'the Porto "
       "trip'). Use it when a "
       "request is an ongoing job rather than a one-off, or when the human asks for one. "
       "It gets its own chat, identity and computer. first_task = what it should start on "
       "right away.",
       {"responsibility": S, "name": S, "context": S, "boundary": S, "first_task": S},
       ["responsibility"]),
    _create_agent, policy="allow")


def PROMPT(agent: dict) -> str | None:
    if agent.get("origin") != "default":
        return None
    return ("# Setting up agents\nYou're the front desk. One-off asks: just do them. When "
            "something is an ongoing job — keeping passports up to date, a trip, waiting for "
            "a spot to free up, a place they deal with — offer (with `offer_choices`) to set up a "
            "dedicated agent, then `create_agent`. Don't make one for a quick question.")
