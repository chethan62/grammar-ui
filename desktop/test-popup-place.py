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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

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
       == {"text": "Fine.", "language": "en-US", "stream": True, "tone": "professional",
           "intent": "concise"},
       "the rephrase body carries the sentence, the tone, the intent and asks to stream")
    ok(popup.rephrase_body("Fine.") == {"text": "Fine.", "language": "en-US", "stream": True},
       "and leaves tone and intent out when none is chosen, while still asking to stream: %r"
       % popup.rephrase_body("Fine."))
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


def test_change_summary(popup):
    """The hint that says what a rephrase changed — the only place before and after sit together.

    The sentence being replaced is in the application behind the card, so without this line the only
    way to judge an answer is to apply it and undo. Each case here is a way the hint can mislead: by
    inventing a change that is only a capital, by missing one, or by letting a four-word cap read as
    if that were all of it.
    """
    ok(popup.change_summary("We are zorbulating the report.", "We are reviewing the report.")
       == ("zorbulating", "reviewing"), "a swapped word is one gone and one arrived")
    ok(popup.change_summary("It is very important that we do so.", "It is crucial that we do so.")
       == ("very, important", "crucial"),
       "a replaced phrase reports every word that went, comma-separated: %r"
       % (popup.change_summary("It is very important that we do so.", "It is crucial that we do so."),))
    # Punctuation rides with its word: the token is "late." and when the full stop stays behind the
    # diff says so, which is truer than pretending the words alone moved.
    ok(popup.change_summary("We are late.", "We are late today.") == ("late.", "late, today."),
       "an addition that re-punctuates reports both halves: %r"
       % (popup.change_summary("We are late.", "We are late today."),))
    ok(popup.change_summary("We are late today.", "We are late.") == ("late, today.", "late."),
       "and so does the reverse")
    ok(popup.change_summary("We are late.", "We are late.") == ("", ""),
       "an unchanged sentence reports no change at all")
    ok(popup.change_summary("we are late.", "We are late.") == ("", ""),
       "and neither does a capital the model added at the start")
    ok(popup.change_summary("one two", "one two three four five six", limit=3)
       == ("", "three, four, five …"),
       "the cap keeps the first three words and says there were more: %r"
       % (popup.change_summary("one two", "one two three four five six", limit=3),))
    removed, added = popup.change_summary("the quick fox", "the fox quick")
    ok(removed and added, "a sentence the model reordered names the moved word on both sides, because "
       "it did move: %r" % ((removed, added),))
    ok(popup.change_summary("", "") == ("", ""), "two empty sentences change nothing")
    ok(popup.change_summary(None, None) == ("", ""), "not even from None")


def test_the_card_is_given_the_changes(popup):
    """The host computes one diff per answer, in the same breath as the answers themselves.

    They have to arrive together: a candidate list that grew while its diffs did not would either
    render an answer with no hint, or — worse, and harder to notice — the *previous* answer's hint
    under the new text.
    """
    class FakeWindow:
        def __init__(self):
            self.props = {}

        def setProperty(self, key, value):
            self.props[key] = value

        def property(self, key):
            return self.props.get(key)

    window = FakeWindow()
    sentence = "It is very important that we do so."
    bridge = popup.Bridge(window, {"api": "http://127.0.0.1:9", "sentence": sentence}, None,
                          "http://127.0.0.1:9")
    real_post = popup.stream_rewrite
    popup.stream_rewrite = lambda url, body, on_delta=None, **kw: (
        200, {"candidates": ["It is crucial that we do so.", "We must do so now."],
              "provider": "ollama", "model": "m", "elapsedMs": 12})
    try:
        bridge._work("", "concise")
    finally:
        popup.stream_rewrite = real_post

    changes = window.property("changes")
    ok(len(changes) == 2, "one diff per answer, and both answers are there: %r" % (changes,))
    ok(list(changes[0]) == ["very, important", "crucial"],
       "the first answer's own change: %r" % (changes[0],))
    ok(changes[1] != changes[0] and changes[1][1],
       "and the second one's, not the first's, repeated: %r" % (changes[1],))
    ok(all(len(pair) == 2 for pair in changes), "each diff is a removed half and an added half")
    ok(window.property("candidates") == ["It is crucial that we do so.", "We must do so now."],
       "and the answers themselves are unchanged by any of this")


