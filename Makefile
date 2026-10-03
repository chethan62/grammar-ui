# A desktop checker: the selection tool and the typing watcher. All of it
# is python and stdlib, so there is nothing to build — install is a copy plus one user unit and
# the launcher for the selection checker. `make test` is the same gate CI runs.
UNITDIR ?= $(HOME)/.config/systemd/user
BINDIR ?= $(HOME)/.local/bin
APPDIR ?= $(HOME)/.local/share/applications
# hicolor's scalable directory is where every theme looks, and the desktop asks for one file instead
# of five: the entries' Icon=grammar-ui resolves to this.
ICONDIR ?= $(HOME)/.local/share/icons/hicolor/scalable/apps

.PHONY: all test check-ui install uninstall

all:
	@echo "Nothing to build. Try: make test | make install"

test: check-app
	python3 desktop/test-lookup.py
	python3 desktop/test-watch.py
	python3 desktop/test-doctor.py
	@/usr/bin/python3 desktop/test-ui.py          # skips unless an app is on the bus; see check-ui

# The end-to-end UI test, driven for real: it clicks the tabs and buttons of the running window and reads
# what the window reports. It needs a display and an app, so this starts one, drives it, and stops it.
# /usr/bin/python3 on purpose, like the watcher unit: pyatspi lives in the system python, and a venv
# python would skip the whole suite instead of running it.
check-ui:
	@/usr/bin/python3 desktop/test-ui.py --start

# The app's own check. It parses the JS, then asks the engine for the two endpoints the UI calls: a
# renamed or removed endpoint leaves a window that says "not answering" and nothing else, and that is
# worth catching before a user does. Skips (does not fail) when no engine is running, because a machine
# without one cannot tell a moved endpoint from a stopped engine. Both halves are ONE recipe line on
# purpose: make gives every line its own shell, so a skip that exits from one line and then probes on the
# next announced itself and failed anyway — green here (the engine runs on this desk) and red in CI.
check-app:
	@for m in shell model rows flow panels app; do node --check app/src/$$m.js || exit 1; done
	@echo "  app: the six modules parse (shell, model, rows, flow, panels, app)"
	node app/check.mjs
	python3 app/check-palette.py
	@api="$${GRAMMAR_API:-http://127.0.0.1:8875}"; \
	if ! curl -sf -o /dev/null "$$api/status" 2>/dev/null; then \
	   echo "  app: skipped the endpoint check (no engine at $$api)"; \
	else \
	   for p in /v1/ai /v2/ignore /v2/languages /v2/dictionary; do \
	     curl -sf -o /dev/null "$$api$$p" && echo "  app: $$p answers" \
	       || { echo "  app: $$p DID NOT ANSWER — the UI calls it"; exit 1; }; \
	   done; \
	   for p in /v2/check /v2/rewrite; do \
	     case "$$p" in \
	       /v2/check) body='{"text":"a","level":"picky"}' ;; \
	       *)         body='{"text":"a","intent":"improve"}' ;; \
	     esac; \
	     code=$$(curl -s -o /dev/null -m 60 -w '%{http_code}' -X POST \
	       -H "Content-Type: application/json" -d "$$body" "$$api$$p"); \
	     case "$$code" in \
	       200) echo "  app: POST $$p answers" ;; \
	       404|400) echo "  app: POST $$p answered $$code — the UI calls it with this payload"; exit 1 ;; \
	       *) echo "  app: POST $$p answered $$code — the route is there, the model was not; skipping" ;; \
	     esac; \
	   done; \
	fi

# Install for the current user: no sudo, and no unit ever references a checkout.
# The mark has ONE source, app/src-tauri/icons/icon.svg, and both this target and install-app install that
# same file. `deployments/grammar-ui.svg` used to be a second, older copy living here, so whichever target
# ran last won and the menu could show a design the app had stopped using — which is exactly what happened.
install:
	install -d $(UNITDIR) $(BINDIR) $(APPDIR) $(ICONDIR)
	install -m644 deployments/systemd/grammar-watch.service $(UNITDIR)/grammar-watch.service
	install -m644 app/src-tauri/icons/icon.svg $(ICONDIR)/grammar-ui.svg
	install -m755 desktop/grammar-lookup.py $(BINDIR)/grammar-lookup
	install -m755 desktop/grammar-watch.py $(BINDIR)/grammar-watch
	install -m755 desktop/grammar-doctor.py $(BINDIR)/grammar-doctor
	install -m755 desktop/grammar-pause.py $(BINDIR)/grammar-pause
	install -m644 desktop/grammar_core.py $(BINDIR)/grammar_core.py
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar-lookup.desktop > $(APPDIR)/grammar-lookup.desktop
	chmod 644 $(APPDIR)/grammar-lookup.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload
	@echo "Installed the clients in $(BINDIR), and the watcher unit with them."
	@echo "  systemctl --user enable --now grammar-watch   # suggestions as you type, anywhere"
	@echo "Shortcuts are yours to choose (System Settings -> Shortcuts), and binding them is a"
	@echo "manual step: this Makefile writes no keys, whatever an older README said. Suggested:"
	@echo "  Ctrl+Alt+C       Check my selection        (grammar --lookup, the same binary)"
	@echo "Or, any time:      grammar-pause 1h|15m|off  # quiet for a while, or back now"
	@echo "A binding takes effect at the next login: kglobalaccel reads its config when it starts."

