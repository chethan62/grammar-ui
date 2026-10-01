#!/usr/bin/env python3
"""Gate for the caret pop-up's placement.

The card is its own process, so this gate is too: it starts the real grammar-popup.py, reads
the PLACED line the card prints from the X server, and asserts the card is where the caret
asked for it.

**The witness is the X server, not a picture.** KWin does not implement the wlr-screencopy
protocol — `grim` answers "compositor doesn't support the screen capture protocol" — so no
compositor-side capture exists on this desktop. Under XWayland an X client is placed by the
X server and composited as positioned, so the X server's own geometry is the honest answer to
"did the card land where it was asked to". The card reports it itself, and xdotool is the
second witness. A previous reading of "mapped but absent from the screenshot" came from a run
where the card never launched at all (an unquoted path): nothing was on screen to capture.

Run with the system python (the one with PySide6). The clamp() and palette assertions need no
display and are the part CI can run; the live leg skips itself where there is none.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# The same guard test-watch.py carries, for the same reason: `make test` calls `python3`,
# which is not necessarily the interpreter that has PySide6. Without this the gate spawned the
# card with a python that cannot import Qt, so it never printed PLACED and the live leg
# failed inside make while passing when run by hand with /usr/bin/python3.
try:
    import PySide6  # noqa: F401
except ImportError:  # the agent's own python has no PySide6; the system one does
    if not os.environ.get("GRAMMAR_TEST_REEXEC") and os.path.exists("/usr/bin/python3"):
        os.environ["GRAMMAR_TEST_REEXEC"] = "1"
        os.execv("/usr/bin/python3", ["/usr/bin/python3", os.path.abspath(__file__)] + sys.argv[1:])

HERE = os.path.dirname(os.path.abspath(__file__))
POPUP = os.path.join(HERE, "grammar-popup.py")

PASS = 0
FAIL = 0


def ok(condition, message):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok: %s" % message)
    else:
        FAIL += 1
        print("  FAIL: %s" % message)


def load_popup():
    """The pop-up's own module, for the parts that need no display.

    Its import guards Qt behind a try, so a machine without PySide6 can still test clamp().
    """
    spec = importlib.util.spec_from_file_location("grammar_popup", POPUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clamp(popup):
    """clamp() is the whole of the placement decision, and it needs no display to test."""
    monitors = [(0, 0, 1920, 1080), (1920, 312, 1360, 768)]

    # A point on the second monitor is left exactly alone: this is the normal case, and the
    # one every measured placement in this project has been.
    ok(popup.clamp(2369, 545, 200, 120, monitors) == (2369, 545),
       "an on-screen point is honoured exactly")

    # Off the top-left: pulled inside rather than allowed to hang off.
    x, y = popup.clamp(-40, -40, 200, 120, monitors)
    ok((x, y) == (0, 0), "a point off the top-left is pulled inside (%d,%d)" % (x, y))

    # Past the bottom-right of the last monitor: still inside it.
    x, y = popup.clamp(9999, 9999, 200, 120, monitors)
    ok(1920 <= x <= 3280 - 200 and 312 <= y <= 1080 - 120,
       "a point past the bottom-right is clamped to fit (%d,%d)" % (x, y))

    # A point on no monitor at all: placed on the last one rather than lost.
    x, y = popup.clamp(5000, 5000, 200, 120, monitors)
    ok(0 <= x <= 3280 and 0 <= y <= 1080, "a point on no monitor still lands on one (%d,%d)" % (x, y))

    # No monitor information (a display that reports none): the request stands, unclamped.
    ok(popup.clamp(10, 20, 200, 120, []) == (10, 20),
       "with no monitors reported, the request is not mangled")

    # A caret near the bottom of the screen: the card must not be pulled up over the very line being
    # typed into, so it hangs above the caret instead — using the caret's own height, the one number
    # only the accessibility side can see.
    mon = [(0, 0, 1920, 1080)]
    ok(popup.clamp(400, 300, 400, 320, mon, 20) == (400, 300),
       "a card with room below the caret stays below it")
    ok(popup.clamp(400, 1000, 400, 70, mon, 20) == (400, 1000),
       "and a short card that still fits at the bottom stays there too")
    flipped = popup.clamp(400, 1040, 400, 320, mon, 20)
    ok(flipped == (400, 1040 - 20 - 2 * popup.CARET_GAP - 320),
       "with no room below, the card hangs above the caret: %r" % (flipped,))
    ok(popup.clamp(400, 1040, 400, 320, mon) == (400, 760),
       "without the caret's height nothing is guessed: clamped as before, the old behaviour kept")
    ok(popup.clamp(400, 1040, 400, 320, mon, 1000) == (400, 760),
       "and with no room above either, the clamp is the best that is left")
    # The flip has to use the monitor the caret is on, not the first one that happens to be listed.
    stacked = [(0, 0, 1920, 1080), (1920, 312, 1360, 768)]
    ok(popup.clamp(2000, 1040, 400, 320, stacked, 20) == (2000, 692),
       "the flip uses the caret's own monitor: %r" % (popup.clamp(2000, 1040, 400, 320, stacked, 20),))
    low = [(0, 0, 1920, 1080), (1920, 700, 1360, 380)]
    ok(popup.clamp(2000, 1040, 400, 320, low, 20) == (2000, 760),
       "and where that monitor has no room above, it clamps instead of leaving the screen")


def test_motion(popup):
    """The desktop's animation factor is read as a multiplier, and a bad value never stops a card.

    The host passes it to QML as `motion`, and every duration in the card is scaled by it, so 0
    means no animation and 0.25 means a quarter of it. What matters here is that nothing in the
    chain can throw or hang on a value that is missing, empty or nonsense.
    """
    ok(popup.motion_factor("0.25") == 0.25,
       "the desktop's own factor is honoured: 0.25")
    ok(popup.motion_factor("0") == 0.0,
       "zero means no animation at all")
    ok(popup.motion_factor("") == 1.0,
       "an unset key means normal speed, not zero")
    ok(popup.motion_factor(None) == 1.0 or popup.motion_factor(None) >= 0.0,
       "a bare call reads this desktop without throwing (%r)" % popup.motion_factor(None))
    ok(popup.motion_factor("nonsense") == 1.0,
       "an unparseable value means normal speed")
    ok(popup.motion_factor("-3") == 0.0,
       "a negative factor is pinned to zero, never a negative duration")
    ok(popup.motion_factor("999") == 4.0,
       "a stray huge value is clamped rather than obeyed")


def test_payload(popup):
    """The card's input contract, which is pure: stdin JSON, the old argv flags, and junk."""
    got = popup.parse_payload(
        '{"old": "teh", "reason": "spelling", "alts": ["Teh", "the", "the"], "badge": "12 ms"}')
    ok(got["old"] == "teh" and got["reason"] == "spelling",
       "the payload's fields arrive: %r" % got)
    ok(got["alts"] == ["Teh", "the"],
       "alternatives are de-duplicated and kept in the engine's order: %r" % got["alts"])
    ok(got["new"] == "Teh", "the first alternative fills the older single-fix field: %r" % got)
    ok(popup.parse_payload("nonsense on stdin")["alts"] == [],
       "junk on stdin is not a payload, and is not an error either")
    ok(popup.parse_payload("", ["--old", "go", "--new", "goes", "--reason", "x"])["new"] == "goes",
       "the argv form still works from a shell")
    many = popup.parse_payload('{"alts": [%s]}' % ", ".join('"a%d"' % i for i in range(12)))
    ok(len(many["alts"]) == popup.MAX_CHIPS,
       "twelve alternatives are capped to a card, not a menu: %d" % len(many["alts"]))
    # The whitelist in parse_payload is half the payload seam, and the half the QML test cannot
    # see: "others" was added to the watcher and the card, and silently dropped here in between.
    ok(popup.parse_payload('{"others": 2}')["others"] == 2,
       "a count the watcher sends survives the whitelist")
    ok(popup.parse_payload('{"others": "junk"}')["others"] == 0,
       "and junk in it is zero, which draws nothing rather than raising")
    ok(popup.parse_payload("{}")["others"] == 0, "a card with no count at all is zero")
    # The same trap, one field later: the pause button is drawn from payload.app, so a payload that
    # carries the application must arrive with it — and one that does not must be empty, which
    # hides the button rather than naming an application the host never identified.
    ok(popup.parse_payload('{"app": "Firefox"}')["app"] == "Firefox",
       "the application the card is shown in reaches the card's payload")
    ok(popup.parse_payload('{"app": "  "}')["app"] == "",
       "and whitespace is no application at all")
    ok(popup.parse_payload("{}")["app"] == "", "a card with no application has none")
    # The same trap a third time, and the reason the comment above it keeps growing: the card's
    # "Ignore this word" button is drawn from payload.word, so the field has to survive this whitelist
    # or the button simply never appears.
    ok(popup.parse_payload('{"word": "zorbulating"}')["word"] == "zorbulating",
       "the word the finding is about reaches the card")
    ok(popup.parse_payload("{}")["word"] == "", "and a card with no word has none, which hides the button")
    # The GTK card shipped Pango markup and had to escape the document's text by hand; the QML
    # card renders plain text, so that escaping is gone by construction. What is left to check is
    # the palette it is rendered in: complete in both schemes, and legible in both.
    def contrast(fg, bg):
        def lum(h):
            parts = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            parts = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
            return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
        a, b = lum(fg), lum(bg)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)

    for dark in (False, True):
        scheme = "dark" if dark else "light"
        colours = popup.card_colors(dark)
        ok(len(colours) >= 10 and all(v.startswith("#") and len(v) == 7 for v in colours.values()),
           "the %s card palette is complete: %d colours" % (scheme, len(colours)))
        ok(contrast(colours["text"], colours["surface"]) >= 4.5,
           "its text is legible on its surface: %.1f:1"
           % contrast(colours["text"], colours["surface"]))
        ok(contrast(colours["accentInk"], colours["accent"]) >= 4.5,
           "the primary chip's label is legible on the accent: %.1f:1"
           % contrast(colours["accentInk"], colours["accent"]))
    ok(popup.card_colors(True)["surface"] != popup.card_colors(False)["surface"],
       "and the two schemes are genuinely different")
    ok(popup.action_json("replace", "the") == '{"action": "replace", "text": "the"}',
       "and the answer is one JSON line: %r" % popup.action_json("replace", "the"))
    ok(popup.action_json("copy") == '{"action": "copy"}',
       "a copy answer carries no text: %r" % popup.action_json("copy"))

    # The rephrase the card now makes itself: the request it builds, and how it reads the answer.
    ok(popup.rephrase_body("Fine.", "professional", "concise")
       == {"text": "Fine.", "language": "en-US", "tone": "professional", "intent": "concise"},
       "the rephrase body carries the sentence, the tone and the intent")
    ok(popup.rephrase_body("Fine.") == {"text": "Fine.", "language": "en-US"},
       "and leaves tone and intent out when none is chosen: %r" % popup.rephrase_body("Fine."))
    ok(popup.candidates_from({"candidates": ["A.", "B.", "A.", "  "], "model": "m"}) == ["A.", "B."],
       "the model's alternatives come back de-duplicated and trimmed: %r"
       % popup.candidates_from({"candidates": ["A.", "B.", "A.", "  "]}))
    ok(popup.candidates_from({"message": "rewrite backend unavailable"}) == [],
       "an error response carries no candidates, only a message")
    ok("unavailable" in popup.api_error_message({"message": "rewrite backend unavailable"}, 503),
       "and what is shown is the server's own words")
    ok("HTTP 503" in popup.api_error_message({}, 503),
       "with the status as the fallback: %r" % popup.api_error_message({}, 503))


