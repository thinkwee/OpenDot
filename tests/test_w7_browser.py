"""Helper agents (``<agent>-wN``) get their own tab in the parent's browser
instead of launching a new Chromium; the root id / instance resolution used
throughout the browser + screencast code is correct."""

from __future__ import annotations

from opendot.computer import computer_for
from opendot.computer.core import _root_id


def test_root_id_strips_worker_suffix():
    assert _root_id("ag_abc123-w0") == "ag_abc123"
    assert _root_id("ag_abc123-w7") == "ag_abc123"
    assert _root_id("ag_abc123") == "ag_abc123"


def test_helper_resolves_to_parent_computer():
    root = computer_for("ag_w7_browser_root")
    helper = computer_for("ag_w7_browser_root-w0")
    assert helper.root_id() == root.agent_id
    assert helper._root() is root


def test_helpers_and_root_share_one_cdp_port():
    root = computer_for("ag_w7_browser_root2")
    h1 = computer_for("ag_w7_browser_root2-w0")
    h2 = computer_for("ag_w7_browser_root2-w1")
    assert h1.cdp_port == root.cdp_port == h2.cdp_port


async def test_helper_tabs_open_in_shared_context():
    root = computer_for("ag_w7_browser_root3")
    h1 = computer_for("ag_w7_browser_root3-w0")
    h2 = computer_for("ag_w7_browser_root3-w1")
    try:
        await root.browser(action="goto", url="https://example.com")
        await h1.browser(action="goto", url="https://example.com")
        await h2.browser(action="goto", url="https://example.com")
        ctx = await root._ensure_context()
        assert (await h1._root()._ensure_context()) is ctx
        assert (await h2._root()._ensure_context()) is ctx
        tabs = root.open_tabs()
        owners = {t["owner_id"] for t in tabs}
        assert owners == {root.agent_id, h1.agent_id, h2.agent_id}
    finally:
        await root.close()