uninstall:
	-systemctl --user disable --now grammar-watch
	rm -f $(UNITDIR)/grammar-watch.service
	rm -f $(BINDIR)/grammar-pause
	rm -f $(APPDIR)/grammar-lookup.desktop
	rm -f $(ICONDIR)/grammar-ui.svg
	-update-desktop-database $(APPDIR) 2>/dev/null
	-systemctl --user daemon-reload

# The window itself, separate from the clients above because it is a different kind of thing: one binary
# built by Tauri from app/ (which embeds its own assets, so there is nothing to copy beside it) and one menu
# entry. Kept out of `install` on purpose — that one is Python and needs no toolchain, while this needs a
# Rust build that a fresh checkout will not have.
APPBIN = app/src-tauri/target/release/grammar
# Where `Icon=grammar-ui` is looked for. It has to be written by this target: the launcher entry named an
# icon that nothing installed, so the file in there was one an older hand-install had left behind and the
# menu kept showing that design no matter what the repo held. An icon the build does not install is an icon
# that silently drifts from the source.
ICONTHEME = $(HOME)/.local/share/icons/hicolor

install-app:
	@test -x $(APPBIN) || { echo "not built yet — run: cd app && npm run tauri build -- --no-bundle"; exit 1; }
	install -d $(BINDIR) $(APPDIR) $(ICONTHEME)/scalable/apps
	install -m755 $(APPBIN) $(BINDIR)/grammar
	sed 's|@BINDIR@|$(BINDIR)|' deployments/grammar.desktop > $(APPDIR)/grammar.desktop
	chmod 644 $(APPDIR)/grammar.desktop
	install -m644 app/src-tauri/icons/icon.svg $(ICONTHEME)/scalable/apps/grammar-ui.svg
	-update-desktop-database $(APPDIR) 2>/dev/null
	-gtk-update-icon-cache -f -t $(ICONTHEME) 2>/dev/null
	@echo "Installed $(BINDIR)/grammar and its icon — it appears in the menu as \"Grammar\"."

uninstall-app:
	rm -f $(BINDIR)/grammar $(APPDIR)/grammar.desktop $(ICONTHEME)/scalable/apps/grammar-ui.svg
	-update-desktop-database $(APPDIR) 2>/dev/null

# A universal artifact: everything `make install` and `make install-app` put on this machine, as one
# archive, in a ~/.local-shaped tree — so extracting it into ~/.local IS the install. No root, no package
# manager, nothing distro-specific. It carries the window and the clients, NOT the engine: grammar-server
# is a separate project with its own install, and the clients need it running.
DISTVER := $(shell python3 -c "import json;print(json.load(open('app/src-tauri/tauri.conf.json'))['version'])" 2>/dev/null || echo 0.0.0)
DISTNAME := grammar-ui-$(DISTVER)-x86_64
DISTDIR := dist/$(DISTNAME)

dist: $(APPBIN)
	@rm -rf $(DISTDIR)
	@mkdir -p $(DISTDIR)/bin $(DISTDIR)/share/applications $(DISTDIR)/share/icons/hicolor/scalable/apps
	install -m755 $(APPBIN) $(DISTDIR)/bin/grammar
	install -m755 desktop/grammar-lookup.py $(DISTDIR)/bin/grammar-lookup
	install -m755 desktop/grammar-watch.py $(DISTDIR)/bin/grammar-watch
	install -m755 desktop/grammar-doctor.py $(DISTDIR)/bin/grammar-doctor
	install -m755 desktop/grammar-pause.py $(DISTDIR)/bin/grammar-pause
	install -m644 desktop/grammar_core.py $(DISTDIR)/bin/grammar_core.py
	install -m644 app/src-tauri/icons/icon.svg $(DISTDIR)/share/icons/hicolor/scalable/apps/grammar-ui.svg
	install -m644 deployments/systemd/grammar-watch.service $(DISTDIR)/grammar-watch.service
# `Exec=grammar`, not a home-absolute path: the archive is extracted into ~/.local, whose bin is on PATH,
# and an absolute path baked in here would be wrong for every other person who unpacks it.
	sed 's|@BINDIR@/||' deployments/grammar.desktop > $(DISTDIR)/share/applications/grammar.desktop
	sed 's|@BINDIR@/||' deployments/grammar-lookup.desktop > $(DISTDIR)/share/applications/grammar-lookup.desktop
	@printf '%s\n' \
	  "grammar-ui $(DISTVER) — the window and the clients, x86_64 Linux" "" \
	  "Extract into ~/.local; no root, no package manager:" "" \
	  "    tar -C ~/.local -xf $(DISTNAME).tar.gz --strip-components=1" "" \
	  "Suggestions as you type, if you want them:" "" \
	  "    install -Dm644 grammar-watch.service ~/.config/systemd/user/grammar-watch.service" \
	  "    systemctl --user enable --now grammar-watch" "" \
	  "Needs: webkit2gtk-4.1, GTK3, python3, and the engine (grammar-server) running on 127.0.0.1:8875." \
	  "The engine is a separate project and is not in this archive." \
	  > $(DISTDIR)/INSTALL
	@cd dist && tar czf $(DISTNAME).tar.gz $(DISTNAME)
	@echo "Built dist/$(DISTNAME).tar.gz — $$(du -h dist/$(DISTNAME).tar.gz | cut -f1)"
	@find $(DISTDIR) -type f | sort | sed 's|$(DISTDIR)|  |'
