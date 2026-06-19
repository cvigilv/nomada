#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyzotero", "requests"]
# ///
"""
Job 2 - SuperNote EXPORT -> Zotero  (Zotero 9)

For every annotated PDF that lands in the Drive EXPORT folder:
  1. look up its parent item (filename -> key map written by Job 1)
  2. attach it as a SECOND child of that item, next to the original,
     using the backend chosen in config.py (Web API by default)
  3. move the matching INBOX original into INBOX/_archived   (step 5)
  4. move the handled export into EXPORT/_processed

The annotated file keeps the same name as the original, which is what lets the
filename map resolve the parent without any guessing.
"""
import json
import shutil
from datetime import datetime

import config as C
from writers import write_attachment, WriteError


def log(msg: str) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} [import] {msg}"
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
    if not C.EXPORT.exists():
        log(f"EXPORT folder not found: {C.EXPORT}")
        return

    C.PROCESSED.mkdir(parents=True, exist_ok=True)
    C.INBOX_ARCHIVE.mkdir(parents=True, exist_ok=True)

    state = load_state()
    fmap = state.get("map", {})

    for pdf in sorted(C.EXPORT.glob("*.pdf")):
        name = pdf.name
        parent_key = fmap.get(name)
        if not parent_key:
            log(f"no parent mapping for '{name}' - left in EXPORT. "
                f"(Did it go to the Nomad via INBOX? Are filenames unique?)")
            continue

        try:
            result = write_attachment(parent_key, pdf)
        except WriteError as e:
            log(f"attach failed for '{name}': {e}")
            continue
        except Exception as e:
            log(f"attach error for '{name}': {e}")
            continue

        log(f"attached '{name}' -> item {parent_key}  ({C.WRITE_BACKEND}: {result})")

        # Step 5: the original is now redundant in the INBOX. Archive it.
        # To hard-delete instead, replace the move() below with: inbox_file.unlink()
        inbox_file = C.INBOX / name
        if inbox_file.exists():
            shutil.move(str(inbox_file), str(C.INBOX_ARCHIVE / name))
            log(f"archived INBOX original: {name}")

        shutil.move(str(pdf), str(C.PROCESSED / name))
        fmap.pop(name, None)

    state["map"] = fmap
    save_state(state)


if __name__ == "__main__":
    main()
