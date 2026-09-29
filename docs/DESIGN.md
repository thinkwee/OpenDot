# How OpenDot works

A short tour for people who want to run it seriously, change it, or check that it's safe.

## The shape of it

```
 browser / phone (PWA) ──HTTPS──┐        ┌── Telegram · WeChat · Feishu · Slack · Discord
                                ▼        ▼
          ┌──────────── one FastAPI process, one port (7878) ────────────┐
          │ REST + WebSocket · SQLite · event bus · extension loader     │
          │                                                              │
          │  agents ── tool loop ── Gatekeeper (allow / ask / deny)      │
          │    │                        │                                │
          │    ├─ helpers (parallel)    └─ vault: secrets filled in at   │
          │    └─ handoffs in groups       run time, never shown to the  │
          │                                model                         │
          │  each agent's computer: home folder · terminal · Chromium    │
          └──────────────────────────────────────────────────────────────┘
                  │                              │
          any model via LiteLLM          email (IMAP/SMTP) · phone (Twilio)
```

Everything lives in `data/` next to the code: the SQLite database, each agent's home
folder, the vault and the pairing token. Back it up and you've backed up everything.

## Agents

- **One responsibility per agent.** A new install starts with one front-desk agent, Pip.
  When something you ask turns into an ongoing job ("tell me when…", "every Monday…"),
  Pip sets up a dedicated agent for it, with its own name, face, rules and check-ins.
- **The loop** (`opendot/runtime.py`): an OpenAI-shaped tool loop per agent. One agent
  does one thing at a time; different agents run in parallel. `delegate` fans wide work
  out to short-lived helpers (`<agent>-w1`, `-w2`…), each with its own computer.
  In a group chat, `handoff` passes the baton to another agent.
- **Proactive, but quiet.** Watches (`ext/watch.py`) re-check something on a schedule and
  act the moment it happens; routines (`scheduler.py`) run on cron or on events (webhooks,
  RSS, email, SMS). At most `DOT_DAILY_NUDGES` unprompted messages a day, none between
  `DOT_QUIET_FROM` and `DOT_QUIET_TO`.
- **Memory you can read** (`memory.py`): `SOUL.md` (who the agent is), `USER.md` (what
  it knows about you), `MEMORY.md` (what it learned) and a daily journal, all plain files
  in the agent's folder.
- **Todo and calendar** (`ext/todo.py`, `ext/agenda.py`): a task opens on an agent's
  first real step and closes with the run (done / waiting on you / stopped / failed);
  agents keep a checklist with the `todo` tool. The agenda merges your calendar links
  with the times routines fire and watches check.

## Models

`opendot/llm.py` talks to every model through [LiteLLM](https://docs.litellm.ai/docs/providers),
an open-source library that calls each provider's own official API (OpenAI, Anthropic,
Gemini, DeepSeek, xAI, Qwen, Kimi, GLM, OpenRouter, Ollama, Bedrock, Azure and 100+ more)
through one interface, with tool calling and streaming. Nothing goes through a middleman.

`./dot.sh setup` asks which provider you use, takes your key, and shows the models that
provider offers **right now** (`opendot/models.py` asks its `/models` endpoint), so new
models appear without an OpenDot update. It makes a test call before saving anything.
Settings → Model profiles does the same in the browser, and lets you give different
agents different models, e.g. a local one for private jobs.

What it writes to `.env`:

```bash
LLM_MODEL=deepseek/<model>            # provider/model, as LiteLLM names it
LLM_API_KEY=...                       # or the provider's usual variable (DEEPSEEK_API_KEY…)
LLM_BASE_URL=                         # only for an OpenAI-compatible server (then a bare model name)
```

Edit `.env` and `./dot.sh restart` to change it by hand.

## Identity

- **Email** (`ext/email.py`): one mailbox (IMAP + SMTP) gives every agent an address,
  either `you+agent@gmail.com` or `agent@your-domain` behind a catch-all. New mail is
  routed to the agent it was addressed to; mail to the bare mailbox address goes to the
  front-desk agent, so use a mailbox made for your agents rather than your personal one.
  One-time codes are hidden from agents. Sending always asks you first, and goes out
  from that mailbox's account.
