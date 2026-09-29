"""Files: parsing any format, uploads → model context, long-text conversion, work-file
tracking + mentioned paths, the universal view endpoints, read_file paging."""

from __future__ import annotations

import asyncio
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from opendot import ext as ext_mod
from opendot import fileparse
from opendot.computer import computer_for
from opendot.db import db, new_id

ext_mod.load_all()
from opendot.ext import uploads as U  # noqa: E402
from opendot.ext import workfiles as W  # noqa: E402


def _agent(name="Pip"):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="✨", color="#FFB38A", role="r")


# ---------------------------------------------------------------- fileparse
def test_parse_text_code_and_encodings(tmp_path):
    (tmp_path / "a.py").write_text("print('hi')\n")
    (tmp_path / "gbk.txt").write_bytes("布达佩斯 会议".encode("gbk"))
    r = fileparse.parse(tmp_path / "a.py")
    assert r["kind"] == "code" and "```py" in r["text"] and "print" in r["text"]
    assert "布达佩斯" in fileparse.parse(tmp_path / "gbk.txt")["text"]


def test_parse_office_docs(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Hungexpo day one")
    d.save(tmp_path / "p.docx")
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["city", "cost"])
    wb.active.append(["Budapest", 120])
    wb.save(tmp_path / "c.xlsx")
    assert "Hungexpo" in fileparse.parse(tmp_path / "p.docx")["text"]
    x = fileparse.parse(tmp_path / "c.xlsx")
    assert x["kind"] == "spreadsheet" and "Budapest" in x["text"] and "120" in x["text"]


def test_parse_archive_lists_without_extracting(tmp_path):
    with zipfile.ZipFile(tmp_path / "b.zip", "w") as z:
        z.writestr("src/x.py", "1")
        z.writestr("../evil.txt", "2")
    r = fileparse.parse(tmp_path / "b.zip")
    assert r["kind"] == "archive" and "src/x.py" in r["text"]
    assert not (tmp_path / "evil.txt").exists()


def test_parse_binary_and_broken_never_raise(tmp_path):
    (tmp_path / "d.bin").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    assert fileparse.parse(tmp_path / "d.bin")["text"]
    assert fileparse.parse(tmp_path / "bad.pdf")["kind"] == "document"


def test_image_ocr(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (700, 160), "white")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 44)
    except OSError:
        font = ImageFont.load_default(size=44)
    ImageDraw.Draw(im).text((20, 50), "TOTAL 4271 EUR", fill="black", font=font)
    im.save(tmp_path / "r.png")
    r = fileparse.parse(tmp_path / "r.png")
    assert r["kind"] == "image" and "4271" in r["text"]


# ---------------------------------------------------------------- uploads API + context
def test_upload_parse_and_context(client: TestClient, auth_headers):
    r = client.post("/api/uploads", headers=auth_headers, data={"thread_id": "th_x"},
                    files={"file": ("notes.md", b"# Plan\nfly on the 24th", "text/markdown")})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["upload"] and up["path"].startswith("shared/uploads/")
    # parsing runs on the app's own event loop, so poll rather than await its Event
    for _ in range(300):
        row = db.one("SELECT * FROM uploads WHERE id=?", up["id"])
        if row["status"] != "parsing":
            break
        time.sleep(0.1)
    assert row["status"] == "ready" and row["chars"] > 0
    block = U.context_block([up], full=True)
    assert "~/shared/uploads/" in block and "fly on the 24th" in block
    lean = U.context_block([up], full=False)
    assert "fly on the 24th" not in lean and "notes.md" in lean
    assert client.get(f"/api/uploads/{up['id']}/text", headers=auth_headers).text.startswith("# Plan")
    assert client.get("/api/uploads?thread=th_x", headers=auth_headers).json()["uploads"]


def test_upload_rejects_oversize(client: TestClient, auth_headers, monkeypatch):
    monkeypatch.setattr(U, "MAX_MB", 0)
    r = client.post("/api/uploads", headers=auth_headers, files={"file": ("big.txt", b"x" * 10)})
    assert r.status_code == 413