def test_settings(popup):
    """The AI-runner panel's view of GET /v1/ai. Pure, so it runs headless.

    A settings panel must render when the backend is down — that is exactly when someone opens it —
    so every state the server can report is asserted here, including the ones that only produce a
    warning line.
    """
    state = {
        "provider": "ollama", "url": "http://127.0.0.1:11434", "model": "qwen2.5:1.5b",
        "protocol": "ollama", "local": True, "hint": "0.9s warm", "keySet": False,
        "reachable": True, "models": ["qwen2.5:1.5b", "qwen3.5:4b"], "writable": True,
        "presets": [
            {"id": "ollama", "label": "Ollama", "url": "http://127.0.0.1:11434",
             "model": "qwen2.5:1.5b", "local": True, "hint": "h"},
            {"id": "lmstudio", "label": "LM Studio", "url": "http://127.0.0.1:1234",
             "model": "", "local": True, "hint": "h"},
            {"id": "openrouter", "label": "OpenRouter", "url": "https://openrouter.ai/api/v1",
             "model": "", "local": False, "keyEnv": "OPENROUTER_API_KEY", "hint": "h"},
        ],
    }
    view = popup.settings_view(state)
    ok([p["id"] for p in view["presets"]] == ["ollama", "lmstudio", "openrouter", "none"],
       "every preset is offered and Off is last: %r" % [p["id"] for p in view["presets"]])
    ok(view["provider"] == "ollama" and view["url"].endswith("11434"),
       "the configured backend and its address are shown")
    ok(view["models"] == ["qwen2.5:1.5b", "qwen3.5:4b"], "the models it reports are offered")
    ok(view["reachable"] and "2 models" in view["status"],
       "the status says it is answering: %r" % view["status"])
    ok(view["warnings"] == [], "and a healthy local backend warns about nothing: %r" % view["warnings"])

    # A configured model the server no longer lists must stay selectable, or the panel would open
    # showing a model nobody can keep.
    gone = popup.settings_view({"provider": "ollama", "model": "old:1b", "reachable": True,
                                "models": ["new:2b"], "writable": True})
    ok(gone["models"][0] == "old:1b" and "new:2b" in gone["models"],
       "a configured model the server no longer lists stays pickable: %r" % gone["models"])

    down = popup.settings_view({"provider": "lmstudio", "url": "http://127.0.0.1:1234",
                                "writable": True})
    ok(not down["reachable"] and "not answering" in down["status"] and down["models"] == [],
       "a backend that is down still renders, and says so: %r" % down["status"])

    cloud = popup.settings_view({"provider": "openrouter", "local": False, "writable": True,
                                 "keyEnv": "OPENROUTER_API_KEY", "keySet": False})
    ok(any("leaves this machine" in w for w in cloud["warnings"]),
       "a cloud backend warns that the text leaves: %r" % cloud["warnings"])
    ok(any("OPENROUTER_API_KEY" in w and "503" in w for w in cloud["warnings"]),
       "and a missing key names the variable and its cost: %r" % cloud["warnings"])

    lan = popup.settings_view({"provider": "ollama", "writable": False})
    ok(lan["writable"] is False and any("Read-only" in w for w in lan["warnings"]),
       "a request from another machine renders read-only: %r" % lan["warnings"])

    # The key, which a custom OpenAI-compatible endpoint needs. The panel never shows the value —
    # the server reports only whether it has one — so what matters here is that the panel says
    # which state it is in, and never claims the key is missing when one is set.
    with_key = popup.settings_view({"provider": "openai", "url": "http://x/v1", "model": "m",
                                    "keyEnv": "OPENAI_API_KEY", "keySet": True, "writable": True})
    ok(with_key["needsKey"] and with_key["keySet"], "a key-needing runner reports that it has one")
    ok("saved" in with_key["keyNote"] and not with_key["warnings"],
       "so the key note says saved and nothing warns about a missing key: %r / %r"
       % (with_key["keyNote"], with_key["warnings"]))
    without = popup.settings_view({"provider": "openai", "keyEnv": "OPENAI_API_KEY",
                                   "keySet": False, "writable": True})
    ok(not without["keySet"] and any("OPENAI_API_KEY" in w and "below" in w
                                     for w in without["warnings"]),
       "no key yet points at the field that fixes it: %r" % without["warnings"])
    local = popup.settings_view({"provider": "ollama", "reachable": True, "writable": True})
    ok(not local["needsKey"], "a local runner does not ask for a key at all")

    # A custom OpenAI-compatible server often has no /v1/models, so an empty model list is not the
    # same fault as nothing answering. Calling it "not answering" would send someone hunting for a
    # server that is running perfectly well.
    custom = popup.settings_view({"provider": "openai", "url": "http://127.0.0.1:8099",
                                  "model": "m", "reachable": False, "writable": True})
    ok(custom["tone"] == "warn" and "not answering" not in custom["status"]
       and "normal for a custom endpoint" in custom["status"],
       "a custom endpoint with no model list is unverified, not dead: %r" % custom["status"])
    others = [popup.settings_view(s)["tone"] for s in (
        {"provider": "ollama", "reachable": True, "writable": True},
        {"provider": "lmstudio", "reachable": False, "writable": True},
        {"provider": "none", "writable": True})]
    ok(others == ["good", "bad", "idle"],
       "and the pill's tone distinguishes working, not answering and off: %r" % others)

    # Where the key is, as the panel must state it: the engine reports the source, and the note has
    # to follow it rather than describing a file that may not be the one holding the key.
    ok(popup.settings_view({"provider": "openai", "keyEnv": "OPENAI_API_KEY", "keySet": True,
                            "keySource": "keyring", "writable": True})["keyNote"]
       == "a key is saved in your desktop keyring (OPENAI_API_KEY in the environment overrides it)",
       "a keyring key is described as a keyring key")
    ok("0600 file" in popup.settings_view({"provider": "openai", "keyEnv": "OPENAI_API_KEY",
                                           "keySet": True, "keySource": "file",
                                           "writable": True})["keyNote"],
       "a file key is still described as a file, for the machine where that is true")
    ok(popup.settings_view({"provider": "openai", "keyEnv": "OPENAI_API_KEY", "keySet": True,
                            "keySource": "env", "writable": True})["keyNote"]
       == "the key comes from OPENAI_API_KEY in the environment",
       "an environment key says so, and promises nothing else")
    ok("0600" not in popup.settings_view({"provider": "openai", "keyEnv": "OPENAI_API_KEY",
                                          "keySet": True, "keySource": "keyring",
                                          "writable": True})["keyNote"],
       "and a keyring key never claims to be a file")
    # An engine with no keySource field at all (an older one): the sentence that was true then.
    ok(popup.settings_view({"provider": "openai", "keyEnv": "OPENAI_API_KEY", "keySet": True,
                            "writable": True})["keyNote"]
       == "a key is saved (OPENAI_API_KEY in the environment overrides it)",
       "an older engine still gets an honest note")

    empty = popup.settings_view({})
    ok(empty["presets"][-1]["id"] == "none" and empty["status"] == "" and empty["models"] == [],
       "an empty state still yields a usable panel: %r" % empty)
    ok(popup.settings_view(None)["provider"] == "", "and None is not a crash either")

    # The seam, same rule as the payload: every key the view produces is one the panel draws.
    with open(os.path.join(HERE, "grammar-card.qml")) as fh:
        qml = fh.read()
    for key in ("presets", "models", "provider", "url", "hint", "warnings", "status",
                "tone", "keySet", "needsKey", "keyNote"):
        ok('s("%s"' % key in qml, "the panel draws the '%s' the view produces" % key)
    ok("card.s(\"model\"" in qml and "writable" in qml,
       "and the model field and the read-only flag reach the panel")


