"""Real PTY shell: cwd/env persist across calls; ANSI is stripped from the
clean text returned to the LLM tool call; a timeout kills only the foreground
job, not the session."""

from __future__ import annotations

from opendot.computer import computer_for


async def test_shell_persists_cwd_and_env():
    c = computer_for("ag_w7_term1")
    r1 = await c.shell("cd /tmp && export DOT_TEST_VAR=hello && pwd")
    assert r1["exit_code"] == 0
    assert "/tmp" in r1["output"]
    r2 = await c.shell("pwd && echo $DOT_TEST_VAR")
    assert "/tmp" in r2["output"]
    assert "hello" in r2["output"]


async def test_shell_strips_ansi_and_prompt_noise():
    c = computer_for("ag_w7_term2")
    r = await c.shell("printf 'plain output\\n'")
    assert r["output"].strip() == "plain output"
    assert "\x1b[" not in r["output"]


async def test_shell_timeout_keeps_session_alive():
    c = computer_for("ag_w7_term3")
    r = await c.shell("sleep 5 && echo should-not-appear", timeout=1)
    assert r["exit_code"] != 0
    assert "timed out" in r["output"]
    # session survives: a follow-up command still works
    r2 = await c.shell("echo still-alive")
    assert "still-alive" in r2["output"]


async def test_exit_code_is_captured():
    c = computer_for("ag_w7_term4")
    ok = await c.shell("true")
    bad = await c.shell("false")
    assert ok["exit_code"] == 0
    assert bad["exit_code"] == 1
