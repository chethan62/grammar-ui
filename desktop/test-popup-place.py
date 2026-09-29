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
        test_payload(popup)
    test_live()
    # A gate that can pass having run nothing is not a gate. This one reported "0 assertions -
    # passed" in CI once, with a green tick, on a runner where the card's module could not even be
    # imported. If nothing ran, that is the finding.
    if PASS + FAIL == 0:
        ok(False, "the gate ran no assertions at all - the pop-up's module could not be loaded")
    print("grammar-popup-place: %d assertions - %s" % (PASS + FAIL, "passed" if not FAIL else "FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
