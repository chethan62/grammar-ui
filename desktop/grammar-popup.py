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

MAX_CHIPS = 6          # a card, not a menu: the engine's first few are the useful ones
MAX_CANDIDATES = 3     # the model's alternatives, shown as rows in the rephrase section
REPHRASE_TIMEOUT = 90  # a cold local model on this CPU has taken 6s; the server caps it anyway
TONES = ("", "professional", "casual", "formal")
INTENTS = ("", "concise", "clear", "simple")


# ---- the pure core: the toolkit does not own any of this ---------------------------------------

def parse_payload(raw, argv=None):
    """The card's content, from stdin JSON or the argv flags. Pure, so it needs no display.

    Tolerant on purpose: a card that refuses to render because one field is the wrong type is worse
    than a card with a missing line. Unknown keys are ignored, so the watcher may grow the payload
    without breaking an older card.
    """
    data = {}
    if isinstance(raw, dict):
        data = dict(raw)
    elif raw and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                data = loaded
        except ValueError:
            data = {}
    if not data:                                     # a shell invocation, or junk on stdin
        flags = argv or []
        for i, a in enumerate(flags):
            key = a.lstrip("-")
            if key in ("old", "new", "reason", "badge", "more") and i + 1 < len(flags):
                data[key] = flags[i + 1]

    def as_text(key):
        value = data.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    alts = []
    for value in data.get("alts") or []:
        if isinstance(value, str) and value.strip() and value.strip() not in alts:
            alts.append(value.strip())
    # The single --new of the old contract is just the first alternative.
    if not alts and as_text("new"):
        alts = [as_text("new")]
    if alts and not as_text("new"):
        data["new"] = alts[0]
    return {"old": as_text("old"), "new": as_text("new"), "reason": as_text("reason"),
            "badge": as_text("badge"), "more": as_text("more"), "alts": alts[:MAX_CHIPS],
            "api": as_text("api").rstrip("/"), "sentence": as_text("sentence")}


def clamp(x, y, w, h, monitors):
    """Keep the card on the monitor the caret is on; edge carets happen constantly.

    monitors is a list of (x, y, width, height) so this is testable without a display.
    """
    for mx, my, mw, mh in monitors:
        if mx <= x < mx + mw and my <= y < my + mh:
            return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    if monitors:
        # Nothing contains it, which happens for real: a caret can report a negative or
        # beyond-the-edge position (a window partly off-screen, a stale AT-SPI rect). Park the card
        # on the *nearest* monitor and pull it inside, rather than always on the last one —
        # measured: an off-the-top-left caret put the card at 3056,936, the far corner of the other
        # monitor, which is the one place a card is least useful.
        def centre_distance(m):
            mx, my, mw, mh = m
            return (x - (mx + mw / 2)) ** 2 + (y - (my + mh / 2)) ** 2
        mx, my, mw, mh = min(monitors, key=centre_distance)
        return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    return (x, y)


def rephrase_body(sentence, tone="", intent=""):
    """The body for POST /v2/rewrite. Pure: the card's own contract with its server."""
    body = {"text": sentence, "language": "en-US"}
    if tone:
        body["tone"] = tone
    if intent:
        body["intent"] = intent
    return body


def candidates_from(response):
    """The alternatives out of a rewrite response. Pure, and tolerant: that JSON is another
    process's. An error response carries no candidates, only a message worth showing."""
    if not isinstance(response, dict) or response.get("message"):
        return []
    out = []
    for value in response.get("candidates") or []:
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out[:MAX_CANDIDATES]


def api_error_message(response, status=0):
    """Why a call failed, in the server's own words when it has them. Pure."""
    if isinstance(response, dict) and response.get("message"):
        return str(response["message"])
    if status:
        return "the rewrite backend answered HTTP %d" % status
    return "the backend did not answer"


def post_json(url, body, timeout=REPHRASE_TIMEOUT):
    """POST, and read the JSON back either way: an error body is the instruction to show."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"message": "cannot reach the engine at %s (%s)" % (url, exc)}


def action_json(action, text=""):
    """One line the watcher can parse. Pure, and the whole output contract."""
    out = {"action": action}
    if text:
        out["text"] = text
    return json.dumps(out, ensure_ascii=False)


def card_colors(dark):
    """The card's palette. Pure: the host decides light or dark from the desktop, and this decides
    what that means. Keeping the colours here rather than in the QML is what makes them testable."""
    if dark:
        return {"surface": "#1a1f26", "chip": "#242b34", "hover": "#2c343e",
                "border": "#2f3844", "text": "#e7eaee", "muted": "#9aa4b2", "faint": "#6f7a88",
                "accent": "#e8ebef", "accentInk": "#11151a", "accentHover": "#ffffff"}
    return {"surface": "#ffffff", "chip": "#f4f5f7", "hover": "#eef0f3",
            "border": "#e2e5ea", "text": "#14181d", "muted": "#5a6472", "faint": "#8a93a0",
            "accent": "#1c2127", "accentInk": "#ffffff", "accentHover": "#2c333b"}


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

    def __init__(self, window, payload, app):
        super().__init__()
        self.window = window
        self.payload = payload
        self.app = app
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


def main():
    args = sys.argv[1:]
    opts = {"x": None, "y": None, "timeout": 12}
    for i, a in enumerate(args):
        key = a.lstrip("-")
        if key in opts and i + 1 < len(args):
            opts[key] = args[i + 1]
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
    engine.load(QUrl.fromLocalFile(QML))
    roots = engine.rootObjects()
    if not roots:
        # Qt has already printed the QML error. Say it in the watcher's terms, and fall back.
        print("no pop-up: the card failed to load", file=sys.stderr)
        return 2
    window = roots[0]
    window.setProperty("payload", payload)
    window.setProperty("colors", colors)

    bridge = Bridge(window, payload, app)
    engine.rootContext().setContextProperty("bridge", bridge)

    if opts["x"] is not None and opts["y"] is not None:
        def place():
            """Position once the layout has settled, from the platform's own screen list.

            Read too early, width and height are still zero and the clamp would park the card in a
            corner. What the server did is then reported, so a test compares the request with the
            real position instead of with what we hoped for.
            """
            monitors = [(s.geometry().x(), s.geometry().y(),
                         s.geometry().width(), s.geometry().height()) for s in app.screens()]
            x, y = clamp(int(opts["x"]), int(opts["y"]), window.width(), window.height(), monitors)
            window.setPosition(x, y)
            # The size is reported with the position: a card that maps at 1x1 and positions "fine"
            # is the failure this line exists to make visible, and it happened here once.
            print("PLACED %d %d (asked %s %s) size %dx%d"
                  % (window.x(), window.y(), opts["x"], opts["y"], window.width(), window.height()),
                  file=sys.stderr, flush=True)
        QTimer.singleShot(40, place)

    QTimer.singleShot(timeout * 1000, app.quit)
    app.exec()

    if bridge.chosen["action"]:
        print(action_json(bridge.chosen["action"], bridge.chosen["text"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
