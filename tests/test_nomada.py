"""Tests for nomada, run against a fake Zotero (local API and Local Write API)."""
import fcntl
import importlib.machinery
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

SCRIPT = Path(__file__).resolve().parents[1] / "nomada"
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


@pytest.fixture
def nomada(tmp_path, monkeypatch):
    """A fresh copy of the nomada module whose XDG directories live in tmp_path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    loader = importlib.machinery.SourceFileLoader("nomada", str(SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("nomada", loader))
    monkeypatch.setitem(sys.modules, "nomada", module)
    loader.exec_module(module)
    return module


class FakeZotero:
    """Local API with `items`: parent key -> list of child attachments, all tagged "new"."""

    def __init__(self, items):
        self.items = items

    def top(self, tag):
        return tag

    def everything(self, tag):
        return [{"key": k} for k in self.items] if tag == "new" else []

    def children(self, key):
        return self.items[key]


class FakeResponse:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def pdf_child(key, filename, title="Full Text PDF"):
    return {"key": key, "data": {"contentType": "application/pdf", "linkMode": "imported_file",
                                 "filename": filename, "title": title}}


def write_settled(path, data=PDF):
    """Write a file whose mtime is old enough for pull to treat it as fully synced."""
    path.write_bytes(data)
    old = time.time() - 120
    os.utime(path, (old, old))


@pytest.fixture
def env(nomada, tmp_path, monkeypatch):
    dirs = {d: tmp_path / d for d in ("INBOX", "EXPORT", "storage")}
    for d in dirs.values():
        d.mkdir()
    cfg = nomada.Config(inbox=str(dirs["INBOX"]), export=str(dirs["EXPORT"]),
                        zotero_storage=str(dirs["storage"]))
    writes = []

    def post(url, json, timeout):
        writes.append((url.removeprefix(nomada.LOCAL_BASE), json))
        return FakeResponse({"success": True, "attachment_key": "NEWKEY"})

    monkeypatch.setattr(nomada.requests, "post", post)

    def set_items(items):
        monkeypatch.setattr(nomada, "local_zotero", lambda: FakeZotero(items))

    def store(att_key, filename, data=PDF):
        (dirs["storage"] / att_key).mkdir()
        (dirs["storage"] / att_key / filename).write_bytes(data)

    set_items({})
    return SimpleNamespace(m=nomada, cfg=cfg, inbox=dirs["INBOX"], export=dirs["EXPORT"],
                           writes=writes, set_items=set_items, store=store)


def tags_added(writes):
    return [(w["item_key"], w["tags"]) for path, w in writes
            if path == "/write" and w["operation"] == "add_item_tags"]


def attaches(writes):
    return [w for path, w in writes if path == "/attach"]


def test_push_then_pull(env):
    m = env.m
    env.set_items({"P1": [pdf_child("A1", "a.pdf")]})
    env.store("A1", "a.pdf")

    assert m.run_push(env.cfg) == 0
    assert (env.inbox / "a.pdf").read_bytes() == PDF
    assert m.load_state()["map"] == {"a.pdf": "P1"}
    assert tags_added(env.writes) == [("P1", ["inreview"])]

    write_settled(env.export / "a.pdf", PDF + b"annotated\n%%EOF\n")
    assert m.run_pull(env.cfg) == 0
    (attach,) = attaches(env.writes)
    assert attach["item_key"] == "P1"
    assert attach["file_name"] == "a (annotated).pdf"
    assert tags_added(env.writes)[-1] == ("P1", ["reviewed"])
    assert not (env.inbox / "a.pdf").exists()
    assert (env.inbox / "_archived" / "a.pdf").read_bytes() == PDF
    assert (env.export / "_processed" / "a.pdf").exists()
    assert not (env.export / "a.pdf").exists()
    assert m.load_state()["map"] == {}


def test_push_skips_annotated_copies(env):
    env.set_items({"P1": [pdf_child("A1", "a.pdf"),
                          pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)]})
    env.store("A1", "a.pdf")
    assert env.m.run_push(env.cfg) == 0
    assert sorted(p.name for p in env.inbox.iterdir()) == ["a.pdf"]


def test_push_rejects_filename_collision(env):
    env.set_items({"P1": [pdf_child("A1", "a.pdf")], "P2": [pdf_child("A2", "a.pdf")]})
    env.store("A1", "a.pdf")
    env.store("A2", "a.pdf", b"%PDF other\n%%EOF\n")
    assert env.m.run_push(env.cfg) == 1
    assert env.m.load_state()["map"] == {"a.pdf": "P1"}
    assert (env.inbox / "a.pdf").read_bytes() == PDF


def test_push_reports_item_without_pdf(env):
    env.set_items({"P1": []})
    assert env.m.run_push(env.cfg) == 1
    assert env.m.load_state()["map"] == {}


def test_push_rejects_item_with_several_pdfs(env):
    env.set_items({"P1": [pdf_child("A1", "a.pdf"), pdf_child("A2", "b.pdf")]})
    env.store("A1", "a.pdf")
    env.store("A2", "b.pdf")
    assert env.m.run_push(env.cfg) == 1
    assert list(env.inbox.iterdir()) == []
    assert env.m.load_state()["map"] == {}
    assert tags_added(env.writes) == []


def test_push_fails_without_inbox(env):
    env.inbox.rmdir()
    with pytest.raises(env.m.click.ClickException, match="INBOX folder not found"):
        env.m.run_push(env.cfg)


def test_pull_ignores_unmatched_pdf(env):
    write_settled(env.export / "notebook.pdf")
    assert env.m.run_pull(env.cfg) == 0
    assert (env.export / "notebook.pdf").exists()
    assert env.writes == []


def test_pull_skips_recently_modified_pdf(env):
    env.m.save_state({"map": {"a.pdf": "P1"}})
    (env.export / "a.pdf").write_bytes(PDF)
    assert env.m.run_pull(env.cfg) == 0
    assert (env.export / "a.pdf").exists()
    assert env.writes == []
    assert env.m.load_state()["map"] == {"a.pdf": "P1"}


def test_pull_rejects_truncated_pdf(env):
    env.m.save_state({"map": {"a.pdf": "P1"}})
    write_settled(env.export / "a.pdf", b"%PDF-1.4\n1 0 obj << >> endo")
    assert env.m.run_pull(env.cfg) == 1
    assert (env.export / "a.pdf").exists()
    assert env.writes == []


def test_pull_keeps_earlier_archived_copies(env):
    env.m.save_state({"map": {"a.pdf": "P1"}})
    for d in (env.inbox / "_archived", env.export / "_processed"):
        d.mkdir()
        (d / "a.pdf").write_bytes(b"earlier")
    (env.inbox / "a.pdf").write_bytes(PDF)
    write_settled(env.export / "a.pdf")

    assert env.m.run_pull(env.cfg) == 0
    for d in (env.inbox / "_archived", env.export / "_processed"):
        assert (d / "a.pdf").read_bytes() == b"earlier"
        assert len(list(d.iterdir())) == 2


def test_second_run_fails_while_lock_is_held(env):
    m = env.m
    m.save_config(env.cfg)
    m.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with m.LOCK_FILE.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        result = CliRunner().invoke(m.cli, ["pull"])
    assert result.exit_code == 1
    assert "another nomada run is in progress" in result.output
    assert CliRunner().invoke(m.cli, ["pull"]).exit_code == 0


def test_invalid_config_is_reported(nomada):
    nomada.CONFIG_FILE.parent.mkdir(parents=True)
    nomada.CONFIG_FILE.write_text('inbox = "/x"\nbogus = "y"\nwrite_backend = "webapi"\n')
    result = CliRunner().invoke(nomada.cli, ["push"])
    assert result.exit_code == 1
    assert "unknown key 'bogus'" in result.output
    assert "missing required key 'export'" in result.output


def test_status_lists_stale_and_unmatched(env, monkeypatch):
    m = env.m
    m.save_config(env.cfg)
    m.save_state({"map": {"gone.pdf": "P1", "here.pdf": "P2"}})
    (env.inbox / "here.pdf").write_bytes(PDF)
    (env.export / "notebook.pdf").write_bytes(PDF)
    version = {"success": True, "version": "3.2.3", "capabilities": ["attach_bytes", "write"]}
    monkeypatch.setattr(m.requests, "get", lambda url, timeout: FakeResponse(version))

    result = CliRunner().invoke(m.cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "In flight but no longer in INBOX (1):\n  gone.pdf" in result.output
    assert "PDFs in EXPORT that match no pushed paper (1):\n  notebook.pdf" in result.output
