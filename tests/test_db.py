"""SQLite store: insert/query/update round-trip, JSON columns, kv store."""

from __future__ import annotations

from opendot.db import db, new_id


def test_insert_and_one():
    row = db.insert("agents", id=new_id("ag_"), name="Testy", emoji="🧪", color="#fff",
                    role="tester", tagline="")
    got = db.one("SELECT * FROM agents WHERE id=?", row["id"])
    assert got["name"] == "Testy"


def test_json_column_round_trip():
    t = db.insert("threads", id=new_id("th_"), title="grp", kind="group",
                  members=["a", "b", "c"])
    got = db.one("SELECT * FROM threads WHERE id=?", t["id"])
    assert got["members"] == ["a", "b", "c"]


def test_update():
    a = db.insert("agents", id=new_id("ag_"), name="Upd", emoji="x", color="#fff", role="r")
    db.update("agents", a["id"], status="working")
    assert db.one("SELECT status FROM agents WHERE id=?", a["id"])["status"] == "working"


def test_delete():
    a = db.insert("agents", id=new_id("ag_"), name="Del", emoji="x", color="#fff", role="r")
    db.delete("agents", a["id"])
    assert db.one("SELECT * FROM agents WHERE id=?", a["id"]) is None


def test_kv_roundtrip():
    db.kv_set("some:key", {"a": 1, "b": [1, 2, 3]})
    assert db.kv_get("some:key") == {"a": 1, "b": [1, 2, 3]}
    assert db.kv_get("missing:key", "default") == "default"
