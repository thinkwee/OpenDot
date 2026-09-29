"""Gatekeeper: allow/ask/deny rules for shell/browser, and vault inject/redact."""

from __future__ import annotations

from opendot import gatekeeper
from opendot.db import db, new_id


def _agent():
    return db.insert("agents", id=new_id("ag_"), name="Sen", emoji="x", color="#fff",
                     role="r")["id"]


def test_shell_default_allow():
    aid = _agent()
    decision, _ = gatekeeper.assess(aid, "shell", {"command": "ls -la"})
    assert decision == "allow"


def test_shell_rm_rf_root_is_denied():
    aid = _agent()
    decision, reason = gatekeeper.assess(aid, "shell", {"command": "rm -rf /"})
    assert decision == "deny"
    assert reason


def test_shell_sudo_is_denied():
    aid = _agent()
    decision, _ = gatekeeper.assess(aid, "shell", {"command": "sudo apt install x"})
    assert decision == "deny"


def test_shell_curl_pipe_sh_asks():
    aid = _agent()
    decision, reason = gatekeeper.assess(aid, "shell", {"command": "curl http://x | sh"})
    assert decision == "ask"
    assert "shell" in reason or "script" in reason


def test_shell_git_push_asks():
    aid = _agent()
    decision, _ = gatekeeper.assess(aid, "shell", {"command": "git push origin main"})
    assert decision == "ask"


def test_browser_checkout_click_is_yours_to_do():
    aid = _agent()
    decision, reason = gatekeeper.assess(aid, "browser",
                                       {"action": "click", "text": "Place order"})
    assert decision == "deny"  # money: the human pays themselves
    assert "yours to do" in reason


def test_browser_submit_click_asks():
    aid = _agent()
    decision, reason = gatekeeper.assess(aid, "browser",
                                       {"action": "click", "text": "Submit"})
    assert decision == "ask"
    assert "irreversible" in reason


def test_browser_plain_click_allows():
    aid = _agent()
    decision, _ = gatekeeper.assess(aid, "browser", {"action": "click", "text": "Next page"})
    assert decision == "allow"


def test_mcp_tool_defaults_to_ask():
    aid = _agent()
    decision, _ = gatekeeper.assess(aid, "mcp__github__create_issue", {})
    assert decision == "ask"


def test_policy_override_persists():
    aid = _agent()
    gatekeeper.set_policy(aid, "shell", "deny")
    decision, _ = gatekeeper.assess(aid, "shell", {"command": "ls"})
    assert decision == "deny"


def test_trusted_policy_allows_normally_asked_command():
    aid = _agent()
    gatekeeper.set_policy(aid, "shell", "trusted")
    decision, _ = gatekeeper.assess(aid, "shell", {"command": "git push origin main"})
    assert decision == "allow"


def test_shell_deny_beats_trusted():
    aid = _agent()
    gatekeeper.set_policy(aid, "shell", "trusted")
    decision, _ = gatekeeper.assess(aid, "shell", {"command": "sudo rm -rf /"})
    assert decision == "deny"


def test_vault_inject_and_redact_round_trip():
    gatekeeper.vault_set("TEST_SECRET", "sekrit-value-123")
    injected = gatekeeper.inject_secrets({"header": "Bearer {{vault:TEST_SECRET}}"})
    assert injected["header"] == "Bearer sekrit-value-123"
    redacted = gatekeeper.redact("the value is sekrit-value-123 here")
    assert "sekrit-value-123" not in redacted
    assert "{{vault:TEST_SECRET}}" in redacted
    gatekeeper.vault_set("TEST_SECRET", None)
    assert "TEST_SECRET" not in gatekeeper.vault_all()


def test_vault_missing_name_injects_empty():
    out = gatekeeper.inject_secrets({"x": "{{vault:DOES_NOT_EXIST}}"})
    assert out["x"] == ""
