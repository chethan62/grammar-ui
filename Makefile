# A desktop checker: the selection tool and the typing watcher. All of it
# is python and stdlib, so there is nothing to build — install is a copy plus one user unit and
# the launcher for the selection checker. `make test` is the same gate CI runs.
UNITDIR ?= $(HOME)/.config/systemd/user
BINDIR ?= $(HOME)/.local/bin
APPDIR ?= $(HOME)/.local/share/applications
# hicolor's scalable directory is where every theme looks, and the desktop asks for one file instead
# of five: the entries' Icon=grammar-ui resolves to this.
ICONDIR ?= $(HOME)/.local/share/icons/hicolor/scalable/apps

.PHONY: all test install uninstall

all:
	@echo "Nothing to build. Try: make test | make install"

test: check-app
	python3 desktop/test-lookup.py
	python3 desktop/test-watch.py
	python3 desktop/test-doctor.py

# The app's own check. It parses the JS, then asks the engine for the two endpoints the UI calls: a
# renamed or removed endpoint leaves a window that says "not answering" and nothing else, and that is
# worth catching before a user does. Skips (does not fail) when no engine is running, because a machine
# without one cannot tell a moved endpoint from a stopped engine.
check-app:
	node --check app/src/main.js
	@echo "  app: src/main.js parses"
	@curl -sf -o /dev/null "$${GRAMMAR_API:-http://127.0.0.1:8875}/status" 2>/dev/null || { \
	   echo "  app: skipped the endpoint check (no engine at $${GRAMMAR_API:-http://127.0.0.1:8875})"; exit 0; }
	@for p in /v1/ai /v2/ignore; do \
	   if curl -sf -o /dev/null "$${GRAMMAR_API:-http://127.0.0.1:8875}$$p"; then echo "  app: $$p answers"; \
	   else echo "  app: $$p DID NOT ANSWER — the UI calls it"; exit 1; fi; \
	 done

# Install for the current user: no sudo, and no unit ever references a checkout.
install:
	install -d $(UNITDIR) $(BINDIR) $(APPDIR) $(ICONDIR)
	install -m644 deployments/systemd/grammar-watch.service $(UNITDIR)/grammar-watch.service
	install -m644 deployments/grammar-ui.svg $(ICONDIR)/grammar-ui.svg
	install -m755 desktop/grammar-lookup.py $(BINDIR)/grammar-lookup
	install -m755 desktop/grammar-watch.py $(BINDIR)/grammar-watch
	install -m755 desktop/grammar-doctor.py $(BINDIR)/grammar-doctor
	install -m755 desktop/grammar-action.py $(BINDIR)/grammar-action
	install -m755 desktop/grammar-pause.py $(BINDIR)/grammar-pause
	install -m644 desktop/grammar_core.py $(BINDIR)/grammar_core.py
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-lookup.desktop > $(APPDIR)/grammar-lookup.desktop
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-accept.desktop > $(APPDIR)/grammar-accept.desktop
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-dismiss.desktop > $(APPDIR)/grammar-dismiss.desktop
	chmod 644 $(APPDIR)/grammar-lookup.desktop $(APPDIR)/grammar-accept.desktop \
	          $(APPDIR)/grammar-dismiss.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
	@echo "Installed $(BINDIR)/grammar-{lookup,watch,action} and the watcher unit."
	@echo "  systemctl --user enable --now grammar-watch   # suggestions as you type, anywhere"
	@echo "Shortcuts are yours to choose (System Settings -> Shortcuts), and binding them is a"
	@echo "manual step: this Makefile writes no keys, whatever an older README said. Suggested:"
	@echo "  Ctrl+Alt+C       Check my selection        (grammar-lookup)"
	@echo "  Ctrl+Alt+Return  Accept the suggestion     (grammar-action accept)"
	@echo "  Ctrl+Alt+Escape  Dismiss the suggestion    (grammar-action dismiss)"
	@echo "Or, any time:      grammar-pause 1h|15m|off  # quiet for a while, or back now"
	@echo "A binding takes effect at the next login: kglobalaccel reads its config when it starts."

uninstall:
	-systemctl --user disable --now grammar-watch
	rm -f $(UNITDIR)/grammar-watch.service
	rm -f $(BINDIR)/grammar-action
	rm -f $(BINDIR)/grammar-pause
	rm -f $(APPDIR)/grammar-lookup.desktop
	rm -f $(APPDIR)/grammar-accept.desktop $(APPDIR)/grammar-dismiss.desktop
	rm -f $(ICONDIR)/grammar-ui.svg
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