def test_provider_note(popup):
    """Where a rephrase goes, said about the model that answers — and never guessed.

    A card that says "nothing leaves this machine" over a cloud backend is the worst lie this product
    could tell, so the wording is asserted here rather than trusted to the panel and the card agreeing
    by luck.
    """
    ok(popup.ai_note("ollama", "qwen2.5:1.5b", True)
       == "ollama · qwen2.5:1.5b — nothing leaves this machine",
       "a local backend says so in the engine's own words: %r" % popup.ai_note("ollama", "m", True))
    ok(popup.ai_note("openrouter", "gpt-4o-mini", False)
       == "openrouter · gpt-4o-mini — what you rephrase leaves this machine",
       "and a cloud one says the other thing, not nothing")
    ok(popup.ai_note("ollama", "qwen2.5:1.5b", True, 1713)
       == "ollama · qwen2.5:1.5b · 1713 ms — nothing leaves this machine",
       "how long the answer took is part of the sentence, from the rewrite's own response")
    ok(popup.ai_note("ollama", "", True) == "ollama — nothing leaves this machine",
       "a backend with no model named still says where the text goes")
    # The one thing this line must never do: imply locality it has not been told. /v1/ai may be
    # unreachable, and then the card names the backend and stops talking about where text goes.
    ok(popup.ai_note("openrouter", "gpt-4o-mini", None) == "openrouter · gpt-4o-mini",
       "an unknown `local` says the model and drops the claim: %r" % popup.ai_note("x", "y", None))
    ok(popup.ai_note("", "qwen", True) == "", "and with no backend at all there is no sentence")
    ok(popup.ai_note(None, None, True) == "", "not even from None")


