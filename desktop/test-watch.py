#!/usr/bin/env python3
"""Gate for grammar-watch: the sentence logic, the notification contract, and a live app.

The live leg starts its own LibreOffice in a throwaway profile so it can never scribble in a
document somebody is working on, drives the watcher against that document, and asserts the
fix landed in the app. It skips itself when there is no LibreOffice or no systemd-run.

Run it with the system python (the one that has gi): it re-execs itself if given another, so
CI and `make test` can just call python3.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import json
import time

try:
    import gi
except ImportError:  # the agent's own python has no gi; the system one does
    if not os.environ.get("GRAMMAR_TEST_REEXEC") and os.path.exists("/usr/bin/python3"):
        os.environ["GRAMMAR_TEST_REEXEC"] = "1"
        os.execv("/usr/bin/python3", ["/usr/bin/python3", os.path.abspath(__file__)] + sys.argv[1:])
    gi = None

Atspi = None
if gi is not None:
    try:
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ValueError, ImportError):
        # gi present without the at-spi typelib — which is exactly what a plain CI runner
        # looks like (python3-gi installed, at-spi2-core not). Not an error, not a pass:
        # main() says it is skipping.
        Atspi = None

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(tempfile.gettempdir(), "grammar-watch-test-profile")
UNIT = "grammar-watch-test"
BAD = "teh report is late. She go to the office."
FIXED = "The report is late. She goes to the office."
checks = []


def ok(condition, message):
    checks.append(message)
    assert condition, message


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Keyed off Atspi, not gi: the watcher imports the namespace at module level, so a gi
# without at-spi cannot even be loaded.
watch = load("grammar_watch", "grammar-watch.py") if Atspi is not None else None
client = watch.load_client() if watch is not None else None


# ---- the sentence window ---------------------------------------------------------------
def test_window():
    t = "Hello there. She go to the office. And more text after that point."
    s, e = watch.snippet_window(t, t.index("go") + 2)
    ok(t[s:e].startswith("She go to the office."),
       "the caret's sentence is in the window: %r" % t[s:e])
    ok(s == t.index("She") and t[s - 1] in ".!?\n ", "the window starts at a boundary: %r" % t[s:e])
    ok(len(t[s:e]) <= watch.BACK + watch.FORWARD + watch.TAIL, "the window stays bounded")

    s, e = watch.snippet_window(t, 0)
    ok(t[s:e].startswith("Hello there."), "no boundary behind the caret: %r" % t[s:e])

    ok(watch.snippet_window("a" * 40, 20) == (0, 40), "no boundary at all keeps the text")

    s, e = watch.snippet_window("One.   Two", 5)
    ok(s == 7, "whitespace after a boundary is trimmed: %r" % "One.   Two"[s:e])

    # the caret at the very end — the normal state while typing — keeps the sentence just typed
    typed = "One two. Three four."
    s, e = watch.snippet_window(typed, len(typed))
    ok(typed[s:e] == "Three four.", "a caret at the end keeps the sentence: %r" % typed[s:e])
    s, e = watch.snippet_window(typed, len("One two. Thr"))
    ok(typed[s:e].startswith("Three"), "a caret inside a sentence: %r" % typed[s:e])

    # a window that reaches the end of the document does not run off it
    s, e = watch.snippet_window("Short.", 6)
    ok(e == 6 and s <= 6, "clamped at the end: %r" % ((s, e),))
    ok(watch.snippet_window("after a line\nbreak here", 15)[0] == 13, "a newline counts as a boundary")


def test_suggestions():
    piece = "teh report is late. She go to the office."
    found = watch.suggestions(piece, [
        {"offset": 24, "length": 2, "message": "verb form", "replacements": [{"value": "goes"}]},
        {"offset": 0, "length": 3, "message": "spelling", "replacements": [{"value": "Teh"}]},
    ])
    ok(found == [("teh", "Teh", "spelling"), ("go", "goes", "verb form")],
       "one (old, new, reason) per finding, in order: %r" % found)
    ok(watch.suggestions(piece, [{"offset": 0, "length": 3, "message": "m", "replacements": []}]) == [],
       "a finding with no replacement is not a suggestion")


# ---- the notification contract ---------------------------------------------------------
def test_notification(tmp):
    stub = os.path.join(tmp, "notify-send")
    with open(stub, "w") as fh:
        fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > "$REC"\necho fix\n')
    os.chmod(stub, 0o755)
    record = os.path.join(tmp, "argv")
    old_path = os.environ["PATH"]
    os.environ["PATH"] = tmp + ":" + old_path
    os.environ["REC"] = record
    try:
        key = watch.notify_actions("go → goes", "Fix it to correct this in place.")
    finally:
        os.environ["PATH"] = old_path
    argv = open(record).read().splitlines()
    ok(key == "fix", "the button pressed comes back: %r" % key)
    ok("-A" in argv and "fix=Fix it" in argv and "copy=Copy fix" in argv,
       "both buttons are offered: %r" % argv)
    ok("-w" in argv and "-t" in argv, "it waits for a press and expires: %r" % argv)
    ok("go → goes" in argv, "the suggestion is the title: %r" % argv)


# ---- a live application ----------------------------------------------------------------
def find_app(sub):
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is not None and sub.lower() in (app.get_name() or "").lower():
            return app
    return None


def document(app, depth=0):
    """LibreOffice's Writer document: the editable paragraph-role text object."""
    if app is None or depth > 16:
        return None
    try:
        if (app.get_role_name() == "paragraph" and app.get_editable_text_iface() is not None
                and Atspi.Text.get_character_count(app.get_text_iface()) >= 0
                and app.get_state_set() is not None):
            return app
    except Exception:
        pass
    try:
        for i in range(min(app.get_child_count(), 300)):
            found = document(app.get_child_at_index(i), depth + 1)
            if found is not None:
                return found
    except Exception:
        pass
    return None


