#!/usr/bin/env python3
"""End-to-end UI test: drives the real window over the accessibility bus.

This is not the stub-DOM check (app/check.mjs, which imports the modules against a fake document). This
one clicks the tabs and the buttons of the *running* app and reads back what the window reports, so it
sees the things a stub cannot: that a control is wired to something, that the three panels are really
exclusive, and that the app survives being driven.

Why actions instead of keystrokes: this desktop is KDE Wayland, where KWin implements no virtual-keyboard
protocol, so synthesised input never arrives (the same reason grammar-lookup cannot paste for you). AT-SPI
actions do work, so every interaction here is an action — except actual typing, which is sent through the X
server with xdotool; the app is therefore started with GDK_BACKEND=x11 so those events arrive. The entry
exposes no editable-text interface, so text goes in through xdotool and is read back through the entry's
Text interface, which does expose the live value.

Skips (exit 0) when there is no display, no pyatspi, or no app on the bus, so `make test` stays green on
CI. `make check-ui` starts an app, runs this, and stops it again.
"""

import argparse
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

PASSED = []
FAILED = []


def skip(reason):
    print(f"  ui: skipped ({reason})")
    sys.exit(0)


def check(ok, message):
    (PASSED if ok else FAILED).append(message)
    print(f"  {'ok  ' if ok else 'FAIL'} {message}")
    return ok


def engine_words(api):
    """The words harper accepts, straight from the engine: the panel's list is a rendering of this answer."""
    try:
        with urllib.request.urlopen(api + "/v2/dictionary", timeout=5) as response:
            return json.load(response).get("words") or []
    except Exception:
        return None


def engine_counters(api):
    """The engine's cache counters, used as a change detector: a check that really reached it moves them."""
    try:
        with urllib.request.urlopen(api + "/status", timeout=5) as response:
            cache = json.load(response).get("cache", {})
        return (cache.get("entries"), cache.get("hits"), cache.get("misses"))
    except Exception:
        return None


# The draft lives in the webview's localStorage: a SQLite ItemTable whose values are UTF-16LE. Seeding it
# is how this suite gets a deterministic input, because the saved draft is the user's own text and may
# contain nothing to find — a suite that fails when someone's writing improves is worse than no suite.
APP_DATA = "~/.local/share/com.chethan62.grammar-ui/localstorage"
SEED = "teh report is late."            # harper flags `teh`, so a finding must appear


def draft_dbs():
    return sorted(glob.glob(os.path.expanduser(os.path.join(APP_DATA, "*.localstorage"))))


def draft_read(path):
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("select value from ItemTable where key = 'grammar-draft'").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        # A store that was never written has no ItemTable: that is "no draft", not a broken store.
        return None
    finally:
        conn.close()


def draft_write(path, value):
    conn = sqlite3.connect(path)
    try:
        # WebKit's own schema, byte for byte — it drops a table whose definition differs and rebuilds it
        # empty, so a hand-made store must match. A fresh file (CI) has no table at all.
        conn.execute("create table if not exists ItemTable "
                     "(key text unique on conflict replace, value blob not null on conflict fail)")
        if value is None:
            conn.execute("delete from ItemTable where key = 'grammar-draft'")
        else:
            conn.execute("insert or replace into ItemTable(key, value) values('grammar-draft', ?)",
                         (value,))
        conn.commit()
    finally:
        conn.close()


# The display is checked before pyatspi is imported, so a headless runner skips without needing the
# GNOME/AT-SPI stack installed at all.
if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
    skip("no display")
try:
    import pyatspi
except ImportError:
    skip("pyatspi is not importable; it lives in the system python (/usr/bin/python3)")


