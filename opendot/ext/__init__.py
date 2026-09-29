"""Extension loader.

Every module in ``opendot/ext/`` and ``opendot/channels/`` is imported at startup.
A module may define (all optional):

    router: fastapi.APIRouter      mounted on the app (use prefix /api/<name>; auth is automatic)
    async def start() -> None      long-running background task (started in lifespan)
    async def stop() -> None       called on shutdown
    PROMPT: str | callable(agent) -> str   extra system-prompt section

and call ``opendot.tools.register_tool(...)`` / ``db.ensure_schema(...)`` /
``bus.listen(...)`` at import time. A module that fails to import is logged and skipped.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

log = logging.getLogger("opendot.ext")
MODULES: list[ModuleType] = []


def load_all() -> list[ModuleType]:
    if MODULES:
        return MODULES
    for pkg_name in ("opendot.ext", "opendot.channels"):
        pkg = importlib.import_module(pkg_name)
        for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            if m.name.startswith("_"):
                continue
            try:
                MODULES.append(importlib.import_module(f"{pkg_name}.{m.name}"))
                log.info("extension loaded: %s.%s", pkg_name, m.name)
            except Exception:
                log.exception("extension %s.%s failed to load", pkg_name, m.name)
    return MODULES


def prompt_sections(agent: dict) -> list[str]:
    out = []
    for m in MODULES:
        p = getattr(m, "PROMPT", None)
        try:
            text = p(agent) if callable(p) else p
        except Exception:
            log.exception("PROMPT of %s failed", m.__name__)
            text = None
        if text:
            out.append(text)
    return out
