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

Positioning, and the two Qt platforms. A Wayland client is not told where other applications are,
so a caret-anchored card cannot be placed natively: the *card* therefore asks for the X11 backend
before Qt starts (QT_QPA_PLATFORM=xcb, the same trick GDK_BACKEND=x11 was) and sets
BypassWindowManagerHint so the compositor neither moves nor decorates it. The card never takes
focus, so typing continues while it is up — which is also why Enter and Escape do nothing:
clicking is the interaction, and the notification covers the case where no card can be shown.

The *settings panel* is a plain window that nobody positions, so it runs on the native platform —
Wayland here — and the compositor places, decorates and moves it. Under XWayland a decorated window
came up with its background painted and no content in it; `choose_platform` is where that split
lives, and it is the only place the backend is chosen.

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

# The Qt platform is chosen in main(), by choose_platform(): it depends on which surface this
# process is (a caret-anchored card needs the X server; a settings window does not) and the env var
# has to be set before QGuiApplication is constructed. Setting it here unconditionally, as this did,
# put a decorated window on XWayland and it came up with no content in it.

# The pure half — findings, the card's contracts, its palette and its two HTTP calls — lives in
# grammar_core, which imports nothing but the standard library. It is re-exported here because
# callers and gates have always reached these names through this module, and because a module
# that needs gi or Qt to import cannot be reasoned about on a machine without them.
#
# sys.path first: the gates load this file by path (spec_from_file_location), which does not put
# its directory on the path, so a plain `import grammar_core` would fail there while working when
# the script is run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grammar_core import (CARET_GAP, KEYBOARD_ACTIONS, MAX_CANDIDATES, MAX_CHIPS, REPHRASE_TIMEOUT,
                         TONES, INTENTS, DEFAULT_API, action_json, ai_note, api_error_message,
                         candidates_from, card_colors, change_summary, clamp, clear_card_action,
                         get_json, parse_payload, post_json, rephrase_body, settings_view,
                         take_card_action)




# ---- the pure core: the toolkit does not own any of this ---------------------------------------

# ---- the host -----------------------------------------------------------------------------------

try:
    from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Slot, QUrl   # noqa: F401
    from PySide6.QtGui import QGuiApplication, QPalette
    from PySide6.QtQml import QQmlApplicationEngine
except Exception as exc:  # noqa: BLE001 - any failure here means "use a notification"
    print("no pop-up: %s" % exc, file=sys.stderr)
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))

# Diagnostics go to stderr, never stdout: stdout is this process's answer channel (one JSON line the
# watcher reads), so a debug print there would be mistaken for an action. Off unless asked for, and
# named like the watcher's switch.
DEBUG = os.environ.get("GRAMMAR_POPUP_DEBUG") == "1"


def debug(*args):
    if DEBUG:
        print("popup:", *args, file=sys.stderr, flush=True)
QML = os.path.join(HERE, "grammar-card.qml")

# Two flag sets, one window at a time. The finding card's is the safe one: override-redirect (so the
# position is ours to set) and no input focus (so the caret stays in the application being typed in —
# WM_HINTS reports "Client accepts input or input focus: False" there, which is the authoritative
# witness: _NET_ACTIVE_WINDOW and `xdotool getwindowfocus` report the card either way and mean
# nothing here).
CARD_FLAGS = (Qt.FramelessWindowHint | Qt.X11BypassWindowManagerHint | Qt.WindowStaysOnTopHint
              | Qt.WindowDoesNotAcceptFocus | Qt.Tool)
# The panel's: the same window *without* the input restriction, because typing an address, a model
# name or an API key is most of what the panel is for. It is still override-redirect — that is the
# only class that renders on this stack.
PANEL_FLAGS = (Qt.FramelessWindowHint | Qt.X11BypassWindowManagerHint | Qt.WindowStaysOnTopHint
               | Qt.Tool)