- **Phone** (`ext/phone.py`): a Twilio account and one number per agent. SMS in and out;
  calls are one-way (it reads a message aloud) and inbound calls go to voicemail, which is
  transcribed. Webhooks are verified with Twilio's signature, so the server needs a
  public HTTPS address (set `DOT_PUBLIC_URL` if you're behind a proxy).

## Safety

**Every tool call goes through the Gatekeeper** (`opendot/gatekeeper.py`): allow, ask or
deny, per tool and per agent, with rules you can change in Settings. Anything that
speaks for you (sending email or SMS, posting, deleting outside the agent's folder) asks
first. Passwords live in a vault as `{{vault:NAME}}` placeholders that are filled in only
when the tool runs, so the model never sees them, and they're masked in logs. Mail, pages
and messages from outside are marked as untrusted before an agent reads them.

**A second look** (`opendot/reviewer.py`): before an outward action (email, SMS, posting,
MCP tools, sending data from the shell…), a short independent model call compares it with
what you actually asked, the agent's job and your rules. It can only ask or block, never
allow; if it fails or times out, the rule decision stands (`DOT_REVIEWER=off` to disable).
**Yours to do**: passwords, 2FA, deleting accounts, moving money, contracts and ID
submissions are refused outright, with the link handed to you. **Check-ins**
(heartbeat/research runs) may only use read-only tools and propose.

**Each agent's computer is isolated as well as your machine allows** (`DOT_SANDBOX=auto`):

1. **bubblewrap** (Linux, if installed): the shell sees only the agent's home, the
   shared drive and read-only system folders, in its own process space.
2. **Docker** (if you've run `docker build -t opendot-computer docker/computer`): a
   container per agent with memory, CPU and process limits, no root, all capabilities
   dropped, only the agent's home and the shared drive mounted.
3. **local** (otherwise, e.g. a plain macOS install): the shell runs as you. The
   Gatekeeper blocks commands that reach for OpenDot's vault, token or database, other
   agents' files, your `.ssh`, cloud and browser credentials, or another agent's browser,
   and asks before an agent touches or deletes anything outside its own folder.

The local guard reads command text, so it's a safety net, not a wall. For real isolation
on a computer you care about, use bubblewrap or Docker. Each agent's Chromium has its own
profile and only listens on `127.0.0.1`.

## Restarts don't lose work

Every run is a row in the `jobs` table, written in the same transaction as the message that
started it (`opendot/durable.py`). On start-up, jobs that were interrupted less than 6 hours
ago are picked up once with the steps they'd already done; older ones are closed. Pending
approvals live in the database, so a decision made after a restart is honoured. Each
outward action writes an `effects` row before it runs and its result after, so a resumed
job never sends the same email twice, and a call cut off mid-way is flagged for the agent
to check rather than blindly repeated.

## Reaching it from your phone

The server listens on `127.0.0.1` only. To use it from a phone:

| | how | link |
|---|---|---|
| **Tailscale** (recommended) | install Tailscale on the computer and the phone, `./dot.sh link` | permanent, private HTTPS: only your own devices can open it |
| **Same Wi-Fi** | `./dot.sh lan` | `http://192.168.x.x:7878`, only at home; no HTTPS |
| **Cloudflare quick link** | `./dot.sh link` without Tailscale | public HTTPS, no account, but it changes when the computer restarts |
| **Your own domain** | set `DOT_TUNNEL_TOKEN` (a Cloudflare named tunnel) and `DOT_PUBLIC_URL` | permanent public HTTPS |

Whatever the route, every request needs the pairing token: `./dot.sh pair` shows a
one-time QR code (valid 10 minutes) that signs a browser or phone in.

## Settings (`.env`)

| variable | default | |
|---|---|---|
| `LLM_MODEL` | set by `./dot.sh setup` | `provider/model` |
| `LLM_API_KEY` | | or the provider's own variable |
| `LLM_BASE_URL` | | only for your own OpenAI-compatible server |
| `LLM_REASONING_EFFORT` | | `low` / `medium` / `high` for models that think |
| `DOT_PORT` | `7878` | |
| `DOT_TIMEZONE` | from the system | routines and quiet hours use it |
| `DOT_SANDBOX` | `auto` | see Safety |
| `DOT_REVIEWER` | `on` | the second look before outward actions |
| `DOT_PUBLIC_URL` | | your public address, if you have one |
| `DOT_DAILY_NUDGES` · `DOT_QUIET_FROM` · `DOT_QUIET_TO` | `4` · `22` · `8` | how pushy agents may be |
| `DOT_SEARXNG_URL` | | your own SearXNG for web search (default: DuckDuckGo) |

## Extending it

Every file in `opendot/ext/` and `opendot/channels/` is loaded at start-up. A module can:

```python
from opendot.tools import register_tool, fn, S
from opendot.db import db

db.ensure_schema("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT)")

async def _jot(ctx, body: str) -> dict:
    ...
    return {"ok": True}

register_tool("jot", fn("jot", "Write a note.", {"body": S}, ["body"]), _jot, policy="allow")

def PROMPT(agent: dict) -> str | None:   # optional: a paragraph added to the system prompt
    return None

router = APIRouter(prefix="/api/notes")  # optional: HTTP routes
async def start() -> None: ...           # optional: background work
```

Skills are folders with a `SKILL.md` (see `skills/`); any MCP server can be connected
from Settings → Other apps.

## Development

```bash
.venv/bin/python -m opendot serve      # backend on :7878
cd web && npm run dev                   # web app with hot reload (proxies to :7878)
.venv/bin/python -m pytest -q           # tests
```