def test_stream_reader(popup):
    """The rewrite stream, parsed: the words as they arrive, then the answer.

    Which kind of line it is must be decided by which key is present, never by position or count — a
    backend that ignores the stream flag answers in one body, and that body has to be accepted. Each
    case here is a shape that decision can be got wrong on.
    """
    events = list(popup.read_stream(['{"delta": "We are "}', '{"delta": "reviewing."}',
                                     '{"candidates": ["We are reviewing."], "model": "m"}']))
    ok(events[0] == ("delta", "We are ") and events[1] == ("delta", "reviewing."),
       "each delta comes through in order: %r" % (events,))
    ok(events[2][0] == "done" and events[2][1]["candidates"] == ["We are reviewing."],
       "and the answer ends it: %r" % (events[2],))
    ok(len(events) == 3, "with nothing after the answer: %r" % (events,))

    # A backend that ignored `stream`: one body, one line, still an answer.
    one = list(popup.read_stream(['{"candidates": ["We are reviewing."], "model": "m"}']))
    ok(len(one) == 1 and one[0][0] == "done",
       "a single-body reply is read as the answer, not as a delta with no end: %r" % (one,))

    # Blank lines are framing, not content.
    ok(list(popup.read_stream(["", "   ", '{"candidates": []}']))[0][0] == "done",
       "blank lines are skipped rather than mistaken for an answer")

    fail = list(popup.read_stream(['{"delta": "We are "}', '{"message": "rewrite timed out"}']))
    ok(fail[-1] == ("error", "rewrite timed out"),
       "the server's own sentence is what a failure reports: %r" % (fail[-1],))

    # Not skipped: a line that is neither is a broken frame, and a half-sentence shown as an answer
    # is worse than a failure.
    for junk in ("<html>502</html>", '["not", "an", "object"]'):
        got = list(popup.read_stream([junk]))
        ok(got and got[0][0] == "error", "junk line %r is an error: %r" % (junk, got))
    ok(list(popup.read_stream(["", ""]))[0][0] == "error",
       "a stream that ends without an answer is an error, not an empty success")


