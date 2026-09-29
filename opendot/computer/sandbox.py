"""Sandbox backends: auto | local | bwrap | docker | e2b — ``DOT_SANDBOX`` env var.

``auto`` (the default) picks the strongest backend this machine can run:
bubblewrap on Linux, else Docker if the ``opendot-computer`` image is built,
else ``local`` (the agent's shell runs as you; only Gatekeeper's path guard
stands between it and your files). The choice is logged once.

The interface is deliberately tiny so backends are swappable:

  ``backend_for(agent_id) -> Backend``
  ``Backend.shell_argv(command, home, env) -> list[str]``   (local/bwrap only —
      these run in-process via asyncio.create_subprocess_exec / PtySession)
  ``Backend.cdp_url(agent_id) -> str | None``                (docker/e2b: where
      to reach the sandbox's Chromium over CDP; None for local/bwrap, which
      launch their own Playwright-managed Chromium instead)

``docker`` targets ``docker/computer/Dockerfile`` (Debian + Xvfb + a light WM +
Chromium + noVNC/websockify): the shell runs via ``docker exec``, the browser
via CDP into the container, and the Computer panel can embed the container's
noVNC page directly (``computer://<agent>/vnc``) as an extra live-view option.

``e2b`` uses the official ``e2b-code-interpreter`` / ``e2b-desktop`` SDKs when
``E2B_API_KEY`` is set.

docker: one container per root agent, the agent's home and the shared drive
bind-mounted at their host paths (so file tools, uploads and the shell all see
the same files), capped by ``DOT_DOCKER_MEMORY`` / ``_CPUS`` / ``_PIDS``, with
no Linux capabilities. e2b is implemented against its docs but less tested.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys

from ..config import settings

log = logging.getLogger("opendot.computer.sandbox")

BWRAP_ISOLATION = ["--unshare-pid", "--unshare-ipc", "--new-session", "--die-with-parent"]
_chosen: str | None = None  # resolved once per process


def backend_name() -> str:
    global _chosen
    if _chosen is None:
        want = os.environ.get("DOT_SANDBOX", "auto").strip().lower() or "auto"
        if want == "auto":
            _chosen = "bwrap" if bwrap_works() else "docker" if docker_ready() else "local"
        elif want == "bwrap" and not shutil.which("bwrap"):
            _chosen = "local"
        else:
            _chosen = want
        if _chosen == "local":
            log.warning("agent computers: no sandbox (local) — shells run as you, guarded "
                        "only by Gatekeeper. Install bubblewrap (Linux) or build "
                        "docker/computer as %s for isolation.", DockerBackend.IMAGE)
        else:
            log.info("agent computers: sandbox = %s", _chosen)
    return _chosen


def bwrap_works() -> bool:
    """Linux + bwrap installed + unprivileged user namespaces actually allowed."""
    if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
        return False
    try:
        return subprocess.run(["bwrap", "--ro-bind", "/", "/", *BWRAP_ISOLATION, "true"],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def docker_ready() -> bool:
    """Docker is running and the computer image has been built."""
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", DockerBackend.IMAGE],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class DockerBackend:
    """One long-lived container per agent, built from docker/computer/Dockerfile."""

    IMAGE = os.environ.get("DOT_DOCKER_IMAGE", "opendot-computer:latest")

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.container = f"dot-computer-{agent_id}"
        self.home = settings.DATA_DIR / "agents" / agent_id / "home"

    def run_argv(self) -> list[str]:
        """``docker run`` for this agent: home + shared drive mounted at their host
        paths (paths mean the same thing inside and out), resource limits, no privileges."""
        shared = settings.DATA_DIR / "shared"
        argv = ["docker", "run", "-d", "--rm", "--name", self.container,
                "--memory", os.environ.get("DOT_DOCKER_MEMORY", "2g"),
                "--cpus", os.environ.get("DOT_DOCKER_CPUS", "2"),
                "--pids-limit", os.environ.get("DOT_DOCKER_PIDS", "512"),
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "-v", f"{self.home}:{self.home}", "-v", f"{shared}:{shared}",
                "-e", f"HOME={self.home}", "-w", str(self.home),
                # 9222: CDP, 6080: noVNC — published on the host's loopback only
                "-p", "127.0.0.1::9222", "-p", "127.0.0.1::6080"]
        if hasattr(os, "getuid"):  # files it writes stay owned by you
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return argv + [self.IMAGE]

    async def ensure(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", self.container,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        if proc.returncode == 0 and out.strip() == b"true":
            return
        self.home.mkdir(parents=True, exist_ok=True)
        (settings.DATA_DIR / "shared").mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *self.run_argv(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"docker run failed: {err.decode(errors='replace')[-300:]}")

    async def exec_argv(self, command: str) -> list[str]:
        await self.ensure()
        return ["docker", "exec", "-i", self.container, "bash", "-lc", command]

    async def cdp_url(self) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "port", self.container, "9222",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        line = out.decode().strip().splitlines()[-1] if out else ""
        return f"http://{line}" if line else None

    async def vnc_url(self) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "port", self.container, "6080",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        line = out.decode().strip().splitlines()[-1] if out else ""
        return f"http://{line}/vnc.html?autoconnect=1&resize=scale" if line else None


class E2BBackend:
    """e2b-code-interpreter / e2b-desktop sandbox — used when E2B_API_KEY is set."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._sbx = None

    async def ensure(self):
        if self._sbx is not None:
            return self._sbx
        key = os.environ.get("E2B_API_KEY")
        if not key:
            raise RuntimeError("E2B_API_KEY not set")
        try:
            from e2b_desktop import Sandbox  # type: ignore
        except ImportError as e:
            raise RuntimeError("pip install e2b-desktop to use DOT_SANDBOX=e2b") from e
        self._sbx = await asyncio.to_thread(Sandbox, api_key=key)
        return self._sbx

    async def run(self, command: str) -> dict:
        sbx = await self.ensure()
        res = await asyncio.to_thread(sbx.commands.run, command)
        return {"exit_code": getattr(res, "exit_code", 0), "output": getattr(res, "stdout", "")}

    async def cdp_url(self) -> str | None:
        sbx = await self.ensure()
        try:
            return await asyncio.to_thread(sbx.get_browser_url)  # type: ignore[attr-defined]
        except Exception:
            return None


async def push_file(backend: str, agent_id: str, local, rel: str) -> None:
    """Copy a host file into an agent's e2b sandbox at ``~/<rel>`` (uploads). Docker
    needs nothing: the home and shared drive are bind-mounted."""
    import re
    root = re.sub(r"-w\d+$", "", agent_id)
    if backend == "e2b":
        sbx = await E2BBackend(root).ensure()
        data = await asyncio.to_thread(local.read_bytes)
        await asyncio.to_thread(sbx.files.write, rel, data)
