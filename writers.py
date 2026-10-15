"""
Write backends for attaching the annotated PDF to its Zotero item.

Selected in config.py via WRITE_BACKEND:

  "localwrite" - the "Local Write API" Zotero add-on
                 (github.com/dzackgarza/zotero-local-write-api). Fully local:
                 no account, no key, no cloud. It adds an /attach endpoint to
                 Zotero's local HTTP server that accepts the PDF as base64
                 bytes, so the file can live anywhere (your Drive folder is
                 fine). Just needs Zotero running with the add-on installed.

  "webapi"     - official Zotero Web API (pyzotero). Needs sync enabled, a
                 numeric user id, and an API key with library write access.
                 Uploaded files use zotero.org storage quota.

Run this file directly to probe the add-on (checks it's installed + loads on
your Zotero version):  uv run --with requests writers.py
"""
import base64
from pathlib import Path

import requests

import config as C

LOCAL_BASE = "http://127.0.0.1:23119"


class WriteError(Exception):
    pass


def _annotated_name(file_path: Path) -> str:
    if C.ANNOTATED_SUFFIX:
        return f"{file_path.stem}{C.ANNOTATED_SUFFIX}{file_path.suffix}"
    return file_path.name


def probe(base: str = LOCAL_BASE) -> dict:
    """GET /version on the Local Write API add-on. Returns its info dict."""
    r = requests.get(f"{base}/version", timeout=10)
    r.raise_for_status()
    return r.json()


# --- pipeline state tags (Local Write API /write) ---------------------------

def _post_write(payload: dict) -> dict:
    try:
        r = requests.post(f"{LOCAL_BASE}/write", json=payload, timeout=30)
    except requests.exceptions.ConnectionError as e:
        raise WriteError(f"can't reach Zotero's local server ({e})")
    if r.status_code == 404:
        raise WriteError("/write not found - Local Write API add-on not loaded?")
    r.raise_for_status()
    res = r.json()
    if not res.get("success"):
        raise WriteError(f"write failed: {res}")
    return res


def set_pipeline_state(item_key: str, target: str) -> None:
    """Move an item to exactly one of the pipeline state tags.

    Removes the other pipeline tags and adds `target`. Other (topic) tags on the
    item are left alone. No-op if USE_TAGS is off.
    """
    if not C.USE_TAGS:
        return
    others = [t for t in C.PIPELINE_TAGS if t != target]
    if others:
        _post_write({"operation": "remove_item_tags", "item_key": item_key, "tags": others})
    _post_write({"operation": "add_item_tags", "item_key": item_key, "tags": [target]})


def _write_localwrite(parent_key: str, file_path: Path) -> str:
    payload = {
        "item_key": parent_key,
        "title": C.ANNOTATED_TITLE,
        "file_name": _annotated_name(file_path),
        "file_bytes_base64": base64.b64encode(file_path.read_bytes()).decode("ascii"),
    }
    try:
        r = requests.post(f"{LOCAL_BASE}/attach", json=payload, timeout=120)
    except requests.exceptions.ConnectionError as e:
        raise WriteError(f"can't reach Zotero's local server - is Zotero running? ({e})")

    if r.status_code == 404:
        raise WriteError(
            "/attach not found - the Local Write API add-on isn't installed or "
            "didn't load on this Zotero version. Run: uv run --with requests writers.py"
        )
    r.raise_for_status()

    res = r.json()
    if not res.get("success"):
        raise WriteError(f"attach failed: {res}")
    return res.get("attachment_key", "ok")


def _write_webapi(parent_key: str, file_path: Path) -> str:
    from pyzotero import zotero
    import shutil
    import tempfile

    if not C.ZOTERO_USER_ID or not C.ZOTERO_API_KEY:
        raise WriteError("webapi backend needs ZOTERO_USER_ID and ZOTERO_API_KEY")

    zot = zotero.Zotero(C.ZOTERO_USER_ID, "user", C.ZOTERO_API_KEY)
    try:
        zot.item(parent_key)
    except Exception as e:
        raise WriteError(f"parent {parent_key} not found on server (is it synced?): {e}")

    upload_path = file_path
    tmpdir = None
    if C.ANNOTATED_SUFFIX:
        tmpdir = Path(tempfile.mkdtemp())
        upload_path = tmpdir / _annotated_name(file_path)
        shutil.copy2(file_path, upload_path)
    try:
        res = zot.attachment_simple([str(upload_path)], parent_key)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    if isinstance(res, dict) and res.get("failure"):
        raise WriteError(f"upload failed: {res['failure']}")
    return "ok"


def write_attachment(parent_key: str, file_path) -> str:
    """Attach file_path to the Zotero item parent_key. Returns a status/key string."""
    file_path = Path(file_path)
    if C.WRITE_BACKEND == "localwrite":
        return _write_localwrite(parent_key, file_path)
    if C.WRITE_BACKEND == "webapi":
        return _write_webapi(parent_key, file_path)
    raise WriteError(f"unknown WRITE_BACKEND: {C.WRITE_BACKEND!r}")


if __name__ == "__main__":
    import json
    try:
        info = probe()
    except Exception as e:
        print(f"Local Write API NOT reachable: {e}")
        print("-> Install the add-on and make sure Zotero is running.")
        raise SystemExit(1)
    caps = info.get("capabilities", [])
    print(json.dumps(info, indent=2))
    ok = info.get("success") and "attach_bytes" in caps
    print("\nOK - ready to attach." if ok else "\nReachable, but missing the attach_bytes capability.")
    raise SystemExit(0 if ok else 1)
