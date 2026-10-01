#!/usr/bin/python3
"""Suggest fixes as you type — in any application, no browser, no selection needed.

A browser extension reads the DOM. There is no DOM outside a browser, so this reads the
accessibility bus instead: every toolkit on the desktop (LibreOffice, GTK, Qt, Firefox,
Chromium) publishes the text object you are typing into, its caret, and — through
EditableText — a way to replace text in it. So the suggestion can be applied in place, by the
application itself, with the application's own undo stack owning the edit.

Flow: a11y focus/text-changed events mark the target; after a 1.2s pause the sentences around
the caret are checked; findings appear as a desktop notification with "Fix it" and "Copy fix".

  GRAMMAR_API=<url>      engine to use (default http://127.0.0.1:8875, same as the client)
  GRAMMAR_LANG=<code>    language to check as (default en-US)

Verified against LibreOffice Writer (see desktop/test-watch.py), which is the app that
motivated this: the document paragraph is findable when focused, its caret is readable, and
rows can be replaced through EditableText. Apps that expose no accessible text — most
terminals, and Electron apps without --force-renderer-accessibility — cannot be served this
way; nothing can, short of an input method, because the API is the only door into them.
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

HERE = os.path.dirname(os.path.abspath(__file__))

# ponytail: fixed-size read around the caret — one cheap read per pause instead of reading a
# whole 50-page document. Widen BACK if suggestions ever miss a sentence that starts further up.
DEBOUNCE_MS = 1200
# ponytail: events are the fast path, but LibreOffice's a11y event emission is partial — a
# slow poll is what makes this work in the app it was built for. One bounded read per tick;
# the engine is only asked when the text actually changed.
POLL_MS = 1500
COOLDOWN_S = 5.0
DEBUG = os.environ.get("GRAMMAR_WATCH_DEBUG") == "1"

# The pure half — findings, the card's contracts, its palette and its two HTTP calls — lives in
# grammar_core, which imports nothing but the standard library. It is re-exported here because
# callers and gates have always reached these names through this module, and because a module
# that needs gi or Qt to import cannot be reasoned about on a machine without them.
#
# sys.path first: the gates load this file by path (spec_from_file_location), which does not put
# its directory on the path, so a plain `import grammar_core` would fail there while working when
# the script is run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grammar_core import (BACK, CARET_GAP, FORWARD, MIN_CHARS, TAIL, add_blocked, alternatives,
                          app_blocked, block_list, blocked_apps, clear_pause, finding_word,
                          first_span, others, parse_reply, paused_until, post_json, shown,
                          snippet_window, suggestions, write_pause)

# The file the per-app pause is kept in — beside the engine's own config, because it is the same
# question ("what does this machine want?") asked about a different thing. One name per line, and
# the defaults in grammar_core are always in force, so this starts empty and only grows by choice.
BLOCKLIST = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "grammar-server", "blocked-apps")



def debug(*args):
    if DEBUG:
        print("watch:", *args, file=sys.stderr, flush=True)

# Roles that carry prose. Toolbar combo boxes are editable text objects too, and checking a
# font-size field is not a service to anyone.
PROSE_ROLES = {"text", "paragraph", "entry", "document text", "text frame", "heading"}


def load_module(path, name="grammar_client"):
    """Load a script by path. Installed copies have no .py suffix (~/.local/bin/grammar-lookup)
    and spec_from_file_location cannot infer a loader for those — hence the explicit one. The
    repository copy loads fine without it, so this only shows up once installed."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_client():
    """The lookup client, not a copy of it: same request shape, same fix rule, same clipboard."""
    for path in (os.path.join(HERE, "grammar-lookup.py"), os.path.join(HERE, "grammar-lookup"),
                 os.path.expanduser("~/.local/bin/grammar-lookup"),
                 os.path.expanduser("~/.local/share/grammar-ui/grammar-lookup.py")):
        if os.path.exists(path):
            return load_module(path)
    raise SystemExit("grammar-lookup not found — install it next to this script")