def editable_texts(app, out=None, depth=0):
    """Every object under this app holding editable text: Text to read the caret, EditableText
    to put a fix back. Walked by interface, not by role, because the roles differ by toolkit
    (LibreOffice's document is a "paragraph", Kate's is a "text").

    This returns all of them rather than the first: Kate exposes an editable *label* (its
    status bar) as well as the editor, and taking the first meant this leg drove the status
    bar while claiming to test the editor. The caller picks by role and says which it got.
    """
    if out is None:
        out = []
    if app is None or depth > 20:
        return out
    try:
        if (app.get_text_iface() is not None and app.get_editable_text_iface() is not None
                and Atspi.Text.get_character_count(app.get_text_iface()) >= 0):
            out.append(app)
    except Exception:
        pass
    try:
        for i in range(min(app.get_child_count(), 300)):
            editable_texts(app.get_child_at_index(i), out, depth + 1)
    except Exception:
        pass
    return out


def editable_text(app, prefer=("text", "paragraph")):
    """The app's document view when it has one, else any editable text object.

    Passing prefer, the caller can say what a document looks like in this toolkit; the
    fallback keeps the leg useful on a toolkit whose role nobody has looked up yet, and the
    caller reports which one it got so a fallback is never mistaken for the real thing.
    """
    found = editable_texts(app)
    for role in prefer:
        for a in found:
            try:
                if a.get_role_name() == role:
                    return a
            except Exception:
                pass
    return found[0] if found else None