def test_provider_seam(popup, base):
    """The host asks /v1/ai and puts the answer on the card, before the Rephrase button is clicked.

    The seam, not the wording: a status line is set either way, so only reading it back proves the
    question was asked and the answer parsed. Skips where no engine answers — the pure check above
    still covers what the line says.
    """
    class FakeWindow:
        def __init__(self):
            self.props = {}

        def setProperty(self, key, value):
            self.props[key] = value

        def property(self, key):
            return self.props.get(key)

    window = FakeWindow()
    bridge = popup.Bridge(window, {"api": base, "sentence": "We are zorbulating."}, None, base)
    bridge.note_ai()
    status = window.property("status") or ""
    if not status:
        print("  seam: skipped (no engine at %s to ask)" % base)
        return
    ok(status.startswith("Rephrase: "), "the note names what it is about: %r" % status)
    ok(bridge.ai_local is True, "and it learned where the text goes from the engine, not by guessing")
    ok("leaves this machine" in status, "so the sentence is complete: %r" % status)

    # After a rephrase, the provenance comes from the rewrite's own response — including the model
    # that actually answered, which is the only thing that knows.
    ok(bridge.note_for({"provider": "ollama", "model": "qwen2.5:1.5b", "elapsedMs": 1713})
       == "Rephrase: ollama · qwen2.5:1.5b · 1713 ms — nothing leaves this machine",
       "the result line carries the model and the time")
    ok(bridge.note_for({"message": "no backend"}) == "",
       "and an error response says nothing about a model, leaving the error to the error path")