def read(obj, start=None, end=None):
    """(Text interface, text). Call through Atspi.Text explicitly: Accessible.get_text() — the
    deprecated shim — takes the same name in these bindings and shadows the interface method.
    """
    text = obj.get_text_iface()
    if start is None:
        return text, Atspi.Text.get_text(text, 0, Atspi.Text.get_character_count(text))
    return text, Atspi.Text.get_text(text, start, end)


def notify_actions(summary, body, actions=(("fix", "Fix it"), ("copy", "Copy fix")), timeout=25):
    """A toast with buttons; returns the key of the button pressed ("" if it went away)."""
    if not shutil.which("notify-send"):
        return ""
    cmd = ["notify-send", "-a", "grammar", "-t", "15000", "-w"]
    for key, label in actions:
        cmd += ["-A", "%s=%s" % (key, label)]
    cmd += [summary, body]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).stdout.decode().strip()
    except subprocess.SubprocessError:
        return ""


def popup_actions(issue, position):
    """The card next to the caret. Returns {"action": ..., "text": ...}, or None when no card is
    possible at all (no display, no Qt) — the caller then falls back to a toast.

    Its own process, so nothing Qt touches this daemon. The payload is one dict — old, reason,
    badge, alts, more, api, sentence, others — which is also the shape a future IPC would carry,
    if this ever grows a
    second host. It travels on stdin rather than in argv because the alternatives are a list, and
    a list on a command line is a quoting bug waiting to happen.
    """
    script = os.path.join(HERE, "grammar-popup.py")
    if position is None or not os.path.exists(script):
        return None
    argv = [sys.executable, script, "--x", str(position[0]), "--y", str(position[1])]
    if len(position) > 2 and position[2]:
        # The caret's own height: enough for clamp() to hang the card above the line when the bottom
        # of the screen is in the way, instead of putting it over the text being typed.
        argv += ["--caret-h", str(int(position[2]))]
    # The card makes the rephrase call itself, so it needs the engine's address and the sentence.
    # Both arrive in the issue: the address from the client's own API constant (one source of the
    # default, not two), and an empty one simply means the card offers no rephrase row instead of
    # a button that fails on every click.
    payload = {"old": issue.get("old", ""), "reason": issue.get("reason", ""),
               "badge": issue.get("badge", ""), "alts": issue.get("alts") or [],
               "api": issue.get("api", ""), "sentence": issue.get("sentence", "")}
    if issue.get("more"):
        payload["more"] = issue["more"]
    if issue.get("others"):
        # Only when there is one: the card says nothing rather than "0 more issues".
        payload["others"] = int(issue["others"])
    try:
        proc = subprocess.run(argv, input=json.dumps(payload).encode(),
                              capture_output=True, timeout=30)
    except subprocess.SubprocessError:
        return None
    if proc.returncode != 0:
        # Any failure is "no pop-up" — never an empty answer, which offer() reads as a dismissal and
        # then drops the suggestion. A pop-up that cannot start must say so in the journal.
        print("grammar-watch: pop-up exited %d: %s"
              % (proc.returncode, proc.stderr.decode().strip()[:200]), file=sys.stderr, flush=True)
        return None
    return parse_reply(proc.stdout.decode())


def ask(issue, position=None):
    """Show it where it belongs: a card at the caret when there is something to apply, else a toast.

    A card with no diff is not a card — "the text changed, nothing was applied" is a message, and
    messages belong in a notification. Same for anything without a replacement to offer.
    """
    if issue.get("new") or issue.get("alts"):
        answer = popup_actions(issue, position)
        if answer is not None:
            return answer
    summary = issue.get("summary") or ("%s → %s" % (issue["old"], issue["new"]))
    body = issue.get("more") or issue.get("reason") or "Fix it to correct this in place."
    key = notify_actions(summary, body)
    if not key:
        return {}
    return {"action": "sentence" if key == "fix" else key, "text": ""}


