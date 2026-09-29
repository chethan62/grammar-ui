#!/usr/bin/python3
"""The suggestion card: what is wrong, every fix the engine offered, and a rephrase on request.

Its own process on purpose. The watcher asks it the way it asks notify-send — payload in, one
JSON line out — so no GTK state lands in the daemon and a card that dies cannot take the watcher
with it.

The card, top to bottom:

* the offending text struck through, and the rule's own one-line reason;
* a chip per replacement the engine returned. Clicking a chip applies *that* one — the previous
  card threw all but the first away, which is how a spelling fix ended up offering only "Teh"
  when the engine had also suggested "the", "tea" and "tech";
* Fix sentence, which applies every non-overlapping correction in the line at once (the watcher
  computes that text, not this process);
* Copy, for when the destination is not the document; and Ignore, which is a real answer;
* a tone and an intent, and Rephrase.

**This process makes the rephrase call itself.** It is a local process on loopback and it owns
the interaction, so the few seconds a small model needs are spent here rather than inside the
watcher's ask path, where they would hold every other application's suggestions while the user
waited. The call runs on a thread and comes back through GLib.idle_add, because GTK is not
thread-safe and a frozen card is worse than no card. No browser is involved in this path: when
the chips come back they are answered with the same one-line contract as any other button.

It follows the desktop's theme instead of hardcoding one, and it never takes focus
(accept_focus False plus a POPUP_MENU window), so typing in the application continues while it
is up. That last property also means Enter and Escape cannot work: the card never receives key
events, by design. Clicking is the interaction, and the notification covers the case where no
card can be shown.

Positioning: on Wayland a client cannot choose its own position, so this asks for the X11 backend
before GTK starts. Measured: X11 honours the caret's coordinates, while the Wayland backend
cannot map a parentless popup at all — "Gdk-WARNING: Couldn't map as window ... as popup because
it doesn't have a parent" — and lands wherever the compositor likes.

Input, one of two ways:

* a JSON object on stdin (what the watcher sends):
      {"old": "teh", "reason": "...", "badge": "Rules engine · 12 ms",
       "alts": ["Teh", "the", "tea", "tech"], "more": "...",
       "api": "http://127.0.0.1:8875", "sentence": "She go to the office."}
* the old argv flags, for a shell: --old --new --reason --badge --more

Either way: --x/--y <px> position it at the caret, --timeout <s> is how long it stays up (12).

Prints one JSON line when an action is taken — {"action": "replace", "text": "the"},
{"action": "sentence"}, {"action": "sentence", "text": "<a rephrased line>"}, {"action": "copy"}
— and nothing if it was dismissed or expired. Exits 2 when no pop-up is possible at all (no
display, no GTK) so the caller can fall back to a notification. On stderr: PLACED <x> <y>
(asked <x> <y>), so a test can tell placement from luck.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

os.environ.setdefault("GDK_BACKEND", "x11")  # before Gtk: positioning needs the X11 backend

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
except Exception as exc:  # noqa: BLE001 - any failure here means "use a notification"
    print("no pop-up: %s" % exc, file=sys.stderr)
    sys.exit(2)

MAX_CHIPS = 6          # a card, not a menu: the engine's first few are the useful ones
MAX_CANDIDATES = 3     # the model's alternatives, shown in place of the fixes
REPHRASE_TIMEOUT = 90  # a cold local model on this CPU has taken 6s; the server caps it anyway
TONES = ("", "professional", "casual", "formal")
INTENTS = ("", "concise", "clear", "simple")


def parse_payload(raw, argv=None):
    """The card's content, from stdin JSON or the argv flags. Pure, so it needs no display.

    Tolerant on purpose: a card that refuses to render because one field is the wrong type is
    worse than a card with a missing line. Unknown keys are ignored, so the watcher may grow the
    payload without breaking an older card.
    """
    data = {}
    if isinstance(raw, dict):
        data = dict(raw)
    elif raw and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                data = loaded
        except ValueError:
            data = {}
    if not data:                                     # a shell invocation, or junk on stdin
        flags = argv or []
        for i, a in enumerate(flags):
            key = a.lstrip("-")
            if key in ("old", "new", "reason", "badge", "more") and i + 1 < len(flags):
                data[key] = flags[i + 1]

    def as_text(key):
        value = data.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    alts = []
    for value in data.get("alts") or []:
        if isinstance(value, str) and value.strip() and value.strip() not in alts:
            alts.append(value.strip())
    # The single --new of the old contract is just the first alternative.
    if not alts and as_text("new"):
        alts = [as_text("new")]
    if alts and not as_text("new"):
        data["new"] = alts[0]
    return {"old": as_text("old"), "new": as_text("new"), "reason": as_text("reason"),
            "badge": as_text("badge"), "more": as_text("more"), "alts": alts[:MAX_CHIPS],
            "api": as_text("api").rstrip("/"), "sentence": as_text("sentence")}


def rephrase_body(sentence, tone="", intent=""):
    """The body for POST /v2/rewrite. Pure: the card's own contract with its server."""
    body = {"text": sentence, "language": "en-US"}
    if tone:
        body["tone"] = tone
    if intent:
        body["intent"] = intent
    return body


