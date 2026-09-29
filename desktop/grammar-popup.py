#!/usr/bin/python3
"""The suggestion card: what is wrong, what it should be, and one button that fixes it.

Its own process on purpose. The watcher asks it the way it asks notify-send — argv in, one word
out — so no GTK state lands in the daemon and a card that dies cannot take the watcher with it.

The card: struck-through original, an arrow, the replacement, a one-line reason, a small badge
showing the engine's own milliseconds, and Accept as the only filled button. It follows the
desktop's theme instead of hardcoding one, and it never takes focus (accept_focus False plus a
POPUP_MENU window), so typing in the application continues while it is up. That last property
also means Enter and Escape cannot work: the card never receives key events, by design. Clicking
is the interaction, and the notification covers the case where no card can be shown.

Positioning: on Wayland a client cannot choose its own position, so this asks for the X11 backend
before GTK starts. Measured: X11 honours the caret's coordinates (OCR found the card's own words
where they were asked for), while the Wayland backend cannot map a parentless popup at all —
"Gdk-WARNING: Couldn't map as window ... as popup because it doesn't have a parent" — and lands
wherever the compositor likes.

  --x/--y <px>     the caret's screen position (its extents, not the window's)
  --old <text>     the original text
  --new <text>     the replacement
  --reason <text>  one short line naming the rule
  --badge <text>   e.g. "Rules engine · 12 ms"
  --more <text>    further findings (optional, one line)
  --timeout <s>    how long to stay up if nobody touches it (default 12)

Prints "fix" or "copy" when a button is pressed, nothing if it was dismissed or expired. Exits 2
when no pop-up is possible at all (no display, no GTK) so the caller can fall back to a
notification. On stderr: PLACED <x> <y> (asked <x> <y>), so a test can tell placement from luck.
"""

import os
import sys
from xml.sax.saxutils import escape

os.environ.setdefault("GDK_BACKEND", "x11")  # before Gtk: positioning needs the X11 backend

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
except Exception as exc:  # noqa: BLE001 - any failure here means "use a notification"
    print("no pop-up: %s" % exc, file=sys.stderr)
    sys.exit(2)


def card_markup(old, new, reason="", badge="", more=""):
    """The card's text as Pango markup. Pure, so it is testable without a display.

    Everything from the document goes through escape(): the text reaches us from the user's own
    writing via the engine, so it can contain '&' or '<'. Pango would otherwise reject the label
    outright, or render the document's text as markup — the same trust boundary as esc() in the
    web UI, where an unescaped engine message broke out of an attribute.
    """
    parts = []
    if old and new:
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


def main():
    args = sys.argv[1:]
    opts = {"x": None, "y": None, "old": "", "new": "", "reason": "",
            "badge": "", "more": "", "timeout": 12}
    for i, a in enumerate(args):
        key = a.lstrip("-")
        if key in opts and i + 1 < len(args):
            opts[key] = args[i + 1]
    timeout = int(opts["timeout"])
    chosen = {"key": ""}

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
    box.set_border_width(12)
    text = Gtk.Label()
    text.set_markup(card_markup(str(opts["old"]), str(opts["new"]), str(opts["reason"]),
                                str(opts["badge"]), str(opts["more"])))
    text.set_xalign(0)
    text.set_line_wrap(True)
    text.set_max_width_chars(38)          # about 290 px, the width the design asks for
    text.set_selectable(False)
    box.pack_start(text, False, False, 0)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

    def pick(key):
        def handler(*_):
            chosen["key"] = key
            Gtk.main_quit()
        return handler

    for key, label, primary in (("fix", "Accept", True), ("", "Ignore", False)):
        button = Gtk.Button(label=label)
        if primary:
            # GTK's own "this is the primary action" class, so Breeze and Adwaita both style it.
            button.get_style_context().add_class("suggested-action")
        button.connect("clicked", pick(key))
        row.pack_end(button, False, False, 0)
    box.pack_start(row, False, False, 0)
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
    if chosen["key"]:
        print(chosen["key"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