class Watcher:
    def __init__(self, client, ask=ask, cooldown=COOLDOWN_S):
        self.client = client
        self.ask = ask
        self.cooldown = cooldown
        self.target = None
        self.timer = None
        self.busy = False
        self.last_window = None    # the text we last checked
        self.last_engine_ms = None  # how long the engine took, for the card's badge
        self.last_notified = None  # the text we last mentioned (never nag twice)
        self.last_time = 0.0

    # ---- events -------------------------------------------------------------------------
    def on_focus(self, event):
        self.adopt(event.source)
        return False

    def on_text(self, event):
        """Text changed somewhere. The event's source is the object being typed into, which is
        what makes target tracking work without walking the tree."""
        self.adopt(event.source)
        if self.target is None:
            return False
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(DEBOUNCE_MS, self.check)
        return False

    def adopt(self, obj):
        try:
            if (obj is not None and obj.get_editable_text_iface() is not None
                    and obj.get_role_name() in PROSE_ROLES):
                if self.target is None:
                    print("watching a %s in %s" % (obj.get_role_name(), self.app_name(obj)),
                          file=sys.stderr, flush=True)
                self.target = obj
        except Exception:
            pass

    def poll(self):
        """The safety net under the event path, kept for the life of the process."""
        if self.target is not None:
            self.check()
        return True

    def app_name(self, obj=None):
        try:
            return (obj or self.target).get_application().get_name()
        except Exception:
            return "this application"

    def blocklist(self):
        """The blocklist in force, read per check.

        It is a few hundred bytes, and a setting that only takes effect after a restart is a
        setting that looks broken — the whole point is that the pause takes hold the moment it is
        asked for. ponytail: one file read per pause; cache it on mtime if it ever shows up in a
        profile.
        """
        try:
            with open(BLOCKLIST) as fh:
                return block_list(fh.read())
        except OSError:
            return block_list("")

    def pause_here(self):
        """Stop checking in this application — what the card's "Ignore in <app>" asks for.

        Written to a file rather than held in memory: the promise is about this application, and it
        has to survive the next restart of the watcher. A toast says so, and says where to undo it,
        because a rule the user cannot find again is a bug report waiting to happen.
        """
        name = self.app_name()
        if name == "this application":   # the bus would not say: never write a rule about nobody
            debug("the focused application will not name itself; nothing paused")
            return
        try:
            with open(BLOCKLIST) as fh:
                text = fh.read()
        except OSError:
            text = ""
        updated = add_blocked(text, name)
        if updated == text:
            # Already paused — by this click before, or by a default. Saying so again would be a
            # toast that reports nothing was done, and repeated identical toasts are throttled by
            # the notification daemon anyway.
            debug("already paused in %s" % name)
            return
        try:
            os.makedirs(os.path.dirname(BLOCKLIST), exist_ok=True)
            with open(BLOCKLIST, "w") as fh:
                fh.write(updated)
        except OSError as exc:
            debug("could not write %s: %s" % (BLOCKLIST, exc))
            return
        debug("paused in %s" % name)
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "grammar", "-t", "5000",
                              "Paused in %s" % name,
                              "Checking is off in this application. Delete its line from %s "
                              "to bring it back." % BLOCKLIST])

    def pause_for(self, seconds):
        """Silence the checker for a while — what the card's "Pause for an hour" asks for.

        A timestamp, not a flag: the silence ends by itself, so an interrupted day does not leave the
        checker off with nothing on screen to say so. The toast names the command that ends it early,
        because "it will come back in an hour" is only reassuring if you can also make it come back.
        """
        until = write_pause(seconds)
        debug("paused for %ds (until %d)" % (seconds, until))
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "grammar", "-t", "5000",
                              "Paused for %s" % ("%d minutes" % (seconds // 60) if seconds % 3600
                                                 else "%d hour%s" % (seconds // 3600,
                                                                     "" if seconds == 3600 else "s")),
                              "Suggestions are off until %s. `grammar-pause off` ends it early."
                              % time.strftime("%H:%M", time.localtime(until))])

    def ignore_word(self, word):
        """Ask the engine to stop reporting this word — the card's "Ignore this word".

        A request to the engine rather than a filter in this client, for two reasons: the engine is
        where every client's matches come through, so one list covers the watcher, the selection
        checker and anything added later; and the answer carries the file it wrote, so the toast can
        name it even when the engine is on another machine.

        The word is still wrong to harper and to every other editor — this is one product declining to
        repeat itself — and the toast says so, because the two are easy to confuse and only one of
        them was built.
        """
        if not word:
            return
        base = (getattr(self.client, "API", "") or "").rstrip("/")
        if not base:
            debug("no engine address, so %r cannot be ignored" % word)
            return
        status, answer = post_json(base + "/v2/ignore", {"word": word})
        if status != 200:
            debug("the engine refused to ignore %r: %s" % (word, answer.get("message") or status))
            return
        debug("ignoring %r (%s words now)" % (word, answer.get("ignored")))
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "grammar", "-t", "6000",
                              "Ignoring “%s”" % word,
                              "This engine will not report it again. Other editors still will — "
                              "delete the line from %s to bring it back."
                              % (answer.get("path") or "its ignored-words file")])

    # ---- the check ----------------------------------------------------------------------
    def check(self):
        self.timer = None
        if self.busy or self.target is None:
            return False
        # "Not now" beats every other consideration, including "not this application": a pause is the
        # user having said they do not want suggestions at all for a while.
        until = paused_until()
        if until:
            debug("paused until %d" % until)
            return False
        try:
            if not self.target.get_state_set().contains(Atspi.StateType.FOCUSED):
                return False  # they moved on; a debounce that fired late is stale
        except Exception:
            pass
        # A paused application is skipped before anything is read, so a password field is never even
        # sent to the engine: the pause is about privacy as much as about noise.
        app = self.app_name()
        if app_blocked(app, self.blocklist()):
            debug("paused in %s" % app)
            return False
        try:
            text, _ = read(self.target)
            count = Atspi.Text.get_character_count(text)
            caret = Atspi.Text.get_caret_offset(text)
            if caret < 0:
                caret = count
            begin = max(0, caret - BACK)
            snippet = Atspi.Text.get_text(text, begin, min(count, caret + FORWARD))
        except Exception:
            return False
        start, end = snippet_window(snippet, caret - begin)
        piece = snippet[start:end]
        debug("window %r (caret %d, %d chars)" % (piece[:60], caret, count))
        if len(piece.strip()) < MIN_CHARS or not any(c.isalpha() for c in piece):
            debug("too short to be prose")
            return False
        if piece == self.last_window:
            debug("unchanged since the last check")
            return False
        self.last_window = piece
        started = time.monotonic()
        try:
            matches = self.client.check(piece).get("matches", [])
        except Exception as exc:
            # Never silent: an unreachable engine is the first thing to suspect when this
            # daemon appears to do nothing, and it is what the lookup client shouts about.
            print("grammar-watch: engine check failed: %s" % exc, file=sys.stderr, flush=True)
            return False
        self.last_engine_ms = (time.monotonic() - started) * 1000.0
        debug("matches: %d in %.0f ms" % (len(matches), self.last_engine_ms))
        if not matches:
            return False
        fixed = self.client.fix_until_stable(piece, self.client.check)
        if fixed == piece or piece == self.last_notified:
            return False
        if time.monotonic() - self.last_time < self.cooldown:
            return False
        self.last_notified, self.last_time = piece, time.monotonic()
        found = suggestions(piece, matches)
        old, new, reason = found[0]
        span = first_span(matches)
        issue = {"old": old, "new": new, "reason": reason,
                 # The card offers every fix the engine returned for this finding, not just the
                 # first: the alternatives were being thrown away a line later.
                 "alts": alternatives(matches),
                 # The card rephrases the *corrected* line: sending the user's own errors to a
                 # small model invites it to preserve or pad them.
                 "sentence": fixed,
                 # Where the card should send a rephrase. From the client, which owns the
                 # GRAMMAR_API default; empty when it cannot be read, and the card copes.
                 "api": getattr(self.client, "API", ""),
                 # The span a chip replaces: the finding's own words, absolute in the document.
                 # Fix sentence is the button that takes the whole line.
                 "span": [begin + start + span[0], begin + start + span[0] + span[1]],
                 # Which application this is, for the pause action. Empty when the bus will not say:
                 # a card offering "Ignore in this application" would be promising a rule about
                 # nobody, and the button is hidden rather than wrong.
                 "app": "" if app == "this application" else app,
                 # The word this finding is about, when it is exactly one misspelled word. Empty
                 # otherwise, which hides the card's ignore button: the engine's list is a *word*
                 # list, so a phrase or a whole sentence cannot be added to it honestly.
                 "word": finding_word(piece, start + span[0], shown(matches)),
                 # The badge is the engine's real time, not a decoration: it is how the user sees
                 # whether a suggestion is instant or cost something.
                 "badge": "Rules engine · %d ms" % round(self.last_engine_ms or 0),
                 "more": "; ".join("%s → %s" % (o, n) for o, n, _ in found[1:4]),
                 # A second issue in the same sentence used to be invisible: the card named one
                 # error while Fix sentence corrected both, which reads as a mystery.
                 "others": others(matches)}
        position = self.caret_position(text, caret)
        self.busy = True
        # Off the a11y event loop: waiting for a button press must not deafen the listener.
        threading.Thread(target=self.offer, daemon=True,
                         args=(begin + start, begin + end, piece, fixed, issue, position)).start()
        return False

    def caret_position(self, text, caret):
        """Where the caret is on screen, so the pop-up can sit next to it. None when the app
        will not say — a notification is shown instead of a pop-up somewhere wrong.

        Three numbers, not two: the height of the caret's own rect is what lets clamp() hang the card
        *above* the line when there is no room below it, and only this side of the wire can see it.
        """
        try:
            ext = Atspi.Text.get_character_extents(text, max(0, caret), Atspi.CoordType.SCREEN)
            if ext.width or ext.height:
                # The caret's bottom edge plus a gap: just under the line being typed in.
                return ext.x, ext.y + ext.height + CARET_GAP, ext.height
        except Exception:
            pass
        return None

    def offer(self, start, end, piece, fixed, issue, position=None):
        """Ask, then act — and only act on the text we actually checked.

        Three answers are actable: a chip names the replacement for this finding's own words, Fix
        sentence applies every correction in the line at once, and Copy puts that line on the
        clipboard. A chip replaces issue["span"] only — start/end is the whole sentence here, so
        using them for a chip would rewrite a sentence with one word.
        """
        try:
            answer = self.ask(issue, position) or {}
            if not isinstance(answer, dict):
                # A stub, or an older caller, may still answer in a single word: "fix" has always
                # meant "apply the whole sentence".
                answer = {"action": "sentence" if answer == "fix" else "", "text": ""}
            action = answer.get("action")
            if action == "ignore-app":
                # Not an edit: this pauses checking in this application, so it is answered before
                # any of the text guards — there is no text here to have gone stale.
                self.pause_here()
                return
            if action == "pause-hour":
                # Also not an edit, and also about the future rather than this finding.
                self.pause_for(3600)
                return
            if action == "ignore-word":
                # Not an edit either: it asks the engine to stop reporting this word, here and for
                # anyone else using this engine.
                self.ignore_word(issue.get("word") or "")
                return
            if action not in ("replace", "sentence", "copy"):
                return
            if not self.unchanged(start, end, piece):
                self.ask({"old": "", "new": "", "reason": "", "summary": "The text changed",
                          "more": "Nothing was applied. Ctrl+Alt+C checks a selection."})
                return
            if action == "replace":
                span = issue.get("span") or [start, end]
                text = answer.get("text") or issue.get("new") or ""
                applied = self.replace(span[0], span[1], text)
            elif action == "sentence":
                # A chip from the card's own Rephrase carries its text; Fix sentence does not and
                # means the correction the watcher already computed.
                text = answer.get("text") or fixed
                applied = self.replace(start, end, text)
            else:
                # Copy is an outcome too: claiming it worked when no clipboard tool exists leaves
                # the user with nothing and no way to know. copy() returns whether it worked.
                if not self.client.copy(fixed):
                    self.ask({"old": fixed, "new": "",
                              "reason": "No clipboard tool — install wl-clipboard or xclip",
                              "summary": "Could not copy", "more": fixed[:200]})
                return
            if applied is False:
                # The document refused the edit, so the clipboard is the fallback — and whether
                # that worked decides what to say. Two things can be wrong at once (no editable
                # text, and no clipboard tool), so the message covers both rather than claiming
                # "copied" for a copy that did not happen.
                #
                # Tested with `is False`, not truthiness: only an explicit refusal means the edit
                # did not happen, so a replace() that returns nothing (an older implementation, a
                # stand-in in a test) keeps the behaviour it always had.
                if self.client.copy(text):
                    self.ask({"old": text, "new": "",
                              "reason": "This document cannot be edited from here",
                              "summary": "Copied instead",
                              "more": "Paste it where you need it, or select text and press Ctrl+Alt+C."})
                else:
                    self.ask({"old": text, "new": "",
                              "reason": "This document cannot be edited here, and there is no "
                                        "clipboard to copy it to (install wl-clipboard or xclip)",
                              "summary": "Could not apply or copy", "more": text[:200]})
        finally:
            self.busy = False

    def unchanged(self, start, end, piece):
        """The toast may sit for 15s while they keep typing — don't splice stale offsets."""
        try:
            _, now = read(self.target, start, end)
            return now == piece
        except Exception:
            return False

    def replace(self, start, end, replacement):
        """Replace a range through the app's own text interface, so its undo owns the edit.

        Returns True when the text actually changed. A document that cannot be edited is a real
        case — a read-only file, a protected sheet, a viewer that publishes text but no
        EditableText — and this used to raise straight out of a daemon thread: the click did
        nothing at all, and the only trace was a dead thread in the journal.
        """
        try:
            editable = self.target.get_editable_text_iface()
            if editable is None:
                return False
            editable.delete_text(start, end)
            if replacement:
                editable.insert_text(start, replacement, len(replacement))
        except Exception as exc:  # noqa: BLE001 - any refusal means "cannot edit here", not "crash"
            debug("cannot edit here: %s" % exc)
            return False
        # The edit is done: where the caret lands is a courtesy, and some apps accept an edit but
        # not a caret set. Letting that raise would report a failure for a change that happened —
        # and the card would say "copied instead" about text it had just fixed.
        try:
            Atspi.Text.set_caret_offset(self.target.get_text_iface(), start + len(replacement))
        except Exception as exc:  # noqa: BLE001
            debug("caret not moved after the edit: %s" % exc)
        return True


def listen(watcher):
    """Register the two listeners and hand them back: the GI binding wants an EventListener
    instance (not a plain function) and a listener that gets collected stops delivering."""
    listeners = []
    for event, handler in (("object:state-changed:focused", watcher.on_focus),
                           ("object:text-changed", watcher.on_text)):
        listener = Atspi.EventListener.new(handler)
        listener.register(event)
        listeners.append(listener)
    return listeners


def main():
    client = load_client()
    watcher = Watcher(client)
    Atspi.init()
    try:
        listeners = listen(watcher)
    except Exception as exc:
        print("cannot listen on the accessibility bus: %s" % exc, file=sys.stderr)
        return 1
    loop = GLib.MainLoop()
    GLib.timeout_add(POLL_MS, watcher.poll)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    print("watching for text anywhere — suggestions arrive as notifications", flush=True)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
