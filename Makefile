# The UI is three static files plus one python script: nothing to build, so install is a
# copy plus the user unit and the launcher for the selection checker. `make test` is the
# same gate CI runs.
PORT ?= 8899
DATADIR ?= $(HOME)/.local/share/grammar-ui
UNITDIR ?= $(HOME)/.config/systemd/user
BINDIR ?= $(HOME)/.local/bin
APPDIR ?= $(HOME)/.local/share/applications

.PHONY: all serve test install uninstall

all:
	@echo "Nothing to build — three static files. Try: make serve | make install"

serve:
	python3 -m http.server $(PORT) --bind 127.0.0.1

test:
	node --check app.js
	node test/esc.test.js
	python3 desktop/test-lookup.py
	python3 desktop/test-watch.py

# Install for the current user: no sudo, and the unit never references a checkout.
install:
	install -d $(DATADIR) $(UNITDIR) $(BINDIR) $(APPDIR)
	install -m644 index.html app.js style.css $(DATADIR)/
	install -m644 deployments/systemd/grammar-ui.service $(UNITDIR)/grammar-ui.service
	install -m644 deployments/systemd/grammar-watch.service $(UNITDIR)/grammar-watch.service
	install -m755 desktop/grammar-lookup.py $(BINDIR)/grammar-lookup
	install -m755 desktop/grammar-watch.py $(BINDIR)/grammar-watch
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-lookup.desktop > $(APPDIR)/grammar-lookup.desktop
	chmod 644 $(APPDIR)/grammar-lookup.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
	@echo "Installed $(DATADIR), the user units and $(BINDIR)/grammar-{lookup,watch}. Start it with:"
	@echo "  systemctl --user enable --now grammar-ui"
	@echo "  systemctl --user enable --now grammar-watch   # suggestions as you type, anywhere"
	@echo "The selection checker is bound to Ctrl+Alt+C (see the README); the binding takes"
	@echo "effect at the next login, because kglobalaccel reads its config when it starts."

uninstall:
	-systemctl --user disable --now grammar-ui
	-systemctl --user disable --now grammar-watch
	rm -rf $(DATADIR) $(UNITDIR)/grammar-ui.service $(UNITDIR)/grammar-watch.service
	rm -f $(BINDIR)/grammar-lookup $(BINDIR)/grammar-watch $(APPDIR)/grammar-lookup.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