def test_live():
    """The card in a real process, placed for real, reporting from the X server.

    Opt-in through GRAMMAR_LIVE=1. This puts a real window on the real screen, and running the
    gate while the desktop is in use sprayed cards at the person using it. The pure assertions
    above are the gate; this is the measurement, taken when the placement code changes.
    """
    if not os.environ.get("GRAMMAR_LIVE"):
        print("  live: skipped (GRAMMAR_LIVE=1 puts a real card on screen and measures where)")
        return
    if not os.environ.get("DISPLAY"):
        print("  live: skipped (no DISPLAY)")
        return
    xdotool = shutil.which("xdotool")
    if not xdotool:
        print("  live: skipped (no xdotool, so no second witness)")
        return

    x, y = 2369, 545      # a real caret position measured in LibreOffice on this box
    errfile = os.path.join("/tmp", "grammar-popup-gate.err")
    if os.path.exists(errfile):
        os.remove(errfile)

    # stderr to a file rather than a pipe: the card prints PLACED 400 ms in and then waits, and
    # communicate() would wait for it to exit instead of reading what it already said.
    with open(errfile, "wb") as err:
        proc = subprocess.Popen(
            [sys.executable, POPUP, "--x", str(x), "--y", str(y),
             "--old", "go", "--new", "goes", "--reason", "Subject-verb agreement",
             "--badge", "Rules engine · 12 ms", "--timeout", "20"],
            stdout=subprocess.DEVNULL, stderr=err)
    try:
        time.sleep(3)
        placed = ""
        try:
            with open(errfile) as fh:
                placed = fh.read()
        except OSError:
            pass

        ok("PLACED" in placed, "the card reported where it was placed: %r" % placed.strip()[:80])
        m = re.search(r"PLACED (\d+) (\d+)", placed)
        if not m:
            ok(False, "the PLACED line carries coordinates")
            return
        px, py = int(m.group(1)), int(m.group(2))
        ok((px, py) == (x, y),
           "placed exactly where the caret asked: asked %d,%d got %d,%d" % (x, y, px, py))

        # A card that maps at 1x1 and positions "fine" is not a card. Qt was measured doing exactly
        # that — the window existed at 3x3 while the layout was still settling — so the size is
        # asserted rather than assumed.
        ms = re.search(r"size (\d+)x(\d+)", placed)
        ok(bool(ms) and int(ms.group(1)) > 200 and int(ms.group(2)) > 80,
           "and the card has a card's size: %s" % (ms.group(0) if ms else "none reported"))

        # Second witness: the X server's own view of the same window, searched by the pop-up's
        # own pid rather than by its title. Measured: with a second grammar card on screen (a
        # manual 120s one at 700,300) the title search found *that* window and this gate reported
        # "the window is at 700,300" against its own card's 2369,545. The watcher may well have a
        # card up while make test runs, so the gate must identify its own and nothing else.
        ids = subprocess.run([xdotool, "search", "--pid", str(proc.pid)],
                             capture_output=True, text=True).stdout.split()
        ok(bool(ids), "the card has an X window (%d found, own pid %d)" % (len(ids), proc.pid))
        # GTK gives the card more than one X window (measured: 2 for a single pop-up) and
        # xdotool's order is not guaranteed, so ask whether *any* of this pid's windows sits at
        # the asked spot. Reading ids[0] passed on this box by luck of ordering.
        positions = []
        for wid in ids:
            geo = subprocess.run([xdotool, "getwindowgeometry", wid],
                                 capture_output=True, text=True).stdout
            g = re.search(r"Position: (\d+),(\d+)", geo)
            if g:
                positions.append((int(g.group(1)), int(g.group(2))))
        ok((x, y) in positions,
           "xdotool agrees one of the card's %d windows is at %d,%d: %r"
           % (len(ids), x, y, positions))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        # No cleanup search here: searching by title would have closed nothing anyway (xdotool
        # search does not dismiss a window), and while a card was up it picked the wrong one.


