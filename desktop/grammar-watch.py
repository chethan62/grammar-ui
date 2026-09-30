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
BACK, FORWARD, TAIL = 500, 300, 240
DEBOUNCE_MS = 1200
# ponytail: events are the fast path, but LibreOffice's a11y event emission is partial — a
# slow poll is what makes this work in the app it was built for. One bounded read per tick;
# the engine is only asked when the text actually changed.
POLL_MS = 1500
COOLDOWN_S = 5.0
MIN_CHARS = 12
DEBUG = os.environ.get("GRAMMAR_WATCH_DEBUG") == "1"


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


def snippet_window(text, caret, back=BACK, forward=FORWARD):
    """The whole sentences around `caret` as (start, end) offsets into `text`.

    Snapping back to a sentence boundary keeps the engine from being handed a fragment, which
    it duly reports as an incomplete sentence.
    """
    start = max(0, caret - back)
    # From caret - 2, not caret - 1: while typing, the caret sits right after the final period
    # of the sentence just written, and snapping to that boundary returns an empty window.
    for i in range(caret - 2, start - 1, -1):
        if text[i] in ".!?\n":
            start = i + 1
            break
    end = min(len(text), caret + forward)
    limit = min(len(text), end + TAIL)
    for i in range(end, limit):
        if text[i] in ".!?\n":
            end = i + 1
            break
        end = i + 1
    while start < end and text[start].isspace():
        start += 1
    return start, end


def suggestions(text, matches):
    """(old, new, reason) per finding, in document order.

    Structured rather than pre-rendered, because the card shows the diff and the reason apart,
    while a notification only has room for "old → new".
    """
    out = []
    for m in sorted(matches, key=lambda m: m["offset"]):
        if not m.get("replacements"):
            continue
        old = text[m["offset"]:m["offset"] + m["length"]].strip()
        out.append((old, m["replacements"][0]["value"], m["message"].strip().replace("\n", " ")))
    return out


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


def shown(matches):
    """The one finding the card is showing: the first in document order that carries a replacement.

    This rule had three copies — in alternatives(), in first_span() and in the count for "more
    issues" — and two of them carried a comment warning that they must not drift, because a chip
    that picked a different finding than the span would rewrite the text somewhere else in the
    sentence. It lives here once now. Pure.
    """
    for m in sorted(matches, key=lambda m: m["offset"]):
        if m.get("replacements"):
            return m
    return None


def alternatives(matches):
    """Every replacement the engine offered for the finding the card is showing, in its own order.

    The card used to receive only replacements[0], so a spelling fix offered "Teh" and hid "the",
    "tea" and "tech" — the engine had already found them. Pure, so the ordering and the
    de-duplication are testable without a display.
    """
    match = shown(matches)
    if match is None:
        return []
    out = []
    for rep in match["replacements"]:
        value = (rep.get("value") or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def first_span(matches):
    """Where the finding the card is showing sits, as (offset, length) in the checked window.

    It must pick the same match alternatives() does — both now call shown() — or a chip would
    rewrite the text somewhere else in the sentence. The pair is asserted together in
    test-watch.py for that reason.
    """
    match = shown(matches)
    if match is None:
        return 0, 0
    return int(match["offset"]), int(match["length"])


def others(matches):
    """How many *other* findings with a fix the same sentence has, for the card to mention.

    A sentence carrying two issues showed one and said nothing about the second, so fixing what
    the card named left the line still underlined with no explanation — measured on this project's
    own sample, "She go to the office.", which the engine flags twice. Pure.
    """
    with_fix = [m for m in matches if m.get("replacements")]
    return max(0, len(with_fix) - (1 if shown(matches) is not None else 0))


def parse_reply(text):
    """The card's answer: its JSON line, or the older plain word. Pure.

    Tolerant because this crosses a process boundary, and anything unrecognised is a dismissal
    rather than a fix — a garbled line must never edit the user's document.
    """
    text = (text or "").strip()
    if not text:
        return None
    if not text.startswith("{"):
        # The pre-JSON contract, still accepted: "fix" meant the sentence, "copy" the clipboard.
        return {"action": "sentence", "text": ""} if text == "fix" else (
            {"action": "copy", "text": ""} if text == "copy" else None)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("action"):
        return None
    return {"action": str(data["action"]), "text": str(data.get("text") or "")}


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

    # ---- the check ----------------------------------------------------------------------
    def check(self):
        self.timer = None
        if self.busy or self.target is None:
            return False
        try:
            if not self.target.get_state_set().contains(Atspi.StateType.FOCUSED):
                return False  # they moved on; a debounce that fired late is stale
        except Exception:
            pass
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
        will not say — a notification is shown instead of a pop-up somewhere wrong."""
        try:
            ext = Atspi.Text.get_character_extents(text, max(0, caret), Atspi.CoordType.SCREEN)
            if ext.width or ext.height:
                return ext.x, ext.y + ext.height + 4     # just under the caret's line
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
                self.client.copy(fixed)
                return
            if applied is False:
                # A document that cannot be edited is not a crash and must not be a silence: put the
                # correction on the clipboard and say what happened. This is the difference between
                # a click that appears broken and one that still did the useful thing.
                #
                # Tested with `is False`, not truthiness: only an explicit refusal means the edit
                # did not happen, so a replace() that returns nothing (an older implementation, a
                # stand-in in a test) keeps the behaviour it always had.
                self.client.copy(text)
                self.ask({"old": text, "new": "",
                          "reason": "This document cannot be edited from here",
                          "summary": "Copied instead",
                          "more": "Paste it where you need it, or select text and press Ctrl+Alt+C."})
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
