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
# Only --settings needs this: a finding card is told where the engine is by the watcher's payload.
# It mirrors the client's own GRAMMAR_API default, which is where that value is really owned.
DEFAULT_API = os.environ.get("GRAMMAR_API") or "http://127.0.0.1:8875"


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

    def as_count(key):
        """A number the card acts on (only "others" today). Junk is 0, which draws nothing."""
        try:
            return max(0, int(data.get(key) or 0))
        except (TypeError, ValueError):
            return 0

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
            "api": as_text("api").rstrip("/"), "sentence": as_text("sentence"),
            # Every field the watcher sends has to be named here or it never reaches the card:
            # this whitelist is exactly where "others" was dropped, and the QML seam test cannot
            # see that half of the seam.
            "others": as_count("others")}


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


def get_json(url, timeout=REPHRASE_TIMEOUT):
    """GET, with the same tolerance as post_json: the body is another process's."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"message": "cannot reach the engine at %s (%s)" % (url, exc)}


def settings_view(state):
    """What the AI-runner settings panel shows, from the server's GET /v1/ai answer. Pure.

    The server already knows all of it: the presets, which one is configured, whether it answers,
    which models it has, whether a key is present, and whether this machine may change any of it.
    This turns that into exactly what the panel draws, so the panel holds no opinions of its own
    and the same JSON could feed any other surface.
    """
    if not isinstance(state, dict):
        state = {}
    presets = []
    for p in state.get("presets") or []:
        if isinstance(p, dict) and p.get("id"):
            presets.append({"id": str(p["id"]), "label": str(p.get("label") or p["id"]),
                            "url": str(p.get("url") or ""), "model": str(p.get("model") or ""),
                            "hint": str(p.get("hint") or ""), "local": bool(p.get("local")),
                            "keyEnv": str(p.get("keyEnv") or "")})
    # Off is a real choice and belongs last: it is not a preset (there is nothing to configure),
    # but it is how you stop every rephrase without touching a config file.
    presets.append({"id": "none", "label": "Off — no rewriting", "url": "", "model": "",
                    "hint": "Replacing text never calls a model.", "local": True, "keyEnv": ""})

    provider = str(state.get("provider") or "")
    models = [str(m) for m in (state.get("models") or []) if str(m).strip()]
    if state.get("model") and str(state["model"]) not in models:
        models.insert(0, str(state["model"]))     # the configured one is always pickable

    warnings = []
    if state.get("writable") is False:
        warnings.append("Read-only here: the backend can only be changed on the machine the "
                        "server runs on.")
    if provider and provider != "none" and state.get("local") is False:
        warnings.append("Cloud backend: the sentence you rephrase leaves this machine.")
    if state.get("keyEnv") and not state.get("keySet"):
        warnings.append("No %s in the server's environment — rewriting will answer 503 until it "
                        "is set and the server restarted." % state["keyEnv"])

    if state.get("reachable"):
        status = "answering — %d model%s, ready to choose" % (len(models), "" if len(models) == 1 else "s")
    elif provider and provider != "none":
        status = "not answering at %s" % (state.get("url") or "")
    else:
        status = ""
    return {"provider": provider, "url": str(state.get("url") or ""),
            "model": str(state.get("model") or ""), "presets": presets, "models": models,
            "hint": str(state.get("hint") or ""), "warnings": warnings,
            "reachable": bool(state.get("reachable")),
            "writable": state.get("writable") is not False, "status": status}


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

    @Slot(str, str, str)
    def saveSettings(self, provider, url, model):
        # A save failure must be visible, so the panel says it is working before the round trip.
        self.window.setProperty("status", "Saving…")
        threading.Thread(target=self._save_settings, args=(provider, url, model), daemon=True).start()

    def _save_settings(self, provider, url, model):
        code, state = post_json(self.api_base + "/v1/ai",
                                {"provider": provider, "url": url, "model": model})
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