def test_streaming_rewrite(popup):
    """The streaming call itself, against a server that speaks the shape the engine sends.

    The cancel case is the one worth having: the plan asks for a Cancel button, and the only thing that
    makes it work is the reader noticing between lines and dropping the connection — which is also what
    stops the model at the other end.
    """
    import http.server

    def serve(lines, delay=0.0):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for line in lines:
                    try:
                        self.wfile.write(("%s\n" % line).encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        # The client stopped reading and dropped the connection — which is exactly what
                        # the cancel case below is testing, and why this is caught instead of printed.
                        return
                    if delay:
                        time.sleep(delay)

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, "http://127.0.0.1:%d" % srv.server_address[1]

    srv, url = serve(['{"delta": "We are "}', '{"delta": "reviewing."}',
                      '{"candidates": ["We are reviewing."], "model": "m", "provider": "ollama"}'])
    try:
        seen = []
        status, final = popup.stream_rewrite(url, {"text": "x"}, seen.append)
        ok(status == 200 and final.get("candidates") == ["We are reviewing."],
           "the answer comes back like post_json's does: %r" % (final,))
        ok(seen == ["We are ", "reviewing."], "and the words were handed over as they arrived: %r" % (seen,))
    finally:
        srv.shutdown()

    srv, url = serve(['{"delta": "one"}', '{"delta": "two"}', '{"delta": "three"}',
                      '{"candidates": ["three"]}'], delay=0.05)
    try:
        seen = []
        status, final = popup.stream_rewrite(url, {"text": "x"}, seen.append,
                                             should_stop=lambda: len(seen) >= 1)
        ok(final.get("message") == "cancelled", "a stop between lines ends the read: %r" % (final,))
        ok(len(seen) == 1, "and does not wait for the rest of the stream: %r" % (seen,))
    finally:
        srv.shutdown()

    srv, url = serve(['{"message": "rewrite backend unavailable (ollama at http://x) — start it"}'])
    try:
        _status, final = popup.stream_rewrite(url, {"text": "x"})
        ok("unavailable" in (final.get("message") or ""),
           "a failure after the status line arrives as a message: %r" % (final,))
    finally:
        srv.shutdown()

    got = popup.stream_rewrite("http://127.0.0.1:9/v2/rewrite", {"text": "x"})
    ok(got[0] == 0 and "cannot reach" in got[1].get("message", ""),
       "and an engine that is not there reads the same way post_json's failure does: %r" % (got,))


def test_hostile_input_from_the_engine(popup):
    """The readers that take another process's JSON, fed shapes that process should never send.

    The engine is the one input these functions do not control — and if someone points the panel at a
    proxy, whatever answers on that port. A number where a list belongs is that process's bug; what
    matters here is that it is not *our* crash, and in one case that it cannot become our lie. Every
    case below is one a sweep found or a reading of the code predicted.
    """
    # candidates_from: not a list at all used to raise, or worse, yield a dict's keys as alternatives.
    for value in (7, 1.5, True, "abc", {"a": 1}, None):
        try:
            got = popup.candidates_from({"candidates": value})
            ok(got == [], "candidates=%r yields nothing rather than a crash or a key: %r"
               % (value, got))
        except Exception as exc:
            ok(False, "candidates=%r raised %s: %s" % (value, type(exc).__name__, exc))
    ok(popup.candidates_from({"candidates": ["ok", None, 3, ""]}) == ["ok"],
       "and inside a real list only real strings survive")

    # read_stream: framing that is broken but still JSON.
    events = list(popup.read_stream(['{"delta": 5}', '{"candidates": 7}']))
    ok(events[0] == ("delta", "5"), "a number where words belong is read as text: %r" % (events,))
    ok(events[-1] == ("done", {"candidates": 7}),
       "and the stream ends on the answer key, whatever it holds: %r" % (events[-1],))

    # ai_note: a wrong-typed `local` must drop the claim rather than assert one.
    for value in ("true", 1, [], {}, 0, "yes"):
        got = popup.ai_note("ollama", "m", value)
        ok("leaves this machine" not in got,
           "local=%r is not a boolean, so nothing is claimed about where text goes: %r" % (value, got))
    ok(popup.ai_note(5, None, True) == "5 — nothing leaves this machine",
       "a number where a backend name belongs is text, not a crash")
    ok(popup.ai_note("ollama", "m", True, "fast") == "ollama · m — nothing leaves this machine",
       "and a non-numeric duration is left out rather than rounded into nonsense")
    ok(popup.ai_note("ollama", "m", True, 12.4).endswith("· 12 ms — nothing leaves this machine"),
       "while a real one is still shown: %r" % popup.ai_note("ollama", "m", True, 12.4))


def test_a_background_failure_is_not_silent(popup):
    """A thread that dies quietly is how a card sits on "Rephrasing…" forever.

    Four paths run off the UI thread (the rephrase, the note, loading and saving the panel), so one
    wrapper catches them all: whatever goes wrong becomes a sentence on the card with the button back.
    The test drives the wrapper directly, with a body that raises, because that is the seam.
    """
    class FakeWindow:
        def __init__(self):
            self.props = {}

        def setProperty(self, key, value):
            self.props[key] = value

        def property(self, key):
            return self.props.get(key)

    window = FakeWindow()
    bridge = popup.Bridge(window, {"api": "http://127.0.0.1:9", "sentence": "x"}, None,
                          "http://127.0.0.1:9")
    window.props["busy"] = True

    def explode():
        raise RuntimeError("the engine answered something odd")

    thread = bridge._in_background(explode)
    ok(thread is not None, "the wrapper hands back the thread, so a caller can wait for it")
    thread.join(timeout=10)
    ok(not thread.is_alive(), "and the body ran to completion, exception and all")
    status = window.property("status") or ""
    ok(status.startswith("That did not work:"), "the failure is on the card: %r" % status)
    ok("the engine answered something odd" in status,
       "carrying the exception's own words, which is the only useful part: %r" % status)
    ok(window.property("busy") is False,
       "and the card is usable again rather than stuck mid-rephrase: %r" % window.property("busy"))

    quiet = bridge._in_background(lambda: None)
    quiet.join(timeout=10)
    ok((window.property("status") or "").startswith("That did not work:"),
       "while a body that works leaves whatever the last message was alone")


def test_settings_holds_everything(popup):
    """The panel is the one window for every setting, so its view must carry them all.

    Each comes from a different owner — the engine's ignore list, this client's blocklist, a pause
    timestamp — and two of them had no surface at all before: a word could be added from a card and only
    a text editor could take it back, and "Ignore in <application>" had the same problem. What this pins
    is that the view carries them, sorted, and with nothing dropped: the cap that used to hide the
    seventh row is gone, because the panel scrolls now.
    """
    eight = ["Zorbulating", "kanban", "tea", "Tech", "zebra", "alpha", "beta", "gamma"]
    view = popup.settings_view({"provider": "ollama", "url": "u", "model": "m", "words": eight,
                                "pausedApps": ["Kate", "Firefox"], "pauseUntil": 0})
    ok(len(view["words"]) == len(eight),
       "every word is the panel's to draw, not just the first six: %d" % len(view["words"]))
    ok(view["words"] == ["alpha", "beta", "gamma", "kanban", "tea", "Tech", "zebra", "Zorbulating"],
       "sorted the way a person reads a list: %r" % view["words"])
    ok(view["pausedApps"] == ["Firefox", "Kate"], "the paused applications: %r" % view["pausedApps"])
    ok(view["pauseNote"] == "not paused", "an expired pause reads as none: %r" % view["pauseNote"])
    # The list height is the host's measurement of the screen, not a taste: on a 768-tall display the
    # panel must give way somewhere, and the lists are the only part that can.
    ok(view["listMax"] == 132, "with no measurement the designed height stands: %r" % view["listMax"])
    ok(popup.settings_view({"listMax": 90})["listMax"] == 90,
       "and a short screen's smaller budget is carried through")
    ok(popup.settings_view({"listMax": 5})["listMax"] == 60,
       "clamped to something still scrollable rather than to nothing")
    short = popup.list_max_for(768)
    ok(short == 104, "a 768-px screen means %d-px lists, so the footer still fits" % short)
    ok(popup.list_max_for(1080) == 132 and popup.list_max_for(2400) == 132,
       "a tall screen is capped at the designed height, not stretched")

    paused = popup.settings_view({"pauseUntil": time.time() + 3600})
    ok(paused["pauseNote"].startswith("paused for another"),
       "a real pause says how long: %r" % paused["pauseNote"])
    ok(popup.settings_view({})["words"] == [] and popup.settings_view({})["pausedApps"] == [],
       "and a panel with none of it still has the keys the QML reads")

    # The other half of the seam, the one that would go unnoticed: a key renamed on the view side is a
    # blank row on the panel, and nothing else in the gates would say a word about it. The reversed
    # check matters just as much — a key the QML still reads after the view stopped sending it is a
    # note that renders as the word "undefined".
    qml = open(os.path.join(HERE, "grammar-card.qml")).read()
    for key in ("words", "pausedApps", "pauseNote", "listMax"):
        ok(('"%s"' % key) in qml,
           "the panel draws %r, so renaming either side cannot pass silently" % key)
    for gone in ("wordsMore", "wordsPath"):
        ok(('"%s"' % gone) not in qml,
           "and nothing still reads %r, which the view no longer sends" % gone)


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


def test_live_a_pressed_button_says_the_same_thing_as_the_key():
    """The card's buttons, pressed for real — the one route the keyboard leg does not cover.

    test_live_keyboard drives the card through grammar-action.py, which is what a shortcut runs, and
    asserts the line the watcher parses. It never presses a button: the QML's own `onClicked` wiring is
    the part nothing checked, and an Act renamed or left unwired would give a card whose only working
    route is the keyboard — invisible to every gate in the repo, because the pure assertions test
    grammar_core's helpers, not the QML that calls them.

    Both routes are asserted against the *same* expected line on purpose: a click and a key must produce
    one answer. The expectation is written out rather than built with action_json(), because a contract
    asserted with the function that produces it cannot fail.

    Opt-in, and the marker goes to a throwaway XDG_CACHE_HOME for the same reason as the keyboard leg:
    the action written here must never be one the running watcher could pick up and act on.
    """
    if not os.environ.get("GRAMMAR_LIVE"):
        print("  live button: skipped (GRAMMAR_LIVE=1 puts a real card on screen)")
        return
    if not os.environ.get("DISPLAY"):
        print("  live button: skipped (no DISPLAY)")
        return
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ValueError, ImportError):
        # ValueError, not ImportError, when the typelib is absent: the same trap the watch gate
        # carries a guard for. A CI runner has python3-gi and no at-spi2-core.
        print("  live button: skipped (no at-spi typelib here)")
        return

    def app_for(pid):
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is not None and app.get_process_id() == pid:
                return app
        return None

    def button_named(node, name, depth=0):
        """A named node that takes an action — a QML Button, found by what it says rather than by where
        it is. Role names differ between toolkits; 'has something to activate' does not."""
        if node is None or depth > 12:
            return None
        try:
            if node.get_name() == name:
                iface = node.get_action_iface()
                if iface is not None and iface.get_n_actions() > 0:
                    return node
        except Exception:
            pass
        try:
            for i in range(min(node.get_child_count(), 200)):
                found = button_named(node.get_child_at_index(i), name, depth + 1)
                if found is not None:
                    return found
        except Exception:
            pass
        return None

    cache = tempfile.mkdtemp(prefix="grammar-button-live-")
    env = dict(os.environ, XDG_CACHE_HOME=cache)
    proc = subprocess.Popen(
        [sys.executable, POPUP, "--x", "300", "--y", "300", "--old", "go", "--new", "goes",
         "--reason", "Subject-verb agreement", "--timeout", "20"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        # A window is not in the a11y tree the instant it exists, so poll rather than sleep and hope.
        button, deadline = None, time.time() + 10
        while button is None and time.time() < deadline:
            button = button_named(app_for(proc.pid), "Fix sentence")
            if button is None:
                time.sleep(0.25)
        ok(button is not None, "the card drew a button called 'Fix sentence' (pid %d)" % proc.pid)
        if button is None:
            return
        iface = button.get_action_iface()
        names = [iface.get_action_name(i) for i in range(iface.get_n_actions())]
        press = "Press" if "Press" in names else (names[0] if names else None)
        ok(press is not None, "and it can be activated from a11y: %r" % (names,))
        iface.do_action(names.index(press))
        try:
            out, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            # A button that is drawn but not wired does nothing, so the card sits until its own
            # --timeout and the reader gets a bare TimeoutExpired. That timeout *is* the finding.
            out = b""
            ok(False, "the card answered nothing in 15 s — a drawn but unwired button does this"
                      " (its own --timeout is what ended the wait)")
        ok(out.decode().strip() == '{"action": "sentence"}',
           "pressing it answers exactly what the keyboard answers, got %r" % out.decode().strip())
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        shutil.rmtree(cache, ignore_errors=True)


def test_live_the_panel_lists_and_removes():
    """The settings window's own buttons — and the list case its row cap used to hide.

    The card's buttons got a leg earlier; the panel's had none, so "Allow <word>" could stop working
    and every gate would stay green. It also carries the case the six-row cap hid: a word that did not
    fit was not in the tree at all, and the panel is the only place it can be undone.

    The engine owns the ignore list, so the words go in over its own API and come out again through a
    press on the panel — net zero, on spellings nobody would type. XDG_CONFIG_HOME and XDG_CACHE_HOME
    point at a throwaway directory: that moves the blocklist and the pause this panel touches, so the
    real ones are not in this test's hands. The engine has no such switch (it is a service with its own
    environment), which is exactly why the words are cleaned up in a finally.
    """
    if not os.environ.get("GRAMMAR_LIVE"):
        print("  live panel: skipped (GRAMMAR_LIVE=1 puts a real window on screen)")
        return
    if not os.environ.get("DISPLAY"):
        print("  live panel: skipped (no DISPLAY)")
        return
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ValueError, ImportError):
        print("  live panel: skipped (no at-spi typelib here)")
        return

    api = os.environ.get("GRAMMAR_API") or "http://127.0.0.1:8875"
    words = ["zztmp%d" % n for n in range(1, 9)]        # eight: more than the cap this test is about
    cache = tempfile.mkdtemp(prefix="grammar-panel-live-")
    env = dict(os.environ, XDG_CONFIG_HOME=cache, XDG_CACHE_HOME=cache)

    def ask(word, forget_word=False):
        """(status, body) from the engine's ignore route. Its list is the owner's, not this test's, so
        it is changed over its own API rather than by writing a file behind it."""
        body = json.dumps({"word": word, "forget": forget_word}).encode()
        req = urllib.request.Request(api + "/v2/ignore", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except Exception as exc:
            return 0, {"error": str(exc)}

    def listed_now():
        try:
            with urllib.request.urlopen(api + "/v2/ignore", timeout=5) as r:
                return json.loads(r.read().decode() or "{}").get("words") or []
        except Exception:
            return []

    def forget(word):
        ask(word, forget_word=True)

    def node_named(node, name, depth=0):
        if node is None or depth > 14:
            return None
        try:
            if node.get_name() == name:
                iface = node.get_action_iface()
                if iface is not None and iface.get_n_actions() > 0:
                    return node
        except Exception:
            pass
        try:
            for i in range(min(node.get_child_count(), 200)):
                found = node_named(node.get_child_at_index(i), name, depth + 1)
                if found is not None:
                    return found
        except Exception:
            pass
        return None

    def app_for(pid):
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is not None and app.get_process_id() == pid:
                return app
        return None

    def window_height(pid):
        """The panel window's own height, from the X server.

        This is the instrument that tells a rendered list from a collapsed one. It was found by measuring
        both builds rather than trusting the obvious property: a row inside a list that had collapsed to
        zero height still reports 103x26 at a plausible spot inside the window, so extents and position
        are both blind to it — both said "fine" on the broken build. The window's height said 845 px
        with eight words against 709 with one, because the height follows the rows.
        """
        try:
            wid = subprocess.run(["xdotool", "search", "--pid", str(pid)], capture_output=True,
                                 text=True).stdout.split()[-1]
            geo = subprocess.run(["xdotool", "getwindowgeometry", wid], capture_output=True,
                                 text=True).stdout
            for line in geo.splitlines():
                if "Geometry:" in line:
                    return int(line.split(":")[1].strip().split("x")[1])
        except Exception:
            return None
        return None

    proc = None
    try:
        code, answer = ask(words[0])
        if code != 200:
            print("  live panel: skipped (no engine at %s: %s)" % (api, answer))
            return
        forget(words[0])
        for word in words:
            ask(word)

        proc = subprocess.Popen([sys.executable, POPUP, "--settings"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
        last = "Allow " + words[-1]
        first = "Allow " + words[0]
        button, seen, deadline = None, None, time.time() + 15
        while (button is None or seen is None) and time.time() < deadline:
            app = app_for(proc.pid)
            button = button or node_named(app, last)
            seen = seen or node_named(app, first)
            if button is None or seen is None:
                time.sleep(0.3)
        ok(button is not None,
           "the panel lists all %d words, including the last one (%r)" % (len(words), last))
        if button is None:
            return
        ok(seen is not None, "the panel's first row is in the tree too")
        iface = button.get_action_iface()
        names = [iface.get_action_name(i) for i in range(iface.get_n_actions())]
        iface.do_action(names.index("Press") if "Press" in names else 0)
        time.sleep(2.5)
        listed = listed_now()
        ok(words[-1] not in listed,
           "pressing it took the word off the engine's list: %r" % (listed,))
        ok(len(listed) == len(words) - 1,
           "and only that one, out of the %d: %r" % (len(words), listed))
        # Being in the tree is not being on screen. Two lists once rendered empty — the ScrollView was
        # bound to `contentItem`, its own Flickable container, which is 0 high for a Column child — while
        # every name-based assertion above passed against them. Neither the rows' extents nor their
        # position detects that; both were measured on the broken build and both said "fine". The
        # window's own height is not blind to it: 845 px with eight words against 709 with one, because
        # the height follows the rows. That is what this asserts, measured the same way it was found.
        rows_h = window_height(proc.pid)
        for word in words[1:]:
            forget(word)                      # down to one, well under the list's cap: nothing scrolls
        proc.kill()
        proc.wait(timeout=5)
        proc = subprocess.Popen([sys.executable, POPUP, "--settings"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        one_h, deadline = None, time.time() + 15
        while one_h is None and time.time() < deadline:
            if node_named(app_for(proc.pid), "Allow " + words[0]) is not None:
                time.sleep(1.5)               # the window is sized a beat after its rows appear
                one_h = window_height(proc.pid)
            if one_h is None:
                time.sleep(0.4)
        if rows_h is None or one_h is None:
            print("  live panel: skipped the height check (no window geometry to read here)")
        else:
            ok(rows_h - one_h > 60,
               "and the height follows its rows — %d px with %d words, %d px with one; a collapsed list "
               "is the same height for either" % (rows_h, len(words), one_h))
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        for word in words:
            forget(word)
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
        test_change_summary(popup)
        test_the_card_is_given_the_changes(popup)
        test_stream_reader(popup)
        test_streaming_rewrite(popup)
        test_hostile_input_from_the_engine(popup)
        test_a_background_failure_is_not_silent(popup)
        # The seam needs an engine to ask, and skips itself where there is none — the same shape as
        # the live leg below, minus the screen.
        test_provider_seam(popup, os.environ.get("GRAMMAR_API") or "http://127.0.0.1:8875")
    test_settings_holds_everything(popup)
    test_live()
    test_live_keyboard()
    test_live_a_pressed_button_says_the_same_thing_as_the_key()
    test_live_the_panel_lists_and_removes()
    # A gate that can pass having run nothing is not a gate. This one reported "0 assertions -
    # passed" in CI once, with a green tick, on a runner where the card's module could not even be
    # imported. If nothing ran, that is the finding.
    if PASS + FAIL == 0:
        ok(False, "the gate ran no assertions at all - the pop-up's module could not be loaded")
    print("grammar-popup-place: %d assertions - %s" % (PASS + FAIL, "passed" if not FAIL else "FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