def candidates_from(response):
    """The alternatives out of a rewrite response. Pure, and tolerant: that JSON is another
    process's. An error response carries no candidates, only a message worth showing."""
    if not isinstance(response, dict) or response.get("message"):
        return []
    out = []
    for value in response.get("candidates") or []:
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out[:MAX_CANDIDATES]


def api_error_message(response, status=0):
    """Why a call failed, in the server's own words when it has them. Pure."""
    if isinstance(response, dict) and response.get("message"):
        return str(response["message"])
    if status:
        return "the rewrite backend answered HTTP %d" % status
    return "the backend did not answer"


def post_json(url, body, timeout=REPHRASE_TIMEOUT):
    """POST, and read the JSON back either way: an error body is the instruction to show."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"message": "cannot reach the engine at %s (%s)" % (url, exc)}


def card_markup(old, new, reason="", badge="", more="", chips=False):
    """The card's text as Pango markup. Pure, so it is testable without a display.

    Everything from the document goes through escape(): the text reaches us from the user's own
    writing via the engine, so it can contain '&' or '<'. Pango would otherwise reject the label
    outright, or render the document's text as markup — the same trust boundary as esc() in the
    web UI, where an unescaped engine message broke out of an attribute.
    """
    parts = []
    if chips:
        # The alternatives are the buttons now, so the headline is just the offender — repeating
        # the first one as "old → new" would show the same fix twice. Large and bold because it is
        # the problem the card exists for, and as plain grey it was the faintest thing on it.
        if old:
            parts.append('<span size="large" weight="bold"><s>%s</s></span>' % escape(old))
    elif old and new:
        parts.append("<s>%s</s>  →  <b>%s</b>" % (escape(old), escape(new)))
    elif new:
        parts.append("<b>%s</b>" % escape(new))
    elif old:
        parts.append("<s>%s</s>" % escape(old))
    if reason:
        parts.append('<span size="small">%s</span>' % escape(reason))
    if more:
        parts.append('<span size="small">%s</span>' % escape(more))
    if badge:
        parts.append('<span size="small" alpha="55%%">%s</span>' % escape(badge))
    return "\n".join(parts)


def clamp(x, y, w, h, monitors):
    """Keep the card on the monitor the caret is on; edge carets happen constantly.

    monitors is a list of (x, y, width, height) so this is testable without a display.
    """
    for mx, my, mw, mh in monitors:
        if mx <= x < mx + mw and my <= y < my + mh:
            return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    if monitors:
        # Nothing contains it, which happens for real: a caret can report a negative or
        # beyond-the-edge position (a window partly off-screen, a stale AT-SPI rect). Park the
        # card on the *nearest* monitor and pull it inside, rather than always on the last one —
        # measured: an off-the-top-left caret put the card at 3056,936, the far corner of the
        # other monitor, which is the one place a card is least useful.
        def centre_distance(m):
            mx, my, mw, mh = m
            return (x - (mx + mw / 2)) ** 2 + (y - (my + mh / 2)) ** 2
        mx, my, mw, mh = min(monitors, key=centre_distance)
        return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    return (x, y)


def action_json(action, text=""):
    """One line the watcher can parse. Pure, and the whole output contract."""
    out = {"action": action}
    if text:
        out["text"] = text
    return json.dumps(out, ensure_ascii=False)


def main():
    args = sys.argv[1:]
    opts = {"x": None, "y": None, "timeout": 12}
    for i, a in enumerate(args):
        key = a.lstrip("-")
        if key in opts and i + 1 < len(args):
            opts[key] = args[i + 1]
    timeout = int(opts["timeout"])

    raw = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except (OSError, UnicodeDecodeError):
            raw = ""
    payload = parse_payload(raw, args)
    chosen = {"action": "", "text": ""}

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_accept_focus(False)   # keep typing in the app: never steal the caret
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_resizable(False)
    win.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
    win.set_title("grammar")
    win.connect("destroy", lambda *_: Gtk.main_quit())

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(14)
    chips = len(payload["alts"]) > 1
    text = Gtk.Label()
    text.set_markup(card_markup(payload["old"], payload["new"], payload["reason"],
                                payload["badge"], payload["more"], chips=chips))
    text.set_xalign(0)
    text.set_line_wrap(True)
    text.set_max_width_chars(38)          # about 290 px, the width the design asks for
    text.set_selectable(False)
    box.pack_start(text, False, False, 0)

    def pick(action, value=""):
        def handler(*_):
            chosen["action"], chosen["text"] = action, value
            Gtk.main_quit()
        return handler

    def section(label):
        """A faint section label over a hairline rule.

        The first version was one flat column, so nothing said where the fixes ended and the
        actions began — the parts were all there and the card still read as a wall.
        """
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
        cap = Gtk.Label()
        cap.set_markup('<span size="small" weight="bold" alpha="55%%">%s</span>' % escape(label))
        cap.set_xalign(0)
        box.pack_start(cap, False, False, 0)

    if chips:
        # One chip per replacement, wrapping. The engine returns them best-first, so the first
        # wears the primary style: which fix is being offered should be visible, not inferred.
        section("FIXES")
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_row_spacing(6)
        flow.set_column_spacing(6)
        for i, alt in enumerate(payload["alts"]):
            chip = Gtk.Button(label=alt)
            if i == 0:
                chip.get_style_context().add_class("suggested-action")
            chip.connect("clicked", pick("replace", alt))
            flow.add(chip)
        box.pack_start(flow, False, False, 0)

    status = Gtk.Label()
    status.set_xalign(0)
    status.set_line_wrap(True)
    status.set_max_width_chars(38)
    status.set_selectable(False)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    for action, label, primary in (("sentence", "Fix sentence", True),
                                   ("copy", "Copy", False),
                                   ("", "Ignore", False)):
        button = Gtk.Button(label=label)
        if primary:
            # GTK's own "this is the primary action" class, so Breeze and Adwaita both style it.
            button.get_style_context().add_class("suggested-action")
        button.connect("clicked", pick(action, ""))
        row.pack_end(button, False, False, 0)
    box.pack_start(row, False, False, 0)

    # The model's lines land here, directly under their own heading, so the card says what they
    # are instead of showing three unexplained sentences.
    ai_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

    # The model lives here now, not in a browser. Rephrase needs an engine URL and the sentence it
    # is rephrasing; without both the section is not offered at all, rather than offered broken.
    if payload["api"] and payload["sentence"]:
        section("REPHRASE")
        box.pack_start(ai_slot, False, False, 0)
        airow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tone = Gtk.ComboBoxText()
        for value in TONES:
            tone.append(value, "tone: " + (value or "as-is"))
        tone.set_active(0)
        tone.set_size_request(124, -1)     # the combos used to be the widest thing on the card
        intent = Gtk.ComboBoxText()
        for value in INTENTS:
            intent.append(value, value or "rephrase as-is")
        intent.set_active(0)
        intent.set_size_request(146, -1)
        rephrase = Gtk.Button(label="Rephrase")

        def render_candidates(candidates, message):
            """Back on the main loop: the alternatives appear under their own heading, as flat
            rows. A bordered button on every line would shout as loudly as the fixes, and a fix
            is the more likely answer — but flat rows still need the heading, or three
            unexplained sentences appear in the middle of the card."""
            status.set_markup("")
            rephrase.set_sensitive(True)
            for child in ai_slot.get_children():
                ai_slot.remove(child)
            if message:
                status.set_markup('<span size="small">%s</span>' % escape(message))
                return False
            for candidate in candidates:
                chip = Gtk.Button(label=candidate)
                chip.set_relief(Gtk.ReliefStyle.NONE)
                chip.set_halign(Gtk.Align.START)
                # A rephrase replaces the whole sentence, so the answer is a sentence action
                # carrying its own text — the watcher applies answer["text"] when it is there.
                chip.connect("clicked", pick("sentence", candidate))
                ai_slot.pack_start(chip, False, False, 0)
            win.show_all()
            return False

        def worker(sentence, tone_value, intent_value, url):
            """Off the main loop: the call, then back on it. GLib is not thread-safe, and this is
            the only way a local model's few seconds do not freeze the card."""
            status_code, response = post_json(url + "/v2/rewrite",
                                              rephrase_body(sentence, tone_value, intent_value))
            candidates = candidates_from(response)
            if candidates:
                GLib.idle_add(render_candidates, candidates, "")
            else:
                GLib.idle_add(render_candidates, [], api_error_message(response, status_code))

        def on_rephrase(*_):
            rephrase.set_sensitive(False)
            status.set_markup('<span size="small">Rephrasing… a local model takes a few '
                              'seconds</span>')
            threading.Thread(target=worker, daemon=True,
                             args=(payload["sentence"], tone.get_active_id() or "",
                                   intent.get_active_id() or "", payload["api"])).start()

        rephrase.connect("clicked", on_rephrase)
        airow.pack_start(tone, False, False, 0)
        airow.pack_start(intent, False, False, 0)
        airow.pack_end(rephrase, False, False, 0)
        box.pack_start(airow, False, False, 0)
        box.pack_start(status, False, False, 0)

    win.add(box)
    win.show_all()

    if opts["x"] is not None and opts["y"] is not None:
        display = Gdk.Display.get_default()
        monitors = []
        if display is not None:
            for i in range(display.get_n_monitors()):
                geo = display.get_monitor(i).get_geometry()
                monitors.append((geo.x, geo.y, geo.width, geo.height))
        alloc = win.get_allocation()
        px, py = clamp(int(opts["x"]), int(opts["y"]), alloc.width, alloc.height, monitors)
        win.move(px, py)
        # Reported, not assumed, and from the X server rather than GDK: under XWayland
        # get_position() still says 0 0 after a successful move (measured), while the X server
        # agrees with the request. A test that trusted get_position() would report every card as
        # misplaced.
        def report_placed():
            """Report after the geometry settles.

            Read immediately after move(), both get_position() and get_root_coords() return 0 0 —
            they reflect what the server has confirmed, and it has not confirmed yet. Measured:
            the X server already agrees with the request (xdotool shows Position: 900,300) while
            GDK still says 0 0 in the same instant.
            """
            try:
                root = win.get_window().get_root_coords(0, 0)
                print("PLACED %d %d (asked %s %s)" % (root[0], root[1], opts["x"], opts["y"]),
                      file=sys.stderr, flush=True)
            except Exception as exc:
                print("PLACED unknown (%s)" % exc, file=sys.stderr, flush=True)
            return False

        GLib.timeout_add(400, report_placed)

    GLib.timeout_add_seconds(timeout, lambda: (Gtk.main_quit(), False)[1])
    Gtk.main()
    if chosen["action"]:
        print(action_json(chosen["action"], chosen["text"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
