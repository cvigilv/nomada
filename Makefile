BINDIR ?= $(HOME)/.local/bin
SCRIPT := $(CURDIR)/nomada

.PHONY: install uninstall test

# Installs a wrapper rather than a symlink: uv looks for nomada.lock next to
# the path it is given, so the script must be run from its real location.
# The old file is removed first so a symlink to the script is not written
# through.
install:
	@command -v uv >/dev/null || { echo "uv not found; install it from https://docs.astral.sh/uv/" >&2; exit 1; }
	mkdir -p "$(BINDIR)"
	rm -f "$(BINDIR)/nomada"
	printf '#!/bin/sh\nexec uv run --quiet --locked --script "%s" "$$@"\n' "$(SCRIPT)" > "$(BINDIR)/nomada"
	chmod +x "$(BINDIR)/nomada"
	@case ":$$PATH:" in *":$(BINDIR):"*) ;; *) echo "note: $(BINDIR) is not on PATH" >&2 ;; esac

uninstall:
	rm -f "$(BINDIR)/nomada"

test:
	uv run --no-project --with pytest --with click --with pyzotero --with requests --with tomli-w --with pymupdf --with numpy pytest tests/
