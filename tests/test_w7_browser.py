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


def test_block_pages_are_recognised_and_normal_ones_arent():
    from opendot.computer.core import blocked_note
    assert blocked_note("https://www.kayak.co.uk/help/bots.html", "What is a bot?", "")
    assert blocked_note("https://x.com", "Just a moment...", "")
    assert blocked_note("https://x.com", "Bot or Not?", "Show us your human side... We can't "
                        "tell if you're a human or a bot.")
    assert not blocked_note("https://x.com/contact", "Contact us",
                            "This form is protected by reCAPTCHA and the Google Privacy Policy.")


def test_proxy_setting_is_split_for_playwright():
    from opendot.computer.core import _proxy
    assert _proxy("http://me%40x:pw@proxy.example:8080") == {
        "server": "http://proxy.example:8080", "username": "me@x", "password": "pw"}


async def test_elements_are_numbered_and_acted_on_by_number():
    c = computer_for("ag_w7_browser_refs")
    try:
        page = (await c._tab()).page
        await page.set_content('<input placeholder="City"><select><option>One</option>'
                               '<option>Two</option></select><button onclick="document.title='
                               "'clicked:'+document.querySelector('input').value+':'+"
                               "document.querySelector('select').value\">Search</button>")
        r = await c.browser("read")
        assert r["elements"][0] == '[1] textbox "City"'
        assert r["elements"][1].startswith("[2] select") and "options: One | Two" in r["elements"][1]
        assert r["elements"][2] == '[3] button "Search"'
        assert c.element_label("3") == 'button "Search"'
        await c.browser("type", ref="1", text="Nice")
        await c.browser("select", ref="2", text="Two")
        r = await c.browser("click", ref="[3]")
        assert r["title"] == "clicked:Nice:Two"
        assert "error" in await c.browser("click", ref="99")
    finally:
        await c.close()


async def test_google_cookie_wall_is_answered_with_reject_all():
    from opendot.computer.core import past_consent
    c = computer_for("ag_w7_browser_consent")
    wall = ("<form action='https://consent.google.com/save' method='post'>"
            "<input type=hidden name=set_eom value=true><button>Tout refuser</button></form>"
            "<form action='https://consent.google.com/save' method='post'>"
            "<input type=hidden name=set_sc value=true><input type=hidden name=set_eom "
            "value=false><button>Tout accepter</button></form>")
    answers = []

    async def serve(route):
        req = route.request
        if req.url.startswith("https://consent.google.com/save"):
            answers.append(req.post_data)
            return await route.fulfill(status=200, body="<title>Flights</title>ok",
                                       content_type="text/html")
        await route.fulfill(status=200, body=wall, content_type="text/html")
    try:
        page = (await c._tab()).page
        await page.route("https://consent.google.com/**", serve)
        await page.goto("https://consent.google.com/m?continue=https://www.google.com/travel")
        await past_consent(page)
        assert answers and "set_eom=true" in answers[0] and "set_sc" not in answers[0]
        assert await page.title() == "Flights"
    finally:
        await c.close()
