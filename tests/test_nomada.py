"""Tests for nomada, run against a fake Zotero (local API and Local Write API)."""
import fcntl
import math
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


STAMPS = ((72, 300, 150, 340), (400, 600, 480, 640))  # points
INK, DPI = (213, 94, 0), 150  # handwriting image options for tests
IMAGE_OPTIONS = {"ink_color": "#d55e00", "dpi": DPI}


def annotated_export():
    """A PDF with one highlight and two distant handwriting stamps, as the Nomad exports it."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Hyperdimensional vectors are robust to noise.", fontsize=11)
    page.add_highlight_annot(page.search_for("vectors are robust", quads=True))
    for rect in STAMPS:
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


def test_pull_matches_names_in_any_unicode_form(env):
    m = env.m
    composed, decomposed = "Verg\u00e9s.pdf", "Verge\u0301s.pdf"
    env.set_items({"P1": [pdf_child("A1", composed)]})
    env.store("A1", composed)
    assert m.run_push(env.cfg) == 0

    write_settled(env.export / decomposed, PDF + b"annotated\n%%EOF\n")
    assert m.run_pull(env.cfg) == 0
    (attach,) = attaches(env.writes)
    assert attach["item_key"] == "P1"
    assert [p.name for p in (env.inbox / "_archived").iterdir()] == [composed]
    assert m.load_state()["map"] == {}


def test_state_keys_are_normalized_on_load(env):
    env.m.save_state({"map": {"Verge\u0301s.pdf": "P1"}})
    assert env.m.load_state()["map"] == {"Verg\u00e9s.pdf": "P1"}


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


def test_webapi_attach_returns_attachment_key(env, monkeypatch):
    class FakeWebZotero:
        def __init__(self, *args):
            pass

        def item(self, key):
            return {"key": key}

        def attachment_simple(self, files, parent):
            return {"success": [{"key": "WEBKEY", "filename": Path(files[0]).name}], "failure": [], "unchanged": []}

    import pyzotero.zotero
    monkeypatch.setattr(pyzotero.zotero, "Zotero", FakeWebZotero)
    env.cfg.write_backend = "webapi"
    write_settled(env.export / "a.pdf")
    assert env.m.attach(env.cfg, "P1", env.export / "a.pdf") == "WEBKEY"


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
    assert "> vectors are robust ([p. 1](zotero://open-pdf/library/items/NEWKEY?page=1))" in text
    assert 'citekey:    "kanerva2021"' in text
    assert 'title:      "Kanerva, 2021 - Robust Vectors"\n' in text
    assert "signature:  refs\n" in text
    assert "\n# Kanerva, 2021 - Robust Vectors\n" in text
    images = sorted(p.name for p in (env.notes / "attachments").iterdir())
    assert images == ["20210301T090001==refs--kanerva-2021-robust-vectors-annotation-p1-1.png",
                      "20210301T090002==refs--kanerva-2021-robust-vectors-annotation-p1-2.png"]
    assert all(f"![[{name}]]" in text for name in images)


def test_handwriting_images_color_one_group_each(nomada, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(annotated_export())
    images = nomada.handwriting_images(pdf, **IMAGE_OPTIONS)
    assert [page for page, _, _ in images] == [1, 1]
    scale = DPI / 72
    # Away from the handwriting, each image is the page without highlights.
    plain = pymupdf.open(pdf)[0].get_pixmap(dpi=DPI, annots=False, alpha=False)
    expected = np.frombuffer(plain.samples, np.uint8).reshape(plain.h, plain.w, 3)
    away = np.ones(expected.shape[:2], bool)
    for x0, y0, x1, y1 in STAMPS:
        away[math.floor(y0 * scale) - 2:math.ceil(y1 * scale) + 2, math.floor(x0 * scale) - 2:math.ceil(x1 * scale) + 2] = False
    pad = round(nomada.INK_PAD_PT * scale)
    for (_, box, png), (x0, y0, x1, y1) in zip(images, STAMPS):
        assert x0 - 1 <= box[0] < box[2] <= x1 + 1 and y0 - 1 <= box[1] < box[3] <= y1 + 1
        # The group's box plus the padding, within the page.
        bx0, by0, bx1, by1 = (round(v * scale) for v in box)
        cx0, cy0 = max(bx0 - pad, 0), max(by0 - pad, 0)
        cx1, cy1 = min(bx1 + pad, expected.shape[1]), min(by1 + pad, expected.shape[0])
        pix = pymupdf.Pixmap(png)
        assert (pix.w, pix.h) == (cx1 - cx0, cy1 - cy0)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n).astype(int)
        ys, xs = np.nonzero((rgb == INK).all(axis=2))
        assert len(ys), "the group is in INK, whatever the ink's own color"
        ys, xs = ys + cy0, xs + cx0
        assert x0 * scale - 2 <= xs.min() and xs.max() <= x1 * scale + 2
        assert y0 * scale - 2 <= ys.min() and ys.max() <= y1 * scale + 2
        crop = np.s_[cy0:cy1, cx0:cx1]
        assert (rgb[away[crop]] == expected[crop][away[crop]]).all()


def nomad_stamp(page, alpha):
    """Add handwriting to `page` as the Nomad stores it: a full-page stamp
    drawing a black image whose alpha mask, `alpha`, holds the ink."""
    doc, number = page.parent, page.number
    h, w = alpha.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[:, :, 3] = alpha
    scratch = doc.new_page()
    scratch.insert_image(scratch.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, w, h, rgba.tobytes(), True))
    image = scratch.get_images()[0][0]
    doc.delete_page(scratch.number)
    page = doc[number]  # deleting a page invalidates the others' objects
    stamp = page.add_stamp_annot(page.rect)
    stamp.set_name("Supernote_Bitmap")
    stamp.update()
    form = doc.get_new_xref()
    doc.update_object(form, f"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << /XObject << /I {image} 0 R >> >> >>")
    doc.update_stream(form, b"/I Do")
    doc.xref_set_key(stamp.xref, "AP", f"<< /N {form} 0 R >>")
    # Set by hand: PyMuPDF fits a stamp's rect to the aspect ratio of its text.
    doc.xref_set_key(stamp.xref, "Rect", f"[0 0 {page.rect.width:g} {page.rect.height:g}]")


def test_hidden_ink_groups_but_is_not_drawn(nomada, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    alpha = np.zeros((792, 612), np.uint8)  # one pixel per point
    alpha[300:304, 100:200] = 255  # black
    alpha[310:314, 100:300] = 54   # light gray, beside it
    alpha[600:604, 100:300] = 54   # light gray, alone
    nomad_stamp(page, alpha)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(doc.tobytes())
    scale = DPI / 72
    pad = round(nomada.INK_PAD_PT * scale)

    def blue(png, rows, cols):
        """Whether each pixel of `png` in the page area `rows` x `cols`, in points, is INK."""
        pix = pymupdf.Pixmap(png)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
        y0, x0 = round(rows[0] * scale) - pad + 1, round(cols[0] * scale) - pad + 1
        y1, x1 = round(rows[1] * scale) - pad - 1, round(cols[1] * scale) - pad - 1
        return (rgb[y0:y1, x0:x1] == INK).all(axis=2)

    (_, box, png), (_, alone, png_alone) = nomada.handwriting_images(pdf, hidden_ink="light gray", **IMAGE_OPTIONS)
    assert [round(v) for v in box] == [100, 300, 300, 314]  # widened by the light gray
    assert blue(png, (300, 304), (100, 200)).all()
    assert not blue(png, (310, 314), (100, 300)).any()
    pix = pymupdf.Pixmap(png_alone)
    assert [round(v) for v in alone] == [100, 600, 300, 604]
    assert len(set(pix.samples)) == 1, "an empty page, without the ink"

    (_, _, png), _ = nomada.handwriting_images(pdf, **IMAGE_OPTIONS)
    assert blue(png, (310, 314), (100, 300)).all()


def test_split_ink_keeps_crossing_edges_shown(nomada):
    alpha = np.zeros((9, 9), np.uint8)
    alpha[3:6, :] = 27  # light gray edges
    alpha[4, :] = 54    # light gray
    alpha[:, 3:6] = 48  # black edges, crossing on top
    alpha[:, 4] = 255   # black
    shown, hidden = nomada.split_ink(alpha, 201)
    black = np.zeros_like(alpha)
    black[:, 3:6] = alpha[:, 3:6]
    assert (shown == black).all()
    assert (hidden == alpha - black).all()


def test_annotations_follow_the_text(nomada, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Vectors are robust to noise.", fontsize=11)
    page.insert_text((72, 400), "Graphs need encoders.", fontsize=11)
    page.add_highlight_annot(page.search_for("need encoders", quads=True))
    # In the margin: beside the first line, then beside the highlighted one.
    for rect in ((480, 85, 560, 115), (480, 385, 560, 415)):
        stamp = page.add_stamp_annot(pymupdf.Rect(rect))
        stamp.set_name("Supernote_Bitmap")
        stamp.update()
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(doc.tobytes())
    entries = nomada.read_annotations("test", pdf)
    assert [type(content) for _, content in entries] == [bytes, str, bytes]
    assert entries[1] == (1, "need encoders.")


def test_flow_position_uses_the_nearest_block(nomada):
    left = ((46, 60, 303, 120), [(46, 60, 303, 72), (46, 84, 303, 96), (46, 108, 303, 120)])
    right = ((309, 60, 566, 120), [(309, 60, 566, 72), (309, 84, 566, 96), (309, 108, 566, 120)])
    flow = [left, right]
    assert nomada.flow_position(flow, 590, 90) == (1, 1)  # right margin
    assert nomada.flow_position(flow, 20, 115) == (0, 2)  # left margin
    assert nomada.flow_position(flow, 100, 30) == (0, 0)  # above the text
    assert nomada.flow_position([], 100, 30) == (0, 30)


def test_annotation_name_keeps_keywords_last(nomada):
    note = Path("20210301T090000==refs--kanerva-2021-robust-vectors__hdc_graph.md")
    assert (nomada.annotation_name(note, "20210301T090003", 4, 2)
            == "20210301T090003==refs--kanerva-2021-robust-vectors-annotation-p4-2__hdc_graph.png")


def test_note_skips_existing_unless_forced(notes_env):
    env, m = notes_env, notes_env.m
    child = pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)
    env.set_items({"P1": [child]}, tag="reviewed", records={"P1": record("P1")})
    env.store("A2", "a (annotated).pdf", annotated_export())
    # A renamed note is still found by the Zotero link in its front matter.
    edited = env.notes / "20210301T090000--robust-vectors__hdc.md"
    old_image = "20210301T090005--robust-vectors-annotation-p3-1__hdc.png"
    edited.write_text('---\nzotero: "zotero://select/library/items/P1"\n---\nmy edits\n'
                      f"![[{old_image}]]\n")
    (env.notes / "attachments").mkdir()
    (env.notes / "attachments" / old_image).write_bytes(b"old")
    # A link to the paper outside the front matter does not make a note its note.
    (env.notes / "20240101T090000--other.md").write_text("see zotero://select/library/items/P1\n")

    assert m.run_note(env.cfg, (), force=False) == 0
    assert "my edits" in edited.read_text()
    assert not (env.notes / NOTE_NAME).exists()

    assert m.run_note(env.cfg, (), force=True) == 0
    assert "> vectors are robust ([p. 1](zotero://open-pdf/library/items/A2?page=1))" in edited.read_text()
    assert not (env.notes / NOTE_NAME).exists()
    # The old image is replaced, and the new ones are named after the renamed note.
    assert sorted(p.name for p in (env.notes / "attachments").iterdir()) == [
        "20210301T090001--robust-vectors-annotation-p1-1__hdc.png",
        "20210301T090002--robust-vectors-annotation-p1-2__hdc.png",
    ]


def test_note_takes_keywords_from_manual_tags(notes_env):
    env, m = notes_env, notes_env.m
    item = record("P1")
    item["data"]["tags"] = [{"tag": "HDC"}, {"tag": "Graph Learning", "type": 0}, {"tag": "reviewed"},
                            {"tag": "Computer Science - Machine Learning", "type": 1}]
    child = pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)
    env.set_items({"P1": [child]}, records={"P1": item})
    env.store("A2", "a (annotated).pdf", annotated_export())
    assert m.run_note(env.cfg, ("P1",), force=False) == 0
    note = env.notes / NOTE_NAME.replace(".md", "__graphlearning_hdc.md")
    assert 'tags:       ["graphlearning", "hdc"]\n' in note.read_text()
    assert sorted(p.name for p in (env.notes / "attachments").iterdir())[0] == (
        "20210301T090001==refs--kanerva-2021-robust-vectors-annotation-p1-1__graphlearning_hdc.png")


def test_note_reports_missing_annotated_pdf(notes_env):
    notes_env.set_items({"P1": [pdf_child("A1", "a.pdf")]}, tag="reviewed", records={"P1": record("P1")})
    assert notes_env.m.run_note(notes_env.cfg, (), force=False) == 1
    assert list(notes_env.notes.iterdir()) == []


def test_note_identifiers_skip_taken_seconds(notes_env):
    env, m = notes_env, notes_env.m
    (env.notes / "20210301T090000==refs--other-2021-paper.md").write_text("")
    (env.notes / "attachments").mkdir()
    (env.notes / "attachments" / "20210301T090002==refs--other-2021-paper-annotation-p1-1.png").write_bytes(b"")
    child = pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)
    env.set_items({"P1": [child]}, records={"P1": record("P1")})
    env.store("A2", "a (annotated).pdf", annotated_export())
    assert m.run_note(env.cfg, ("P1",), force=False) == 0
    assert (env.notes / NOTE_NAME.replace("T090000", "T090001")).exists()
    names = sorted(p.name for p in (env.notes / "attachments").iterdir())
    assert names[1:] == ["20210301T090003==refs--kanerva-2021-robust-vectors-annotation-p1-1.png",
                         "20210301T090004==refs--kanerva-2021-robust-vectors-annotation-p1-2.png"]


def test_free_identifiers_carry_into_minutes(nomada):
    start = nomada.datetime(2021, 3, 1, 9, 0, 58)
    assert nomada.free_identifiers(start, 2, {"20210301T090059"}) == ["20210301T090058", "20210301T090100"]


def test_note_template_keeps_literal_text(notes_env, tmp_path):
    env, m = notes_env, notes_env.m
    template = tmp_path / "template.md"
    template.write_text("---\nzotero: {{zotero_link}}\n---\n# {{title}}\n\n## Summary\n\n## Notes\n\n{{annotations}}\n")
    env.cfg.note_template = str(template)
    child = pdf_child("A2", "a (annotated).pdf", title=env.cfg.annotated_title)
    env.set_items({"P1": [child]}, records={"P1": record("P1")})
    env.store("A2", "a (annotated).pdf", annotated_export())
    assert m.run_note(env.cfg, ("P1",), force=False) == 0
    text = (env.notes / NOTE_NAME).read_text()
    assert text.startswith("---\nzotero: zotero://select/library/items/P1\n---\n# Robust Vectors\n\n## Summary\n\n## Notes\n\n> vectors are robust")


def test_note_template_rejects_unknown_placeholder(notes_env, tmp_path):
    template = tmp_path / "template.md"
    template.write_text("# {{title}}\n{{abstract}}\n")
    notes_env.cfg.note_template = str(template)
    with pytest.raises(notes_env.m.click.ClickException, match="unknown placeholder"):
        notes_env.m.run_note(notes_env.cfg, (), force=False)


def test_note_template_requires_zotero_link(notes_env, tmp_path):
    template = tmp_path / "template.md"
    template.write_text("---\ntitle: {{title}}\n---\n{{zotero_link}}\n")
    notes_env.cfg.note_template = str(template)
    with pytest.raises(notes_env.m.click.ClickException, match="must have {{zotero_link}} in its front matter"):
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


def test_config_rejects_unknown_hidden_ink(nomada):
    nomada.CONFIG_FILE.parent.mkdir(parents=True)
    nomada.CONFIG_FILE.write_text('inbox = "/x"\nexport = "/y"\nhidden_ink = "blue"\n')
    result = CliRunner().invoke(nomada.cli, ["note"])
    assert result.exit_code == 1
    assert "hidden_ink must be empty or one of dark gray, light gray, got 'blue'" in result.output


def test_config_rejects_bad_image_options(nomada):
    nomada.CONFIG_FILE.parent.mkdir(parents=True)
    nomada.CONFIG_FILE.write_text('inbox = "/x"\nexport = "/y"\nink_color = "red"\nimage_dpi = true\n')
    result = CliRunner().invoke(nomada.cli, ["note"])
    assert result.exit_code == 1
    assert "image_dpi must be an integer" in result.output
    nomada.CONFIG_FILE.write_text('inbox = "/x"\nexport = "/y"\nink_color = "red"\nimage_dpi = 0\n')
    result = CliRunner().invoke(nomada.cli, ["note"])
    assert "ink_color must be a hex color like #d55e00, got 'red'" in result.output
    assert "image_dpi must be positive, got 0" in result.output


def test_config_rejects_mistyped_flag(nomada):
    nomada.CONFIG_FILE.parent.mkdir(parents=True)
    nomada.CONFIG_FILE.write_text('inbox = "/x"\nexport = "/y"\nnote_on_pull = "yes"\n')
    result = CliRunner().invoke(nomada.cli, ["note"])
    assert result.exit_code == 1
    assert "note_on_pull must be true or false" in result.output
