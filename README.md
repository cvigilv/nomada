# Zotero ⇄ SuperNote Nomad sync (Zotero 9)

Two small jobs that move papers between Zotero and a SuperNote Nomad through
Google Drive:

```
new paper in Zotero  ──Job 1──▶  Drive/SuperNote/INBOX   ──▶ read + annotate on Nomad
                                                                      │ export
Zotero item (original + annotated)  ◀──Job 2──  Drive/SuperNote/EXPORT ◀┘
```

The exported file keeps the **same filename** as the original. Job 1 records
`filename → parent-item-key`, so Job 2 always knows which item the returned
file belongs to.

## What changed for Zotero 9

- **Job 1 (read) is unchanged.** The local API `?since=` read path and the
  `~/Zotero/storage/<KEY>/` layout are identical on Zotero 9. The local API is
  still **read-only**, which is fine — Job 1 only reads.
- **Job 2 (write) is fully local by default.** It uses the **Local Write API**
  add-on, so no Zotero account or cloud storage is involved. (The debug-bridge
  route I originally planned is broken on Zotero 8/9 and has been dropped.) A
  `webapi` backend is still available if you'd rather use your online account.
- **Native citation keys.** Zotero 8/9 added a built-in citekey field (it
  replaced Better BibTeX's). Doesn't affect these scripts, but it's the easy way
  to keep attachment filenames unique — see *Things to know*.

## Write backends (Job 2)

Set `WRITE_BACKEND` in `config.py`:

**`localwrite`  (default — fully local, no credentials)**
Uses the **Local Write API** add-on
(github.com/dzackgarza/zotero-local-write-api). It adds an `/attach` endpoint to
Zotero's local server that takes the PDF as bytes, so nothing leaves your
machine and the annotated file can sit anywhere (your Drive folder is fine).
No account, no API key, no storage quota. You just need Zotero running with the
add-on installed.

> Heads-up: this add-on is small and only *tested* against Zotero 8.0.1, but its
> version range is open-ended (`strict_max_version: *`), so it installs on
> Zotero 9. Step 4 below has you confirm it actually loads before relying on it.
> It registers unauthenticated write endpoints on localhost — fine on a personal
> machine, but worth knowing.

**`webapi`  (alternative — needs a Zotero account)**
Uses the official Zotero Web API. Needs sync **on**, your numeric `ZOTERO_USER_ID`,
and a `ZOTERO_API_KEY` with library write access
(https://www.zotero.org/settings/keys). Uploaded PDFs use zotero.org storage
quota. Pick this only if you'd rather not install the add-on.

## Setup

1. **Install uv** (if you don't have it) and put the folder in your home dir:
   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   mv ~/Downloads/zotero-nomad ~/zotero-nomad
   ```
   That's the whole install. Each script declares its own dependencies in a
   header block, so `uv run` builds (and caches) the right environment on the
   fly — there's no venv to create and nothing to `pip install`.

2. **Edit `config.py`** — Drive paths (replace the e-mail in the
   `GoogleDrive-…` path), pick `WRITE_BACKEND`, and fill in the credentials for
   the backend you chose.

3. **Enable the Zotero local API** (needed by Job 1)
   Settings → Advanced → *Allow other applications on this computer to
   communicate with Zotero.*

4. **Set up the write backend**
   *Default (`localwrite`):* download the latest `.xpi` from
   github.com/dzackgarza/zotero-local-write-api/releases, then in Zotero:
   Tools → Plugins → gear icon → *Install Plugin From File…* → pick the `.xpi`.
   Restart Zotero. Then confirm it loaded on your Zotero 9:
   ```sh
   cd ~/zotero-nomad
   uv run --with requests writers.py
   ```
   You want to see `OK - ready to attach.` If instead it says "NOT reachable",
   the add-on didn't load — check Tools → Plugins shows it enabled and that
   Zotero is running.

   *Alternative (`webapi`):* turn on Zotero sync, create an API key, and set
   `ZOTERO_USER_ID` + `ZOTERO_API_KEY` in `config.py`.

5. **Install the launch agents**
   In both `.plist` files replace `USERNAME`, the Drive e-mail, and the `uv`
   path (run `which uv` to find it — usually `~/.local/bin/uv`), then:
   ```sh
   cp com.user.zotero-nomad-*.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.user.zotero-nomad-inbox.plist
   launchctl load -w ~/Library/LaunchAgents/com.user.zotero-nomad-import.plist
   ```
   (To stop: `launchctl unload ~/Library/LaunchAgents/<label>.plist`.)

   Prefer cron? One line does the same as Job 1's timer:
   ```
   */3 * * * * cd ~/zotero-nomad && ~/.local/bin/uv run --quiet zotero_to_inbox.py
   ```

6. **Check it's alive**
   ```sh
   cd ~/zotero-nomad
   uv run zotero_to_inbox.py     # first run: prints "initialised checkpoint"
   tail -f ~/.zotero-nomad/zotero-nomad.log
   ```

## Things to know

- **Zotero must be running** for Job 1 (local API) and for the `localwrite`
  backend (the add-on lives inside the app). The `webapi` backend works whether
  or not the desktop app is open, as long as the item has synced. Both jobs
  no-op quietly when they can't reach what they need.
- **Filenames must be unique** across papers — the match relies on it. Letting
  Zotero's native citekey (or Better BibTeX) drive a rename pattern is the clean
  way to guarantee that. Job 1 copies whatever the stored filename is, so the
  round-trip stays consistent; two papers literally named `paper.pdf` collide.
- **Google Drive for desktop in *mirror* mode** is what makes `WatchPaths`
  reliable; streaming/File-Provider mode may not emit local FS events, which is
  why Job 2 also has a 5-minute safety-net timer.
- **Deletion is soft.** Step 5 moves the INBOX original to `INBOX/_archived`.
  To hard-delete instead, see the marked line in `export_to_zotero.py`.
- **First-run flood guard.** Job 1 won't copy your whole library; it starts
  acting only on papers added after the first run.
- **`localwrite` troubleshooting:** if attaching fails, run
  `uv run --with requests writers.py` to check the add-on. `OK - ready to
  attach.` means it's good; "NOT reachable" means the add-on isn't loaded or
  Zotero isn't running. After a Zotero update, re-run this — a major Zotero
  version could in principle stop the add-on loading.

> First time you run a script, uv resolves and caches its dependencies, so that
> run is a few seconds slower; every run after is instant.