def choose_platform(_in_settings=False):
    """The Qt platform: the X path, unless someone pinned one.

    The card is placed at the caret from accessibility coordinates, and Wayland does not tell a
    client where another application is — so the UI runs on X (XWayland on a Wayland session, a real
    X server otherwise). The panel is this same window, so it shares the platform; a native-Wayland
    panel would be a second window in a second process, which is what "one UI" rules out.
    """
    return os.environ.get("QT_QPA_PLATFORM") or "xcb"


def motion_factor(raw=None):
    """How fast animations should run here: the desktop's own knob, as a multiplier.

    KDE publishes this as `AnimationDurationFactor` in kdeglobals — 1.0 is normal, 0.25 is a
    quarter duration (this desktop's setting), and 0 means no animations at all, which is what a
    reduced-motion request actually asks for. Scaling by it is more honest than a boolean: a user
    who chose 0.25 should get 30 ms, not "animate exactly as before".

    Qt has no hint for this (checked: QStyleHints in 6.11.2 carries none), and the key is a desktop
    convention rather than a freedesktop one, so this reads the one that exists here. ponytail:
    KDE-only; add the GNOME key (gsettings) or a platform hint if Qt grows one, when a second
    desktop has to be served. An unreadable value means 1.0 — motion is decoration, never worth
    failing a card over.
    """
    if raw is None:
        path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "kdeglobals")
        raw = QSettings(path, QSettings.IniFormat).value("KDE/AnimationDurationFactor")
    try:
        factor = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return min(max(factor, 0.0), 4.0)      # clamped: a stray value must not stall the entrance


def apply_view(window, in_settings):
    """The one place the view, and with it the window's flags, are decided.

    Flags have to change with the view and they cannot be a QML binding (the host assigns them), and
    changing flags on X recreates the native window. A recreate of a *visible* window paints nothing
    afterwards — measured twice: right size, right pid, empty client area — so the change is made
    with the window off screen and it is shown again on the other side, which is the state it starts
    in and the one that renders.
    """
    window.setProperty("view", "settings" if in_settings else "finding")
    flags = PANEL_FLAGS if in_settings else CARD_FLAGS
    if window.flags() != flags:
        was_visible = window.isVisible()
        was_at = (window.x(), window.y())
        window.setVisible(False)
        window.setFlags(flags)
        window.setPosition(was_at[0], was_at[1])
        window.setVisible(was_visible)
    if in_settings:
        # The panel wants the keyboard; the finding view must not have it, which is why the flag
        # above goes back on when this function is called with False.
        window.requestActivate()