def test_upload_name_sanitised(client: TestClient, auth_headers):
    r = client.post("/api/uploads", headers=auth_headers,
                    files={"file": ("../../etc/passwd", b"root")}).json()
    assert r["path"].startswith("shared/uploads/") and ".." not in r["path"]


def test_long_text_becomes_file():
    async def go():
        stub, atts = await U.maybe_long_text("x" * (U.LONG_TEXT_CHARS + 10) + " please summarise",
                                             "th_long", "app")
        await U.wait_ready([atts[0]["id"]])
        return stub, atts
    stub, atts = asyncio.run(go())
    assert len(atts) == 1 and atts[0]["title"].startswith("pasted-")
    assert "please summarise" in stub and len(stub) < 1000
    short, none = asyncio.run(U.maybe_long_text("hi", "th_long", "app"))
    assert short == "hi" and none == []


def test_big_upload_inlines_head_only(tmp_path):
    async def go():
        row = await U.ingest_bytes("big.txt", ("line\n" * 20000).encode(), "th_big")
        await U.wait_ready([row["id"]])
        return U.public(db.one("SELECT * FROM uploads WHERE id=?", row["id"]))
    card = asyncio.run(go())
    block = U.context_block([card], full=True)
    assert "read_file(" in block and len(block) < U.INLINE_HEAD + 2000


# ---------------------------------------------------------------- work files
def test_workfiles_track_and_mentions():
    a = _agent()
    before = W.snapshot(a["id"])
    c = computer_for(a["id"])
    c.write_file("shared/trip_notes.md", "# notes")
    c.write_file("draft.py", "x=1")
    rows = W.record_diff(before, a["id"], "th_w", "run_w")
    paths = {r["path"] for r in rows}
    assert {"shared/trip_notes.md", "draft.py"} <= paths
    cards = W.mentioned_files("Saved to `shared/trip_notes.md` and ~/draft.py, see 2.5 too",
                              a["id"], "th_w")
    assert [x["path"] for x in cards] == ["shared/trip_notes.md", "draft.py"]
    # the symlinked and real path of a shared file are one entry, not two
    n = db.one("SELECT COUNT(*) n FROM thread_files WHERE thread_id='th_w' AND name='trip_notes.md'")
    assert n["n"] == 1


def test_view_endpoints_and_sandboxing(client: TestClient, auth_headers):
    a = _agent()
    c = computer_for(a["id"])
    c.write_file("page.html", "<script>alert(1)</script>")
    c.write_file("doc.md", "# hi")
    r = client.get("/api/view/raw", headers=auth_headers, params={"agent": a["id"], "path": "page.html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "sandbox" in r.headers.get("content-security-policy", "")
    t = client.get("/api/view/text", headers=auth_headers, params={"agent": a["id"], "path": "doc.md"})
    assert t.text.startswith("# hi")
    bad = client.get("/api/view/raw", headers=auth_headers, params={"agent": a["id"], "path": "../../../etc/passwd"})
    assert bad.status_code in (403, 404)
    assert client.get("/api/view/raw", params={"agent": a["id"], "path": "doc.md"}).status_code == 401


def test_read_file_pages_and_parses(tmp_path):
    a = _agent()
    c = computer_for(a["id"])
    c.write_file("long.txt", "abc" * 30000)
    first = c.read_file("long.txt", 40_000, 0)
    assert len(first["content"]) == 40_000 and first["next_offset"] == 40_000
    got, off = first["content"], first["next_offset"]
    while off is not None:
        page = c.read_file("long.txt", 40_000, off)
        got, off = got + page["content"], page["next_offset"]
    assert got == "abc" * 30000
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["k", "v"])
    wb.save(c.home / "t.xlsx")
    x = c.read_file("t.xlsx")
    assert "| k | v |" in x["content"] and "note" in x