class Window:
    """The running app, found by name, walked through its accessibility tree."""

    def __init__(self):
        self.app = self._find_app()

    @staticmethod
    def _find_app():
        for a in pyatspi.Registry.getDesktop(0):
            try:
                if a.name and "grammar" in a.name.lower():
                    return a
            except Exception:
                pass
        return None

    def nodes(self):
        out = []

        def walk(n):
            try:
                out.append(n)
            except Exception:
                return
            try:
                for c in n:
                    walk(c)
            except Exception:
                pass

        if self.app:
            for w in self.app:
                walk(w)
        return out

    def alive(self):
        """Re-find the app each time: a crash between two assertions must not read as a stale tree."""
        self.app = self._find_app()
        return self.app is not None

    def find(self, role, name=None):
        for n in self.nodes():
            try:
                if n.getRoleName() == role and (name is None or (n.name or "").strip() == name):
                    return n
            except Exception:
                pass
        return None

    def names(self, role):
        out = []
        for n in self.nodes():
            try:
                if n.getRoleName() == role and (n.name or "").strip():
                    out.append((n.name or "").strip())
            except Exception:
                pass
        return out

    def click(self, role, name=None):
        n = self.find(role, name)
        if n is None:
            return False
        try:
            action = n.queryAction()
            labels = [action.getName(i) for i in range(action.nActions)]
            action.doAction(labels.index("click") if "click" in labels else 0)
            return True
        except Exception:
            return False

    def finding_buttons(self):
        """A finding renders as a button named with its message and the suggested text.

        Measured, not assumed: after a check the tree gained
        `Passive voice: consider naming the actor and using the active voice. — be given` and `Rephrase`.
        """
        return [n for n in self.names("button")
                if "—" in n or n in ("Rephrase", "Fix sentence", "Add to dictionary")]

    def ready(self, seconds=25):
        """Wait for the WEBVIEW, not just the window.

        The tabs appear a moment after the app is on the accessibility bus, and asserting in that gap reads
        as "this window has no tabs" — which is how this test first failed against a perfectly good app.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.names("page tab"):
                return True
            time.sleep(0.5)
        return False


def tab_widgets(w, tab):
    """The controls that only exist on one tab, used both ways: present on its own, absent on the others."""
    return {
        "Check": [("entry", "Text to check"), ("button", "Check"), ("button", "Fix all")],
        "Rewrite": [("button", "Rewrite")],
        "Settings": [("heading", "Accepted words"), ("heading", "AI settings"),
                     ("heading", "Pause status"), ("button", "Save")],
    }[tab]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true",
                        help="start an app if none is running, and stop it afterwards")
    parser.add_argument("--binary", default=os.path.expanduser("~/.local/bin/grammar"))
    parser.add_argument("--wait", type=int, default=25, help="seconds to wait for the app to appear")
    args = parser.parse_args()

    started = None
    seeded = {}
    w = Window()
    if w.app is None and args.start:
        if not os.path.exists(args.binary):
            skip(f"no app running and nothing to start at {args.binary}")
        # Seed a known draft while nothing has the file open — a running webview would write its own copy
        # back over it — then put the user's own draft back once the app is stopped. Every origin is seeded
        # because the dev server and the release build name their storage differently.
        dbs = draft_dbs()
        if not dbs and started is None and args.start:
            # A machine that has never run the app (CI) has no store, and hand-crafting one does not work:
            # the webview drops a table it did not create (measured — a schema-identical hand-made store
            # was emptied within a second of launch). So the app is run once to build its own store — it
            # only writes one on the first input, so a single character is typed — then stopped, and only
            # then is the real store seeded.
            probe = subprocess.Popen([args.binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True,
                                     env={**os.environ, "GDK_BACKEND": "x11"})
            deadline = time.time() + args.wait
            while time.time() < deadline and w._find_app() is None:
                time.sleep(0.7)
            probe_win = Window()
            if probe_win.ready(seconds=15):
                entry = probe_win.find("entry", "Text to check")
                if entry is not None:
                    import pyatspi as atspi_mod  # pyright: ignore[reportMissingImports]
                    ext = entry.queryComponent().getExtents(atspi_mod.DESKTOP_COORDS)
                    cx, cy = int(ext.x + ext.width / 2), int(ext.y + ext.height / 2)
                    subprocess.run(["xdotool", "mousemove", str(cx), str(cy), "click", "1"], check=False)
                    time.sleep(0.3)
                    subprocess.run(["xdotool", "type", "--delay", "60", "x"], check=False)
                    time.sleep(0.8)
            probe.terminate()
            try:
                probe.wait(timeout=10)
            except Exception:
                probe.kill()
            dbs = draft_dbs()
            if dbs:
                print(f"  note: no draft store existed; the app made one, so it was seeded from that")
        for db in dbs:
            try:
                seeded[db] = draft_read(db)
                draft_write(db, SEED.encode("utf-16-le"))
            except Exception as error:
                print(f"  note: could not seed {os.path.basename(db)}: {error}")
        started = subprocess.Popen([args.binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True,
                                   env={**os.environ, "GDK_BACKEND": "x11"})
        deadline = time.time() + args.wait
        while time.time() < deadline and w._find_app() is None:
            time.sleep(0.7)
        w = Window()
    if w.app is None:
        skip("the app is not running, or not on the accessibility bus")
    if not w.ready():
        skip("the app is on the bus but its window never drew the tabs")

    try:
        if os.environ.get("GRAMMAR_E2E_DEBUG"):
            # Dump every role/name pair: a role vocabulary change (WebKitGTK version, AT-SPI registry)
            # makes controls "missing" when they are present under another role — the tree itself is the
            # only witness, and this is how the CI job can show it without a human at a screen.
            for n in w.nodes():
                try:
                    print(f"  tree: {n.getRoleName()} {n.name!r}")
                except Exception:
                    pass
        check(w.names("page tab") == ["Check", "Rewrite", "Settings"],
              f"the window offers the three tabs: {w.names('page tab')}")

        for tab in ("Check", "Rewrite", "Settings"):
            if not check(w.click("page tab", tab), f"the {tab} tab can be clicked"):
                continue
            time.sleep(1.2)
            for role, name in tab_widgets(w, tab):
                check(w.find(role, name) is not None, f"{tab}: {role} {name!r} is on screen")
            for other in ("Check", "Rewrite", "Settings"):
                if other == tab:
                    continue
                # A control from another tab on this one means the panels are not exclusive, which is
                # exactly the navigation bug a stub DOM cannot see.
                leaked = [f"{r} {n!r}" for r, n in tab_widgets(w, other) if w.find(r, n) is not None]
                hint = "" if not leaked else f" (leaked: {leaked})"
                check(not leaked, f"{tab}: no controls of the {other} tab{hint}")

        # Clicking the primary action, with two independent signals that it did something. The engine's own
        # counters are the stronger one: they move whatever the draft holds, so this does not depend on the
        # saved draft happening to contain a mistake. The finding buttons are the visible half.
        w.click("page tab", "Check")
        time.sleep(1.0)
        api = os.environ.get("GRAMMAR_API", "http://127.0.0.1:8875").rstrip("/")
        before_counters, before_findings = engine_counters(api), w.finding_buttons()
        check(w.click("button", "Check"), "the Check button can be clicked")
        reached = drew = False
        deadline = time.time() + 25
        while time.time() < deadline and not (reached and drew):
            time.sleep(0.8)
            if before_counters is not None and engine_counters(api) != before_counters:
                reached = True
            if w.finding_buttons() != before_findings:
                drew = True
        if before_counters is None:
            print(f"  note: no engine at {api} — the check cannot be observed from here")
        else:
            check(reached, "clicking Check reached the engine (its counters moved)")
        if seeded:
            # Deterministic: the seeded draft contains `teh`, so this finding must be drawn — and the
            # assertion is about that word rather than about "something appeared".
            flagged = [b for b in w.finding_buttons() if "teh" in b.lower()]
            check(bool(flagged), f"the seeded mistake is drawn as a finding: {flagged[:1]}")
        else:
            check(drew, f"the window drew the result ({len(w.finding_buttons())} finding button(s))")
            print("  note: an app was already running, so its draft was not seeded and the finding itself "
                  "is not asserted — only that the click reached the engine")

        # Autocomplete: typing asks the engine for the word at the caret, and a suggestion is selectable.
        # The expected words come from the engine's own answer, never a hardcoded list, and the draft is
        # read back through the entry's Text interface, which exposes the live value. Keystrokes arrive
        # only through the X server, so this leg needs the app this suite started (GDK_BACKEND=x11) and an
        # xdotool — both are preconditions, and their absence is a note, not a failure.
        if started is None:
            print("  note: autocomplete not exercised (an app was already running; it may not take X input)")
        elif shutil.which("xdotool") is None:
            print("  note: autocomplete not exercised (xdotool is not installed)")
        else:
            completions = []
            try:
                with urllib.request.urlopen(api + "/v2/complete?prefix=spec", timeout=5) as response:
                    completions = json.load(response).get("words") or []
            except Exception:
                print("  note: autocomplete not exercised (no engine answer for /v2/complete)")
            if completions:
                entry = w.find("entry", "Text to check")
                if entry is None:
                    print("  note: autocomplete not exercised (no draft entry in the tree)")
                else:
                    import pyatspi as atspi_mod  # pyright: ignore[reportMissingImports] — system python only
                    ext = entry.queryComponent().getExtents(atspi_mod.DESKTOP_COORDS)
                    cx, cy = int(ext.x + ext.width / 2), int(ext.y + ext.height / 2)

                    def type_text():
                        subprocess.run(["xdotool", "mousemove", str(cx), str(cy), "click", "1"], check=False)
                        time.sleep(0.4)
                        subprocess.run(["xdotool", "key", "ctrl+a"], check=False)
                        subprocess.run(["xdotool", "type", "--delay", "60", "the report is spec"], check=False)

                    type_text()
                    deadline = time.time() + 8
                    while time.time() < deadline:
                        probe = w.find("entry", "Text to check")
                        if probe is not None and probe.queryText().getText(0, -1).endswith("spec"):
                            break
                        time.sleep(0.5)
                    else:
                        # Measured flake: the window was still moving when the coordinates were read, so the
                        # click landed beside the draft and the keys went nowhere. One retry, same target.
                        type_text()
                        time.sleep(1.0)
                    offered = []
                    deadline = time.time() + 15
                    while time.time() < deadline and len(offered) < min(8, len(completions)):
                        time.sleep(0.5)
                        offered = [b for b in w.names("button") if b in completions]
                    check(bool(offered), f"typing offered the engine's completions: {offered[:4]}…")
                    if offered:
                        picked = completions[1] if len(completions) > 1 else completions[0]
                        check(w.click("button", picked), f"a suggestion ({picked}) can be clicked")
                        value = ""
                        deadline = time.time() + 10
                        while time.time() < deadline:
                            live = w.find("entry", "Text to check")
                            if live is None:
                                break
                            value = live.queryText().getText(0, -1)
                            if value.endswith(picked):
                                break
                            time.sleep(0.4)
                        check(value.endswith(picked),
                              f"clicking it completed the word at the caret: …{value[-len(picked):]!r}")

        # An empty word must be ignored rather than written: the dictionary takes one word and refuses
        # a phrase, and the box has nothing in it.
        w.click("page tab", "Settings")
        time.sleep(1.0)
        check(w.click("button", "Add"), "the Add button can be clicked with an empty box")
        time.sleep(0.8)
        check(w.alive(), "the app is still running after every click")

        # Read-only settings the app only reports: if these vanish, the panel lost a section.
        for role, name in (("combo box", "Runner"), ("combo box", "Dialect"), ("button", "Save")):
            check(w.find(role, name) is not None, f"Settings: {role} {name!r} is present")

        # Every accepted word carries the way back out. The list is a rendering of the engine's own answer,
        # so the controls are counted against that answer rather than merely found: a row without a Remove is
        # a word that can only be added, which is the state this feature exists to end. An engine that does
        # not answer means no question — a missing live precondition is a skip, never a failure.
        words = engine_words(api)
        if words is None:
            print("  skip the Remove controls: the engine did not answer /v2/dictionary")
        else:
            removes = w.names("button").count("Remove")
            check(removes == len(words),
                  f"Settings: {len(words)} accepted word(s), {removes} Remove control(s) drawn")
    finally:
        if started is not None:
            started.terminate()
            try:
                started.wait(timeout=10)
            except Exception:
                started.kill()
            # The user's own draft goes back only after the app has stopped, or the webview would write its
            # seeded copy back over the restore on the way out.
            for db, value in seeded.items():
                try:
                    draft_write(db, value)
                except Exception as error:
                    # Loud on purpose: leaving someone else's text in the app is the one side effect this
                    # suite must never have.
                    print(f"  WARN could not restore the draft in {os.path.basename(db)}: {error}")

    print(f"  grammar-ui e2e: {len(PASSED)} assertions - "
          f"{'passed' if not FAILED else 'FAILED: ' + '; '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
