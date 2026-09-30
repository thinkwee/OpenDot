"""The first-run model picker behind ``./dot.sh`` / ``./dot.sh setup``.

Pick a provider → paste a key → choose from the models that provider offers *right now*
→ a test call. Nothing is written to ``.env`` until the model has answered (or you say
"keep it anyway"), so a typo never leaves a broken install behind.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
from .models import BY_ID, PROVIDERS, friendly_error, full_name, list_models  # noqa: E402

ENV = Path(__file__).resolve().parent.parent / ".env"
SHOW = 12


def c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def say(s: str) -> None:
    print(f"  {c('38;5;209', '✦')} {s}")


def warn(s: str) -> None:
    print(f"  {c('33', '!')} {s}")


def ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"     {prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:  # no terminal to answer from
        raise SystemExit(1)
    return v or default


def setenv(values: dict[str, str]) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if ln.split("=", 1)[0] not in values]
    lines += [f"{k}={v}" for k, v in values.items()]
    ENV.write_text("\n".join(lines) + "\n")
    ENV.chmod(0o600)


def pick_provider() -> dict:
    print()
    say("Which AI should your agents think with?")
    for i, p in enumerate(PROVIDERS, 1):
        print(f"     {i:>2}) {p['label']:<22} {c('2', p.get('note', ''))}")
    print(f"     {len(PROVIDERS) + 1:>2}) {'Something else':<22} "
          f"{c('2', 'any LiteLLM model name — docs.litellm.ai/docs/providers')}")
    while True:
        ch = ask("choice", "1")
        if ch.isdigit() and 1 <= int(ch) <= len(PROVIDERS) + 1:
            return PROVIDERS[int(ch) - 1] if int(ch) <= len(PROVIDERS) else {"id": "other", "env": ""}
        warn("type one of the numbers above")


def get_key(p: dict) -> str:
    if not p.get("env") and p["id"] not in ("custom", "other"):
        return ""
    if p.get("env") and os.environ.get(p["env"]):
        if ask(f"use the key in ${p['env']}? (Y/n)", "y").lower().startswith("y"):
            return os.environ[p["env"]]
    if p.get("keys"):
        print(f"     get a key at {c('4', p['keys'])}")
    hint = "(hidden)" if p["id"] not in ("custom", "other") else "(hidden, Enter if none)"
    try:
        return getpass.getpass(f"     API key {hint}: ").strip()
    except EOFError:
        raise SystemExit(1)


def pick_model(p: dict, key: str, base: str) -> tuple[str, str]:
    """(model name, base URL). Lists live models when the provider allows it."""
    if p["id"] == "other":
        return ask("LiteLLM model name (provider/model)"), ""
    say("asking which models are available…")
    try:
        models, used = list_models(p["id"], key, base)
    except PermissionError:
        raise
    except Exception as e:
        warn(f"couldn't list models: {friendly_error(e)}")
        if p["id"] == "ollama":
            warn("is Ollama running? Install it from https://ollama.com, then pull a model (ollama.com/library)")
        return full_name(p["id"], ask("model name")), base
    if not models:
        warn("no chat models found on this account")
        return full_name(p["id"], ask("model name")), used or base
    shown = models[:SHOW]
    for i, m in enumerate(shown, 1):
        print(f"     {i:>2}) {m.split('/', 1)[1] if p.get('prefix') else m}")
    if len(models) > SHOW:
        print(c("2", f"         … {len(models) - SHOW} more — type any name to use one of them"))
    ch = ask("model (number or name)", "1")
    if ch.isdigit() and 1 <= int(ch) <= len(shown):
        return shown[int(ch) - 1], used or base
    return full_name(p["id"], ch), used or base


async def _try(model: str, key: str, base: str) -> str:
    from .llm import LLM
    cli = LLM(base_url=base, api_key=key, model=model, reasoning_effort="", record=False)
    r = await asyncio.wait_for(
        cli.chat([{"role": "user", "content": "Say hi in 3 words."}], max_tokens=400), 90)
    return r.content or "(it answered)"


def test(p: dict, model: str, key: str, base: str) -> tuple[bool, str, str]:
    """(ok, reply-or-reason, base that worked)."""
    bases = [base] if base or "bases" not in p else p["bases"]
    reason = ""
    for b in bases:
        try:
            return True, asyncio.run(_try(model, key, b)), b
        except Exception as e:
            reason = friendly_error(e)
    return False, reason, base


def main() -> int:
    while True:
        p = pick_provider()
        base = ask("base URL (ends in /v1)") if p["id"] == "custom" else ""
        key = get_key(p)
        while True:
            try:
                model, base = pick_model(p, key, base)
            except PermissionError as e:
                warn(friendly_error(e))
                key = get_key(p)
                continue
            if not model or model.endswith("/"):
                warn("a model name is needed")
                continue
            say(f"testing {c('1', model)}…")
            ok, said, base = test(p, model, key, base)
            if ok:
                print(f"     it says: {said.splitlines()[0][:120]}")
                say(c("32", "it works"))
                break
            warn(f"that didn't work: {said}")
            nxt = ask("m) another model   k) another key   p) another provider   s) keep it anyway", "m")
            if nxt.startswith("k"):
                key = get_key(p)
            elif nxt.startswith("p"):
                break
            elif nxt.startswith("s"):
                ok = True
                break
        if ok:
            break
    effort = "low" if p["id"] == "openai" and re.match(r"openai/(gpt-5|o\d)", model) else ""
    setenv({"LLM_MODEL": model, "LLM_API_KEY": key, "LLM_BASE_URL": base,
            "LLM_REASONING_EFFORT": effort})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
