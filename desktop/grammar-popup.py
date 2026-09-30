#!/usr/bin/python3
"""The suggestion card: what is wrong, every fix the engine offered, and a rephrase on request.

Its own process on purpose. The watcher asks it the way it asks notify-send — payload in, one JSON
line out — so no UI state lands in the daemon and a card that dies cannot take the watcher with it.

**The surface is QML.** It was GTK3, and GTK3 with Breeze is ~90% Breeze's own decisions: every
attempt to design the card came back looking like a stock dialog. QML puts the surface, the chips,
the spacing and the entrance under our control while the colours still come from the desktop's
palette, so the card belongs on this desktop instead of merely appearing on it. This file is the
host: it parses the payload, positions the window, and owns the one network call.

The card, top to bottom: the offending text struck through with the engine's measured time beside
it; the rule's one-line reason; every replacement the engine returned, best-first, the first wearing
the accent; Fix sentence / Copy / Ignore; and — when the watcher sent an engine address and the
sentence — a tone, an intent and Rephrase, whose alternatives appear as flat rows under their own
heading.

**This process makes the rephrase call itself.** It is a local process on loopback and it owns the
interaction, so the few seconds a small model needs are spent here rather than inside the watcher's
ask path, where they would hold every other application's suggestions while the user waited.

Positioning: under Wayland a client cannot choose its own position, so this asks for the X11 backend
before Qt starts (QT_QPA_PLATFORM=xcb, the same trick GDK_BACKEND=x11 was) and sets
BypassWindowManagerHint so the compositor neither moves nor decorates the window. The card never
takes focus, so typing continues while it is up — which is also why Enter and Escape do nothing:
clicking is the interaction, and the notification covers the case where no card can be shown.

Input, one of two ways:

* a JSON object on stdin (what the watcher sends):
      {"old": "teh", "reason": "...", "badge": "Rules engine · 12 ms",
       "alts": ["Teh", "the", "tea", "tech"], "more": "...",
       "api": "http://127.0.0.1:8875", "sentence": "She go to the office."}
* the old argv flags, for a shell: --old --new --reason --badge --more

Either way: --x/--y <px> position it at the caret, --timeout <s> how long it stays up (12).

Prints one JSON line when an action is taken — {"action": "replace", "text": "the"},
{"action": "sentence"}, {"action": "sentence", "text": "<a rephrased line>"}, {"action": "copy"} —
and nothing if it was dismissed or expired. Exits 2 when no card is possible at all (no display, no
Qt) so the caller can fall back to a notification. On stderr: PLACED <x> <y> (asked <x> <y>), so a
test can tell placement from luck.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request

# Before Qt starts: positioning needs the X server, exactly as the GTK card needed GDK_BACKEND=x11.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

# The pure half — findings, the card's contracts, its palette and its two HTTP calls — lives in
# grammar_core, which imports nothing but the standard library. It is re-exported here because
# callers and gates have always reached these names through this module, and because a module
# that needs gi or Qt to import cannot be reasoned about on a machine without them.
#
# sys.path first: the gates load this file by path (spec_from_file_location), which does not put
# its directory on the path, so a plain `import grammar_core` would fail there while working when
# the script is run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grammar_core import (MAX_CANDIDATES, MAX_CHIPS, REPHRASE_TIMEOUT, TONES, INTENTS, DEFAULT_API,
                         action_json, api_error_message, candidates_from, card_colors,
                         clamp, get_json, parse_payload, post_json, rephrase_body,
                         settings_view)




# ---- the pure core: the toolkit does not own any of this ---------------------------------------

# ---- the host -----------------------------------------------------------------------------------

try:
    from PySide6.QtCore import QObject, Qt, QTimer, Slot, QUrl          # noqa: F401
    from PySide6.QtGui import QGuiApplication, QPalette
    from PySide6.QtQml import QQmlApplicationEngine
except Exception as exc:  # noqa: BLE001 - any failure here means "use a notification"
    print("no pop-up: %s" % exc, file=sys.stderr)
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
QML = os.path.join(HERE, "grammar-card.qml")


class Bridge(QObject):
    """What the QML may call: answer, or ask the model. The network stays on this side."""

    def __init__(self, window, payload, app, api_base):
        super().__init__()
        self.window = window
        self.payload = payload
        self.app = app
        self.api_base = api_base.rstrip("/")
        self.chosen = {"action": "", "text": ""}

    @Slot(str, str)
    def choose(self, action, text):
        self.chosen = {"action": action, "text": text}
        self.app.quit()

    @Slot(int, int)
    def rephrase(self, tone_index, intent_index):
        tone = TONES[tone_index] if 0 <= tone_index < len(TONES) else ""
        intent = INTENTS[intent_index] if 0 <= intent_index < len(INTENTS) else ""
        threading.Thread(target=self._work, args=(tone, intent), daemon=True).start()

    def _work(self, tone, intent):
        """Off the UI thread; back on it through the window's own properties, which the QML is
        already bound to. A frozen card is worse than no card."""
        code, response = post_json(self.payload["api"] + "/v2/rewrite",
                                   rephrase_body(self.payload["sentence"], tone, intent))
        candidates = candidates_from(response)
        if candidates:
            self.window.setProperty("candidates", candidates)
            self.window.setProperty("status", "")
        else:
            self.window.setProperty("status", api_error_message(response, code))
        self.window.setProperty("busy", False)

    # ---- the AI runner: the server owns the setting, this panel is its face --------------------
    #
    # Nothing here decides anything. GET /v1/ai already knows the presets, which one is
    # configured, whether it answers, what models it has, and whether this machine may change it;
    # POST /v1/ai validates, applies and saves to ~/.config/grammar-server/ai.json so the choice
    # outlives a restart. Asking for the model list is the same round trip as "is it up", so Test
    # and Save are the only two verbs the panel needs.

    @Slot()
    def loadSettings(self):
        self.window.setProperty("status", "Asking the engine…")
        threading.Thread(target=self._load_settings, daemon=True).start()

    def _load_settings(self):
        code, state = get_json(self.api_base + "/v1/ai")
        if code == 200:
            self.window.setProperty("settings", settings_view(state))
            self.window.setProperty("status", "")
        else:
            self.window.setProperty("status", api_error_message(state, code))

    @Slot(str, str, str, str)
    def saveSettings(self, provider, url, model, key):
        # A save failure must be visible, so the panel says it is working before the round trip.
        self.window.setProperty("status", "Saving…")
        threading.Thread(target=self._save_settings, args=(provider, url, model, key),
                         daemon=True).start()

    def _save_settings(self, provider, url, model, key):
        body = {"provider": provider, "url": url, "model": model}
        # Only when one was typed. An empty field means "leave the stored key alone" — the only
        # safe reading for a password box that never displays what is saved, and the server treats
        # an empty apiKey the same way.
        if key:
            body["apiKey"] = key
        code, state = post_json(self.api_base + "/v1/ai", body)
        if code == 200:
            self.window.setProperty("settings", settings_view(state))
            self.window.setProperty("status", "Saved — this backend outlives a restart.")
        else:
            # The server's own words, verbatim: it distinguishes an unknown provider, a missing
            # model name, an unreachable server and a LAN client that may not write at all.
            self.window.setProperty("status", api_error_message(state, code))


def main():
    args = sys.argv[1:]
    opts = {"x": None, "y": None, "timeout": 0, "api": ""}
    for i, a in enumerate(args):
        key = a.lstrip("-")
        if key in opts and i + 1 < len(args):
            opts[key] = args[i + 1]
    # --settings opens the card as the AI-runner panel: no finding, no payload, no auto-dismiss
    # (a settings panel that vanished after 12 seconds would be a joke), and nothing on stdout when
    # it closes, because a settings panel is not an edit to anything.
    settings_mode = "--settings" in args
    if not settings_mode and not opts["timeout"]:
        opts["timeout"] = 12
    timeout = int(opts["timeout"])

    raw = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except (OSError, UnicodeDecodeError):
            raw = ""
    payload = parse_payload(raw, args)

    app = QGuiApplication(sys.argv[:1])
    # The desktop's own scheme, not a hardcoded one: light or dark comes from the platform palette,
    # and what those mean comes from card_colors(), which is where the colours can be tested.
    colors = card_colors(app.palette().color(QPalette.ColorRole.Window).lightness() < 128)

    engine = QQmlApplicationEngine()
    # Collect the QML errors before loading: Qt normally prints them through its own logger, but a
    # card with a bad property line failed to load here with *nothing* on stderr, and finding out
    # why took a separate probe. A load failure that cannot say why is a bug in the host.
    qml_errors = []
    engine.warnings.connect(lambda errs: qml_errors.extend(errs))
    engine.load(QUrl.fromLocalFile(QML))
    roots = engine.rootObjects()
    if not roots:
        print("no pop-up: the card failed to load", file=sys.stderr)
        for err in qml_errors:
            print("  %s" % err.toString(), file=sys.stderr)
        return 2
    window = roots[0]
    api_base = payload["api"] or opts["api"] or DEFAULT_API
    window.setProperty("payload", payload)
    window.setProperty("colors", colors)
    window.setProperty("view", "settings" if settings_mode else "finding")

    bridge = Bridge(window, payload, app, api_base)
    engine.rootContext().setContextProperty("bridge", bridge)
    if settings_mode:
        bridge.loadSettings()          # the panel asks for its own state; nothing is passed in

    if opts["x"] is not None and opts["y"] is not None or settings_mode:
        def place():
            """Position once the layout has settled, from the platform's own screen list.

            Read too early, width and height are still zero and the clamp would park the card in a
            corner. What the server did is then reported, so a test compares the request with the
            real position instead of with what we hoped for.
            """
            monitors = [(s.geometry().x(), s.geometry().y(),
                         s.geometry().width(), s.geometry().height()) for s in app.screens()]
            if opts["x"] is not None and opts["y"] is not None:
                asked = "%s %s" % (opts["x"], opts["y"])
                x, y = clamp(int(opts["x"]), int(opts["y"]),
                             window.width(), window.height(), monitors)
            else:
                # The settings panel has no caret to sit beside, and an override-redirect window
                # that is never placed lands in the corner: centre it on the primary screen.
                asked = "centred"
                g = app.primaryScreen().geometry()
                x = g.x() + (g.width() - window.width()) // 2
                y = g.y() + (g.height() - window.height()) // 2
            window.setPosition(x, y)
            # The size is reported with the position: a card that maps at 1x1 and positions "fine"
            # is the failure this line exists to make visible, and it happened here once.
            print("PLACED %d %d (asked %s) size %dx%d"
                  % (window.x(), window.y(), asked, window.width(), window.height()),
                  file=sys.stderr, flush=True)
        # The settings panel's height arrives with its state, a round trip later, so it is measured
        # after that rather than at the finding card's 40 ms.
        QTimer.singleShot(300 if settings_mode else 40, place)

    # In settings mode timeout is 0, and a 0 ms timer would close the panel immediately.
    if timeout:
        QTimer.singleShot(timeout * 1000, app.quit)
    app.exec()

    if bridge.chosen["action"]:
        print(action_json(bridge.chosen["action"], bridge.chosen["text"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
