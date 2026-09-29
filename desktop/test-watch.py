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
import shutil
import subprocess
import sys
import tempfile
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
    lines = watch.suggestions(piece, [
        {"offset": 24, "length": 2, "message": "verb form", "replacements": [{"value": "goes"}]},
        {"offset": 0, "length": 3, "message": "spelling", "replacements": [{"value": "Teh"}]},
    ])
    ok(lines == ["teh → Teh", "go → goes"], "one line per finding, in order: %r" % lines)
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
    quiet = watch.Watcher(client, notify=lambda *a, **k: "")
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
        watcher = watch.Watcher(client, notify=lambda s, b, **k: (calls.append((s, b)), "fix")[1],
                                cooldown=0.0)
        watcher.target = doc
        write(doc, BAD)

        if take_focus(doc):
            watcher.check()
            settle(watcher, calls)
            ok(calls and "→" in calls[0][0], "the bad sentence is reported: %r" % (calls or None))
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
        watcher.offer(start, end, BAD, "CLOBBERED", ["x → y"])
        ok(doc_text(doc).startswith("Something else"), "stale offsets are refused: %r" % doc_text(doc)[:40])
        ok(calls and "changed" in calls[-1][0].lower(), "and the refusal is said out loud: %r" % calls)
    finally:
        subprocess.run(["systemctl", "--user", "stop", UNIT], capture_output=True)


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
    test_listeners()
    test_live()
    print("grammar-watch: %d assertions - passed" % len(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
