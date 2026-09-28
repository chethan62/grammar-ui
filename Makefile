# The UI is three static files: there is nothing to build, so install is a copy plus
# the user unit. `make test` is the same gate CI runs.
PORT ?= 8899
DATADIR ?= $(HOME)/.local/share/grammar-ui
UNITDIR ?= $(HOME)/.config/systemd/user

.PHONY: all serve test install uninstall

all:
	@echo "Nothing to build — three static files. Try: make serve | make install"

serve:
	python3 -m http.server $(PORT) --bind 127.0.0.1

test:
	node --check app.js
	node test/esc.test.js

# Install for the current user: no sudo, and the unit never references a checkout.
install:
	install -d $(DATADIR) $(UNITDIR)
	install -m644 index.html app.js style.css $(DATADIR)/
	install -m644 deployments/systemd/grammar-ui.service $(UNITDIR)/grammar-ui.service
	-systemctl --user daemon-reload
	@echo "Installed $(DATADIR) and the user unit. Start it with:"
	@echo "  systemctl --user enable --now grammar-ui"

uninstall:
	-systemctl --user disable --now grammar-ui
	rm -rf $(DATADIR) $(UNITDIR)/grammar-ui.service
	-systemctl --user daemon-reload