def test_keyboard(popup):
    """The marker a shortcut leaves, and the card that consumes it.

    The card can never take the keyboard, so this little file is the entire interface between a
    shortcut and the card's answer — which means it has to stay safe when the marker is stale, when
    the verb is a typo, and when one press could otherwise be counted twice.
    """
    import grammar_core                     # the pop-up puts its own directory on sys.path as it loads
    path = os.path.join(tempfile.mkdtemp(prefix="grammar-action-"), "card-action")
    ok(grammar_core.take_card_action(path) is None, "no marker is no action")
    ok(grammar_core.write_card_action("accept", path) is True, "a shortcut can leave one")
    ok(grammar_core.take_card_action(path) == "accept", "and the card reads it")
    ok(grammar_core.take_card_action(path) is None,
       "reading it consumes it, so one press cannot be applied twice")
    ok(grammar_core.write_card_action("dismiss", path) is True
       and grammar_core.take_card_action(path) == "dismiss", "dismiss is a verb too")
    ok(grammar_core.write_card_action("nonsense", path) is False,
       "a typo in a shortcut writes nothing at all")
    ok(not os.path.exists(path), "and leaves no file behind")
    with open(path, "w") as fh:
        fh.write("nonsense\n")
    ok(grammar_core.take_card_action(path) is None, "a verb that is not ours is dropped, not acted on")
    grammar_core.write_card_action("accept", path)
    grammar_core.clear_card_action(path)
    ok(grammar_core.take_card_action(path) is None,
       "and a card clears a stale marker as it starts, so only its own presses count")
    # The card answers through the very functions the gate just tested, and the mapping is the
    # contract between the two: accept is the primary action the card already offers, dismiss is the
    # empty answer every dismissal looks like.
    ok(popup.take_card_action is grammar_core.take_card_action
       and popup.clear_card_action is grammar_core.clear_card_action,
       "the card's process uses the same reader the tests exercise")
    ok(popup.KEYBOARD_ACTIONS == {"accept": ("sentence", ""), "dismiss": ("", "")},
       "and the verbs map to what a click sends: %r" % (popup.KEYBOARD_ACTIONS,))