def start_kate():
    """Start Kate and wait for its text view. Returns (app, process) or (None, reason).

    Not systemd-run, which is how the LibreOffice leg starts soffice: a systemd-run --user
    unit for Kate exits within milliseconds on this box (measured twice, displays passed
    through with --setenv), while the same command launched directly starts it and it
    appears on the accessibility bus immediately. The reason the two behave differently is
    not established, so this leg does not pretend to know it.
    """
    if not shutil.which("kate"):
        return None, "no kate on PATH"
    proc = subprocess.Popen(["kate", "--startanon", "--new"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    for _ in range(30):
        time.sleep(1)
        app = find_app("kate")
        if app is not None and editable_text(app) is not None:
            return app, proc
        if proc.poll() is not None and app is None:
            return None, "kate exited immediately (exit %s)" % proc.returncode
    if find_app("kate") is None:
        return None, "kate started but never appeared on the accessibility bus"
    return None, "kate is on the bus but exposes no editable text object"


def start_libreoffice():
    if not shutil.which("soffice") or not shutil.which("systemd-run"):
        return False
    shutil.rmtree(PROFILE, ignore_errors=True)
    subprocess.run(["systemctl", "--user", "reset-failed", UNIT], capture_output=True)
    subprocess.run(["systemd-run", "--user", "--unit", UNIT, "--collect", "soffice",
                    "--writer", "--norestore", "--nologo",
                    "-env:UserInstallation=file://" + PROFILE], capture_output=True)
    for _ in range(45):
        time.sleep(1)
        if document(find_app("soffice")) is not None:
            return True
    return False


def doc_text(doc):
    text = doc.get_text_iface()
    return Atspi.Text.get_text(text, 0, Atspi.Text.get_character_count(text))


def take_focus(doc, tries=20):
    """Ask the app for focus until it sticks. grab_focus returns False and sometimes focuses
    anyway, and a window manager can refuse to hand focus to a window nobody clicked on."""
    component = doc.get_component()
    for _ in range(tries):
        if component is not None:
            Atspi.Component.grab_focus(component)
        time.sleep(0.5)
        try:
            if doc.get_state_set().contains(Atspi.StateType.FOCUSED):
                return True
        except Exception:
            pass
    return False


def write(doc, content):
    editable = doc.get_editable_text_iface()
    editable.delete_text(0, Atspi.Text.get_character_count(doc.get_text_iface()))
    if content:
        editable.insert_text(0, content, len(content))
    time.sleep(0.6)


def settle(watcher, calls):
    for _ in range(60):
        if calls and not watcher.busy:
            return
        time.sleep(0.1)


def test_live_qt():
    """The promise is "suggests anywhere you type", and LibreOffice is one toolkit.

    Qt apps publish accessibility text on this box (measured: konsole, dolphin,
    plasmashell), but the watcher had never been driven against one, so the claim rested on
    an inference. This asks the narrow question the promise needs: can the watcher find the
    caret's text object in a Qt app, read its sentence, and put a fix back through the app's
    own interface?
    """
    app, proc = start_kate()
    try:
        if app is None:
            # Never a silent pass: say which of the reasons it was.
            print("  qt: skipped (%s)" % proc)
            return
        doc = editable_text(app)
        role = doc.get_role_name()
        # Which object this leg drove is part of the result, not a detail: taking the first
        # editable widget once meant driving Kate's status bar while the log said "Qt app".
        print("  qt: driving the %r role% s%s" % (role, " " + repr(doc.get_name()) if doc.get_name() else "",
              " — the document view" if role in ("text", "paragraph")
              else " — NOT a document view; this run proves less than it looks like"))
        ok(role in ("text", "paragraph"),
           "the Qt leg found the app's document view rather than a fallback widget (got %r)" % role)
        write(doc, BAD)

        calls = []
        watcher = watch.Watcher(
            client, ask=lambda issue, pos=None, **k: (calls.append((issue, pos)), "fix")[1],
            cooldown=0.0)
        watcher.target = doc

        if not take_focus(doc):
            # Focus is the window manager's to give. Assert what does not need it: the app
            # takes a correction through EditableText.
            print("  qt: focus stayed with the window manager — asserting the write path only")
            fixed = client.fix_until_stable(BAD, client.check)
            watcher.replace(0, len(BAD), fixed)
            ok(doc_text(doc) == FIXED, "the Qt app took the fix through EditableText: %r"
               % doc_text(doc)[:60])
            return

        watcher.check()
        settle(watcher, calls)
        ok(bool(calls) and calls[0][0].get("new"),
           "the bad sentence is reported from a Qt app: %r" % (calls[:1] or None))
        if calls:
            ok("goes" in doc_text(doc), "Fix it was applied in the Qt app: %r" % doc_text(doc)[:60])
    finally:
        if proc is not None:
            proc.terminate()
        subprocess.run(["pkill", "-x", "kate"], capture_output=True)


def test_module_loading(tmp):
    """The installed client has no .py suffix and the repository one does, so loading by path
    has to work for both. Learned from the service failing its first start after `make
    install`: the repo copy loaded, the installed copy raised AttributeError on a None spec."""
    nameless = os.path.join(tmp, "grammar-lookup")
    shutil.copy(os.path.join(HERE, "grammar-lookup.py"), nameless)
    module = watch.load_module(nameless, "grammar_client_copy")
    ok(callable(getattr(module, "check", None)), "a client with no .py suffix loads by path")
    # The exact string depends on what the engine sees: in isolation "teh report is late."
    # comes back as "the report is late.", because the capitalisation rule is reading a
    # sentence start that is not there. Assert the case the gate already verified.
    ok(module.fix_until_stable("She go to the office.", module.check) == "She goes to the office.",
       "and it is the real client")


def test_listeners():
    """The daemon's own startup path. The GI binding wants an EventListener instance, so a
    plain function there is a TypeError that only appears when the service starts — no test
    of Watcher would ever have caught it."""
    quiet = watch.Watcher(client, ask=lambda *a, **k: "")
    ok(len(watch.listen(quiet)) == 2, "both listeners register on the accessibility bus")
    ok(quiet.target is None, "registering does not invent a target")


def test_live():
    if not start_libreoffice():
        print("  live: skipped (no LibreOffice, or no systemd-run)")
        return
    try:
        doc = document(find_app("soffice"))
        ok(doc is not None, "the document paragraph is reachable through a11y")

        calls = []
        watcher = watch.Watcher(
            client, ask=lambda issue, pos=None, **k: (calls.append((issue, pos)), "fix")[1],
            cooldown=0.0)
        watcher.target = doc
        write(doc, BAD)

        if take_focus(doc):
            watcher.check()
            settle(watcher, calls)
            ok(calls and calls[0][0]["new"], "the bad sentence is reported: %r" % (calls[:1] or None))
            ok(calls[0][0]["badge"].endswith("ms"),
               "the card carries a measured badge: %r" % calls[0][0]["badge"])
            ok("goes" in doc_text(doc), "Fix it was applied in the app: %r" % doc_text(doc)[:60])
            ok(doc_text(doc) == "teh report is late. She goes to the office.",
               "only the caret's sentence was touched: %r" % doc_text(doc))

            # The earlier sentence is checked when the caret is in it: that is how every
            # sentence gets checked as it is written, without re-reading the document each pause.
            Atspi.Text.set_caret_offset(doc.get_text_iface(), len("teh report is late."))
            calls.clear()
            watcher.last_window = None
            watcher.check()
            settle(watcher, calls)
            ok(doc_text(doc) == FIXED, "the earlier sentence is corrected too: %r" % doc_text(doc))

            # the same sentence again: reported once, not twice
            calls.clear()
            write(doc, BAD)
            watcher.check()
            settle(watcher, calls)
            ok(len(calls) == 1, "a new sentence is reported once: %d" % len(calls))
            watcher.last_window = None
            watcher.check()
            settle(watcher, calls)
            ok(len(calls) == 1, "and not again for the same finding: %d" % len(calls))
        else:
            # Focus is the window manager's to give, so assert what does not depend on it: the
            # app accepts the correction through EditableText. Said out loud, not silently
            # passed — a swallowed assertion is how a suite stops meaning anything.
            print("  live: focus stayed with the window manager — asserting the write path only")
            fixed = client.fix_until_stable(BAD, client.check)
            watcher.replace(0, len(BAD), fixed)
            ok(doc_text(doc) == FIXED, "the app took the fix through EditableText: %r"
               % doc_text(doc)[:60])

        # stale offsets: the text moved on between the suggestion and the button press
        calls.clear()
        start, end = 0, len(BAD)
        write(doc, "Something else entirely was typed here.")
        watcher.offer(start, end, BAD, "CLOBBERED", {"old": "teh", "new": "the"})
        ok(doc_text(doc).startswith("Something else"), "stale offsets are refused: %r" % doc_text(doc)[:40])
        ok(calls and calls[-1][0].get("summary") == "The text changed",
           "and the refusal is said out loud, as a message: %r" % calls)
    finally:
        subprocess.run(["systemctl", "--user", "stop", UNIT], capture_output=True)


def test_popup(tmp):
    """The pop-up's contract: JSON payload on stdin, one JSON line out, exit 2 when it cannot run.

    Checked with a stub standing in for the pop-up script, because the real one opens a window
    for twelve seconds — the same reason the dialog contract is checked with stubs on PATH. The
    payload goes on stdin, not in argv, because it carries the alternatives as a list; the caret's
    coordinates stay in argv, where a pair of numbers belongs.
    """
    stubdir = tempfile.mkdtemp(prefix="grammar-popup-")
    stub = os.path.join(stubdir, "grammar-popup.py")
    with open(stub, "w") as fh:
        fh.write('import json, os, sys\n'
                 'open(os.environ["REC"], "w").write(sys.stdin.read())\n'
                 'open(os.environ["REC"] + ".argv", "w").write("\\n".join(sys.argv))\n'
                 'print(json.dumps({"action": "replace", "text": "goes"}))\n')
    saved_here, saved_rec = watch.HERE, os.environ.get("REC")
    watch.HERE, os.environ["REC"] = stubdir, os.path.join(stubdir, "payload")
    try:
        issue = {"old": "go", "new": "goes", "reason": "verb form",
                 "badge": "Rules engine · 12 ms", "more": "teh → the",
                 "alts": ["goes", "went"]}
        answer = watch.popup_actions(issue, (640, 480))
        payload = json.load(open(os.environ["REC"]))
        argv = open(os.environ["REC"] + ".argv").read().splitlines()
        ok(answer == {"action": "replace", "text": "goes"},
           "the card's JSON answer comes back whole: %r" % answer)
        ok(payload.get("old") == "go" and payload.get("reason") == "verb form",
           "the card is given the finding and its reason: %r" % payload)
        ok(payload.get("alts") == ["goes", "went"],
           "and every alternative the engine offered, not just the first: %r" % payload)
        ok(payload.get("badge") == "Rules engine · 12 ms",
           "and the measured badge: %r" % payload)
        ok("--x" in argv and "640" in argv and "480" in argv,
           "and the caret's coordinates, still in argv: %r" % argv)
        ok(watch.popup_actions(issue, None) is None,
           "no caret position: no card, so the caller can use a toast")
        # A pop-up that fails for ANY reason must mean "no pop-up", not "the user dismissed it".
        # Only exit 2 used to count, so a crash returned "" and the suggestion was dropped in silence.
        for code in (1, 2, 3, 127):
            with open(stub, "w") as fh:
                fh.write('import sys\nprint("partial noise")\nsys.exit(%d)\n' % code)
            result = watch.popup_actions({"old": "x", "new": "y"}, (10, 10))
            ok(result is None, "exit %d means no pop-up, not a dismissal (got %r)" % (code, result))
    finally:
        watch.HERE = saved_here
        if saved_rec is None:
            os.environ.pop("REC", None)
        else:
            os.environ["REC"] = saved_rec

    # The two answers the card can give that carry their own text, and the one that must not.
    # offer() has to route them by hand: a chip replaces the finding's own span, a rephrase chip
    # replaces the sentence, and plain "sentence" means the correction the watcher computed.
    class FakeWatcher(watch.Watcher):
        def __init__(self, answer):
            self.busy = False
            self.answer = answer
            self.applied = None

        def ask(self, issue, position=None):
            return self.answer

        def unchanged(self, *args):
            return True

        def replace(self, start, end, text):
            self.applied = (start, end, text)

    fw = FakeWatcher({"action": "replace", "text": "the"})
    fw.offer(100, 140, "piece", "Fixed.", {"span": [108, 111]})
    ok(fw.applied == (108, 111, "the"),
       "a chip replaces the finding's own span, not the sentence: %r" % (fw.applied,))
    ok(fw.busy is False, "and the watcher is free again afterwards")

    fw = FakeWatcher({"action": "sentence", "text": "A rephrased line."})
    fw.offer(100, 140, "piece", "Fixed.", {"span": [108, 111]})
    ok(fw.applied == (100, 140, "A rephrased line."),
       "a rephrase chip carries its own text and replaces the sentence: %r" % (fw.applied,))

    fw = FakeWatcher({"action": "sentence"})
    fw.offer(100, 140, "piece", "Fixed.", {"span": [108, 111]})
    ok(fw.applied == (100, 140, "Fixed."),
       "plain Fix sentence still means the correction the watcher computed: %r" % (fw.applied,))

    fw = FakeWatcher({"action": ""})
    fw.offer(100, 140, "piece", "Fixed.", {"span": [108, 111]})
    ok(fw.applied is None, "and Ignore applies nothing at all")

    # and the fallback ordering: with no position, the toast path answers
    stub_ns = os.path.join(tmp, "notify-send")
    with open(stub_ns, "w") as fh:
        fh.write('#!/bin/sh\necho copy\n')
    os.chmod(stub_ns, 0o755)
    saved_path = os.environ["PATH"]
    os.environ["PATH"] = tmp + ":" + saved_path
    try:
        ok(watch.ask({"old": "go", "new": "goes", "reason": "verb form"}, None)
           == {"action": "copy", "text": ""},
           "with no card possible, the notification answers instead")
    finally:
        os.environ["PATH"] = saved_path

    # The two halves of the card's payload that must agree with each other: the alternatives come
    # from the same finding first_span() points at, or a chip would rewrite different words.
    ms = [{"offset": 12, "length": 3, "message": "m", "replacements": [{"value": "Teh"}]},
          {"offset": 3, "length": 2, "message": "m",
           "replacements": [{"value": "the"}, {"value": "tea"}, {"value": "the"}]},
          {"offset": 20, "length": 4, "message": "m", "replacements": []}]
    ok(watch.alternatives(ms) == ["the", "tea"],
       "every alternative, de-duplicated and in the engine's order: %r" % watch.alternatives(ms))
    ok(watch.first_span(ms) == (3, 2),
       "and the span points at that same finding: %r" % (watch.first_span(ms),))
    ok(watch.alternatives([{"offset": 0, "length": 1, "replacements": []}]) == [],
       "a finding with no replacement offers no chips")
    # A sentence with two issues: the card shows one, and says how many others there are. The
    # engine's own sample does this, so a user fixing the named error watched the line stay
    # underlined with nothing to explain it.
    two = [{"offset": 0, "length": 2, "replacements": [{"value": "a"}]},
           {"offset": 9, "length": 3, "replacements": [{"value": "b"}]}]
    ok(watch.others(two) == 1, "a sentence with two fixable issues reports one more: %r"
       % watch.others(two))
    ok(watch.others([{"offset": 0, "length": 2, "replacements": [{"value": "a"}]}]) == 0,
       "a sentence with one issue reports no others")
    ok(watch.others([{"offset": 0, "length": 2, "replacements": []},
                     {"offset": 9, "length": 3, "replacements": [{"value": "b"}]}]) == 0,
       "a finding with no replacement is not counted as another issue")
    ok(watch.others([]) == 0, "and no findings at all is zero, not negative")
    ok(watch.shown([{"offset": 0, "length": 2, "replacements": []},
                    {"offset": 9, "length": 3, "replacements": [{"value": "b"}]}])["offset"] == 9,
       "shown() skips a finding that carries no replacement, rather than pointing at it")
    ok(watch.first_span(two) == (0, 2) and watch.alternatives(two) == ["a"],
       "and alternatives() and first_span() agree on which one that is: %r / %r"
       % (watch.first_span(two), watch.alternatives(two)))
    ok(watch.parse_reply('{"action": "replace", "text": "the"}')
       == {"action": "replace", "text": "the"}, "the JSON reply is read")
    ok(watch.parse_reply("fix") == {"action": "sentence", "text": ""},
       "and the older plain word still means the sentence")
    ok(watch.parse_reply("partial noise") is None,
       "but anything unparseable is a dismissal, never an edit")
    ok(watch.parse_reply("") is None, "and silence is a dismissal too")

    # the position arithmetic, which is pure — an edge caret is the common case, not the exotic one
    try:
        popup = load("grammar_popup", "grammar-popup.py")
    except SystemExit:
        print("  pop-up geometry: skipped (no Qt here)")
        return
    # The card renders plain text now, so escaping is structural rather than hand-rolled — and that
    # is worth pinning, because the failure it prevents was real: the GTK card had to escape the
    # document's own writing before handing it to Pango, or an '&' in it broke the label. If the
    # surface ever switches to rich text, this fails instead of the card.
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "grammar-card.qml")) as fh:
        qml = fh.read()
    ok("RichText" not in qml,
       "the card draws plain text, so the document's own writing cannot become markup")
    # A literal fractional pixelSize fails the *whole* card to load ("int expected"), while the
    # same value inside a ternary is coerced at runtime — so this trap has now been walked into
    # twice. The QML run-time error only appears in the card's stderr, which is why it is checked
    # here instead.
    bad_size = re.search(r"pixelSize:\s*\d+\.\d+", qml)
    ok(bad_size is None,
       "no fractional font.pixelSize literal (it fails the load): %r"
       % (bad_size.group(0) if bad_size else ""))
    # And the other half of the seam: every field the watcher puts in the payload is one the card
    # actually draws. A key renamed on one side and not the other is a silently blank line.
    for key in ("old", "reason", "badge", "alts", "more", "api", "sentence", "others"):
        ok("payload.%s" % key in qml, "the card draws the '%s' the watcher sends" % key)

    monitors = [(0, 0, 1920, 1080)]
    ok(popup.clamp(500, 400, 200, 60, monitors) == (500, 400), "an ordinary caret stays put")
    ok(popup.clamp(1910, 1075, 200, 60, monitors) == (1720, 1020), "a corner caret is pulled in")
    ok(popup.clamp(2500, 400, 200, 60, [(0, 0, 1920, 1080), (1920, 0, 1640, 1080)])[0] >= 1920,
       "the pop-up follows the caret onto the second monitor")


