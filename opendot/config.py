"""Runtime settings, read from environment / .env (no extra deps)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class Settings:
    # LLM — any LiteLLM model name (see opendot/llm.py); ./dot.sh setup writes these
    LLM_MODEL = _env("LLM_MODEL")  # provider/model — ./dot.sh setup picks it
    LLM_API_KEY = _env("LLM_API_KEY")  # or the provider's own variable (OPENAI_API_KEY…)
    LLM_BASE_URL = _env("LLM_BASE_URL")  # only for your own OpenAI-compatible server
    LLM_REASONING_EFFORT = _env("LLM_REASONING_EFFORT")  # low | medium | high, if the model has it
    LLM_MAX_TOKENS = int(_env("LLM_MAX_TOKENS", "16384"))
    LLM_CONCURRENCY = int(_env("LLM_CONCURRENCY", "4"))
    LLM_VISION = _env("LLM_VISION", "0") == "1"  # send uploaded images to the model as images

    HOST = _env("DOT_HOST", "127.0.0.1")
    PORT = int(_env("DOT_PORT", "7878"))
    DATA_DIR = Path(_env("DOT_DATA_DIR", str(ROOT / "data"))).resolve()
    TIMEZONE = _env("DOT_TIMEZONE", "Europe/London")
    LANGUAGE = _env("DOT_LANGUAGE", "auto")

    MAX_STEPS = int(_env("DOT_MAX_STEPS", "24"))
    # context budget (see context.py): clear old tool results past this many tokens (or
    # half the model's window, if smaller); the window assumed for models LiteLLM doesn't know
    CONTEXT_TRIGGER = int(_env("DOT_CONTEXT_TRIGGER", "60000"))
    CONTEXT_WINDOW = int(_env("DOT_CONTEXT_WINDOW", "128000"))
    SHELL_TIMEOUT = int(_env("DOT_SHELL_TIMEOUT", "120"))
    HEARTBEAT_MINUTES = int(_env("DOT_HEARTBEAT_MINUTES", "60"))
    CHROMIUM_PATH = _env("DOT_CHROMIUM_PATH", "")
    PROXY = _env("DOT_PROXY", "")  # e.g. a residential proxy when running in a data centre

    @property
    def access_token(self) -> str:
        """Pairing token: required for every API/WS call. Generated on first run."""
        p = self.DATA_DIR / "access_token"
        if not p.exists():
            self.DATA_DIR.mkdir(parents=True, exist_ok=True)
            p.write_text(secrets.token_urlsafe(24))
            p.chmod(0o600)
        return p.read_text().strip()


settings = Settings()
