"""Agent computer: files must stay inside the agent's home (or shared/) — no path escapes."""

from __future__ import annotations

import pytest

from opendot.computer import computer_for


def test_resolve_relative_path_stays_home():
    c = computer_for("ag_test_computer")
    p = c.resolve("notes.txt")
    assert p.parent == c.home


def test_resolve_rejects_parent_escape():
    c = computer_for("ag_test_computer2")
    with pytest.raises(PermissionError):
        c.resolve("../../../etc/passwd")


def test_resolve_rejects_absolute_escape():
    c = computer_for("ag_test_computer3")
    with pytest.raises(PermissionError):
        c.resolve("/etc/passwd")


def test_resolve_allows_shared_dir():
    c = computer_for("ag_test_computer4")
    p = c.resolve("shared/note.md")
    assert p.is_relative_to(c.home) or "shared" in str(p)


def test_write_then_read_file_round_trip():
    c = computer_for("ag_test_computer5")
    c.write_file("hello.txt", "hi there")
    out = c.read_file("hello.txt")
    assert out["content"] == "hi there"


def test_list_files_hides_dotfiles():
    c = computer_for("ag_test_computer6")
    c.write_file("visible.txt", "x")
    c.write_file(".hidden.txt", "x")
    names = {f["path"] for f in c.list_files()["files"]}
    assert "visible.txt" in names
    assert ".hidden.txt" not in names
