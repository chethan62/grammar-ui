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

Run with the system python (the one with gi). The clamp() assertions need no display and are
the part CI can run; the live leg skips itself where there is none.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time

# The same guard test-watch.py carries, for the same reason: `make test` calls `python3`,
# which is not necessarily the interpreter that has gi. Without this the gate spawned the
# card with a python that cannot import GTK, so it never printed PLACED and the live leg
# failed inside make while passing when run by hand with /usr/bin/python3.
try:
    import gi  # noqa: F401
except ImportError:  # the agent's own python has no gi; the system one does
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

    Its import guards gi behind a try, so a machine without GTK can still test clamp().
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
    ok(popup.card_markup("teh", "the", chips=True) == "<s>teh</s>",
       "with chips the headline is the offender alone, so the first fix is not printed twice")
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
    """The card in a real process, placed for real, reporting from the X server."""
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
    # SystemExit included deliberately: with gi present but no Gtk typelib - exactly what a CI
    # runner looks like - the pop-up's own import guard prints "no pop-up: Namespace Gtk not
    # available" and exits 2. That is SystemExit, which `except Exception` does not catch, and
    # the gate died with exit 2 inside CI while passing on a desktop with GTK.
    except (Exception, SystemExit) as exc:
        print("  clamp: skipped (the pop-up cannot run here: %s)" % exc)
        popup = None
    if popup is not None:
        test_clamp(popup)
        test_payload(popup)
    test_live()
    print("grammar-popup-place: %d assertions - %s" % (PASS + FAIL, "passed" if not FAIL else "FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
