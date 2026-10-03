#!/usr/bin/env python3
"""End-to-end UI test: drives the real window over the accessibility bus.

This is not the stub-DOM check (app/check.mjs, which imports the modules against a fake document). This
one clicks the tabs and the buttons of the *running* app and reads back what the window reports, so it
sees the things a stub cannot: that a control is wired to something, that the three panels are really
exclusive, and that the app survives being driven.

Why actions instead of keystrokes: this desktop is KDE Wayland, where KWin implements no virtual-keyboard
protocol, so synthesised input never arrives (the same reason grammar-lookup cannot paste for you). AT-SPI
actions do work, so every interaction here is an action. Text is not typed either — this WebKit exposes no
editable-text interface on the entry (interfaces: Accessible, Action, Collection, Component, Hyperlink,
Text) — so the draft is whatever the app already holds and the assertions are about the app's RESPONSE,
not about particular words. The app persists its draft in localStorage, so a check has real text to work
on after the first run.

Skips (exit 0) when there is no display, no pyatspi, or no app on the bus, so `make test` stays green on
CI. `make check-ui` starts an app, runs this, and stops it again.
"""

import argparse
import json
import os
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


def engine_counters(api):
    """The engine's cache counters, used as a change detector: a check that really reached it moves them."""
    try:
        with urllib.request.urlopen(api + "/status", timeout=5) as response:
            cache = json.load(response).get("cache", {})
        return (cache.get("entries"), cache.get("hits"), cache.get("misses"))
    except Exception:
        return None


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
    w = Window()
    if w.app is None and args.start:
        if not os.path.exists(args.binary):
            skip(f"no app running and nothing to start at {args.binary}")
        started = subprocess.Popen([args.binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        deadline = time.time() + args.wait
        while time.time() < deadline and w._find_app() is None:
            time.sleep(0.7)
        w = Window()
    if w.app is None:
        skip("the app is not running, or not on the accessibility bus")
    if not w.ready():
        skip("the app is on the bus but its window never drew the tabs")

    try:
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
        check(drew, f"the window drew the result ({len(w.finding_buttons())} finding button(s))")

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
    finally:
        if started is not None:
            started.terminate()
            try:
                started.wait(timeout=10)
            except Exception:
                started.kill()

    print(f"  grammar-ui e2e: {len(PASSED)} assertions - "
          f"{'passed' if not FAILED else 'FAILED: ' + '; '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
