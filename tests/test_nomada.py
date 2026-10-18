"""Tests for nomada, run against a fake Zotero (local API and Local Write API)."""
import fcntl
import importlib.machinery
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf
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
    """Local API with `items`: parent key -> list of child attachments, all tagged `tag`.

    `records` maps parent keys to the item JSON that `item` returns.
    """

    def __init__(self, items, tag="new", records=None):
        self.items = items
        self.tag = tag
        self.records = records or {}

    def top(self, tag):
        return tag

    def everything(self, tag):
        return [{"key": k} for k in self.items] if tag == self.tag else []

    def children(self, key):
        return self.items[key]

    def item(self, key):
        return self.records[key]


def record(key, title="Robust Vectors", date="2021-03", author="Kanerva"):
    return {"key": key, "meta": {"parsedDate": date},
            "data": {"title": title, "creators": [{"creatorType": "author", "lastName": author}],
                     "extra": "Citation Key: kanerva2021"}}


def annotated_export():
    """A PDF with one highlight and two distant handwriting stamps, as the Nomad exports it."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Hyperdimensional vectors are robust to noise.", fontsize=11)
    page.add_highlight_annot(page.search_for("vectors are robust", quads=True))
    for rect in ((72, 300, 150, 340), (400, 600, 480, 640)):
        stamp = page.add_stamp_annot(pymupdf.Rect(rect))
        stamp.set_name("Supernote_Bitmap")
        stamp.update()
    return doc.tobytes()


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

    def set_items(items, **kwargs):
        monkeypatch.setattr(nomada, "local_zotero", lambda: FakeZotero(items, **kwargs))

    def store(att_key, filename, data=PDF):
        (dirs["storage"] / att_key).mkdir()
        (dirs["storage"] / att_key / filename).write_bytes(data)

    set_items({})
    return SimpleNamespace(m=nomada, cfg=cfg, inbox=dirs["INBOX"], export=dirs["EXPORT"],
                           storage=dirs["storage"], writes=writes, set_items=set_items, store=store)


@pytest.fixture
def notes_env(env, tmp_path):
    env.notes = tmp_path / "vault"
    env.notes.mkdir()
    env.cfg.notes = str(env.notes)
    env.cfg.note_images = str(env.notes / "attachments")
    return env


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


NOTE_NAME = "20210301T090000==refs--kanerva-2021-robust-vectors.md"


def test_pull_writes_note(notes_env):
    env, m = notes_env, notes_env.m
    env.cfg.note_on_pull = True
    env.set_items({}, records={"P1": record("P1")})
    m.save_state({"map": {"a.pdf": "P1"}})
    write_settled(env.export / "a.pdf", annotated_export())

    assert m.run_pull(env.cfg) == 0
    text = (env.notes / NOTE_NAME).read_text()
    assert "> vectors are robust (p. 1)" in text
    assert 'citekey:    "kanerva2021"' in text
    images = sorted(p.name for p in (env.notes / "attachments").iterdir())
    assert images == ["20210301T090000--p1-1.png", "20210301T090000--p1-2.png"]
    assert all(f"![[{name}]]" in text for name in images)


def test_note_skips_existing_unless_forced(notes_env):
    env, m = notes_env, notes_env.m
    child = pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)
    env.set_items({"P1": [child]}, tag="reviewed", records={"P1": record("P1")})
    env.store("A2", "a (annotated).pdf", annotated_export())
    # Denote appends keywords to the name once they are added to a note.
    edited = env.notes / NOTE_NAME.replace(".md", "__hdc.md")
    edited.write_text("my edits")

    assert m.run_note(env.cfg, (), force=False) == 0
    assert edited.read_text() == "my edits"
    assert not (env.notes / NOTE_NAME).exists()

    assert m.run_note(env.cfg, (), force=True) == 0
    assert "> vectors are robust (p. 1)" in edited.read_text()
    assert not (env.notes / NOTE_NAME).exists()


def test_note_reports_missing_annotated_pdf(notes_env):
    notes_env.set_items({"P1": [pdf_child("A1", "a.pdf")]}, tag="reviewed", records={"P1": record("P1")})
    assert notes_env.m.run_note(notes_env.cfg, (), force=False) == 1
    assert list(notes_env.notes.iterdir()) == []


def test_note_identifier_skips_taken_seconds(notes_env):
    env, m = notes_env, notes_env.m
    (env.notes / "20210301T090000==refs--other-2021-paper.md").write_text("")
    child = pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)
    env.set_items({"P1": [child]}, records={"P1": record("P1")})
    env.store("A2", "a (annotated).pdf", annotated_export())
    assert m.run_note(env.cfg, ("P1",), force=False) == 0
    assert (env.notes / NOTE_NAME.replace("T090000", "T090001")).exists()


def test_note_template_rejects_unknown_placeholder(notes_env, tmp_path):
    template = tmp_path / "template.md"
    template.write_text("# {{title}}\n{{abstract}}\n")
    notes_env.cfg.note_template = str(template)
    with pytest.raises(notes_env.m.click.ClickException, match="unknown placeholder"):
        notes_env.m.run_note(notes_env.cfg, (), force=False)


def test_note_requires_date(nomada):
    item = record("P1", date="")
    with pytest.raises(nomada.NoteError, match="no parseable date"):
        nomada.paper_fields(item)


def test_ink_boxes_groups_nearby_marks(nomada):
    mask = np.zeros((200, 200), bool)
    mask[10:20, 10:20] = mask[25:30, 30:40] = True  # one group
    mask[150:160, 150:160] = True                   # another, far away
    assert nomada.ink_boxes(mask, gap=20) == [(10, 10, 39, 29), (150, 150, 159, 159)]


def test_config_rejects_mistyped_flag(nomada):
    nomada.CONFIG_FILE.parent.mkdir(parents=True)
    nomada.CONFIG_FILE.write_text('inbox = "/x"\nexport = "/y"\nnote_on_pull = "yes"\n')
    result = CliRunner().invoke(nomada.cli, ["note"])
    assert result.exit_code == 1
    assert "note_on_pull must be true or false" in result.output