def test_live_keyboard():
    """A real card, the real command a shortcut runs, and the answer the watcher would act on.

    Opt-in like the placement leg, and for the same reason: it puts a card on screen. XDG_CACHE_HOME
    points at a temporary directory for both processes, so the marker written here is never the one a
    real key press would use.
    """
    if not os.environ.get("GRAMMAR_LIVE"):
        print("  live keyboard: skipped (GRAMMAR_LIVE=1 puts a real card on screen)")
        return
    if not os.environ.get("DISPLAY"):
        print("  live keyboard: skipped (no DISPLAY)")
        return
    cache = tempfile.mkdtemp(prefix="grammar-action-live-")
    env = dict(os.environ, XDG_CACHE_HOME=cache)
    command = os.path.join(HERE, "grammar-action.py")
    try:
        for verb, want in (("accept", '{"action": "sentence"}'), ("dismiss", "")):
            proc = subprocess.Popen(
                [sys.executable, POPUP, "--x", "300", "--y", "300", "--old", "go", "--new", "goes",
                 "--reason", "Subject-verb agreement", "--timeout", "20"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            try:
                time.sleep(2.5)          # Qt up, the stale marker cleared, the poll running
                run = subprocess.run([sys.executable, command, verb], env=env,
                                     capture_output=True, text=True, timeout=10)
                ok(run.returncode == 0, "grammar-action %s exits 0" % verb)
                out, _ = proc.communicate(timeout=15)
                ok(out.decode().strip() == want,
                   "a %s press answers the card with exactly %r, got %r"
                   % (verb, want, out.decode().strip()))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        # A verb that is not ours: refused, and it leaves nothing for a card to pick up.
        bad = subprocess.run([sys.executable, command, "wat"], env=env,
                             capture_output=True, text=True, timeout=10)
        ok(bad.returncode == 2, "an unknown verb is refused rather than guessed at: %r"
           % bad.stderr.strip())
        ok(not os.path.exists(os.path.join(cache, "grammar-server", "card-action")),
           "and a refused verb leaves no marker")
    finally:
        shutil.rmtree(cache, ignore_errors=True)


def main():
    try:
        popup = load_popup()
    # SystemExit included deliberately: with PySide6 absent - exactly what a CI runner without the
    # Qt step looks like - the pop-up's own import guard prints "no pop-up: ..." and exits 2. That
    # is SystemExit, which `except Exception` does not catch, and the gate died with exit 2 inside
    # CI while passing on a desktop with Qt.
    except (Exception, SystemExit) as exc:
        print("  clamp: skipped (the pop-up cannot run here: %s)" % exc)
        popup = None
    if popup is not None:
        test_clamp(popup)
        test_motion(popup)
        test_payload(popup)
        test_keyboard(popup)
        test_settings(popup)
        test_provider_note(popup)
        # The seam needs an engine to ask, and skips itself where there is none — the same shape as
        # the live leg below, minus the screen.
        test_provider_seam(popup, os.environ.get("GRAMMAR_API") or "http://127.0.0.1:8875")
    test_live()
    test_live_keyboard()
    # A gate that can pass having run nothing is not a gate. This one reported "0 assertions -
    # passed" in CI once, with a green tick, on a runner where the card's module could not even be
    # imported. If nothing ran, that is the finding.
    if PASS + FAIL == 0:
        ok(False, "the gate ran no assertions at all - the pop-up's module could not be loaded")
    print("grammar-popup-place: %d assertions - %s" % (PASS + FAIL, "passed" if not FAIL else "FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