class Bridge(QObject):
    """What the QML may call: answer, or ask the model. The network stays on this side."""

    def __init__(self, window, payload, app, api_base):
        super().__init__()
        self.window = window
        self.payload = payload
        self.app = app
        self.api_base = api_base.rstrip("/")
        self.chosen = {"action": "", "text": ""}
        # Whether the configured backend keeps the text on this machine. None until /v1/ai says, and
        # never guessed: ai_note() drops the "where it goes" half rather than assert something.
        self.ai_local = None

    @Slot(str, str)
    def choose(self, action, text):
        self.chosen = {"action": action, "text": text}
        self.app.quit()

    @Slot()
    def openSettings(self):
        """Switch this window to the panel, in place — one UI, one window, one process.

        The panel needs the keyboard and the finding view must not have it, so the flags change with
        the view; see apply_view for why that happens off screen. The ✕ in the panel's own titlebar
        and Escape both come back through choose("", "").
        """
        apply_view(self.window, True)
        self.window.setProperty("status", "")
        self.loadSettings()

    @Slot(float, float)
    def dragWindow(self, dx, dy):
        """Move the window by a delta — ours to do, because nothing manages this window.

        The panel is override-redirect (see apply_view), so there is no window manager to drag it;
        the titlebar MouseArea sends deltas here instead. This is the same authority that puts the
        card at the caret.
        """
        self.window.setPosition(self.window.x() + int(dx), self.window.y() + int(dy))

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
            # What each one changed, so an answer can be judged *before* it is applied: the sentence
            # being replaced is in the application, behind this card, and this is the only place the
            # before and the after are both visible at once.
            self.window.setProperty("changes", [list(change_summary(self.payload["sentence"], text))
                                                for text in candidates])
            # Which model answered, and whether the sentence left the machine: the card is holding
            # text that came back from somewhere, and the row under the button is where it can say so.
            self.window.setProperty("status", self.note_for(response))
        else:
            self.window.setProperty("status", api_error_message(response, code))
        self.window.setProperty("busy", False)
        debug("rephrase done: %d candidates, status %r"
              % (len(candidates), self.window.property("status")))

    def note_for(self, response=None, state=None):
        """The one line about where a rephrase goes: "Rephrase: ollama · qwen2.5:1.5b — …".

        From the rewrite's own answer when there is one, from the engine's /v1/ai otherwise, and
        nothing at all when the engine cannot say. Said before the click as well as after it: the
        question is worth more before sending the sentence than after.
        """
        source = response if isinstance(response, dict) and response.get("provider") else (state or {})
        note = ai_note(source.get("provider"), source.get("model"), self.ai_local,
                       source.get("elapsedMs"))
        return ("Rephrase: " + note) if note else ""

    def note_ai(self):
        """Ask /v1/ai once, so the note above the Rephrase button is there before the click."""
        code, state = get_json(self.api_base + "/v1/ai")
        if code != 200 or not isinstance(state, dict):
            return
        self.ai_local = state.get("local")
        note = self.note_for(state=state)
        # Never over a line that is already saying something (a rephrase in flight, or an error).
        if note and not self.window.property("status"):
            self.window.setProperty("status", note)
        debug("note_ai: local=%r, status %r" % (self.ai_local, self.window.property("status")))

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
    opts = {"x": None, "y": None, "timeout": 0, "api": "", "caret-h": None}
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

    # Before Qt starts (QGuiApplication is what loads the platform plugin): the card needs the X
    # path to be placed at the caret, the settings window takes the native platform. See the
    # function for why the two surfaces differ.
    os.environ["QT_QPA_PLATFORM"] = choose_platform(settings_mode)

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
    # The desktop's animation factor, so the card's entrance follows the same knob the rest of the
    # session does (see motion_factor).
    window.setProperty("motion", motion_factor())
    # sets the view and its flags: the finding view is the card, the panel is that same window.
    apply_view(window, settings_mode)

    bridge = Bridge(window, payload, app, api_base)
    engine.rootContext().setContextProperty("bridge", bridge)
    if not settings_mode and payload.get("api"):
        # Which model would rephrase, and whether the sentence would leave this machine — asked now,
        # in the background, so the answer is on screen *before* the Rephrase button is clicked.
        # Only when there is an engine to ask and a rephrase row to explain (payload["api"] is what
        # canRephrase binds to, so the note and the row appear together or not at all).
        threading.Thread(target=bridge.note_ai, daemon=True).start()
    if not settings_mode:
        # The keyboard route: a shortcut leaves a marker, and this process — the only one that can
        # answer for this card — turns it into the same action a click would. Cleared first, so only
        # presses made while this card is on screen can count, and consumed on use, so one press can
        # never be applied twice.
        clear_card_action()
        keyboard = QTimer()
        keyboard.setInterval(150)

        def take_action():
            verb = take_card_action()
            if verb:
                bridge.choose(*KEYBOARD_ACTIONS[verb])
        keyboard.timeout.connect(take_action)
        keyboard.start()
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
                             window.width(), window.height(), monitors,
                             int(opts["caret-h"] or 0))
            else:
                # The settings panel has no caret to sit beside, and an override-redirect window
                # that is never placed lands in the corner: centre it on the primary screen.
                asked = "centred"
                g = app.primaryScreen().geometry()
                x = g.x() + (g.width() - window.width()) // 2
                y = g.y() + (g.height() - window.height()) // 2
            # The position is ours to set: the window is override-redirect, which is also why the
            # panel has its own drag. The size is reported with it — a card that maps at 1x1 and
            # positions "fine" is the failure this line exists to make visible.
            window.setPosition(x, y)
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
