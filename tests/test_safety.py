"""Safe-by-default agent computer: Gatekeeper's path guard + sandbox auto-selection."""

from __future__ import annotations

import os

import pytest

from opendot import gatekeeper
from opendot.computer import sandbox
from opendot.computer.core import CDP_PORT_BASE
from opendot.config import settings
from opendot.db import db, new_id


@pytest.fixture
def aid() -> str:
    a = new_id("ag_")
    db.insert("agents", id=a, name="Safe", emoji="x", color="#fff", role="r")
    (settings.DATA_DIR / "agents" / a / "home").mkdir(parents=True, exist_ok=True)
    return a


def shell(aid, command, tool="shell"):
    key = "code" if tool == "python" else "command"
    return gatekeeper.assess(aid, tool, {key: command})


@pytest.mark.parametrize("cmd", [
    "ls -la", "python3 script.py", "pip install requests", "ls /usr/bin",
    "cat /etc/os-release", "echo hi > /tmp/x.txt", "ls sub/..", "cat shared/uploads/a.pdf",
    "export PATH=$HOME/.local/bin:$PATH", "curl -s https://example.com/api/v1 -o out.json",
    "ls ~/notes", "grep -r TODO . 2>/dev/null", "rm -rf build/",
])
def test_normal_work_runs_without_asking(aid, cmd):
    assert shell(aid, cmd) == ("allow", "")


@pytest.mark.parametrize("cmd", [
    "cat ../../../vault.json", "cat vault.json", "cd ..", "cat ../../other/home/notes.txt",
    f"cat {settings.DATA_DIR}/access_token", f"ls {settings.DATA_DIR}",
    "cat $HOME/../../../access_token", "cat ~/../../../dot.db",
])
def test_private_data_is_denied(aid, cmd):
    decision, reason = shell(aid, cmd)
    assert decision == "deny" and reason == gatekeeper.PRIVATE_DATA


def test_python_escaping_home_is_denied(aid):
    code = "print(open('../../../vault.json').read())"
    assert shell(aid, code, "python")[0] == "deny"
    code = f"import os; os.listdir('{settings.DATA_DIR}/agents')"
    assert shell(aid, code, "python")[0] == "deny"


def test_project_env_is_denied(aid):
    from opendot.config import ROOT
    assert shell(aid, f"cat {ROOT}/.env")[0] == "deny"


@pytest.mark.parametrize("cmd", [
    "cat /home/me/.ssh/id_rsa", "ls ~/.aws", "gpg --homedir /Users/me/.gnupg -k",
    "cat /Users/me/.config/gcloud/credentials.db", "ls /Users/me/Library/Keychains",
    "cp '/Users/me/Library/Application Support/Google/Chrome/Default/Cookies' .",
    "ls /home/me/.mozilla/firefox",
])
def test_personal_secrets_are_denied(aid, cmd):
    assert shell(aid, cmd) == ("deny", gatekeeper.PERSONAL)


@pytest.mark.parametrize("cmd", [
    f"curl http://localhost:{CDP_PORT_BASE + 7}/json", "curl 127.0.0.1:9222/json/version",
    "chromium --remote-debugging-port=9999",
])
def test_browser_debug_port_is_denied(aid, cmd):
    assert shell(aid, cmd) == ("deny", gatekeeper.CDP)


def test_outside_home_asks(aid):
    home_parent = os.path.dirname(os.path.expanduser("~"))  # /home or /Users exists
    decision, reason = shell(aid, f"cat {home_parent}/someone/Documents/notes.txt")
    assert (decision, reason) == ("ask", gatekeeper.OUTSIDE)


def test_delete_outside_home_asks(aid):
    assert shell(aid, "rm /tmp/report.pdf") == ("ask", gatekeeper.DELETES_OUTSIDE)
    assert shell(aid, "import shutil; shutil.rmtree('/tmp/work')", "python")[0] == "ask"
    assert shell(aid, "rm old.txt 2>/dev/null") == ("allow", "")


def test_api_like_strings_are_not_paths(aid):
    assert shell(aid, "print('/api/v1/users'.split('/'))", "python") == ("allow", "")


def test_guard_does_not_override_trusted_or_other_asks(aid):
    assert shell(aid, "git push origin main")[1] == "publishes code"
    gatekeeper.set_policy(aid, "shell", "trusted")
    assert shell(aid, "cat /tmp/../srv/data.csv")[0] == "allow"
    assert shell(aid, "cat ../../../vault.json")[0] == "deny"  # deny is never trusted away


# ---------------- sandbox auto-selection ----------------
@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(sandbox, "_chosen", None)
    monkeypatch.delenv("DOT_SANDBOX", raising=False)
    return monkeypatch


def test_auto_prefers_bwrap(fresh):
    fresh.setattr(sandbox, "bwrap_works", lambda: True)
    fresh.setattr(sandbox, "docker_ready", lambda: True)
    assert sandbox.backend_name() == "bwrap"


def test_auto_falls_back_to_docker_then_local(fresh):
    fresh.setattr(sandbox, "bwrap_works", lambda: False)
    fresh.setattr(sandbox, "docker_ready", lambda: True)
    assert sandbox.backend_name() == "docker"
    fresh.setattr(sandbox, "_chosen", None)
    fresh.setattr(sandbox, "docker_ready", lambda: False)
    assert sandbox.backend_name() == "local"


def test_explicit_choice_wins(fresh):
    fresh.setattr(sandbox, "bwrap_works", lambda: True)
    fresh.setenv("DOT_SANDBOX", "local")
    assert sandbox.backend_name() == "local"


def test_bwrap_needs_linux(fresh):
    fresh.setattr(sandbox.sys, "platform", "darwin")
    assert sandbox.bwrap_works() is False


def test_docker_needs_image(fresh):
    fresh.setattr(sandbox.shutil, "which", lambda _: None)
    assert sandbox.docker_ready() is False


def test_docker_run_is_isolated_and_mounts_home():
    dk = sandbox.DockerBackend("ag_x")
    argv = dk.run_argv()
    home = str(settings.DATA_DIR / "agents" / "ag_x" / "home")
    for flag in ("--memory", "--cpus", "--pids-limit", "--cap-drop"):
        assert flag in argv
    assert f"{home}:{home}" in argv and f"HOME={home}" in argv
    assert "127.0.0.1::9222" in argv and argv[-1] == dk.IMAGE


def test_bwrap_isolation_flags():
    for flag in ("--unshare-ipc", "--new-session", "--die-with-parent", "--unshare-pid"):
        assert flag in sandbox.BWRAP_ISOLATION
