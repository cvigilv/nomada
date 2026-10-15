"""
Shared configuration for the Zotero <-> SuperNote (Nomad) automation.
Tested against Zotero 9 (local API read + Web API write).

Edit the paths and pick a write backend, then leave the rest alone.
Anything here can also be overridden with an environment variable.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Zotero - reading (Job 1)
# ---------------------------------------------------------------------------
# Job 1 reads the read-only local API (http://localhost:23119/api) via
# pyzotero local=True. "0" is the local user library and works for this.
ZOTERO_LIBRARY_ID = os.environ.get("ZOTERO_LIBRARY_ID", "0")

# ---------------------------------------------------------------------------
# Zotero - writing (Job 2):  pick "localwrite" (fully local) or "webapi"
# ---------------------------------------------------------------------------
# "localwrite" - the "Local Write API" add-on. Fully local: no account, no API
#                key, no cloud storage. It adds an /attach endpoint to Zotero's
#                local server that takes the PDF as bytes. Just needs Zotero
#                running with the add-on installed. (Confirm it loads on your
#                Zotero with: uv run --with requests writers.py)
# "webapi"     - official Zotero Web API. Needs sync ON, a numeric user id, and
#                an API key with write access; uploads use storage quota.
WRITE_BACKEND = os.environ.get("ZOTERO_WRITE_BACKEND", "localwrite")

# Title + filename given to the annotated copy inside Zotero (both backends).
ANNOTATED_TITLE  = "Annotated (SuperNote)"
ANNOTATED_SUFFIX = " (annotated)"   # appended to the filename; "" keeps the original name

# --- webapi backend only ---
ZOTERO_USER_ID = os.environ.get("ZOTERO_USER_ID", "REDACTED")   # numeric: zotero.org/settings/keys
ZOTERO_API_KEY = os.environ.get("ZOTERO_API_KEY", "REDACTED")   # needs library write access

# ---------------------------------------------------------------------------
# Pipeline state tags  (requires the localwrite add-on)
# ---------------------------------------------------------------------------
# You tag a paper TAG_NEW to queue it for the Nomad. Job 1 then advances it to
# TAG_INREVIEW when it copies it out, and Job 2 advances it to TAG_REVIEWED when
# the annotated copy comes back. Only ONE of these is ever on an item at a time;
# your own topic tags are never touched.
USE_TAGS      = True
TAG_NEW       = "new"
TAG_INREVIEW  = "inreview"
TAG_REVIEWED  = "reviewed"
PIPELINE_TAGS = (TAG_NEW, TAG_INREVIEW, TAG_REVIEWED)

# ---------------------------------------------------------------------------
# Paths  (EDIT THESE)
# ---------------------------------------------------------------------------
# "Google Drive for desktop" mounts here on macOS. Replace the e-mail.
GDRIVE = Path.home() / "Insync/carlos.vigil.v@gmail.com/Google Drive"

INBOX         = GDRIVE / "SuperNote/INBOX"      # papers waiting to be read on the Nomad
EXPORT        = GDRIVE / "SuperNote/EXPORT"      # annotated PDFs come back here
PROCESSED     = EXPORT / "_processed"            # handled exports are moved here
INBOX_ARCHIVE = INBOX  / "_archived"             # INBOX originals are moved here (step 5)

ZOTERO_STORAGE = Path.home() / "Zotero/storage"

# ---------------------------------------------------------------------------
# Internal state (no need to edit)
# ---------------------------------------------------------------------------
STATE_DIR  = Path.home() / ".zotero-nomad"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE   = STATE_DIR / "zotero-nomad.log"
