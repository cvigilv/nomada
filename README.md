# nomada: Zotero ⇄ SuperNote Nomad sync (Zotero 9)

`nomada` moves papers between Zotero and a SuperNote Nomad through a synced
folder (Google Drive, Insync, …):

```
new paper in Zotero  ──push──▶  SuperNote/INBOX   ──▶ read + annotate on Nomad
                                                                  │ export
Zotero item (original + annotated)  ◀──pull──  SuperNote/EXPORT ◀┘
```

```
nomada config   # prompt for settings, saved to ~/.config/nomada/config.toml
nomada push     # copy papers tagged "new" to INBOX; tag them "inreview"
nomada pull     # attach annotated PDFs from EXPORT to their items; tag them "reviewed"
nomada sync     # pull, then push
nomada status   # show folders, in-flight papers, tag counts, Zotero connectivity
```

The exported file keeps the **same filename** as the original. `push` records
`filename → parent-item-key`, so `pull` always knows which item the returned
file belongs to.

## Pipeline tags

You queue a paper by tagging it `new` in Zotero. `push` moves the tag to
`inreview` when it copies the PDF out, and `pull` moves it to `reviewed` when
the annotated copy comes back. Only one of these tags is on an item at a time;
your other tags are never touched. The tag names are configurable. Tagging
always goes through the Local Write API add-on (below), whichever write backend
you pick.

## Write backends (`pull`)

**`localwrite` (default; fully local, no credentials)**
Uses the **Local Write API** add-on
(github.com/dzackgarza/zotero-local-write-api). It adds an `/attach` endpoint to
Zotero's local server that takes the PDF as bytes, so nothing leaves your
machine and the annotated file can sit anywhere. You just need Zotero running
with the add-on installed.

> The add-on is only *tested* against Zotero 8.0.1, but its version range is
> open-ended, so it installs on Zotero 9; `nomada status` confirms it loads. It
> registers unauthenticated write endpoints on localhost, which is fine on a
> personal machine but worth knowing.

**`webapi` (needs a Zotero account)**
Uses the official Zotero Web API. Needs sync **on**, your numeric Zotero user
ID, and an API key with library write access
(https://www.zotero.org/settings/keys). Uploaded PDFs use zotero.org storage
quota.

## Setup

1. **Install uv** and make `nomada` available on your `PATH`:
   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ln -s ~/zotero-nomad/nomada ~/.local/bin/nomada
   ```
   `nomada` is a single file that declares its own dependencies, so `uv`
   builds (and caches) its environment on the first run. Python ≥ 3.11 is
   required; uv will fetch one if needed.

2. **Enable the Zotero local API** (needed by `push` and `status`):
   Settings → Advanced → *Allow other applications on this computer to
   communicate with Zotero.*

3. **Install the Local Write API add-on**: download the latest `.xpi` from
   github.com/dzackgarza/zotero-local-write-api/releases, then in Zotero:
   Tools → Plugins → gear icon → *Install Plugin From File…*. Restart Zotero.

4. **Configure**:
   ```sh
   nomada config
   nomada status
   ```
   `status` exits with an error and marks the problem if a folder is missing
   or Zotero or the add-on can't be reached.

5. **Optional: run automatically with launchd.** In both `.plist` files
   replace `USERNAME`, the Drive path in `WatchPaths`, and the `uv` path (run
   `which uv`), then:
   ```sh
   mkdir -p ~/.local/state/nomada
   cp com.user.zotero-nomad-*.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.user.zotero-nomad-inbox.plist
   launchctl load -w ~/Library/LaunchAgents/com.user.zotero-nomad-import.plist
   ```
   The `inbox` agent runs `nomada push` every 3 minutes; the `import` agent
   runs `nomada pull` when EXPORT changes and every 5 minutes as a fallback.
   To stop one: `launchctl unload ~/Library/LaunchAgents/<label>.plist`.

## Files

| what   | where |
|--------|-------|
| config | `$XDG_CONFIG_HOME/nomada/config.toml` (default `~/.config/nomada/`) |
| state  | `$XDG_STATE_HOME/nomada/state.json` (default `~/.local/state/nomada/`) |
| log    | `$XDG_STATE_HOME/nomada/nomada.log` |

The config file is written with owner-only permissions because it can hold an
API key.

## Things to know

- **Errors are reported, not skipped silently.** `push` and `pull` report each
  paper they can't handle (no stored PDF, unknown export, failed attach or
  tag), carry on with the others, and exit with status 1. If Zotero, INBOX, or
  EXPORT is unavailable, they stop immediately with status 1.
- **Zotero must be running** for `push`, for tagging, and for the `localwrite`
  backend. The `webapi` backend can attach while the desktop app is closed, as
  long as the item has synced, but tagging still needs the app.
- **Filenames must be unique** across papers, because `pull` matches by
  filename. `push` refuses to send a paper whose filename matches another
  in-flight paper. Letting Zotero's native citekey drive a rename pattern
  guarantees uniqueness.
- **Deletion is soft.** `pull` moves the INBOX original to `INBOX/_archived` and
  the handled export to `EXPORT/_processed`.
- **Google Drive for desktop in *mirror* mode** is what makes `WatchPaths`
  reliable; streaming mode may not emit local file-system events, which is why
  the `import` agent also runs on a timer.
