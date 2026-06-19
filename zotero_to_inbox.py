#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyzotero"]
# ///
"""
Job 1 - Zotero -> SuperNote INBOX  (Zotero 9)

Unchanged from earlier Zotero versions: the local API's ?since= read path and
the ~/Zotero/storage/<KEY>/<file> layout are the same on Zotero 9.

Runs on a timer. Asks the read-only local Zotero API which items changed since
the last run, finds each item's PDF on disk, copies it into the Drive INBOX,
and records  filename -> parent-item-key  so Job 2 can put the annotated copy
back on the right item.

First run only sets a checkpoint and copies nothing, so you don't dump your
entire back-catalogue onto the Nomad.
"""
import json
import shutil
from datetime import datetime

from pyzotero import zotero

import config as C


def log(msg: str) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} [inbox]  {msg}"
    print(line)
    with open(C.LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if C.STATE_FILE.exists():
        return json.loads(C.STATE_FILE.read_text())
    return {"version": None, "map": {}}


def save_state(state: dict) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.STATE_FILE.write_text(json.dumps(state, indent=2))


def main() -> None:
    try:
        zot = zotero.Zotero(C.ZOTERO_LIBRARY_ID, "user", local=True)
        current_version = zot.last_modified_version()
    except Exception as e:
        log(f"local API unreachable (is Zotero running?): {e}")
        return

    state = load_state()
    state.setdefault("map", {})
    C.INBOX.mkdir(parents=True, exist_ok=True)

    # First run: remember where we are, copy nothing.
    if state.get("version") is None:
        state["version"] = current_version
        save_state(state)
        log(f"initialised checkpoint at library version {current_version}; "
            f"papers added from now on will sync")
        return

    since = state["version"]
    try:
        changed = zot.items(since=since)
    except Exception as e:
        log(f"items(since={since}) failed: {e}")
        return

    # Collect parent items to inspect: top-level items that changed, plus the
    # parents of any attachments that changed (covers 'added a PDF later').
    parent_keys = set()
    for it in changed:
        d = it["data"]
        itype = d.get("itemType")
        if itype == "note":
            continue
        if itype == "attachment":
            pk = d.get("parentItem")
            if pk:
                parent_keys.add(pk)
            continue
        parent_keys.add(it["key"])

    copied = 0
    for pk in parent_keys:
        try:
            children = zot.children(pk)
        except Exception as e:
            log(f"children({pk}) failed: {e}")
            continue
        for ch in children:
            cd = ch["data"]
            if cd.get("contentType") != "application/pdf":
                continue
            if cd.get("linkMode") not in ("imported_file", "imported_url"):
                continue
            fname = cd.get("filename")
            if not fname:
                continue
            src = C.ZOTERO_STORAGE / ch["key"] / fname
            dst = C.INBOX / fname
            if not src.exists():
                log(f"stored file missing on disk: {src}")
                continue
            # Always (re)record the mapping so Job 2 can find the parent.
            state["map"][fname] = pk
            if dst.exists():
                continue
            shutil.copy2(src, dst)
            copied += 1
            log(f"-> INBOX  {fname}   (item {pk})")
            break  # one PDF per item is enough

    state["version"] = current_version
    save_state(state)
    if copied:
        log(f"copied {copied} new paper(s) to INBOX")


if __name__ == "__main__":
    main()