def test_edit_refusal(module):
    """A document that cannot be edited must not swallow the click.

    replace() used to raise out of a daemon thread, so a Fix in a read-only document did nothing
    at all: no error on screen, a dead thread in the journal, and a button that looked broken. The
    correction now goes to the clipboard and the card says what happened, which is the useful half
    of the outcome. Headless: the target is a stand-in, so this runs in CI.
    """
    class Refusing:
        """A viewer that publishes text but offers no EditableText — a PDF, a protected sheet."""
        def get_editable_text_iface(self):
            return None

        def get_text_iface(self):
            return None

    class Exploding:
        """An app that raises when asked to edit, which is the same answer in a worse mood."""
        def get_editable_text_iface(self):
            raise RuntimeError("no such interface")

        def get_text_iface(self):
            raise RuntimeError("no such interface")

    class StubClient:
        """A client whose clipboard works — as the real one does on a box with wl-clipboard.

        copy() returns whether it worked, so a stub that returns nothing is not modelling the
        contract: `not None` is true, which quietly sent every successful copy down the
        could-not-copy path.
        """
        API = "http://127.0.0.1:8875"

        def __init__(self):
            self.copied = []
            self.copied_ok = True

        def copy(self, text):
            self.copied.append(text)
            return self.copied_ok

        def check(self, text):
            return {"matches": []}

        def fix_until_stable(self, text, check):
            return text

    for target, label in ((Refusing(), "publishes no editable interface"),
                          (Exploding(), "raises when asked to edit")):
        client = StubClient()
        asked = []
        watcher = module.Watcher(client, cooldown=0.0,
                                 ask=lambda issue, pos=None: (asked.append(issue),
                                                              {"action": "sentence",
                                                               "text": "the"})[1])
        watcher.target = target
        watcher.unchanged = lambda *a: True          # the text is fine; the app is the problem
        ok(watcher.replace(0, 3, "the") is False,
           "replace() reports the refusal when the document %s" % label)
        watcher.offer(0, 3, "teh", "the corrected sentence",
                      {"old": "teh", "new": "the", "span": [0, 3]}, None)
        ok(client.copied == ["the"],
           "and the correction still reaches the clipboard: %r" % client.copied)
        ok(asked and "cannot be edited" in asked[-1].get("reason", ""),
           "with a card that says why instead of nothing at all: %r"
           % (asked[-1] if asked else None))
        ok(len(asked) == 2,
           "exactly one notice on top of the question — the refusal is reported, not repeated: %d"
           % len(asked))

    # The other half of the contract, and the one the refusal branch must not swallow: an app that
    # accepts the edit reports it. Without this, `if not applied` would have looked correct while
    # every successful replace also ran the failure path.
    class Working:
        def __init__(self):
            self.edits = []

        def get_editable_text_iface(self):
            return self

        def get_text_iface(self):
            return self

        def delete_text(self, start, end):
            self.edits.append(("delete", start, end))

        def insert_text(self, start, text, length):
            self.edits.append(("insert", start, text))

    working = Working()
    client = StubClient()
    asked = []
    watcher = module.Watcher(client, cooldown=0.0,
                             ask=lambda issue, pos=None: (asked.append(issue),
                                                          {"action": "sentence", "text": "the"})[1])
    watcher.target = working
    watcher.unchanged = lambda *a: True
    ok(watcher.replace(0, 3, "the") is True, "a replace the app accepted reports success")
    ok(working.edits == [("delete", 0, 3), ("insert", 0, "the")],
       "and it deleted then inserted, in the app's own interface: %r" % working.edits)
    watcher.offer(0, 3, "teh", "the corrected sentence",
                  {"old": "teh", "new": "the", "span": [0, 3]}, None)
    ok(client.copied == [] and len(asked) == 1,
       "so a click that works asks the question once and posts no notice: %d call(s)" % len(asked))

    # And the same claim checked one branch over: a Copy that could not be written must say so
    # rather than leave the user pasting something that is not there.
    target = Refusing()
    client = StubClient()
    client.copied_ok = False
    asked = []
    watcher = module.Watcher(client, cooldown=0.0,
                             ask=lambda issue, pos=None: (asked.append(issue),
                                                          {"action": "copy"})[1])
    watcher.target = target
    watcher.unchanged = lambda *a: True
    watcher.offer(0, 3, "teh", "the corrected sentence",
                  {"old": "teh", "new": "the", "span": [0, 3]}, None)
    ok(client.copied == ["the corrected sentence"], "Copy still tried to write it: %r" % client.copied)
    ok(asked and asked[-1].get("summary") == "Could not copy",
       "and says it could not copy, instead of nothing: %r" % (asked[-1] if asked else None))

    # Both wrong at once: the document refuses the edit and there is no clipboard to fall back to.
    # "Copied instead" would be a lie about the one thing the user is relying on.
    client = StubClient()
    client.copied_ok = False
    asked = []
    watcher = module.Watcher(client, cooldown=0.0,
                             ask=lambda issue, pos=None: (asked.append(issue),
                                                          {"action": "sentence", "text": "the"})[1])
    watcher.target = Refusing()
    watcher.unchanged = lambda *a: True
    watcher.offer(0, 3, "teh", "the corrected sentence",
                  {"old": "teh", "new": "the", "span": [0, 3]}, None)
    ok(client.copied == ["the"], "the fallback still tried the clipboard: %r" % client.copied)
    ok(asked and "no clipboard" in asked[-1].get("reason", ""),
       "and the notice names the real problem: %r" % (asked[-1] if asked else None))
    ok(not any("Copied instead" in (i.get("summary") or "") for i in asked),
       "never claiming copied-instead when nothing was copied")


def main():
    if watch is None:
        print("grammar-watch: skipped — no gi/at-spi bindings here (needs the system python, "
              "or the at-spi typelib on this machine)")
        return 0
    tmp = tempfile.mkdtemp(prefix="grammar-watch-test-")
    test_window()
    test_suggestions()
    test_notification(tmp)
    test_module_loading(tmp)
    test_popup(tmp)
    test_edit_refusal(watch)
    test_listeners()
    # The live legs drive real applications on the real desktop — that is their whole value, and
    # also why they are opt-in: running the gate while someone is working types into a window and
    # offers cards at them. Everything above this line runs in CI without a display.
    if os.environ.get("GRAMMAR_LIVE"):
        test_live()
        test_live_qt()
    else:
        print("  live: skipped (GRAMMAR_LIVE=1 drives real apps on this desktop)")
    print("grammar-watch: %d assertions - passed" % len(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
