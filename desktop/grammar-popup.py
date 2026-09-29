#!/usr/bin/python3
"""The suggestion pop-up: "go → goes" with Fix it / Copy, placed next to the caret.

Its own process on purpose. The watcher asks it the way it asks notify-send — argv in, one
word out — so no GTK state ever lands in the daemon, and a pop-up that dies cannot take the
watcher with it.

Positioning: on Wayland a client cannot choose its own position, so this asks for the X11
backend before GTK starts. Under XWayland the window is override-redirect (POPUP_MENU), which
is the case the compositor does not place — measured first: GDK reports the requested root
coordinates and the X server agrees with GDK.

  --x/--y <px>     where the caret is (pass the caret's own extents, not the window's)
  --label <text>   the suggestion, e.g. "go → goes"
  --more <text>    the rest of the findings
  --timeout <s>    how long to stay up if nobody touches it (default 12)

Prints "fix", "copy", or nothing if dismissed. Exits 2 when no pop-up is possible at all
(no display, no GTK) so the caller can fall back to a notification.
"""

import os
import sys

os.environ.setdefault("GDK_BACKEND", "x11")  # before Gtk: positioning needs the X11 backend

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
except Exception as exc:  # noqa: BLE001 - any failure here means "use a notification"
    print("no pop-up: %s" % exc, file=sys.stderr)
    sys.exit(2)

CSS = b"""
window.grammar-pop { background: rgba(24,24,28,0.97); border: 1px solid rgba(255,255,255,0.25);
                     border-radius: 8px; }
label.grammar-pop { color: #ffffff; }
button.grammar-pop { color: #ffffff; background: rgba(255,255,255,0.10); border: none;
                     padding: 6px 12px; border-radius: 6px; }
button.grammar-pop:hover { background: rgba(255,255,255,0.22); }
"""


def clamp(x, y, w, h, monitors):
    """Keep the pop-up on the monitor the caret is on; edge carets happen constantly.

    monitors is a list of (x, y, width, height) so this is testable without a display.
    """
    for mx, my, mw, mh in monitors:
        if mx <= x < mx + mw and my <= y < my + mh:
            return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    if monitors:
        mx, my, mw, mh = monitors[-1]
        return (mx + mw - w - 24, my + mh - h - 24)
    return (x, y)


def main():
    args = sys.argv[1:]
    opts = {"x": None, "y": None, "label": "Suggestion", "more": "", "timeout": 12}
    for i, a in enumerate(args):
        key = a.lstrip("-")
        if key in opts and i + 1 < len(args):
            opts[key] = args[i + 1]
    label = str(opts["label"])
    more = str(opts["more"])
    timeout = int(opts["timeout"])
    chosen = {"key": ""}

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.get_style_context().add_class("grammar-pop")
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_accept_focus(False)   # keep typing in the app: never steal the caret
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
    win.set_title("grammar")
    win.connect("destroy", lambda *_: Gtk.main_quit())

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_border_width(12)
    head = Gtk.Label(label=label)
    head.get_style_context().add_class("grammar-pop")
    head.set_xalign(0)
    head.set_line_wrap(True)
    head.set_max_width_chars(48)
    box.pack_start(head, False, False, 0)
    if more:
        sub = Gtk.Label(label=more)
        sub.get_style_context().add_class("grammar-pop")
        sub.set_xalign(0)
        sub.set_line_wrap(True)
        sub.set_max_width_chars(48)
        box.pack_start(sub, False, False, 0)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

    def pick(key):
        def handler(*_):
            chosen["key"] = key
            Gtk.main_quit()
        return handler

    for key, text in (("fix", "Fix it"), ("copy", "Copy fix")):
        button = Gtk.Button(label=text)
        button.get_style_context().add_class("grammar-pop")
        button.connect("clicked", pick(key))
        row.pack_start(button, False, False, 0)
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

    GLib.timeout_add_seconds(timeout, lambda: (Gtk.main_quit(), False)[1])
    Gtk.main()
    if chosen["key"]:
        print(chosen["key"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
