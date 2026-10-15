#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyzotero", "requests"]
# ///
"""
Job 1 - Zotero -> SuperNote INBOX  (Zotero 9, tag-driven)

You queue a paper by tagging it "new" in Zotero. This job:
  1. finds items tagged "new"
  2. copies the original PDF to the Drive INBOX
  3. advances the tag "new" -> "inreview"
  4. records filename -> parent-item-key so Job 2 can find it on the way back

Only "new"-tagged papers are ever touched, so nothing gets sent by accident
(e.g. just from being re-added to a collection).
"""
import json
import shutil
from datetime import datetime

from pyzotero import zotero

import config as C
from writers import set_pipeline_state, WriteError


def log(msg: str) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} [inbox]  {msg}"
    print(line)
    with open(C.LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if C.STATE_FILE.exists():
        return json.loads(C.STATE_FILE.read_text())
    return {"map": {}}


def save_state(state: dict) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.STATE_FILE.write_text(json.dumps(state, indent=2))


def is_annotated(cd: dict) -> bool:
    """True if this attachment is an annotated copy our pipeline created."""
    title = cd.get("title") or ""
    fname = cd.get("filename") or ""
    if title == C.ANNOTATED_TITLE:
        return True
    if fname.startswith("local-write-api-"):
        return True
    suffix = C.ANNOTATED_SUFFIX.strip()
    if suffix and suffix in fname:
        return True
    return False


def main() -> None:
    try:
        zot = zotero.Zotero(C.ZOTERO_LIBRARY_ID, "user", local=True)
        queued = zot.everything(zot.top(tag=C.TAG_NEW))
    except Exception as e:
        log(f"local API unreachable (is Zotero running?): {e}")
        return

    state = load_state()
    state.setdefault("map", {})
    C.INBOX.mkdir(parents=True, exist_ok=True)

    copied = 0
    for it in queued:
        pk = it["key"]
        try:
            children = zot.children(pk)
        except Exception as e:
            log(f"children({pk}) failed: {e}")
            continue

        # Candidate originals = PDFs that are not our own annotated copies.
        originals = [
            ch for ch in children
            if ch["data"].get("contentType") == "application/pdf"
            and ch["data"].get("linkMode") in ("imported_file", "imported_url")
            and ch["data"].get("filename")
            and not is_annotated(ch["data"])
        ]
        if not originals:
            log(f"item {pk} tagged '{C.TAG_NEW}' has no original PDF - skipping")
            continue

        ch = originals[0]
        fname = ch["data"]["filename"]
        src = C.ZOTERO_STORAGE / ch["key"] / fname
        dst = C.INBOX / fname
        if not src.exists():
            log(f"stored file missing on disk: {src}")
            continue

        state["map"][fname] = pk
        if not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
            log(f"-> INBOX  {fname}   (item {pk})")

        # Advance the state tag: new -> inreview.
        try:
            set_pipeline_state(pk, C.TAG_INREVIEW)
        except WriteError as e:
            log(f"tagging '{C.TAG_INREVIEW}' failed for {pk} (non-fatal): {e}")

    save_state(state)
    if copied:
        log(f"copied {copied} new paper(s) to INBOX")


if __name__ == "__main__":
    main()
