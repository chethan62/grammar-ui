# A desktop checker: the selection tool, the typing watcher, and the suggestion card. All of it
# is python and stdlib, so there is nothing to build — install is a copy plus one user unit and
# the launcher for the selection checker. `make test` is the same gate CI runs.
UNITDIR ?= $(HOME)/.config/systemd/user
BINDIR ?= $(HOME)/.local/bin
APPDIR ?= $(HOME)/.local/share/applications

.PHONY: all test install uninstall

all:
	@echo "Nothing to build. Try: make test | make install"

test:
	python3 desktop/test-lookup.py
	python3 desktop/test-watch.py
	python3 desktop/test-popup-place.py
	python3 desktop/test-doctor.py

# Install for the current user: no sudo, and no unit ever references a checkout.
install:
	install -d $(UNITDIR) $(BINDIR) $(APPDIR)
	install -m644 deployments/systemd/grammar-watch.service $(UNITDIR)/grammar-watch.service
	install -m755 desktop/grammar-lookup.py $(BINDIR)/grammar-lookup
	install -m755 desktop/grammar-watch.py $(BINDIR)/grammar-watch
	install -m755 desktop/grammar-popup.py $(BINDIR)/grammar-popup.py
	install -m755 desktop/grammar-doctor.py $(BINDIR)/grammar-doctor
	install -m644 desktop/grammar-card.qml $(BINDIR)/grammar-card.qml
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-lookup.desktop > $(APPDIR)/grammar-lookup.desktop
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-settings.desktop > $(APPDIR)/grammar-settings.desktop
	chmod 644 $(APPDIR)/grammar-lookup.desktop $(APPDIR)/grammar-settings.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
	@echo "Installed $(BINDIR)/grammar-{lookup,watch} and grammar-popup.py, plus the watcher unit."
	@echo "  systemctl --user enable --now grammar-watch   # suggestions as you type, anywhere"
	@echo "The selection checker is bound to Ctrl+Alt+C (see the README); the binding takes"
	@echo "effect at the next login, because kglobalaccel reads its config when it starts."

uninstall:
	-systemctl --user disable --now grammar-watch
	rm -f $(UNITDIR)/grammar-watch.service
	rm -f $(BINDIR)/grammar-lookup $(BINDIR)/grammar-watch $(BINDIR)/grammar-popup.py
	rm -f $(APPDIR)/grammar-lookup.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
